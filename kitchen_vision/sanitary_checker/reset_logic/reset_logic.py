import logging
import time
import uuid
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from collections import defaultdict

try:
    from kitchen_vision.sanitary_checker.fsm.fsm_hub import FSMHub
except ImportError as e:
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reset_logic")


RESET_TYPES = {
    "hands_washed": "hands_washed",
    "gloves_changed": "gloves_changed",
    "gloves_removed": "gloves_removed",
    "gloves_put_on": "gloves_put_on",
    "tool_washed": "tool_washed",
    "clean_tool_taken": "clean_tool_taken",
    "tool_replaced": "tool_replaced",
    "container_replaced_clean": "container_replaced_clean",
    "disposable_container_used": "disposable_container_used",
    "surface_sanitized": "surface_sanitized",
    "entered_reset_zone": "entered_reset_zone",
    "passed_clean_gate": "passed_clean_gate",
    "shift_started": "shift_started",
    "returned_to_kitchen": "returned_to_kitchen",
}

SANITARY_STATES = {
    "clean_confirmed": "clean_confirmed",
    "clean_degrading": "clean_degrading",
    "unknown": "unknown",
    "contaminated": "contaminated",
    "sanitized": "sanitized",
    "reset_pending": "reset_pending",
}

ENTITY_TYPES = {
    "employee": "employee",
    "hands": "hands",
    "tool": "tool",
    "container": "container",
    "surface": "surface",
    "zone": "zone",
    "food": "food",
    "action": "action",
    "incident": "incident",
    "system": "system",
}

OBSERVATION_STATES = {
    "obs_ok": "obs_ok",
    "obs_weak": "obs_weak",
    "obs_degraded": "obs_degraded",
    "obs_lost": "obs_lost",
}


@dataclass
class ResetResultForTTL:
    entity_type: str  # "hands", "tool", "container", "surface", "employee"
    track_id: str  # Идентификатор трека
    reset_type: str  # Тип reset (из RESET_TYPES)
    ttl_reset_type: str  # "full", "partial", "none"
    new_state: str  # Новое санитарное состояние

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "track_id": self.track_id,
            "reset_type": self.reset_type,
            "ttl_reset_type": self.ttl_reset_type,
            "new_state": self.new_state,
        }


@dataclass
class ResetRule:
    reset_type: str
    target_entity: str
    resulting_state: str
    ttl_reset: str
    min_confidence: float
    requires_confirmation: bool = False


RESET_RULES = [
    ResetRule("hands_washed", "hands", "sanitized", "full", 0.6),
    ResetRule("gloves_changed", "hands", "sanitized", "full", 0.6),
    ResetRule("gloves_removed", "hands", "clean_degrading", "partial", 0.6),
    ResetRule("gloves_put_on", "hands", "sanitized", "full", 0.6),

    ResetRule("tool_washed", "tool", "sanitized", "full", 0.6),
    ResetRule("clean_tool_taken", "tool", "sanitized", "full", 0.6),
    ResetRule("tool_replaced", "tool", "sanitized", "full", 0.6),

    ResetRule("container_replaced_clean", "container", "sanitized", "full", 0.6),
    ResetRule("disposable_container_used", "container", "sanitized", "full", 0.6),

    ResetRule("surface_sanitized", "surface", "sanitized", "full", 0.6),

    ResetRule("entered_reset_zone", "hands", "reset_pending", "none", 0.6, requires_confirmation=True),
    ResetRule("entered_reset_zone", "employee", "reset_pending", "none", 0.6, requires_confirmation=True),
    ResetRule("passed_clean_gate", "employee", "reset_pending", "none", 0.6, requires_confirmation=True),

    ResetRule("returned_to_kitchen", "employee", "clean_degrading", "none", 0.6),

    ResetRule("shift_started", "employee", "clean_confirmed", "full", 0.8),
    ResetRule("shift_started", "hands", "clean_confirmed", "full", 0.8),
    ResetRule("shift_started", "tool", "clean_confirmed", "full", 0.8),
    ResetRule("shift_started", "surface", "clean_confirmed", "full", 0.8),
    ResetRule("shift_started", "container", "clean_confirmed", "full", 0.8),
]

RESET_RULES_BY_TYPE: Dict[str, List[ResetRule]] = defaultdict(list)
for r in RESET_RULES:
    RESET_RULES_BY_TYPE[r.reset_type].append(r)


class ResetLogic:
    def __init__(
            self,
            fsm_hub: FSMHub,
            config: Optional[Dict[str, Any]] = None
    ):
        self._fsm_hub = fsm_hub or None
        self._reset_types = RESET_TYPES
        self._sanitary_states = SANITARY_STATES
        self._entity_types = ENTITY_TYPES
        self._observation_states = OBSERVATION_STATES

        self._config = config or {}
        self._min_conf_reset_default = self._config.get("min_conf_reset_default", 0.6)
        self._min_conf_reset_strict = self._config.get("min_conf_reset_strict", 0.8)
        self._T_reset_pending_confirm_sec = self._config.get("T_reset_pending_confirm_sec", 60)
        self._T_reset_dedup_sec = self._config.get("T_reset_dedup_sec", 2.0)

        # Хранилище для отслеживания состояний reset_pending
        self._reset_pending_states: Dict[str, Dict[str, Any]] = {}

        # Дедупликация reset-событий
        self._last_reset_timestamps: Dict[str, float] = {}

        # Состояние сущностей (кэш для быстрого доступа)
        self._entity_states: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)

    def _get_reset_rules(self, reset_type: str) -> List[ResetRule]:
        return RESET_RULES_BY_TYPE.get(reset_type, [])

    def _validate_reset_type(self, reset_type: str) -> bool:
        return reset_type in self._reset_types

    def _validate_entity_type(self, entity_type: str) -> bool:
        return entity_type in self._entity_types

    def _validate_observation_state(self, state: str) -> bool:
        return state in self._observation_states

    def _is_deduped(self, reset_type: str, target_entity: str, track_id: str) -> bool:
        """
        Дедупликация reset-событий (раздел 29.4, п. 3)

        Многократные reset одного типа в течение T_reset_dedup_sec считаются одним событием.
        """
        key = f"{reset_type}_{target_entity}_{track_id}"
        current_time = time.time()

        if key in self._last_reset_timestamps:
            elapsed = current_time - self._last_reset_timestamps[key]
            if elapsed < self._T_reset_dedup_sec:
                logger.debug(f"Reset dedup: {key} within {self._T_reset_dedup_sec}s")
                return True

        self._last_reset_timestamps[key] = current_time
        return False

    def _get_observation_state(self, camera_id: str) -> str:
        """
        Получение текущего состояния наблюдаемости камеры из ObservationFSM

        Args:
            camera_id: идентификатор камеры

        Returns:
            Состояние наблюдаемости (obs_ok, obs_weak, obs_degraded, obs_lost)
        """
        # Получаем ObservationFSM из FSMHub
        if hasattr(self._fsm_hub, 'observation_fsm'):
            observation_fsm = self._fsm_hub.observation_fsm
            if hasattr(observation_fsm, 'get_camera_state'):
                return observation_fsm.get_camera_state(camera_id)

        # Fallback: если ObservationFSM недоступен
        return self._observation_states["obs_ok"]

    def _should_apply_reset(
            self,
            rule: ResetRule,
            track_id: str,
            camera_id: str,
            confidence: float,
    ) -> tuple[bool, str]:
        """
        Проверка условий применения reset (раздел 29.3, 29.4)

        Returns:
            (should_apply, reason)
        """
        # Проверка 1: confidence
        min_conf = self._min_conf_reset_strict if rule.reset_type == "shift_started" else self._min_conf_reset_default
        if confidence < min_conf:
            return False, f"low_confidence: {confidence} < {min_conf}"

        # Проверка 2: observation_state
        observation_state = self._get_observation_state(camera_id)

        if observation_state == self._observation_states["obs_lost"]:
            return False, "obs_lost"

        # Проверка 3: obs_degraded - применяется с ограничениями
        if observation_state == self._observation_states["obs_degraded"]:
            # В degraded режиме разрешены только не критические reset
            if rule.reset_type in ("shift_started",):
                return False, "degraded_mode_blocked"

        # Проверка 4: дедупликация
        if self._is_deduped(rule.reset_type, rule.target_entity, track_id):
            return False, "deduped"

        return True, "ok"

    def apply_reset_to_entity(
            self,
            rule: ResetRule,
            track_id: str,
            camera_id: str,
            confidence: float,
            timestamp: int,
    ) -> dict[str, Any] | None:
        """
        Args:
            rule: правило применения reset
            track_id: идентификатор трека сущности
            camera_id: идентификатор камеры (для проверки наблюдаемости)
            confidence: уверенность события
            timestamp: время события в мс

        Returns:
            ResetResultForTTL или None если reset не применен
        """
        entity_type = rule.target_entity

        # Проверка условий применения
        should_apply, reason = self._should_apply_reset(
            rule, track_id, camera_id, confidence
        )

        if not should_apply:
            logger.debug(f"Reset not applied: {rule.reset_type} -> {entity_type}:{track_id}, reason: {reason}")
            return None

        # Получение FSM для сущности
        fsm = self._get_fsm_for_entity(entity_type)
        # if fsm is None:
        #     logger.error(f"No FSM found for entity: {entity_type}")
        #     return None

        # Определение нового состояния (раздел 29.2)
        observation_state = self._get_observation_state(camera_id)
        new_state = self._get_resulting_state(rule, track_id, observation_state)

        # Применение reset через FSM
        if new_state == self._sanitary_states["reset_pending"]:
            # Промежуточное состояние - требует подтверждения (раздел 29.3, п. 7)
            self._set_reset_pending(track_id, rule, timestamp, camera_id)
            logger.info(
                f"Reset pending: {rule.reset_type} -> {entity_type}:{track_id}, TTL: {self._T_reset_pending_confirm_sec}s")
        else:
            # Применение состояния через FSM
            self._apply_state_to_fsm(fsm, track_id, new_state)
            logger.info(f"Reset applied: {rule.reset_type} -> {entity_type}:{track_id} -> {new_state}")

        # Обновление состояния сущности
        self._entity_states[track_id][entity_type] = {
            "state": new_state,
            "reset_type": rule.reset_type,
            "timestamp": timestamp,
            "confidence": confidence,
        }

        # Создание результата для TTL логики
        return ResetResultForTTL(
            entity_type=entity_type,
            track_id=track_id,
            reset_type=rule.reset_type,
            ttl_reset_type=rule.ttl_reset,
            new_state=new_state,
        ).to_dict()

    def _get_resulting_state(
            self,
            rule: ResetRule,
            track_id: str,
            observation_state: str,
    ) -> str:
        """
        Определение результирующего состояния (раздел 29.3)

        Особые случаи:
        - obs_lost: reset_pending до восстановления
        - already sanitized: продление TTL, состояние не меняется
        """
        entity_type = rule.target_entity

        # Если сущность уже в sanitized - просто продлеваем TTL (раздел 29.4, п. 4)
        current_state = self._get_current_state(track_id, entity_type)
        if current_state == self._sanitary_states["sanitized"]:
            return self._sanitary_states["sanitized"]

        # obs_lost - reset_pending (раздел 29.4, п. 1)
        if observation_state == self._observation_states["obs_lost"]:
            return self._sanitary_states["reset_pending"]

        # Требуется подтверждение
        if rule.requires_confirmation:
            return self._sanitary_states["reset_pending"]

        return rule.resulting_state

    def _get_current_state(self, track_id: str, entity_type: str) -> Optional[str]:
        """Получение текущего состояния сущности из FSM"""
        fsm = self._get_fsm_for_entity(entity_type)
        if fsm and hasattr(fsm, 'get_sanitary_state'):
            return fsm.get_sanitary_state(track_id)

        # Fallback: из локального хранилища
        if track_id in self._entity_states:
            return self._entity_states[track_id].get(entity_type, {}).get("state")
        return None

    def _set_reset_pending(self, track_id: str, rule: ResetRule, timestamp: int, camera_id: str) -> None:
        """
        Установка состояния reset_pending

        entered_reset_zone, passed_clean_gate переводят в промежуточное состояние
        и требуют подтверждения в окне T_reset_pending_confirm_sec.
        """
        key = f"{track_id}_{rule.target_entity}_{rule.reset_type}"
        self._reset_pending_states[key] = {
            "track_id": track_id,
            "entity_type": rule.target_entity,
            "reset_type": rule.reset_type,
            "timestamp": timestamp,
            "camera_id": camera_id,
            "expires_at": timestamp + (self._T_reset_pending_confirm_sec * 1000),
            "confirmed": False,
        }

        # Устанавливаем состояние reset_pending в FSM
        fsm = self._get_fsm_for_entity(rule.target_entity)
        if fsm:
            self._apply_state_to_fsm(fsm, track_id, self._sanitary_states["reset_pending"])

    def confirm_reset_pending(self, track_id: str, entity_type: str) -> bool:
        """
        Подтверждение reset_pending

        Если подтверждение в окне T_reset_pending_confirm_sec отсутствует,
        сущность возвращается в clean_degrading.
        """
        current_time = time.time()

        for key, pending in list(self._reset_pending_states.items()):
            if pending["track_id"] != track_id:
                continue
            if pending["entity_type"] != entity_type:
                continue
            if pending.get("confirmed", False):
                continue

            # Проверка истечения окна
            if current_time > pending["expires_at"]:
                # Окно истекло -> clean_degrading
                logger.info(f"Reset pending expired: {track_id} ({entity_type}) -> clean_degrading")
                self._reset_pending_states[key]["confirmed"] = True

                fsm = self._get_fsm_for_entity(entity_type)
                if fsm:
                    self._apply_state_to_fsm(fsm, track_id, self._sanitary_states["clean_degrading"])
                return False

            # Подтверждение получено -> sanitized
            logger.info(f"Reset pending confirmed: {track_id} ({entity_type}) -> sanitized")
            self._reset_pending_states[key]["confirmed"] = True

            fsm = self._get_fsm_for_entity(entity_type)
            if fsm:
                self._apply_state_to_fsm(fsm, track_id, self._sanitary_states["sanitized"])
            return True

        return False

    def _get_fsm_for_entity(self, entity_type: str):
        """Получение FSM для типа сущности"""
        if self._fsm_hub is None:
            return None
        if entity_type == "employee":
            return self._fsm_hub.employee_context
        elif entity_type == "hands":
            return self._fsm_hub.hands_context
        elif entity_type == "tool":
            return self._fsm_hub.tool_context
        elif entity_type == "container":
            return self._fsm_hub.container_context
        elif entity_type == "surface":
            return self._fsm_hub.surface_context
        elif entity_type == "zone":
            return self._fsm_hub.zone_context
        return None

    def _apply_state_to_fsm(self, fsm, track_id: str, state: str) -> None:
        """Применение состояния к FSM"""
        if hasattr(fsm, 'set_sanitary_state'):
            fsm.set_sanitary_state(track_id=track_id, new_state=state)
        else:
            logger.warning(f"FSM does not support set_sanitary_state: {fsm}")

    def process_reset_event(self, normalized_event: Dict[str, Any]) -> List[ResetResultForTTL]:
        """
        Args:
            normalized_event: полностью нормализованное событие

        Returns:
            Список ResetResultForTTL в виде словаря для передачи в TTL логику
            Каждый элемент содержит: entity_type, track_id, reset_type, ttl_reset_type, new_state
        """
        # Извлечение данных из нормализованного события
        event_type = normalized_event.get("event_type")
        if event_type != "reset_event":
            logger.warning(f"Expected reset_event, got: {event_type}")
            return []

        payload = normalized_event.get("payload", {})
        camera_id = normalized_event.get("camera_id", "unknown")

        reset_type = payload.get("reset_type")
        target_entities = payload.get("target_entities", {})  # {"entity_type": track_id}
        confidence = normalized_event.get("confidence", 0.0)
        timestamp = normalized_event.get("timestamp", time.time())

        # Валидация
        if not self._validate_reset_type(reset_type):
            logger.warning(f"Invalid reset_type: {reset_type}")
            return []

        if not target_entities or not isinstance(target_entities, dict):
            logger.warning("target_entities must be a dict: {entity_type: track_id}")
            return []

        # Получение правил для данного reset_type
        rules = self._get_reset_rules(reset_type)
        if not rules:
            logger.warning(f"No rules found for reset_type: {reset_type}")
            return []

        results = []

        # Применение reset к каждой целевой сущности
        for entity_type, track_id in target_entities.items():
            # Валидация entity_type
            if not self._validate_entity_type(entity_type):
                logger.warning(f"Invalid entity_type: {entity_type}")
                continue

            # Поиск правила для данной сущности
            matching_rules = [r for r in rules if r.target_entity == entity_type]

            for rule in matching_rules:
                result = self.apply_reset_to_entity(
                    rule=rule,
                    track_id=track_id,
                    camera_id=camera_id,
                    confidence=confidence,
                    timestamp=timestamp,
                )

                if result:
                    results.append(result)

        self._check_expired_reset_pending()

        return results

    def _check_expired_reset_pending(self) -> None:
        """
        Проверка истекших reset_pending

        Если подтверждение в окне T_reset_pending_confirm_sec отсутствует,
        сущность возвращается в clean_degrading.
        """
        current_time = time.time()

        for key, pending in list(self._reset_pending_states.items()):
            if pending.get("confirmed", False):
                continue

            if current_time > pending["expires_at"]:
                # Окно истекло -> clean_degrading
                track_id = pending["track_id"]
                entity_type = pending["entity_type"]

                logger.info(f"Reset pending expired: {track_id} ({entity_type}) -> clean_degrading")
                self._reset_pending_states[key]["confirmed"] = True

                fsm = self._get_fsm_for_entity(entity_type)
                if fsm:
                    self._apply_state_to_fsm(fsm, track_id, self._sanitary_states["clean_degrading"])

    def get_sanitary_state(self, track_id: str, entity_type: str) -> Optional[str]:
        """Получение текущего санитарного состояния сущности"""
        fsm = self._get_fsm_for_entity(entity_type)
        if fsm and hasattr(fsm, 'get_sanitary_state'):
            return fsm.get_sanitary_state(track_id)

        # Fallback: из локального хранилища
        if track_id in self._entity_states:
            return self._entity_states[track_id].get(entity_type, {}).get("state")
        return None

    def reset_all_for_employee(self, employee_track_id: str, camera_id: str) -> List[ResetResultForTTL]:
        """
        Сброс всех состояний для сотрудника (при shift_started)

        Returns:
            Список ResetResultForTTL для передачи в TTL логику
        """
        results: List[ResetResultForTTL] = []

        for entity_type in ["employee", "hands", "tool", "container", "surface"]:
            fsm = self._get_fsm_for_entity(entity_type)
            if fsm:
                self._apply_state_to_fsm(fsm, employee_track_id, self._sanitary_states["clean_confirmed"])

                results.append(ResetResultForTTL(
                    entity_type=entity_type,
                    track_id=employee_track_id,
                    reset_type="shift_started",
                    ttl_reset_type="full",
                    new_state=self._sanitary_states["clean_confirmed"],
                ))

        return results


if __name__ == "__main__":
    pkt = {
        "event_id": str(uuid.uuid4()),
        "event_type": "reset_event",
        "timestamp": time.time(),
        "camera_id": "Kitchen_1",
        "entity_id": 23,
        "entity_type": "employee",
        "confidence": 0.91,
        "time_source": "camera_rtsp",
        "payload": {
            "reset_type": "shift_started",
            "target_entities": {
                "employee": 23,
                "hands": 12,
            },
            "confidence": 0.91,
        }
    }

    reset_logic = ResetLogic(fsm_hub=None)
    r = reset_logic.process_reset_event(normalized_event=pkt)
    print(r)
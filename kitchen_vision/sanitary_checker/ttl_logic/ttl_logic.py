import logging
import time
import uuid
from typing import Dict, Any
from dataclasses import dataclass

try:
    from kitchen_vision.sanitary_checker.fsm.fsm_hub import FSMHub
except ImportError as e:
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ttl_logic")

SANITARY_STATES = {
    "clean_confirmed": "clean_confirmed",
    "clean_degrading": "clean_degrading",
    "contaminated": "contaminated",
    "unknown": "unknown",
}

ENTITY_WITH_TTL = {
    "employee": "employee",
    "hands": "hands",
    "tool": "tool",
    "container": "container",
    "surface": "surface",
}

ENTITY_TTL = {
    "ttl_hands_confirm_sec": 300,
    "ttl_hands_degrading_sec": 120,
    "ttl_tool_confirm_sec": 600,
    "ttl_tool_degrading_sec": 180,
    "ttl_container_confirm_sec": 600,
    "ttl_container_degrading_sec": 180,
    "ttl_surface_confirm_sec": 900,
    "ttl_surface_degrading_sec": 300,
    "ttl_employee_confirm_sec": 300,
    "ttl_employee_degrading_sec": 120,
}

TTL_RESET_TYPES = {
    "full": "full",
    "partial": "partial",
    "none": "none",
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


class TTLLogic:
    def __init__(self, fsm_hub: FSMHub):
        self._fsm_hub = fsm_hub

    def _get_sanitary_state(self, fsm, track_id: str):
        if fsm is None:
            return None
        if hasattr(fsm, "get_sanitary_state"):
            return fsm.get_sanitary_state(track_id=track_id)
        return None

    def _get_last_event_timestamp(self, fsm, track_id: str):
        if fsm is None:
            return None
        if hasattr(fsm, "get_last_event_timestamp"):
            return fsm.get_last_event_timestamp(track_id=track_id)
        return None

    def _get_ttl_remaining(self, fsm, track_id: str):
        if fsm is None:
            return None
        if hasattr(fsm, "get_ttl_remaining"):
            return fsm.get_ttl_remaining(track_id=track_id)
        return None

    def _validate_entity_type(self, entity_type: str):
        if entity_type not in ENTITY_WITH_TTL:
            logger.info(f"entity_type:{entity_type} has no TTL")
            return False
        return True

    def _validate_reset_type(self, reset_type: str):
        if reset_type not in TTL_RESET_TYPES:
            logger.info(f"reset_type:{reset_type} unknown")
            return False
        return True

    def _set_new_entity_state(self, fsm, new_state: str, track_id: str):
        if fsm is None:
            return
        if hasattr(fsm, "set_sanitary_state"):
            fsm.set_sanitary_state(track_id=track_id, new_state=new_state)

    def _set_new_entity_context(self, fsm, new_context: str, track_id: str):
        if fsm is None:
            return
        if hasattr(fsm, "set_context"):
            fsm.set_context(track_id=track_id, new_context=new_context)

    def _set_ttl_remaining(self, fsm, track_id: str, new_ttl: int):
        if fsm is None:
            return None
        if hasattr(fsm, "set_ttl_remaining"):
            fsm.set_ttl_remaining(track_id=track_id, new_ttl=new_ttl)
        return None

    def _update_ttl_remaining(self, fsm, track_id: str, additional_ttl: int):
        if fsm is None:
            return None
        if hasattr(fsm, "update_ttl_remaining"):
            fsm.set_ttl_remaining(track_id=track_id, additional_ttl=additional_ttl)
        return None

    def _update_last_event_timestamp(self, fsm, track_id: str):
        if fsm is None:
            return
        if hasattr(fsm, "update_last_event_timestamp"):
            fsm.update_last_event_timestamp(track_id=track_id)

    def _process_normal_ttl_event(self, normalized_event: Dict[str, Any]):
        """Обработка TTL логики не reset-события"""
        entity_id = normalized_event.get("entity_id")
        entity_type = normalized_event.get("entity_type")
        if not self._validate_entity_type(entity_type=entity_type):
            return

        fsm = self._get_fsm_for_entity(entity_type=entity_type)
        if fsm is None:
            logger.info(f"no FSM found for entity_type:{entity_type}")
            return

        sanitary_state = self._get_sanitary_state(fsm=fsm, track_id=entity_id)
        ttl_remaining = self._get_ttl_remaining(fsm=fsm, track_id=entity_id)
        last_event_timestamp = self._get_last_event_timestamp(fsm=fsm, track_id=entity_id)
        current_timestamp = time.time()
        if sanitary_state == SANITARY_STATES.get("clean_confirmed"):
            # Получаем временной промежуток с момента последней проверки
            time_gap = current_timestamp - last_event_timestamp
            # Подсчет оставшегося времени, при котором сохраняется текущее состояние
            new_ttl_remaining = ttl_remaining - time_gap

            if entity_type == ENTITY_WITH_TTL.get("employee"):
                # Если время истекло -> переход в состояние clean_degrading
                if new_ttl_remaining <= 0:
                    self._set_new_entity_state(fsm=fsm, new_state="clean_degrading", track_id=entity_id)
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                            new_ttl=ENTITY_TTL.get("ttl_employee_degrading_sec"))
                else:
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id, new_ttl=new_ttl_remaining)

            elif entity_type == ENTITY_WITH_TTL.get("hands"):
                if new_ttl_remaining <= 0:
                    self._set_new_entity_state(fsm=fsm, new_state="clean_degrading", track_id=entity_id)
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                            new_ttl=ENTITY_TTL.get("ttl_hands_degrading_sec"))
                else:
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id, new_ttl=new_ttl_remaining)

            elif entity_type == ENTITY_WITH_TTL.get("tool"):
                if new_ttl_remaining <= 0:
                    self._set_new_entity_state(fsm=fsm, new_state="clean_degrading", track_id=entity_id)
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                            new_ttl=ENTITY_TTL.get("ttl_tool_degrading_sec"))
                else:
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id, new_ttl=new_ttl_remaining)

            elif entity_type == ENTITY_WITH_TTL.get("container"):
                if new_ttl_remaining <= 0:
                    self._set_new_entity_state(fsm=fsm, new_state="clean_degrading", track_id=entity_id)
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                            new_ttl=ENTITY_TTL.get("ttl_container_degrading_sec"))
                else:
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id, new_ttl=new_ttl_remaining)

            elif entity_type == ENTITY_WITH_TTL.get("surface"):
                if new_ttl_remaining <= 0:
                    self._set_new_entity_state(fsm=fsm, new_state="clean_degrading", track_id=entity_id)
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                            new_ttl=ENTITY_TTL.get("ttl_surface_degrading_sec"))
                else:
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id, new_ttl=new_ttl_remaining)

            # Обновление контекста
            self._set_new_entity_context(fsm=fsm, new_context="unknown", track_id=entity_id)
            # Обновление времени последней проверки
            self._update_last_event_timestamp(fsm=fsm, track_id=entity_id)

        # ===============================================================================================
        elif sanitary_state == SANITARY_STATES.get("clean_degrading"):
            time_gap = current_timestamp - last_event_timestamp
            new_ttl_remaining = ttl_remaining - time_gap

            if entity_type == ENTITY_WITH_TTL.get("employee"):
                if new_ttl_remaining <= 0:
                    self._set_new_entity_state(fsm=fsm, new_state="unknown", track_id=entity_id)
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                            new_ttl=0)
                else:
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id, new_ttl=new_ttl_remaining)

            elif entity_type == ENTITY_WITH_TTL.get("hands"):
                if new_ttl_remaining <= 0:
                    self._set_new_entity_state(fsm=fsm, new_state="unknown", track_id=entity_id)
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                            new_ttl=0)
                else:
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id, new_ttl=new_ttl_remaining)

            elif entity_type == ENTITY_WITH_TTL.get("tool"):
                if new_ttl_remaining <= 0:
                    self._set_new_entity_state(fsm=fsm, new_state="unknown", track_id=entity_id)
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                            new_ttl=0)
                else:
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id, new_ttl=new_ttl_remaining)

            elif entity_type == ENTITY_WITH_TTL.get("container"):
                if new_ttl_remaining <= 0:
                    self._set_new_entity_state(fsm=fsm, new_state="unknown", track_id=entity_id)
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                            new_ttl=0)
                else:
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id, new_ttl=new_ttl_remaining)

            elif entity_type == ENTITY_WITH_TTL.get("surface"):
                if new_ttl_remaining <= 0:
                    self._set_new_entity_state(fsm=fsm, new_state="unknown", track_id=entity_id)
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                            new_ttl=0)
                else:
                    self._set_ttl_remaining(fsm=fsm, track_id=entity_id, new_ttl=new_ttl_remaining)

            self._set_new_entity_context(fsm=fsm, new_context="unknown", track_id=entity_id)
            self._update_last_event_timestamp(fsm=fsm, track_id=entity_id)

    def _process_reset_ttl_event(self, normalized_event: Dict[str, Any], reset_result_for_ttl: list[Dict[str, Any]]):
        """Обработка TTL логики reset-события"""
        payload = normalized_event.get("payload")
        if payload is None:
            logger.error("TTL event payload is None in event: {}".format(normalized_event.get("event_id")))
            return

        for item in reset_result_for_ttl:
            entity_id = item.get("track_id")
            entity_type = item.get("entity_type")
            ttl_reset_type = item.get("ttl_reset_type")
            new_state = item.get("new_state")

            if not self._validate_entity_type(entity_type=entity_type):
                continue

            if not self._validate_reset_type(reset_type=ttl_reset_type):
                continue

            if ttl_reset_type == TTL_RESET_TYPES.get("none"):
                logger.info("TTL reset type is none, skipping...")
                continue

            if new_state == "reset_pending":
                continue

            fsm = self._get_fsm_for_entity(entity_type=entity_type)
            if fsm is None:
                logger.info(f"no FSM found for entity_type:{entity_type}")
                return

            if new_state == SANITARY_STATES.get("clean_confirmed"):
                if entity_type == ENTITY_WITH_TTL.get("employee"):
                    if ttl_reset_type == TTL_RESET_TYPES.get("full"):
                        self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                new_ttl=ENTITY_TTL.get("ttl_employee_confirm_sec"))
                    elif ttl_reset_type == TTL_RESET_TYPES.get("partial"):
                        self._update_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                   additional_ttl=ENTITY_TTL.get("ttl_employee_confirm_sec"))
                if entity_type == ENTITY_WITH_TTL.get("hands"):
                    if ttl_reset_type == TTL_RESET_TYPES.get("full"):
                        self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                new_ttl=ENTITY_TTL.get("ttl_hands_confirm_sec"))
                    elif ttl_reset_type == TTL_RESET_TYPES.get("partial"):
                        self._update_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                   additional_ttl=ENTITY_TTL.get("ttl_hands_confirm_sec"))
                if entity_type == ENTITY_WITH_TTL.get("tool"):
                    if ttl_reset_type == TTL_RESET_TYPES.get("full"):
                        self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                new_ttl=ENTITY_TTL.get("ttl_tool_confirm_sec"))
                    elif ttl_reset_type == TTL_RESET_TYPES.get("partial"):
                        self._update_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                   additional_ttl=ENTITY_TTL.get("ttl_tool_confirm_sec"))
                if entity_type == ENTITY_WITH_TTL.get("container"):
                    if ttl_reset_type == TTL_RESET_TYPES.get("full"):
                        self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                new_ttl=ENTITY_TTL.get("ttl_container_confirm_sec"))
                    elif ttl_reset_type == TTL_RESET_TYPES.get("partial"):
                        self._update_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                   additional_ttl=ENTITY_TTL.get("ttl_container_confirm_sec"))
                if entity_type == ENTITY_WITH_TTL.get("surface"):
                    if ttl_reset_type == TTL_RESET_TYPES.get("full"):
                        self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                new_ttl=ENTITY_TTL.get("ttl_surface_confirm_sec"))
                    elif ttl_reset_type == TTL_RESET_TYPES.get("partial"):
                        self._update_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                   additional_ttl=ENTITY_TTL.get("ttl_surface_confirm_sec"))

                self._set_new_entity_context(fsm=fsm, new_context="sanitized", track_id=entity_id)

            elif new_state == SANITARY_STATES.get("clean_degrading"):
                if entity_type == ENTITY_WITH_TTL.get("employee"):
                    if ttl_reset_type == TTL_RESET_TYPES.get("full"):
                        self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                new_ttl=ENTITY_TTL.get("ttl_employee_degrading_sec"))
                    elif ttl_reset_type == TTL_RESET_TYPES.get("partial"):
                        self._update_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                   additional_ttl=ENTITY_TTL.get("ttl_employee_degrading_sec"))
                if entity_type == ENTITY_WITH_TTL.get("hands"):
                    if ttl_reset_type == TTL_RESET_TYPES.get("full"):
                        self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                new_ttl=ENTITY_TTL.get("ttl_employee_degrading_sec"))
                    elif ttl_reset_type == TTL_RESET_TYPES.get("partial"):
                        self._update_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                   additional_ttl=ENTITY_TTL.get("ttl_employee_degrading_sec"))
                if entity_type == ENTITY_WITH_TTL.get("tool"):
                    if ttl_reset_type == TTL_RESET_TYPES.get("full"):
                        self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                new_ttl=ENTITY_TTL.get("ttl_employee_degrading_sec"))
                    elif ttl_reset_type == TTL_RESET_TYPES.get("partial"):
                        self._update_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                   additional_ttl=ENTITY_TTL.get("ttl_employee_degrading_sec"))
                if entity_type == ENTITY_WITH_TTL.get("container"):
                    if ttl_reset_type == TTL_RESET_TYPES.get("full"):
                        self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                new_ttl=ENTITY_TTL.get("ttl_employee_degrading_sec"))
                    elif ttl_reset_type == TTL_RESET_TYPES.get("partial"):
                        self._update_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                   additional_ttl=ENTITY_TTL.get("ttl_employee_degrading_sec"))
                if entity_type == ENTITY_WITH_TTL.get("surface"):
                    if ttl_reset_type == TTL_RESET_TYPES.get("full"):
                        self._set_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                new_ttl=ENTITY_TTL.get("ttl_employee_degrading_sec"))
                    elif ttl_reset_type == TTL_RESET_TYPES.get("partial"):
                        self._update_ttl_remaining(fsm=fsm, track_id=entity_id,
                                                   additional_ttl=ENTITY_TTL.get("ttl_employee_degrading_sec"))

                self._set_new_entity_context(fsm=fsm, new_context="unknown", track_id=entity_id)

            self._update_last_event_timestamp(fsm=fsm, track_id=entity_id)

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

    def process_ttl_event(self, normalized_event: Dict[str, Any], reset_result_for_ttl: list[Dict[str, Any]]):
        # Проверка было ли предыдущее событие reset-событием
        if len(reset_result_for_ttl) == 0:
            self._process_normal_ttl_event(normalized_event=normalized_event)
        elif len(reset_result_for_ttl) >= 1:
            self._process_reset_ttl_event(normalized_event=normalized_event, reset_result_for_ttl=reset_result_for_ttl)


if __name__ == "__main__":
    ttl_logic = TTLLogic(fsm_hub=None)
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
    lst = [
        {'entity_type': 'employee', 'track_id': 23, 'reset_type': 'shift_started', 'ttl_reset_type': 'full',
         'new_state': 'clean_confirmed'},
        {'entity_type': 'hands', 'track_id': 12, 'reset_type': 'shift_started', 'ttl_reset_type': 'full',
         'new_state': 'clean_confirmed'}
    ]
    ttl_logic.process_ttl_event(normalized_event=pkt, reset_result_for_ttl=lst)

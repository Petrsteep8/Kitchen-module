import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from collections import defaultdict

try:
    from kitchen_vision.sanitary_checker.fsm.fsm_hub import FSMHub
    from kitchen_vision.sanitary_checker.context_transition_engine.compatibility_matrix import (
        CompatibilityMatrix,
        CompatibilityViolation,
    )
except ImportError:
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("context_transfer_table")


CONTEXTS = {
    "neutral": "neutral",
    "unknown": "unknown",
    "raw_animal": "raw_animal",
    "raw_plant_unwashed": "raw_plant_unwashed",
    "clean_prep": "clean_prep",
    "ready_to_eat": "ready_to_eat",
    "packaging_dispatch": "packaging_dispatch",
    "waste": "waste",
    "dirty_dishes_containers": "dirty_dishes_containers",
    "cleaning_inventory": "cleaning_inventory",
    "nonfood_contact": "nonfood_contact",
    "dirty_zone": "dirty_zone",
    "clean_zone": "clean_zone",
    "sanitized": "sanitized",
    "contaminated": "contaminated",
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

INTERACTION_TYPES = ["contact", "hold", "place", "carry", "touch", "transfer"]


@dataclass
class TransferRule:
    """Правило переноса контекста"""
    source_entity: str
    target_entity: str
    interaction_type: str
    source_context: str
    target_context: str
    min_confidence: float = 0.6
    priority: int = 0


# Таблица переноса
TRANSFER_RULES = [
    # food -> tool
    TransferRule("food", "tool", "contact", "raw_animal", "raw_animal", 0.6, 1),
    TransferRule("food", "tool", "hold", "raw_animal", "raw_animal", 0.6, 1),
    TransferRule("food", "tool", "carry", "raw_animal", "raw_animal", 0.6, 1),
    TransferRule("food", "tool", "contact", "raw_plant_unwashed", "raw_plant_unwashed", 0.6, 1),
    TransferRule("food", "tool", "hold", "raw_plant_unwashed", "raw_plant_unwashed", 0.6, 1),
    TransferRule("food", "tool", "carry", "raw_plant_unwashed", "raw_plant_unwashed", 0.6, 1),
    TransferRule("food", "tool", "place", "*", "source_context", 0.6, 0),

    # food -> hands
    TransferRule("food", "hands", "contact", "raw_animal", "raw_animal", 0.6, 1),
    TransferRule("food", "hands", "hold", "raw_animal", "raw_animal", 0.6, 1),
    TransferRule("food", "hands", "carry", "raw_animal", "raw_animal", 0.6, 1),
    TransferRule("food", "hands", "contact", "raw_plant_unwashed", "raw_plant_unwashed", 0.6, 1),
    TransferRule("food", "hands", "hold", "raw_plant_unwashed", "raw_plant_unwashed", 0.6, 1),
    TransferRule("food", "hands", "carry", "raw_plant_unwashed", "raw_plant_unwashed", 0.6, 1),

    # tool -> hands
    TransferRule("tool", "hands", "contact", "*", "source_context", 0.6, 1),
    TransferRule("tool", "hands", "hold", "*", "source_context", 0.6, 1),

    # hands -> container
    TransferRule("hands", "container", "contact", "*", "source_context", 0.6, 1),
    TransferRule("hands", "container", "place", "*", "source_context", 0.6, 1),

    # hands -> surface
    TransferRule("hands", "surface", "contact", "*", "source_context", 0.6, 1),

    # surface -> food
    TransferRule("surface", "food", "contact", "*", "contaminated", 0.6, 1),
    TransferRule("surface", "food", "place", "*", "contaminated", 0.6, 1),

    # employee -> tool
    TransferRule("employee", "tool", "hold", "*", "source_context", 0.6, 1),
    TransferRule("employee", "tool", "carry", "*", "source_context", 0.6, 1),

    # employee -> container
    TransferRule("employee", "container", "hold", "*", "source_context", 0.6, 1),
    TransferRule("employee", "container", "carry", "*", "source_context", 0.6, 1),

    # zone -> employee (dwell)
    TransferRule("zone", "employee", "dwell", "*", "source_context", 0.6, 1),

    # zone -> food, tool, container (dwell)
    TransferRule("zone", "food", "dwell", "*", "source_context", 0.6, 1),
    TransferRule("zone", "tool", "dwell", "*", "source_context", 0.6, 1),
    TransferRule("zone", "container", "dwell", "*", "source_context", 0.6, 1),
]

TRANSFER_INDEX = defaultdict(list)
for rule in TRANSFER_RULES:
    TRANSFER_INDEX[(rule.source_entity, rule.target_entity, rule.interaction_type)].append(rule)


class ContextTransferTable:
    """
    Механизм переноса контекста
    Проверка совместимости переходов
    Генерация нарушений совместимости (для SANITARY_EVENT)
    """

    def __init__(
        self,
        fsm_hub: FSMHub,
        config: Optional[Dict[str, Any]] = None,
    ):
        self._fsm_hub = fsm_hub
        self._config = config or {}
        self._compatibility_matrix = CompatibilityMatrix(config)

        # Канонические словари
        self._contexts = CONTEXTS
        self._entity_types = ENTITY_TYPES
        self._interaction_types = INTERACTION_TYPES

        # Параметры из конфигурации
        self._min_confidence_transfer = self._config.get("min_confidence_transfer", 0.6)

        # Хранилище текущих контекстов (для отладки и fallback)
        self._entity_contexts: Dict[str, Dict[str, str]] = defaultdict(dict)

    def _get_fsm(self, entity_type: str):
        """Получение FSM для сущности"""
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
        elif entity_type == "food":
            return None
        return None

    def _get_context(self, entity_type: str, entity_id: str) -> Optional[str]:
        """Получение контекста сущности из FSM"""
        fsm = self._get_fsm(entity_type)
        if fsm and hasattr(fsm, 'get_context'):
            return fsm.get_context(entity_id)
        # Fallback: из локального хранилища
        return self._entity_contexts.get(entity_id, {}).get(entity_type)

    def _set_context(self, entity_type: str, entity_id: str, context: str) -> None:
        """Установка контекста сущности в FSM"""
        fsm = self._get_fsm(entity_type)
        if fsm and hasattr(fsm, 'set_context'):
            fsm.set_context(track_id=entity_id, new_context=context)
        else:
            # Fallback: локальное хранилище
            if entity_id not in self._entity_contexts:
                self._entity_contexts[entity_id] = {}
            self._entity_contexts[entity_id][entity_type] = context

    def _apply_transfer_rules(
        self,
        source_entity: str,
        target_entity: str,
        interaction_type: str,
        source_context: str,
        target_entity_id: str,
        confidence: float,
    ) -> Optional[str]:
        """
        Применение таблицы переноса
        Возвращает новый контекст для целевой сущности или None, если правило не найдено
        """
        key = (source_entity, target_entity, interaction_type)
        rules = TRANSFER_INDEX.get(key, [])

        if not rules:
            return None

        # Фильтруем правила по source_context (если не "*")
        applicable = []
        for rule in rules:
            if rule.source_context == "*" or rule.source_context == source_context:
                applicable.append(rule)

        if not applicable:
            return None

        # Выбираем правило
        applicable.sort(key=lambda r: (r.priority, r.min_confidence), reverse=True)
        best_rule = applicable[0]

        if confidence < best_rule.min_confidence:
            logger.debug(
                f"Transfer rule skipped due to low confidence: {best_rule.min_confidence} > {confidence}"
            )
            return None

        # Определяем новый контекст
        if best_rule.target_context == "source_context":
            new_context = source_context
        else:
            new_context = best_rule.target_context

        return new_context

    def process_event(
        self,
        normalized_event: Dict[str, Any],
    ) -> List[CompatibilityViolation]:
        """
        Обработка INTERACTION события

        Возвращает список нарушений совместимости (для генерации SANITARY_EVENT)
        """
        violations: List[CompatibilityViolation] = []

        # Проверяем тип события
        if normalized_event.get("event_type") != "interaction":
            logger.warning("Expected interaction event, got: %s", normalized_event.get("event_type"))
            return violations

        payload = normalized_event.get("payload", {})
        camera_id = normalized_event.get("camera_id", "unknown")
        timestamp = normalized_event.get("timestamp", 0)
        confidence = normalized_event.get("confidence", 0.0)

        source_entity = payload.get("actor_entity_type")
        source_entity_id = payload.get("actor_entity_id")
        target_entity = payload.get("target_entity_type")
        target_entity_id = payload.get("target_entity_id")
        interaction_type = payload.get("interaction_type")

        # Валидация
        if not all([source_entity, source_entity_id, target_entity, target_entity_id, interaction_type]):
            logger.warning("Missing required fields in interaction event")
            return violations

        # Получаем текущие контексты
        source_context = self._get_context(source_entity, source_entity_id)
        target_current_context = self._get_context(target_entity, target_entity_id)

        if source_context is None:
            source_context = "unknown"
        if target_current_context is None:
            target_current_context = "unknown"

        new_context = self._apply_transfer_rules(
            source_entity=source_entity,
            target_entity=target_entity,
            interaction_type=interaction_type,
            source_context=source_context,
            target_entity_id=target_entity_id,
            confidence=confidence,
        )

        final_new_context = new_context if new_context is not None else target_current_context

        violation = self._compatibility_matrix.check_transition(
            target_context=final_new_context,
            source_context=target_current_context,
            entity_id=target_entity_id,
            entity_type=target_entity,
            camera_id=camera_id,
            timestamp=timestamp,
            confidence=confidence,
        )
        if violation:
            violations.append(violation)

        # Применяем новый контекст к целевой сущности
        if final_new_context != target_current_context:
            self._set_context(target_entity, target_entity_id, final_new_context)
            logger.debug(
                f"Context transferred: {target_entity}:{target_entity_id} "
                f"{target_current_context} -> {final_new_context} (via {source_entity}:{source_entity_id})"
            )
        else:
            logger.debug(f"No context change for {target_entity}:{target_entity_id}")

        return violations
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from collections import defaultdict

try:
    from kitchen_vision.sanitary_checker.fsm.fsm_hub import FSMHub
except ImportError:
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("contamination_table")


CONTAMINATION_TYPES = {
    "raw_food": "raw_food",
    "waste": "waste",
    "dirty_surface": "dirty_surface",
    "packaging": "packaging",
    "nonfood_contact": "nonfood_contact",
    "unknown": "unknown",
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

SANITARY_STATES = {
    "CLEAN_CONFIRMED": "clean_confirmed",
    "CLEAN_DEGRADING": "clean_degrading",
    "UNKNOWN": "unknown",
    "CONTAMINATED": "contaminated",
    "SANITIZED": "sanitized",
    "RESET_PENDING": "reset_pending",
}


@dataclass
class ContaminationRule:
    """Правило загрязнения"""
    contamination_type: str
    target_entity: str
    resulting_state: str
    priority: int
    min_confidence: float = 0.6


CONTAMINATION_RULES = [
    ContaminationRule("raw_food", "hands", "contaminated", 1, 0.6),
    ContaminationRule("raw_food", "tool", "contaminated", 1, 0.6),
    ContaminationRule("raw_food", "container", "contaminated", 1, 0.6),
    ContaminationRule("raw_food", "surface", "contaminated", 1, 0.6),
    ContaminationRule("raw_food", "employee", "contaminated", 1, 0.6),

    ContaminationRule("waste", "hands", "contaminated", 1, 0.6),
    ContaminationRule("waste", "tool", "contaminated", 1, 0.6),
    ContaminationRule("waste", "container", "contaminated", 1, 0.6),
    ContaminationRule("waste", "surface", "contaminated", 1, 0.6),
    ContaminationRule("waste", "employee", "contaminated", 1, 0.6),

    ContaminationRule("dirty_surface", "hands", "contaminated", 2, 0.6),
    ContaminationRule("dirty_surface", "tool", "contaminated", 2, 0.6),
    ContaminationRule("dirty_surface", "container", "contaminated", 2, 0.6),
    ContaminationRule("dirty_surface", "surface", "contaminated", 2, 0.6),

    ContaminationRule("packaging", "hands", "contaminated", 3, 0.6),
    ContaminationRule("packaging", "tool", "contaminated", 3, 0.6),
    ContaminationRule("packaging", "container", "contaminated", 3, 0.6),
    ContaminationRule("packaging", "surface", "contaminated", 3, 0.6),

    ContaminationRule("nonfood_contact", "hands", "contaminated", 2, 0.6),
    ContaminationRule("nonfood_contact", "tool", "contaminated", 2, 0.6),
    ContaminationRule("nonfood_contact", "container", "contaminated", 2, 0.6),
    ContaminationRule("nonfood_contact", "surface", "contaminated", 2, 0.6),

    ContaminationRule("unknown", "hands", "unknown", 4, 0.6),
    ContaminationRule("unknown", "tool", "unknown", 4, 0.6),
    ContaminationRule("unknown", "container", "unknown", 4, 0.6),
    ContaminationRule("unknown", "surface", "unknown", 4, 0.6),
    ContaminationRule("unknown", "employee", "unknown", 4, 0.6),
]

CONTAMINATION_INDEX = defaultdict(list)
for rule in CONTAMINATION_RULES:
    CONTAMINATION_INDEX[(rule.contamination_type, rule.target_entity)].append(rule)


class ContaminationTable:
    """
    Применение таблицы загрязнения
    Устанавливает санитарное состояние сущности (contaminated или unknown)
    с учётом приоритетов
    """

    def __init__(
        self,
        fsm_hub: FSMHub,
        config: Optional[Dict[str, Any]] = None,
    ):
        self._fsm_hub = fsm_hub
        self._config = config or {}

        # Канонические словари
        self._contamination_types = CONTAMINATION_TYPES
        self._entity_types = ENTITY_TYPES
        self._sanitary_states = SANITARY_STATES

        # Параметры из конфигурации
        self._min_confidence = self._config.get("min_confidence_contamination", 0.6)

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
        else:
            return None

    def process_event(self, normalized_event: Dict[str, Any]) -> None:
        """
        Обработка CONTAMINATION_EVENT
        Применяет таблицу загрязнения, обновляет состояние в FSM
        """
        # Проверяем тип события
        if normalized_event.get("event_type") != "contamination_event":
            logger.warning("Expected contamination_event, got: %s", normalized_event.get("event_type"))
            return

        payload = normalized_event.get("payload", {})
        confidence = normalized_event.get("confidence", 0.0)

        contamination_type = payload.get("contamination_type")
        target_entity = payload.get("target_entity")
        target_entity_id = payload.get("target_entity_id")  # должно быть в payload

        # Валидация
        if not contamination_type or not target_entity or not target_entity_id:
            logger.warning("Missing required fields in contamination event")
            return

        if contamination_type not in self._contamination_types:
            logger.warning("Invalid contamination_type: %s", contamination_type)
            return

        if target_entity not in self._entity_types:
            logger.warning("Invalid target_entity: %s", target_entity)
            return

        # Получаем все правила для данной пары
        key = (contamination_type, target_entity)
        rules = CONTAMINATION_INDEX.get(key, [])

        if not rules:
            logger.debug("No contamination rule for %s -> %s", contamination_type, target_entity)
            return

        # Выбираем правило с наивысшим приоритетом
        rules.sort(key=lambda r: (r.priority, r.min_confidence), reverse=True)
        best_rule = rules[0]

        if confidence < best_rule.min_confidence:
            logger.debug(
                f"Contamination rule skipped due to low confidence: {best_rule.min_confidence} > {confidence}"
            )
            return

        new_state = best_rule.resulting_state

        fsm = self._get_fsm(target_entity)
        if fsm is None:
            logger.warning("No FSM for entity: %s", target_entity)
            return

        if hasattr(fsm, 'set_sanitary_state'):
            fsm.set_sanitary_state(track_id=target_entity_id, new_state=new_state)
            logger.info(
                f"Contamination applied: {contamination_type} -> {target_entity}:{target_entity_id} -> {new_state}"
            )
        else:
            logger.warning("FSM does not support set_sanitary_state: %s", fsm)
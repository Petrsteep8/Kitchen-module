import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

try:
    from kitchen_vision.sanitary_checker.fsm.fsm_hub import FSMHub
except ImportError:
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("compatibility_matrix")


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

DIRTY_CONTEXTS = [
    "raw_animal",
    "raw_plant_unwashed",
    "waste",
    "dirty_dishes_containers",
    "cleaning_inventory",
    "nonfood_contact",
    "dirty_zone",
    "contaminated",
]

COMPATIBILITY_MATRIX = {
    "ready_to_eat": ["neutral", "sanitized", "clean_prep", "packaging_dispatch"],
    "clean_prep": ["neutral", "sanitized", "clean_zone"],
    "packaging_dispatch": ["neutral", "sanitized", "clean_prep", "ready_to_eat"],
    "clean_zone": ["neutral", "sanitized", "clean_prep", "clean_zone"],
}


@dataclass
class CompatibilityViolation:
    """Информация о нарушении совместимости"""
    target_context: str
    source_context: str
    severity: str = "violation"
    severity_internal: str = "critical"
    incompatibility_cause: str = "forbidden_zone_transition"


class CompatibilityMatrix:
    """
    Проверка совместимости переходов
    Генерирует объект нарушения при невалидном переходе
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        self._contexts = CONTEXTS
        self._dirty_contexts = DIRTY_CONTEXTS
        self._compatibility_matrix = COMPATIBILITY_MATRIX

    def check_transition(
        self,
        target_context: str,
        source_context: str,
        entity_id: str,
        entity_type: str,
        camera_id: str,
        timestamp: int,
        confidence: float = 1.0,
    ) -> Optional[CompatibilityViolation]:
        """
        Проверка перехода из source_context в target_context.

        Если переход запрещён матрицей совместимости, возвращает объект нарушения.
        Иначе возвращает None.
        """
        if target_context not in self._compatibility_matrix:
            return None

        allowed_predecessors = self._compatibility_matrix[target_context]
        if source_context in allowed_predecessors:
            return None

        if target_context in ("ready_to_eat", "packaging_dispatch"):
            severity_internal = "critical"
        elif target_context in ("clean_prep", "clean_zone"):
            severity_internal = "high"
        else:
            severity_internal = "warning"

        if source_context in self._dirty_contexts:
            cause = "contaminated_source_contact"
        else:
            cause = "forbidden_zone_transition"

        logger.warning(
            f"Incompatible transition: {source_context} -> {target_context} "
            f"for {entity_type}:{entity_id} on camera {camera_id}"
        )

        return CompatibilityViolation(
            target_context=target_context,
            source_context=source_context,
            severity="violation",
            severity_internal=severity_internal,
            incompatibility_cause=cause,
        )
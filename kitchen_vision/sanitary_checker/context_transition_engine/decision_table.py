import logging
from typing import Dict, List, Optional, Any
import uuid
import time

try:
    from kitchen_vision.sanitary_checker.fsm.fsm_hub import FSMHub
    from kitchen_vision.sanitary_checker.context_transition_engine.context_transfer_table import ContextTransferTable
    from kitchen_vision.sanitary_checker.context_transition_engine.contamination_table import ContaminationTable
    from kitchen_vision.sanitary_checker.context_transition_engine.compatibility_matrix import CompatibilityViolation
except ImportError:
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("decision_table")


EVENT_TYPES = {
    "interaction": "interaction",
    "contamination_event": "contamination_event",
}

SEVERITY_INTERNAL = ["critical", "high", "medium", "low", "info", "warning", "violation"]
SEVERITY_EXTERNAL = ["info", "warning", "violation"]

SEVERITY_MAPPING = {
    "critical": "violation",
    "high": "violation",
    "violation": "violation",
    "medium": "warning",
    "low": "warning",
    "warning": "warning",
    "info": "info",
}


class DecisionTable:
    """
    Модуль принятия решений для контекстного движка.
    Обрабатывает события interaction и contamination_event:
      - Для interaction: применяет перенос контекста и проверяет совместимость,
        генерирует SANITARY_EVENT при нарушениях.
      - Для contamination_event: применяет загрязнение (обновляет состояние).
    """

    def __init__(
        self,
        context_transfer_table: ContextTransferTable,
        contamination_table: ContaminationTable,
    ):
        self._context_transfer_table = context_transfer_table
        self._contamination_table = contamination_table

        self._event_types = EVENT_TYPES
        self._severity_internal = SEVERITY_INTERNAL
        self._severity_external = SEVERITY_EXTERNAL
        self._severity_mapping = SEVERITY_MAPPING

    def process_event(
        self,
        normalized_event: Dict[str, Any],
        camera_id: str,
    ) -> list[Any] | tuple[bool, list[dict[str, Any]]]:
        """
        Обрабатывает событие и возвращает список сгенерированных SANITARY_EVENT.

        Для interaction: возвращает нарушения совместимости в виде SANITARY_EVENT.
        Для contamination_event: возвращает пустой список (только обновляет состояние).

        Args:
            normalized_event: нормализованное событие
            camera_id: идентификатор камеры

        Returns:
            Список событий SANITARY_EVENT
        """
        event_type = normalized_event.get("event_type")
        if event_type not in self._event_types:
            logger.warning("Unsupported event_type for decision: %s", event_type)
            return []

        generated_events: List[Dict[str, Any]] = []
        result: bool = False

        if event_type == "interaction":
            result, violations = self._context_transfer_table.process_event(normalized_event)

            for violation in violations:
                sanitary_event = self._create_sanitary_event_from_violation(
                    violation=violation,
                    camera_id=camera_id,
                    normalized_event=normalized_event,
                )
                if sanitary_event:
                    generated_events.append(sanitary_event)

        elif event_type == "contamination_event":
            result = self._contamination_table.process_event(normalized_event)

        return result, generated_events

    def _create_sanitary_event_from_violation(
        self,
        violation: CompatibilityViolation,
        camera_id: str,
        normalized_event: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Создаёт SANITARY_EVENT из объекта нарушения
        """
        timestamp = normalized_event.get("timestamp", int(time.time() * 1000))
        payload = normalized_event.get("payload", {})

        target_entity_id = payload.get("target_entity_id")
        target_entity_type = payload.get("target_entity_type")

        if not target_entity_id or not target_entity_type:
            logger.warning("Cannot determine target entity for SANITARY_EVENT")
            return None

        severity_internal = violation.severity_internal
        severity = self._severity_mapping.get(severity_internal, "violation")

        sanitary_event = {
            "sanitary_event_id": str(uuid.uuid4()),
            "sanitary_event_type": "incompatible_transition_detected",
            "source_context": violation.source_context,
            "target_context": violation.target_context,
            "severity": severity,
            "severity_internal": severity_internal,
            "escalation_category": None,
            "incompatibility_cause": violation.incompatibility_cause,
            "rule_id": None,
            "zone_id": payload.get("zone_id"),
            "employee_id": target_entity_id if target_entity_type == "employee" else None,
            "decision_mode": "SANITARY_VIOLATION_CONFIRMED",
        }

        return {
            "event_id": str(uuid.uuid4()),
            "timestamp": timestamp,
            "event_type": "sanitary_event",
            "camera_id": camera_id,
            "entity_id": target_entity_id,
            "entity_type": target_entity_type,
            "confidence": normalized_event.get("confidence", 1.0),
            "time_source": normalized_event.get("time_source", "server_ntp"),
            "late": normalized_event.get("late", False),
            "track_lineage_id": normalized_event.get("track_lineage_id"),
            "executor_attribution": normalized_event.get("executor_attribution"),
            "rules_config_version": normalized_event.get("rules_config_version"),
            "model_versions": normalized_event.get("model_versions"),
            "decision_trace": normalized_event.get("decision_trace"),
            "payload": sanitary_event,
        }
import logging
from typing import Dict, Optional, Any

try:
    from kitchen_vision.sanitary_checker.decision_logic.decision_logic import DecisionLogic, DecisionRule
except ImportError:
    from decision_logic import DecisionLogic, DecisionRule

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("decision_fsm")

EVENT_TYPES = {
    "sanitary_event": "sanitary_event",
    "process_event": "process_event",
    "data_quality_event": "data_quality_event",
}

DECISION_MODES = {
    "sanitary_violation_confirmed": "sanitary_violation_confirmed",
    "process_event_confirmed": "process_event_confirmed",
    "unresolved_due_to_data_quality": "unresolved_due_to_data_quality",
    "data_quality_event_confirmed": "data_quality_event_confirmed",
    "no_event": "no_event",
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


class DecisionFSM:
    """
    Конечный автомат принятия решений
    """

    def __init__(self, decision_logic: DecisionLogic, config: Optional[Dict[str, Any]] = None):
        self._decision_logic = decision_logic
        self._config = config or {}
        self._event_types = EVENT_TYPES
        self._decision_modes = DECISION_MODES
        self._severity_internal = SEVERITY_INTERNAL
        self._severity_external = SEVERITY_EXTERNAL
        self._severity_mapping = SEVERITY_MAPPING

        # Порог уверенности для правила 19
        self._min_confidence_for_violation_source = self._config.get("min_confidence_for_violation_source", 0.7)

    def process_event(self, normalized_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Обработка события: применяет таблицу решений, заполняет decision_mode и rule_id.

        Args:
            normalized_event: нормализованное событие (содержит event_type и payload)

        Returns:
            обновлённое событие с заполненными decision_mode и rule_id (если применимо),
            или None, если событие не обрабатывается данным FSM.
        """
        event_type = normalized_event.get("event_type")
        if event_type not in (self._event_types["sanitary_event"],
                              self._event_types["process_event"],
                              self._event_types["data_quality_event"]):
            return None

        payload = normalized_event.get("payload", {})
        confidence = normalized_event.get("confidence", 1.0)

        sanitary_event_type = payload.get("sanitary_event_type")
        process_event_type = payload.get("process_event_type")
        data_quality_reason = payload.get("reason")

        observation_state = payload.get("observation_state", "obs_ok")
        carrying_type = payload.get("carrying_type")
        handoff_food = payload.get("handoff_food")
        target_context = payload.get("target_context")
        source_context = payload.get("source_context")

        rule = self._decision_logic.lookup_by_conditions(
            event_type=event_type,
            sanitary_event_type=sanitary_event_type,
            process_event_type=process_event_type,
            data_quality_reason=data_quality_reason,
            observation_state=observation_state,
            carrying_type=carrying_type,
            handoff_food=handoff_food,
            target_context=target_context,
            source_context=source_context,
            confidence=confidence,
        )

        if not rule:
            logger.warning(f"No decision rule found for event: {event_type} / {sanitary_event_type or process_event_type or data_quality_reason}")
            return normalized_event

        decision_mode = rule.decision
        if "sanitary_violation_confirmed" in decision_mode:
            decision_mode = self._decision_modes["sanitary_violation_confirmed"]
        elif "process_event_confirmed" in decision_mode:
            decision_mode = self._decision_modes["process_event_confirmed"]
        elif "unresolved_due_to_data_quality" in decision_mode:
            decision_mode = self._decision_modes["unresolved_due_to_data_quality"]
        elif "data_quality_event_confirmed" in decision_mode:
            decision_mode = self._decision_modes["data_quality_event_confirmed"]
        elif "no_event" in decision_mode:
            decision_mode = self._decision_modes["no_event"]
        else:
            decision_mode = self._decision_modes["no_event"]

        severity_internal = rule.severity_internal
        severity = rule.severity

        if not severity_internal:
            severity_internal = payload.get("severity_internal", "warning")
        if not severity:
            severity = self._severity_mapping.get(severity_internal, "warning")

        payload["decision_mode"] = decision_mode

        if rule.rule_id:
            payload["rule_id"] = str(rule.rule_id)

        if "severity" in payload:
            payload["severity"] = severity
        if "severity_internal" in payload:
            payload["severity_internal"] = severity_internal

        if event_type == self._event_types["process_event"]:
            if "decision_mode" not in payload:
                payload["decision_mode"] = decision_mode
        if event_type == self._event_types["data_quality_event"]:
            if "decision_mode" not in payload:
                payload["decision_mode"] = decision_mode

        normalized_event["payload"] = payload

        logger.info(
            f"DecisionFSM: event {event_type} -> {decision_mode} (rule {rule.rule_id}) "
            f"for {sanitary_event_type or process_event_type or data_quality_reason}"
        )

        return normalized_event

    def _get_severity_from_type(self, event_subtype: str) -> tuple:
        if event_subtype in ("food_consumption_in_work_zone", "drinking_in_work_zone"):
            return "medium", "warning"
        elif event_subtype == "food_consumption_after_handling":
            return "high", "violation"
        elif event_subtype in ("no_cap", "no_gloves", "no_apron", "face_touch", "phone_usage"):
            return "warning", "warning"
        elif event_subtype == "mouth_contact_with_hands_or_objects_in_work_zone":
            return "medium", "warning"
        else:
            return "warning", "warning"



if __name__ == "__main__":
    decision_logic = DecisionLogic()
    decision_fsm = DecisionFSM(decision_logic)

    event = {
        "event_id": "evt_1",
        "timestamp": 1234567890,
        "event_type": "sanitary_event",
        "camera_id": "cam1",
        "entity_id": "emp1",
        "entity_type": "employee",
        "confidence": 0.9,
        "time_source": "server_ntp",
        "payload": {
            "sanitary_event_id": "san_1",
            "sanitary_event_type": "incompatible_transition_detected",
            "source_context": "raw_animal",
            "target_context": "ready_to_eat",
            "severity": "violation",
            "severity_internal": "critical",
            "decision_mode": "DECISION_PENDING",
        }
    }

    result = decision_fsm.process_event(event)
    if result:
        print("After DecisionFSM:", result["payload"]["decision_mode"], result["payload"]["rule_id"])
        print("Severity:", result["payload"]["severity_internal"])

    # event2 = {
    #     "event_type": "sanitary_event",
    #     "payload": {
    #         "sanitary_event_type": "left_zone_with_tool_candidate",
    #         "carrying_type": "tool",
    #         "severity": "violation",
    #         "severity_internal": "critical",
    #         "decision_mode": "DECISION_PENDING",
    #     }
    # }
    # result2 = decision_fsm.process_event(event2)
    # if result2:
    #     print("Result2:", result2["payload"]["decision_mode"])
    #
    # event3 = {
    #     "event_type": "process_event",
    #     "payload": {
    #         "process_event_type": "action_interruption",
    #         "severity": "info",
    #         "severity_internal": "info",
    #     }
    # }
    # result3 = decision_fsm.process_event(event3)
    # if result3:
    #     print("Result3:", result3["payload"]["decision_mode"])
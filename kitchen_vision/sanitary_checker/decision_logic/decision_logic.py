import logging
from typing import Optional
from dataclasses import dataclass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("decision_logic")

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

SANITARY_EVENT_TYPES = [
    "incompatible_transition_detected",
    "left_zone_with_tool",
    "left_zone_with_food",
    "intrusion_work_zone",
    "no_cap",
    "no_gloves",
    "no_apron",
    "face_touch",
    "phone_usage",
    "food_consumption_in_work_zone",
    "food_consumption_after_handling",
    "drinking_in_work_zone",
    "alcohol_consumption_in_work_zone",
    "smoking_in_work_zone",
    "mouth_contact_with_hands_or_objects_in_work_zone",
]

PROCESS_EVENT_TYPES = ["food_handoff", "action_interruption", "zone_risk_score"]

DATA_QUALITY_REASONS = [
    "tracking_lost",
    "key_object_lost",
    "ambiguous_handoff_target",
    "OBS_LOST",
    "OBS_DEGRADED",
    "unresolved_due_to_data_quality",
]


@dataclass
class DecisionRule:
    rule_id: int
    condition: str
    decision: str
    severity: str
    severity_internal: str


DECISION_TABLE = [
    DecisionRule(
        1,
        "переход из forbidden_predecessor в ready_to_eat или packaging_dispatch, observation_state = OBS_OK, reset отсутствует",
        "sanitary_violation_confirmed",
        "violation",
        "critical",
    ),
    DecisionRule(
        2,
        "переход из forbidden_predecessor в clean_prep, observation_state = OBS_OK, reset отсутствует",
        "sanitary_violation_confirmed",
        "violation",
        "high",
    ),
    DecisionRule(
        3,
        "переход из forbidden_predecessor в clean_zone, observation_state = OBS_OK, reset отсутствует",
        "sanitary_violation_confirmed",
        "violation",
        "high",
    ),
    DecisionRule(
        4,
        "переход из forbidden_predecessor, observation_state = OBS_WEAK",
        "sanitary_violation_confirmed",
        "warning",
        "warning",
    ),
    DecisionRule(
        5,
        "переход из forbidden_predecessor, observation_state ∈ {OBS_DEGRADED, OBS_LOST}",
        "unresolved_due_to_data_quality",
        "",
        "",
    ),
    DecisionRule(
        6,
        "переход из UNKNOWN в любой food-контекст",
        "unresolved_due_to_data_quality",
        "",
        "",
    ),
    DecisionRule(
        7,
        "end_reason=left_zone и carrying_type=tool, observation_state ∈ {OBS_OK, OBS_WEAK}",
        "sanitary_violation_confirmed",
        "violation",
        "critical",
    ),
    DecisionRule(
        8,
        "end_reason=left_zone и carrying_type=food, handoff_food=false по истечении окна",
        "sanitary_violation_confirmed",
        "violation",
        "critical",
    ),
    DecisionRule(
        9,
        "end_reason=left_zone и carrying_type=food, handoff_food=true",
        "process_event_confirmed",
        "info",
        "info",
    ),
    DecisionRule(
        10,
        "end_reason=left_zone и carrying_type=food, множественные кандидаты без разрешения",
        "unresolved_due_to_data_quality",
        "",
        "",
    ),
    DecisionRule(
        11,
        "end_reason=tracking_lost",
        "data_quality_event_confirmed",
        "",
        "",
    ),
    DecisionRule(
        12,
        "end_reason=key_object_lost без подтверждения переноса",
        "data_quality_event_confirmed",
        "",
        "",
    ),
    DecisionRule(
        13,
        "intrusion_work_zone, observation_state=OBS_OK, T_intrusion_confirm_sec пройден",
        "sanitary_violation_confirmed",
        "violation",
        "high",
    ),
    DecisionRule(
        14,
        "подтверждённое поведенческое нарушение из раздела 19.2 (кроме алкоголя и курения), observation_state=OBS_OK",
        "sanitary_violation_confirmed",
        "по таблице 7.2",
        "по таблице 7.2",
    ),
    DecisionRule(
        15,
        "подтверждённое поведенческое нарушение alcohol_consumption_in_work_zone",
        "sanitary_violation_confirmed c escalation_category=alcohol_escalation",
        "violation",
        "critical",
    ),
    DecisionRule(
        16,
        "подтверждённое поведенческое нарушение smoking_in_work_zone",
        "sanitary_violation_confirmed c escalation_category=smoking_escalation",
        "violation",
        "critical",
    ),
    DecisionRule(
        17,
        "подтверждённое no_cap / no_gloves / no_apron / face_touch / phone_usage, observation_state=OBS_OK",
        "sanitary_violation_confirmed",
        "по таблице 7.2",
        "по таблице 7.2",
    ),
    DecisionRule(
        18,
        "ACTION_INTERRUPTION подтверждён по правилам раздела 33.9",
        "process_event_confirmed",
        "info",
        "info",
    ),
    DecisionRule(
        19,
        "источник события с confidence < min_confidence_for_violation_source для санитарного нарушения",
        "no_event или data_quality_event_confirmed по решению контура",
        "",
        "",
    ),
    DecisionRule(
        20,
        "вторник",
        "no_event",
        "",
        "",
    ),
]


class DecisionLogic:
    """
    Таблица итоговых решений.
    Предоставляет методы для поиска правила по условиям.
    """

    def __init__(self):
        self._rules = DECISION_TABLE
        self._decision_modes = DECISION_MODES
        self._severity_internal = SEVERITY_INTERNAL
        self._severity_external = SEVERITY_EXTERNAL
        self._severity_mapping = SEVERITY_MAPPING

    def lookup_rule(self, rule_id: int) -> Optional[DecisionRule]:
        for rule in self._rules:
            if rule.rule_id == rule_id:
                return rule
        return None

    def lookup_by_conditions(
        self,
        event_type: str,
        sanitary_event_type: Optional[str] = None,
        process_event_type: Optional[str] = None,
        data_quality_reason: Optional[str] = None,
        observation_state: Optional[str] = None,
        carrying_type: Optional[str] = None,
        handoff_food: Optional[bool] = None,
        target_context: Optional[str] = None,
        source_context: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> Optional[DecisionRule]:

        # Правило 1-3: incompatible_transition_detected
        if event_type == "sanitary_event" and sanitary_event_type == "incompatible_transition_detected":
            if target_context in ("ready_to_eat", "packaging_dispatch"):
                if observation_state == "obs_ok":
                    return self.lookup_rule(1)
                elif observation_state == "obs_weak":
                    return self.lookup_rule(4)
                elif observation_state in ("obs_degraded", "obs_lost"):
                    return self.lookup_rule(5)
            elif target_context in ("clean_prep", "clean_zone"):
                if observation_state == "obs_ok":
                    return self.lookup_rule(2) if target_context == "clean_prep" else self.lookup_rule(3)
                elif observation_state == "obs_weak":
                    return self.lookup_rule(4)
                elif observation_state in ("obs_degraded", "obs_lost"):
                    return self.lookup_rule(5)
            if source_context == "unknown":
                return self.lookup_rule(6)

        # Правило 7: left_zone_with_tool_candidate (в payload будет sanitary_event_type)
        if event_type == "sanitary_event" and sanitary_event_type == "left_zone_with_tool_candidate":
            if observation_state in ("obs_ok", "obs_weak"):
                return self.lookup_rule(7)

        # Правило 8-10: left_zone_with_food
        if event_type == "sanitary_event" and sanitary_event_type == "left_zone_with_food_candidate":
            if handoff_food is False:
                return self.lookup_rule(8)
            elif handoff_food:
                return self.lookup_rule(9)

        # Правило 11: tracking_lost
        if event_type == "data_quality_event" and data_quality_reason == "tracking_lost":
            return self.lookup_rule(11)

        # Правило 12: key_object_lost
        if event_type == "data_quality_event" and data_quality_reason == "key_object_lost":
            return self.lookup_rule(12)

        # Правило 13: intrusion_work_zone
        if event_type == "sanitary_event" and sanitary_event_type == "intrusion_work_zone":
            if observation_state == "obs_ok":
                return self.lookup_rule(13)

        # Правило 14-17: поведенческие
        behavioral_types = [
            "food_consumption_in_work_zone",
            "food_consumption_after_handling",
            "drinking_in_work_zone",
            "mouth_contact_with_hands_or_objects_in_work_zone",
            "no_cap",
            "no_gloves",
            "no_apron",
            "face_touch",
            "phone_usage",
        ]
        if event_type == "sanitary_event" and sanitary_event_type in behavioral_types:
            if observation_state == "obs_ok":
                # Для алкоголя и курения отдельные правила
                if sanitary_event_type == "alcohol_consumption_in_work_zone":
                    return self.lookup_rule(15)
                elif sanitary_event_type == "smoking_in_work_zone":
                    return self.lookup_rule(16)
                else:
                    return self.lookup_rule(14)  # или 17 для uniform_behavior_rules, но упростим

        # Правило 18: action_interruption
        if event_type == "process_event" and process_event_type == "action_interruption":
            return self.lookup_rule(18)

        # Правило 19: низкая уверенность
        if confidence is not None and confidence < 0.7:  # min_confidence_for_violation_source
            return self.lookup_rule(19)

        # Правило 20: fallback
        return self.lookup_rule(20)
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import *
import uuid

# SANITARY-СОБЫТИЯ
SANITARY_EVENT_TYPES = [
    'incompatible_transition_detected',
    'left_zone_with_tool',
    'right_zone_with_tool',
    'intrusion_work_zone',
    'no_cap',
    'no_gloves',
    'no_apron',
    'face_touch',
    'phone_usage',
    'food_consumption_in_work_zone',
    'food_consumption_after_handling',
    'drinking_in_work_zone',
    'alcohol_consumption_in_work_zone',
    'smoking_in_work_zone',
    'mouth_contact_with_hands_or_objects_in_work_zone'
]

#ВНУТРЕННИИ УРОВНИ SEVERITY
SEVERITY_INNER_LEVELS = [
    'critical',
    'high',
    'medium',
    'low',
    'info',
    'warning',
    'violation'
]

#УРОВНИ SEVERITY ДЛЯ DBI
SEVERITY_LEVELS = [
    'info',
    'warning',
    'violation'
]

#УРОВНИ ЭСКАЛАЦИИ
ESCALATION_LEVELS = [
    'alcohol_escalation',
    'smoking_escalation'
]

#НЕСОВМЕСТИМЫЕ ПЕРЕХОДЫ
IMCOMPATIBILITY_CAUSES = [
    'missing_hands_washed',
    'missing_gloves_change',
    'missing_surface_sanitized',
    'missing_tool_reset',
    'missing_container_reset',
    'forbidden_zone_transition',
    'contaminated_source_contact',
    'unknown_source_state',
    'behavioral_block_active'
]

#ИТОГОВЫЕ РЕШЕНИЯ
FINAL_DECISIONS = [
    'SANITARY_VIOLATION_CONFIRMED',
    'PROCESS_EVENT_CONFIRMED',
    'UNRESOLVED_DUE_TO_DATA_QUALITY',
    'DATA_QUALITY_EVENT_CONFIRMED',
    'NO_EVENT'
]

#OBSERVATION_STATES
OBSERVATION_STATES = [
    'OBS_OK',
    'OBS_WEAK',
    'OBS_DEGRADED',
    'OBS_LOST'
]

#PROCESS_EVENT_TYPES
PROCESS_EVENT_TYPES = [
    'food_handoff',
    'ACTION_INTERRUPTION',
    'ZONE_RISK_SCORE'
]

#END_REASONS
END_REASONS = [
    'tracking_lost',
    'left_zone',
    'key_object_lost',
    'motion_stopped'
]

#CARRYING_TYPES
CARRYING_TYPES = [
    'none',
    'food',
    'tool'
]

# SEVERITY
SEVERITY_VIOLATION = [
    'violation', 'high', 'critical'
]
SEVERITY_WARNING = [
    'warning', 'low', 'medium'
]
SEVERITY_INFO = [
    'info'
]

class ActionEventLn(BaseModel):
    event_type: str
    action_id: str
    employee_id: str
    action_type: str
    object_class: str
    zone_id: str
    duration: float = Field(ge=0.0)
    end_reason: str
    employee_confidence: float = Field(ge=0.0, le=1.0)
    carrying_type: str
    carrying_confidence: float = Field(ge=0.0, le=1.0)

    @field_validator('end_reason', mode='before')
    @classmethod
    def validate_sanitary_end_reason(cls, v):
        if v in END_REASONS:
            return v
        else:
            raise ValueError("end_reason must be in SANITARY_EVENTS: {}".format(END_REASONS))

    @field_validator('carrying_type', mode='before')
    @classmethod
    def validate_sanitary_carrying_type(cls, v):
        if v in CARRYING_TYPES:
            return v
        else:
            raise ValueError("carrying_type must be in SANITARY_EVENTS: {}".format(CARRYING_TYPES))


class HygieneCheckLn(BaseModel):
    event_type: str
    hygiene_check_id: str
    action_id: str
    restaurant_id: int = Field(ge=0)
    employee_id: int = Field(ge=0)
    camera_id: str
    zone_id: str
    timestamp: str
    hands: str
    surface: str
    decision: str
    latency_ms: float = Field(ge=0.0)
    raw_payload: dict


class EnvironmentStateLn(BaseModel):
    event_type: str
    environment_state_id: str
    restaurant_id: int = Field(ge=0)
    camera_id: str
    zone_id: str
    timestamp: str
    surface_dirty_score: float = Field(ge=0.0, le=1.0)
    contact_zone_dirty_score: float = Field(ge=0.0, le=1.0)
    trash_score: float = Field(ge=0.0, le=1.0)
    contamination_score: float = Field(ge=0.0, le=1.0)
    open_container_detected: int = Field(ge=0)
    status: str
    raw_payload: dict


class SanitaryEventLn(BaseModel):
    event_type: str
    sanitary_event_id: str
    sanitary_event_type: str
    source_context: str = Field(default='')
    target_context: str = Field(default='')
    severity: str
    severity_iternal: str = Field(exclude=True)
    escalation_category: str = Field(default='')
    incompatibility_cause: str = Field(default='')
    rule_id: str = Field(default='')
    zone_id: str
    employee_id: str = Field(default='')
    decision_mode: str

    @field_validator('sanitary_event_type', mode='before')
    @classmethod
    def validate_sanitary_event_type(cls, v):
        if v in SANITARY_EVENT_TYPES:
            return v
        else:
            raise ValueError("sanitary_event_type must be in SANITARY_EVENTS: {}".format(SANITARY_EVENT_TYPES))

    @field_validator('severity', mode='before')
    @classmethod
    def validate_sanitary_severity(cls, v):
        if v in SEVERITY_LEVELS:
            return v
        else:
            raise ValueError("severity must be in SEVERITY_LEVELS: {}".format(SEVERITY_LEVELS))

    @field_validator('escalation_category', mode='before')
    @classmethod
    def validate_sanitary_escalation_category(cls, v):
        if v in ESCALATION_LEVELS:
            return v
        else:
            raise ValueError("escalation_category must be in ESCALATION_LEVELS: {}".format(ESCALATION_LEVELS))

    @field_validator('incompatibility_cause', mode='before')
    @classmethod
    def validate_sanitary_incompatibility_cause(cls, v):
        if v in IMCOMPATIBILITY_CAUSES:
            return v
        else:
            raise ValueError("incompatibility_cause must be in IMCOMPATIBILITY_CAUSES: {}".format(IMCOMPATIBILITY_CAUSES))

    @field_validator('decision_mode', mode='before')
    @classmethod
    def validate_sanitary_decision_mode(cls, v):
        if v in FINAL_DECISIONS:
            return v
        else:
            raise ValueError(
                "decision_mode must be in FINAL_DECISIONS: {}".format(FINAL_DECISIONS))

    @model_validator(mode='after')
    def map_severity_iternal(self):
        if self.severity_iternal in SEVERITY_VIOLATION:
            self.severity = 'violation'
        elif self.severity_iternal in SEVERITY_VIOLATION:
            self.severity = 'warning'
        elif self.severity_iternal in SEVERITY_INFO:
            self.severity = 'info'
        else:
            raise ValueError("Severity_iternal must be in SEVERITY_TYPES: {}".format(SEVERITY_VIOLATION),
                             "{}".format(SEVERITY_WARNING), "{}".format(SEVERITY_INFO))
        return self


class DataQualityEventLn(BaseModel):
    event_type: str
    data_quality_event_id: str
    reason: str
    action_id: str = Field(default='')
    employee_id: str = Field(default='')
    observation_state: str
    suppressed_events: List[str] = Field(default=[])

    @field_validator('observation_state', mode='before')
    @classmethod
    def validate_observation_state_type(cls, v):
        if v in OBSERVATION_STATES:
            return v
        else:
            raise ValueError("observation_state  must be in OBSERVATION_STATES: {}".format(OBSERVATION_STATES))


class ProcessEventLn(BaseModel):
    event_type: str
    process_event_id: str
    process_event_type: str
    employee_id: str = Field(default='')
    zone_id: str = Field(default='')
    details: object = Field(default={})

    @field_validator('process_event_type', mode='before')
    @classmethod
    def validate_process_event_type(cls, v):
        if v in PROCESS_EVENT_TYPES:
            return v
        else:
            raise ValueError("process_event must be in PROCESS_EVENT_TYPES: {}".format(PROCESS_EVENT_TYPES))


class AuditClipLn(BaseModel):
    event_type: str
    audit_clip_id: str
    sanitary_event_id: str = Field(default='')
    process_event_id: str = Field(default='')
    data_quality_event_id: str = Field(default='')

    @field_validator('process_event_id', mode='before')
    @classmethod
    def validate_process_event_id(cls, v):
        if isinstance(v, str) and v == '':
            return v
        if isinstance(v, str) and v != '':
            try:
                v = uuid.UUID(v)
                return str(v)
            except ValueError:
                raise ValueError("Cannot convert process_event_id to UUID4")
        else:
            raise ValueError("Process_event_id must be UUID4")

    @field_validator('data_quality_event_id', mode='before')
    @classmethod
    def validate_data_quality_event_id(cls, v):
        if isinstance(v, str) and v == '':
            return v
        if isinstance(v, str) and v != '':
            try:
                v = uuid.UUID(v)
                return str(v)
            except ValueError:
                raise ValueError("Cannot convert data_quality_event_id to UUID4")
        else:
            raise ValueError("Data_quality_event_id must be UUID4")


# TESTS
if __name__ == '__main__':
    sanitary_data = {'event_type': 'SANITARY_EVENT', 'sanitary_event_id': str(uuid.uuid4()), 'sanitary_event': 'no_cap',
                     'severity': 'violation', 'severity_iternal': 'violation', 'escalation_category': '',}
    try:
        sanitary = SanitaryEventLn(**sanitary_data)
        print(sanitary.model_dump())
    except ValueError as e:
        print(e)
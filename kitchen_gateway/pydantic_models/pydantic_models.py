from pydantic import BaseModel, Field, field_validator, model_validator
import uuid

# SANITARY-СОБЫТИЯ
UNIFORM_BEHAVIOUR_RULES = [
    'no_cap', 'no_gloves', 'no_apron',
    'face_touch', 'phone_usage'
]
BEHAVIOUR_SANITARY_RULES = [
    'food_consumption_in_work_zone', 'food_consumption_after_handling', 'drinking_in_work_zone',
    'alcohol_consumption_in_work_zone', 'smoking_in_work_zone', 'mouth_contact_with_hands_or_objects_in_work_zone'
]
ZONE_EXIT_CONTROL = [
    'left_zone_with_tool', 'left_zone_with_food'
]
INTRUSION_CONTROL = [
    'intrusion_work_zone'
]

# PROCESS-СОБЫТИЯ
PROCESS_EVENTS = [
    'food_handoff', 'ACTION_INTERRUPTION', 'ZONE_RISK_SCORE'
]

# DATA_QUALITY-СОБЫТИЯ
DATA_QUALITY_EVENTS = [
    'tracking_lost', 'key_object_lost', 'OBS_LOST',
    'OBS_DEGRADED', 'ambiguous_handoff_target', 'UNRESOLVED_DUE_TO_DATA_QUALITY'
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
    employee_id: int = Field(ge=0)
    camera_id: str
    zone_id: str
    action_type: str
    object_class: str
    timestamp_start: str
    timestamp_end: str
    duration: float = Field(ge=0.0)
    end_reason: str
    employee_confidence: float = Field(ge=0.0, le=1.0)
    carrying_type: str
    carrying_confidence: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator('action_id', mode='before')
    @classmethod
    def validate_action_id(cls, v):
        if isinstance(v, str):
            try:
                v = uuid.UUID(v)
                return str(v)
            except ValueError:
                raise ValueError("Cannot convert action_id to UUID4")
        else:
            raise ValueError("Action_id must be UUID4")

    @field_validator('carrying_type', mode='before')
    @classmethod
    def validate_carrying_type(cls, v):
        if isinstance(v, str):
            return v
        if v != 'food' and v != 'tool' and v != 'none':
            raise ValueError("Carrying type must be 'food' or 'tool' or 'none'")
        else:
            raise ValueError("Carrying type must be str")


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

    @field_validator('hygiene_check_id', mode='before')
    @classmethod
    def validate_hygiene_check_id(cls, v):
        if isinstance(v, str):
            try:
                v = uuid.UUID(v)
                return str(v)
            except ValueError:
                raise ValueError("Cannot convert hygiene_check_id to UUID4")
        else:
            raise ValueError("Hygiene_check_id must be UUID4")

    @field_validator('action_id', mode='before')
    @classmethod
    def validate_action_id(cls, v):
        if isinstance(v, str):
            try:
                v = uuid.UUID(v)
                return str(v)
            except ValueError:
                raise ValueError("Cannot convert action_id to UUID4")
        else:
            raise ValueError("Action_id must be UUID4")


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

    @field_validator('environment_state_id', mode='before')
    @classmethod
    def validate_hygiene_check_id(cls, v):
        if isinstance(v, str):
            try:
                v = uuid.UUID(v)
                return str(v)
            except ValueError:
                raise ValueError("Cannot convert environment_state_id to UUID4")
        else:
            raise ValueError("Environment_state_id must be UUID4")


class SanitaryEventLn(BaseModel):
    event_type: str
    sanitary_event_id: str
    sanitary_event: str
    severity: str
    severity_iternal: str = Field(exclude=True)
    escalation_category: str = Field(default='')

    @field_validator('sanitary_event_id', mode='before')
    @classmethod
    def validate_sanitary_event_id(cls, v):
        if isinstance(v, str):
            try:
                v = uuid.UUID(v)
                return str(v)
            except ValueError:
                raise ValueError("Cannot convert sanitary_event_id to UUID4")
        else:
            raise ValueError("Sanitary_event_id must be UUID4")

    @field_validator('sanitary_event', mode='before')
    @classmethod
    def validate_sanitary_event_type(cls, v):
        if v in UNIFORM_BEHAVIOUR_RULES or v in BEHAVIOUR_SANITARY_RULES or v in ZONE_EXIT_CONTROL\
                or v in INTRUSION_CONTROL or v in PROCESS_EVENTS or v in DATA_QUALITY_EVENTS:
            return v
        else:
            raise ValueError("Sanitary_event must be in SANITARY_EVENTS: {}".format(UNIFORM_BEHAVIOUR_RULES),
                        "{}".format(BEHAVIOUR_SANITARY_RULES), "{}".format(ZONE_EXIT_CONTROL),
                        "{}".format(INTRUSION_CONTROL), "{}".format(PROCESS_EVENTS),
                        "{}".format(DATA_QUALITY_EVENTS))

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
    observation_state: str

    @field_validator('data_quality_event_id', mode='before')
    @classmethod
    def validate_data_quality_event_id(cls, v):
        if isinstance(v, str):
            try:
                v = uuid.UUID(v)
                return str(v)
            except ValueError:
                raise ValueError("Cannot convert data_quality_event_id to UUID4")
        else:
            raise ValueError("Data_quality_event_id must be UUID4")

    @field_validator('observation_state', mode='before')
    @classmethod
    def validate_observation_state_type(cls, v):
        if v in DATA_QUALITY_EVENTS:
            return v
        else:
            raise ValueError("Observation_state  must be in DATA_QUALITY_EVENTS: {}".format(DATA_QUALITY_EVENTS))


class ProcessEventLn(BaseModel):
    event_type: str
    process_event_id: str
    process_event: str

    @field_validator('process_event_id', mode='before')
    @classmethod
    def validate_process_event_id(cls, v):
        if isinstance(v, str):
            try:
                v = uuid.UUID(v)
                return str(v)
            except ValueError:
                raise ValueError("Cannot convert process_event_id to UUID4")
        else:
            raise ValueError("Process_event_id must be UUID4")

    @field_validator('process_event', mode='before')
    @classmethod
    def validate_process_event_type(cls, v):
        if v in PROCESS_EVENTS:
            return v
        else:
            raise ValueError("Process_event must be in PROCESS_EVENTS: {}".format(PROCESS_EVENTS))


class AuditClipLn(BaseModel):
    event_type: str
    audit_clip_id: str
    sanitary_event_id: str = Field(default='')
    process_event_id: str = Field(default='')
    data_quality_event_id: str = Field(default='')

    @field_validator('audit_clip_id', mode='before')
    @classmethod
    def validate_audit_clip_id(cls, v):
        if isinstance(v, str):
            try:
                v = uuid.UUID(v)
                return str(v)
            except ValueError:
                raise ValueError("Cannot convert audit_clip_id to UUID4")
        else:
            raise ValueError("Audit_clip_id must be UUID4")

    @field_validator('sanitary_event_id', mode='before')
    @classmethod
    def validate_sanitary_event_id(cls, v):
        if isinstance(v, str) and v == '':
            return v
        if isinstance(v, str) and v != '':
            try:
                v = uuid.UUID(v)
                return str(v)
            except ValueError:
                raise ValueError("Cannot convert sanitary_event_id to UUID4")
        else:
            raise ValueError("Sanitary_event_id must be UUID4")

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
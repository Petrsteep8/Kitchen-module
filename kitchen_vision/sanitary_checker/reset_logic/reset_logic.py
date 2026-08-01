import enum

try:
    from kitchen_vision.sanitary_checker.fsm.fsm_hub import FSMHub
except ImportError as e:
    pass


class ResetTypes(enum.Enum):
    hands_washed = "hands_washed"
    gloves_changed = "gloves_changed"
    gloves_removed = "gloves_removed"
    gloves_put_on = "gloves_put_on"
    tool_washed = "tool_washed"
    clean_tool_taken = "clean_tool_taken"
    tool_replaced = "tool_replaced"
    container_replaced_clean = "container_replaced_clean"
    disposable_container_used = "disposable_container_used"
    surface_sanitized = "surface_sanitized"
    entered_reset_zone = "entered_reset_zone"
    passed_clean_gate = "passed_clean_gate"
    shift_started = "shift_started"
    returned_to_kitchen = "returned_to_kitchen"


class SanitaryStates(enum.Enum):
    clean_confirmed = "clean_confirmed"
    clean_degrading = "clean_degrading"
    sanitized = "sanitized"
    unknown = "unknown"
    contaminated = "contaminated"


sanitized_resulting_state = ['hands_washed', 'gloves_changed', 'gloves_put_on',
                             'tool_washed', 'clean_tool_taken', 'tool_replaced',
                             'container_replaced_clean', 'disposable_container_used',
                             'surface_sanitized', 'entered_reset_zone', 'passed_clean_gate']
clean_degrading_resulting_state = ['gloves_removed', 'returned_to_kitchen']
full_resulting_state = ['shift_started']

full_ttl_reset = ['hands_washed', 'gloves_changed', 'gloves_put_on',
                  'tool_washed', 'clean_tool_taken', 'tool_replaced',
                  'container_replaced_clean', 'disposable_container_used',
                  'surface_sanitized']
partial_ttl_reset = ['gloves_removed']
no_ttl_reset = ['entered_reset_zone', 'passed_clean_gate', 'returned_to_kitchen']


class ResetLogic:
    def __init__(self, fsm_hub: FSMHub):
        self._reset_types = ResetTypes
        self._sanitary_states = SanitaryStates
        self._fsm_hub = fsm_hub

    def apply_ttl_reset(self, reset_entity: dict, track_id: str, fsm):
        pass

    def apply_reset(self, reset_entity: dict, track_id: str, fsm):
        if reset_entity[track_id] in sanitized_resulting_state:
            fsm.set_sanitary_state(self._sanitary_states.sanitized)
        elif reset_entity[track_id] in clean_degrading_resulting_state:
            fsm.set_sanitary_state(self._sanitary_states.clean_degrading)
        elif reset_entity[track_id] in full_resulting_state:
            fsm.set_sanitary_state(self._sanitary_states.clean_confirmed)

    def process_reset_event(self, pkt):
        reset_type = pkt["reset_type"]
        reset_entities = pkt["target_entities"]
        track_id = pkt["entity_id"]

        if reset_type == self._reset_types.hands_washed:
            for reset_entity in reset_entities:
                if reset_entity[track_id] == "hands":  # TODO dict structure
                    fsm = self._fsm_hub.hands_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
        elif reset_type == self._reset_types.gloves_changed:
            for reset_entity in reset_entities:
                if reset_entity[track_id] == "hands":  # TODO dict structure
                    fsm = self._fsm_hub.hands_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
        elif reset_type == self._reset_types.gloves_removed:
            for reset_entity in reset_entities:
                if reset_entity[track_id] == "hands":  # TODO dict structure
                    fsm = self._fsm_hub.hands_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
        elif reset_type == self._reset_types.gloves_put_on:
            for reset_entity in reset_entities:
                if reset_entity[track_id] == "hands":  # TODO dict structure
                    fsm = self._fsm_hub.hands_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
        elif reset_type == self._reset_types.tool_washed:
            for reset_entity in reset_entities:
                if reset_entity[track_id] == "tool":  # TODO dict structure
                    fsm = self._fsm_hub.tool_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
        elif reset_type == self._reset_types.clean_tool_taken:
            for reset_entity in reset_entities:
                if reset_entity[track_id] == "tool":  # TODO dict structure
                    fsm = self._fsm_hub.tool_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
        elif reset_type == self._reset_types.tool_replaced:
            for reset_entity in reset_entities:
                if reset_entity[track_id] == "tool":  # TODO dict structure
                    fsm = self._fsm_hub.tool_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
        elif reset_type == self._reset_types.container_replaced_clean:
            for reset_entity in reset_entities:
                if reset_entity[track_id] == "container":  # TODO dict structure
                    fsm = self._fsm_hub.container_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
        elif reset_type == self._reset_types.disposable_container_used:
            for reset_entity in reset_entities:
                if reset_entity[track_id] == "container":  # TODO dict structure
                    fsm = self._fsm_hub.container_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
        elif reset_type == self._reset_types.surface_sanitized:
            for reset_entity in reset_entities:
                if reset_entity[track_id] == "surface":  # TODO dict structure
                    fsm = self._fsm_hub.surface_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
        elif reset_type == self._reset_types.entered_reset_zone:
            for reset_entity in reset_entities:
                if reset_entity[track_id] == "hands":  # TODO dict structure
                    fsm = self._fsm_hub.hands_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                elif reset_entity[track_id] == "employee":  # TODO dict structure
                    fsm = self._fsm_hub.employee_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
        elif reset_type == self._reset_types.passed_clean_gate:
            for reset_entity in reset_entities:
                if reset_entity[track_id] == "employee":  # TODO dict structure
                    fsm = self._fsm_hub.employee_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
        elif reset_type == self._reset_types.shift_started:
            for reset_entity in reset_entities:
                if reset_entity[track_id] == "employee":  # TODO dict structure
                    fsm = self._fsm_hub.employee_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                elif reset_entity[track_id] == "hands":
                    fsm = self._fsm_hub.hands_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                elif reset_entity[track_id] == "tool":
                    fsm = self._fsm_hub.tool_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                elif reset_entity[track_id] == "surface":
                    fsm = self._fsm_hub.surface_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                elif reset_entity[track_id] == "container":
                    fsm = self._fsm_hub.container_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)

        elif reset_type == self._reset_types.returned_to_kitchen:
            for reset_entity in reset_entities:
                if reset_entity[track_id] == "employee":  # TODO dict structure
                    fsm = self._fsm_hub.employee_context()
                    self.apply_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
                    self.apply_ttl_reset(reset_entity=reset_entity, track_id=track_id, fsm=fsm)
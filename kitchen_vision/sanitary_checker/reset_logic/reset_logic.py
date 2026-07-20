import enum

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


class ResetLogic:
    def __init__(self):
        self._reset_types = ResetTypes

    def apply_ttl_reset(self, reset_entity: dict):
        pass

    def apply_reset(self, reset_entity: dict):
        pass

    def process_reset_event(self, pkt):
        reset_type = pkt["reset_type"]
        reset_entities = pkt["target_entities"]

        if reset_type == self._reset_types.hands_washed:
            for reset_entity in reset_entities:
                if reset_entity["track_id"] == "hands": #TODO dict structure
                    pass
        elif reset_type == self._reset_types.gloves_changed:
            for reset_entity in reset_entities:
                if reset_entity["track_id"] == "hands": #TODO dict structure
                    pass
        elif reset_type == self._reset_types.gloves_removed:
            for reset_entity in reset_entities:
                if reset_entity["track_id"] == "hands": #TODO dict structure
                    pass
        elif reset_type == self._reset_types.gloves_put_on:
            for reset_entity in reset_entities:
                if reset_entity["track_id"] == "hands": #TODO dict structure
                    pass
        elif reset_type == self._reset_types.tool_washed:
            for reset_entity in reset_entities:
                if reset_entity["track_id"] == "tool": #TODO dict structure
                    pass
        elif reset_type == self._reset_types.clean_tool_taken:
            for reset_entity in reset_entities:
                if reset_entity["track_id"] == "tool": #TODO dict structure
                    pass
        elif reset_type == self._reset_types.tool_replaced:
            for reset_entity in reset_entities:
                if reset_entity["track_id"] == "tool": #TODO dict structure
                    pass
        elif reset_type == self._reset_types.container_replaced_clean:
            for reset_entity in reset_entities:
                if reset_entity["track_id"] == "container": #TODO dict structure
                    pass
        elif reset_type == self._reset_types.disposable_container_used:
            for reset_entity in reset_entities:
                if reset_entity["track_id"] == "container": #TODO dict structure
                    pass
        elif reset_type == self._reset_types.surface_sanitized:
            for reset_entity in reset_entities:
                if reset_entity["track_id"] == "surface": #TODO dict structure
                    pass
        elif reset_type == self._reset_types.entered_reset_zone:
            for reset_entity in reset_entities:
                if reset_entity["track_id"] == "hands" or reset_entity["track_id"] == "employee": #TODO dict structure
                    pass
        elif reset_type == self._reset_types.passed_clean_gate:
            for reset_entity in reset_entities:
                if reset_entity["track_id"] == "employee": #TODO dict structure
                    pass
        elif reset_type == self._reset_types.passed_clean_gate:
            for reset_entity in reset_entities:
                if reset_entity["track_id"] == "employee": #TODO dict structure
                    pass
        elif reset_type == self._reset_types.shift_started:
            for reset_entity in reset_entities:
                if reset_entity["track_id"] == "employee" or reset_entity["track_id"] == "hands"\
                        or reset_entity["track_id"] == "tool" or reset_entity["track_id"] == "surface"\
                        or reset_entity["track_id"] == "container": #TODO dict structure
                    pass
        elif reset_type == self._reset_types.returned_to_kitchen:
            for reset_entity in reset_entities:
                if reset_entity["track_id"] == "employee": #TODO dict structure
                    pass

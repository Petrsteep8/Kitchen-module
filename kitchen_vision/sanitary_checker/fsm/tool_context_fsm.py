import time
import enum
from typing import Optional, Dict, Any


class ToolEvents(enum.Enum):
    INTERACTION = "INTERACTION"
    CONTAMINATION_EVENT = "CONTAMINATION_EVENT"
    SANITIZATION_EVENT = "SANITIZATION_EVENT"
    OBS_EVENT = "OBS_EVENT"
    TTL_EXPIRED = "TTL_EXPIRED"


class ToolContexts(enum.Enum):
    UNKNOWN = "UNKNOWN"
    NEUTRAL = "NEUTRAL"
    RAW_ANIMAL = "RAW_ANIMAL"
    RTE_FOOD = "RTE_FOOD"
    CONTAMINATED = "CONTAMINATED"
    SANITIZED = "SANITIZED"
    IN_USE = "IN_USE"
    OBS_LOST = "OBS_LOST"
    RAW_FOOD = "RAW_FOOD"
    HANDS = "HANDS"
    FOOD = "FOOD"


class ToolSanitaryStates(enum.Enum):
    CLEAN_CONFIRMED = "CLEAN_CONFIRMED"
    CLEAN_DEGRADING = "CLEAN_DEGRADING"
    UNKNOWN = "UNKNOWN"
    CONTAMINATED = "CONTAMINATED"


class ToolContextFSM:
    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._event_types = ToolEvents
        self._context = ToolContexts
        self._sanitary_states = ToolSanitaryStates

    def _get_current_context(self, track_id: str) -> ToolContexts:
        data = self._data.get(track_id)
        if not data:
            return self._context.UNKNOWN
        return data.get("context", self._context.UNKNOWN)

    def _get_current_state(self, track_id: str) -> ToolSanitaryStates:
        data = self._data.get(track_id)
        if not data:
            return self._sanitary_states.UNKNOWN
        return data.get("sanitary_state", self._sanitary_states.UNKNOWN)

    def set_context(self, track_id: str, new_context: ToolContexts) -> None:
        if track_id in self._data:
            self._data[track_id]["context"] = new_context

    def set_sanitary_state(self, track_id: str, new_state: ToolSanitaryStates) -> None:
        if track_id in self._data:
            self._data[track_id]["sanitary_state"] = new_state

    def process_event(
            self,
            track_id: str,
            event: ToolEvents,
            event_type: Optional[ToolContexts] = None,
    ) -> None:
        current_context = self._get_current_context(track_id)

        if (event == ToolEvents.INTERACTION and
                event_type == self._context.FOOD and
                current_context in (self._context.UNKNOWN, self._context.NEUTRAL)):
            self.set_context(track_id, self._context.IN_USE)
            return

        if (event == ToolEvents.CONTAMINATION_EVENT and
                event_type == self._context.RAW_FOOD and
                current_context in (self._context.UNKNOWN, self._context.NEUTRAL, self._context.IN_USE)):
            self.set_context(track_id, self._context.RAW_ANIMAL)
            return

        if (event == ToolEvents.SANITIZATION_EVENT and
                current_context in (self._context.RAW_ANIMAL, self._context.CONTAMINATED, self._context.IN_USE)):
            self.set_context(track_id, self._context.SANITIZED)
            self.set_sanitary_state(track_id, self._sanitary_states.CLEAN_CONFIRMED)
            return

        if (event == ToolEvents.INTERACTION and
                event_type == self._context.HANDS and
                current_context == self._context.SANITIZED):
            self.set_context(track_id, self._context.IN_USE)
            self.set_sanitary_state(track_id, self._sanitary_states.CLEAN_DEGRADING)
            return

        if event == ToolEvents.TTL_EXPIRED:
            self.set_context(track_id, self._context.UNKNOWN)
            return

        if (event == ToolEvents.OBS_EVENT and
                event_type == self._context.OBS_LOST):
            self.set_context(track_id, self._context.UNKNOWN)
            return

    def add_data_by_track_id(self, track_id: str, data: Dict[str, Any]) -> None:
        if "context" not in data:
            data["context"] = self._context.UNKNOWN
        if "sanitary_state" not in data:
            data["sanitary_state"] = self._sanitary_states.UNKNOWN
        if "ttl_remaining_time" not in data:
            data["ttl_remaining_time"] = 0
        if "last_event_timestamp" not in data:
            data["last_event_timestamp"] = time.time()
        if "entity_id" not in data:
            data["entity_id"] = track_id
        if "entity_type" not in data:
            data["entity_type"] = "tool"

        self._data[track_id] = data

    def get_data_by_track_id(self, track_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get(track_id)

    def get_context(self, track_id: str) -> ToolContexts:
        return self._get_current_context(track_id)

    def get_sanitary_state(self, track_id: str) -> ToolSanitaryStates:
        return self._get_current_state(track_id)

    def get_ttl_remaining(self, track_id: str) -> float:
        data = self._data.get(track_id)
        if not data:
            return 0.0
        return data.get("ttl_remaining_time", 0.0)


if __name__ == "__main__":
    tool = ToolContextFSM()

    payload = {
        "tool_id": "knife_1",
        "entity_type": "tool",
        "sanitary_state": ToolSanitaryStates.UNKNOWN,
        "context": ToolContexts.NEUTRAL,
        "ttl_remaining_time": 120,
        "last_event_timestamp": time.time(),
    }

    tool.add_data_by_track_id(track_id="knife_1", data=payload)

    tool.process_event(
        "knife_1",
        ToolEvents.INTERACTION,
        event_type=ToolContexts.FOOD
    )
    print(tool.get_data_by_track_id('knife_1'))

    tool.process_event(
        "knife_1",
        ToolEvents.TTL_EXPIRED
    )
    print(tool.get_data_by_track_id('knife_1'))
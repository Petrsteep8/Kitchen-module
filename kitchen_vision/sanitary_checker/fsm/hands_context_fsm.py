import time
import enum
from typing import Optional, Dict, Any


class HandsEvents(enum.Enum):
    INTERACTION = "INTERACTION"
    CONTAMINATION_EVENT = "CONTAMINATION_EVENT"
    OBS_EVENT = "OBS_EVENT"
    TTL_EXPIRED = "TTL_EXPIRED"


class HandsContexts(enum.Enum):
    UNKNOWN = "UNKNOWN"
    NEUTRAL = "NEUTRAL"
    RAW_ANIMAL = "RAW_ANIMAL"
    NONFOOD_CONTACT = "NONFOOD_CONTACT"
    CONTAMINATED = "CONTAMINATED"
    SANITIZED = "SANITIZED"
    CLEAN_PREP = "CLEAN_PREP"
    OBS_LOST = "OBS_LOST"
    RAW_FOOD = "RAW_FOOD"
    TOOL = "TOOL"
    FOOD = "FOOD"


class HandsSanitaryStates(enum.Enum):
    CLEAN_CONFIRMED = "CLEAN_CONFIRMED"
    CLEAN_DEGRADING = "CLEAN_DEGRADING"
    SANITIZED = "SANITIZED"
    UNKNOWN = "UNKNOWN"
    CONTAMINATED = "CONTAMINATED"


class HandsContextFSM:
    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._event_types = HandsEvents
        self._context = HandsContexts
        self._sanitary_states = HandsSanitaryStates

    def _get_current_context(self, track_id: str) -> HandsContexts:
        data = self._data.get(track_id)
        if not data:
            return self._context.UNKNOWN
        return data.get("context", self._context.UNKNOWN)

    def _get_current_state(self, track_id: str) -> HandsSanitaryStates:
        data = self._data.get(track_id)
        if not data:
            return self._sanitary_states.UNKNOWN
        return data.get("sanitary_state", self._sanitary_states.UNKNOWN)

    def set_context(self, track_id: str, new_context: HandsContexts) -> None:
        if track_id in self._data:
            self._data[track_id]["context"] = new_context

    def set_sanitary_state(self, track_id: str, new_state: HandsSanitaryStates) -> None:
        if track_id in self._data:
            self._data[track_id]["sanitary_state"] = new_state

    def process_event(
            self,
            track_id: str,
            event: HandsEvents,
            event_type: Optional[HandsContexts] = None,
    ) -> None:
        current_context = self._get_current_context(track_id)

        if (event == HandsEvents.INTERACTION and
                event_type == self._context.TOOL and
                current_context in (self._context.UNKNOWN, self._context.NEUTRAL)):
            self.set_context(track_id, self._context.RAW_ANIMAL)
            return

        if (event == HandsEvents.CONTAMINATION_EVENT and
                event_type == self._context.NONFOOD_CONTACT and
                current_context in (self._context.UNKNOWN, self._context.NEUTRAL)):
            self.set_context(track_id, self._context.NONFOOD_CONTACT)
            return

        if (event == HandsEvents.CONTAMINATION_EVENT and
                event_type == self._context.RAW_FOOD and
                current_context in (self._context.UNKNOWN, self._context.NEUTRAL)):
            self.set_context(track_id, self._context.CONTAMINATED)
            return

        if (event == HandsEvents.INTERACTION and
                event_type == self._context.FOOD and
                current_context == self._context.SANITIZED):
            self.set_context(track_id, self._context.CLEAN_PREP)
            return

        if event == HandsEvents.TTL_EXPIRED:
            self.set_context(track_id, self._context.UNKNOWN)
            return

        if (event == HandsEvents.OBS_EVENT and
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
            data["entity_type"] = "hands"

        self._data[track_id] = data

    def get_data_by_track_id(self, track_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get(track_id)

    def get_context(self, track_id: str) -> HandsContexts:
        return self._get_current_context(track_id)

    def get_sanitary_state(self, track_id: str) -> HandsSanitaryStates:
        return self._get_current_state(track_id)

    def get_ttl_remaining(self, track_id: str) -> float:
        data = self._data.get(track_id)
        if not data:
            return 0.0
        return data.get("ttl_remaining_time", 0.0)


if __name__ == "__main__":
    hands = HandsContextFSM()

    payload = {
        "employee_id": 1,
        "entity_type": "hands",
        "sanitary_state": HandsSanitaryStates.UNKNOWN,
        "context": HandsContexts.NEUTRAL,
        "ttl_remaining_time": 60,
        "last_event_timestamp": time.time(),
    }

    hands.add_data_by_track_id(track_id=str(1), data=payload)

    hands.process_event(
        str(1),
        HandsEvents.INTERACTION,
        event_type=HandsContexts.TOOL
    )

    hands.process_event(
        str(1),
        HandsEvents.TTL_EXPIRED
    )
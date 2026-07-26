import time
import enum
from typing import Optional, Dict, Any


class HandsEvents(enum.Enum):
    interaction = "interaction"
    contamination_event = "contamination_event"
    obs_event = "obs_event"
    ttl_expired = "ttl_expired"


class HandsContexts(enum.Enum):
    unknown = "unknown"
    neutral = "neutral"
    raw_animal = "raw_animal"
    nonfood_contact = "nonfood_contact"
    contaminated = "contaminated"
    sanitized = "sanitized"
    clean_prep = "clean_prep"
    obs_lost = "obs_lost"
    raw_food = "raw_food"
    tool = "tool"
    food = "food"


class HandsSanitaryStates(enum.Enum):
    clean_confirmed = "clean_confirmed"
    clean_degrading = "clean_degrading"
    sanitized = "sanitized"
    unknown = "unknown"
    contaminated = "contaminated"


class HandsContextFSM:
    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._event_types = HandsEvents
        self._context = HandsContexts
        self._sanitary_states = HandsSanitaryStates

    def _get_current_context(self, track_id: str) -> HandsContexts:
        data = self._data.get(track_id)
        if not data:
            return self._context.unknown
        return data.get("context", self._context.unknown)

    def _get_current_state(self, track_id: str) -> HandsSanitaryStates:
        data = self._data.get(track_id)
        if not data:
            return self._sanitary_states.unknown
        return data.get("sanitary_state", self._sanitary_states.unknown)

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

        if (event == HandsEvents.interaction and
                event_type == self._context.tool and
                current_context in (self._context.unknown, self._context.neutral)):
            self.set_context(track_id, self._context.raw_animal)
            return

        if (event == HandsEvents.contamination_event and
                event_type == self._context.nonfood_contact and
                current_context in (self._context.unknown, self._context.neutral)):
            self.set_context(track_id, self._context.nonfood_contact)
            return

        if (event == HandsEvents.contamination_event and
                event_type == self._context.raw_food and
                current_context in (self._context.unknown, self._context.neutral)):
            self.set_context(track_id, self._context.contaminated)
            return

        if (event == HandsEvents.interaction and
                event_type == self._context.food and
                current_context == self._context.sanitized):
            self.set_context(track_id, self._context.clean_prep)
            return

        if event == HandsEvents.ttl_expired:
            self.set_context(track_id, self._context.unknown)
            return

        if (event == HandsEvents.obs_event and
                event_type == self._context.obs_lost):
            self.set_context(track_id, self._context.unknown)
            return

    def add_data_by_track_id(self, track_id: str, data: Dict[str, Any]) -> None:
        if "context" not in data:
            data["context"] = self._context.unknown
        if "sanitary_state" not in data:
            data["sanitary_state"] = self._sanitary_states.unknown
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
        "sanitary_state": HandsSanitaryStates.unknown,
        "context": HandsContexts.neutral,
        "ttl_remaining_time": 60,
        "last_event_timestamp": time.time(),
    }

    hands.add_data_by_track_id(track_id=str(1), data=payload)

    hands.process_event(
        str(1),
        HandsEvents.interaction,
        event_type=HandsContexts.tool
    )

    hands.process_event(
        str(1),
        HandsEvents.ttl_expired
    )
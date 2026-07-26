import time
import enum
from typing import Optional, Dict, Any


class ContainerEvents(enum.Enum):
    interaction = "interaction"
    contamination_event = "contamination_event"
    sanitization_event = "sanitization_event"
    obs_event = "obs_event"
    ttl_expired = "ttl_expired"


class ContainerContexts(enum.Enum):
    unknown = "unknown"
    neutral = "neutral"
    raw_animal = "raw_animal"
    rte_food = "rte_food"
    contaminated = "contaminated"
    sanitized = "sanitized"
    in_use = "in_use"
    obs_lost = "obs_lost"
    raw_food = "raw_food"
    hands = "hands"
    food = "food"


class ContainerSanitaryStates(enum.Enum):
    clean_confirmed = "clean_confirmed"
    clean_degrading = "clean_degrading"
    unknown = "unknown"
    contaminated = "contaminated"


class ContainerContextFSM:
    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._event_types = ContainerEvents
        self._context = ContainerContexts
        self._sanitary_states = ContainerSanitaryStates

    def _get_current_context(self, track_id: str) -> ContainerContexts:
        data = self._data.get(track_id)
        if not data:
            return self._context.unknown
        return data.get("context", self._context.unknown)

    def _get_current_state(self, track_id: str) -> ContainerSanitaryStates:
        data = self._data.get(track_id)
        if not data:
            return self._sanitary_states.unknown
        return data.get("sanitary_state", self._sanitary_states.unknown)

    def set_context(self, track_id: str, new_context: ContainerContexts) -> None:
        if track_id in self._data:
            self._data[track_id]["context"] = new_context

    def set_sanitary_state(self, track_id: str, new_state: ContainerSanitaryStates) -> None:
        if track_id in self._data:
            self._data[track_id]["sanitary_state"] = new_state

    def process_event(
            self,
            track_id: str,
            event: ContainerEvents,
            event_type: Optional[ContainerContexts] = None,
    ) -> None:
        current_context = self._get_current_context(track_id)

        if (event == ContainerEvents.interaction and
                event_type == self._context.food and
                current_context in (self._context.unknown, self._context.neutral)):
            self.set_context(track_id, self._context.in_use)
            return

        if (event == ContainerEvents.contamination_event and
                event_type == self._context.raw_food and
                current_context in (self._context.unknown, self._context.neutral, self._context.in_use)):
            self.set_context(track_id, self._context.raw_animal)
            self.set_sanitary_state(track_id, self._sanitary_states.contaminated)
            return

        if (event == ContainerEvents.sanitization_event and
                current_context in (self._context.raw_animal, self._context.contaminated, self._context.in_use)):
            self.set_context(track_id, self._context.sanitized)
            self.set_sanitary_state(track_id, self._sanitary_states.clean_confirmed)
            return

        if (event == ContainerEvents.interaction and
                event_type == self._context.hands and
                current_context == self._context.sanitized):
            self.set_context(track_id, self._context.in_use)
            self.set_sanitary_state(track_id, self._sanitary_states.clean_degrading)
            return

        if event == ContainerEvents.ttl_expired:
            self.set_context(track_id, self._context.unknown)
            return

        if (event == ContainerEvents.obs_event and
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
            data["entity_type"] = "container"

        self._data[track_id] = data

    def get_data_by_track_id(self, track_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get(track_id)

    def get_context(self, track_id: str) -> ContainerContexts:
        return self._get_current_context(track_id)

    def get_sanitary_state(self, track_id: str) -> ContainerSanitaryStates:
        return self._get_current_state(track_id)

    def get_ttl_remaining(self, track_id: str) -> float:
        data = self._data.get(track_id)
        if not data:
            return 0.0
        return data.get("ttl_remaining_time", 0.0)


if __name__ == "__main__":
    container = ContainerContextFSM()

    payload = {
        "container_id": "tray_1",
        "entity_type": "container",
        "sanitary_state": ContainerSanitaryStates.unknown,
        "context": ContainerContexts.neutral,
        "ttl_remaining_time": 300,
        "last_event_timestamp": time.time(),
    }

    container.add_data_by_track_id(track_id="tray_1", data=payload)

    container.process_event(
        "tray_1",
        ContainerEvents.interaction,
        event_type=ContainerContexts.food
    )

    container.process_event(
        "tray_1",
        ContainerEvents.ttl_expired
    )
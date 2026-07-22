import time
import enum
from typing import Optional, Dict, Any


class ContainerEvents(enum.Enum):
    INTERACTION = "INTERACTION"
    CONTAMINATION_EVENT = "CONTAMINATION_EVENT"
    SANITIZATION_EVENT = "SANITIZATION_EVENT"
    OBS_EVENT = "OBS_EVENT"
    TTL_EXPIRED = "TTL_EXPIRED"


class ContainerContexts(enum.Enum):
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


class ContainerSanitaryStates(enum.Enum):
    CLEAN_CONFIRMED = "CLEAN_CONFIRMED"
    CLEAN_DEGRADING = "CLEAN_DEGRADING"
    UNKNOWN = "UNKNOWN"
    CONTAMINATED = "CONTAMINATED"


class ContainerContextFSM:
    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._event_types = ContainerEvents
        self._context = ContainerContexts
        self._sanitary_states = ContainerSanitaryStates

    def _get_current_context(self, track_id: str) -> ContainerContexts:
        data = self._data.get(track_id)
        if not data:
            return self._context.UNKNOWN
        return data.get("context", self._context.UNKNOWN)

    def _get_current_state(self, track_id: str) -> ContainerSanitaryStates:
        data = self._data.get(track_id)
        if not data:
            return self._sanitary_states.UNKNOWN
        return data.get("sanitary_state", self._sanitary_states.UNKNOWN)

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

        if (event == ContainerEvents.INTERACTION and
                event_type == self._context.FOOD and
                current_context in (self._context.UNKNOWN, self._context.NEUTRAL)):
            self.set_context(track_id, self._context.IN_USE)
            return

        if (event == ContainerEvents.CONTAMINATION_EVENT and
                event_type == self._context.RAW_FOOD and
                current_context in (self._context.UNKNOWN, self._context.NEUTRAL, self._context.IN_USE)):
            self.set_context(track_id, self._context.RAW_ANIMAL)
            self.set_sanitary_state(track_id, self._sanitary_states.CONTAMINATED)
            return

        if (event == ContainerEvents.SANITIZATION_EVENT and
                current_context in (self._context.RAW_ANIMAL, self._context.CONTAMINATED, self._context.IN_USE)):
            self.set_context(track_id, self._context.SANITIZED)
            self.set_sanitary_state(track_id, self._sanitary_states.CLEAN_CONFIRMED)
            return

        if (event == ContainerEvents.INTERACTION and
                event_type == self._context.HANDS and
                current_context == self._context.SANITIZED):
            self.set_context(track_id, self._context.IN_USE)
            self.set_sanitary_state(track_id, self._sanitary_states.CLEAN_DEGRADING)
            return

        if event == ContainerEvents.TTL_EXPIRED:
            self.set_context(track_id, self._context.UNKNOWN)
            return

        if (event == ContainerEvents.OBS_EVENT and
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
        "sanitary_state": ContainerSanitaryStates.UNKNOWN,
        "context": ContainerContexts.NEUTRAL,
        "ttl_remaining_time": 300,
        "last_event_timestamp": time.time(),
    }

    container.add_data_by_track_id(track_id="tray_1", data=payload)

    container.process_event(
        "tray_1",
        ContainerEvents.INTERACTION,
        event_type=ContainerContexts.FOOD
    )

    container.process_event(
        "tray_1",
        ContainerEvents.TTL_EXPIRED
    )
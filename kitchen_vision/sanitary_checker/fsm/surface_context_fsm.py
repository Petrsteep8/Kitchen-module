import time
import enum
from typing import Optional, Dict, Any


class SurfaceEvents(enum.Enum):
    INTERACTION = "INTERACTION"
    CONTAMINATION_EVENT = "CONTAMINATION_EVENT"
    SANITIZATION_EVENT = "SANITIZATION_EVENT"
    OBS_EVENT = "OBS_EVENT"
    TTL_EXPIRED = "TTL_EXPIRED"


class SurfaceContexts(enum.Enum):
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
    TOOL = "TOOL"
    CONTAINER = "CONTAINER"


class SurfaceSanitaryStates(enum.Enum):
    CLEAN_CONFIRMED = "CLEAN_CONFIRMED"
    CLEAN_DEGRADING = "CLEAN_DEGRADING"
    UNKNOWN = "UNKNOWN"
    CONTAMINATED = "CONTAMINATED"


class SurfaceContextFSM:
    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._event_types = SurfaceEvents
        self._context = SurfaceContexts
        self._sanitary_states = SurfaceSanitaryStates

    def _get_current_context(self, track_id: str) -> SurfaceContexts:
        data = self._data.get(track_id)
        if not data:
            return self._context.UNKNOWN
        return data.get("context", self._context.UNKNOWN)

    def _get_current_state(self, track_id: str) -> SurfaceSanitaryStates:
        data = self._data.get(track_id)
        if not data:
            return self._sanitary_states.UNKNOWN
        return data.get("sanitary_state", self._sanitary_states.UNKNOWN)

    def set_context(self, track_id: str, new_context: SurfaceContexts) -> None:
        if track_id in self._data:
            self._data[track_id]["context"] = new_context

    def set_sanitary_state(self, track_id: str, new_state: SurfaceSanitaryStates) -> None:
        if track_id in self._data:
            self._data[track_id]["sanitary_state"] = new_state

    def process_event(
            self,
            track_id: str,
            event: SurfaceEvents,
            event_type: Optional[SurfaceContexts] = None,
    ) -> None:
        current_context = self._get_current_context(track_id)

        if (event == SurfaceEvents.INTERACTION and
                event_type in (self._context.FOOD, self._context.TOOL, self._context.CONTAINER) and
                current_context in (self._context.UNKNOWN, self._context.NEUTRAL)):
            self.set_context(track_id, self._context.IN_USE)
            return

        if (event == SurfaceEvents.CONTAMINATION_EVENT and
                event_type == self._context.RAW_FOOD and
                current_context in (self._context.UNKNOWN, self._context.NEUTRAL, self._context.IN_USE)):
            self.set_context(track_id, self._context.RAW_ANIMAL)
            self.set_sanitary_state(track_id, self._sanitary_states.CONTAMINATED)
            return

        if (event == SurfaceEvents.SANITIZATION_EVENT and
                current_context in (self._context.RAW_ANIMAL, self._context.CONTAMINATED, self._context.IN_USE)):
            self.set_context(track_id, self._context.SANITIZED)
            self.set_sanitary_state(track_id, self._sanitary_states.CLEAN_CONFIRMED)
            return

        if (event == SurfaceEvents.INTERACTION and
                event_type == self._context.HANDS and
                current_context == self._context.SANITIZED):
            self.set_context(track_id, self._context.IN_USE)
            self.set_sanitary_state(track_id, self._sanitary_states.CLEAN_DEGRADING)
            return

        if event == SurfaceEvents.TTL_EXPIRED:
            self.set_context(track_id, self._context.UNKNOWN)
            return

        if (event == SurfaceEvents.OBS_EVENT and
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
            data["entity_type"] = "surface"

        self._data[track_id] = data

    def get_data_by_track_id(self, track_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get(track_id)

    def get_context(self, track_id: str) -> SurfaceContexts:
        return self._get_current_context(track_id)

    def get_sanitary_state(self, track_id: str) -> SurfaceSanitaryStates:
        return self._get_current_state(track_id)

    def get_ttl_remaining(self, track_id: str) -> float:
        data = self._data.get(track_id)
        if not data:
            return 0.0
        return data.get("ttl_remaining_time", 0.0)


if __name__ == "__main__":
    surface = SurfaceContextFSM()

    payload = {
        "surface_id": "table_1",
        "entity_type": "surface",
        "sanitary_state": SurfaceSanitaryStates.UNKNOWN,
        "context": SurfaceContexts.NEUTRAL,
        "ttl_remaining_time": 600,
        "last_event_timestamp": time.time(),
    }

    surface.add_data_by_track_id(track_id="table_1", data=payload)

    surface.process_event(
        "table_1",
        SurfaceEvents.INTERACTION,
        event_type=SurfaceContexts.FOOD
    )

    surface.process_event(
        "table_1",
        SurfaceEvents.TTL_EXPIRED
    )
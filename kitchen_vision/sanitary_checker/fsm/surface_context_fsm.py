import time
import enum
from typing import Optional, Dict, Any


class SurfaceEvents(enum.Enum):
    interaction = "interaction"
    contamination_event = "contamination_event"
    sanitization_event = "sanitization_event"
    obs_event = "obs_event"
    ttl_expired = "ttl_expired"


class SurfaceContexts(enum.Enum):
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
    tool = "tool"
    container = "container"


class SurfaceSanitaryStates(enum.Enum):
    clean_confirmed = "clean_confirmed"
    clean_degrading = "clean_degrading"
    unknown = "unknown"
    contaminated = "contaminated"


class SurfaceContextFSM:
    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._event_types = SurfaceEvents
        self._context = SurfaceContexts
        self._sanitary_states = SurfaceSanitaryStates

    def _get_current_context(self, track_id: str) -> SurfaceContexts:
        data = self._data.get(track_id)
        if not data:
            return self._context.unknown
        return data.get("context", self._context.unknown)

    def _get_current_state(self, track_id: str) -> SurfaceSanitaryStates:
        data = self._data.get(track_id)
        if not data:
            return self._sanitary_states.unknown
        return data.get("sanitary_state", self._sanitary_states.unknown)

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

        if (event == SurfaceEvents.interaction and
                event_type in (self._context.food, self._context.tool, self._context.container) and
                current_context in (self._context.unknown, self._context.neutral)):
            self.set_context(track_id, self._context.in_use)
            return

        if (event == SurfaceEvents.contamination_event and
                event_type == self._context.raw_food and
                current_context in (self._context.unknown, self._context.neutral, self._context.in_use)):
            self.set_context(track_id, self._context.raw_animal)
            self.set_sanitary_state(track_id, self._sanitary_states.contaminated)
            return

        if (event == SurfaceEvents.sanitization_event and
                current_context in (self._context.raw_animal, self._context.contaminated, self._context.in_use)):
            self.set_context(track_id, self._context.sanitized)
            self.set_sanitary_state(track_id, self._sanitary_states.clean_confirmed)
            return

        if (event == SurfaceEvents.interaction and
                event_type == self._context.hands and
                current_context == self._context.sanitized):
            self.set_context(track_id, self._context.in_use)
            self.set_sanitary_state(track_id, self._sanitary_states.clean_degrading)
            return

        if event == SurfaceEvents.ttl_expired:
            self.set_context(track_id, self._context.unknown)
            return

        if (event == SurfaceEvents.obs_event and
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
        "sanitary_state": SurfaceSanitaryStates.unknown,
        "context": SurfaceContexts.neutral,
        "ttl_remaining_time": 600,
        "last_event_timestamp": time.time(),
    }

    surface.add_data_by_track_id(track_id="table_1", data=payload)

    surface.process_event(
        "table_1",
        SurfaceEvents.interaction,
        event_type=SurfaceContexts.food
    )

    surface.process_event(
        "table_1",
        SurfaceEvents.ttl_expired
    )
import time
from typing import Optional, Dict, Any

# Замена enum-классов на словари в формате UPPER_CASE
ZONE_EVENTS: Dict[str, str] = {
    "interaction": "interaction",
    "contamination_event": "contamination_event",
    "sanitization_event": "sanitization_event",
    "obs_event": "obs_event",
    "ttl_expired": "ttl_expired",
}

ZONE_CONTEXTS: Dict[str, str] = {
    "unknown": "unknown",
    "neutral": "neutral",
    "raw_animal": "raw_animal",
    "rte_food": "rte_food",
    "contaminated": "contaminated",
    "sanitized": "sanitized",
    "in_use": "in_use",
    "obs_lost": "obs_lost",
    "raw_food": "raw_food",
    "hands": "hands",
    "food": "food",
    "tool": "tool",
    "container": "container",
    "surface": "surface",
}

ZONE_SANITARY_STATES: Dict[str, str] = {
    "clean_confirmed": "clean_confirmed",
    "clean_degrading": "clean_degrading",
    "unknown": "unknown",
    "contaminated": "contaminated",
}


class ZoneContextFSM:
    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._event_types = ZONE_EVENTS
        self._context = ZONE_CONTEXTS
        self._sanitary_states = ZONE_SANITARY_STATES

    def _get_current_context(self, track_id: str) -> str:
        data = self._data.get(track_id)
        if not data:
            return self._context["unknown"]
        return data.get("context", self._context["unknown"])

    def _get_current_state(self, track_id: str) -> str:
        data = self._data.get(track_id)
        if not data:
            return self._sanitary_states["unknown"]
        return data.get("sanitary_state", self._sanitary_states["unknown"])

    def set_context(self, track_id: str, new_context: str) -> None:
        if track_id in self._data:
            self._data[track_id]["context"] = new_context

    def set_sanitary_state(self, track_id: str, new_state: str) -> None:
        if track_id in self._data:
            self._data[track_id]["sanitary_state"] = new_state

    def _get_last_event_timestamp(self, track_id: str) -> float:
        data = self._data.get(track_id)
        if not data:
            return 0.0
        return data.get("last_event_timestamp", 0.0)

    def set_ttl_remaining(self, track_id: str, new_ttl: int) -> None:
        if track_id in self._data:
            self._data[track_id]["ttl_remaining"] = new_ttl

    def update_ttl_remaining(self, track_id: str, additional_ttl: int) -> None:
        if track_id in self._data:
            self._data[track_id]["ttl_remaining"] += additional_ttl

    def update_last_event_timestamp(self, track_id: str):
        if track_id in self._data:
            self._data[track_id]["last_event_timestamp"] = time.time()

    def get_last_event_timestamp(self, track_id: str) -> float:
        return self._get_last_event_timestamp(track_id)

    def process_event(
            self,
            track_id: str,
            event: str,
            event_type: Optional[str] = None,
    ) -> None:
        current_context = self._get_current_context(track_id)

        if (event == ZONE_EVENTS["interaction"] and
                event_type in (self._context["food"], self._context["tool"], self._context["container"],
                               self._context["surface"]) and
                current_context in (self._context["unknown"], self._context["neutral"])):
            self.set_context(track_id, self._context["in_use"])
            return

        if (event == ZONE_EVENTS["contamination_event"] and
                event_type == self._context["raw_food"] and
                current_context in (self._context["unknown"], self._context["neutral"], self._context["in_use"])):
            self.set_context(track_id, self._context["raw_animal"])
            self.set_sanitary_state(track_id, self._sanitary_states["contaminated"])
            return

        if (event == ZONE_EVENTS["sanitization_event"] and
                current_context in (self._context["raw_animal"], self._context["contaminated"], self._context["in_use"])):
            self.set_context(track_id, self._context["sanitized"])
            self.set_sanitary_state(track_id, self._sanitary_states["clean_confirmed"])
            return

        if (event == ZONE_EVENTS["interaction"] and
                event_type == self._context["hands"] and
                current_context == self._context["sanitized"]):
            self.set_context(track_id, self._context["in_use"])
            self.set_sanitary_state(track_id, self._sanitary_states["clean_degrading"])
            return

        if event == ZONE_EVENTS["ttl_expired"]:
            self.set_context(track_id, self._context["unknown"])
            return

        if (event == ZONE_EVENTS["obs_event"] and
                event_type == self._context["obs_lost"]):
            self.set_context(track_id, self._context["unknown"])
            return

    def add_data_by_track_id(self, track_id: str, data: Dict[str, Any]) -> None:
        if "context" not in data:
            data["context"] = self._context["unknown"]
        if "sanitary_state" not in data:
            data["sanitary_state"] = self._sanitary_states["unknown"]
        if "ttl_remaining_time" not in data:
            data["ttl_remaining_time"] = 0
        if "last_event_timestamp" not in data:
            data["last_event_timestamp"] = time.time()
        if "entity_id" not in data:
            data["entity_id"] = track_id
        if "entity_type" not in data:
            data["entity_type"] = "zone"

        self._data[track_id] = data

    def get_data_by_track_id(self, track_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get(track_id)

    def get_context(self, track_id: str) -> str:
        return self._get_current_context(track_id)

    def get_sanitary_state(self, track_id: str) -> str:
        return self._get_current_state(track_id)

    def get_ttl_remaining(self, track_id: str) -> float:
        data = self._data.get(track_id)
        if not data:
            return 0.0
        return data.get("ttl_remaining_time", 0.0)


if __name__ == "__main__":
    zone = ZoneContextFSM()

    payload = {
        "zone_id": "prep_zone_1",
        "entity_type": "zone",
        "sanitary_state": ZONE_SANITARY_STATES["unknown"],
        "context": ZONE_CONTEXTS["neutral"],
        "ttl_remaining_time": 900,
        "last_event_timestamp": time.time(),
    }

    zone.add_data_by_track_id(track_id="prep_zone_1", data=payload)

    zone.process_event(
        "prep_zone_1",
        ZONE_EVENTS["interaction"],
        event_type=ZONE_CONTEXTS["food"]
    )

    zone.process_event(
        "prep_zone_1",
        ZONE_EVENTS["ttl_expired"]
    )
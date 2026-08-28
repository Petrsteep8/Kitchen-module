import time
from typing import Optional, Dict, Any

# Замена enum-классов на словари в формате UPPER_CASE
CONTAINER_EVENTS: Dict[str, str] = {
    "interaction": "interaction",
    "contamination_event": "contamination_event",
    "sanitization_event": "sanitization_event",
    "obs_event": "obs_event",
    "ttl_expired": "ttl_expired",
}

CONTAINER_CONTEXTS: Dict[str, str] = {
    "unknown": "unknown",
    "neutral": "neutral",
    "raw_animal": "raw_animal",
    "raw_plant_unwashed": "raw_plant_unwashed",
    "clean_prep": "clean_prep",
    "ready_to_eat": "ready_to_eat",
    "packaging_dispatch": "packaging_dispatch",
    "waste": "waste",
    "dirty_dishes_containers": "dirty_dishes_containers",
    "cleaning_inventory": "cleaning_inventory",
    "nonfood_contact": "nonfood_contact",
    "dirty_zone": "dirty_zone",
    "clean_zone": "clean_zone",
    "sanitized": "sanitized",
    "contaminated": "contaminated",
    "mixed": "mixed",
    "reset_pending": "reset_pending",
    "temporarily_blocked": "temporarily_blocked",
    "pending_resolution": "pending_resolution",
    "obs_lost": "obs_lost",
    "entered_reset_zone": "entered_reset_zone",
    "passed_clean_gate": "passed_clean_gate",
    "shift_started": "shift_started",
    "returned_to_kitchen": "returned_to_kitchen",
}

CONTAINER_SANITARY_STATES: Dict[str, str] = {
    "clean_confirmed": "clean_confirmed",
    "clean_degrading": "clean_degrading",
    "unknown": "unknown",
    "contaminated": "contaminated",
}


class ContainerContextFSM:
    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._event_types = CONTAINER_EVENTS
        self._context = CONTAINER_CONTEXTS
        self._sanitary_states = CONTAINER_SANITARY_STATES

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
            source_context: str = None,
    ) -> None:
        if self.get_data_by_track_id(track_id) is None:
            return

        current_context = self._get_current_context(track_id)

        if (event == CONTAINER_EVENTS["interaction"] and event_type == "hands" and current_context in (
                self._context["unknown"], self._context["neutral"])):
            self.set_context(track_id, source_context)
            return

        if (event == CONTAINER_EVENTS["contamination_event"] and
                event_type in ["raw_food", "waste", "dirty_surface"] and current_context in (
                        self._context["unknown"], self._context["neutral"])):
            self.set_context(track_id, event_type)
            self.set_sanitary_state(track_id, self._sanitary_states["contaminated"])
            return

        if event == CONTAINER_EVENTS["reset_event"] and event_type in ["container_replaced_clean",
                                                                       "disposable_container_used"]:
            self.set_context(track_id, self._context["sanitized"])
            self.set_sanitary_state(track_id, self._sanitary_states["clean_confirmed"])
            return

        if event == CONTAINER_EVENTS["ttl_expired"]:
            self.set_context(track_id, self._context["unknown"])
            self.set_sanitary_state(track_id, self._sanitary_states["unknown"])
            return

        if (event == CONTAINER_EVENTS["obs_event"] and
                event_type == self._context["obs_lost"]):
            self.set_context(track_id, self._context["unknown"])
            self.set_sanitary_state(track_id, self._sanitary_states["unknown"])
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
            data["entity_type"] = "container"

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
    container = ContainerContextFSM()

    payload = {
        "container_id": "tray_1",
        "entity_type": "container",
        "sanitary_state": CONTAINER_SANITARY_STATES["unknown"],
        "context": CONTAINER_CONTEXTS["neutral"],
        "ttl_remaining_time": 300,
        "last_event_timestamp": time.time(),
    }

    container.add_data_by_track_id(track_id="tray_1", data=payload)

    container.process_event(
        "tray_1",
        CONTAINER_EVENTS["interaction"],
        event_type=CONTAINER_CONTEXTS["food"]
    )

    container.process_event(
        "tray_1",
        CONTAINER_EVENTS["ttl_expired"]
    )

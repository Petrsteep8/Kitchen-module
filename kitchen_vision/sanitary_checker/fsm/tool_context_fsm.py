import time
from typing import Optional, Dict, Any

# Замена enum-классов на словари в формате UPPER_CASE
TOOL_EVENTS: Dict[str, str] = {
    "interaction": "interaction",
    "contamination_event": "contamination_event",
    "sanitization_event": "sanitization_event",
    "obs_event": "obs_event",
    "ttl_expired": "ttl_expired",
}

TOOL_CONTEXTS: Dict[str, str] = {
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

TOOL_SANITARY_STATES: Dict[str, str] = {
    "clean_confirmed": "clean_confirmed",
    "clean_degrading": "clean_degrading",
    "unknown": "unknown",
    "contaminated": "contaminated",
}


class ToolContextFSM:
    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._event_details = TOOL_EVENTS
        self._context = TOOL_CONTEXTS
        self._sanitary_states = TOOL_SANITARY_STATES

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
            event_details: Optional[str] = None,
    ) -> None:
        if self.get_data_by_track_id(track_id) is None:
            return
        
        current_context = self._get_current_context(track_id)

        if (event == TOOL_EVENTS["interaction"] and
                event_details == "food" and
                current_context in (self._context["unknown"], self._context["neutral"])):
            self.set_context(track_id, current_context)
            self.set_sanitary_state(track_id, self._sanitary_states["contaminated"])
            return

        if (event == TOOL_EVENTS["interaction"] and
                event_details == "food" and
                current_context in (self._context["sanitized"])):
            self.set_context(track_id, current_context)
            self.set_sanitary_state(track_id, self._sanitary_states["clean_confirmed"])
            return

        if (event == TOOL_EVENTS["contamination_event"] and
                event_details in (self._context["raw_food"], self._context["waste"], self._context["dirty_surface"]) and
                current_context in (self._context["unknown"], self._context["neutral"])):
            self.set_context(track_id, self._context["raw_animal"])
            self.set_sanitary_state(track_id, self._sanitary_states["contaminated"])
            return

        if event == TOOL_EVENTS["reset_event"] and event_details in ["tool_washed", "clean_tool_taken", "tool_replaced"]:
            self.set_context(track_id, self._context["sanitized"])
            self.set_sanitary_state(track_id, self._sanitary_states["clean_confirmed"])
            return

        if event == TOOL_EVENTS["ttl_expired"]:
            self.set_context(track_id, event_details)
            self.set_sanitary_state(track_id, event_details)
            return

        if (event == TOOL_EVENTS["obs_event"] and
                event_details == self._context["obs_lost"]):
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
            data["entity_type"] = "tool"

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
    tool = ToolContextFSM()

    payload = {
        "tool_id": "knife_1",
        "entity_type": "tool",
        "sanitary_state": TOOL_SANITARY_STATES["unknown"],
        "context": TOOL_CONTEXTS["neutral"],
        "ttl_remaining_time": 120,
        "last_event_timestamp": time.time(),
    }

    tool.add_data_by_track_id(track_id="knife_1", data=payload)

    tool.process_event(
        "knife_1",
        TOOL_EVENTS["interaction"],
        event_details=TOOL_CONTEXTS["food"]
    )
    print(tool.get_data_by_track_id('knife_1'))

    tool.process_event(
        "knife_1",
        TOOL_EVENTS["ttl_expired"]
    )
    print(tool.get_data_by_track_id('knife_1'))
import time
from typing import Optional, Dict, Any

EMPLOYEE_EVENTS: Dict[str, str] = {
    "context_observed_raw_animal": "context_observed_raw_animal",
    "context_observed_clean_prep": "context_observed_clean_prep",
    "context_observed_ready_to_eat": "context_observed_ready_to_eat",
    "zone_enter": "zone_enter",
    "zone_exit": "zone_exit",
    "contamination_event": "contamination_event",
    "reset_event": "reset_event",
    "ttl_expired": "ttl_expired",
    "obs_event": "obs_event",
}

EMPLOYEE_CONTEXTS: Dict[str, str] = {
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

EMPLOYEE_SANITARY_STATES: Dict[str, str] = {
    "clean_confirmed": "clean_confirmed",
    "clean_degrading": "clean_degrading",
    "unknown": "unknown",
    "contaminated": "contaminated",
}


class EmployeeContextFSM:
    """
    Конечный автомат контекста сотрудника (раздел 28.2)

    Таблица переходов:
    current_state | event | condition | next_state
    UNKNOWN | CONTEXT_OBSERVED_RAW_ANIMAL | confidence достаточен | RAW_ANIMAL
    UNKNOWN | CONTEXT_OBSERVED_CLEAN_PREP | confidence достаточен | CLEAN_PREP
    UNKNOWN | CONTEXT_OBSERVED_READY_TO_EAT | confidence достаточен | READY_TO_EAT
    ANY | ZONE_ENTER(DIRTY_ZONE) | dwell подтверждён | DIRTY_ZONE
    ANY | ZONE_ENTER(CLEAN_ZONE) | dwell подтверждён | CLEAN_ZONE
    ANY | ZONE_EXIT | подтверждённый выход | NEUTRAL
    ANY | CONTAMINATION_EVENT(NONFOOD_CONTACT) | target=employee | NONFOOD_CONTACT
    ANY | RESET_EVENT(ENTERED_RESET_ZONE, PASSED_CLEAN_GATE, SHIFT_STARTED, RETURNED_TO_KITCHEN) | правило reset применимо | SANITIZED или CLEAN_DEGRADING
    ANY | TTL_EXPIRED | нет подтверждений | UNKNOWN
    ANY | OBS_EVENT(OBS_LOST) | — | UNKNOWN
    """

    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._event_detailss = EMPLOYEE_EVENTS
        self._context = EMPLOYEE_CONTEXTS
        self._sanitary_states = EMPLOYEE_SANITARY_STATES

        self._contaminating_contexts = {
            self._context["raw_animal"],
            self._context["raw_plant_unwashed"],
            self._context["waste"],
            self._context["dirty_dishes_containers"],
            self._context["cleaning_inventory"],
            self._context["nonfood_contact"],
            self._context["dirty_zone"],
            self._context["contaminated"],
        }

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

    def _get_last_event_timestamp(self, track_id: str) -> float:
        data = self._data.get(track_id)
        if not data:
            return 0.0
        return data.get("last_event_timestamp", 0.0)

    def set_context(self, track_id: str, new_context: str) -> None:
        if track_id in self._data:
            self._data[track_id]["context"] = new_context

    def set_sanitary_state(self, track_id: str, new_state: str) -> None:
        if track_id in self._data:
            self._data[track_id]["sanitary_state"] = new_state

    def set_is_reset_pending(self, track_id: str, reset_pending: bool) -> None:
        if track_id in self._data:
            self._data[track_id]["reset_pending"] = reset_pending

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
        current_context = self._get_current_context(track_id)

        if (event == EMPLOYEE_EVENTS["context_observed_raw_animal"] and
                current_context == self._context["unknown"]):
            self.set_context(track_id, self._context["raw_animal"])
            return

        if (event == EMPLOYEE_EVENTS["context_observed_clean_prep"] and
                current_context == self._context["unknown"]):
            self.set_context(track_id, self._context["clean_prep"])
            return

        if (event == EMPLOYEE_EVENTS["context_observed_ready_to_eat"] and
                current_context == self._context["unknown"]):
            self.set_context(track_id, self._context["ready_to_eat"])
            return

        if (event == EMPLOYEE_EVENTS["zone_enter"] and
                event_details == self._context["dirty_zone"]):
            self.set_context(track_id, self._context["dirty_zone"])
            return

        if (event == EMPLOYEE_EVENTS["zone_enter"] and
                event_details == self._context["clean_zone"]):
            self.set_context(track_id, self._context["clean_zone"])
            return

        if (event == EMPLOYEE_EVENTS["contamination_event"] and
                event_details == self._context["nonfood_contact"]):
            self.set_context(track_id, self._context["nonfood_contact"])
            self.set_sanitary_state(track_id, self._sanitary_states["contaminated"])
            return

        if event == EMPLOYEE_EVENTS["reset_event"]:
            reset_types = {
                self._context["entered_reset_zone"],
                self._context["passed_clean_gate"],
                self._context["shift_started"],
                self._context["returned_to_kitchen"],
            }
            if event_details in reset_types:
                if event_details == self._context["shift_started"]:
                    self.set_context(track_id, self._context["sanitized"])
                    self.set_sanitary_state(track_id, self._sanitary_states["clean_confirmed"])
                else:
                    self.set_context(track_id, self._context["reset_pending"])
                    if track_id in self._data:
                        self._data[track_id]["reset_pending"] = True
                return

        if event == EMPLOYEE_EVENTS["ttl_expired"]:
            self.set_context(track_id, self._context["unknown"])
            self.set_sanitary_state(track_id, self._sanitary_states["unknown"])
            return

        if (event == EMPLOYEE_EVENTS["obs_event"] and
                event_details == self._context["obs_lost"]):
            self.set_context(track_id, self._context["unknown"])
            self.set_sanitary_state(track_id, self._sanitary_states["unknown"])
            return

    def add_data_by_track_id(self, track_id: str, data: Dict[str, Any]) -> None:
        if "context" not in data:
            data["context"] = self._context["unknown"]
        if "sanitary_state" not in data:
            data["sanitary_state"] = self._sanitary_states["unknown"]
        if "reset_pending" not in data:
            data["reset_pending"] = False
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

    def get_context(self, track_id: str) -> str:
        return self._get_current_context(track_id)

    def get_sanitary_state(self, track_id: str) -> str:
        return self._get_current_state(track_id)

    def is_reset_pending(self, track_id: str) -> bool:
        data = self._data.get(track_id)
        if not data:
            return False
        return data.get("reset_pending", False)

    def get_ttl_remaining(self, track_id: str) -> float:
        data = self._data.get(track_id)
        if not data:
            return 0.0
        return data.get("ttl_remaining_time", 0.0)


if __name__ == "__main__":
    employee = EmployeeContextFSM()

    payload = {
        "employee_id": 1,
        "entity_type": "employee",
        "sanitary_state": EMPLOYEE_SANITARY_STATES["clean_confirmed"],
        "context": EMPLOYEE_CONTEXTS["unknown"],
        "ttl_remaining_time": 60,
        "last_event_timestamp": time.time(),
        "reset_pending": False
    }

    employee.add_data_by_track_id(track_id=str(1), data=payload)
    print("Initial:", employee.get_data_by_track_id(str(1)))

    employee.process_event(
        str(1),
        EMPLOYEE_EVENTS["zone_enter"],
        event_details=EMPLOYEE_CONTEXTS["dirty_zone"]
    )
    print("After DIRTY_ZONE:", employee.get_data_by_track_id(str(1)))

    employee.process_event(
        str(1),
        EMPLOYEE_EVENTS["contamination_event"],
        event_details=EMPLOYEE_CONTEXTS["nonfood_contact"]
    )
    print("After NONFOOD_CONTACT:", employee.get_data_by_track_id(str(1)))

    employee.process_event(
        str(1),
        EMPLOYEE_EVENTS["zone_exit"]
    )
    print("After ZONE_EXIT:", employee.get_data_by_track_id(str(1)))

    employee.process_event(
        str(1),
        EMPLOYEE_EVENTS["ttl_expired"]
    )
    print("After TTL_EXPIRED:", employee.get_data_by_track_id(str(1)))
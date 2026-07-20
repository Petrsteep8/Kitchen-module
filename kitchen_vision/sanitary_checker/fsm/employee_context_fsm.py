import time
import enum
from typing import Optional, Dict, Any


class EmployeeEvents(enum.Enum):
    CONTEXT_OBSERVED_RAW_ANIMAL = "CONTEXT_OBSERVED_RAW_ANIMAL"
    CONTEXT_OBSERVED_CLEAN_PREP = "CONTEXT_OBSERVED_CLEAN_PREP"
    CONTEXT_OBSERVED_READY_TO_EAT = "CONTEXT_OBSERVED_READY_TO_EAT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    CONTAMINATION_EVENT = "CONTAMINATION_EVENT"
    RESET_EVENT = "RESET_EVENT"
    TTL_EXPIRED = "TTL_EXPIRED"
    OBS_EVENT = "OBS_EVENT"


class EmployeeContexts(enum.Enum):
    UNKNOWN = "UNKNOWN"
    NEUTRAL = "NEUTRAL"
    RAW_ANIMAL = "RAW_ANIMAL"
    RAW_PLANT_UNWASHED = "RAW_PLANT_UNWASHED"
    CLEAN_PREP = "CLEAN_PREP"
    READY_TO_EAT = "READY_TO_EAT"
    PACKAGING_DISPATCH = "PACKAGING_DISPATCH"
    WASTE = "WASTE"
    DIRTY_DISHES_CONTAINERS = "DIRTY_DISHES_CONTAINERS"
    CLEANING_INVENTORY = "CLEANING_INVENTORY"
    NONFOOD_CONTACT = "NONFOOD_CONTACT"
    DIRTY_ZONE = "DIRTY_ZONE"
    CLEAN_ZONE = "CLEAN_ZONE"
    SANITIZED = "SANITIZED"
    CONTAMINATED = "CONTAMINATED"
    MIXED = "MIXED"
    RESET_PENDING = "RESET_PENDING"
    TEMPORARILY_BLOCKED = "TEMPORARILY_BLOCKED"
    PENDING_RESOLUTION = "PENDING_RESOLUTION"
    OBS_LOST = "OBS_LOST"
    ENTERED_RESET_ZONE = "ENTERED_RESET_ZONE"
    PASSED_CLEAN_GATE = "PASSED_CLEAN_GATE"
    SHIFT_STARTED = "SHIFT_STARTED"
    RETURNED_TO_KITCHEN = "RETURNED_TO_KITCHEN"


class EmployeeSanitaryStates(enum.Enum):
    CLEAN_CONFIRMED = "CLEAN_CONFIRMED"
    CLEAN_DEGRADING = "CLEAN_DEGRADING"
    UNKNOWN = "UNKNOWN"
    CONTAMINATED = "CONTAMINATED"


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
        self._event_types = EmployeeEvents
        self._context = EmployeeContexts
        self._sanitary_states = EmployeeSanitaryStates

        self._contaminating_contexts = {
            self._context.RAW_ANIMAL,
            self._context.RAW_PLANT_UNWASHED,
            self._context.WASTE,
            self._context.DIRTY_DISHES_CONTAINERS,
            self._context.CLEANING_INVENTORY,
            self._context.NONFOOD_CONTACT,
            self._context.DIRTY_ZONE,
            self._context.CONTAMINATED,
        }

    def _get_current_context(self, track_id: str) -> EmployeeContexts:
        data = self._data.get(track_id)
        if not data:
            return self._context.UNKNOWN
        return data.get("context", self._context.UNKNOWN)

    def _get_current_state(self, track_id: str) -> EmployeeSanitaryStates:
        data = self._data.get(track_id)
        if not data:
            return self._sanitary_states.UNKNOWN
        return data.get("sanitary_state", self._sanitary_states.UNKNOWN)

    def set_context(self, track_id: str, new_context: EmployeeContexts) -> None:
        if track_id in self._data:
            self._data[track_id]["context"] = new_context

    def set_sanitary_state(self, track_id: str, new_state: EmployeeSanitaryStates) -> None:
        if track_id in self._data:
            self._data[track_id]["sanitary_state"] = new_state

    def set_is_reset_pending(self, track_id: str, reset_pending: bool) -> None:
        if track_id in self._data:
            self._data[track_id]["reset_pending"] = reset_pending

    def process_event(
            self,
            track_id: str,
            event: EmployeeEvents,
            event_type: Optional[EmployeeContexts] = None,
    ) -> None:
        current_context = self._get_current_context(track_id)

        if (event == EmployeeEvents.CONTEXT_OBSERVED_RAW_ANIMAL and
                current_context == self._context.UNKNOWN):
            self.set_context(track_id, self._context.RAW_ANIMAL)
            return

        if (event == EmployeeEvents.CONTEXT_OBSERVED_CLEAN_PREP and
                current_context == self._context.UNKNOWN):
            self.set_context(track_id, self._context.CLEAN_PREP)
            return

        if (event == EmployeeEvents.CONTEXT_OBSERVED_READY_TO_EAT and
                current_context == self._context.UNKNOWN):
            self.set_context(track_id, self._context.READY_TO_EAT)
            return

        if (event == EmployeeEvents.ZONE_ENTER and
                event_type == self._context.DIRTY_ZONE):
            self.set_context(track_id, self._context.DIRTY_ZONE)
            self.set_sanitary_state(track_id, self._sanitary_states.CONTAMINATED)
            return

        if (event == EmployeeEvents.ZONE_ENTER and
                event_type == self._context.CLEAN_ZONE):
            self.set_context(track_id, self._context.CLEAN_ZONE)
            return

        if event == EmployeeEvents.ZONE_EXIT:
            self.set_context(track_id, self._context.NEUTRAL)
            return

        if (event == EmployeeEvents.CONTAMINATION_EVENT and
                event_type == self._context.NONFOOD_CONTACT):
            self.set_context(track_id, self._context.NONFOOD_CONTACT)
            self.set_sanitary_state(track_id, self._sanitary_states.CONTAMINATED)
            return

        #TODO тут хуета переделать надо
        if event == EmployeeEvents.RESET_EVENT:
            reset_types = {
                self._context.ENTERED_RESET_ZONE,
                self._context.PASSED_CLEAN_GATE,
                self._context.SHIFT_STARTED,
                self._context.RETURNED_TO_KITCHEN,
            }
            if event_type in reset_types:
                if event_type == self._context.SHIFT_STARTED:
                    self.set_sanitary_state(track_id, self._sanitary_states.CLEAN_CONFIRMED)
                    self.set_context(track_id, self._context.SANITIZED)
                else:
                    self.set_context(track_id, self._context.RESET_PENDING)
                    if track_id in self._data:
                        self._data[track_id]["reset_pending"] = True
                return

        if event == EmployeeEvents.TTL_EXPIRED:
            self.set_context(track_id, self._context.UNKNOWN)
            self.set_sanitary_state(track_id, self._sanitary_states.UNKNOWN)
            return

        if (event == EmployeeEvents.OBS_EVENT and
                event_type == self._context.OBS_LOST):
            self.set_context(track_id, self._context.UNKNOWN)
            self.set_sanitary_state(track_id, self._sanitary_states.UNKNOWN)
            return

    def add_data_by_track_id(self, track_id: str, data: Dict[str, Any]) -> None:
        if "context" not in data:
            data["context"] = self._context.UNKNOWN
        if "sanitary_state" not in data:
            data["sanitary_state"] = self._sanitary_states.UNKNOWN
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

    def get_context(self, track_id: str) -> EmployeeContexts:
        return self._get_current_context(track_id)

    def get_sanitary_state(self, track_id: str) -> EmployeeSanitaryStates:
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
        "sanitary_state": EmployeeSanitaryStates.CLEAN_CONFIRMED,
        "context": EmployeeContexts.UNKNOWN,
        "ttl_remaining_time": 60,
        "last_event_timestamp": time.time(),
        "reset_pending": False
    }

    employee.add_data_by_track_id(track_id=str(1), data=payload)
    print("Initial:", employee.get_data_by_track_id(str(1)))

    employee.process_event(
        str(1),
        EmployeeEvents.ZONE_ENTER,
        event_type=EmployeeContexts.DIRTY_ZONE
    )
    print("After DIRTY_ZONE:", employee.get_data_by_track_id(str(1)))

    employee.process_event(
        str(1),
        EmployeeEvents.CONTAMINATION_EVENT,
        event_type=EmployeeContexts.NONFOOD_CONTACT
    )
    print("After NONFOOD_CONTACT:", employee.get_data_by_track_id(str(1)))

    employee.process_event(
        str(1),
        EmployeeEvents.ZONE_EXIT
    )
    print("After ZONE_EXIT:", employee.get_data_by_track_id(str(1)))

    employee.process_event(
        str(1),
        EmployeeEvents.TTL_EXPIRED
    )
    print("After TTL_EXPIRED:", employee.get_data_by_track_id(str(1)))
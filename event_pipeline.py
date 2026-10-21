import queue
import threading
import logging
from typing import Optional, Dict, Any, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("event_pipeline")

try:
    from kitchen_vision.sanitary_checker.fsm.fsm_hub import FSMHub
    from kitchen_vision.sanitary_checker.reset_logic.reset_logic import ResetLogic
    from kitchen_vision.sanitary_checker.ttl_logic.ttl_logic import TTLLogic
    from kitchen_vision.sanitary_checker.context_transition_engine.decision_table import DecisionTable
    from kitchen_vision.sanitary_checker.context_transition_engine.contamination_table import ContaminationTable
    from kitchen_vision.sanitary_checker.context_transition_engine.context_transfer_table import ContextTransferTable
except ImportError:
    logger.error("Failed to import FSM")

ENTITY_TYPES = {
    "employee": "employee",
    "hands": "hands",
    "tool": "tool",
    "container": "container",
    "surface": "surface",
    "zone": "zone",
    "food": "food",
    "action": "action",
    "incident": "incident",
    "system": "system",
}

SANITARY_STATES = {
    "clean_confirmed": "clean_confirmed",
    "clean_degrading": "clean_degrading",
    "unknown": "unknown",
    "contaminated": "contaminated",
}

CONTEXTS = {
    "neutral": "neutral",
    "unknown": "unknown",
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
}

OBSERVATION_STATES = {
    "obs_ok": "obs_ok",
    "obs_weak": "obs_weak",
    "obs_degraded": "obs_degraded",
    "obs_lost": "obs_lost",
}

ACTION_END_REASONS = {
    "tracking_lost": "tracking_lost",
    "left_zone": "left_zone",
    "key_object_lost": "key_object_lost",
    "motion_stopped": "motion_stopped",
}

CONTAMINATION_TYPES = {
    "raw_food": "raw_food",
    "waste": "waste",
    "dirty_surface": "dirty_surface",
    "packaging": "packaging",
    "nonfood_contact": "nonfood_contact",
    "unknown": "unknown",
}

RESET_TYPES = {
    "hands_washed": "hands_washed",
    "gloves_changed": "gloves_changed",
    "gloves_removed": "gloves_removed",
    "gloves_put_on": "gloves_put_on",
    "tool_washed": "tool_washed",
    "clean_tool_taken": "clean_tool_taken",
    "tool_replaced": "tool_replaced",
    "container_replaced_clean": "container_replaced_clean",
    "disposable_container_used": "disposable_container_used",
    "surface_sanitized": "surface_sanitized",
    "entered_reset_zone": "entered_reset_zone",
    "passed_clean_gate": "passed_clean_gate",
    "shift_started": "shift_started",
    "returned_to_kitchen": "returned_to_kitchen",
}

ZONE_TYPES = {
    "dirty_zone": "dirty_zone",
    "prep_zone": "prep_zone",
    "clean_zone": "clean_zone",
    "packaging_zone": "packaging_zone",
    "reset_zone": "reset_zone",
    "waste_zone": "waste_zone",
    "wash_zone": "wash_zone",
    "neutral_zone": "neutral_zone",
    "clean_gate": "clean_gate",
}

EVENT_TYPES = {
    "detection": "detection",
    "tracking": "tracking",
    "zone_enter": "zone_enter",
    "zone_exit": "zone_exit",
    "interaction": "interaction",
    "action_start": "action_start",
    "action_end": "action_end",
    "reset_event": "reset_event",
    "contamination_event": "contamination_event",
    "obs_event": "obs_event",
    "data_quality_event": "data_quality_event",
    "sanitary_event": "sanitary_event",
    "process_event": "process_event",
    "incident": "incident",
    "ttl_expired": "ttl_expired",
}

DECISION_MODES = {
    "sanitary_violation_confirmed": "sanitary_violation_confirmed",
    "process_event_confirmed": "process_event_confirmed",
    "unresolved_due_to_data_quality": "unresolved_due_to_data_quality",
    "data_quality_event_confirmed": "data_quality_event_confirmed",
    "no_event": "no_event",
}

ACTION_END_REASONS_LIST = ["tracking_lost", "left_zone", "key_object_lost", "motion_stopped"]

CARRY_TYPES = ["none", "food", "tool"]

SEVERITY_INTERNAL = ["critical", "high", "medium", "low", "info", "warning", "violation"]

SEVERITY_EXTERNAL = ["info", "warning", "violation"]

ESCALATION_CATEGORIES = ["alcohol_escalation", "smoking_escalation"]

INCOMPATIBLE_CAUSES = [
    "missing_hands_washed",
    "missing_gloves_change",
    "missing_surface_sanitized",
    "missing_tool_reset",
    "missing_container_reset",
    "forbidden_zone_transition",
    "contaminated_source_contact",
    "unknown_source_state",
    "behavioral_block_active",
]

PROCESS_EVENT_TYPES = ["food_handoff", "action_interruption", "zone_risk_score"]

HYGIENE_ATTRIBUTES = ["gloves", "cap", "apron", "mask", "hairnet"]

# Итоговые решения (строка)
DECISION_MODE_VALUES = list(DECISION_MODES.keys())

SEVERITY_MAPPING = {
    "critical": "violation",
    "high": "violation",
    "violation": "violation",
    "medium": "warning",
    "low": "warning",
    "warning": "warning",
    "info": "info",
}

EVENTS_WITH_NO_TTL = [
    "obs_event",
    "data_quality_event",
    "sanitary_event",
    "process_event",
]


def choose_event_by_priority(event_data_for_fsm_transition: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if len(event_data_for_fsm_transition) == 0:
        return None
    return min(event_data_for_fsm_transition, key=lambda e: e["priority"])


def validate_event_structure(normalized_event: Dict) -> bool:
    if not normalized_event.get("event_id"):
        return False
    if not normalized_event.get("event_type"):
        return False
    if not normalized_event.get("timestamp"):
        return False
    if not normalized_event.get("camera_id"):
        return False
    if not normalized_event.get("entity_id"):
        return False
    if not normalized_event.get("entity_type"):
        return False
    if not normalized_event.get("confidence"):
        return False
    if not normalized_event.get("time_source"):
        return False
    if not normalized_event.get("payload"):
        return False
    return True


class EventPipeline(threading.Thread):
    def __init__(self, input_queue: queue.Queue):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self._input_queue = input_queue

        self._event_types = EVENT_TYPES
        self._process_event_types = PROCESS_EVENT_TYPES
        self._reset_types = RESET_TYPES
        self._contamination_types = CONTAMINATION_TYPES
        self._entity_types = ENTITY_TYPES
        self._sanitary_states = SANITARY_STATES
        self._contexts = CONTEXTS
        self._observation_states = OBSERVATION_STATES
        self._action_end_reasons = ACTION_END_REASONS
        self._zone_types = ZONE_TYPES
        self._decision_modes = DECISION_MODES
        self._incompatible_causes = INCOMPATIBLE_CAUSES
        self._severity_internal = SEVERITY_INTERNAL
        self._severity_external = SEVERITY_EXTERNAL
        self._escalation_categories = ESCALATION_CATEGORIES
        self._carry_types = CARRY_TYPES
        self._hygiene_attributes = HYGIENE_ATTRIBUTES
        self._events_with_no_ttl = EVENTS_WITH_NO_TTL

        self._fsm_hub = FSMHub()
        self._reset_logic = ResetLogic(fsm_hub=self._fsm_hub)
        self._ttl_logic = TTLLogic(fsm_hub=self._fsm_hub)
        self._context_transfer_table = ContextTransferTable(fsm_hub=self._fsm_hub)
        self._contamination_table = ContaminationTable()
        self._decision_table = DecisionTable(
            context_transfer_table=self._context_transfer_table,
            contamination_table=self._contamination_table
        )

        logger.info("EventPipeline initialized")

    def run(self):
        while not self.stop_event.is_set():
            try:
                pkt = self._input_queue.get()
                if pkt is not None:
                    pass
            except queue.Empty:
                continue

    def _get_fsm(self, entity_type: str):
        """Получение FSM для сущности"""
        if entity_type == "employee":
            return self._fsm_hub.employee_context
        elif entity_type == "hands":
            return self._fsm_hub.hands_context
        elif entity_type == "tool":
            return self._fsm_hub.tool_context
        elif entity_type == "container":
            return self._fsm_hub.container_context
        elif entity_type == "surface":
            return self._fsm_hub.surface_context
        elif entity_type == "zone":
            return self._fsm_hub.zone_context
        elif entity_type == "food":
            return None
        elif entity_type == "obs":
            return self._fsm_hub.observation_fsm
        return None

    def get_data_for_fsm_transition(self,
                                    normalized_event: Optional[Dict[str, Any]],
                                    sanitary_events: List[Dict[str, Any]] = None
        ):
        event_type = normalized_event.get("event_type")
        payload = normalized_event.get("payload")
        target_entities = []
        event_details = None
        source_context = None
        priority = 0

        if event_type == self._event_types["ttl_expired"]:
            target_entities.append({
                payload.get("target_entity_type"): payload.get("target_entity_id")
            })
            event_details = payload["next_state"]
            priority = 6

        if event_type == self._event_types["zone_enter"]:
            target_entities.append({"employee": payload.get("employee_id")})
            event_details = payload.get("zone_type")
            priority = 4

        elif event_type == self._event_types["zone_exit"]:
            target_entities.append({"employee": payload.get("employee_id")})
            priority = 4

        elif event_type == self._event_types["interaction"]:
            target_entities.append({
                payload.get("target_entity_type"): payload.get("target_entity_id")
            })
            event_details = payload.get("actor_entity_type")
            if sanitary_events and len(sanitary_events) > 0:
                source_context = sanitary_events[0].get("source_context")
            priority = 3

        elif event_type == self._event_types["action_start"]:
            priority = 10

        elif event_type == self._event_types["action_end"]:
            event_details = payload.get("end_reason")
            priority = 10

        elif event_type == self._event_types["reset_event"]:
            target_entities = payload.get("target_entities")
            event_details = payload.get("reset_type")
            priority = 0

        elif event_type == self._event_types["contamination_event"]:
            target_entities.append({
                payload.get("target_entity"): payload.get("target_entity_id")
            })
            event_details = payload.get("contamination_type")

            if payload.get("priority") == "high":
                priority = 1
            else:
                priority = 5

        elif event_type == self._event_types["obs_event"]:
            event_details = payload.get("state")
            if event_details == "obs_lost":
                priority = 7
            else:
                priority = 10

        elif event_type == self._event_types["data_quality_event"]:
            event_details = payload.get("reason")
            priority = 10

        elif event_type == self._event_types["process_event"]:
            event_details = payload.get("process_event_type")
            priority = 10

        elif event_type == self._event_types["sanitary_event"]:
            event_details = payload.get("sanitary_event_type")
            priority = 10

        return {
            "event_type": event_type,
            "event_details": event_details,
            "target_entities": target_entities,
            "source_context": source_context,
            "priority": priority,
        }

    def choose_fsm_transition(self, event_data_for_fsm_transition):
        target_entities = event_data_for_fsm_transition.get("target_entities")
        event_type = event_data_for_fsm_transition.get("event_type")
        event_details = event_data_for_fsm_transition.get("event_details")

        for target_entity in target_entities:
            for key, value in target_entity.items():
                print(f"{key}: {value}")

                if key == self._entity_types["employee"]:
                    fsm = self._get_fsm(entity_type=self._entity_types["employee"])
                    if fsm and hasattr(fsm, "process_event"):
                        fsm.process_event(event=event_type, event_details=event_details, track_id=value)

                elif key == self._entity_types["hands"]:
                    fsm = self._get_fsm(entity_type=self._entity_types["hands"])
                    if fsm and hasattr(fsm, "process_event"):
                        fsm.process_event(event=event_type, event_details=event_details, track_id=value)

                elif key == self._entity_types["tool"]:
                    fsm = self._get_fsm(entity_type=self._entity_types["tool"])
                    if fsm and hasattr(fsm, "process_event"):
                        fsm.process_event(event=event_type, event_details=event_details, track_id=value)

                elif key == self._entity_types["container"]:
                    source_context = event_data_for_fsm_transition.get("source_context")
                    fsm = self._get_fsm(entity_type=self._entity_types["container"])
                    if fsm and hasattr(fsm, "process_event"):
                        fsm.process_event(event=event_type, event_details=event_details, track_id=value, source_context=source_context)

                elif key == self._entity_types["surface"]:
                    source_context = event_data_for_fsm_transition.get("source_context")
                    fsm = self._get_fsm(entity_type=self._entity_types["surface"])
                    if fsm and hasattr(fsm, "process_event"):
                        fsm.process_event(event=event_type, event_details=event_details, track_id=value, source_context=source_context)

                elif key == self._entity_types["zone"]:
                    fsm = self._get_fsm(entity_type=self._entity_types["zone"])
                    if fsm and hasattr(fsm, "process_event"):
                        fsm.process_event(event=event_type, event_details=event_details, track_id=value)

    def process_event(self, normalized_event: Dict) -> None:
        # Шаг 1: Валидация структуры входного события
        if not validate_event_structure(normalized_event=normalized_event):
            logger.warning("Event structure validation failed, skipping...")
            return

        event_type = normalized_event.get("event_type")
        camera_id = normalized_event.get("camera_id")

        # Шаг 2: APPLY_EVENT_EFFECT -> context_transition_engine
        event_data_for_fsm_transition = []
        if event_type in (self._event_types["interaction"], self._event_types["contamination_event"]):
            if self._decision_table is not None:
                result, generated_sanitary_events = self._decision_table.process_event(
                    normalized_event=normalized_event,
                    camera_id=camera_id,
                )

                if result:
                    event_data_for_fsm_transition.append(
                        self.get_data_for_fsm_transition(
                            normalized_event=normalized_event,
                            sanitary_events=generated_sanitary_events
                        )
                    )

                try:
                    for sanitary_event in generated_sanitary_events:
                        self._input_queue.put(sanitary_event)
                except Exception as e:
                    logger.warning("Unable to process event due to %s", e)


        # Шаг 3: APPLY_RESET -> reset_logic
        reset_result_for_ttl = []
        if event_type == self._event_types["reset_event"]:
            if self._reset_logic is not None:
                result, reset_result_for_ttl = self._reset_logic.process_reset_event(normalized_event=normalized_event)
                if result:
                    event_data_for_fsm_transition.append(
                        self.get_data_for_fsm_transition(normalized_event=normalized_event)
                    )


        # Шаг 4: APPLY_TTL -> ttl_logic
        if event_type not in self._events_with_no_ttl:
            if self._ttl_logic is not None:
                is_reset = True if len(reset_result_for_ttl) > 0 else False

                generated_ttl_events = self._ttl_logic.process_ttl_event(
                    normalized_event=normalized_event,
                    reset_result_for_ttl=reset_result_for_ttl,
                    is_reset=is_reset
                )

                for ttl_event in generated_ttl_events:
                    event_data_for_fsm_transition.append(
                        self.get_data_for_fsm_transition(
                            normalized_event=ttl_event
                        )
                    )


        # Шаг 6: APPLY_FSM_TRANSITION -> fsm
        most_important_event = choose_event_by_priority(event_data_for_fsm_transition)
        self.choose_fsm_transition(event_data_for_fsm_transition=most_important_event)

    def stop(self) -> None:
        self.stop_event.set()

    def test_func(self):
        print(self._fsm_hub)
        print(self._reset_logic)
        print(self._ttl_logic)
        print(self._context_transfer_table)
        print(self._contamination_table)
        print(self._decision_table)


if __name__ == "__main__":
    input_queue = queue.Queue()
    event_pipeline = EventPipeline(input_queue=input_queue)

    from utils.test_data import *

    lst = []
    r = event_pipeline.get_data_for_fsm_transition(normalized_event=zone_exit)
    lst.append(r)
    r = event_pipeline.get_data_for_fsm_transition(normalized_event=contamination_event)
    lst.append(r)
    r = event_pipeline.get_data_for_fsm_transition(normalized_event=interaction)
    lst.append(r)
    r = event_pipeline.get_data_for_fsm_transition(normalized_event=process_event_food_handoff)
    lst.append(r)
    r = event_pipeline.get_data_for_fsm_transition(normalized_event=reset_event)
    lst.append(r)
    r = event_pipeline.get_data_for_fsm_transition(normalized_event=zone_enter)
    lst.append(r)

    for event in lst:
        print(event)

    a = choose_event_by_priority(event_data_for_fsm_transition=lst)
    print('\n', a)
    event_pipeline.choose_fsm_transition(event_data_for_fsm_transition=a)


import json
import queue
import threading
import logging
import time
import uuid
import enum

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("event_pipeline")

from temp_file import *

class EventTypes(enum.Enum):
    DETECTION = "DETECTION"
    TRACKING = "TRACKING"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    INTERACTION = "INTERACTION"
    ACTION_START = "ACTION_START"
    ACTION_END = "ACTION_END"
    RESET_EVENT = "RESET_EVENT"
    CONTAMINATION_EVENT = "CONTAMINATION_EVENT"
    OBS_EVENT = "OBS_EVENT"
    DATA_QUALITY_EVENT = "DATA_QUALITY_EVENT"
    SANITARY_EVENT = "SANITARY_EVENT"
    PROCESS_EVENT = "PROCESS_EVENT"


class ProcessEvents(enum.Enum):
    FOOD_HANDOFF = "food_handoff"
    ACTION_INTERRUPTION = "ACTION_INTERRUPTION"
    ZONE_RISK_SCORE = "ZONE_RISK_SCORE"


class ResetTypes(enum.Enum):
    hands_washed = "hands_washed"
    gloves_changed = "gloves_changed"
    gloves_removed = "gloves_removed"
    gloves_put_on = "gloves_put_on"
    tool_washed = "tool_washed"
    clean_tool_taken = "clean_tool_taken"
    tool_replaced = "tool_replaced"
    container_replaced_clean = "container_replaced_clean"
    disposable_container_used = "disposable_container_used"
    surface_sanitized = "surface_sanitized"
    entered_reset_zone = "entered_reset_zone"
    passed_clean_gate = "passed_clean_gate"
    shift_started = "shift_started"
    returned_to_kitchen = "returned_to_kitchen"


class ContaminationTypes(enum.Enum):
    raw_food = "raw_food"
    waste = "waste"
    dirty_surface = "dirty_surface"
    packaging = "packaging"
    nonfood_contact = "nonfood_contact"
    unknown = "unknown"


class EventPipeline(threading.Thread):
    def __init__(self, input_queue: queue.Queue):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self._input_queue = input_queue
        self._event_types = EventTypes
        self._process_events = ProcessEvents
        self._reset_events = ResetTypes
        self._contamination_events = ContaminationTypes

    def run(self):
        while not self.stop_event.is_set():
            try:
                pkt = self._input_queue.get()
            except queue.Empty:
                continue

    def normalise_event(self, event, event_type, camera_id):
        n_event_id = str(uuid.uuid4())
        n_timestamp = time.time()
        n_event_type = event_type
        n_camera_id = camera_id
        n_confidence = 0.5
        n_payload = event
        n_time_source = "null"
        normalised_event = {
            "event_id": n_event_id,
            "timestamp": n_timestamp,
            "event_type": n_event_type.name,
            "camera_id": n_camera_id,
            "confidence": n_confidence,
            "payload": n_payload,
            "time_source": n_time_source,
        }

        if event_type == self._event_types.DETECTION:
            try:
                normalised_event["entity_id"] = event["track_id"]
                normalised_event["entity_type"] = event["object_class"]
            except KeyError:
                logger.warning("KeyError in normalise_event")
                return None

        if event_type == self._event_types.TRACKING:
            try:
                normalised_event["entity_id"] = event["track_id"]
                normalised_event["entity_type"] = "system"
            except KeyError:
                logger.warning("KeyError in normalise_event")
                return None

        if event_type == self._event_types.ZONE_ENTER:
            try:
                normalised_event["entity_id"] = event["track_id"]
                normalised_event["entity_type"] = "system"
            except KeyError:
                logger.warning("KeyError in normalise_event")
                return None

        if event_type == self._event_types.ZONE_EXIT:
            try:
                normalised_event["entity_id"] = event["track_id"]
                normalised_event["entity_type"] = "system"
            except KeyError:
                logger.warning("KeyError in normalise_event")
                return None

        if event_type == self._event_types.INTERACTION:
            try:
                if event["target_entity_type"] in ['employee', 'hands', 'tool', 'container', 'surface', 'food']:
                    normalised_event["entity_id"] = event["target_entity_id"]
                    normalised_event["entity_type"] = event["target_entity_type"]
                else:
                    return None
            except KeyError:
                logger.warning("KeyError in normalise_event")
                return None

        if event_type == self._event_types.ACTION_START:
            try:
                normalised_event["entity_id"] = event["action_id"]
                normalised_event["entity_type"] = "action"
            except KeyError:
                logger.warning("KeyError in normalise_event")
                return None

        if event_type == self._event_types.ACTION_END:
            try:
                normalised_event["entity_id"] = event["action_id"]
                normalised_event["entity_type"] = "action"
            except KeyError:
                logger.warning("KeyError in normalise_event")
                return None

        if event_type == self._event_types.RESET_EVENT:
            try:
                normalised_event["entity_id"] = event["zone_id"]
                reset_type = event["reset_type"]
                if reset_type == self._reset_events.hands_washed:
                    normalised_event["entity_type"] = "hands"
                elif reset_type == self._reset_events.gloves_changed:
                    normalised_event["entity_type"] = "hands"
                elif reset_type == self._reset_events.gloves_removed:
                    normalised_event["entity_type"] = "hands"
                elif reset_type == self._reset_events.gloves_put_on:
                    normalised_event["entity_type"] = "hands"
                elif reset_type == self._reset_events.tool_washed:
                    normalised_event["entity_type"] = "tool"
                elif reset_type == self._reset_events.clean_tool_taken:
                    normalised_event["entity_type"] = "tool"
                elif reset_type == self._reset_events.tool_replaced:
                    normalised_event["entity_type"] = "tool"
                elif reset_type == self._reset_events.container_replaced_clean:
                    normalised_event["entity_type"] = "container"
                elif reset_type == self._reset_events.disposable_container_used:
                    normalised_event["entity_type"] = "container"
                elif reset_type == self._reset_events.surface_sanitized:
                    normalised_event["entity_type"] = "surface"
                elif reset_type == self._reset_events.entered_reset_zone:
                    normalised_event["entity_type"] = "employee"
                elif reset_type == self._reset_events.passed_clean_gate:
                    normalised_event["entity_type"] = "employee"
                elif reset_type == self._reset_events.shift_started:
                    normalised_event["entity_type"] = "employee"
                elif reset_type == self._reset_events.returned_to_kitchen:
                    normalised_event["entity_type"] = "employee"
            except KeyError:
                logger.warning("KeyError in normalise_event")
                return None

        if event_type == self._event_types.CONTAMINATION_EVENT:
            try:
                normalised_event["entity_id"] = event["track_id"]
                contamination_type = event["contamination_type"]
                if contamination_type == self._contamination_events.nonfood_contact and event["target_entity"] == "employee":
                    normalised_event["entity_type"] = "employee"
                elif contamination_type == self._contamination_events.nonfood_contact and event["target_entity"] == "hands":
                    normalised_event["entity_type"] = "hands"
                elif contamination_type == self._contamination_events.raw_food and event["target_entity"] == "hands":
                    normalised_event["entity_type"] = "hands"
                elif contamination_type == self._contamination_events.raw_food or contamination_type == self._contamination_events.waste \
                        or contamination_type == self._contamination_events.dirty_surface and event["target_entity"] == "tool":
                    normalised_event["entity_type"] = "tool"
                elif contamination_type == self._contamination_events.raw_food or contamination_type == self._contamination_events.waste \
                        or contamination_type == self._contamination_events.dirty_surface and event["target_entity"] == "container":
                    normalised_event["entity_type"] = "container"
                elif contamination_type == self._contamination_events.raw_food or contamination_type == self._contamination_events.waste \
                        or contamination_type == self._contamination_events.dirty_surface and event["target_entity"] == "surface":
                    normalised_event["entity_type"] = "surface"
            except KeyError:
                logger.warning("KeyError in normalise_event")
                return None

        if event_type == self._event_types.OBS_EVENT:
            try:
                normalised_event["entity_id"] = camera_id
                normalised_event["entity_type"] = "system"
            except KeyError:
                logger.warning("KeyError in normalise_event")
                return None

        if event_type == self._event_types.DATA_QUALITY_EVENT:
            try:
                normalised_event["entity_id"] = event["data_quality_event_id"]
                normalised_event["entity_type"] = "system"
            except KeyError:
                logger.warning("KeyError in normalise_event")
                return None

        if event_type == self._event_types.SANITARY_EVENT:
            try:
                normalised_event["entity_id"] = event["sanitary_event_id"]
                normalised_event["entity_type"] = "employee"
            except KeyError:
                logger.warning("KeyError in normalise_event")
                return None

        if event_type == self._event_types.PROCESS_EVENT:
            try:
                normalised_event["entity_id"] = event["process_event_id"]
                process_type = event["process_event_type"]
                if process_type == self._process_events.FOOD_HANDOFF:
                    normalised_event["entity_type"] = "employee"
                elif process_type == self._process_events.ACTION_INTERRUPTION:
                    normalised_event["entity_type"] = "action"
                elif process_type == self._process_events.ZONE_RISK_SCORE:
                    normalised_event["entity_type"] = "zone"
            except KeyError:
                logger.warning("KeyError in normalise_event")
                return None

        return normalised_event

    def process_event(self, event_type, camera_id, pkt):
        # Применяем пункт 1. -> normalise
        normalised_event = self.normalise_event(event=pkt, event_type=event_type, camera_id=camera_id)
        if normalised_event is None:
            return
        print(json.dumps(normalised_event, indent=4))

        #Применяем пункт 2. -> apply_event_effects
        if event_type == self._event_types.INTERACTION:
            print("Apply event effects -> context_transition_engine")
        elif event_type == self._event_types.CONTAMINATION_EVENT:
            print("Apply event effects -> context_transition_engine")

        # Применяем пункт 3. -> apply_reset_logic
        if event_type == self._event_types.RESET_EVENT:
            print("Apply reset logic -> reset_logic")

        # Применяем пункт 4. -> apply_ttl_logic
        if event_type != self._event_types.OBS_EVENT and event_type != self._event_types.DATA_QUALITY_EVENT:
            print("Apply TTL logic -> ttl_logic")

    def stop(self):
        self.stop_event.set()


if __name__ == "__main__":
    event_pipeline = EventPipeline(queue.Queue())
    event_pipeline.process_event(
        event_type=EventTypes.DATA_QUALITY_EVENT,
        camera_id="Kitchen_1",
        pkt=DATA_QUALITY_EVENT["payload"]
    )
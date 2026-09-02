import queue
import logging
from typing import Optional

from kitchen_vision.action_detector import ActionDetector
from kitchen_vision.pattern_analyzer import MotionPatternAnalyzer
from kitchen_vision.sanitary_checker.observation_logic.observation_logic import ObservationLogic
from kitchen_vision.sanitary_checker.uniform_behavior_rules import UniformBehaviorRules
from kitchen_vision.sanitary_checker.intrusion_control import IntrusionControl

from event_pipeline import EventPipeline

from kitchen_gateway.action_card_cache import ActionCardCache

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("facts_consumer")


class ZoneResolver:
    def __init__(self, zone_config: dict):
        self.zones = zone_config.get("zones", [])
        self.calibration = zone_config.get("calibration", {})

    def get_zone(self, zone_id: str) -> Optional[dict]:
        for z in self.zones:
            if z.get("zone_id") == zone_id:
                return z
        return None


class FactsConsumer:
    def __init__(self, cams = None):
        self._cameras = cams or [{"camera_id": "Kitchen_1", "source": 0}]
        self._zone_resolver = ZoneResolver()

        # Единая очередь для выходных событий
        self._out_action_queue = queue.Queue(maxsize=64)

        # OBS logic
        self._input_obs_logic_queue = queue.Queue(maxsize=64)
        self._obs_logic = ObservationLogic(
            input_queue=self._input_obs_logic_queue,
            out_queue=self._out_action_queue,
        )
        self._obs_logic.daemon = True
        self._obs_logic.start()

        # Action detector
        self._input_action_detector_queue = queue.Queue(maxsize=64)
        self._analyzer = MotionPatternAnalyzer()
        self._action_card_cache = ActionCardCache()
        self._action_detector = ActionDetector(
            cams=[cam["camera_id"] for cam in self._cameras],
            in_queue=self._input_action_detector_queue,
            out_queue=self._out_action_queue,
            analyser=self._analyzer,
            action_card_cache= self._action_card_cache,
        )
        self._action_detector.daemon = True
        self._action_detector.start()

        # Uniform behavior rules
        self._input_uniform_behavior_rules_queue = queue.Queue(maxsize=64)
        self._uniform_behavior_rules = UniformBehaviorRules(
            input_queue=self._input_uniform_behavior_rules_queue,
            out_queue=self._out_action_queue,
        )
        self._uniform_behavior_rules.daemon = True
        self._uniform_behavior_rules.start()

        # Intrusion control
        self._input_intrusion_control_queue = queue.Queue(maxsize=64)
        self._intrusion_control = IntrusionControl(
            input_queue=self._input_intrusion_control_queue,
            out_queue=self._out_action_queue,
            intrusion_zone_resolver=self._zone_resolver,
            observation_fsm=None
        )
        self._intrusion_control.daemon = True
        self._intrusion_control.start()

        self._event_pipeline = EventPipeline(input_queue=self._out_action_queue)
        self._event_pipeline.daemon = True
        self._event_pipeline.start()

        logger.info("Facts consumer initialized")

    def put_facts(self, facts):
        if not facts is None:
            try:
                self._input_obs_logic_queue.put(facts)
                self._input_action_detector_queue.put(facts)
                self._input_uniform_behavior_rules_queue.put(facts)
                self._input_intrusion_control_queue.put(facts)
            except queue.Full:
                logger.error("Facts queue is full")


if __name__ == "__main__":
    consumer = FactsConsumer()
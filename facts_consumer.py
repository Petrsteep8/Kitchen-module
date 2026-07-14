import queue
import logging

from kitchen_vision.action_detector import ActionDetector
from kitchen_vision.pattern_analyzer import MotionPatternAnalyzer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("facts_consumer")

class FactsConsumer:
    def __init__(self, cams = None):
        self._cameras = cams or [{"camera_id": "Kitchen_1", "source": 0}]
        self._input_queue = queue.Queue(maxsize=64)
        self._output_queue = queue.Queue(maxsize=64)
        self._analyzer = MotionPatternAnalyzer()
        self._action_detector = ActionDetector(
            cams=[cam["camera_id"] for cam in self._cameras],
            in_queue=self._input_queue,
            out_queue=self._output_queue,
            analyser=self._analyzer
        )
        self._action_detector.daemon = True
        self._action_detector.start()
        logger.info("FactsConsumer initialized")

    def put_facts(self, facts):
        if not facts is None:
            try:
                self._input_queue.put(facts)
            except queue.Full:
                logger.error("Facts queue is full")


if __name__ == "__main__":
    consumer = FactsConsumer()
import json
import queue
import threading
import logging

from kitchen_vision.sanitary_checker.observation_logic.prometheus_metrics import PrometheusMetrics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("observation_logic")

class ObservationLogic(threading.Thread):
    def __init__(self, input_queue: queue.Queue):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()

        self._prometheus_metrics = PrometheusMetrics()
        self._camera_state: dict = {}
        self._input_queue = input_queue
        logger.info("ObservationLogic initialized")

    def run(self):
        while not self.stop_event.is_set():
            try:
                pkt = self._input_queue.get()
                print(pkt["camera_id"])
                print(json.dumps(pkt["facts"], indent=4))
                print(json.dumps(pkt["identities"], indent=4))
            except queue.Empty:
                continue

    def stop(self):
        self.stop_event.set()

    def get_obs_state_for_camera(self, camera: str):
        self._prometheus_metrics.get_metrics_for_camera(camera=camera)

    def get_state_for_camera(self, camera: str):
        if camera not in self._camera_state:
            return None
        return self._camera_state.get(camera)
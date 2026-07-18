import queue
import threading
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("event_pipeline")

class EventPipeline(threading.Thread):
    def __init__(self, input_queue: queue.Queue):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self._input_queue = input_queue

    def run(self):
        while not self.stop_event.is_set():
            try:
                pkt = self._input_queue.get()
            except queue.Empty:
                continue

    def stop(self):
        self.stop_event.set()
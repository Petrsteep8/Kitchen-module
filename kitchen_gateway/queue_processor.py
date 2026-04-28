import threading
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("queue_processor_integrated")

class QueueProcessor:
    def __init__(self, dbi_client, interval_seconds: int = 60):
        self.dbi_client = dbi_client
        self.interval = interval_seconds
        self.running = False
        self.thread = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._process_loop, daemon=True)
        self.thread.start()
        logger.info("Queue processor started")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()
        logger.info("Queue processor stopped")

    def _process_loop(self):
        while self.running:
            try:
                self.dbi_client.process_queue()
            except Exception as e:
                logger.error(f"Error in queue processor: {e}")
            time.sleep(self.interval)
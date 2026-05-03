import cv2
import json
import logging
import queue
import threading
import time
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

from typing import Any, Dict, List, Optional
from collections import defaultdict, deque

# НАШИ МОДУЛИ
from kitchen_vision.video_ingestion import CameraWorker
from kitchen_vision.face.arcface import ArcFaceRecognizer
from kitchen_vision.face.face_db import FaceDatabase
from kitchen_vision.ai_perception import PerceptionWorker
from kitchen_vision.sanitary_checker import SanitaryChecker
from kitchen_vision.action_detector import ActionDetector
from kitchen_vision.event_aggregator import EventAggregator
from kitchen_environment.snapshot_collector.collector import SnapshotCollector
from kitchen_gateway.dbi_client import DBIClient
from kitchen_gateway.queue_processor import QueueProcessor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("run_multi_camera_integrated")


# ============================================================
# НАСТРОЙКИ
# ============================================================
CAMERAS = [
    {
        "camera_id": "Kitchen_1",
        "source": 0,
    },
]
TARGET_FPS = 3
RESOLUTION = (640, 480)

# Формат зависит от ActionDetector. Если он ожидает polygon по camera_id,
# оставь словарь такого вида.
WORK_ZONE_POLYGONS: Dict[str, List[List[float]]] = {
    "Kitchen_1": [],
}

class MotionPatternDebugTracker:
    def __init__(self, analyser, max_len: int = 20):
        self.analyser = analyser
        self.previous_position = defaultdict(dict)
        self.movement_vectors = defaultdict(lambda: defaultdict(lambda: deque(maxlen=max_len)))
        self.last_seen_ids = defaultdict(dict)

    def parse_bbox(self, bbox):
        try:
            if bbox is None:
                return None
            if isinstance(bbox, str):
                import json
                bbox = json.loads(bbox)
            vals = [float(x) for x in bbox]
            if len(vals) != 4:
                return None
            x1, y1, x2, y2 = vals
            if x2 <= x1 or y2 <= y1:
                return None
            return vals
        except Exception:
            return None

    def _bbox_center(self, bbox):
        x1, y1, x2, y2 = bbox
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0

    def update(self, packet: Dict[str, Any]) -> Dict[str, str]:
        camera_id = packet.get("camera_id")
        if not camera_id:
            return {}

        current_time = time.time()
        objects = packet.get("objects", [])
        current_ids = set()

        for obj in objects:
            bbox = self.parse_bbox(obj.get("bbox"))
            if not bbox:
                continue

            track_id = obj.get("id")
            cls = obj.get("class")

            if track_id is None:
                track_id = f"{cls}_fallback_{int(bbox[0])}_{int(bbox[1])}_{int(bbox[2])}_{int(bbox[3])}"

            uid = f"{cls}_{track_id}"
            current_ids.add(uid)

            cx, cy = self._bbox_center(bbox)

            if uid in self.previous_position[camera_id]:
                px, py, pt, _ = self.previous_position[camera_id][uid]
                dt = current_time - pt
                if dt > 0:
                    vx = (cx - px) / dt
                    vy = (cy - py) / dt
                    self.movement_vectors[camera_id][uid].append((cx, cy, vx, vy))

            self.previous_position[camera_id][uid] = (cx, cy, current_time, bbox)
            self.last_seen_ids[camera_id][uid] = current_time

        for uid in list(self.previous_position[camera_id].keys()):
            if uid not in current_ids:
                last_ts = self.last_seen_ids[camera_id].get(uid, 0.0)
                if current_time - last_ts > 1.5:
                    self.previous_position[camera_id].pop(uid, None)
                    self.movement_vectors[camera_id].pop(uid, None)
                    self.last_seen_ids[camera_id].pop(uid, None)

        patterns = self.analyser.analyze_motion_patterns(
            dict(self.movement_vectors[camera_id])
        )
        return patterns or {}


# ============================================================
# ROUTER: perception -> sanitary / action / display
# ============================================================
class PerceptionRouter(threading.Thread):
    def __init__(
        self,
        in_queue: queue.Queue,
        action_in_queue: queue.Queue,
        display_queue: queue.Queue,
        event_aggregator: EventAggregator,
        sanitary_checker: SanitaryChecker,
        work_zone_polygons: Dict[str, List[List[float]]],
        motion_tracker: MotionPatternDebugTracker,
        snapshot_collector: Optional[SnapshotCollector] = None,
    ):
        super().__init__(daemon=True)
        self.in_queue = in_queue
        self.action_in_queue = action_in_queue
        self.display_queue = display_queue
        self.event_aggregator = event_aggregator
        self.sanitary_checker = sanitary_checker
        self.work_zone_polygons = work_zone_polygons
        self.motion_tracker = motion_tracker
        self.snapshot_collector = snapshot_collector
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        while not self.stop_event.is_set():
            try:
                pkt = self.in_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            try:
                enriched = dict(pkt)

                motion_patterns = self.motion_tracker.update(enriched)
                enriched["motion_patterns"] = motion_patterns

                for obj in enriched.get("objects", []):
                    try:
                        bbox = self.motion_tracker.parse_bbox(obj.get("bbox"))
                        if not bbox:
                            obj["motion_pattern"] = None
                            continue

                        track_id = obj.get("id")
                        cls = obj.get("class")

                        if track_id is None:
                            track_id = f"{cls}_fallback_{int(bbox[0])}_{int(bbox[1])}_{int(bbox[2])}_{int(bbox[3])}"

                        uid = f"{cls}_{track_id}"
                        obj["motion_pattern"] = motion_patterns.get(uid)
                    except Exception:
                        obj["motion_pattern"] = None

                camera_id = enriched.get("camera_id")
                if camera_id:
                    enriched["work_zone_polygon"] = self.work_zone_polygons.get(camera_id)

                enriched = self.sanitary_checker.process_packet(enriched)

                print(json.dumps({
                    "camera_id": enriched.get("camera_id"),
                    "timestamp": enriched.get("timestamp"),
                    "objects": enriched.get("objects", []),
                    "motion_patterns": enriched.get("motion_patterns", {}),
                    "sanitary_report": enriched.get("sanitary_report", []),
                    "sanitary_events": enriched.get("sanitary_events", []),
                }, ensure_ascii=False, indent=2, default=str))

                produced = self.event_aggregator.process(enriched)
                if produced:
                    logger.debug(
                        "Aggregator produced from perception packet: %s",
                        produced
                    )

                self._safe_put(self.action_in_queue, enriched, name="action_in_queue")
                self._safe_put(self.display_queue, enriched, name="display_queue")

                if self.snapshot_collector is not None:
                    frame = enriched.get("frame_raw")
                    if frame is None:
                        frame = enriched.get("frame")

                    if frame is not None and camera_id:
                        self.snapshot_collector.register_frame(camera_id, frame)

            except Exception:
                logger.exception("PerceptionRouter processing error")

    @staticmethod
    def _safe_put(q: queue.Queue, item: Dict[str, Any], name: str) -> None:
        try:
            q.put_nowait(item)
        except Exception:
            try:
                q.put(item, timeout=0.1)
            except Exception:
                logger.warning("Failed to push packet to %s", name)


# ============================================================
# FORWARDER: action events -> aggregator
# ============================================================
class ActionEventForwarder(threading.Thread):
    def __init__(
        self,
        in_queue: queue.Queue,
        event_aggregator: EventAggregator,
        sanitary_checker: SanitaryChecker,
        work_zone_polygons: Dict[str, List[List[float]]],
    ):
        super().__init__(daemon=True)
        self.in_queue = in_queue
        self.event_aggregator = event_aggregator
        self.sanitary_checker = sanitary_checker
        self.work_zone_polygons = work_zone_polygons
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        while not self.stop_event.is_set():
            try:
                event = self.in_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            try:
                enriched_event = dict(event)

                camera_id = enriched_event.get("camera_id")
                if camera_id:
                    enriched_event["work_zone_polygon"] = self.work_zone_polygons.get(camera_id)

                enriched_event = self.sanitary_checker.process_packet(enriched_event)

                produced = self.event_aggregator.process(enriched_event)
                if produced:
                    logger.debug(
                        "Aggregator produced from action event: %s",
                        produced
                    )

            except Exception:
                logger.exception("ActionEventForwarder processing error")


# ============================================================
# HELPERS
# ============================================================
def draw_objects(frame, objects: List[Dict[str, Any]]) -> None:
    for obj in objects:
        try:
            bbox = obj.get("bbox")
            if not bbox or len(bbox) != 4:
                continue

            x1, y1, x2, y2 = [int(v) for v in bbox]
            cls_name = str(obj.get("class", "unknown"))
            conf = float(obj.get("confidence", 0.0))

            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                frame,
                f"{cls_name} {conf:.2f}",
                (x1, max(20, y1 - 5)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )
        except Exception:
            logger.exception("Failed to draw object: %s", obj)


def draw_sanitary_report(frame, report: List[Dict[str, Any]]) -> None:
    y = 30
    for r in report:
        try:
            violations = r.get("violations", {})
            active = [k for k, v in violations.items() if v]
            if not active:
                continue

            text = f"VIOLATION person={r.get('person_id')} -> {', '.join(active)}"
            cv2.putText(
                frame,
                text,
                (20, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 0, 255),
                2,
            )
            y += 30
        except Exception:
            logger.exception("Failed to draw sanitary report entry: %s", r)


def stop_thread_safe(obj: Any, name: str) -> None:
    if obj is None:
        return
    try:
        obj.stop()
    except Exception:
        logger.exception("Failed to stop %s", name)


def join_thread_safe(obj: Any, name: str, timeout: float = 2.0) -> None:
    if obj is None:
        return
    try:
        obj.join(timeout=timeout)
    except Exception:
        logger.exception("Failed to join %s", name)


# ============================================================
# MAIN
# ============================================================
def main():
    frame_queue: queue.Queue = queue.Queue(maxsize=32)
    perception_queue: queue.Queue = queue.Queue(maxsize=32)
    action_in_queue: queue.Queue = queue.Queue(maxsize=64)
    action_event_queue: queue.Queue = queue.Queue(maxsize=64)
    display_queue: queue.Queue = queue.Queue(maxsize=32)

    workers: List[Any] = []
    router: Optional[PerceptionRouter] = None
    action_forwarder: Optional[ActionEventForwarder] = None
    perception: Optional[PerceptionWorker] = None
    action_detector: Optional[ActionDetector] = None
    queue_processor: Optional[QueueProcessor] = None
    snapshot_collector: Optional[SnapshotCollector] = None

    try:
        # Прямое создание analyser без динамического loader
        # pattern_analyser = MotionPatternAnalyzer(min_samples=5)
        # motion_tracker = MotionPatternDebugTracker(pattern_analyser)
        # logger.info("Pattern analyser loaded: %s", type(pattern_analyser).__name__)
        # logger.info("Pattern analyser loaded: %s", type(pattern_analyser).__name__)

        dbi_client = DBIClient(base_url='https://127.0.0.1/api/db_ingest/', secret_key=b'test_key')
        logger.info("DBIClient created")

        arcface = ArcFaceRecognizer()
        logger.info("ArcFaceRecognizer created")

        face_database = FaceDatabase('./face/faces_db.npz', threshold=0.45)
        logger.info("FaceDatabase created")

        event_aggregator = EventAggregator()
        logger.info("EventAggregator created")

        sanitary_checker = SanitaryChecker()
        logger.info("SanitaryChecker created")

        time.sleep(1.0)
        # 0) QueueProcessor worker
        queue_processor = QueueProcessor(dbi_client=dbi_client, interval_seconds=60)
        queue_processor.start()
        logger.info("QueueProcessor started")

        # 1) SnapshotCollector worker
        snapshot_collector = SnapshotCollector(interval_sec=60)
        snapshot_collector.start()
        logger.info("SnapshotCollector started")

        # 2) Camera workers
        for cam in CAMERAS:
            w = CameraWorker(
                camera_id=cam["camera_id"],
                source=cam["source"],
                out_queue=frame_queue,
                target_fps=TARGET_FPS,
                target_resolution=RESOLUTION,
                brightness_alpha=1.0,
                brightness_beta=0.0,
            )
            w.daemon = True
            w.start()
            workers.append(w)
            logger.info("Started video ingestion for %s", cam["camera_id"])
            time.sleep(1.0)

        # 3) Perception worker
        perception = PerceptionWorker(in_queue=frame_queue, out_queue=perception_queue, arc_face_recognizer=arcface,
                                      face_database=face_database)
        perception.daemon = True
        perception.start()
        logger.info("Started AI perception module")

        # 4) Action detector
        action_detector = ActionDetector(
            cams=[cam["camera_id"] for cam in CAMERAS],
            in_queue=action_in_queue,
            out_queue=action_event_queue,
            analyser=None, # Пока убрал, т.к. его не было в проекте
            work_zone_polygons=WORK_ZONE_POLYGONS,
        )
        action_detector.daemon = True
        action_detector.start()
        logger.info("Started ActionDetector")

        # # 5) Router perception -> sanitary + aggregator + action + display
        # router = PerceptionRouter(
        #     in_queue=perception_queue,
        #     action_in_queue=action_in_queue,
        #     display_queue=display_queue,
        #     event_aggregator=event_aggregator,
        #     sanitary_checker=sanitary_checker,
        #     work_zone_polygons=WORK_ZONE_POLYGONS,
        #     motion_tracker=None,
        #     snapshot_collector=snapshot_collector,
        # )
        # router.start()
        # logger.info("Started PerceptionRouter")
        #
        # # 6) Forward action events -> aggregator
        # action_forwarder = ActionEventForwarder(
        #     in_queue=action_event_queue,
        #     event_aggregator=event_aggregator,
        #     sanitary_checker=sanitary_checker,
        #     work_zone_polygons=WORK_ZONE_POLYGONS,
        # )
        # action_forwarder.start()
        # logger.info("Started ActionEventForwarder")

        # ====================================================
        # UI LOOP
        # ====================================================
        last_alive_check = time.time()

        while True:
            try:
                out = perception_queue.get(timeout=0.5) # display_queue
            except queue.Empty:
                if time.time() - last_alive_check > 5:
                    alive_cams = [w.is_alive() for w in workers]
                    alive_perception = perception.is_alive() if perception else False
                    alive_action_detector = action_detector.is_alive() if action_detector else False
                    # alive_router = router.is_alive() if router else False
                    # alive_forwarder = action_forwarder.is_alive() if action_forwarder else False

                    logger.info(
                        "[HEALTH] Cameras=%s Perception=%s ActionDetector=%s",
                        alive_cams,
                        alive_perception,
                        alive_action_detector,
                        # alive_router,
                        # alive_forwarder,
                    )
                    last_alive_check = time.time()
                continue

            camera_id = out.get("camera_id", "Unknown")
            objects = out.get("objects", [])

            frame = out.get("frame_raw")
            if frame is None:
                frame = out.get("frame")

            if frame is None:
                continue

            draw_objects(frame, objects)
            draw_sanitary_report(frame, out.get("sanitary_report", []))

            cv2.imshow(f"Camera - {camera_id}", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                logger.info("Stopping due to 'q' pressed")
                break

    except KeyboardInterrupt:
        logger.info("Stopping due to Ctrl+C")

    except Exception:
        logger.exception("Fatal error in main loop")
        raise

    finally:
        logger.info("Stopping all workers...")

        # stop_thread_safe(router, "router")
        # stop_thread_safe(action_forwarder, "action_forwarder")
        stop_thread_safe(action_detector, "action_detector")
        stop_thread_safe(perception, "perception")
        stop_thread_safe(snapshot_collector, "snapshot_collector")
        stop_thread_safe(queue_processor, "queue_processor")

        for idx, w in enumerate(workers):
            stop_thread_safe(w, f"CameraWorker[{idx}]")

        for idx, w in enumerate(workers):
            join_thread_safe(w, f"CameraWorker[{idx}]")

        # join_thread_safe(router, "router")
        # join_thread_safe(action_forwarder, "action_forwarder")
        join_thread_safe(action_detector, "action_detector")
        join_thread_safe(perception, "perception")

        cv2.destroyAllWindows()
        logger.info("All stopped cleanly.")


if __name__ == "__main__":
    main()

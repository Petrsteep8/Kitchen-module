import json
import os
import queue
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any

import cv2

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uniform_behavior_rules")

SEVERITY_MAP = {
    "no_cap": ["warning", "warning"],
    "no_gloves": ["warning", "warning"],
    "no_apron": ["warning", "warning"],
    "face_touch": ["warning", "medium"],
    "phone_usage": ["warning", "medium"],
}

config = {
    "T_uniform_confirm_sec": 1.0,
    "employee_confidence_floor": 0.9,
    "employee_id_confidence_floor": 0.6,
}


# ============================================================================
# Ring buffer кадров + генерация видео-клипов
# ============================================================================

@dataclass
class BufferedFrame:
    """Кадр с временной меткой."""
    timestamp: float
    frame: Any              # numpy.ndarray
    camera_id: str


class FrameRingBuffer:
    """
    Потокобезопасный ring buffer кадров по камерам.
    Хранит последние max_seconds секунд (и не более max_frames кадров).
    """

    def __init__(self, max_seconds: float = 5.0, max_frames: int = 500):
        self._buffers: Dict[str, deque] = {}
        self._max_seconds = max_seconds
        self._max_frames = max_frames
        self._lock = threading.Lock()

    def push(self, camera_id: str, timestamp: float, frame) -> None:
        if frame is None:
            return
        with self._lock:
            if camera_id not in self._buffers:
                self._buffers[camera_id] = deque(maxlen=self._max_frames)
            buf = self._buffers[camera_id]
            buf.append(BufferedFrame(timestamp, frame, camera_id))
            # вытесняем кадры старше окна
            cutoff = timestamp - self._max_seconds
            while buf and buf[0].timestamp < cutoff:
                buf.popleft()

    def snapshot(self, camera_id: str, start_ts: float, end_ts: float) -> List[BufferedFrame]:
        """Снимок кадров из буфера в диапазоне [start_ts, end_ts]."""
        with self._lock:
            buf = self._buffers.get(camera_id)
            if not buf:
                return []
            return [f for f in buf if start_ts <= f.timestamp <= end_ts]

    def clear(self) -> None:
        with self._lock:
            self._buffers.clear()


@dataclass
class PendingClip:
    """Запрос на клип, ожидающий накопления пост-ролл кадров."""
    camera_id: str
    event_type: str
    violation_id: str
    trigger_ts: float
    post_roll_seconds: float
    pre_roll_frames: List[BufferedFrame] = field(default_factory=list)
    post_roll_frames: List[BufferedFrame] = field(default_factory=list)

    def is_post_roll_complete(self, current_ts: float) -> bool:
        return current_ts >= (self.trigger_ts + self.post_roll_seconds)


class ClipRecorder:
    """
    Управляет генерацией клипов:
      1) при триггере снимает пре-ролл из ring buffer,
      2) собирает пост-ролл кадры по мере поступления,
      3) по завершении окна записывает .mp4.
    """

    def __init__(
        self,
        frame_buffer: FrameRingBuffer,
        output_dir: str = "./audit_clips",
        fps: float = 8.0,
        pre_roll_seconds: float = 5.0,
        post_roll_seconds: float = 5.0,
    ):
        self._buffer = frame_buffer
        self._output_dir = output_dir
        self._fps = fps
        self._pre_roll_seconds = pre_roll_seconds
        self._post_roll_seconds = post_roll_seconds
        self._pending: List[PendingClip] = []
        self._lock = threading.Lock()
        os.makedirs(self._output_dir, exist_ok=True)

    def schedule_clip(
        self,
        camera_id: str,
        event_type: str,
        trigger_ts: float,
        extra_id: Optional[str] = None,
    ) -> str:
        """
        Регистрирует запрос на клип. Снимок пре-ролл берётся из ring buffer.
        Возвращает violation_id, который также используется в имени файла.
        """
        violation_id = extra_id or f"{camera_id}_{event_type}_{int(trigger_ts * 1000)}"

        with self._lock:
            start_ts = trigger_ts - self._pre_roll_seconds
            pre_frames = self._buffer.snapshot(camera_id, start_ts, trigger_ts)
            pending = PendingClip(
                camera_id=camera_id,
                event_type=event_type,
                violation_id=violation_id,
                trigger_ts=trigger_ts,
                post_roll_seconds=self._post_roll_seconds,
                pre_roll_frames=pre_frames,
            )
            self._pending.append(pending)

        logger.info(
            "Clip scheduled: id=%s camera=%s event=%s pre_roll_frames=%d",
            violation_id, camera_id, event_type, len(pre_frames)
        )
        return violation_id

    def update(self, camera_id: str, timestamp: float, frame) -> None:
        """
        Вызывается на каждом кадре. Добавляет кадр в пост-ролл всех активных
        запросов для данной камеры. По завершении окна записывает клип.
        """
        if frame is None:
            return

        completed: List[PendingClip] = []
        with self._lock:
            for pending in self._pending:
                if pending.camera_id != camera_id:
                    continue
                if timestamp < pending.trigger_ts:
                    continue
                if timestamp <= pending.trigger_ts + pending.post_roll_seconds:
                    pending.post_roll_frames.append(
                        BufferedFrame(timestamp, frame, camera_id)
                    )
                if pending.is_post_roll_complete(timestamp):
                    completed.append(pending)
            for p in completed:
                self._pending.remove(p)

        # запись вне блокировки
        for pending in completed:
            try:
                self._write_clip(pending)
            except Exception as e:
                logger.error("Failed to write clip %s: %s", pending.violation_id, e)

    def _write_clip(self, pending: PendingClip) -> Optional[str]:
        all_frames = pending.pre_roll_frames + pending.post_roll_frames
        if not all_frames:
            logger.warning("No frames to write for clip %s", pending.violation_id)
            return None

        first = next((f.frame for f in all_frames if f.frame is not None), None)
        if first is None:
            return None
        h, w = first.shape[:2]

        filepath = os.path.join(self._output_dir, f"{pending.violation_id}.mp4")
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(filepath, fourcc, self._fps, (w, h))

        try:
            for bf in all_frames:
                if bf.frame is None:
                    continue
                f = bf.frame
                if f.shape[1] != w or f.shape[0] != h:
                    f = cv2.resize(f, (w, h))
                writer.write(f)
        finally:
            writer.release()

        logger.info(
            "Clip saved: %s (%d frames, ~%.1fs)",
            filepath, len(all_frames), len(all_frames) / self._fps
        )
        return filepath

    def flush(self) -> None:
        """Записывает оставшиеся клипы (например, при остановке модуля)."""
        with self._lock:
            pending = list(self._pending)
            self._pending.clear()
        for p in pending:
            try:
                self._write_clip(p)
            except Exception as e:
                logger.error("Failed to flush clip %s: %s", p.violation_id, e)


# ============================================================================
# UniformBehaviorRules
# ============================================================================

class UniformBehaviorRules(threading.Thread):
    def __init__(
        self,
        input_queue: queue.Queue,
        out_queue: queue.Queue,
        intrusion_config: Dict,
        clip_config: Optional[Dict] = None,
    ):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()

        self.input_queue = input_queue
        self.out_queue = out_queue
        self._config: Dict = intrusion_config

        self._T_uniform_confirm_sec = self._config.get("T_uniform_confirm_sec", 1.0)
        self._employee_confidence_floor = self._config.get("employee_confidence_floor", 0.9)
        self._employee_id_confidence_floor = self._config.get("employee_id_confidence_floor", 0.6)

        self._is_no_cap_detected: dict = {}
        self._no_cap_detected_timestamp: dict = {}
        self._is_no_glove_detected: dict = {}
        self._no_glove_detected_timestamp: dict = {}
        self._is_no_apron_detected: dict = {}
        self._no_apron_detected_timestamp: dict = {}
        self._is_phone_usage_detected: dict = {}
        self._phone_usage_timestamp: dict = {}
        self._is_face_touch_detected: dict = {}
        self._face_touch_timestamp: dict = {}

        self._known_cameras = []
        self._generated_violations_per_employee = {}

        # ---- Ring buffer + clip recorder ----
        clip_config = clip_config or {}
        pre_roll = clip_config.get("pre_roll_seconds", 5.0)
        post_roll = clip_config.get("post_roll_seconds", 5.0)

        self._pre_roll_seconds = pre_roll
        self._post_roll_seconds = post_roll

        self._frame_buffer = FrameRingBuffer(
            max_seconds=pre_roll + 1.0,
            max_frames=clip_config.get("max_frames", 500),
        )
        self._clip_recorder = ClipRecorder(
            frame_buffer=self._frame_buffer,
            output_dir=clip_config.get("output_dir", "./audit_clips"),
            fps=clip_config.get("fps", 8.0),
            pre_roll_seconds=pre_roll,
            post_roll_seconds=post_roll,
        )

    def run(self):
        while not self.stop_event.is_set():
            try:
                pkt = self.input_queue.get()
                if pkt is not None:
                    self.process_pkt(pkt)
            except queue.Empty:
                continue

    def stop(self):
        self.stop_event.set()
        try:
            self._clip_recorder.flush()
        except Exception as e:
            logger.warning("Error flushing clips on stop: %s", e)

    # ------------------------------------------------------------------------
    # Обработка пакета
    # ------------------------------------------------------------------------

    def process_pkt(self, pkt):
        if pkt is None:
            return

        # --- 1. Кладём кадр в ring buffer и обновляем активные клипы ---
        camera_id = pkt.get("camera_id", "default")
        frame = pkt.get("frame_bgr")
        now = time.time()
        if frame is not None:
            self._frame_buffer.push(camera_id, now, frame)
            self._clip_recorder.update(camera_id, now, frame)

        if camera_id not in self._known_cameras:
            self._is_no_cap_detected[camera_id] = False
            self._is_no_apron_detected[camera_id] = False
            self._is_no_glove_detected[camera_id] = False
            self._is_face_touch_detected[camera_id] = False
            self._is_phone_usage_detected[camera_id] = False
            self._known_cameras.append(camera_id)

        events = []

        no_cap = self._detect_no_cap(pkt=pkt)
        if no_cap is not None:
            events.extend(no_cap)

        no_gloves = self._detect_no_gloves(pkt=pkt)
        if no_gloves is not None:
            events.extend(no_gloves)

        no_apron = self._detect_no_apron(pkt=pkt)
        if no_apron is not None:
            events.extend(no_apron)

        phone_usage = self._detect_phone_usage(pkt=pkt)
        if phone_usage is not None:
            events.extend(phone_usage)

        if events:
            self._emit_events(events=events)

    def _emit_events(self, events):
        if not events:
            return
        for event in events:
            logger.info("ACTION EVENT JSON:\n%s",
                        json.dumps(event, ensure_ascii=False, indent=2, default=str))
            if self.out_queue is not None:
                try:
                    self.out_queue.put_nowait(event)
                except Exception:
                    try:
                        self.out_queue.put(event, timeout=0.1)
                    except Exception:
                        logger.warning("Failed to push action event to out_queue")

    # ------------------------------------------------------------------------
    # Генерация санитарного события + планирование клипа
    # ------------------------------------------------------------------------

    def _generate_sanitary_event(
        self,
        event_type: str,
        zone_id: str,
        camera_id: str,
        employee_id: str,
        track_id: str,
        track_lineage_id: Optional[str],
        confidence: float,
    ) -> Dict:

        entity_id = employee_id if employee_id else track_id
        entity_type = "employee" if employee_id else "system"
        action_id = str(uuid.uuid4())

        # --- Планируем клип (5 сек до + 5 сек после) ---
        try:
            self._clip_recorder.schedule_clip(
                camera_id=camera_id,
                event_type=event_type,
                trigger_ts=time.time(),
                extra_id=action_id,
            )
        except Exception as e:
            logger.warning("Failed to schedule clip for %s: %s", event_type, e)

        sanitary_event = {
            "sanitary_event_id": action_id,
            "sanitary_event_type": event_type,
            "severity": SEVERITY_MAP[event_type][0],
            "severity_internal": SEVERITY_MAP[event_type][1],
            "zone_id": zone_id,
            "decision_mode": "decision_pending"
        }

        return {
            "event_id": action_id,
            "timestamp": time.time(),
            "event_type": "sanitary_event",
            "camera_id": camera_id,
            "entity_id": entity_id,
            "entity_type": entity_type,
            "confidence": confidence,
            "time_source": "server_ntp",
            "late": False,
            "track_lineage_id": track_lineage_id,
            "executor_attribution": "unresolved" if not employee_id else "confirmed",
            "rules_config_version": None,
            "model_versions": None,
            "decision_trace": None,
            "audit_clip_id": action_id,   # ссылка на клип (по соглашению — совпадает с id)
            "payload": sanitary_event,
        }

    # ------------------------------------------------------------------------
    # Детекторы (без изменений в логике)
    # ------------------------------------------------------------------------

    def _detect_no_cap(self, pkt):
        cam_id = pkt.get("camera_id", "default")
        zone_id = pkt.get("zone_id", "")
        facts = pkt["facts"]
        identities = pkt["identities"]

        person = self._get_fact_by_object_class(facts, "person")
        if not person or person["confidence"] is None or person["confidence"] < self._employee_confidence_floor:
            return None

        generated_events = []

        identity_map: Dict[str, Dict] = {}
        for id_item in identities:
            lineage_id = id_item.get("track_lineage_id")
            if lineage_id:
                identity_map[lineage_id] = {
                    "employee_id": id_item.get("employee_id"),
                    "employee_confidence": id_item.get("employee_confidence", 0.0),
                    "executor_attribution": id_item.get("executor_attribution", "unresolved"),
                }

        track_id = person.get("track_id", "")
        track_linage_id = person.get("track_lineage_id")

        identity = identity_map.get(track_linage_id) if track_linage_id else None
        employee_id = identity.get("employee_id") if identity else None
        employee_confidence = identity.get("employee_confidence", 0.0) if identity else None

        if not employee_confidence or employee_confidence < self._employee_id_confidence_floor:
            return None

        cap = self._get_fact_by_object_class(facts, "cap")
        if cap is None and not self._is_no_cap_detected.get(cam_id, False):
            self._is_no_cap_detected[cam_id] = True
            self._no_cap_detected_timestamp[cam_id] = time.time()
        else:
            if cap is not None and self._is_no_cap_detected.get(cam_id):
                self._is_no_cap_detected[cam_id] = False

        time_since_last_no_cap_violation = time.time() - self._generated_violations_per_employee.get(employee_id, 0)
        violation_time = time.time() - self._no_cap_detected_timestamp.get(cam_id, 0)

        if (self._is_no_cap_detected.get(cam_id) and violation_time > self._T_uniform_confirm_sec
                and time_since_last_no_cap_violation > self._T_uniform_confirm_sec * 5):

            event = self._generate_sanitary_event(
                event_type="no_cap",
                zone_id=zone_id,
                camera_id=cam_id,
                employee_id=employee_id,
                confidence=employee_confidence,
                track_id=track_id,
                track_lineage_id=track_linage_id,
            )
            self._generated_violations_per_employee[employee_id] = time.time()
            generated_events.append(event)
            self._is_no_cap_detected[cam_id] = False

        return generated_events

    def _detect_no_gloves(self, pkt):
        cam_id = pkt.get("camera_id", "default")
        zone_id = pkt.get("zone_id", "")
        facts = pkt["facts"]
        identities = pkt["identities"]

        person = self._get_fact_by_object_class(facts, "person")
        if not person or person["confidence"] is None or person["confidence"] < self._employee_confidence_floor:
            return None

        generated_events = []

        identity_map: Dict[str, Dict] = {}
        for id_item in identities:
            lineage_id = id_item.get("track_lineage_id")
            if lineage_id:
                identity_map[lineage_id] = {
                    "employee_id": id_item.get("employee_id"),
                    "employee_confidence": id_item.get("employee_confidence", 0.0),
                    "executor_attribution": id_item.get("executor_attribution", "unresolved"),
                }

        track_id = person.get("track_id", "")
        track_linage_id = person.get("track_lineage_id")

        identity = identity_map.get(track_linage_id) if track_linage_id else None
        employee_id = identity.get("employee_id") if identity else None
        employee_confidence = identity.get("employee_confidence", 0.0) if identity else None

        if not employee_confidence or employee_confidence < self._employee_id_confidence_floor:
            return None

        glove = self._get_fact_by_object_class(facts, "glove")
        if glove is None and not self._is_no_glove_detected.get(cam_id, False):
            self._is_no_glove_detected[cam_id] = True
            self._no_glove_detected_timestamp[cam_id] = time.time()
        else:
            if glove is not None and self._is_no_glove_detected.get(cam_id):
                self._is_no_glove_detected[cam_id] = False

        time_since_last_no_glove_violation = time.time() - self._generated_violations_per_employee.get(employee_id, 0)
        violation_time = time.time() - self._no_glove_detected_timestamp.get(cam_id, 0)

        if (self._is_no_glove_detected.get(cam_id) and violation_time > self._T_uniform_confirm_sec
                and time_since_last_no_glove_violation > self._T_uniform_confirm_sec * 5):

            event = self._generate_sanitary_event(
                event_type="no_gloves",
                zone_id=zone_id,
                camera_id=cam_id,
                employee_id=employee_id,
                confidence=employee_confidence,
                track_id=track_id,
                track_lineage_id=track_linage_id,
            )
            self._generated_violations_per_employee[employee_id] = time.time()
            generated_events.append(event)
            self._is_no_glove_detected[cam_id] = False

        return generated_events

    def _detect_no_apron(self, pkt):
        cam_id = pkt.get("camera_id", "default")
        zone_id = pkt.get("zone_id", "")
        facts = pkt["facts"]
        identities = pkt["identities"]

        person = self._get_fact_by_object_class(facts, "person")
        if not person or person["confidence"] is None or person["confidence"] < self._employee_confidence_floor:
            return None

        generated_events = []

        identity_map: Dict[str, Dict] = {}
        for id_item in identities:
            lineage_id = id_item.get("track_lineage_id")
            if lineage_id:
                identity_map[lineage_id] = {
                    "employee_id": id_item.get("employee_id"),
                    "employee_confidence": id_item.get("employee_confidence", 0.0),
                    "executor_attribution": id_item.get("executor_attribution", "unresolved"),
                }

        track_id = person.get("track_id", "")
        track_linage_id = person.get("track_lineage_id")

        identity = identity_map.get(track_linage_id) if track_linage_id else None
        employee_id = identity.get("employee_id") if identity else None
        employee_confidence = identity.get("employee_confidence", 0.0) if identity else None

        if not employee_confidence or employee_confidence < self._employee_id_confidence_floor:
            return None

        apron = self._get_fact_by_object_class(facts, "apron")
        if apron is None and not self._is_no_apron_detected.get(cam_id, False):
            self._is_no_apron_detected[cam_id] = True
            self._no_apron_detected_timestamp[cam_id] = time.time()
        else:
            if apron is not None and self._is_no_apron_detected.get(cam_id):
                self._is_no_apron_detected[cam_id] = False

        time_since_last_no_apron_violation = time.time() - self._generated_violations_per_employee.get(employee_id, 0)
        violation_time = time.time() - self._no_apron_detected_timestamp.get(cam_id, 0)

        if (self._is_no_apron_detected.get(cam_id) and violation_time > self._T_uniform_confirm_sec
                and time_since_last_no_apron_violation > self._T_uniform_confirm_sec * 5):

            event = self._generate_sanitary_event(
                event_type="no_apron",
                zone_id=zone_id,
                camera_id=cam_id,
                employee_id=employee_id,
                confidence=employee_confidence,
                track_id=track_id,
                track_lineage_id=track_linage_id,
            )
            self._generated_violations_per_employee[employee_id] = time.time()
            generated_events.append(event)
            self._is_no_apron_detected[cam_id] = False

        return generated_events

    def _detect_phone_usage(self, pkt):
        cam_id = pkt.get("camera_id", "")
        zone_id = pkt.get("zone_id", "")
        facts = pkt["facts"]
        identities = pkt["identities"]

        person = self._get_fact_by_object_class(facts, "person")
        if not person or person["confidence"] is None or person["confidence"] < self._employee_confidence_floor:
            return None

        generated_events = []

        identity_map: Dict[str, Dict] = {}
        for id_item in identities:
            lineage_id = id_item.get("track_lineage_id")
            if lineage_id:
                identity_map[lineage_id] = {
                    "employee_id": id_item.get("employee_id"),
                    "employee_confidence": id_item.get("employee_confidence", 0.0),
                    "executor_attribution": id_item.get("executor_attribution", "unresolved"),
                }

        track_id = person.get("track_id", "")
        track_linage_id = person.get("track_lineage_id")

        identity = identity_map.get(track_linage_id) if track_linage_id else None
        employee_id = identity.get("employee_id") if identity else None
        employee_confidence = identity.get("employee_confidence", 0.0) if identity else None

        if not employee_confidence or employee_confidence < self._employee_id_confidence_floor:
            return None

        phone = self._get_fact_by_object_class(facts, "phone")
        hands = self._get_fact_by_object_class(facts, "hands")
        if phone is None or phone["confidence"] < 0.5 or hands is None or hands["confidence"] < 0.5:
            self._is_phone_usage_detected[cam_id] = False
            return None

        person_bbox = self._parse_bbox(person["bbox"])
        phone_bbox = self._parse_bbox(phone["bbox"])
        hands_bbox = self._parse_bbox(hands["bbox"])
        if person_bbox is None or phone_bbox is None or hands_bbox is None:
            return None

        person_bbox_center = self._calculate_bbox_center(person_bbox)
        hands_bbox_center = self._calculate_bbox_center(hands_bbox)

        if (self._iou(hands_bbox, phone_bbox) >= 0.5
                > self._calculate_distance(person_bbox_center, hands_bbox_center)):
            if not self._is_phone_usage_detected.get(cam_id, False):
                self._is_phone_usage_detected[cam_id] = True
                self._phone_usage_timestamp[cam_id] = time.time()
        else:
            self._is_phone_usage_detected[cam_id] = False

        time_since_last_phone_usage_violation = time.time() - self._generated_violations_per_employee.get(employee_id, 0)
        violation_time = time.time() - self._phone_usage_timestamp.get(cam_id, 0)

        if (self._is_phone_usage_detected.get(cam_id, False)
                and violation_time > self._T_uniform_confirm_sec
                and time_since_last_phone_usage_violation > self._T_uniform_confirm_sec * 5):

            event = self._generate_sanitary_event(
                event_type="phone_usage",
                zone_id=zone_id,
                camera_id=cam_id,
                employee_id=employee_id,
                confidence=employee_confidence,
                track_id=track_id,
                track_lineage_id=track_linage_id,
            )
            generated_events.append(event)
            self._is_phone_usage_detected[cam_id] = False

        return generated_events

    def _detect_face_touch(self, pkt):
        # TODO: реализовать при необходимости
        pass

    # ------------------------------------------------------------------------
    # Утилиты
    # ------------------------------------------------------------------------

    def _get_fact_by_object_class(self, facts, object_class):
        for fact in facts:
            if fact["object_class"] == object_class:
                return fact
        return None

    def _parse_bbox(self, bbox):
        try:
            if bbox is None:
                return None
            if isinstance(bbox, str):
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

    def _calculate_bbox_center(self, bbox):
        x1, y1, x2, y2 = bbox
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0

    def _calculate_distance(self, p1, p2):
        return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5

    def _iou(self, boxA, boxB):
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])

        inter_w = max(0.0, xB - xA)
        inter_h = max(0.0, yB - yA)
        inter = inter_w * inter_h

        area_a = max(0.0, boxA[2] - boxA[0]) * max(0.0, boxA[3] - boxA[1])
        area_b = max(0.0, boxB[2] - boxB[0]) * max(0.0, boxB[3] - boxB[1])

        union = area_a + area_b - inter
        if union <= 0:
            return 0.0
        return inter / union


if __name__ == "__main__":
    input_q = queue.Queue()
    output_q = queue.Queue()

    cfg = {
        "T_uniform_confirm_sec": 1.0,
        "employee_confidence_floor": 0.9,
        "employee_id_confidence_floor": 0.6,
    }

    clip_cfg = {
        "pre_roll_seconds": 5.0,
        "post_roll_seconds": 5.0,
        "fps": 8.0,
        "output_dir": "./audit_clips",
        "max_frames": 500,
    }

    module = UniformBehaviorRules(
        input_queue=input_q,
        out_queue=output_q,
        intrusion_config=cfg,
        clip_config=clip_cfg,
    )
    module.start()
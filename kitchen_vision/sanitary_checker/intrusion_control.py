import json
import os
import queue
import threading
import time
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set

import cv2
import yaml

try:
    from kitchen_vision.sanitary_checker.fsm.observation_fsm import ObservationFSM
    from kitchen_vision.sanitary_checker.zones.zone_resolver import ZoneResolver
except ImportError:
    class ObservationFSM:
        def get_obs_state(self, camera_id: str = None) -> str:
            return "obs_ok"

    class ZoneResolver:
        def __init__(self, zone_config: dict):
            self.zones = zone_config.get("zones", [])
            self.calibration = zone_config.get("calibration", {})

        def get_zone(self, zone_id: str) -> Optional[dict]:
            for z in self.zones:
                if z.get("zone_id") == zone_id:
                    return z
            return None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("intrusion_control")

OBSERVATION_STATES = {
    "obs_ok": "obs_ok",
    "obs_weak": "obs_weak",
    "obs_degraded": "obs_degraded",
    "obs_lost": "obs_lost",
}

allowed = {
    "prep_main": {"emp_007_ivan", "emp_008_petr"},
}
config = {
    "T_intrusion_confirm_sec": 2.0,
    "person_confidence_threshold": 0.8,
    "employee_confidence_floor": 0.5,
    "allowed_employees_per_zone": allowed,
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
# IntrusionControl
# ============================================================================

class IntrusionControl(threading.Thread):
    """
    Контур контроля вторжения (раздел 21 ТЗ).
    Отслеживает нахождение сотрудников в рабочих зонах и генерирует
    SANITARY_EVENT при обнаружении вторжения.
    """

    def __init__(
            self,
            input_queue: queue.Queue,
            out_queue: queue.Queue,
            intrusion_zone_resolver: ZoneResolver = None,
            observation_fsm: ObservationFSM = None,
            intrusion_config: Optional[Dict[str, Any]] = None,
            clip_config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()

        self.input_queue = input_queue
        self.out_queue = out_queue
        self._zone_resolver = intrusion_zone_resolver
        self._observation_fsm = observation_fsm
        self._config = intrusion_config or {}

        self._T_intrusion_confirm_sec = self._config.get("T_intrusion_confirm_sec", 2.0)
        self._person_confidence_threshold = self._config.get("person_confidence_threshold", 0.8)
        self._employee_confidence_floor = self._config.get("employee_confidence_floor", 0.5)

        self._allowed_employees: Dict[str, Set[str]] = self._config.get("allowed_employees_per_zone", {})

        self._intrusion_state: Dict[tuple, Dict[str, Any]] = {}
        self._zone_polygons: Dict[str, List] = {}
        self._load_zone_polygons()

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

    def process_pkt(self, pkt):
        # --- 1. Кладём кадр в ring buffer и обновляем активные клипы ---
        camera_id = pkt.get("camera_id", "default")
        frame = pkt.get("frame_bgr")
        now = time.time()
        if frame is not None:
            self._frame_buffer.push(camera_id, now, frame)
            self._clip_recorder.update(camera_id, now, frame)

        # --- 2. Обрабатываем факты ---
        data_facts = pkt.get("facts") or []
        data_identities = pkt.get("identities") or []

        if data_facts and data_identities:
            events = self.update_facts(
                facts=data_facts,
                identities=data_identities,
                timestamp=pkt.get("timestamp", int(now * 1000)),
                camera_id=camera_id,
            )
            if events:
                self._emit_events(events=events)

    def _emit_events(self, events):
        if not events:
            return

        for event in events:
            logger.info("SANITARY EVENT JSON:\n%s",
                        json.dumps(event, ensure_ascii=False, indent=2, default=str))
            if self.out_queue is not None:
                try:
                    self.out_queue.put_nowait(event)
                except Exception:
                    try:
                        self.out_queue.put(event, timeout=0.1)
                    except Exception:
                        logger.warning("Failed to push sanitary event to out_queue")

    def _load_zone_polygons(self) -> None:
        """Загружает полигоны всех зон из zone_resolver."""
        if hasattr(self._zone_resolver, 'zones'):
            for zone in self._zone_resolver.zones:
                zone_id = zone.get("zone_id")
                polygon = zone.get("polygon")
                if zone_id and polygon:
                    self._zone_polygons[zone_id] = polygon

    def _is_inside_zone(self, bbox: List[float], polygon: List[List[float]]) -> bool:
        if not bbox or len(bbox) < 4:
            return False
        x = (bbox[0] + bbox[2]) / 2.0
        y = bbox[3]
        return self._point_in_polygon(x, y, polygon)

    def _point_in_polygon(self, x: float, y: float, polygon: List[List[float]]) -> bool:
        n = len(polygon)
        inside = False
        p1x, p1y = polygon[0]
        for i in range(1, n + 1):
            p2x, p2y = polygon[i % n]
            if y > min(p1y, p2y):
                if y <= max(p1y, p2y):
                    if x <= max(p1x, p2x):
                        if p1y != p2y:
                            x_inters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                        if p1x == p2x or x <= x_inters:
                            inside = not inside
            p1x, p1y = p2x, p2y
        return inside

    def _get_relevant_employee_id(
            self,
            zone_id: str,
            track_data: Dict,
    ) -> Optional[str]:
        employee_id = track_data.get("employee_id")
        employee_confidence = track_data.get("employee_confidence", 0.0)

        if not employee_id or employee_confidence < self._employee_confidence_floor:
            return None

        allowed_employees = self._allowed_employees.get(zone_id)
        if allowed_employees is not None:
            if employee_id in allowed_employees:
                return employee_id
            else:
                return None

        return employee_id

    def update_facts(
            self,
            facts: List[Dict],
            identities: List[Dict],
            timestamp: int,
            camera_id: Optional[str] = None,
    ) -> List[Dict]:
        """
        Обрабатывает новые факты (детекции) и идентификации.

        camera_id добавлен для явной передачи в _create_intrusion_event
        (в фактах тоже есть, но лучше явно).
        """
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

        for fact in facts:
            if fact.get("object_class") != "person":
                continue

            track_id = fact.get("track_id")
            track_lineage_id = fact.get("track_lineage_id")
            confidence = fact.get("confidence", 0.0)
            bbox = fact.get("bbox")
            fact_camera_id = fact.get("camera_id") or camera_id or "default"
            zone_id = fact.get("zone_id")

            if confidence < self._person_confidence_threshold:
                continue

            if not zone_id:
                continue

            polygon = self._zone_polygons.get(zone_id)
            if not polygon:
                continue

            if not self._is_inside_zone(bbox, polygon):
                self._clear_intrusion_state(zone_id, track_id, track_lineage_id)
                continue

            identity = identity_map.get(track_lineage_id) if track_lineage_id else None
            employee_id = identity.get("employee_id") if identity else None
            employee_confidence = identity.get("employee_confidence", 0.0) if identity else 0.0

            relevant_employee = self._get_relevant_employee_id(zone_id, {
                "employee_id": employee_id,
                "employee_confidence": employee_confidence,
            })

            if relevant_employee and employee_id == relevant_employee:
                self._clear_intrusion_state(zone_id, track_id, track_lineage_id)
                continue

            obs_state = self._observation_fsm.get_obs_state(camera_id=fact_camera_id) \
                if self._observation_fsm else "obs_ok"
            if obs_state == OBSERVATION_STATES["obs_lost"]:
                self._clear_intrusion_state(zone_id, track_id, track_lineage_id)
                continue

            key = (zone_id, track_lineage_id if track_lineage_id else track_id)
            state = self._intrusion_state.get(key)

            if state is None:
                self._intrusion_state[key] = {
                    "first_seen": timestamp,
                    "violation_triggered": False,
                    "employee_id": employee_id,
                    "track_id": track_id,
                }
                continue

            if state.get("violation_triggered", False):
                continue

            elapsed = timestamp - state["first_seen"]
            if elapsed >= self._T_intrusion_confirm_sec:
                event = self._create_intrusion_event(
                    zone_id=zone_id,
                    track_id=track_id,
                    track_lineage_id=track_lineage_id,
                    employee_id=employee_id,
                    camera_id=fact_camera_id,
                    timestamp=timestamp,
                    confidence=confidence,
                )
                generated_events.append(event)
                state["violation_triggered"] = True
                logger.info(f"Intrusion detected in zone {zone_id} for track {track_id}")

        return generated_events

    def _clear_intrusion_state(self, zone_id: str, track_id: str, track_lineage_id: Optional[str]) -> None:
        key = (zone_id, track_lineage_id if track_lineage_id else track_id)
        if key in self._intrusion_state:
            del self._intrusion_state[key]

    def _create_intrusion_event(
            self,
            zone_id: str,
            track_id: str,
            track_lineage_id: Optional[str],
            employee_id: Optional[str],
            camera_id: str,
            timestamp: int,
            confidence: float,
    ) -> Dict:

        entity_id = employee_id if employee_id else track_id
        entity_type = "employee" if employee_id else "system"
        action_id = str(uuid.uuid4())

        # --- Планируем клип (5 сек до + 5 сек после) ---
        try:
            self._clip_recorder.schedule_clip(
                camera_id=camera_id,
                event_type="intrusion_work_zone",
                trigger_ts=time.time(),
                extra_id=action_id,
            )
        except Exception as e:
            logger.warning("Failed to schedule clip for intrusion_work_zone: %s", e)

        sanitary_event = {
            "sanitary_event_id": action_id,
            "sanitary_event_type": "intrusion_work_zone",
            "source_context": None,
            "target_context": None,
            "severity": "violation",
            "severity_internal": "high",
            "escalation_category": None,
            "incompatibility_cause": None,
            "rule_id": None,
            "zone_id": zone_id,
            "employee_id": employee_id,
            "decision_mode": "SANITARY_VIOLATION_CONFIRMED",
        }

        return {
            "event_id": action_id,
            "timestamp": timestamp,
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
            "audit_clip_id": action_id,
            "payload": sanitary_event,
        }


if __name__ == "__main__":
    input_q = queue.Queue()
    output_q = queue.Queue()

    with open("zones.yaml", "r") as f:
        zones_cfg = yaml.safe_load(f)

    zone_resolver = ZoneResolver(zones_cfg)
    obs_fsm = ObservationFSM()

    intrusion_cfg = {
        "T_intrusion_confirm_sec": 2.0,
        "person_confidence_threshold": 0.8,
        "employee_confidence_floor": 0.5,
        "allowed_employees_per_zone": allowed,
    }
    clip_cfg = {
        "pre_roll_seconds": 5.0,
        "post_roll_seconds": 5.0,
        "fps": 8.0,
        "output_dir": "./audit_clips",
        "max_frames": 500,
    }

    module = IntrusionControl(
        input_queue=input_q,
        out_queue=output_q,
        intrusion_zone_resolver=zone_resolver,
        observation_fsm=obs_fsm,
        intrusion_config=intrusion_cfg,
        clip_config=clip_cfg,
    )
    module.start()
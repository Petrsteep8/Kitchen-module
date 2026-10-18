import json
import os
import queue
import threading
import time
import uuid
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional, List, Any

import cv2

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zone_exit_control")

SEVERITY_MAP = {
    "left_zone_with_tool": ["violation", "critical"],
    "left_zone_with_food": ["violation", "critical"],
}

OBSERVATION_STATES = {
    "obs_ok": "obs_ok",
    "obs_weak": "obs_weak",
    "obs_degraded": "obs_degraded",
    "obs_lost": "obs_lost",
}


# ============================================================================
# Ring buffer кадров + генерация видео-клипов
# ============================================================================

@dataclass
class BufferedFrame:
    timestamp: float
    frame: Any
    camera_id: str


class FrameRingBuffer:
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
        self, camera_id: str, event_type: str, trigger_ts: float,
        extra_id: Optional[str] = None,
    ) -> str:
        violation_id = extra_id or f"{camera_id}_{event_type}_{int(trigger_ts * 1000)}"
        with self._lock:
            start_ts = trigger_ts - self._pre_roll_seconds
            pre_frames = self._buffer.snapshot(camera_id, start_ts, trigger_ts)
            self._pending.append(PendingClip(
                camera_id=camera_id, event_type=event_type,
                violation_id=violation_id, trigger_ts=trigger_ts,
                post_roll_seconds=self._post_roll_seconds,
                pre_roll_frames=pre_frames,
            ))
        logger.info("Clip scheduled: id=%s camera=%s event=%s pre_frames=%d",
                    violation_id, camera_id, event_type, len(pre_frames))
        return violation_id

    def update(self, camera_id: str, timestamp: float, frame) -> None:
        if frame is None:
            return
        completed = []
        with self._lock:
            for pending in self._pending:
                if pending.camera_id != camera_id:
                    continue
                if timestamp < pending.trigger_ts:
                    continue
                if timestamp <= pending.trigger_ts + pending.post_roll_seconds:
                    pending.post_roll_frames.append(BufferedFrame(timestamp, frame, camera_id))
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
            logger.warning("No frames for clip %s", pending.violation_id)
            return None
        first = next((f.frame for f in all_frames if f.frame is not None), None)
        if first is None:
            return None
        h, w = first.shape[:2]
        filepath = os.path.join(self._output_dir, f"{pending.violation_id}.mp4")
        writer = cv2.VideoWriter(filepath, cv2.VideoWriter_fourcc(*"mp4v"),
                                 self._fps, (w, h))
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
        logger.info("Clip saved: %s (%d frames, ~%.1fs)",
                    filepath, len(all_frames), len(all_frames) / self._fps)
        return filepath

    def flush(self) -> None:
        with self._lock:
            pending = list(self._pending)
            self._pending.clear()
        for p in pending:
            try:
                self._write_clip(p)
            except Exception as e:
                logger.error("Failed to flush clip %s: %s", p.violation_id, e)


# ============================================================================
# ZoneExitControl
# ============================================================================

class ZoneExitControl(threading.Thread):
    """Подконтур контроля выхода из рабочей зоны (раздел 20 ТЗ)."""

    def __init__(
        self,
        input_action_queue: queue.Queue,
        input_data_queue: queue.Queue,
        out_queue: queue.Queue,
        zone_resolver=None,
        observation_fsm=None,
        config: Optional[Dict[str, Any]] = None,
        clip_config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()

        self._input_action_queue = input_action_queue
        self._input_data_queue = input_data_queue
        self.out_queue = out_queue
        self._zone_resolver = zone_resolver
        self._observation_fsm = observation_fsm

        cfg = config or {}
        self._handoff_confirm_window_sec: float = cfg.get("handoff_confirm_window_sec", 2.0)
        self._T_handoff_confirm_sec: float = cfg.get("T_handoff_confirm_sec", 0.6)
        self._handoff_radius_m: float = cfg.get("handoff_radius_m", 3.0)
        self._handoff_nearest_margin_m: float = cfg.get("handoff_nearest_margin_m", 0.5)

        # Состояние активных окон
        # {camera_id: {start_ts, window_sec, zone_id, original_employee_id,
        #              original_lineage_id, original_track_id,
        #              candidates: {food_id: {first_seen, last_seen, ...}}}}
        self._data_capture_started: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

        # Полигоны зон
        self._zone_polygons: Dict[str, List] = {}
        self._load_zone_polygons()

        # Clip config
        clip_config = clip_config or {}
        pre_roll = clip_config.get("pre_roll_seconds", 5.0)
        post_roll = clip_config.get("post_roll_seconds", 5.0)
        self._clip_for_process_event = clip_config.get("clip_for_process_event", False)
        self._clip_for_data_quality = clip_config.get("clip_for_data_quality_obs_lost", False)

        self._frame_buffer = FrameRingBuffer(max_seconds=pre_roll + 1.0,
                                             max_frames=clip_config.get("max_frames", 500))
        self._clip_recorder = ClipRecorder(
            frame_buffer=self._frame_buffer,
            output_dir=clip_config.get("output_dir", "./audit_clips"),
            fps=clip_config.get("fps", 8.0),
            pre_roll_seconds=pre_roll,
            post_roll_seconds=post_roll,
        )

    # ------------------------------------------------------------------------
    # Основной цикл
    # ------------------------------------------------------------------------

    def run(self):
        while not self.stop_event.is_set():
            got_something = False

            # --- action queue (non-blocking) ---
            try:
                action_pkt = self._input_action_queue.get_nowait()
                if action_pkt is not None:
                    self._handle_action(action_pkt)
                    got_something = True
            except queue.Empty:
                pass

            # --- data queue (non-blocking) ---
            try:
                data_pkt = self._input_data_queue.get_nowait()
                if data_pkt is not None:
                    self._handle_data(data_pkt)
                    got_something = True
            except queue.Empty:
                pass

            # --- timeouts ---
            self._check_timeouts()

            if not got_something:
                time.sleep(0.01)

    def stop(self):
        self.stop_event.set()
        try:
            self._clip_recorder.flush()
        except Exception as e:
            logger.warning("Error flushing clips on stop: %s", e)

    # ------------------------------------------------------------------------
    # Обработка action_end
    # ------------------------------------------------------------------------

    def _handle_action(self, action_pkt: Dict) -> None:
        end_reason = action_pkt.get("end_reason")
        camera_id = action_pkt.get("camera_id")

        if end_reason == "left_zone":
            self._process_left_zone(action_pkt)

        elif end_reason == "tracking_lost":
            # 20.2.5: нарушение left_zone_with_* НЕ генерируется
            logger.info("tracking_lost during zone exit -> DATA_QUALITY_EVENT")
            event = self._create_data_quality_event(
                reason="tracking_lost_during_zone_exit",
                camera_id=camera_id,
                zone_id=action_pkt.get("zone_id"),
                employee_id=action_pkt.get("employee_id"),
                track_lineage_id=action_pkt.get("track_lineage_id"),
                action_id=action_pkt.get("action_id"),
                timestamp=action_pkt.get("timestamp", int(time.time() * 1000)),
            )
            self._emit_events([event])

    def _process_left_zone(self, action_pkt: Dict) -> None:
        """20.2: обработка end_reason='left_zone'."""
        carrying_type = action_pkt.get("carrying_type", "none")
        camera_id = action_pkt.get("camera_id")
        observation_state = self._get_observation_state(camera_id)

        # В OBS_DEGRADED/OBS_LOST нарушения не подтверждаем
        if observation_state in (OBSERVATION_STATES["obs_degraded"],
                                 OBSERVATION_STATES["obs_lost"]):
            logger.info("left_zone skipped due to observation_state=%s", observation_state)
            return

        # --- 20.2.1: выход с инструментом ---
        if carrying_type == "tool":
            event = self._create_sanitary_event(
                event_type="left_zone_with_tool",
                camera_id=camera_id,
                zone_id=action_pkt.get("zone_id"),
                employee_id=action_pkt.get("employee_id"),
                track_lineage_id=action_pkt.get("track_lineage_id"),
                track_id=action_pkt.get("track_id"),
                timestamp=action_pkt.get("timestamp", int(time.time() * 1000)),
                confidence=action_pkt.get("confidence", 0.8),
            )
            self._emit_events([event])
            return

        # --- 20.2.2: выход с едой → открыть окно handoff_food ---
        if carrying_type == "food":
            self._open_handoff_window(action_pkt)
            return

        logger.debug("left_zone without risk factor (carrying_type=%s)", carrying_type)

    # ------------------------------------------------------------------------
    # Окно handoff_food
    # ------------------------------------------------------------------------

    def _open_handoff_window(self, action_pkt: Dict) -> None:
        camera_id = action_pkt.get("camera_id")
        if not camera_id:
            return
        with self._lock:
            self._data_capture_started[camera_id] = {
                "start_ts": time.time(),
                "window_sec": self._handoff_confirm_window_sec,
                "zone_id": action_pkt.get("zone_id"),
                "original_employee_id": action_pkt.get("employee_id"),
                "original_lineage_id": action_pkt.get("track_lineage_id"),
                "original_track_id": action_pkt.get("track_id"),
                "candidates": {},
            }
        logger.info("Handoff window opened for camera=%s zone=%s",
                    camera_id, action_pkt.get("zone_id"))

    def _handle_data(self, data_pkt: Dict) -> None:
        camera_id = data_pkt.get("camera_id")
        if not camera_id:
            return

        # Ring buffer + clip post-roll
        frame = data_pkt.get("frame_bgr")
        now = time.time()
        if frame is not None:
            self._frame_buffer.push(camera_id, now, frame)
            self._clip_recorder.update(camera_id, now, frame)

        # Обновляем активное окно handoff, если есть
        with self._lock:
            capture = self._data_capture_started.get(camera_id)
        if capture is not None:
            self._update_handoff_window(camera_id, capture, data_pkt, now)

    def _update_handoff_window(self, camera_id: str, capture: Dict,
                                data_pkt: Dict, now: float) -> None:
        self._scan_for_candidates(camera_id, capture, data_pkt, now)

        if now - capture["start_ts"] >= capture["window_sec"]:
            self._finalize_handoff(camera_id, capture, data_pkt)

    def _scan_for_candidates(self, camera_id: str, capture: Dict,
                              data_pkt: Dict, now: float) -> None:
        facts = data_pkt.get("facts") or []
        food_facts = [f for f in facts if f.get("object_class") == "food"]
        person_facts = [f for f in facts if f.get("object_class") == "person"]

        if not food_facts or not person_facts:
            return

        zone_id = capture.get("zone_id")
        zone_polygon = self._zone_polygons.get(zone_id)
        radius_px = self._meters_to_px(camera_id, self._handoff_radius_m)

        for food in food_facts:
            food_bbox = self._parse_bbox(food.get("bbox"))
            if not food_bbox:
                continue
            food_center = self._calculate_bbox_center(food_bbox)

            # 20.2.3: food должен быть в радиусе handoff_radius_m от исходной зоны
            if zone_polygon and radius_px is not None:
                zone_center = self._polygon_centroid(zone_polygon)
                dist = self._calculate_distance(food_center, zone_center)
                if dist > radius_px:
                    continue
            elif zone_polygon and radius_px is None:
                # нет калибровки — пропускаем проверку радиуса с предупреждением
                logger.debug("No calibration for %s; skipping radius check", camera_id)

            # ищем person, "с которым" находится food
            matched_person = self._find_person_with_food(
                food_bbox, person_facts, capture.get("original_lineage_id")
            )
            if matched_person is None:
                continue

            food_id = food.get("track_id")
            if food_id is None:
                continue

            if food_id not in capture["candidates"]:
                capture["candidates"][food_id] = {
                    "first_seen": now,
                    "last_seen": now,
                    "person_track_id": matched_person.get("track_id"),
                    "person_lineage_id": matched_person.get("track_lineage_id"),
                    "distance_to_zone": (
                        self._calculate_distance(
                            food_center, self._polygon_centroid(zone_polygon))
                        if zone_polygon else 0.0
                    ),
                }
            else:
                capture["candidates"][food_id]["last_seen"] = now

    def _find_person_with_food(self, food_bbox: List[float],
                                person_facts: List[Dict],
                                original_lineage_id: Optional[str]) -> Optional[Dict]:
        """Возвращает person, который не является исходным сотрудником и
        "держит" food (bbox пересекаются или food рядом с person)."""
        for person in person_facts:
            # исключаем исходного сотрудника
            if original_lineage_id and person.get("track_lineage_id") == original_lineage_id:
                continue
            person_bbox = self._parse_bbox(person.get("bbox"))
            if not person_bbox:
                continue
            if self._bboxes_overlap(food_bbox, person_bbox) or \
               self._is_center_in_bbox(self._calculate_bbox_center(food_bbox),
                                       person_bbox):
                return person
        return None

    def _finalize_handoff(self, camera_id: str, capture: Dict,
                           data_pkt: Dict) -> None:
        now = time.time()
        original_zone = capture.get("zone_id")
        original_employee = capture.get("original_employee_id")
        original_lineage = capture.get("original_lineage_id")
        original_track_id = capture.get("original_track_id")

        # Проверяем, не потеряли ли наблюдаемость в процессе окна
        obs_state = self._get_observation_state(camera_id)
        if obs_state in (OBSERVATION_STATES["obs_degraded"],
                         OBSERVATION_STATES["obs_lost"]):
            with self._lock:
                self._data_capture_started.pop(camera_id, None)
            event = self._create_data_quality_event(
                reason="UNRESOLVED_DUE_TO_DATA_QUALITY",
                camera_id=camera_id,
                zone_id=original_zone,
                employee_id=original_employee,
                track_lineage_id=original_lineage,
                timestamp=int(now * 1000),
            )
            self._emit_events([event])
            return

        # Кандидаты, наблюдавшиеся >= T_handoff_confirm_sec
        valid = [(fid, c) for fid, c in capture["candidates"].items()
                 if (c["last_seen"] - c["first_seen"]) >= self._T_handoff_confirm_sec]

        events = []

        if len(valid) == 0:
            # 20.2.4: handoff_food=false → left_zone_with_food
            events.append(self._create_sanitary_event(
                event_type="left_zone_with_food",
                camera_id=camera_id,
                zone_id=original_zone,
                employee_id=original_employee,
                track_lineage_id=original_lineage,
                track_id=original_track_id,
                timestamp=int(now * 1000),
                confidence=0.8,
            ))

        elif len(valid) == 1:
            # 20.2.4: handoff_food=true → PROCESS_EVENT: food_handoff
            events.append(self._create_process_event(
                process_event_type="food_handoff",
                camera_id=camera_id,
                zone_id=original_zone,
                employee_id=original_employee,
                track_lineage_id=original_lineage,
                timestamp=int(now * 1000),
                confidence=0.8,
            ))

        else:
            # 20.2.3: nearest-or-unresolved
            valid.sort(key=lambda x: x[1]["distance_to_zone"])
            margin_px = valid[1][1]["distance_to_zone"] - valid[0][1]["distance_to_zone"]
            margin_m = self._px_to_meters(camera_id, margin_px)

            # Без калибровки не можем сравнивать — трактуем как ambiguous
            resolved = margin_m is not None and margin_m > self._handoff_nearest_margin_m

            if resolved:
                events.append(self._create_process_event(
                    process_event_type="food_handoff",
                    camera_id=camera_id,
                    zone_id=original_zone,
                    employee_id=original_employee,
                    track_lineage_id=original_lineage,
                    timestamp=int(now * 1000),
                    confidence=0.8,
                ))
            else:
                events.append(self._create_data_quality_event(
                    reason="ambiguous_handoff_target",
                    camera_id=camera_id,
                    zone_id=original_zone,
                    employee_id=original_employee,
                    track_lineage_id=original_lineage,
                    timestamp=int(now * 1000),
                ))

        with self._lock:
            self._data_capture_started.pop(camera_id, None)

        self._emit_events(events)

    def _check_timeouts(self) -> None:
        """Закрывает окна, истёкшие без новых данных."""
        now = time.time()
        expired = []
        with self._lock:
            for cam_id, cap in list(self._data_capture_started.items()):
                if now - cap["start_ts"] >= cap["window_sec"]:
                    expired.append((cam_id, cap))
        for cam_id, cap in expired:
            # данные отсутствуют — считаем как без кандидатов
            self._finalize_handoff(cam_id, cap, {})

    # ------------------------------------------------------------------------
    # Фабрики событий (все триггерят клип согласно §37.1/37.2)
    # ------------------------------------------------------------------------

    def _create_sanitary_event(
        self, event_type: str, camera_id: str, zone_id: Optional[str],
        employee_id: Optional[str], track_lineage_id: Optional[str],
        track_id: Optional[str], timestamp: int, confidence: float,
    ) -> Dict:
        event_id = str(uuid.uuid4())
        entity_id = employee_id if employee_id else (track_id or event_id)
        entity_type = "employee" if employee_id else "system"

        # §37.1: клип для SANITARY_VIOLATION_CONFIRMED обязателен
        try:
            self._clip_recorder.schedule_clip(
                camera_id=camera_id, event_type=event_type,
                trigger_ts=time.time(), extra_id=event_id,
            )
        except Exception as e:
            logger.warning("Failed to schedule clip: %s", e)

        severity, severity_internal = SEVERITY_MAP.get(event_type, ["violation", "critical"])

        payload = {
            "sanitary_event_id": event_id,
            "sanitary_event_type": event_type,
            "source_context": None,
            "target_context": None,
            "severity": severity,
            "severity_internal": severity_internal,
            "escalation_category": None,
            "incompatibility_cause": None,
            "rule_id": "20.2.1" if event_type == "left_zone_with_tool" else "20.2.4",
            "zone_id": zone_id,
            "employee_id": employee_id,
            "decision_mode": "SANITARY_VIOLATION_CONFIRMED",
        }

        return {
            "event_id": event_id,
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
            "audit_clip_id": event_id,
            "payload": payload,
        }

    def _create_process_event(
        self, process_event_type: str, camera_id: str, zone_id: Optional[str],
        employee_id: Optional[str], track_lineage_id: Optional[str],
        timestamp: int, confidence: float,
    ) -> Dict:
        event_id = str(uuid.uuid4())

        # §37.2: клип для PROCESS_EVENT — опционально
        if self._clip_for_process_event:
            try:
                self._clip_recorder.schedule_clip(
                    camera_id=camera_id, event_type=process_event_type,
                    trigger_ts=time.time(), extra_id=event_id,
                )
            except Exception as e:
                logger.warning("Failed to schedule process clip: %s", e)

        return {
            "event_id": event_id,
            "timestamp": timestamp,
            "event_type": "process_event",
            "camera_id": camera_id,
            "entity_id": employee_id or event_id,
            "entity_type": "employee" if employee_id else "system",
            "confidence": confidence,
            "time_source": "server_ntp",
            "late": False,
            "track_lineage_id": track_lineage_id,
            "executor_attribution": "unresolved" if not employee_id else "confirmed",
            "rules_config_version": None,
            "model_versions": None,
            "decision_trace": None,
            "audit_clip_id": event_id,
            "payload": {
                "process_event_id": event_id,
                "process_event_type": process_event_type,
                "decision_mode": "PROCESS_EVENT_CONFIRMED",
            },
        }

    def _create_data_quality_event(
        self, reason: str, camera_id: str, zone_id: Optional[str],
        employee_id: Optional[str], track_lineage_id: Optional[str],
        timestamp: int, action_id: Optional[str] = None,
    ) -> Dict:
        event_id = str(uuid.uuid4())

        # §37.2: клип для DATA_QUALITY_EVENT — опционально
        if self._clip_for_data_quality:
            try:
                self._clip_recorder.schedule_clip(
                    camera_id=camera_id, event_type=f"dq_{reason}",
                    trigger_ts=time.time(), extra_id=event_id,
                )
            except Exception as e:
                logger.warning("Failed to schedule dq clip: %s", e)

        return {
            "event_id": event_id,
            "timestamp": timestamp,
            "event_type": "data_quality_event",
            "camera_id": camera_id,
            "entity_id": event_id,
            "entity_type": "system",
            "confidence": 0.0,
            "time_source": "server_ntp",
            "late": False,
            "track_lineage_id": track_lineage_id,
            "executor_attribution": "unresolved",
            "rules_config_version": None,
            "model_versions": None,
            "decision_trace": None,
            "payload": {
                "data_quality_event_id": event_id,
                "reason": reason,
                "action_id": action_id,
                "employee_id": employee_id,
                "observation_state": self._get_observation_state(camera_id),
                "suppressed_events": [],
                "zone_id": zone_id,
                "decision_mode": "DATA_QUALITY_EVENT_CONFIRMED",
            },
        }

    # ------------------------------------------------------------------------
    # Утилиты
    # ------------------------------------------------------------------------

    def _emit_events(self, events: List[Dict]) -> None:
        if not events:
            return
        for event in events:
            logger.info("EVENT JSON:\n%s",
                        json.dumps(event, ensure_ascii=False, indent=2, default=str))
            if self.out_queue is not None:
                try:
                    self.out_queue.put_nowait(event)
                except Exception:
                    try:
                        self.out_queue.put(event, timeout=0.1)
                    except Exception:
                        logger.warning("Failed to push event to out_queue")

    def _load_zone_polygons(self) -> None:
        if self._zone_resolver and hasattr(self._zone_resolver, "zones"):
            for z in self._zone_resolver.zones:
                zid = z.get("zone_id")
                poly = z.get("polygon")
                if zid and poly:
                    self._zone_polygons[zid] = poly

    def _get_observation_state(self, camera_id: str) -> str:
        if self._observation_fsm is None:
            return OBSERVATION_STATES["obs_ok"]
        try:
            return self._observation_fsm.get_obs_state(camera_id)
        except TypeError:
            return self._observation_fsm.get_obs_state()

    def _meters_to_px(self, camera_id: str, meters: float) -> Optional[float]:
        if not self._zone_resolver:
            return None
        calib = getattr(self._zone_resolver, "calibration", {}) or {}
        cam_cal = calib.get(camera_id) or {}
        px_per_m = cam_cal.get("px_per_meter")
        if px_per_m is None:
            return None
        return meters * px_per_m

    def _px_to_meters(self, camera_id: str, px: float) -> Optional[float]:
        if not self._zone_resolver:
            return None
        calib = getattr(self._zone_resolver, "calibration", {}) or {}
        cam_cal = calib.get(camera_id) or {}
        px_per_m = cam_cal.get("px_per_meter")
        if not px_per_m:
            return None
        return px / px_per_m

    def _polygon_centroid(self, polygon: List[List[float]]) -> Tuple[float, float]:
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    def _parse_bbox(self, bbox) -> Optional[List[float]]:
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

    def _calculate_bbox_center(self, bbox: List[float]) -> Tuple[float, float]:
        x1, y1, x2, y2 = bbox
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0

    def _calculate_distance(self, p1, p2) -> float:
        return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5

    def _bboxes_overlap(self, b1: List[float], b2: List[float]) -> bool:
        return not (b1[2] < b2[0] or b2[2] < b1[0] or b1[3] < b2[1] or b2[3] < b1[1])

    def _is_center_in_bbox(self, center: Tuple[float, float],
                            bbox: List[float]) -> bool:
        x, y = center
        x1, y1, x2, y2 = bbox
        return x1 <= x <= x2 and y1 <= y <= y2
import logging
import time
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("sanitary_checker")


class SanitaryChecker:
    """
    Sanitary Checker по ТЗ.

    Поддерживает 2 типа входов через process_packet(packet):

    1) perception packet:
       {
         "camera_id": ...,
         "timestamp": ...,
         "objects": [...],
         "work_zone_polygon": [...],   # optional
       }

       На выходе:
       - sanitary_report
       - sanitary_events (например intrusion)

    2) action end packet:
       {
         "event_type": "ACTION_END",
         "action_id": ...,
         "employee_id": ...,
         "camera_id": ...,
         "zone_id": ...,
         "action_type": ...,
         "end_reason": ...,
         "carrying_tool": bool,
         "carrying_food": bool,
         "employee_confidence": ...,
         "timestamp_end": ...,
         "objects": [...],             # желательно передавать текущие objects
         "work_zone_polygon": [...],   # желательно передавать полигон зоны
       }

       На выходе:
       - sanitary_events для left_zone / tracking_lost
       - запуск delayed handoff tracking по action_id

    Важно:
    Для food_handoff / left_zone_with_food модуль хранит pending-состояние.
    Чтобы delayed-события реально появлялись, process_packet(...) должен вызываться
    и дальше на следующих perception packet после ACTION_END.
    """

    def __init__(
        self,
        face_region_ratio: float = 0.30,
        phone_iou_threshold: float = 0.10,
        handoff_confirm_window: float = 2.0,
        T_handoff_confirm: float = 0.6,
        T_intrusion_confirm: float = 2.0,
        handoff_distance_threshold_px: float = 80.0,
        bbox_overlap_threshold: float = 0.05,
    ):
        self.face_region_ratio = float(face_region_ratio)
        self.phone_iou_threshold = float(phone_iou_threshold)

        self.handoff_confirm_window = float(handoff_confirm_window)
        self.T_handoff_confirm = float(T_handoff_confirm)
        self.T_intrusion_confirm = float(T_intrusion_confirm)
        self.handoff_distance_threshold_px = float(handoff_distance_threshold_px)
        self.bbox_overlap_threshold = float(bbox_overlap_threshold)

        # pending handoff checks by action_id
        self.pending_food_handoffs: Dict[str, Dict[str, Any]] = {}

        # intrusion timers by (camera_id, employee_id, intruder_track_id, zone_key)
        self.intrusion_timers: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}

        # last known perception context by camera_id
        self.last_context_by_camera: Dict[str, Dict[str, Any]] = {}

    # ==========================================================
    # ========================= PUBLIC ==========================
    # ==========================================================
    def process_packet(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(packet, dict):
            return packet

        now = time.time()
        event_type = packet.get("event_type")
        camera_id = packet.get("camera_id")
        objects = self._safe_objects(packet.get("objects", []))
        work_zone_polygon = self._extract_polygon(packet)

        if camera_id:
            self.last_context_by_camera[camera_id] = {
                "objects": deepcopy(objects),
                "work_zone_polygon": deepcopy(work_zone_polygon),
                "timestamp": packet.get("timestamp"),
                "updated_at_monotonic": now,
            }

        sanitary_report = self._build_sanitary_report(objects)
        events: List[Dict[str, Any]] = []

        # 1) базовые санитарные/поведенческие нарушения по perception
        # 2) intrusion по зоне и известному employee_id владельца действия
        events.extend(self._process_intrusion(packet, objects, work_zone_polygon, now))

        # 3) обработка ACTION_END
        if event_type == "ACTION_END":
            events.extend(self._process_action_end(packet, objects, work_zone_polygon, now))

        # 4) delayed handoff logic
        events.extend(self._process_pending_food_handoffs(packet, objects, work_zone_polygon, now))

        out = dict(packet)
        out["sanitary_report"] = sanitary_report
        out["sanitary_events"] = events
        return out

    # ==========================================================
    # ===================== SANITARY REPORT =====================
    # ==========================================================
    def _build_sanitary_report(self, objects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        persons = [o for o in objects if o.get("class") == "person"]
        bare_hands = [o for o in objects if o.get("class") == "bare_hand"]
        gloved_hands = [o for o in objects if o.get("class") == "gloved_hand"]
        phones = [o for o in objects if o.get("class") in {"cell_phone", "phone"}]

        report: List[Dict[str, Any]] = []
        for person in persons:
            report.append(self._analyze_person(person, bare_hands, gloved_hands, phones))
        return report

    def _analyze_person(
        self,
        person: Dict[str, Any],
        bare_hands: List[Dict[str, Any]],
        gloved_hands: List[Dict[str, Any]],
        phones: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        person_bbox = self._parse_bbox(person.get("bbox"))

        if not person_bbox:
            return {
                "person_id": person.get("id"),
                "employee_id": person.get("employee_id"),
                "violations": {
                    "no_gloves": False,
                    "face_touch": False,
                    "phone_usage": False,
                },
            }

        person_bare = [h for h in bare_hands if self._bbox_overlap(h.get("bbox"), person_bbox)]
        person_gloved = [h for h in gloved_hands if self._bbox_overlap(h.get("bbox"), person_bbox)]
        person_phones = [p for p in phones if self._bbox_overlap(p.get("bbox"), person_bbox)]

        hands_detected = (len(person_bare) + len(person_gloved)) > 0
        no_gloves = len(person_bare) > 0 if hands_detected else False
        face_touch = self._detect_face_touch(person_bbox, person_bare)
        phone_usage = self._detect_phone_usage(person_bare + person_gloved, person_phones)

        return {
            "person_id": person.get("id"),
            "employee_id": person.get("employee_id"),
            "violations": {
                "no_gloves": bool(no_gloves),
                "face_touch": bool(face_touch),
                "phone_usage": bool(phone_usage),
            },
        }

    # ==========================================================
    # ======================= ACTION_END ========================
    # ==========================================================
    def _process_action_end(
        self,
        packet: Dict[str, Any],
        objects: List[Dict[str, Any]],
        work_zone_polygon: Optional[List[List[float]]],
        now: float,
    ) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []

        action_id = packet.get("action_id")
        if not action_id:
            return events

        end_reason = packet.get("end_reason")
        employee_id = packet.get("employee_id")
        camera_id = packet.get("camera_id")
        zone_id = packet.get("zone_id")
        action_type = packet.get("action_type")

        # 5.5.2.3 degradation
        if end_reason == "tracking_lost":
            events.append(
                self._make_event(
                    event_type="DATA_QUALITY_EVENT",
                    code="tracking_lost",
                    action_id=action_id,
                    camera_id=camera_id,
                    zone_id=zone_id,
                    employee_id=employee_id,
                    action_type=action_type,
                    timestamp=packet.get("timestamp_end") or packet.get("timestamp") or self._now_iso(),
                    payload={"end_reason": "tracking_lost"},
                )
            )
            return events

        # left_zone сам по себе не нарушение
        if end_reason != "left_zone":
            return events

        carrying_tool = bool(packet.get("carrying_tool", False))
        carrying_food = bool(packet.get("carrying_food", False))

        if carrying_tool:
            events.append(
                self._make_event(
                    event_type="SANITARY_VIOLATION",
                    code="left_zone_with_tool",
                    action_id=action_id,
                    camera_id=camera_id,
                    zone_id=zone_id,
                    employee_id=employee_id,
                    action_type=action_type,
                    timestamp=packet.get("timestamp_end") or packet.get("timestamp") or self._now_iso(),
                    payload={
                        "end_reason": "left_zone",
                        "carrying_tool": True,
                    },
                )
            )

        if carrying_food:
            employee_person = self._find_person_by_employee_or_best(objects, employee_id)
            employee_track_id = None if employee_person is None else employee_person.get("id")

            self.pending_food_handoffs[action_id] = {
                "action_id": action_id,
                "camera_id": camera_id,
                "zone_id": zone_id,
                "employee_id": employee_id,
                "employee_track_id": employee_track_id,
                "action_type": action_type,
                "end_time_monotonic": now,
                "window_deadline_monotonic": now + self.handoff_confirm_window,
                "confirm_started_monotonic": None,
                "confirmed": False,
                "origin_zone_polygon": deepcopy(work_zone_polygon),
                "origin_food_bboxes": self._extract_food_bboxes(objects),
                "origin_employee_bbox": None if employee_person is None else self._parse_bbox(employee_person.get("bbox")),
                "handoff_person_track_id": None,
            }

        return events

    # ==========================================================
    # ====================== FOOD HANDOFF =======================
    # ==========================================================
    def _process_pending_food_handoffs(
        self,
        packet: Dict[str, Any],
        objects: List[Dict[str, Any]],
        work_zone_polygon: Optional[List[List[float]]],
        now: float,
    ) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        if not self.pending_food_handoffs:
            return events

        camera_id = packet.get("camera_id")
        persons = [o for o in objects if o.get("class") == "person"]
        foods = [o for o in objects if o.get("class") == "food"]

        for action_id in list(self.pending_food_handoffs.keys()):
            state = self.pending_food_handoffs[action_id]

            if camera_id is not None and state.get("camera_id") != camera_id:
                continue

            if now > state["window_deadline_monotonic"]:
                if not state["confirmed"]:
                    events.append(
                        self._make_event(
                            event_type="SANITARY_VIOLATION",
                            code="left_zone_with_food",
                            action_id=state["action_id"],
                            camera_id=state.get("camera_id"),
                            zone_id=state.get("zone_id"),
                            employee_id=state.get("employee_id"),
                            action_type=state.get("action_type"),
                            timestamp=self._now_iso(),
                            payload={
                                "handoff_food": False,
                            },
                        )
                    )
                del self.pending_food_handoffs[action_id]
                continue

            candidate_found = False

            for person in persons:
                person_track_id = person.get("id")
                if state.get("employee_track_id") is not None and str(person_track_id) == str(state["employee_track_id"]):
                    continue

                person_bbox = self._parse_bbox(person.get("bbox"))
                if not person_bbox:
                    continue

                for food in foods:
                    food_bbox = self._parse_bbox(food.get("bbox"))
                    if not food_bbox:
                        continue

                    if not self._food_near_person(person_bbox, food_bbox):
                        continue

                    if not self._food_near_origin_zone(
                        food_bbox=food_bbox,
                        origin_polygon=state.get("origin_zone_polygon"),
                        origin_food_bboxes=state.get("origin_food_bboxes", []),
                    ):
                        continue

                    candidate_found = True
                    if state["confirm_started_monotonic"] is None:
                        state["confirm_started_monotonic"] = now
                        state["handoff_person_track_id"] = person_track_id
                    elif now - state["confirm_started_monotonic"] >= self.T_handoff_confirm:
                        state["confirmed"] = True
                        events.append(
                            self._make_event(
                                event_type="PROCESS_EVENT",
                                code="food_handoff",
                                action_id=state["action_id"],
                                camera_id=state.get("camera_id"),
                                zone_id=state.get("zone_id"),
                                employee_id=state.get("employee_id"),
                                action_type=state.get("action_type"),
                                timestamp=self._now_iso(),
                                payload={
                                    "handoff_food": True,
                                    "receiver_person_track_id": person_track_id,
                                },
                            )
                        )
                        del self.pending_food_handoffs[action_id]
                    break

                if action_id not in self.pending_food_handoffs:
                    break

            if action_id not in self.pending_food_handoffs:
                continue

            if not candidate_found:
                state["confirm_started_monotonic"] = None
                state["handoff_person_track_id"] = None

        return events

    # ==========================================================
    # ======================== INTRUSION ========================
    # ==========================================================
    def _process_intrusion(
        self,
        packet: Dict[str, Any],
        objects: List[Dict[str, Any]],
        work_zone_polygon: Optional[List[List[float]]],
        now: float,
    ) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        if not work_zone_polygon:
            return events

        action_employee_id = packet.get("employee_id")
        action_id = packet.get("action_id")
        camera_id = packet.get("camera_id")
        zone_id = packet.get("zone_id")
        action_type = packet.get("action_type")

        if not action_employee_id:
            return events

        zone_key = self._polygon_key(work_zone_polygon)
        active_person_keys = set()

        for obj in objects:
            if obj.get("class") != "person":
                continue

            bbox = self._parse_bbox(obj.get("bbox"))
            if not bbox:
                continue

            center = self._bbox_center(bbox)
            if not self._point_in_polygon(center, work_zone_polygon):
                continue

            intruder_employee_id = obj.get("employee_id")
            intruder_track_id = obj.get("id")

            # по ТЗ: посторонний = любой person_track внутри зоны,
            # который не совпадает с employee_id владельца действия
            if intruder_employee_id == action_employee_id:
                continue

            key = (
                str(camera_id),
                str(action_employee_id),
                str(intruder_track_id),
                zone_key,
            )
            active_person_keys.add(key)

            timer = self.intrusion_timers.get(key)
            if timer is None:
                self.intrusion_timers[key] = {
                    "since_monotonic": now,
                    "last_emit_monotonic": None,
                    "action_id": action_id,
                    "camera_id": camera_id,
                    "zone_id": zone_id,
                    "employee_id": action_employee_id,
                    "action_type": action_type,
                    "intruder_track_id": intruder_track_id,
                    "intruder_employee_id": intruder_employee_id,
                }
                continue

            if now - timer["since_monotonic"] >= self.T_intrusion_confirm:
                # чтобы не спамить одинаковыми intrusion на каждом кадре
                last_emit = timer.get("last_emit_monotonic")
                if last_emit is None or (now - last_emit) >= self.T_intrusion_confirm:
                    events.append(
                        self._make_event(
                            event_type="SANITARY_VIOLATION",
                            code="intrusion_work_zone",
                            action_id=timer.get("action_id"),
                            camera_id=timer.get("camera_id"),
                            zone_id=timer.get("zone_id"),
                            employee_id=timer.get("employee_id"),
                            action_type=timer.get("action_type"),
                            timestamp=self._now_iso(),
                            payload={
                                "intruder_track_id": intruder_track_id,
                                "intruder_employee_id": intruder_employee_id,
                            },
                        )
                    )
                    timer["last_emit_monotonic"] = now

        # очистка таймеров intrusion для тех, кого больше нет в зоне
        for key in list(self.intrusion_timers.keys()):
            cam, owner_emp, _, zone_key_saved = key
            if str(camera_id) != cam:
                continue
            if str(action_employee_id) != owner_emp:
                continue
            if zone_key_saved != zone_key:
                continue
            if key not in active_person_keys:
                self.intrusion_timers.pop(key, None)

        return events

    # ==========================================================
    # ========================= HELPERS =========================
    # ==========================================================
    def _make_event(
        self,
        event_type: str,
        code: str,
        action_id: Optional[str],
        camera_id: Optional[str],
        zone_id: Optional[str],
        employee_id: Optional[str],
        action_type: Optional[str],
        timestamp: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "type": event_type,
            "code": code,
            "action_id": action_id,
            "camera_id": camera_id,
            "zone_id": zone_id,
            "employee_id": employee_id,
            "action_type": action_type,
            "timestamp": timestamp,
            "payload": payload or {},
        }

    def _extract_polygon(self, packet: Dict[str, Any]) -> Optional[List[List[float]]]:
        polygon = packet.get("work_zone_polygon")
        if polygon is None:
            return None
        normalized = []
        for pt in polygon:
            if pt is None or len(pt) != 2:
                continue
            normalized.append([float(pt[0]), float(pt[1])])
        return normalized if len(normalized) >= 3 else None

    def _safe_objects(self, objects: Any) -> List[Dict[str, Any]]:
        if not isinstance(objects, list):
            return []
        out: List[Dict[str, Any]] = []
        for obj in objects:
            if isinstance(obj, dict):
                out.append(obj)
        return out

    def _extract_food_bboxes(self, objects: List[Dict[str, Any]]) -> List[List[float]]:
        out = []
        for obj in objects:
            if obj.get("class") == "food":
                bbox = self._parse_bbox(obj.get("bbox"))
                if bbox:
                    out.append(bbox)
        return out

    def _find_person_by_employee_or_best(
        self,
        objects: List[Dict[str, Any]],
        employee_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        persons = [o for o in objects if o.get("class") == "person"]
        if not persons:
            return None

        if employee_id is not None:
            for p in persons:
                if p.get("employee_id") == employee_id:
                    return p

        return max(persons, key=lambda x: float(x.get("confidence", 0.0) or 0.0))

    def _detect_phone_usage(
        self,
        hands: List[Dict[str, Any]],
        phones: List[Dict[str, Any]],
    ) -> bool:
        for hand in hands:
            hand_bbox = self._parse_bbox(hand.get("bbox"))
            if not hand_bbox:
                continue
            for phone in phones:
                phone_bbox = self._parse_bbox(phone.get("bbox"))
                if not phone_bbox:
                    continue
                if self._iou(hand_bbox, phone_bbox) > self.phone_iou_threshold:
                    return True
        return False

    def _detect_face_touch(
        self,
        person_bbox: List[float],
        bare_hands: List[Dict[str, Any]],
    ) -> bool:
        x1, y1, x2, y2 = person_bbox
        height = y2 - y1

        face_region = [
            x1,
            y1,
            x2,
            y1 + height * self.face_region_ratio,
        ]

        for hand in bare_hands:
            hand_bbox = self._parse_bbox(hand.get("bbox"))
            if not hand_bbox:
                continue
            if self._iou(hand_bbox, face_region) > 0.05:
                return True
        return False

    def _food_near_person(self, person_bbox: List[float], food_bbox: List[float]) -> bool:
        if self._iou(person_bbox, food_bbox) > self.bbox_overlap_threshold:
            return True
        person_center = self._bbox_center(person_bbox)
        food_center = self._bbox_center(food_bbox)
        return self._distance(person_center, food_center) <= self.handoff_distance_threshold_px

    def _food_near_origin_zone(
        self,
        food_bbox: List[float],
        origin_polygon: Optional[List[List[float]]],
        origin_food_bboxes: List[List[float]],
    ) -> bool:
        if origin_polygon:
            center = self._bbox_center(food_bbox)
            if self._point_in_polygon(center, origin_polygon):
                return True

        for old_bbox in origin_food_bboxes:
            if self._iou(food_bbox, old_bbox) > self.bbox_overlap_threshold:
                return True
            if self._distance(self._bbox_center(food_bbox), self._bbox_center(old_bbox)) <= self.handoff_distance_threshold_px:
                return True

        return False

    def _bbox_overlap(self, box_a: Any, box_b: Any) -> bool:
        a = self._parse_bbox(box_a)
        b = self._parse_bbox(box_b)
        if not a or not b:
            return False
        return self._iou(a, b) > self.bbox_overlap_threshold

    def _parse_bbox(self, bbox: Any) -> Optional[List[float]]:
        try:
            if bbox is None:
                return None
            if isinstance(bbox, str):
                import json
                bbox = json.loads(bbox)
            vals = [float(v) for v in bbox]
            if len(vals) != 4:
                return None
            x1, y1, x2, y2 = vals
            if x2 <= x1 or y2 <= y1:
                return None
            return vals
        except Exception:
            return None

    def _bbox_center(self, bbox: List[float]) -> Tuple[float, float]:
        return (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0

    def _distance(self, p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
        dx = p1[0] - p2[0]
        dy = p1[1] - p2[1]
        return (dx * dx + dy * dy) ** 0.5

    def _iou(self, box_a: List[float], box_b: List[float]) -> float:
        x_a = max(box_a[0], box_b[0])
        y_a = max(box_a[1], box_b[1])
        x_b = min(box_a[2], box_b[2])
        y_b = min(box_a[3], box_b[3])

        inter_w = max(0.0, x_b - x_a)
        inter_h = max(0.0, y_b - y_a)
        inter = inter_w * inter_h

        area_a = max(0.0, box_a[2] - box_a[0]) * max(0.0, box_a[3] - box_a[1])
        area_b = max(0.0, box_b[2] - box_b[0]) * max(0.0, box_b[3] - box_b[1])

        union = area_a + area_b - inter
        if union <= 0.0:
            return 0.0
        return inter / union

    def _point_in_polygon(
        self,
        point: Tuple[float, float],
        polygon: List[List[float]],
    ) -> bool:
        x, y = point
        inside = False
        n = len(polygon)
        if n < 3:
            return False

        px1, py1 = polygon[0]
        for i in range(n + 1):
            px2, py2 = polygon[i % n]
            if y > min(py1, py2):
                if y <= max(py1, py2):
                    if x <= max(px1, px2):
                        if py1 != py2:
                            xinters = (y - py1) * (px2 - px1) / ((py2 - py1) + 1e-9) + px1
                        else:
                            xinters = px1
                        if px1 == px2 or x <= xinters:
                            inside = not inside
            px1, py1 = px2, py2
        return inside

    def _polygon_key(self, polygon: List[List[float]]) -> str:
        return "|".join(f"{round(p[0], 2)}:{round(p[1], 2)}" for p in polygon)

    def _now_iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
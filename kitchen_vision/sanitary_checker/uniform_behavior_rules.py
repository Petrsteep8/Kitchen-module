import json
import queue
import threading
import time
import uuid
from typing import Optional, Dict
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


class UniformBehaviorRules(threading.Thread):
    def __init__(self, input_queue: queue.Queue, out_queue: queue.Queue):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()

        self.input_queue = input_queue
        self.out_queue = out_queue
        self._T_uniform_confirm_sec = 1.0
        self._employee_confidence_floor = 0.9

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

    def process_pkt(self, pkt):
        if pkt is None:
            return

        if pkt["camera_id"] not in self._known_cameras:
            self._is_no_cap_detected[pkt["camera_id"]] = False
            self._is_no_apron_detected[pkt["camera_id"]] = False
            self._is_no_glove_detected[pkt["camera_id"]] = False
            self._is_face_touch_detected[pkt["camera_id"]] = False
            self._is_phone_usage_detected[pkt["camera_id"]] = False
            self._known_cameras.append(pkt["camera_id"])

        events = self._detect_no_cap(pkt)
        if events is not None and len(events) > 0:
            self._emit_events(events=events)
        # events = self._detect_no_gloves(pkt)
        # if events is not None and len(events) > 0:
        #     self._emit_events(events=events)
        # events = self._detect_no_apron(pkt)
        # if events is not None and len(events) > 0:
        #     self._emit_events(events=events)
        # events = self._detect_phone_usage(pkt)
        # if events is not None and len(events) > 0:
        #     self._emit_events(events=events)

    def _emit_events(self, events):
        if not events:
            return

        import json
        for event in events:
            logger.info("ACTION EVENT JSON:\n%s", json.dumps(event, ensure_ascii=False, indent=2, default=str))
            if self.out_queue is not None:
                try:
                    self.out_queue.put_nowait(event)
                except Exception:
                    try:
                        self.out_queue.put(event, timeout=0.1)
                    except Exception:
                        logger.warning("Failed to push action event to out_queue")

    def _generate_sanitary_event(self,
                                 event_type: str,
                                 zone_id: str,
                                 camera_id: str,
                                 employee_id: str,
                                 track_id: str,
                                 track_lineage_id: Optional[str],
                                 confidence: float
                                 ) -> Dict:

        entity_id = employee_id if employee_id else track_id
        entity_type = "employee" if employee_id else "system"  # или "system" для неизвестного

        sanitary_event = {
            "sanitary_event_id": str(uuid.uuid4()),
            "sanitary_event_type": event_type,
            "severity": SEVERITY_MAP[event_type][0],
            "severity_internal": SEVERITY_MAP[event_type][1],
            "zone_id": zone_id,
            "decision_mode": "decision_pending"
        }

        return {
            "event_id": str(uuid.uuid4()),
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
            "payload": sanitary_event,
        }

    def _detect_no_cap(self, pkt):
        cam_id = pkt.get("cam_id", "default")
        zone_id = pkt.get("zone_id", "")
        facts = pkt["facts"]
        identities = pkt["identities"]

        person = self._get_fact_by_object_class(facts, "person")
        if not person or person["confidence"] is None or person["confidence"] < self._employee_confidence_floor:
            return None

        generated_events = []

        # строим маппинг track_lineage_id -> employee_id из identities
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

        cap = self._get_fact_by_object_class(facts, "cap")
        if cap is None and not self._is_no_cap_detected.get(cam_id, False):
            self._is_no_cap_detected[cam_id] = True
            self._no_cap_detected_timestamp[cam_id] = time.time()
        else:
            if cap is not None and self._is_no_cap_detected.get(cam_id):
                self._is_no_cap_detected[cam_id] = False

        time_since_last_no_cap_violation = time.time() - self._generated_violations_per_employee.get(employee_id, 0)
        violation_time = time.time() - self._no_cap_detected_timestamp.get(cam_id,0)

        if (self._is_no_cap_detected.get(cam_id) and violation_time > self._T_uniform_confirm_sec
                and time_since_last_no_cap_violation > self._T_uniform_confirm_sec * 5):

            event = self._generate_sanitary_event(
                event_type="no_cap",
                zone_id=zone_id,
                camera_id=cam_id,
                employee_id=employee_id,
                confidence=employee_confidence,
                track_id=track_id,
                track_lineage_id=track_linage_id
            )

            self._generated_violations_per_employee[employee_id] = time.time()

            generated_events.append(event)
            self._is_no_cap_detected[cam_id] = False

        return generated_events

    def _detect_no_gloves(self, pkt):
        cam_id = pkt.get("cam_id", "default")
        zone_id = pkt.get("zone_id", "")
        facts = pkt["facts"]
        identities = pkt["identities"]

        person = self._get_fact_by_object_class(facts, "person")
        if not person or person["confidence"] is None or person["confidence"] < self._employee_confidence_floor:
            return None

        generated_events = []

        # строим маппинг track_lineage_id -> employee_id из identities
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
                track_lineage_id=track_linage_id
            )

            self._generated_violations_per_employee[employee_id] = time.time()

            generated_events.append(event)
            self._is_no_glove_detected[cam_id] = False

        return generated_events

    def _detect_no_apron(self, pkt):
        cam_id = pkt.get("cam_id", "default")
        zone_id = pkt.get("zone_id", "")
        facts = pkt["facts"]
        identities = pkt["identities"]

        person = self._get_fact_by_object_class(facts, "person")
        if not person or person["confidence"] is None or person["confidence"] < self._employee_confidence_floor:
            return None

        generated_events = []

        # строим маппинг track_lineage_id -> employee_id из identities
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
                track_lineage_id=track_linage_id
            )

            self._generated_violations_per_employee[employee_id] = time.time()

            generated_events.append(event)
            self._is_no_apron_detected[cam_id] = False

        return generated_events

    def _detect_phone_usage(self, pkt):
        cam_id = pkt.get("cam_id", "")
        zone_id = pkt.get("zone_id", "")
        facts = pkt["facts"]
        identities = pkt["identities"]

        person = self._get_fact_by_object_class(facts, "person")
        if not person or person["confidence"] is None or person["confidence"] < self._employee_confidence_floor:
            return None

        generated_events = []

        # строим маппинг track_lineage_id -> employee_id из identities
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

        phone = self._get_fact_by_object_class(facts, "phone")
        hands = self._get_fact_by_object_class(facts, "hands")
        if phone is None or phone["confidence"] < 0.5 or hands is None or hands["confidence"] < 0.5:
            self._is_phone_usage_detected[cam_id] = False
            return None

        person_bbox = self._parse_bbox(person["bbox"])
        phone_bbox = self._parse_bbox(phone["bbox"])
        hands_bbox = self._parse_bbox(hands["bbox"])
        person_bbox_center = self._calculate_bbox_center(person_bbox)
        hands_bbox_center = self._calculate_bbox_center(hands_bbox)

        if self._iou(hands_bbox, phone_bbox) >= 0.5 > self._calculate_distance(person_bbox_center, hands_bbox_center):
            if not self._is_phone_usage_detected.get(cam_id, False):
                self._is_phone_usage_detected[cam_id] = True
                self._phone_usage_timestamp[cam_id] = time.time()
        else:
            self._is_phone_usage_detected[cam_id] = False

        time_since_last_phone_usage_violation = time.time() - self._generated_violations_per_employee.get(employee_id, 0)
        violation_time = time.time() - self._phone_usage_timestamp.get(cam_id, 0)

        if (self._is_phone_usage_detected.get(cam_id, False) and violation_time > self._T_uniform_confirm_sec
                and time_since_last_phone_usage_violation > self._T_uniform_confirm_sec * 5):

            event = self._generate_sanitary_event(
                event_type="phone_usage",
                zone_id=zone_id,
                camera_id=cam_id,
                employee_id=employee_id,
                confidence=employee_confidence,
                track_id=track_id,
                track_lineage_id=track_linage_id
            )

            generated_events.append(event)
            self._is_phone_usage_detected[cam_id] = False

        return generated_events

    def _calculate_employee_and_stuff_distance(self, employee_id: str, stuff_id: str, data_pkt):
        """
        Вычисляет расстояние в пикселях между центром bbox сотрудника (person) и центром bbox еды (food).

        Параметры:
            employee_id (str): идентификатор сотрудника (из identities) или track_id (если None/"undefined").
            stuff_id (str): track_id факта, соответствующего еде.
            data_pkt (dict): пакет данных камеры (содержит 'facts' и 'identities').

        Возвращает:
            float или None: расстояние в пикселях или None, если не удалось найти bbox.
        """
        facts = data_pkt.get('facts', [])
        identities = data_pkt.get('identities', [])

        person_bbox = None

        # 1. Определяем bbox человека
        if employee_id is not None and employee_id != "undefined":
            # Ищем identity по employee_id
            found_identity = None
            for ident in identities:
                if ident.get('employee_id') == employee_id:
                    found_identity = ident
                    break

            if found_identity:
                lineage_id = found_identity.get('track_lineage_id')
                if lineage_id:
                    # Ищем факт person с таким track_lineage_id
                    for fact in facts:
                        if (fact.get('track_lineage_id') == lineage_id and
                                fact.get('object_class') == 'person'):
                            person_bbox = fact.get('bbox')
                            break
            # Если identity не найден, person_bbox остаётся None
        else:
            # employee_id интерпретируется как track_id (если не None)
            if employee_id is not None:
                for fact in facts:
                    if fact.get('track_id') == employee_id and fact.get('object_class') == 'person':
                        person_bbox = fact.get('bbox')
                        break

        if person_bbox is None:
            return None

        # 2. Определяем bbox еды по track_id
        food_bbox = None
        for fact in facts:
            if fact.get('track_id') == stuff_id:
                food_bbox = fact.get('bbox')
                break

        if food_bbox is None:
            return None

        # 3. Валидация bbox
        person_bbox_valid = self._parse_bbox(person_bbox)
        food_bbox_valid = self._parse_bbox(food_bbox)
        if person_bbox_valid is None or food_bbox_valid is None:
            return None

        # 4. Вычисление центров
        center_person = self.__calculate_bbox_center(person_bbox_valid)
        center_food = self.__calculate_bbox_center(food_bbox_valid)

        # 5. Расстояние Евклида
        distance = self.__calculate_distance(center_person, center_food)
        return distance

    def _parse_bbox(self, bbox):
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

    def _get_fact_by_object_class(self, facts, object_class):
        for fact in facts:
            if fact["object_class"] == object_class:
                return fact
        return None

    def _calculate_bbox_center(self, bbox):
        x1, y1, x2, y2 = bbox
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0

    def _calculate_distance(self, p1, p2):
        return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5

    def _detect_face_touch(self, pkt):
        pass

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

    def __calculate_bbox_center(self, bbox):
        x1, y1, x2, y2 = bbox
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0

    def __calculate_distance(self, p1, p2):
        return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5

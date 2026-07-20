import queue
import threading
import time


class UniformBehaviorRules(threading.Thread):
    def __init__(self, input_queue: queue.Queue):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()

        self._input_queue = input_queue
        self._T_uniform_confirm_sec = 1.0

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

    def run(self):
        while not self.stop_event.is_set():
            try:
                pkt = self._input_queue.get()
                if pkt is not None:
                    self.process_pkt(pkt)
            except queue.Empty:
                continue

    def stop(self):
        self.stop_event.set()

    def process_pkt(self, pkt):
        if pkt is None:
            return

        self._detect_no_cap(pkt)
        self._detect_no_gloves(pkt)
        self._detect_no_apron(pkt)
        self._detect_phone_usage(pkt)
        self._detect_face_touch(pkt)

    def _generate_sanitary_event(self, event_type: str):
        pass

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

    def _detect_no_cap(self, pkt):
        cam_id = pkt.get("cam_id", "default")
        facts = pkt["facts"]

        person = self._get_fact_by_object_class(facts, "person")
        cap = self._get_fact_by_object_class(facts, "cap")
        if person is None or person["confidence"] < 0.4:
            self._is_no_cap_detected[cam_id] = False
            return

        person_bbox = self._parse_bbox(person["bbox"])
        cap_bbox = self._parse_bbox(cap["bbox"])
        if cap is not None and cap["confidence"] > 0.4:
            if self._iou(person_bbox, cap_bbox) == 0:
                self._is_no_cap_detected[cam_id] = True
                self._no_cap_detected_timestamp[cam_id] = time.time()
            else:
                if self._is_no_cap_detected.get(cam_id, False):
                    self._is_no_cap_detected[cam_id] = False
        else:
            if not self._is_no_cap_detected.get(cam_id, False):
                self._is_no_cap_detected[cam_id] = True
                self._no_cap_detected_timestamp[cam_id] = time.time()

        if self._is_no_cap_detected.get(cam_id, False) and time.time() - self._no_cap_detected_timestamp.get(cam_id, 0) > self._T_uniform_confirm_sec:
            self._generate_sanitary_event(event_type="no_cap")
            self._is_no_cap_detected[cam_id] = False
            print("VIOLATION")

    def _detect_no_gloves(self, pkt):
        cam_id = pkt.get("cam_id", "default")
        facts = pkt["facts"]

        person = self._get_fact_by_object_class(facts, "person")
        glove = self._get_fact_by_object_class(facts, "glove")
        if person is None or person["confidence"] < 0.4:
            self._is_no_glove_detected[cam_id] = False
            return

        person_bbox = self._parse_bbox(person["bbox"])
        glove_bbox = self._parse_bbox(glove["bbox"])
        if glove is not None and glove["confidence"] > 0.4:
            if self._iou(person_bbox, glove_bbox) == 0:
                self._is_no_glove_detected[cam_id] = True
                self._no_glove_detected_timestamp[cam_id] = time.time()
            else:
                if self._is_no_glove_detected.get(cam_id, False):
                    self._is_no_glove_detected[cam_id] = False
        else:
            if not self._is_no_glove_detected.get(cam_id, False):
                self._is_no_glove_detected[cam_id] = True
                self._no_glove_detected_timestamp[cam_id] = time.time()

        if self._is_no_glove_detected.get(cam_id, False) and time.time() - self._no_glove_detected_timestamp.get(cam_id, 0) > self._T_uniform_confirm_sec:
            self._generate_sanitary_event(event_type="no_glove")
            self._is_no_glove_detected[cam_id] = False
            print("VIOLATION")

    def _detect_no_apron(self, pkt):
        cam_id = pkt.get("cam_id", "default")
        facts = pkt["facts"]

        person = self._get_fact_by_object_class(facts, "person")
        apron = self._get_fact_by_object_class(facts, "apron")
        if person is None or person["confidence"] < 0.4:
            self._is_no_apron_detected[cam_id] = False
            return

        person_bbox = self._parse_bbox(person["bbox"])
        apron_bbox = self._parse_bbox(apron["bbox"])
        if apron is not None and apron["confidence"] > 0.4:
            if self._iou(person_bbox, apron_bbox) == 0:
                self._is_no_apron_detected[cam_id] = True
                self._no_apron_detected_timestamp[cam_id] = time.time()
            else:
                if self._is_no_apron_detected.get(cam_id, False):
                    self._is_no_apron_detected[cam_id] = False
        else:
            if not self._is_no_apron_detected.get(cam_id, False):
                self._is_no_apron_detected[cam_id] = True
                self._no_apron_detected_timestamp[cam_id] = time.time()

        if self._is_no_apron_detected.get(cam_id, False) and time.time() - self._no_apron_detected_timestamp.get(cam_id, 0) > self._T_uniform_confirm_sec:
            self._generate_sanitary_event(event_type="no_apron")
            self._is_no_apron_detected[cam_id] = False
            print("VIOLATION")

    def _calculate_bbox_center(self, bbox):
        x1, y1, x2, y2 = bbox
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0

    def _calculate_distance(self, p1, p2):
        return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5

    def _detect_phone_usage(self, pkt):
        cam_id = pkt.get("cam_id", "default")
        facts = pkt["facts"]

        person = self._get_fact_by_object_class(facts, "person")
        phone = self._get_fact_by_object_class(facts, "phone")
        hands = self._get_fact_by_object_class(facts, "hands")
        if person is None or person["confidence"] < 0.4 or phone is None or phone["confidence"] < 0.4\
                or hands is None or hands["confidence"] < 0.4:
            self._is_phone_usage_detected[cam_id] = False
            return

        person_bbox = self._parse_bbox(person["bbox"])
        phone_bbox = self._parse_bbox(phone["bbox"])
        hands_bbox = self._parse_bbox(hands["bbox"])
        person_bbox_center = self._calculate_bbox_center(person_bbox)
        hands_bbox_center = self._calculate_bbox_center(hands_bbox)

        if self._iou(hands_bbox, phone_bbox) >= 0.5 \
                and self._calculate_distance(person_bbox_center, hands_bbox_center) < 1.0:
            if not self._is_phone_usage_detected.get(cam_id, False):
                self._is_phone_usage_detected[cam_id] = True
                self._phone_usage_timestamp[cam_id] = time.time()
        else:
            self._is_phone_usage_detected[cam_id] = False

        if self._is_phone_usage_detected.get(cam_id, False) and time.time() - self._phone_usage_timestamp.get(cam_id, 0) > self._T_uniform_confirm_sec:
            self._generate_sanitary_event(event_type="phone_usage")
            self._is_phone_usage_detected[cam_id] = False
            print("VIOLATION")

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
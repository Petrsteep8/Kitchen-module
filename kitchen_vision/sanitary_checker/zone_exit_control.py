import queue
import threading
import time
import uuid
import logging
from typing import Dict, Tuple, Optional, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zone_exit_control")

SEVERITY_MAP = {
    "left_zone_with_tool": ["violation", "critical"],
    "left_zone_with_food": ["violation", "critical"],
}


class ZoneExitControl(threading.Thread):
    def __init__(self, input_action_queue: queue.Queue, input_data_queue: queue.Queue, out_queue: queue.Queue):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()

        self._input_action_queue = input_action_queue
        self._input_data_queue = input_data_queue
        self.out_queue = out_queue

        # Показывает нужно ли перехватывать данные с камеры
        self._camera_capture_data: Dict[str, Tuple[bool, Dict[str, Optional]]] = dict()

        # _data_capture_started = {
        #   "camera_id": {
        #       "start": (employee_id, food_id),
        #       "timestamp_handoff_confirm_window_sec": time.time(),
        #       "timestamp_T_handoff_confirm_sec": time.time(),
        #       "end": ((new)employee_id, food_id)
        #   }
        # }
        self._data_capture_started: Dict[str, Dict[str, Optional]] = dict()

        # Реестр П1
        self._handoff_confirm_window_sec: float = 2.0
        self._T_handoff_confirm_sec: float = 0.6
        self._handoff_radius_m = 3.0
        self._handoff_nearest_margin_m = 0.5

        self._known_cameras = []

    def run(self):
        while not self.stop_event.is_set():
            try:
                # ZoneExitControl срабатывает, когда действие закончилось с end_reason='left_zone', здесь отлавливаем action_end-события с нужным end_reason
                action_pkt = self._input_action_queue.get()
                if action_pkt is not None:
                    end_reason = action_pkt.get("end_reason", None)
                    if end_reason is not None and end_reason == "left_zone":
                        self._init_data_capture(action_pkt=action_pkt)

                # Проверка, есть ли данные с камер наблюдения ml-модуля
                self.process_pkt()
            except queue.Empty:
                self.process_pkt()

    def stop(self):
        self.stop_event.set()

    def process_pkt(self):
        pkt = self._input_data_queue.get()
        if pkt is None:
            return

        if pkt["camera_id"] not in self._known_cameras:
            self._camera_capture_data[pkt["camera_id"]] = (False, {})
            self._known_cameras.append(pkt["camera_id"])

        camera_id = pkt.get("camera_id", None)
        if camera_id is not None and self._camera_capture_data[camera_id][0] == True:
            events = self.update_events(pkt=pkt)
            if events and len(events) > 0:
                self._emit_events(events=events)

    def _emit_events(self, events):
        if not events:
            return

        import json
        for event in events:
            logger.info("SANITARY EVENT JSON:\n%s", json.dumps(event, ensure_ascii=False, indent=2, default=str))
            if self.out_queue is not None:
                try:
                    self.out_queue.put_nowait(event)
                except Exception:
                    try:
                        self.out_queue.put(event, timeout=0.1)
                    except Exception:
                        logger.warning("Failed to push sanitary event to out_queue")

    def update_events(self, pkt) -> List | None:
        camera_id = pkt.get("camera_id", None)
        facts = pkt.get("facts", None)
        identities = pkt.get("identities", None)
        generated_events = []

        if not camera_id or not facts or not identities:
            logger.warning(f"Empty packet, skipping...")
            return None

        # Сначала строим маппинг track_lineage_id -> employee_id из identities
        identity_map: Dict[str, Dict] = {}
        for id_item in identities:
            lineage_id = id_item.get("track_lineage_id")
            if lineage_id:
                identity_map[lineage_id] = {
                    "employee_id": id_item.get("employee_id"),
                    "employee_confidence": id_item.get("employee_confidence", 0.0),
                    "executor_attribution": id_item.get("executor_attribution", "unresolved"),
                }



        return generated_events

    def _calculate_employee_and_food_distance(self, employee_id: str, food_id: str, pkt):
        """
        Вычисляет расстояние в пикселях между центром bbox сотрудника (person) и центром bbox еды (food).

        Параметры:
            employee_id (str): идентификатор сотрудника (из identities) или track_id (если None/"undefined").
            food_id (str): track_id факта, соответствующего еде.
            pkt (dict): пакет данных камеры (содержит 'facts' и 'identities').

        Возвращает:
            float или None: расстояние в пикселях или None, если не удалось найти bbox.
        """
        facts = pkt.get('facts', [])
        identities = pkt.get('identities', [])

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
            if fact.get('track_id') == food_id:
                food_bbox = fact.get('bbox')
                break

        if food_bbox is None:
            return None

        # 3. Валидация bbox
        person_bbox_valid = self.__parse_bbox(person_bbox)
        food_bbox_valid = self.__parse_bbox(food_bbox)
        if person_bbox_valid is None or food_bbox_valid is None:
            return None

        # 4. Вычисление центров
        center_person = self.__calculate_bbox_center(person_bbox_valid)
        center_food = self.__calculate_bbox_center(food_bbox_valid)

        # 5. Расстояние Евклида
        distance = self.__calculate_distance(center_person, center_food)
        return distance

    def _init_data_capture(self, action_pkt):
        camera_id = action_pkt.get("camera_id", None)
        if camera_id:
            logger.info(f"Data capture for camera: {camera_id} started due to zone exit")
            self._camera_capture_data[camera_id] = (True, action_pkt)

    def _create_zone_exit_event(
            self,
            zone_exit_event_type: str,
            zone_id: str,
            employee_id: Optional[str],
            camera_id: str,
            timestamp: int,
            confidence: float,
            track_lineage_id: Optional[str],
            track_id: str = None,

    ) -> Dict:

        entity_id = employee_id if employee_id else track_id
        entity_type = "employee" if employee_id else "system"  # или "system" для неизвестного
        action_id = str(uuid.uuid4())

        sanitary_event = {
            "sanitary_event_id": action_id,
            "sanitary_event_type": zone_exit_event_type,
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
            "payload": sanitary_event,
        }

    def _get_food_id(self, pkt):
        fact = self._get_fact_by_object_class(facts=pkt.get("facts", []), object_class='food')
        if fact is None:
            return None
        food_id = fact.get('track_id', None)
        return food_id

    def _get_employee_id(self, action_pkt, pkt):
        employee_id = action_pkt.get("employee_id", None)
        if employee_id is None or employee_id == "undefined":
            fact = self._get_fact_by_object_class(facts=pkt.get("facts", []), object_class='person')
            if fact is None:
                return None
            employee_id = fact.get('track_id', None)
        return employee_id

    def _get_fact_by_object_class(self, facts, object_class):
        for fact in facts:
            if fact["object_class"] == object_class:
                return fact
        return None

    def _clear_camera_capture_data(self, camera_id: str):
        if camera_id in self._camera_capture_data:
            del self._camera_capture_data[camera_id]

    def _clear_data_capture_started(self, camera_id: str):
        if camera_id in self._data_capture_started:
            del self._data_capture_started[camera_id]

    # Utils bbox
    def __calculate_bbox_center(self, bbox):
        x1, y1, x2, y2 = bbox
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0

    def __calculate_distance(self, p1, p2):
        return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5

    def __parse_bbox(self, bbox):
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

    def __point_in_bbox(self, pt, bbox):
        x, y = pt
        x1, y1, x2, y2 = bbox
        return x1 <= x <= x2 and y1 <= y <= y2

    def __bbox_contains_bbox(self, outer, inner):
        ox1, oy1, ox2, oy2 = outer
        ix1, iy1, ix2, iy2 = inner
        return ox1 <= ix1 and oy1 <= iy1 and ox2 >= ix2 and oy2 >= iy2

    def __iou(self, boxA, boxB):
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

    def __point_in_polygon(self, point, polygon):
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
                            xinters = (y - py1) * (px2 - px1) / (py2 - py1 + 1e-9) + px1
                        else:
                            xinters = px1
                        if px1 == px2 or x <= xinters:
                            inside = not inside
            px1, py1 = px2, py2
        return inside

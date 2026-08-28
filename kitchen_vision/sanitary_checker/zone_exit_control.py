import queue
import threading
import time
import uuid
import logging
from typing import Dict, Tuple, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zone_exit_control")

SEVERITY_MAP = {
    "left_zone_with_tool": ["violation", "critical"],
    "left_zone_with_food": ["violation", "critical"],
}


class ZoneExitControl(threading.Thread):
    def __init__(self, input_action_queue: queue.Queue, input_data_queue: queue.Queue):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()

        self._input_action_queue = input_action_queue
        self._input_data_queue = input_data_queue

        # Показывает нужно ли перехватывать данные с камеры
        self._camera_capture_data: Dict[str, Tuple[bool, Dict[str, Optional]]] = dict()

        # _started_camera_data = {
        #   "camera_id": {
        #       "start": (employee_id, food_id),
        #       "timestamp_handoff_confirm_window_sec": time.time(),
        #       "timestamp_T_handoff_confirm_sec": time.time(),
        #       "end": ((new)employee_id, food_id)
        #   }
        # }
        self._started_camera_data: Dict[str, Dict[str, Optional]] = dict()

        # Реестр П1
        self._handoff_confirm_window_sec: float = 2.0
        self._T_handoff_confirm_sec: float = 0.6
        self._handoff_radius_m = 3.0
        self._handoff_nearest_margin_m = 0.5

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
                self._check_data_queue()
            except queue.Empty:
                self._check_data_queue()

    def stop(self):
        self.stop_event.set()

    def process_pkt(self, data_pkt):
        camera_id = data_pkt.get("camera_id", None)
        if self._started_camera_data[camera_id]:
            pass

        action_pkt = {}
        if camera_id is not None and self._camera_capture_data[camera_id][0] == True:
            action_pkt = self._camera_capture_data[camera_id][1]

        end_reason = action_pkt.get("end_reason", None)
        carrying_type = action_pkt.get("carrying_type", None)
        if end_reason == "left_zone" and carrying_type == "tool":
            observation_state = action_pkt.get("observation_state", "obs_ok")
            if observation_state in ("obs_ok", "obs_weak"):
                sanitary_event = self._generate_sanitary_event(sanitary_event_type="left_zone",
                                                               zone_id=action_pkt.get("zone_id"))
                return sanitary_event
            return None

        if end_reason == "left_zone" and carrying_type == "food":
            employee_id = self._get_employee_id(action_pkt=action_pkt, data_pkt=data_pkt)
            food_id = self._get_food_id(data_pkt=data_pkt)

            # Нужно убедиться, что именно этот работник вышел из зоны с этим блюдом(едой)
            # Так как в кадр может случайно попасть не та еда, что приведёт к потенциально ложному нарушению
            employee_food_distance = self._calculate_employee_and_food_distance(employee_id=employee_id,
                                                                                food_id=food_id, data_pkt=data_pkt)
            if not employee_id or not food_id:
                logger.warning(f"employee id or food id in none, skipping...")
                return None

            if employee_food_distance is not None and employee_food_distance < 0.5:
                self._started_camera_data[camera_id] = {
                    "start": (employee_id, food_id),
                    "timestamp_handoff_confirm_window_sec": time.time(),
                    "timestamp_T_handoff_confirm_sec": None,
                    "end": None
                }

        return None

    def _calculate_employee_and_food_distance(self, employee_id: str, food_id: str, data_pkt):
        """
        Вычисляет расстояние в пикселях между центром bbox сотрудника (person) и центром bbox еды (food).

        Параметры:
            employee_id (str): идентификатор сотрудника (из identities) или track_id (если None/"undefined").
            food_id (str): track_id факта, соответствующего еде.
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

    def _check_data_queue(self):
        data_pkt = self._input_data_queue.get()
        if data_pkt is not None:
            camera_id = data_pkt.get("camera_id", None)
            if camera_id is not None and self._camera_capture_data[camera_id] == True:
                self.process_pkt(data_pkt=data_pkt)

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

        sanitary_event = {
            "sanitary_event_id": str(uuid.uuid4()),
            "sanitary_event_type": sanitary_event_type,
            "severity": SEVERITY_MAP[sanitary_event_type][0],
            "severity_internal": SEVERITY_MAP[sanitary_event_type][1],
            "zone_id": zone_id,
            "decision_mode": "decision_pending"
        }
        return sanitary_event

    def _get_food_id(self, data_pkt):
        fact = self._get_fact_by_object_class(facts=data_pkt.get("facts", []), object_class='food')
        if fact is None:
            return None
        food_id = fact.get('track_id', None)
        return food_id

    def _get_employee_id(self, action_pkt, data_pkt):
        employee_id = action_pkt.get("employee_id", None)
        if employee_id is None or employee_id == "undefined":
            fact = self._get_fact_by_object_class(facts=data_pkt.get("facts", []), object_class='person')
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

    def _clear_started_camera_data(self, camera_id: str):
        if camera_id in self._started_camera_data:
            del self._started_camera_data[camera_id]

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

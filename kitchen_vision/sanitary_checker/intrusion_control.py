import queue
import threading
import time
import logging
import uuid
from typing import Dict, List, Optional, Any, Set
import yaml

try:
    from kitchen_vision.sanitary_checker.fsm.observation_fsm import ObservationFSM
    from kitchen_vision.sanitary_checker.zones.zone_resolver import ZoneResolver  # для работы с zones.yaml
except ImportError:
    class ObservationFSM:
        def get_obs_state(self) -> str:
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
            intrusion_zone_resolver: ZoneResolver,
            observation_fsm: ObservationFSM,
            intrusion_config: Optional[Dict[str, Any]] = None,
    ):
        """
        Args:
            intrusion_zone_resolver: объект для доступа к разметке зон (zones.yaml)
            observation_fsm: объект ObservationFSM для получения состояния наблюдаемости камеры
            intrusion_config: словарь с параметрами:
                - T_intrusion_confirm_sec (float): время подтверждения вторжения, по умолчанию 2.0
                - person_confidence_threshold (float): минимальная уверенность детекции person, по умолчанию 0.8
                - employee_confidence_floor (float): минимальная уверенность идентификации, по умолчанию 0.5
                - allowed_employees_per_zone (Dict[str, Set[str]]): разрешённые employee_id для каждой зоны
        """
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

        # Разрешённые сотрудники для каждой зоны (получаем из DBI или конфига)
        self._allowed_employees: Dict[str, Set[str]] = self._config.get("allowed_employees_per_zone", {})
        # Если не задано, можно загрузить из другого источника (например, из мастер-данных)

        # Состояние вторжений: key = (zone_id, track_lineage_id) или (zone_id, track_id)
        # value = { "first_seen": timestamp, "violation_triggered": bool, "employee_id": str|None }
        self._intrusion_state: Dict[tuple, Dict[str, Any]] = {}

        # Кэш полигонов зон: zone_id -> polygon (для быстрого доступа)
        self._zone_polygons: Dict[str, List] = {}
        self._load_zone_polygons()
        
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
        data_facts = pkt["facts"]
        data_identities = pkt["identities"]
        
        if data_facts and data_identities:
            events = self.update_facts(facts=data_facts, identities=data_identities, timestamp=pkt["timestamp"])
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
    
    def _load_zone_polygons(self) -> None:
        """Загружает полигоны всех зон из zone_resolver."""
        # В реальной реализации zone_resolver может предоставлять список всех зон
        # Для упрощения используем прямой доступ к данным зон (если есть)
        if hasattr(self._zone_resolver, 'zones'):
            for zone in self._zone_resolver.zones:
                zone_id = zone.get("zone_id")
                polygon = zone.get("polygon")
                if zone_id and polygon:
                    self._zone_polygons[zone_id] = polygon

    def _is_inside_zone(self, bbox: List[float], polygon: List[List[float]]) -> bool:
        """
        Проверяет, находится ли объект (по опорной точке – середине нижней грани bbox)
        внутри полигона зоны.
        """
        if not bbox or len(bbox) < 4:
            return False
        # Опорная точка: середина нижней грани (раздел 14)
        x = (bbox[0] + bbox[2]) / 2.0
        y = bbox[3]  # нижняя грань
        return self._point_in_polygon(x, y, polygon)

    def _point_in_polygon(self, x: float, y: float, polygon: List[List[float]]) -> bool:
        """Алгоритм Ray Casting для проверки принадлежности точки полигону."""
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
        """
        Определяет релевантный employee_id для зоны.
        В данном контуре релевантным считается сотрудник, который:
          - имеет подтверждённую идентичность (employee_confidence >= floor)
          - присутствует в разрешённом списке для зоны (или если список пуст – любой)
        Возвращает employee_id или None, если релевантный не определён.
        """
        employee_id = track_data.get("employee_id")
        employee_confidence = track_data.get("employee_confidence", 0.0)

        # Если идентификация отсутствует или низкая – не релевантный
        # TODO подумать надо ли фиксировать нарушение при employee_id=None/undefined
        if not employee_id or employee_confidence < self._employee_confidence_floor:
            return None

        # Если для зоны задан разрешённый список, проверяем вхождение
        allowed_employees = self._allowed_employees.get(zone_id)
        if allowed_employees is not None:
            if employee_id in allowed_employees:
                return employee_id
            else:
                return None  # не разрешён

        # Если список не задан, считаем любого идентифицированного релевантным
        return employee_id

    def update_facts(
            self,
            facts: List[Dict],
            identities: List[Dict],
            timestamp: int,
    ) -> List[Dict]:
        """
        Обрабатывает новые факты (детекции) и идентификации, обновляет состояние
        вторжений и возвращает список сгенерированных SANITARY_EVENT.

        Args:
            facts: список фактов из data.json (каждый содержит track_id, object_class, bbox, confidence, zone_id)
            identities: список идентификаций (track_lineage_id -> employee_id, employee_confidence)
            timestamp: временная метка в мс

        Returns:
            список событий (SANITARY_EVENT) в формате, готовом для отправки в event_aggregator
        """
        generated_events = []

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

        # Обрабатываем каждый факт, относящийся к person
        for fact in facts:
            if fact.get("object_class") != "person":
                continue

            track_id = fact.get("track_id")
            track_lineage_id = fact.get("track_lineage_id")
            confidence = fact.get("confidence", 0.0)
            bbox = fact.get("bbox")
            zone_id = fact.get("zone_id")  # может быть None

            # Проверка минимальной уверенности для person (по условию)
            if confidence < self._person_confidence_threshold:
                continue

            # Если zone_id не определён – игнорируем (зона не распознана)
            if not zone_id:
                continue

            # Получаем полигон зоны (рабочей зоны)
            polygon = self._zone_polygons.get(zone_id)
            if not polygon:
                continue  # зона не загружена или не является рабочей

            # Проверяем, находится ли человек внутри зоны
            if not self._is_inside_zone(bbox, polygon):
                # Человек вне зоны – сбрасываем состояние вторжения для этого трека/зоны
                self._clear_intrusion_state(zone_id, track_id, track_lineage_id)
                continue

            # Получаем идентификацию для этого трека
            identity = identity_map.get(track_lineage_id) if track_lineage_id else None
            employee_id = identity.get("employee_id") if identity else None
            employee_confidence = identity.get("employee_confidence", 0.0) if identity else 0.0

            # Если employee_id отсутствует, считаем это нарушением (неизвестный сотрудник)
            # Если employee_id присутствует, проверяем, разрешён ли он
            relevant_employee = self._get_relevant_employee_id(zone_id, {
                "employee_id": employee_id,
                "employee_confidence": employee_confidence,
            })

            # Если релевантный сотрудник определён и совпадает с текущим employee_id,
            # то вторжения нет – сбрасываем состояние
            if relevant_employee and employee_id == relevant_employee:
                self._clear_intrusion_state(zone_id, track_id, track_lineage_id)
                continue

            # Проверка состояния наблюдаемости камеры
            obs_state = self._observation_fsm.get_obs_state() if self._observation_fsm else "obs_ok"
            if obs_state == OBSERVATION_STATES["obs_lost"]:
                # Если наблюдаемость потеряна, не считаем нарушение
                self._clear_intrusion_state(zone_id, track_id, track_lineage_id)
                continue

            # Теперь мы имеем потенциальное вторжение: сотрудник внутри зоны,
            # но не является релевантным (или не идентифицирован).
            # Проверяем, подтверждено ли присутствие в течение T_intrusion_confirm_sec
            key = (zone_id, track_lineage_id if track_lineage_id else track_id)
            state = self._intrusion_state.get(key)
            current_time = timestamp

            if state is None:
                # Первое появление – фиксируем время
                self._intrusion_state[key] = {
                    "first_seen": current_time,
                    "violation_triggered": False,
                    "employee_id": employee_id,
                    "track_id": track_id,
                }
                continue

            # Обновляем время последнего появления (можно использовать для сброса)
            # Но для простоты оставляем first_seen

            # Если нарушение уже было сгенерировано для этой пары, не дублируем
            if state.get("violation_triggered", False):
                continue

            # Проверяем, прошло ли достаточно времени
            elapsed = current_time - state["first_seen"]
            if elapsed >= self._T_intrusion_confirm_sec:
                # Генерируем SANITARY_EVENT
                event = self._create_intrusion_event(
                    zone_id=zone_id,
                    track_id=track_id,
                    track_lineage_id=track_lineage_id,
                    employee_id=employee_id,
                    camera_id=camera_id,
                    timestamp=timestamp,
                    confidence=confidence,
                )
                generated_events.append(event)

                # Отмечаем, что нарушение уже сгенерировано
                state["violation_triggered"] = True
                logger.info(f"Intrusion detected in zone {zone_id} for track {track_id}")

        return generated_events

    def _clear_intrusion_state(self, zone_id: str, track_id: str, track_lineage_id: Optional[str]) -> None:
        """Очищает состояние вторжения для трека в указанной зоне."""
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
        entity_type = "employee" if employee_id else "system"  # или "system" для неизвестного

        sanitary_event = {
            "sanitary_event_id": str(uuid.uuid4()),
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
            "event_id": str(uuid.uuid4()),
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


# ============================================================================
# Пример использования
# ============================================================================

if __name__ == "__main__":
    t1 = time.time() / 1000
    # Загрузка zones.yaml
    with open("zones.yaml", "r") as f:
        zones_config = yaml.safe_load(f)

    zone_resolver = ZoneResolver(zones_config)
    obs_fsm = ObservationFSM()

    # Разрешённые сотрудники для зон (заглушка)
    allowed = {
        "prep_main": {"emp_007_ivan", "emp_008_petr"},
    }

    config = {
        "T_intrusion_confirm_sec": 2.0,
        "person_confidence_threshold": 0.8,
        "employee_confidence_floor": 0.5,
        "allowed_employees_per_zone": allowed,
    }

    intrusion = IntrusionControl(zone_resolver, obs_fsm, config)

    # Пример данных (из data.json)
    facts = [
        {
            "track_id": "42",
            "track_lineage_id": "global-uuid-1234-5678",
            "object_class": "person",
            "bbox": [150.5, 80.0, 400.2, 700.8],
            "confidence": 0.8912,
            "zone_id": "prep_main",
        }
    ]
    identities = [
        {
            "track_lineage_id": "global-uuid-1234-5678",
            "employee_id": "emp_007_ivan",
            "employee_confidence": 0.95,
        }
    ]
    camera_id = "Kitchen_1"
    timestamp = 1696154312000

    events = intrusion.update_facts(facts, identities, timestamp)
    for ev in events:
        print("Generated event:", ev)

    t2 = time.time() / 1000
    print(t2 - t1)

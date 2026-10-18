import queue
import logging
import threading

import yaml
from typing import Optional, Dict, Any

from kitchen_vision.action_detector import ActionDetector
from kitchen_vision.pattern_analyzer import MotionPatternAnalyzer
from kitchen_vision.sanitary_checker.observation_logic.observation_logic import ObservationLogic
from kitchen_vision.sanitary_checker.fsm.observation_fsm import ObservationFSM
from kitchen_vision.sanitary_checker.uniform_behavior_rules import UniformBehaviorRules
from kitchen_vision.sanitary_checker.intrusion_control import IntrusionControl
from kitchen_vision.sanitary_checker.zone_exit_control import ZoneExitControl

from event_pipeline import EventPipeline

from kitchen_gateway.action_card_cache import ActionCardCache

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("facts_consumer")


# ============================================================================
# ZoneResolver
# ============================================================================

class ZoneResolver:
    def __init__(self, zone_config: Optional[Dict[str, Any]] = None):
        zone_config = zone_config or {}
        self.zones = zone_config.get("zones", [])
        self.calibration = zone_config.get("calibration", {})

    def get_zone(self, zone_id: str) -> Optional[dict]:
        for z in self.zones:
            if z.get("zone_id") == zone_id:
                return z
        return None


OBSERVATION_CONFIG: Dict[str, Any] = {
    "T_obs_restore_sec": 3.0,
    "employee_confidence_floor": 0.5,
    "camera_silence_timeout_sec": 5.0,
    "low_confidence_confirm_sec": 5.0,
}

UNIFORM_BEHAVIOR_CONFIG: Dict[str, Any] = {
    "T_uniform_confirm_sec": 1.0,
    "employee_confidence_floor": 0.9,
    "employee_id_confidence_floor": 0.6,
}

INTRUSION_CONFIG: Dict[str, Any] = {
    "T_intrusion_confirm_sec": 2.0,
    "person_confidence_threshold": 0.8,
    "employee_confidence_floor": 0.5,
    "allowed_employees_per_zone": {
        "prep_main": {"emp_007_ivan", "emp_008_petr"},
    },
}

ZONE_EXIT_CONFIG: Dict[str, Any] = {
    "handoff_confirm_window_sec": 2.0,
    "T_handoff_confirm_sec": 0.6,
    "handoff_radius_m": 3.0,
    "handoff_nearest_margin_m": 0.5,
}

CLIP_CONFIG: Dict[str, Any] = {
    "pre_roll_seconds": 5.0,
    "post_roll_seconds": 5.0,
    "fps": 8.0,
    "output_dir": "./audit_clips",
    "max_frames": 500,
    "clip_for_process_event": False,
    "clip_for_data_quality_obs_lost": False,
}


class FactsConsumer:
    def __init__(
        self,
        cams=None,
        zones_path: str = "zones.yaml",
        config_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        self._cameras = cams or [{"camera_id": "Kitchen_1", "source": 0}]

        overrides = config_overrides or {}
        obs_cfg = {**OBSERVATION_CONFIG, **overrides.get("observation", {})}
        uniform_cfg = {**UNIFORM_BEHAVIOR_CONFIG, **overrides.get("uniform_behavior", {})}
        intrusion_cfg = {**INTRUSION_CONFIG, **overrides.get("intrusion", {})}
        zone_exit_cfg = {**ZONE_EXIT_CONFIG, **overrides.get("zone_exit", {})}
        clip_cfg = {**CLIP_CONFIG, **overrides.get("clip", {})}

        zone_config = self._load_zones(zones_path)
        self._zone_resolver = ZoneResolver(zone_config)

        self._observation_fsm = ObservationFSM(config=obs_cfg)

        # --- Очереди ---
        # Единая очередь событий от всех модулей -> router -> EventPipeline
        self._out_action_queue = queue.Queue(maxsize=64)
        # Очередь на вход EventPipeline (router перенаправляет сюда)
        self._event_pipeline_in_queue = queue.Queue(maxsize=64)

        self._input_obs_logic_queue = queue.Queue(maxsize=64)
        self._input_action_detector_queue = queue.Queue(maxsize=64)
        self._input_uniform_behavior_rules_queue = queue.Queue(maxsize=64)
        self._input_intrusion_control_queue = queue.Queue(maxsize=64)
        self._input_zone_exit_action_queue = queue.Queue(maxsize=64)
        self._input_zone_exit_data_queue = queue.Queue(maxsize=64)

        # --- 1. ObservationLogic ---
        self._obs_logic = ObservationLogic(
            input_queue=self._input_obs_logic_queue,
            out_queue=self._out_action_queue,
            config=obs_cfg,
        )
        self._obs_logic.daemon = True
        self._obs_logic.start()

        # --- 2. ActionDetector ---
        self._analyzer = MotionPatternAnalyzer()
        self._action_card_cache = ActionCardCache()
        self._action_detector = ActionDetector(
            cams=[cam["camera_id"] for cam in self._cameras],
            in_queue=self._input_action_detector_queue,
            out_queue=self._out_action_queue,
            analyser=self._analyzer,
            action_card_cache=self._action_card_cache,
        )
        self._action_detector.daemon = True
        self._action_detector.start()

        # --- 3. UniformBehaviorRules (контур 18) ---
        self._uniform_behavior_rules = UniformBehaviorRules(
            input_queue=self._input_uniform_behavior_rules_queue,
            out_queue=self._out_action_queue,
            intrusion_config=uniform_cfg,
            clip_config=clip_cfg,
        )
        self._uniform_behavior_rules.daemon = True
        self._uniform_behavior_rules.start()

        # --- 4. IntrusionControl (контур 21) ---
        self._intrusion_control = IntrusionControl(
            input_queue=self._input_intrusion_control_queue,
            out_queue=self._out_action_queue,
            intrusion_zone_resolver=self._zone_resolver,
            observation_fsm=self._observation_fsm,
            intrusion_config=intrusion_cfg,
            clip_config=clip_cfg,
        )
        self._intrusion_control.daemon = True
        self._intrusion_control.start()

        # --- 5. ZoneExitControl (контур 20) ---
        self._zone_exit_control = ZoneExitControl(
            input_action_queue=self._input_zone_exit_action_queue,
            input_data_queue=self._input_zone_exit_data_queue,
            out_queue=self._out_action_queue,
            zone_resolver=self._zone_resolver,
            observation_fsm=self._observation_fsm,
            config=zone_exit_cfg,
            clip_config=clip_cfg,
        )
        self._zone_exit_control.daemon = True
        self._zone_exit_control.start()

        # --- 6. Router: tap _out_action_queue -> pipeline + zone_exit ---
        self._router_thread = threading.Thread(target=self._router_loop, daemon=True)
        self._router_thread.start()

        # --- 7. EventPipeline (потребляет из _event_pipeline_in_queue) ---
        self._event_pipeline = EventPipeline(input_queue=self._event_pipeline_in_queue)
        self._event_pipeline.daemon = True
        self._event_pipeline.start()

        logger.info("Facts consumer initialized")

    # ------------------------------------------------------------------------
    # Внешний API
    # ------------------------------------------------------------------------

    def put_facts(self, facts):
        if facts is None:
            return

        # 1. ObservationLogic — обновляем состояние наблюдаемости первым,
        #    чтобы последующие модули видели актуальный obs_state.
        self._safe_put(self._input_obs_logic_queue, facts, "obs_logic")

        # 2. ActionDetector — генерация action_start/action_end
        self._safe_put(self._input_action_detector_queue, facts, "action_detector")

        # 3. UniformBehaviorRules — проверка спецодежды/поведения
        self._safe_put(self._input_uniform_behavior_rules_queue, facts, "uniform_rules")

        # 4. IntrusionControl — проверка вторжений
        self._safe_put(self._input_intrusion_control_queue, facts, "intrusion_control")

        # 5. ZoneExitControl (data) — сканирование окна handoff_food
        self._safe_put(self._input_zone_exit_data_queue, facts, "zone_exit_data")

    def stop(self):
        """Корректная остановка всех модулей (опционально, для тестов)."""
        for mod in (
            self._obs_logic,
            self._action_detector,
            self._uniform_behavior_rules,
            self._intrusion_control,
            self._zone_exit_control,
            self._event_pipeline,
        ):
            try:
                if hasattr(mod, "stop"):
                    mod.stop()
            except Exception as e:
                logger.warning(f"Error stopping {mod}: {e}")

    # ------------------------------------------------------------------------
    # Router: fanout _out_action_queue
    # ------------------------------------------------------------------------

    def _router_loop(self):
        """
        Читает из _out_action_queue:
          - Всегда отправляет в EventPipeline.
          - Если это action_end с end_reason=left_zone — также отправляет
            в ZoneExitControl.input_action_queue.
        """
        while True:
            pkt = self._out_action_queue.get()
            if pkt is None:
                continue

            # Всегда в pipeline
            self._safe_put(self._event_pipeline_in_queue, pkt, "pipeline")

            # Специальная маршрутизация для ZoneExitControl
            if pkt.get("event_type") == "action_end":
                payload = pkt.get("payload") or {}
                if payload.get("end_reason") == "left_zone":
                    action_for_zone_exit = self._normalize_action_for_zone_exit(pkt)
                    if action_for_zone_exit is not None:
                        self._safe_put(
                            self._input_zone_exit_action_queue,
                            action_for_zone_exit,
                            "zone_exit_action",
                        )

    def _normalize_action_for_zone_exit(self, pkt: Dict) -> Optional[Dict]:
        """
        Приводит нормализованный action_end к виду, который ждёт ZoneExitControl.
        ZoneExitControl не требует полной нормализации (25.1) — ему нужны
        конкретные поля из payload.
        """
        payload = pkt.get("payload") or {}
        camera_id = pkt.get("camera_id")
        end_reason = payload.get("end_reason")

        if end_reason != "left_zone" or not camera_id:
            return None

        return {
            "action_id": payload.get("action_id"),
            "employee_id": payload.get("employee_id"),
            "carrying_type": payload.get("carrying_type", "none"),
            "zone_id": payload.get("zone_id"),
            "end_reason": end_reason,
            "camera_id": camera_id,
            "track_lineage_id": pkt.get("track_lineage_id"),
            "track_id": pkt.get("entity_id"),
            "confidence": pkt.get("confidence", 0.0),
            "timestamp": pkt.get("timestamp"),
        }

    # ------------------------------------------------------------------------
    # Утилиты
    # ------------------------------------------------------------------------

    def _load_zones(self, zones_path: str) -> Dict[str, Any]:
        try:
            with open(zones_path, "r") as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            logger.warning(f"zones.yaml not found at {zones_path}, using empty config")
            return {}
        except Exception as e:
            logger.error(f"Failed to load {zones_path}: {e}")
            return {}

    def _safe_put(self, q: queue.Queue, item, name: str) -> None:
        try:
            q.put_nowait(item)
        except queue.Full:
            logger.warning(f"Queue '{name}' is full, dropping packet")


if __name__ == "__main__":
    consumer = FactsConsumer(
        cams=[{"camera_id": "Kitchen_1", "source": 0}],
        zones_path="zones.yaml",
    )
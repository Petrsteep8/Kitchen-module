import json
import queue
import threading
import logging
import time
from typing import Dict, Optional, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("observation_logic")

OBSERVATION_STATES = {
    "OBS_OK": "obs_ok",
    "OBS_WEAK": "obs_weak",
    "OBS_DEGRADED": "obs_degraded",
    "OBS_LOST": "obs_lost",
}

OBS_REASONS = {
    "track_lost": "track_lost",
    "key_object_lost": "key_object_lost",
    "low_confidence": "low_confidence",
    "multiple_data_failures": "multiple_data_failures",
    "restored": "restored",
}

config = {
    "T_obs_restore_sec": 3.0,
    "employee_confidence_floor": 0.5,
    "T_low_confidence_confirm_sec": 5.0,
    "T_multiple_data_failures_sec": 10.0,
}


class ObservationLogic(threading.Thread):
    """
    Модуль отслеживания наблюдаемости камер.
    Генерирует OBS_EVENT при изменении состояния наблюдаемости камеры.
    """

    def __init__(
        self,
        input_queue: queue.Queue,
        out_queue: queue.Queue,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Args:
            input_queue: очередь входящих событий (нормализованные события)
            out_queue: очередь исходящих событий (OBS_EVENT)
            config: словарь с параметрами:
                - T_obs_restore_sec (float): время восстановления, по умолчанию 3.0
                - employee_confidence_floor (float): порог уверенности, по умолчанию 0.5
                - T_low_confidence_confirm_sec (float): время подтверждения low_confidence, по умолчанию 5.0
                - T_multiple_data_failures_sec (float): время без данных до OBS_LOST, по умолчанию 10.0
        """
        super().__init__(daemon=True)
        self.stop_event = threading.Event()

        self._input_queue = input_queue
        self._out_queue = out_queue
        self._config = config or {}

        # Параметры из конфигурации (П1)
        self._T_obs_restore_sec = self._config.get("T_obs_restore_sec", 3.0)
        self._employee_confidence_floor = self._config.get("employee_confidence_floor", 0.5)
        self._T_low_confidence_confirm_sec = self._config.get("T_low_confidence_confirm_sec", 5.0)
        self._T_multiple_data_failures_sec = self._config.get("T_multiple_data_failures_sec", 10.0)

        # Состояние каждой камеры:
        # {
        #   "obs_state": str,           # текущее состояние
        #   "last_event_time": float,   # время последнего полученного события
        #   "last_data_time": float,    # время последнего получения данных (любого события)
        #   "low_confidence_since": float,  # время начала low_confidence (если активно)
        #   "degraded_since": float,    # время начала degraded (если активно)
        #   "restored_ready": bool,     # флаг, что восстановление готово (используется для restored)
        # }
        self._camera_state: Dict[str, Dict[str, Any]] = {}

        logger.info("ObservationLogic initialized")

    def run(self):
        """Основной цикл обработки входящих событий и проверки таймеров."""
        while not self.stop_event.is_set():
            try:
                # Проверяем входящие события
                pkt = self._input_queue.get(timeout=0.1)
                if pkt is not None:
                    self._process_event(pkt)
            except queue.Empty:
                pass

            # Проверяем таймеры для всех камер
            self._check_timers()

    def _get_camera_data(self, camera_id: str) -> Dict[str, Any]:
        """Получить данные камеры, создав при необходимости."""
        if camera_id not in self._camera_state:
            self._camera_state[camera_id] = {
                "obs_state": OBSERVATION_STATES["OBS_OK"],
                "last_event_time": time.time(),
                "last_data_time": time.time(),
                "low_confidence_since": None,
                "degraded_since": None,
                "restored_ready": False,
            }
        return self._camera_state[camera_id]

    def _process_event(self, pkt: Dict[str, Any]) -> None:
        """
        Обрабатывает входящее нормализованное событие.
        Обновляет состояние камеры на основе события.
        """
        camera_id = pkt.get("camera_id")
        if not camera_id:
            logger.warning("Event without camera_id, ignoring")
            return

        data = self._get_camera_data(camera_id)
        current_time = time.time()

        # Обновляем время последнего получения данных
        data["last_data_time"] = current_time

        # Если камера была в OBS_LOST и пришли данные -> готовим восстановление
        if data["obs_state"] == OBSERVATION_STATES["OBS_LOST"]:
            # Начинаем отсчёт восстановления
            if data.get("degraded_since") is None:
                data["degraded_since"] = current_time
                data["restored_ready"] = False
            # Проверяем, прошло ли время восстановления
            if current_time - data["degraded_since"] >= self._T_obs_restore_sec:
                if not data["restored_ready"]:
                    data["restored_ready"] = True
                    self._generate_obs_event(
                        camera_id=camera_id,
                        state=OBSERVATION_STATES["OBS_OK"],
                        reason=OBS_REASONS["restored"],
                    )
                    # Сбрасываем состояние degraded_since
                    data["degraded_since"] = None
                    data["obs_state"] = OBSERVATION_STATES["OBS_OK"]

        # Проверяем employee_confidence (если есть в событии)
        employee_confidence = pkt.get("employee_confidence")
        if employee_confidence is not None:
            if employee_confidence < self._employee_confidence_floor:
                # Если камера в OBS_OK и уверенность низкая -> начинаем отсчёт low_confidence
                if data["obs_state"] == OBSERVATION_STATES["OBS_OK"]:
                    if data["low_confidence_since"] is None:
                        data["low_confidence_since"] = current_time
                    # Проверяем, прошло ли время подтверждения
                    if current_time - data["low_confidence_since"] >= self._T_low_confidence_confirm_sec:
                        # Переход в OBS_WEAK
                        data["obs_state"] = OBSERVATION_STATES["OBS_WEAK"]
                        data["degraded_since"] = current_time
                        self._generate_obs_event(
                            camera_id=camera_id,
                            state=OBSERVATION_STATES["OBS_WEAK"],
                            reason=OBS_REASONS["low_confidence"],
                        )
                        # Сбрасываем low_confidence_since, чтобы не генерировать повторно
                        data["low_confidence_since"] = None
            else:
                # Уверенность восстановилась
                if data["obs_state"] == OBSERVATION_STATES["OBS_WEAK"]:
                    # Если камера в OBS_WEAK и уверенность нормальная -> начинаем восстановление
                    if data["degraded_since"] is None:
                        data["degraded_since"] = current_time
                    # Проверяем, прошло ли время восстановления
                    if current_time - data["degraded_since"] >= self._T_obs_restore_sec:
                        # Переход в OBS_OK
                        data["obs_state"] = OBSERVATION_STATES["OBS_OK"]
                        data["degraded_since"] = None
                        self._generate_obs_event(
                            camera_id=camera_id,
                            state=OBSERVATION_STATES["OBS_OK"],
                            reason=OBS_REASONS["restored"],
                        )
                # Если камера в OBS_OK, просто сбрасываем low_confidence_since
                elif data["obs_state"] == OBSERVATION_STATES["OBS_OK"]:
                    data["low_confidence_since"] = None

    def _check_timers(self) -> None:
        """Проверяет таймеры для всех камер (потеря данных -> OBS_LOST)."""
        current_time = time.time()
        for camera_id, data in list(self._camera_state.items()):
            last_data_time = data.get("last_data_time", current_time)
            elapsed = current_time - last_data_time

            # Проверка на отсутствие данных
            if elapsed >= self._T_multiple_data_failures_sec:
                # Если камера ещё не в OBS_LOST, генерируем событие
                if data["obs_state"] != OBSERVATION_STATES["OBS_LOST"]:
                    data["obs_state"] = OBSERVATION_STATES["OBS_LOST"]
                    data["degraded_since"] = current_time
                    data["restored_ready"] = False
                    self._generate_obs_event(
                        camera_id=camera_id,
                        state=OBSERVATION_STATES["OBS_LOST"],
                        reason=OBS_REASONS["multiple_data_failures"],
                    )

    def _generate_obs_event(
        self,
        camera_id: str,
        state: str,
        reason: str,
    ) -> None:
        import uuid
        import time

        obs_event = {
            "event_id": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),  # в миллисекундах
            "event_type": "obs_event",
            "camera_id": camera_id,
            "entity_id": camera_id,
            "entity_type": "system",
            "confidence": 1.0,
            "time_source": "server_ntp",
            "late": False,
            "track_lineage_id": None,
            "executor_attribution": None,
            "rules_config_version": None,
            "model_versions": None,
            "decision_trace": None,
            "payload": {
                "state": state,
                "reason": reason,
            },
        }

        logger.info(
            f"OBS_EVENT generated: camera={camera_id}, state={state}, reason={reason}"
        )

        try:
            self._out_queue.put(obs_event, block=False)
        except queue.Full:
            logger.error(f"Out queue full, cannot put OBS_EVENT for camera {camera_id}")

    def get_obs_state(self, camera_id: str) -> str:
        data = self._get_camera_data(camera_id)
        return data["obs_state"]

    def stop(self) -> None:

        self.stop_event.set()

import time
from typing import Optional, Dict, Any

OBSERVATION_STATES = {
    "obs_ok": "obs_ok",
    "obs_weak": "obs_weak",
    "obs_degraded": "obs_degraded",
    "obs_lost": "obs_lost",
}

OBS_EVENTS = {
    "track_lost": "track_lost",
    "key_object_lost": "key_object_lost",
    "low_confidence": "low_confidence",
    "multiple_data_failures": "multiple_data_failures",
    "restored": "restored",
}


class ObservationFSM:
    """
    Конечный автомат состояния наблюдаемости камеры

    Таблица переходов:
    current_state | event | condition | next_state
    obs_ok | TRACKING(track_lost) | — | obs_lost
    obs_ok | ACTION_END(key_object_lost) | — | obs_degraded
    obs_ok | OBS_EVENT(low_confidence) | employee_confidence < employee_confidence_floor | obs_weak
    obs_weak | OBS_EVENT(restored) | T_obs_restore_sec выполнено | obs_ok
    obs_degraded | OBS_EVENT(restored) | T_obs_restore_sec выполнено | obs_ok
    ANY | OBS_EVENT(multiple_data_failures) | — | obs_lost
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        self._T_obs_restore_sec = self._config.get("T_obs_restore_sec", 3.0)
        self._employee_confidence_floor = self._config.get("employee_confidence_floor", 0.5)

        self._data: Dict[str, Dict[str, Any]] = {}

        self._obs_states = OBSERVATION_STATES
        self._obs_events = OBS_EVENTS

    def _get_camera_data(self, camera_id: str) -> Dict[str, Any]:
        """Получить данные камеры, создав при необходимости."""
        if camera_id not in self._data:
            self._data[camera_id] = {
                "obs_state": self._obs_states["obs_ok"],
                "last_state_change": time.time(),
                "degraded_since": None,    # время перехода в degraded/weak
                "last_event_timestamp": time.time(),
            }
        return self._data[camera_id]

    def get_obs_state(self, camera_id: str) -> str:
        """
        Получить текущее состояние наблюдаемости камеры.
        Возвращает одно из значений: obs_ok, obs_weak, obs_degraded, obs_lost.
        """
        data = self._get_camera_data(camera_id)
        return data["obs_state"]

    def set_obs_state(self, camera_id: str, new_state: str) -> None:
        """
        Принудительно установить состояние наблюдаемости камеры.
        Используется для внешнего управления (например, при инициализации или форсированном сбросе).
        """
        if new_state not in self._obs_states.values():
            raise ValueError(f"Invalid observation state: {new_state}")

        data = self._get_camera_data(camera_id)
        old_state = data["obs_state"]
        if old_state != new_state:
            data["obs_state"] = new_state
            data["last_state_change"] = time.time()

            # Если переходим в degraded или weak, запоминаем время
            if new_state in (self._obs_states["obs_degraded"], self._obs_states["obs_weak"]):
                data["degraded_since"] = time.time()
            else:
                data["degraded_since"] = None

    def process_event(
        self,
        camera_id: str,
        event: str,
        event_details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Обработка события, влияющего на состояние наблюдаемости камеры.

        Args:
            camera_id: идентификатор камеры
            event: тип события (track_lost, key_object_lost, low_confidence,
                   multiple_data_failures, restored)
            event_details: дополнительные параметры (например, employee_confidence)
        """
        if event not in self._obs_events.values():
            raise ValueError(f"Unknown observation event: {event}")

        data = self._get_camera_data(camera_id)
        current_state = data["obs_state"]

        # Обновляем временную метку последнего события
        data["last_event_timestamp"] = time.time()

        if event == self._obs_events["track_lost"]:
            if current_state == self._obs_states["obs_ok"]:
                self.set_obs_state(camera_id, self._obs_states["obs_lost"])
            return

        if event == self._obs_events["key_object_lost"]:
            if current_state == self._obs_states["obs_ok"]:
                self.set_obs_state(camera_id, self._obs_states["obs_degraded"])
            return

        if event == self._obs_events["low_confidence"]:
            if current_state == self._obs_states["obs_ok"]:
                employee_confidence = event_details.get("employee_confidence", 1.0) if event_details else 1.0
                if employee_confidence < self._employee_confidence_floor:
                    self.set_obs_state(camera_id, self._obs_states["obs_weak"])
            return

        if event == self._obs_events["multiple_data_failures"]:
            self.set_obs_state(camera_id, self._obs_states["obs_lost"])
            return

        if event == self._obs_events["restored"]:
            if current_state in (self._obs_states["obs_weak"], self._obs_states["obs_degraded"]):
                degraded_since = data.get("degraded_since")
                if degraded_since is not None:
                    elapsed = time.time() - degraded_since
                    if elapsed >= self._T_obs_restore_sec:
                        self.set_obs_state(camera_id, self._obs_states["obs_ok"])
                else:
                    self.set_obs_state(camera_id, self._obs_states["obs_ok"])
            return


    def get_data_by_camera_id(self, camera_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get(camera_id)

    def get_last_event_timestamp(self, camera_id: str) -> float:
        data = self._get_camera_data(camera_id)
        return data.get("last_event_timestamp", 0.0)

    def is_state(self, camera_id: str, state: str) -> bool:
        return self.get_obs_state(camera_id) == state


# ============================================================================
# Пример использования
# ============================================================================

if __name__ == "__main__":
    # Создаём FSM с параметрами по умолчанию
    obs_fsm = ObservationFSM({
        "T_obs_restore_sec": 3.0,
        "employee_confidence_floor": 0.5,
    })

    camera_id = "camera_1"

    print(f"Initial state: {obs_fsm.get_obs_state(camera_id)}")

    # Симулируем потерю трека
    obs_fsm.process_event(camera_id, OBS_EVENTS["track_lost"])
    print(f"After track_lost: {obs_fsm.get_obs_state(camera_id)}")  # obs_lost

    # Пытаемся восстановиться (restored) – не должно сработать, т.к. из obs_lost не восстанавливаемся
    obs_fsm.process_event(camera_id, OBS_EVENTS["restored"])
    print(f"After restored (from LOST): {obs_fsm.get_obs_state(camera_id)}")  # всё ещё obs_lost

    # Принудительно сбрасываем в obs_ok (например, после переподключения камеры)
    obs_fsm.set_obs_state(camera_id, OBSERVATION_STATES["obs_ok"])
    print(f"After manual reset to OK: {obs_fsm.get_obs_state(camera_id)}")  # obs_ok

    # Симулируем low_confidence (недостаточная уверенность)
    obs_fsm.process_event(
        camera_id,
        OBS_EVENTS["low_confidence"],
        event_details={"employee_confidence": 0.3}
    )
    print(f"After low_confidence: {obs_fsm.get_obs_state(camera_id)}")  # obs_weak

    # Ждём T_obs_restore_sec (в примере просто ждём 3 секунды)
    import time as t
    t.sleep(3.5)

    # Приходит restored
    obs_fsm.process_event(camera_id, OBS_EVENTS["restored"])
    print(f"After restored (after wait): {obs_fsm.get_obs_state(camera_id)}")  # obs_ok

    # Симулируем множественные сбои данных -> сразу obs_lost
    obs_fsm.process_event(camera_id, OBS_EVENTS["multiple_data_failures"])
    print(f"After multiple_data_failures: {obs_fsm.get_obs_state(camera_id)}")  # obs_lost
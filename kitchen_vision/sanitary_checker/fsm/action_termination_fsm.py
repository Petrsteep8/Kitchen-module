import time
from typing import Optional, Dict, Any

ACTION_TERMINATION_STATES = {
    "action_active": "action_active",
    "action_ended_tracking_lost": "action_ended_tracking_lost",
    "action_ended_left_zone": "action_ended_left_zone",
    "action_ended_key_object_lost": "action_ended_key_object_lost",
    "action_ended_motion_stopped": "action_ended_motion_stopped",
}

ACTION_TERMINATION_EVENTS = {
    "tracking_lost": "tracking_lost",
    "left_zone": "left_zone",
    "key_object_lost": "key_object_lost",
    "motion_stopped": "motion_stopped",
}


class ActionTerminationFSM:
    """
    Конечный автомат завершения действия

    Таблица переходов:
    current_state | event | condition | next_state
    action_active | ACTION_END(tracking_lost) | — | action_ended_tracking_lost
    action_active | ACTION_END(left_zone) | outside zone ≥ T_left_confirm_sec и не tracking_lost | action_ended_left_zone
    action_active | ACTION_END(key_object_lost) | — | action_ended_key_object_lost
    action_active | ACTION_END(motion_stopped) | — | action_ended_motion_stopped
    """

    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}  # key: action_id
        self._states = ACTION_TERMINATION_STATES
        self._events = ACTION_TERMINATION_EVENTS

    def _get_action_data(self, action_id: str) -> Dict[str, Any]:
        """Получить данные действия, создав при необходимости."""
        if action_id not in self._data:
            self._data[action_id] = {
                "state": self._states["action_active"],
                "created_at": time.time(),
                "ended_at": None,
                "end_reason": None,
            }
        return self._data[action_id]

    def get_state(self, action_id: str) -> str:
        data = self._get_action_data(action_id)
        return data["state"]

    def get_end_reason(self, action_id: str) -> Optional[str]:
        data = self._get_action_data(action_id)
        return data.get("end_reason")

    def start_action(self, action_id: str) -> None:
        data = self._get_action_data(action_id)
        data["state"] = self._states["action_active"]
        data["created_at"] = time.time()
        data["ended_at"] = None
        data["end_reason"] = None

    def process_event(self, action_id: str, event: str) -> Optional[str]:
        """
        Обработка события завершения действия.

        Args:
            action_id: идентификатор действия
            event: тип события (tracking_lost, left_zone, key_object_lost, motion_stopped)

        Returns:
            новое состояние или None, если переход не произошёл
        """
        if event not in self._events.values():
            raise ValueError(f"Unknown termination event: {event}")

        data = self._get_action_data(action_id)
        current_state = data["state"]

        # Действие должно быть активным для обработки завершения
        if current_state != self._states["action_active"]:
            # Если действие уже завершено, игнорируем новые события
            return None

        # Определяем новое состояние на основе события
        if event == self._events["tracking_lost"]:
            new_state = self._states["action_ended_tracking_lost"]
        elif event == self._events["left_zone"]:
            # В action_detector уже проверено условие (outside zone ≥ T_left_confirm_sec)
            new_state = self._states["action_ended_left_zone"]
        elif event == self._events["key_object_lost"]:
            new_state = self._states["action_ended_key_object_lost"]
        elif event == self._events["motion_stopped"]:
            new_state = self._states["action_ended_motion_stopped"]
        else:
            return None

        # Применяем переход
        data["state"] = new_state
        data["ended_at"] = time.time()
        data["end_reason"] = event

        return new_state

    def is_action_ended(self, action_id: str) -> bool:
        state = self.get_state(action_id)
        return state != self._states["action_active"]

    def get_data_by_action_id(self, action_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get(action_id)


# ============================================================================
# Пример использования
# ============================================================================

if __name__ == "__main__":
    fsm = ActionTerminationFSM()

    action_id = "action_123"

    # Начало действия
    fsm.start_action(action_id)
    print(f"After start: {fsm.get_state(action_id)}")  # action_active

    # Завершение по tracking_lost
    fsm.process_event(action_id, ACTION_TERMINATION_EVENTS["tracking_lost"])
    print(f"After tracking_lost: {fsm.get_state(action_id)}")  # action_ended_tracking_lost
    print(f"End reason: {fsm.get_end_reason(action_id)}")  # tracking_lost

    # Попытка обработать ещё одно событие (игнорируется, так как действие уже завершено)
    fsm.process_event(action_id, ACTION_TERMINATION_EVENTS["motion_stopped"])
    print(f"After extra event: {fsm.get_state(action_id)}")  # остаётся action_ended_tracking_lost

    # Новое действие
    action_id2 = "action_456"
    fsm.start_action(action_id2)
    fsm.process_event(action_id2, ACTION_TERMINATION_EVENTS["left_zone"])
    print(f"Action 2 after left_zone: {fsm.get_state(action_id2)}")  # action_ended_left_zone
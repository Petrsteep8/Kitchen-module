import time
import uuid
from typing import Dict, Any, Optional

class BehaviorSanitaryRules:
    def __init__(self, config: Dict[str, Any], behavior_types_config: Dict[str, Any]):
        """
        config: основной конфиг с таймерами (config.yaml)
        behavior_types_config: содержимое behavior_violation_types.yaml (п. 19.3)
        """

        self.confirm_windows = {}
        for violation_type in behavior_types_config.get("violation_types", []):

            default_window = 1.0
            if violation_type in ["smoking_in_work_zone", "alcohol_consumption_in_work_zone"]:
                default_window = 1.5
            elif violation_type in ["food_consumption_in_work_zone", "food_consumption_after_handling", "drinking_in_work_zone"]:
                default_window = 0.8
            elif violation_type == "mouth_contact_with_hands_or_objects_in_work_zone":
                default_window = 0.5
                
            window_key = f"T_{violation_type.split('_')[0]}_confirm_sec"
            self.confirm_windows[violation_type] = config.get(window_key, default_window)


        self.states: Dict[tuple, Dict[str, Any]] = {}

    def _build_normalized_event(self, camera_id: str, track_id: str, event_type: str, payload: Dict) -> Dict:
        """Сборка нормализованного события по п. 25.1"""
        return {
            "event_id": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "event_type": event_type,
            "camera_id": camera_id,
            "entity_id": track_id,
            "entity_type": "employee", 
            "confidence": 1.0,
            "payload": payload,
            "time_source": "server_ntp"
        }

    def process_detection(
        self, 
        camera_id: str, 
        track_id: str, 
        zone_id: str, 
        violation_type: str, 
        observation_state: str, 
        glove_present: bool
    ) -> Optional[Dict]:
        

        if observation_state == "OBS_LOST":
            self.states.pop((camera_id, track_id), None)
            return None


        if violation_type not in self.confirm_windows:
            return None

        state_key = (camera_id, track_id)
        current_time = time.time()

        if state_key not in self.states or self.states[state_key]['type'] != violation_type:
            self.states[state_key] = {'type': violation_type, 'start_time': current_time}
            return None

        duration = current_time - self.states[state_key]['start_time']
        confirm_window = self.confirm_windows[violation_type]


        if duration >= confirm_window:
            event = self._generate_sanitary_event(camera_id, track_id, zone_id, violation_type, glove_present)
            del self.states[state_key]  # Сброс после фиксации
            return event
        
        return None

    def _generate_sanitary_event(
        self, camera_id: str, track_id: str, zone_id: str, violation_type: str, glove_present: bool
    ) -> Dict:

        payload = {
            "sanitary_event_id": str(uuid.uuid4()),
            "sanitary_event_type": violation_type,
            "zone_id": zone_id,
            "severity": "warning", 
            "severity_internal": "medium",
            "decision_mode": "SANITARY_VIOLATION_CONFIRMED",
            "target_context": "nonfood_contact"
        }

        required_breaks = []
        if violation_type == "mouth_contact_with_hands_or_objects_in_work_zone":
            required_breaks = ["gloves_changed"] if glove_present else ["hands_washed"]
        elif violation_type in ["food_consumption_in_work_zone", "food_consumption_after_handling"]:
            required_breaks = ["hands_washed", "surface_sanitized"] if not glove_present else ["gloves_changed", "surface_sanitized"]
        elif violation_type == "drinking_in_work_zone":
            required_breaks = ["gloves_changed"] if glove_present else ["hands_washed"]
        elif violation_type == "alcohol_consumption_in_work_zone":
            required_breaks = ["gloves_changed"] if glove_present else ["hands_washed"]
        elif violation_type == "smoking_in_work_zone":
            required_breaks = ["gloves_changed", "surface_sanitized"] if glove_present else ["hands_washed", "surface_sanitized"]

        payload["required_breaks_for_fsm"] = required_breaks

        if violation_type == "alcohol_consumption_in_work_zone":
            payload.update({"severity": "violation", "severity_internal": "critical", "escalation_category": "alcohol_escalation"})
        elif violation_type == "smoking_in_work_zone":
            payload.update({"severity": "violation", "severity_internal": "critical", "escalation_category": "smoking_escalation"})
        elif violation_type == "food_consumption_after_handling":
            payload.update({"severity": "violation", "severity_internal": "high"})

        return self._build_normalized_event(camera_id, track_id, "SANITARY_EVENT", payload)
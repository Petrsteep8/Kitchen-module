import json
import queue
import threading
import logging
import time
import uuid
from typing import Optional, Dict, Any

try:
    from kitchen_vision.sanitary_checker.fsm.fsm_hub import FSMHub
    from kitchen_vision.sanitary_checker.resert_logic.reset_logic import ResetLogic
    from kitchen_vision.sanitary_checker.ttl_logic.ttl_logic import TTLLogic
    from kitchen_vision.sanitary_checker.context_transition_engine.decision_table import DecisionTable
except ImportError:
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("event_pipeline")


ENTITY_TYPES = {
    "employee": "employee",
    "hands": "hands",
    "tool": "tool",
    "container": "container",
    "surface": "surface",
    "zone": "zone",
    "food": "food",
    "action": "action",
    "incident": "incident",
    "system": "system",
}

SANITARY_STATES = {
    "clean_confirmed": "clean_confirmed",
    "clean_degrading": "clean_degrading",
    "unknown": "unknown",
    "contaminated": "contaminated",
}

CONTEXTS = {
    "neutral": "neutral",
    "unknown": "unknown",
    "raw_animal": "raw_animal",
    "raw_plant_unwashed": "raw_plant_unwashed",
    "clean_prep": "clean_prep",
    "ready_to_eat": "ready_to_eat",
    "packaging_dispatch": "packaging_dispatch",
    "waste": "waste",
    "dirty_dishes_containers": "dirty_dishes_containers",
    "cleaning_inventory": "cleaning_inventory",
    "nonfood_contact": "nonfood_contact",
    "dirty_zone": "dirty_zone",
    "clean_zone": "clean_zone",
    "sanitized": "sanitized",
    "contaminated": "contaminated",
    "mixed": "mixed",
    "reset_pending": "reset_pending",
    "temporarily_blocked": "temporarily_blocked",
    "pending_resolution": "pending_resolution",
}

OBSERVATION_STATES = {
    "obs_ok": "obs_ok",
    "obs_weak": "obs_weak",
    "obs_degraded": "obs_degraded",
    "obs_lost": "obs_lost",
}

ACTION_END_REASONS = {
    "tracking_lost": "tracking_lost",
    "left_zone": "left_zone",
    "key_object_lost": "key_object_lost",
    "motion_stopped": "motion_stopped",
}

CONTAMINATION_TYPES = {
    "raw_food": "raw_food",
    "waste": "waste",
    "dirty_surface": "dirty_surface",
    "packaging": "packaging",
    "nonfood_contact": "nonfood_contact",
    "unknown": "unknown",
}

RESET_TYPES = {
    "hands_washed": "hands_washed",
    "gloves_changed": "gloves_changed",
    "gloves_removed": "gloves_removed",
    "gloves_put_on": "gloves_put_on",
    "tool_washed": "tool_washed",
    "clean_tool_taken": "clean_tool_taken",
    "tool_replaced": "tool_replaced",
    "container_replaced_clean": "container_replaced_clean",
    "disposable_container_used": "disposable_container_used",
    "surface_sanitized": "surface_sanitized",
    "entered_reset_zone": "entered_reset_zone",
    "passed_clean_gate": "passed_clean_gate",
    "shift_started": "shift_started",
    "returned_to_kitchen": "returned_to_kitchen",
}

ZONE_TYPES = {
    "dirty_zone": "dirty_zone",
    "prep_zone": "prep_zone",
    "clean_zone": "clean_zone",
    "packaging_zone": "packaging_zone",
    "reset_zone": "reset_zone",
    "waste_zone": "waste_zone",
    "wash_zone": "wash_zone",
    "neutral_zone": "neutral_zone",
    "clean_gate": "clean_gate",
}

EVENT_TYPES = {
    "detection": "detection",
    "tracking": "tracking",
    "zone_enter": "zone_enter",
    "zone_exit": "zone_exit",
    "interaction": "interaction",
    "action_start": "action_start",
    "action_end": "action_end",
    "reset_event": "reset_event",
    "contamination_event": "contamination_event",
    "obs_event": "obs_event",
    "data_quality_event": "data_quality_event",
    "sanitary_event": "sanitary_event",
    "process_event": "process_event",
    "incident": "incident",
}

DECISION_MODES = {
    "sanitary_violation_confirmed": "sanitary_violation_confirmed",
    "process_event_confirmed": "process_event_confirmed",
    "unresolved_due_to_data_quality": "unresolved_due_to_data_quality",
    "data_quality_event_confirmed": "data_quality_event_confirmed",
    "no_event": "no_event",
}

ACTION_END_REASONS_LIST = ["tracking_lost", "left_zone", "key_object_lost", "motion_stopped"]

CARRY_TYPES = ["none", "food", "tool"]

SEVERITY_INTERNAL = ["critical", "high", "medium", "low", "info", "warning", "violation"]

SEVERITY_EXTERNAL = ["info", "warning", "violation"]

ESCALATION_CATEGORIES = ["alcohol_escalation", "smoking_escalation"]

INCOMPATIBLE_CAUSES = [
    "missing_hands_washed",
    "missing_gloves_change",
    "missing_surface_sanitized",
    "missing_tool_reset",
    "missing_container_reset",
    "forbidden_zone_transition",
    "contaminated_source_contact",
    "unknown_source_state",
    "behavioral_block_active",
]

PROCESS_EVENT_TYPES = ["food_handoff", "action_interruption", "zone_risk_score"]

HYGIENE_ATTRIBUTES = ["gloves", "cap", "apron", "mask", "hairnet"]

# Итоговые решения (строка)
DECISION_MODE_VALUES = list(DECISION_MODES.keys())

SEVERITY_MAPPING = {
    "critical": "violation",
    "high": "violation",
    "violation": "violation",
    "medium": "warning",
    "low": "warning",
    "warning": "warning",
    "info": "info",
}


class EventPipeline(threading.Thread):
    def __init__(self, input_queue: queue.Queue):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self._input_queue = input_queue

        self._event_types = EVENT_TYPES
        self._process_event_types = PROCESS_EVENT_TYPES
        self._reset_types = RESET_TYPES
        self._contamination_types = CONTAMINATION_TYPES
        self._entity_types = ENTITY_TYPES
        self._sanitary_states = SANITARY_STATES
        self._contexts = CONTEXTS
        self._observation_states = OBSERVATION_STATES
        self._action_end_reasons = ACTION_END_REASONS
        self._zone_types = ZONE_TYPES
        self._decision_modes = DECISION_MODES
        self._incompatible_causes = INCOMPATIBLE_CAUSES
        self._severity_internal = SEVERITY_INTERNAL
        self._severity_external = SEVERITY_EXTERNAL
        self._escalation_categories = ESCALATION_CATEGORIES
        self._carry_types = CARRY_TYPES
        self._hygiene_attributes = HYGIENE_ATTRIBUTES

        try:
            self._fsm_hub = FSMHub()
            self._reset_logic = ResetLogic(fsm_hub=self._fsm_hub) or None
            self._ttl_logic = TTLLogic(fsm_hub=self._fsm_hub) or None
            self._decision_table = DecisionTable(fsm_hub=self._fsm_hub) or None
        except NameError:
            self._fsm_hub = None
            self._reset_logic = None
            self._ttl_logic = None

    def run(self):
        while not self.stop_event.is_set():
            try:
                pkt = self._input_queue.get()
                if pkt is not None:
                    print(pkt)
            except queue.Empty:
                continue

    def _validate_entity_type(self, entity_type: str) -> bool:
        return entity_type in self._entity_types

    def _validate_event_type(self, event_type: str) -> bool:
        return event_type in self._event_types

    def _validate_observation_state(self, state: str) -> bool:
        return state in self._observation_states

    def _validate_severity_internal(self, severity: str) -> bool:
        return severity in self._severity_internal

    def _get_entity_id_for_event(self, event: Dict, event_type: str, camera_id: str) -> Optional[str]:
        try:
            if event_type == self._event_types["detection"]:
                return str(event.get("track_id"))

            elif event_type == self._event_types["tracking"]:
                return str(event.get("track_id"))

            elif event_type in (self._event_types["zone_enter"], self._event_types["zone_exit"]):
                return str(event.get("track_id"))

            elif event_type == self._event_types["interaction"]:
                return str(event.get("target_entity_id"))

            elif event_type in (self._event_types["action_start"], self._event_types["action_end"]):
                return str(event.get("action_id"))

            elif event_type == self._event_types["reset_event"]:
                # Для reset_event entity_id берется из zone_id (раздел 25.9) #TODO
                return str(event.get("zone_id"))

            elif event_type == self._event_types["contamination_event"]:
                return str(event.get("track_id"))

            elif event_type == self._event_types["obs_event"]:
                return camera_id

            elif event_type == self._event_types["data_quality_event"]:
                return str(event.get("data_quality_event_id"))

            elif event_type == self._event_types["sanitary_event"]:
                return str(event.get("sanitary_event_id"))

            elif event_type == self._event_types["process_event"]:
                return str(event.get("process_event_id"))

            elif event_type == self._event_types["incident"]:
                return str(event.get("incident_id"))

            return None
        except (KeyError, AttributeError):
            return None

    def _get_entity_type_for_event(self, event: Dict, event_type: str) -> Optional[str]:
        try:
            if event_type == self._event_types["detection"]:
                # entity_type берется из object_class
                obj_class = event.get("object_class")
                if obj_class in self._entity_types:
                    return obj_class
                # Если класс не в каноническом словаре, возвращаем "system"
                return "system"

            elif event_type == self._event_types["tracking"]:
                return "system"

            elif event_type in (self._event_types["zone_enter"], self._event_types["zone_exit"]):
                return "system"

            elif event_type == self._event_types["interaction"]:
                target_type = event.get("target_entity_type")
                if target_type in self._entity_types:
                    return target_type
                return None

            elif event_type in (self._event_types["action_start"], self._event_types["action_end"]):
                return "action"

            elif event_type == self._event_types["reset_event"]:
                # entity_type - список типов сущностей из target_entities
                target_entities = event.get("target_entities", [])
                if isinstance(target_entities, list) and target_entities:
                    # Возвращаем первый тип как основной, все типы сохраняются в payload
                    first_type = target_entities[0]
                    return first_type if first_type in self._entity_types else "system"
                return "system"

            elif event_type == self._event_types["contamination_event"]:
                contamination_type = event.get("contamination_type")
                target_entity = event.get("target_entity", "system")

                # Определение entity_type на основе contamination_type и target_entity
                if contamination_type == "nonfood_contact":
                    if target_entity in ("employee", "hands"):
                        return target_entity
                elif contamination_type in ("raw_food", "waste"):
                    if target_entity in ("hands", "tool", "container", "surface"):
                        return target_entity
                elif contamination_type == "dirty_surface":
                    if target_entity in ("tool", "container", "surface"):
                        return target_entity

                # Fallback
                return target_entity if target_entity in self._entity_types else "system"

            elif event_type == self._event_types["obs_event"]:
                return "system"

            elif event_type == self._event_types["data_quality_event"]:
                return "system"

            elif event_type == self._event_types["sanitary_event"]:
                return "employee"

            elif event_type == self._event_types["process_event"]:
                process_type = event.get("process_event_type")
                if process_type == "food_handoff":
                    return "employee"
                elif process_type == "action_interruption":
                    return "action"
                elif process_type == "zone_risk_score":
                    return "zone"
                return "system"

            elif event_type == self._event_types["incident"]:
                # incident - внутренний объект, сериализуется как SANITARY/PROCESS/DATA_QUALITY
                return "incident"

            return "system"
        except (KeyError, AttributeError):
            return None

    def normalise_event(self, event: Dict, event_type: str, camera_id: str) -> Optional[Dict[str, Any]]:
        """
        Нормализация события

        Args:
            event: сырое событие (payload)
            event_type: тип события из EVENT_TYPES
            camera_id: идентификатор камеры

        Returns:
            Нормализованное событие или None при ошибке валидации
        """
        # Валидация типа события
        if not self._validate_event_type(event_type):
            logger.warning(f"Invalid event_type: {event_type}")
            return None

        # Базовые поля - верны для всех событий
        normalized_event = {
            "event_id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "event_type": event_type,
            "camera_id": camera_id,
            "payload": event.copy(),
        }

        entity_id = self._get_entity_id_for_event(event, event_type, camera_id)
        entity_type = self._get_entity_type_for_event(event, event_type)

        if entity_id is None:
            logger.warning(f"Cannot determine entity_id for event_type: {event_type}")
            return None

        if entity_type is None:
            logger.warning(f"Cannot determine entity_type for event_type: {event_type}")
            return None

        normalized_event["entity_id"] = str(entity_id)
        normalized_event["entity_type"] = entity_type

        normalized_event["confidence"] = float(event.get("confidence", 0.0))

        time_source = event.get("time_source", "server_ntp")
        if time_source not in ("camera_rtsp", "server_ntp"):
            time_source = "server_ntp"
        normalized_event["time_source"] = time_source

        if "late" in event:
            normalized_event["late"] = bool(event["late"])

        if "track_lineage_id" in event:
            normalized_event["track_lineage_id"] = str(event["track_lineage_id"])

        if "executor_attribution" in event:
            normalized_event["executor_attribution"] = event["executor_attribution"]

        if "rules_config_version" in event:
            normalized_event["rules_config_version"] = str(event["rules_config_version"])

        if "model_versions" in event:
            normalized_event["model_versions"] = event["model_versions"]

        if "decision_trace" in event:
            normalized_event["decision_trace"] = event["decision_trace"]

        # Специфичные поля для каждого типа события
        if event_type == self._event_types["detection"]:
            # DETECTION: track_id, object_class, bbox, zone_id
            if "bbox" not in event:
                logger.warning("DETECTION event missing bbox")
                return None
            normalized_event["payload"] = {
                "track_id": str(event.get("track_id")),
                "object_class": event.get("object_class", "unknown"),
                "bbox": event.get("bbox", [0, 0, 0, 0]),
                "zone_id": event.get("zone_id"),
            }

        elif event_type == self._event_types["tracking"]:
            # TRACKING: track_id, tracking_state, last_zone_id, last_seen_ts
            tracking_state = event.get("tracking_state", "active")
            if tracking_state not in ("active", "lost", "reidentified"):
                tracking_state = "active"
            normalized_event["payload"] = {
                "track_id": str(event.get("track_id")),
                "tracking_state": tracking_state,
                "last_zone_id": event.get("last_zone_id"),
                "last_seen_ts": event.get("last_seen_ts"),
            }

        elif event_type == self._event_types["zone_enter"]:
            # ZONE_ENTER: zone_id, zone_type, dwell_sec
            zone_type = event.get("zone_type")
            if zone_type not in self._zone_types:
                zone_type = "neutral_zone"
            normalized_event["payload"] = {
                "zone_id": str(event.get("zone_id")),
                "zone_type": zone_type,
                "dwell_sec": float(event.get("dwell_sec", 0.0)),
            }

        elif event_type == self._event_types["zone_exit"]:
            # ZONE_EXIT: zone_id, zone_type, outside_duration_sec, confirmed
            zone_type = event.get("zone_type")
            if zone_type not in self._zone_types:
                zone_type = "neutral_zone"
            normalized_event["payload"] = {
                "zone_id": str(event.get("zone_id")),
                "zone_type": zone_type,
                "outside_duration_sec": float(event.get("outside_duration_sec", 0.0)),
                "confirmed": bool(event.get("confirmed", False)),
            }

        elif event_type == self._event_types["interaction"]:
            # INTERACTION: actor_entity_id, actor_entity_type, target_entity_id, target_entity_type, interaction_type, zone_id
            interaction_type = event.get("interaction_type", "contact")
            if interaction_type not in ("contact", "hold", "place", "carry", "touch", "transfer"):
                interaction_type = "contact"
            normalized_event["payload"] = {
                "actor_entity_id": str(event.get("actor_entity_id")),
                "actor_entity_type": event.get("actor_entity_type", "system"),
                "target_entity_id": str(event.get("target_entity_id")),
                "target_entity_type": event.get("target_entity_type", "system"),
                "interaction_type": interaction_type,
                "zone_id": event.get("zone_id"),
            }

        elif event_type == self._event_types["action_start"]:
            # ACTION_START: action_id, employee_id, action_type, object_class, zone_id
            normalized_event["payload"] = {
                "action_id": str(event.get("action_id")),
                "employee_id": event.get("employee_id"),
                "action_type": event.get("action_type", "unknown"),
                "object_class": event.get("object_class", "unknown"),
                "zone_id": event.get("zone_id"),
            }

        elif event_type == self._event_types["action_end"]:
            # ACTION_END: action_id, employee_id, action_type, object_class, zone_id, duration, end_reason, employee_confidence, carrying_type, carrying_confidence
            end_reason = event.get("end_reason", "motion_stopped")
            if end_reason not in ACTION_END_REASONS_LIST:
                end_reason = "motion_stopped"
            carrying_type = event.get("carrying_type", "none")
            if carrying_type not in self._carry_types:
                carrying_type = "none"
            normalized_event["payload"] = {
                "action_id": str(event.get("action_id")),
                "employee_id": event.get("employee_id"),
                "action_type": event.get("action_type", "unknown"),
                "object_class": event.get("object_class", "unknown"),
                "zone_id": event.get("zone_id"),
                "duration": float(event.get("duration", 0.0)),
                "end_reason": end_reason,
                "employee_confidence": float(event.get("employee_confidence", 0.0)),
                "carrying_type": carrying_type,
                "carrying_confidence": float(event.get("carrying_confidence", 0.0)),
            }

        elif event_type == self._event_types["reset_event"]:
            # RESET_EVENT: reset_type, target_entities, zone_id, confidence
            reset_type = event.get("reset_type")
            if reset_type not in self._reset_types:
                logger.warning(f"Invalid reset_type: {reset_type}")
                return None
            target_entities = event.get("target_entities", [])
            if not isinstance(target_entities, list):
                target_entities = [target_entities] if target_entities else []
            # Валидация target_entities
            valid_targets = [t for t in target_entities if t in self._entity_types]
            normalized_event["payload"] = {
                "reset_type": reset_type,
                "target_entities": valid_targets or ["system"],
                "zone_id": event.get("zone_id"),
                "confidence": float(event.get("confidence", 0.0)),
            }

        elif event_type == self._event_types["contamination_event"]:
            # CONTAMINATION_EVENT: contamination_type, target_entity, source_entity, zone_id
            contamination_type = event.get("contamination_type")
            if contamination_type not in self._contamination_types:
                contamination_type = "unknown"
            normalized_event["payload"] = {
                "contamination_type": contamination_type,
                "target_entity": event.get("target_entity", "system"),
                "source_entity": event.get("source_entity"),
                "zone_id": event.get("zone_id"),
            }

        elif event_type == self._event_types["obs_event"]:
            # OBS_EVENT: state, reason
            state = event.get("state", "obs_ok")
            if state not in self._observation_states:
                state = "obs_ok"
            reason = event.get("reason", "restored")
            if reason not in ("track_lost", "key_object_lost", "low_confidence", "multiple_data_failures", "restored"):
                reason = "restored"
            normalized_event["payload"] = {
                "state": state,
                "reason": reason,
            }

        elif event_type == self._event_types["data_quality_event"]:
            # DATA_QUALITY_EVENT: data_quality_event_id, reason, action_id, employee_id, observation_state, suppressed_events
            observation_state = event.get("observation_state", "obs_ok")
            if observation_state not in self._observation_states:
                observation_state = "obs_ok"
            normalized_event["payload"] = {
                "data_quality_event_id": str(event.get("data_quality_event_id")),
                "reason": event.get("reason", "unknown"),
                "action_id": event.get("action_id"),
                "employee_id": event.get("employee_id"),
                "observation_state": observation_state,
                "suppressed_events": event.get("suppressed_events", []),
            }

        elif event_type == self._event_types["sanitary_event"]:
            # SANITARY_EVENT: sanitary_event_id, sanitary_event_type, source_context, target_context, severity, severity_internal, escalation_category, incompatibility_cause, rule_id, zone_id, employee_id, decision_mode
            severity_internal = event.get("severity_internal", "warning")
            if severity_internal not in self._severity_internal:
                severity_internal = "warning"
            severity = SEVERITY_MAPPING.get(severity_internal, "warning")
            decision_mode = event.get("decision_mode", "SANITARY_VIOLATION_CONFIRMED")
            if decision_mode not in DECISION_MODE_VALUES:
                decision_mode = "SANITARY_VIOLATION_CONFIRMED"
            escalation_category = event.get("escalation_category")
            if escalation_category and escalation_category not in self._escalation_categories:
                escalation_category = None
            incompatibility_cause = event.get("incompatibility_cause")
            if incompatibility_cause and incompatibility_cause not in self._incompatible_causes:
                incompatibility_cause = None
            normalized_event["payload"] = {
                "sanitary_event_id": str(event.get("sanitary_event_id")),
                "sanitary_event_type": event.get("sanitary_event_type", "unknown"),
                "source_context": event.get("source_context"),
                "target_context": event.get("target_context"),
                "severity": severity,
                "severity_internal": severity_internal,
                "escalation_category": escalation_category,
                "incompatibility_cause": incompatibility_cause,
                "rule_id": event.get("rule_id"),
                "zone_id": event.get("zone_id"),
                "employee_id": event.get("employee_id"),
                "decision_mode": decision_mode,
            }

        elif event_type == self._event_types["process_event"]:
            # PROCESS_EVENT: process_event_id, process_event_type
            process_type = event.get("process_event_type")
            if process_type not in self._process_event_types:
                process_type = "food_handoff"
            normalized_event["payload"] = {
                "process_event_id": str(event.get("process_event_id")),
                "process_event_type": process_type,
            }

        elif event_type == self._event_types["incident"]:
            # INCIDENT: incident_id, employee_id, zone_id, incompatibility_cause, rule_id, source_context, target_context, decision_mode, score, level, supporting_events
            decision_mode = event.get("decision_mode", "NO_EVENT")
            if decision_mode not in DECISION_MODE_VALUES:
                decision_mode = "NO_EVENT"
            incompatibility_cause = event.get("incompatibility_cause")
            if incompatibility_cause and incompatibility_cause not in self._incompatible_causes:
                incompatibility_cause = None
            normalized_event["payload"] = {
                "incident_id": str(event.get("incident_id")),
                "employee_id": event.get("employee_id"),
                "zone_id": event.get("zone_id"),
                "incompatibility_cause": incompatibility_cause,
                "rule_id": event.get("rule_id"),
                "source_context": event.get("source_context"),
                "target_context": event.get("target_context"),
                "decision_mode": decision_mode,
                "score": event.get("score"),
                "level": event.get("level"),
                "supporting_events": event.get("supporting_events", []),
            }

        return normalized_event

    def process_event(self, pkt: Dict, event_type: str, camera_id: str) -> None:
        """
        Обработка события через конвейер
        1. NORMALIZE
        2. APPLY_EVENT_EFFECT
        3. APPLY_RESET
        4. APPLY_TTL
        5. APPLY_OBS
        6. FSM_TRANSITION
        7. DECISION
        8. INCIDENT
        """
        # Шаг 1: NORMALIZE
        normalized_event = self.normalise_event(event=pkt, event_type=event_type, camera_id=camera_id)
        if normalized_event is None:
            logger.warning("Event normalization failed, skipping...")
            return

        print(json.dumps(normalized_event, indent=4))

        # Шаг 2: APPLY_EVENT_EFFECT -> context_transition_engine
        if event_type in (self._event_types["interaction"], self._event_types["contamination_event"]):
            if self._decision_table is not None:
                generated_sanitary_events = self._decision_table.process_event(
                    normalized_event=normalized_event,
                    camera_id=camera_id,
                )
                for sanitary_event in generated_sanitary_events:
                    print(f"Generated SANITARY_EVENT: {sanitary_event}")
            print("Apply event effects -> context_transition_engine")

        reset_result_for_ttl = []
        # Шаг 3: APPLY_RESET -> reset_logic
        if event_type == self._event_types["reset_event"]:
            if self._reset_logic is not None:
                reset_result_for_ttl = self._reset_logic.process_reset_event(pkt=normalized_event)

        # Шаг 4: APPLY_TTL -> ttl_logic
        if event_type not in (self._event_types["obs_event"], self._event_types["data_quality_event"]):
            if self._ttl_logic is not None:
                self._ttl_logic.process_ttl_event(
                    pkt=normalized_event,
                    reset_result_for_ttl=reset_result_for_ttl
                )

    def stop(self) -> None:
        self.stop_event.set()


# ============================================================================
# Тесты
# ============================================================================

import unittest


class TestEventPipeline(unittest.TestCase):
    """Тесты для EventPipeline"""

    def setUp(self):
        self.pipeline = EventPipeline(queue.Queue())

    def test_normalise_detection(self):
        """Тест нормализации DETECTION события (25.2)"""
        event = {
            "track_id": "track_123",
            "object_class": "employee",
            "bbox": [100, 200, 300, 400],
            "zone_id": "zone_1",
            "confidence": 0.85,
            "time_source": "camera_rtsp",
        }
        result = self.pipeline.normalise_event(event, "detection", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "detection")
        self.assertEqual(result["entity_id"], "track_123")
        self.assertEqual(result["entity_type"], "employee")
        self.assertEqual(result["confidence"], 0.85)
        self.assertEqual(result["payload"]["bbox"], [100, 200, 300, 400])

    def test_normalise_tracking(self):
        """Тест нормализации TRACKING события (25.3)"""
        event = {
            "track_id": "track_123",
            "tracking_state": "active",
            "last_zone_id": "zone_1",
            "last_seen_ts": 1234567890,
            "confidence": 0.9,
            "time_source": "server_ntp",
        }
        result = self.pipeline.normalise_event(event, "tracking", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "tracking")
        self.assertEqual(result["entity_id"], "track_123")
        self.assertEqual(result["entity_type"], "system")
        self.assertEqual(result["payload"]["tracking_state"], "active")

    def test_normalise_zone_enter(self):
        """Тест нормализации ZONE_ENTER события (25.4)"""
        event = {
            "track_id": "track_123",
            "zone_id": "zone_1",
            "zone_type": "prep_zone",
            "dwell_sec": 2.5,
            "confidence": 0.8,
            "time_source": "server_ntp",
        }
        result = self.pipeline.normalise_event(event, "zone_enter", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "zone_enter")
        self.assertEqual(result["entity_id"], "track_123")
        self.assertEqual(result["payload"]["zone_type"], "prep_zone")
        self.assertEqual(result["payload"]["dwell_sec"], 2.5)

    def test_normalise_zone_exit(self):
        """Тест нормализации ZONE_EXIT события (25.5)"""
        event = {
            "track_id": "track_123",
            "zone_id": "zone_1",
            "zone_type": "prep_zone",
            "outside_duration_sec": 3.0,
            "confirmed": True,
            "confidence": 0.85,
            "time_source": "server_ntp",
        }
        result = self.pipeline.normalise_event(event, "zone_exit", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "zone_exit")
        self.assertEqual(result["payload"]["confirmed"], True)

    def test_normalise_interaction(self):
        """Тест нормализации INTERACTION события (25.6)"""
        event = {
            "actor_entity_id": "track_123",
            "actor_entity_type": "employee",
            "target_entity_id": "track_456",
            "target_entity_type": "hands",
            "interaction_type": "contact",
            "zone_id": "zone_1",
            "confidence": 0.75,
            "time_source": "server_ntp",
        }
        result = self.pipeline.normalise_event(event, "interaction", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "interaction")
        self.assertEqual(result["entity_id"], "track_456")
        self.assertEqual(result["entity_type"], "hands")
        self.assertEqual(result["payload"]["interaction_type"], "contact")

    def test_normalise_action_start(self):
        """Тест нормализации ACTION_START события (25.7)"""
        event = {
            "action_id": "act_123",
            "employee_id": "emp_1",
            "action_type": "cut",
            "object_class": "food",
            "zone_id": "zone_1",
            "confidence": 0.8,
            "time_source": "server_ntp",
        }
        result = self.pipeline.normalise_event(event, "action_start", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "action_start")
        self.assertEqual(result["entity_id"], "act_123")
        self.assertEqual(result["entity_type"], "action")
        self.assertEqual(result["payload"]["action_type"], "cut")

    def test_normalise_action_end(self):
        """Тест нормализации ACTION_END события (25.8)"""
        event = {
            "action_id": "act_123",
            "employee_id": "emp_1",
            "action_type": "cut",
            "object_class": "food",
            "zone_id": "zone_1",
            "duration": 5.0,
            "end_reason": "left_zone",
            "employee_confidence": 0.8,
            "carrying_type": "tool",
            "carrying_confidence": 0.7,
            "confidence": 0.85,
            "time_source": "server_ntp",
        }
        result = self.pipeline.normalise_event(event, "action_end", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "action_end")
        self.assertEqual(result["payload"]["end_reason"], "left_zone")
        self.assertEqual(result["payload"]["carrying_type"], "tool")

    def test_normalise_reset_event(self):
        """Тест нормализации RESET_EVENT события (25.9)"""
        event = {
            "reset_type": "hands_washed",
            "target_entities": ["hands", "employee"],
            "zone_id": "zone_1",
            "confidence": 0.7,
            "time_source": "server_ntp",
        }
        result = self.pipeline.normalise_event(event, "reset_event", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "reset_event")
        self.assertEqual(result["entity_id"], "zone_1")
        self.assertEqual(result["entity_type"], "hands")  # первый из target_entities
        self.assertEqual(result["payload"]["reset_type"], "hands_washed")
        self.assertEqual(result["payload"]["target_entities"], ["hands", "employee"])

    def test_normalise_contamination_event(self):
        """Тест нормализации CONTAMINATION_EVENT события (25.10)"""
        event = {
            "track_id": "track_123",
            "contamination_type": "raw_food",
            "target_entity": "hands",
            "source_entity": "food",
            "zone_id": "zone_1",
            "confidence": 0.8,
            "time_source": "server_ntp",
        }
        result = self.pipeline.normalise_event(event, "contamination_event", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "contamination_event")
        self.assertEqual(result["entity_id"], "track_123")
        self.assertEqual(result["payload"]["contamination_type"], "raw_food")

    def test_normalise_obs_event(self):
        """Тест нормализации OBS_EVENT события (25.11)"""
        event = {
            "state": "obs_lost",
            "reason": "track_lost",
            "confidence": 0.0,
            "time_source": "server_ntp",
        }
        result = self.pipeline.normalise_event(event, "obs_event", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "obs_event")
        self.assertEqual(result["entity_id"], "camera_1")
        self.assertEqual(result["entity_type"], "system")
        self.assertEqual(result["payload"]["state"], "obs_lost")

    def test_normalise_data_quality_event(self):
        """Тест нормализации DATA_QUALITY_EVENT события (25.12)"""
        event = {
            "data_quality_event_id": "dq_123",
            "reason": "tracking_lost_during_zone_exit",
            "action_id": "act_123",
            "employee_id": "emp_1",
            "observation_state": "obs_lost",
            "suppressed_events": ["sanitary_event_1"],
            "confidence": 0.0,
            "time_source": "server_ntp",
        }
        result = self.pipeline.normalise_event(event, "data_quality_event", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "data_quality_event")
        self.assertEqual(result["entity_id"], "dq_123")
        self.assertEqual(result["entity_type"], "system")
        self.assertEqual(result["payload"]["observation_state"], "obs_lost")

    def test_normalise_sanitary_event(self):
        """Тест нормализации SANITARY_EVENT события (25.13)"""
        event = {
            "sanitary_event_id": "san_123",
            "sanitary_event_type": "incompatible_transition_detected",
            "source_context": "raw_animal",
            "target_context": "ready_to_eat",
            "severity_internal": "critical",
            "escalation_category": "alcohol_escalation",
            "incompatibility_cause": "missing_hands_washed",
            "rule_id": "rule_1",
            "zone_id": "zone_1",
            "employee_id": "emp_1",
            "decision_mode": "SANITARY_VIOLATION_CONFIRMED",
            "confidence": 0.85,
            "time_source": "server_ntp",
        }
        result = self.pipeline.normalise_event(event, "sanitary_event", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "sanitary_event")
        self.assertEqual(result["entity_id"], "san_123")
        self.assertEqual(result["entity_type"], "employee")
        self.assertEqual(result["payload"]["severity"], "violation")
        self.assertEqual(result["payload"]["severity_internal"], "critical")
        self.assertEqual(result["payload"]["escalation_category"], "alcohol_escalation")
        self.assertEqual(result["payload"]["incompatibility_cause"], "missing_hands_washed")

    def test_normalise_process_event(self):
        """Тест нормализации PROCESS_EVENT события (25.14)"""
        event = {
            "process_event_id": "proc_123",
            "process_event_type": "food_handoff",
            "confidence": 0.8,
            "time_source": "server_ntp",
        }
        result = self.pipeline.normalise_event(event, "process_event", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "process_event")
        self.assertEqual(result["entity_id"], "proc_123")
        self.assertEqual(result["entity_type"], "employee")
        self.assertEqual(result["payload"]["process_event_type"], "food_handoff")

    def test_normalise_incident(self):
        """Тест нормализации INCIDENT события (25.15)"""
        event = {
            "incident_id": "inc_123",
            "employee_id": "emp_1",
            "zone_id": "zone_1",
            "incompatibility_cause": "missing_hands_washed",
            "rule_id": "rule_1",
            "source_context": "raw_animal",
            "target_context": "ready_to_eat",
            "decision_mode": "sanitary_violation_confirmed",
            "score": 25,
            "level": "violation",
            "supporting_events": ["evt_1", "evt_2"],
            "confidence": 0.85,
            "time_source": "server_ntp",
        }
        result = self.pipeline.normalise_event(event, "incident", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "incident")
        self.assertEqual(result["entity_id"], "inc_123")
        self.assertEqual(result["entity_type"], "incident")
        self.assertEqual(result["payload"]["decision_mode"], "sanitary_violation_confirmed")

    def test_normalise_invalid_event_type(self):
        """Тест обработки невалидного типа события"""
        event = {"confidence": 0.5}
        result = self.pipeline.normalise_event(event, "invalid_type", "camera_1")
        self.assertIsNone(result)

    def test_normalise_missing_entity_id(self):
        """Тест обработки события без entity_id"""
        event = {"confidence": 0.5, "time_source": "server_ntp"}
        result = self.pipeline.normalise_event(event, "detection", "camera_1")
        self.assertIsNone(result)

    def test_normalise_reset_event_invalid_type(self):
        """Тест RESET_EVENT с невалидным reset_type"""
        event = {
            "reset_type": "invalid_reset",
            "target_entities": ["hands"],
            "zone_id": "zone_1",
            "confidence": 0.7,
            "time_source": "server_ntp",
        }
        result = self.pipeline.normalise_event(event, "reset_event", "camera_1")
        self.assertIsNone(result)

    def test_normalise_with_track_lineage_id(self):
        """Тест с полем track_lineage_id"""
        event = {
            "track_id": "track_123",
            "object_class": "person",
            "bbox": [100, 200, 300, 400],
            "zone_id": "zone_1",
            "confidence": 0.85,
            "time_source": "camera_rtsp",
            "track_lineage_id": "lineage_456",
            "executor_attribution": "confirmed",
        }
        result = self.pipeline.normalise_event(event, "detection", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["track_lineage_id"], "lineage_456")
        self.assertEqual(result["executor_attribution"], "confirmed")

    def test_normalise_with_model_versions(self):
        """Тест с полем model_versions"""
        event = {
            "track_id": "track_123",
            "object_class": "person",
            "bbox": [100, 200, 300, 400],
            "zone_id": "zone_1",
            "confidence": 0.85,
            "time_source": "camera_rtsp",
            "model_versions": {"detector": "yolov8s_v1", "tracker": "bytetrack_v2"},
        }
        result = self.pipeline.normalise_event(event, "detection", "camera_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["model_versions"]["detector"], "yolov8s_v1")


def run_tests():
    unittest.main(argv=[''], exit=False)


if __name__ == "__main__":
    run_tests()

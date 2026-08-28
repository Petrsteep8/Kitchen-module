zone_enter = {
    "event_id": "a1b2c3d4-5678-90ab-cdef-111111111111",
    "timestamp": 1696154312000,
    "event_type": "zone_enter",
    "camera_id": "Kitchen_1",
    "entity_id": "zone_prep_main",
    "entity_type": "system",
    "confidence": 0.9200,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": "global-uuid-1234-5678",
    "executor_attribution": "confirmed",
    "rules_config_version": "rules-v2.4.0-abc123",
    "model_versions": {
        "detector": "yolov8s+glove@1696000000",
        "tracker": "bytetrack-supervision",
        "code_version": "ai_perception-2.1.0"
    },
    "decision_trace": None,
    "payload": {
        "zone_id": "zone_prep_main",
        "zone_type": "prep_zone",
        "dwell_sec": 2.5
    }
}

zone_exit = {
    "event_id": "b2c3d4e5-6789-01ab-cdef-222222222222",
    "timestamp": 1696154320000,
    "event_type": "zone_exit",
    "camera_id": "Kitchen_1",
    "entity_id": "zone_prep_main",
    "entity_type": "system",
    "confidence": 0.9100,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": "global-uuid-1234-5678",
    "executor_attribution": "confirmed",
    "rules_config_version": "rules-v2.4.0-abc123",
    "model_versions": {
        "detector": "yolov8s+glove@1696000000",
        "tracker": "bytetrack-supervision",
        "code_version": "ai_perception-2.1.0"
    },
    "decision_trace": None,
    "payload": {
        "zone_id": "zone_prep_main",
        "zone_type": "prep_zone",
        "outside_duration_sec": 3.0,
        "confirmed": True
    }
}

interaction = {
    "event_id": "c3d4e5f6-7890-12ab-cdef-333333333333",
    "timestamp": 1696154330000,
    "event_type": "interaction",
    "camera_id": "Kitchen_1",
    "entity_id": "track_42",
    "entity_type": "employee",
    "confidence": 0.8900,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": "global-uuid-1234-5678",
    "executor_attribution": "confirmed",
    "rules_config_version": "rules-v2.4.0-abc123",
    "model_versions": {
        "detector": "yolov8s+glove@1696000000",
        "tracker": "bytetrack-supervision",
        "code_version": "ai_perception-2.1.0"
    },
    "decision_trace": None,
    "payload": {
        "actor_entity_id": "track_42",
        "actor_entity_type": "employee",
        "target_entity_id": "track_43",
        "target_entity_type": "hands",
        "interaction_type": "contact",
        "zone_id": "zone_prep_main"
    }
}

action_start = {
    "event_id": "d4e5f6g7-8901-23ab-cdef-444444444444",
    "timestamp": 1696154340000,
    "event_type": "action_start",
    "camera_id": "Kitchen_1",
    "entity_id": "act_123",
    "entity_type": "action",
    "confidence": 0.8500,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": "global-uuid-1234-5678",
    "executor_attribution": "confirmed",
    "rules_config_version": "rules-v2.4.0-abc123",
    "model_versions": {
        "detector": "yolov8s+glove@1696000000",
        "tracker": "bytetrack-supervision",
        "code_version": "ai_perception-2.1.0"
    },
    "decision_trace": None,
    "payload": {
        "action_id": "act_123",
        "employee_id": "emp_007_ivan",
        "action_type": "cut",
        "object_class": "food",
        "zone_id": "zone_prep_main"
    }
}

action_end = {
    "event_id": "e5f6g7h8-9012-34ab-cdef-555555555555",
    "timestamp": 1696154360000,
    "event_type": "action_end",
    "camera_id": "Kitchen_1",
    "entity_id": "act_123",
    "entity_type": "action",
    "confidence": 0.8800,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": "global-uuid-1234-5678",
    "executor_attribution": "confirmed",
    "rules_config_version": "rules-v2.4.0-abc123",
    "model_versions": {
        "detector": "yolov8s+glove@1696000000",
        "tracker": "bytetrack-supervision",
        "code_version": "ai_perception-2.1.0"
    },
    "decision_trace": None,
    "payload": {
        "action_id": "act_123",
        "employee_id": "emp_007_ivan",
        "action_type": "cut",
        "object_class": "food",
        "zone_id": "zone_prep_main",
        "duration": 5.2,
        "end_reason": "motion_stopped",
        "employee_confidence": 0.8500,
        "carrying_type": "tool",
        "carrying_confidence": 0.7600
    }
}

reset_event = {
    "event_id": "f6g7h8i9-0123-45ab-cdef-666666666666",
    "timestamp": 1696154370000,
    "event_type": "reset_event",
    "camera_id": "Kitchen_1",
    "entity_id": "zone_wash_station",
    "entity_type": "hands",
    "confidence": 0.8200,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": "global-uuid-1234-5678",
    "executor_attribution": "confirmed",
    "rules_config_version": "rules-v2.4.0-abc123",
    "model_versions": {
        "detector": "yolov8s+glove@1696000000",
        "tracker": "bytetrack-supervision",
        "code_version": "ai_perception-2.1.0"
    },
    "decision_trace": None,
    "payload": {
        "reset_type": "hands_washed",
        "target_entities": [{"hands": 12}, {"employee": 456}],
        "zone_id": "zone_wash_station",
        "confidence": 0.8200
    }
}

contamination_event = {
    "event_id": "g7h8i9j0-1234-56ab-cdef-777777777777",
    "timestamp": 1696154380000,
    "event_type": "contamination_event",
    "camera_id": "Kitchen_1",
    "entity_id": "track_43",
    "entity_type": "hands",
    "confidence": 0.8900,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": "global-uuid-9999-8888",
    "executor_attribution": "confirmed",
    "rules_config_version": "rules-v2.4.0-abc123",
    "model_versions": {
        "detector": "yolov8s+glove@1696000000",
        "tracker": "bytetrack-supervision",
        "code_version": "ai_perception-2.1.0"
    },
    "decision_trace": None,
    "payload": {
        "contamination_type": "raw_food",
        "target_entity": "hands",
        "source_entity": "food",
        "zone_id": "zone_prep_main"
    }
}

obs_event = {
    "event_id": "h8i9j0k1-2345-67ab-cdef-888888888888",
    "timestamp": 1696154390000,
    "event_type": "obs_event",
    "camera_id": "Kitchen_1",
    "entity_id": "Kitchen_1",
    "entity_type": "system",
    "confidence": 1.0000,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": None,
    "executor_attribution": None,
    "rules_config_version": None,
    "model_versions": None,
    "decision_trace": None,
    "payload": {
        "state": "obs_lost",
        "reason": "track_lost"
    }
}

data_quality_event = {
    "event_id": "i9j0k1l2-3456-78ab-cdef-999999999999",
    "timestamp": 1696154400000,
    "event_type": "data_quality_event",
    "camera_id": "Kitchen_1",
    "entity_id": "dq_123",
    "entity_type": "system",
    "confidence": 0.0000,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": "global-uuid-1234-5678",
    "executor_attribution": "unresolved",
    "rules_config_version": "rules-v2.4.0-abc123",
    "model_versions": {
        "detector": "yolov8s+glove@1696000000",
        "tracker": "bytetrack-supervision",
        "code_version": "ai_perception-2.1.0"
    },
    "decision_trace": {
        "fact_ids": ["a1b2c3d4-5678-90ab-cdef-111111111111"],
        "rule_id": "11",
        "conditions": "end_reason=tracking_lost",
        "rules_config_version": "rules-v2.4.0-abc123",
        "observation_state": "obs_lost"
    },
    "payload": {
        "data_quality_event_id": "dq_123",
        "reason": "tracking_lost_during_zone_exit",
        "action_id": "act_123",
        "employee_id": "emp_007_ivan",
        "observation_state": "obs_lost",
        "suppressed_events": ["sanitary_event_1"]
    }
}

sanitary_event_incompatible_transition_detected = {
    "event_id": "j0k1l2m3-4567-89ab-cdef-aaaaaaaaaaaa",
    "timestamp": 1696154410000,
    "event_type": "sanitary_event",
    "camera_id": "Kitchen_1",
    "entity_id": "san_123",
    "entity_type": "employee",
    "confidence": 0.8500,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": "global-uuid-1234-5678",
    "executor_attribution": "confirmed",
    "rules_config_version": "rules-v2.4.0-abc123",
    "model_versions": {
        "detector": "yolov8s+glove@1696000000",
        "tracker": "bytetrack-supervision",
        "code_version": "ai_perception-2.1.0"
    },
    "decision_trace": {
        "fact_ids": ["a1b2c3d4-5678-90ab-cdef-111111111111"],
        "rule_id": "1",
        "conditions": "переход из forbidden_predecessor в ready_to_eat, observation_state=OBS_OK, reset отсутствует",
        "rules_config_version": "rules-v2.4.0-abc123",
        "observation_state": "obs_ok"
    },
    "payload": {
        "sanitary_event_id": "san_123",
        "sanitary_event_type": "incompatible_transition_detected",
        "source_context": "raw_animal",
        "target_context": "ready_to_eat",
        "severity": "violation",
        "severity_internal": "critical",
        "escalation_category": None,
        "incompatibility_cause": "missing_hands_washed",
        "rule_id": "1",
        "zone_id": "zone_prep_main",
        "employee_id": "emp_007_ivan",
        "decision_mode": "decision_pending"
    }
}

sanitary_event_left_zone_with_food = {
    "event_id": "k1l2m3n4-5678-90ab-cdef-bbbbbbbbbbbb",
    "timestamp": 1696154420000,
    "event_type": "sanitary_event",
    "camera_id": "Kitchen_1",
    "entity_id": "san_124",
    "entity_type": "employee",
    "confidence": 0.8200,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": "global-uuid-1234-5678",
    "executor_attribution": "confirmed",
    "rules_config_version": "rules-v2.4.0-abc123",
    "model_versions": {
        "detector": "yolov8s+glove@1696000000",
        "tracker": "bytetrack-supervision",
        "code_version": "ai_perception-2.1.0"
    },
    "decision_trace": {
        "fact_ids": ["b2c3d4e5-6789-01ab-cdef-222222222222"],
        "rule_id": "8",
        "conditions": "end_reason=left_zone, carrying_type=food, handoff_food=False по истечении окна",
        "rules_config_version": "rules-v2.4.0-abc123",
        "observation_state": "obs_ok"
    },
    "payload": {
        "sanitary_event_id": "san_124",
        "sanitary_event_type": "left_zone_with_food",
        "source_context": None,
        "target_context": None,
        "severity": "violation",
        "severity_internal": "critical",
        "escalation_category": None,
        "incompatibility_cause": None,
        "rule_id": "8",
        "zone_id": "zone_prep_main",
        "employee_id": "emp_007_ivan",
        "decision_mode": "decision_pending"
    }
}

sanitary_event_smoking_in_work_zone = {
    "event_id": "l2m3n4o5-6789-01ab-cdef-cccccccccccc",
    "timestamp": 1696154430000,
    "event_type": "sanitary_event",
    "camera_id": "Kitchen_1",
    "entity_id": "san_125",
    "entity_type": "employee",
    "confidence": 0.9000,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": "global-uuid-1234-5678",
    "executor_attribution": "unresolved",
    "rules_config_version": "rules-v2.4.0-abc123",
    "model_versions": {
        "detector": "yolov8s+glove@1696000000",
        "tracker": "bytetrack-supervision",
        "code_version": "ai_perception-2.1.0"
    },
    "decision_trace": {
        "fact_ids": ["c3d4e5f6-7890-12ab-cdef-333333333333"],
        "rule_id": "16",
        "conditions": "подтверждённое поведенческое нарушение smoking_in_work_zone",
        "rules_config_version": "rules-v2.4.0-abc123",
        "observation_state": "obs_ok"
    },
    "payload": {
        "sanitary_event_id": "san_125",
        "sanitary_event_type": "smoking_in_work_zone",
        "source_context": None,
        "target_context": None,
        "severity": "violation",
        "severity_internal": "critical",
        "escalation_category": "smoking_escalation",
        "incompatibility_cause": None,
        "rule_id": "16",
        "zone_id": "zone_prep_main",
        "employee_id": None,
        "decision_mode": "decision_pending"
    }
}

sanitary_event_no_cap = {
    "event_id": "m3n4o5p6-7890-12ab-cdef-dddddddddddd",
    "timestamp": 1696154440000,
    "event_type": "sanitary_event",
    "camera_id": "Kitchen_1",
    "entity_id": "san_126",
    "entity_type": "employee",
    "confidence": 0.7800,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": "global-uuid-1234-5678",
    "executor_attribution": "confirmed",
    "rules_config_version": "rules-v2.4.0-abc123",
    "model_versions": {
        "detector": "yolov8s+glove@1696000000",
        "tracker": "bytetrack-supervision",
        "code_version": "ai_perception-2.1.0"
    },
    "decision_trace": {
        "fact_ids": ["d4e5f6g7-8901-23ab-cdef-444444444444"],
        "rule_id": "17",
        "conditions": "подтверждённое no_cap, observation_state=OBS_OK",
        "rules_config_version": "rules-v2.4.0-abc123",
        "observation_state": "obs_ok"
    },
    "payload": {
        "sanitary_event_id": "san_126",
        "sanitary_event_type": "no_cap",
        "source_context": None,
        "target_context": None,
        "severity": "warning",
        "severity_internal": "warning",
        "escalation_category": None,
        "incompatibility_cause": None,
        "rule_id": "17",
        "zone_id": "zone_prep_main",
        "employee_id": "emp_007_ivan",
        "decision_mode": "decision_pending"
    }
}

process_event_food_handoff = {
    "event_id": "n4o5p6q7-8901-23ab-cdef-eeeeeeeeeeee",
    "timestamp": 1696154450000,
    "event_type": "process_event",
    "camera_id": "Kitchen_1",
    "entity_id": "proc_123",
    "entity_type": "employee",
    "confidence": 0.8500,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": "global-uuid-1234-5678",
    "executor_attribution": "confirmed",
    "rules_config_version": "rules-v2.4.0-abc123",
    "model_versions": {
        "detector": "yolov8s+glove@1696000000",
        "tracker": "bytetrack-supervision",
        "code_version": "ai_perception-2.1.0"
    },
    "decision_trace": {
        "fact_ids": ["e5f6g7h8-9012-34ab-cdef-555555555555"],
        "rule_id": "9",
        "conditions": "end_reason=left_zone, carrying_type=food, handoff_food=true",
        "rules_config_version": "rules-v2.4.0-abc123",
        "observation_state": "obs_ok"
    },
    "payload": {
        "process_event_id": "proc_123",
        "process_event_type": "food_handoff",
        "decision_mode": "PROCESS_EVENT_CONFIRMED"
    }
}

process_event_action_interruption = {
    "event_id": "o5p6q7r8-9012-34ab-cdef-ffffffffffff",
    "timestamp": 1696154460000,
    "event_type": "process_event",
    "camera_id": "Kitchen_1",
    "entity_id": "proc_124",
    "entity_type": "action",
    "confidence": 0.8200,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": "global-uuid-1234-5678",
    "executor_attribution": "confirmed",
    "rules_config_version": "rules-v2.4.0-abc123",
    "model_versions": {
        "detector": "yolov8s+glove@1696000000",
        "tracker": "bytetrack-supervision",
        "code_version": "ai_perception-2.1.0"
    },
    "decision_trace": {
        "fact_ids": ["f6g7h8i9-0123-45ab-cdef-666666666666"],
        "rule_id": "18",
        "conditions": "ACTION_INTERRUPTION подтверждён по правилам раздела 33.9",
        "rules_config_version": "rules-v2.4.0-abc123",
        "observation_state": "obs_ok"
    },
    "payload": {
        "process_event_id": "proc_124",
        "process_event_type": "action_interruption",
        "decision_mode": "PROCESS_EVENT_CONFIRMED"
    }
}

process_event_zone_risk_score = {
    "event_id": "p6q7r8s9-0123-45ab-cdef-gggggggggggg",
    "timestamp": 1696154470000,
    "event_type": "process_event",
    "camera_id": "Kitchen_1",
    "entity_id": "proc_125",
    "entity_type": "zone",
    "confidence": 0.7000,
    "time_source": "server_ntp",
    "late": False,
    "track_lineage_id": None,
    "executor_attribution": None,
    "rules_config_version": "rules-v2.4.0-abc123",
    "model_versions": {
        "detector": "yolov8s+glove@1696000000",
        "tracker": "bytetrack-supervision",
        "code_version": "ai_perception-2.1.0"
    },
    "decision_trace": {
        "fact_ids": ["g7h8i9j0-1234-56ab-cdef-777777777777"],
        "rule_id": None,
        "conditions": "end_reason=left_zone, carrying_type=tool, intrusion_detected=False",
        "rules_config_version": "rules-v2.4.0-abc123",
        "observation_state": "obs_ok"
    },
    "payload": {
        "process_event_id": "proc_125",
        "process_event_type": "zone_risk_score",
        "decision_mode": "PROCESS_EVENT_CONFIRMED"
    }
}

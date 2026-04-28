import json
import logging
import queue
import time
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Iterable, List, Optional, Set

logger = logging.getLogger("event_aggregator")

DEFAULT_CONFIG: Dict[str, Any] = {
    "interruption": {
        "threshold_employee_interrupt": 0.5,
        "T_resume_interrupt_sec": 60.0,
        "min_action_duration_for_interrupt_sec": 0.8,
    },
    "zone_exit": {
        "T_left_confirm_sec": 1.2,
        "handoff_confirm_window_sec": 2.0,
        "T_handoff_confirm_sec": 0.6,
    },
    "intrusion": {
        "T_intrusion_confirm_sec": 2.0,
    },
    "scoring": {
        "w_left_zone": 10,
        "w_carrying_tool": 25,
        "w_carrying_food": 25,
        "w_intrusion": 20,
        "level_info_lt": 25,
        "level_warning_lt": 45,
        "employee_confidence_floor": 0.5,
    },
}


class EventAggregator:
    """
    Склеивает результаты Action / Sanitary, держит state по action_id,
    обеспечивает идемпотентность, детектит ACTION_INTERRUPTION, считает ZONE_RISK_SCORE
    и отправляет финальные события в DBI.

    Ожидаемые входы:
    1) ACTION_START / ACTION_END из action_detector
    2) пакет с sanitary_events (как сейчас делает sanitary_checker)
    3) standalone sanitary event / hygiene event, если модули будут вызывать агрегатор отдельно

    Важный момент:
    delayed sanitary-события, которые приходят ПОСЛЕ ACTION_END, должны содержать action_id.
    Иначе корректно склеить их с конкретным действием невозможно.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        dedup_cache_size: int = 10000,
        finished_action_ttl_sec: float = 300.0,
    ):
        self.config = self._deep_merge(deepcopy(DEFAULT_CONFIG), config or {})
        self.finished_action_ttl_sec = float(finished_action_ttl_sec)

        self._seen_event_keys: Set[str] = set()
        self._seen_event_queue: Deque[str] = deque(maxlen=max(1000, int(dedup_cache_size)))

        self.actions: Dict[str, Dict[str, Any]] = {}
        self.finished_actions: Dict[str, Dict[str, Any]] = {}
        self.pending_left_zone_actions: List[Dict[str, Any]] = []

    # ==========================================================
    # ======================= PUBLIC API ========================
    # ==========================================================
    def process(self, packet: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Главная точка входа.
        Возвращает список новых агрегированных событий, которые также отправляются в DBI.
        """
        self._cleanup_old_state()

        out_events: List[Dict[str, Any]] = []

        if not isinstance(packet, dict):
            return out_events

        event_type = packet.get("event_type")

        if event_type == "ACTION_START":
            produced = self._handle_action_start(packet)
            out_events.extend(self._emit_many(produced))

        elif event_type == "ACTION_END":
            sanitary_events = packet.get("sanitary_events") or []
            inline_events_for_emit: List[Dict[str, Any]] = []
            if sanitary_events:
                inline_events_for_emit = self._normalize_inline_sanitary_events(
                    packet,
                    sanitary_events,
                    emit_mode=False,
                )

            produced = self._handle_action_end(packet)
            out_events.extend(self._emit_many(produced))
            out_events.extend(self._emit_many(inline_events_for_emit))

        elif event_type == "HYGIENE_EVENT":
            produced = self._handle_hygiene_event(packet)
            out_events.extend(self._emit_many(produced))

        elif event_type == "SANITARY_EVENT":
            produced = self._handle_sanitary_event(packet)
            out_events.extend(self._emit_many(produced))

        # Поддержка текущего интерфейса sanitary_checker:
        # он не делает отдельные эвенты, а прикладывает sanitary_events к packet.
        sanitary_events = packet.get("sanitary_events") or []
        if sanitary_events and event_type != "ACTION_END":
            normalized_events = self._normalize_inline_sanitary_events(packet, sanitary_events)
            out_events.extend(self._emit_many(normalized_events))

        return out_events

    def flush_action(self, action_id: str) -> Optional[Dict[str, Any]]:
        """
        Возвращает текущий snapshot action state, если нужен отладочный доступ.
        """
        state = self.actions.get(action_id) or self.finished_actions.get(action_id)
        return deepcopy(state) if state else None

    # ==========================================================
    # ====================== ACTION START =======================
    # ==========================================================
    def _handle_action_start(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        action_id = event.get("action_id")
        if not action_id:
            return []

        state = self.actions.get(action_id, self._new_action_state(action_id))
        state["action_start"] = deepcopy(event)
        state["employee_id"] = event.get("employee_id")
        state["zone_id"] = event.get("zone_id")
        state["camera_id"] = event.get("camera_id")
        state["action_type"] = event.get("action_type")
        state["timestamp_start"] = event.get("timestamp_start")
        state["updated_at_monotonic"] = time.time()

        if event.get("hygiene") is not None:
            state["hygiene"] = deepcopy(event["hygiene"])

        self.actions[action_id] = state

        produced: List[Dict[str, Any]] = [deepcopy(event)]

        interruption_event = self._detect_interruption_for_start(state, event)
        if interruption_event is not None:
            produced.append(interruption_event)

        return produced

    # ==========================================================
    # ======================= ACTION END ========================
    # ==========================================================
    def _handle_action_end(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        action_id = event.get("action_id")
        if not action_id:
            return []

        state = self.actions.get(action_id, self._new_action_state(action_id))
        state["action_end"] = deepcopy(event)
        state["employee_id"] = event.get("employee_id", state.get("employee_id"))
        state["zone_id"] = event.get("zone_id", state.get("zone_id"))
        state["camera_id"] = event.get("camera_id", state.get("camera_id"))
        state["action_type"] = event.get("action_type", state.get("action_type"))
        state["timestamp_start"] = event.get("timestamp_start", state.get("timestamp_start"))
        state["timestamp_end"] = event.get("timestamp_end")
        state["end_reason"] = event.get("end_reason")
        state["employee_confidence"] = float(event.get("employee_confidence", state.get("employee_confidence", 0.0)) or 0.0)
        state["carrying_tool"] = bool(event.get("carrying_tool", False))
        state["carrying_food"] = bool(event.get("carrying_food", False))
        state["updated_at_monotonic"] = time.time()

        if event.get("hygiene") is not None:
            state["hygiene"] = deepcopy(event["hygiene"])

        self.actions[action_id] = state

        produced: List[Dict[str, Any]] = [deepcopy(event)]

        risk_event = self._make_zone_risk_score_event(state)
        if risk_event is not None:
            produced.append(risk_event)
            state["risk_score_event"] = deepcopy(risk_event)

        self._mark_finished(action_id)
        self._remember_left_zone_candidate(state)

        return produced

    # ==========================================================
    # =================== HYGIENE / SANITARY ====================
    # ==========================================================
    def _handle_hygiene_event(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        action_id = event.get("action_id")
        if not action_id:
            return [deepcopy(event)]

        state = self.actions.get(action_id) or self.finished_actions.get(action_id)
        if state is None:
            state = self._new_action_state(action_id)
            self.actions[action_id] = state

        state.setdefault("hygiene_events", []).append(deepcopy(event))
        state["updated_at_monotonic"] = time.time()
        return [deepcopy(event)]

    def _handle_sanitary_event(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        action_id = event.get("action_id")
        if not action_id:
            return [deepcopy(event)]

        state = self.actions.get(action_id) or self.finished_actions.get(action_id)
        if state is None:
            state = self._new_action_state(action_id)
            self.actions[action_id] = state

        state.setdefault("sanitary_events", []).append(deepcopy(event))
        state["updated_at_monotonic"] = time.time()

        produced = [deepcopy(event)]

        # если поздний sanitary event изменил факторы риска — пересчитываем score
        if self._sanitary_event_affects_risk(event):
            risk_event = self._make_zone_risk_score_event(state)
            if risk_event is not None:
                produced.append(risk_event)
                state["risk_score_event"] = deepcopy(risk_event)

        return produced

    def _normalize_inline_sanitary_events(
        self,
        packet: Dict[str, Any],
        sanitary_events: Iterable[Dict[str, Any]],
        emit_mode: bool = True,
    ) -> List[Dict[str, Any]]:
        action_id = packet.get("action_id")
        normalized: List[Dict[str, Any]] = []

        for raw in sanitary_events:
            code = raw.get("code")
            raw_type = raw.get("type")
            event_type = "SANITARY_EVENT"
            if raw_type == "PROCESS_EVENT":
                event_type = "SANITARY_EVENT"
            elif raw_type == "DATA_QUALITY_EVENT":
                event_type = "SANITARY_EVENT"
            elif raw_type == "SANITARY_VIOLATION":
                event_type = "SANITARY_EVENT"

            event = {
                "event_type": event_type,
                "action_id": raw.get("action_id", action_id),
                "camera_id": raw.get("camera_id", packet.get("camera_id")),
                "zone_id": raw.get("zone_id", packet.get("zone_id")),
                "employee_id": raw.get("employee_id", packet.get("employee_id")),
                "action_type": raw.get("action_type", packet.get("action_type")),
                "timestamp": raw.get("timestamp", self._now_iso()),
                "sanitary_type": raw_type,
                "sanitary_code": code,
                "payload": deepcopy(raw),
            }
            if emit_mode:
                normalized.extend(self._handle_sanitary_event(event))
            else:
                action_id = event.get("action_id")
                if action_id:
                    state = self.actions.get(action_id) or self.finished_actions.get(action_id)
                    if state is None:
                        state = self._new_action_state(action_id)
                        self.actions[action_id] = state
                    state.setdefault("sanitary_events", []).append(deepcopy(event))
                    state["updated_at_monotonic"] = time.time()
                normalized.append(event)

        return normalized

    # ==========================================================
    # ================= INTERRUPTION DETECTION ==================
    # ==========================================================
    def _detect_interruption_for_start(
        self,
        current_state: Dict[str, Any],
        action_start_event: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        cfg = self.config["interruption"]
        current_employee_id = action_start_event.get("employee_id")
        current_zone_id = action_start_event.get("zone_id")
        current_action_type = action_start_event.get("action_type")

        if current_employee_id in (None, "undefined"):
            return None

        current_start_ts = self._coerce_ts(action_start_event.get("timestamp_start"))
        if current_start_ts is None:
            return None

        best_candidate = None
        best_gap = None

        for candidate in self.pending_left_zone_actions:
            if candidate.get("employee_id") != current_employee_id:
                continue
            if candidate.get("zone_id") != current_zone_id:
                continue
            if candidate.get("action_type") != current_action_type:
                continue
            if float(candidate.get("employee_confidence", 0.0)) < float(cfg["threshold_employee_interrupt"]):
                continue
            if float(candidate.get("duration_sec", 0.0)) < float(cfg["min_action_duration_for_interrupt_sec"]):
                continue

            prev_end_ts = self._coerce_ts(candidate.get("timestamp_end"))
            if prev_end_ts is None:
                continue

            gap = current_start_ts - prev_end_ts
            if gap < 0:
                continue
            if gap > float(cfg["T_resume_interrupt_sec"]):
                continue

            if best_gap is None or gap < best_gap:
                best_candidate = candidate
                best_gap = gap

        if best_candidate is None:
            return None

        self.pending_left_zone_actions = [
            item for item in self.pending_left_zone_actions
            if item.get("action_id") != best_candidate.get("action_id")
        ]

        return {
            "event_type": "ACTION_INTERRUPTION",
            "employee_id": current_employee_id,
            "zone_id": current_zone_id,
            "action_type": current_action_type,
            "interrupted_action_id": best_candidate["action_id"],
            "resumed_action_id": current_state["action_id"],
            "resume_gap_sec": round(best_gap, 3),
        }

    def _remember_left_zone_candidate(self, state: Dict[str, Any]) -> None:
        if state.get("end_reason") != "left_zone":
            return

        action_id = state.get("action_id")
        if not action_id:
            return

        candidate = {
            "action_id": action_id,
            "employee_id": state.get("employee_id"),
            "zone_id": state.get("zone_id"),
            "action_type": state.get("action_type"),
            "employee_confidence": float(state.get("employee_confidence", 0.0) or 0.0),
            "timestamp_end": state.get("timestamp_end"),
            "duration_sec": float(self._compute_duration_sec(state) or 0.0),
            "saved_at_monotonic": time.time(),
        }

        self.pending_left_zone_actions = [
            item for item in self.pending_left_zone_actions
            if item.get("action_id") != action_id
        ]
        self.pending_left_zone_actions.append(candidate)

    # ==========================================================
    # ===================== RISK SCORING ========================
    # ==========================================================
    def _make_zone_risk_score_event(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        action_id = state.get("action_id")
        if not action_id:
            return None

        action_end = state.get("action_end")
        if not action_end:
            return None

        scoring = self.config["scoring"]

        handoff_food = self._has_sanitary_code(state, "food_handoff")
        intrusion_detected = self._has_sanitary_code(state, "intrusion_work_zone")

        factors = {
            "left_zone": 0,
            "carrying_tool": 0,
            "carrying_food": 0,
            "intrusion": 0,
        }

        if state.get("end_reason") == "left_zone":
            factors["left_zone"] = int(scoring["w_left_zone"])

        if bool(state.get("carrying_tool", False)):
            factors["carrying_tool"] = int(scoring["w_carrying_tool"])

        if bool(state.get("carrying_food", False)):
            factors["carrying_food"] = 0 if handoff_food else int(scoring["w_carrying_food"])

        if intrusion_detected:
            factors["intrusion"] = int(scoring["w_intrusion"])

        score = sum(factors.values())
        level = self._score_to_level(score)

        employee_confidence = float(state.get("employee_confidence", 0.0) or 0.0)
        if employee_confidence < float(scoring["employee_confidence_floor"]):
            if level == "violation":
                level = "warning"

        return {
            "event_type": "ZONE_RISK_SCORE",
            "action_id": action_id,
            "employee_id": state.get("employee_id"),
            "zone_id": state.get("zone_id"),
            "action_type": state.get("action_type"),
            "score": score,
            "level": level,
            "factors": factors,
        }

    def _score_to_level(self, score: int) -> str:
        scoring = self.config["scoring"]
        if score < int(scoring["level_info_lt"]):
            return "info"
        if score < int(scoring["level_warning_lt"]):
            return "warning"
        return "violation"

    # ==========================================================
    # ===================== IDEMPOTENCY =========================
    # ==========================================================
    def _emit_many(self, events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        emitted: List[Dict[str, Any]] = []
        for event in events:
            emitted_event = self._emit_one(event)
            if emitted_event is not None:
                emitted.append(emitted_event)
        return emitted

    def _emit_one(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        key = self._make_idempotency_key(event)
        if key in self._seen_event_keys:
            return None

        if len(self._seen_event_queue) == self._seen_event_queue.maxlen:
            oldest = self._seen_event_queue.popleft()
            self._seen_event_keys.discard(oldest)

        self._seen_event_queue.append(key)
        self._seen_event_keys.add(key)

        payload = deepcopy(event)
        return payload

    def _make_idempotency_key(self, event: Dict[str, Any]) -> str:
        event_type = event.get("event_type")

        if event_type in {"ACTION_START", "ACTION_END", "ZONE_RISK_SCORE", "HYGIENE_EVENT", "SANITARY_EVENT"}:
            stable = {
                "event_type": event_type,
                "action_id": event.get("action_id"),
                "sanitary_code": event.get("sanitary_code"),
                "timestamp_start": event.get("timestamp_start"),
                "timestamp_end": event.get("timestamp_end"),
                "score": event.get("score"),
                "level": event.get("level"),
            }
        elif event_type == "ACTION_INTERRUPTION":
            stable = {
                "event_type": event_type,
                "interrupted_action_id": event.get("interrupted_action_id"),
                "resumed_action_id": event.get("resumed_action_id"),
            }
        else:
            stable = event

        return json.dumps(stable, sort_keys=True, ensure_ascii=False, default=str)

    # ==========================================================
    # ====================== STATE HELPERS ======================
    # ==========================================================
    def _new_action_state(self, action_id: str) -> Dict[str, Any]:
        return {
            "action_id": action_id,
            "action_start": None,
            "action_end": None,
            "hygiene": None,
            "hygiene_events": [],
            "sanitary_events": [],
            "camera_id": None,
            "zone_id": None,
            "employee_id": None,
            "action_type": None,
            "timestamp_start": None,
            "timestamp_end": None,
            "end_reason": None,
            "employee_confidence": 0.0,
            "carrying_tool": False,
            "carrying_food": False,
            "updated_at_monotonic": time.time(),
        }

    def _mark_finished(self, action_id: str) -> None:
        state = self.actions.get(action_id)
        if state is None:
            return
        self.finished_actions[action_id] = deepcopy(state)

    def _cleanup_old_state(self) -> None:
        now = time.time()

        expired_finished = [
            action_id
            for action_id, state in self.finished_actions.items()
            if now - float(state.get("updated_at_monotonic", now)) > self.finished_action_ttl_sec
        ]
        for action_id in expired_finished:
            self.finished_actions.pop(action_id, None)
            self.actions.pop(action_id, None)

        resume_ttl = float(self.config["interruption"]["T_resume_interrupt_sec"])
        self.pending_left_zone_actions = [
            item for item in self.pending_left_zone_actions
            if now - float(item.get("saved_at_monotonic", now)) <= resume_ttl
        ]

    def _compute_duration_sec(self, state: Dict[str, Any]) -> Optional[float]:
        ts_start = self._coerce_ts(state.get("timestamp_start"))
        ts_end = self._coerce_ts(state.get("timestamp_end"))
        if ts_start is None or ts_end is None:
            return None
        return max(0.0, ts_end - ts_start)

    def _has_sanitary_code(self, state: Dict[str, Any], code: str) -> bool:
        for event in state.get("sanitary_events", []):
            if event.get("sanitary_code") == code:
                return True
            payload = event.get("payload") or {}
            if payload.get("code") == code:
                return True
        return False

    def _sanitary_event_affects_risk(self, event: Dict[str, Any]) -> bool:
        return event.get("sanitary_code") in {"food_handoff", "intrusion_work_zone"}

    def _coerce_ts(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except Exception:
                pass
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except Exception:
                return None
        return None

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _deep_merge(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                base[key] = self._deep_merge(base[key], value)
            else:
                base[key] = value
        return base


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out_queue = queue.Queue(maxsize=32)

    agg = EventAggregator(out_queue=out_queue)

    demo = [
        {
            "event_type": "ACTION_START",
            "action_id": "A1",
            "employee_id": 12,
            "zone_id": "cutting_board",
            "camera_id": "Kitchen_1",
            "action_type": "cut",
            "timestamp_start": 100.0,
        },
        {
            "event_type": "ACTION_END",
            "action_id": "A1",
            "employee_id": 12,
            "zone_id": "cutting_board",
            "camera_id": "Kitchen_1",
            "action_type": "cut",
            "timestamp_start": 100.0,
            "timestamp_end": 104.5,
            "end_reason": "left_zone",
            "employee_confidence": 0.91,
            "carrying_tool": False,
            "carrying_food": True,
            "sanitary_events": [
                {"type": "PROCESS_EVENT", "code": "food_handoff"},
                {"type": "SANITARY_VIOLATION", "code": "intrusion_work_zone"},
            ],
        },
        {
            "event_type": "ACTION_START",
            "action_id": "A2",
            "employee_id": 12,
            "zone_id": "cutting_board",
            "camera_id": "Kitchen_1",
            "action_type": "cut",
            "timestamp_start": 120.0,
        },
    ]

    for item in demo:
        agg.process(item)

    for i in range(out_queue.qsize()):
        pkt = out_queue.get()
        print(pkt)
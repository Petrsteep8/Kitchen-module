# kitchen_vision/face/identity.py — обогащение треков идентичностью (задача ML-03)
"""
IdentityEnricher: превращает факты §13.1 (identity-free) + кадр в поток ИДЕНТИЧНОСТЕЙ по линии трека.
Реализует требования §11.4, §11.6 п.3 и §12.2/§12.3.

Ключевое (что доработано против «ArcFace каждый кадр по кропу всего человека»):
  * КЭШ по track_lineage_id: ArcFace считается НЕ ЧАЩЕ T_arcface_refresh_sec на линию (§11.6 п.3),
    между пересчётами — из кэша; кэш инвалидируется при потере трека (gc по last_seen).
  * лицо ищем в ГОЛОВНОЙ области бокса (верх ~50%), а не по всему человеку;
  * сопоставление с МАРЖОЙ (face_db) -> ambiguous не присваивается (анти-FP, §7.2);
  * автомат `executor_attribution` (§12.3): confirmed / anonymous_track / retroactively_assigned / unresolved;
  * РЕТРО-ПРИСВОЕНИЕ (§12.2): пока лицо не распознано — действия идут на анонимную линию и НЕ теряются;
    при распознавании в окне arcface_retro_window_sec идентичность дописывается (retro_update=True),
    без переотправки уже отправленных событий; по истечении окна без матча -> unresolved.

Согласие (§11.4, Д-11): в галерею попадают только согласившиеся сотрудники, несогласные не матчатся
(флаг consent в face_db). Небиометрической идентификации нет.
"""
import logging

logger = logging.getLogger("identity")

CONFIG_DEFAULTS = {
    "T_arcface_refresh_sec": 3.0,        # П1 §11.6/§12.5
    "arcface_retro_window_sec": 10.0,    # П1 §12.5
    "employee_confidence_floor": 0.5,    # П1 (ниже -> OBS_WEAK на стороне FSM); порог «слабой» идентичности
    "head_top_ratio": 0.5,               # доля бокса сверху, где ищем лицо
    "head_pad_px": 8,
    "state_ttl_sec": 12.0,               # через сколько без наблюдения чистим кэш линии (>= retro_window)
}


class IdentityEnricher:
    def __init__(self, arcface, gallery, config=None):
        """
        arcface : объект с best_embedding(image_bgr) -> (embedding|None, quality_dict)
        gallery : FaceDatabase с match(embedding) -> {employee_id, score, margin, decision}
        """
        self.arcface = arcface
        self.gallery = gallery
        self.cfg = dict(CONFIG_DEFAULTS)
        if config:
            self.cfg.update(config)
        self._state = {}  # track_lineage_id -> dict

    def _head_crop(self, frame, bbox):
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in bbox]
        pad = self.cfg["head_pad_px"]
        hy2 = int(y1 + (y2 - y1) * self.cfg["head_top_ratio"])
        x1 = max(0, min(w - 1, x1 - pad)); x2 = max(0, min(w, x2 + pad))
        y1 = max(0, min(h - 1, y1 - pad)); hy2 = max(0, min(h, hy2))
        if x2 <= x1 or hy2 <= y1:
            return None
        return frame[y1:hy2, x1:x2]

    def _new_state(self, now_ms):
        return {"employee_id": None, "employee_confidence": None,
                "executor_attribution": "anonymous_track", "ever_anonymous": True,
                "last_arcface_ms": None, "last_seen_ms": now_ms, "first_seen_ms": now_ms,
                "retro_update": False}

    def enrich(self, facts, frame_bgr):
        if not facts:
            return []
        now_ms = max(int(f.get("timestamp", 0)) for f in facts)
        retro_win_ms = self.cfg["arcface_retro_window_sec"] * 1000.0
        refresh_ms = self.cfg["T_arcface_refresh_sec"] * 1000.0

        # один person-факт на линию (если их несколько — берём самый уверенный)
        persons = {}
        for f in facts:
            if f.get("object_class") != "person":
                continue
            lin = f.get("track_lineage_id")
            if lin and (lin not in persons or f.get("confidence", 0) > persons[lin].get("confidence", 0)):
                persons[lin] = f

        out = []
        for lineage, pf in persons.items():
            st = self._state.get(lineage) or self._new_state(now_ms)
            st["last_seen_ms"] = now_ms
            st["retro_update"] = False

            need_arcface = (st["last_arcface_ms"] is None
                            or (now_ms - st["last_arcface_ms"]) >= refresh_ms)

            if need_arcface and frame_bgr is not None:
                st["last_arcface_ms"] = now_ms
                crop = self._head_crop(frame_bgr, pf["bbox"])
                emb, _q = (self.arcface.best_embedding(crop) if crop is not None else (None, {}))

                if emb is None:
                    # качественного лица нет в этом цикле
                    if st["employee_id"] is None:
                        st["executor_attribution"] = self._anon_or_unresolved(st, now_ms, retro_win_ms)
                    # если идентичность уже подтверждена — сохраняем (лицо временно не видно)
                else:
                    m = self.gallery.match(emb)
                    if m["decision"] == "confirmed":
                        was_anonymous = st["employee_id"] is None and st["ever_anonymous"]
                        first_seen_recent = (now_ms - st["first_seen_ms"]) <= retro_win_ms
                        st["employee_id"] = m["employee_id"]
                        st["employee_confidence"] = m["score"]
                        if was_anonymous and (now_ms - st["first_seen_ms"]) > 0 and st["last_arcface_ms"] != st["first_seen_ms"]:
                            # линия была анонимной в прошлом цикле, теперь распознана -> ретро
                            st["executor_attribution"] = "retroactively_assigned"
                            st["retro_update"] = True
                            if not first_seen_recent:
                                logger.info("retro-присвоение вне окна (%s) — применяем к открытым окнам решений", lineage)
                        else:
                            st["executor_attribution"] = "confirmed"
                        st["ever_anonymous"] = False
                    else:
                        # есть лицо, но матч неуверенный (low_score/ambiguous)
                        if st["employee_id"] is None:
                            st["executor_attribution"] = self._anon_or_unresolved(st, now_ms, retro_win_ms)
                        # подтверждённую идентичность одиночный плохой цикл не сбрасывает
            else:
                # кэш-хит: ArcFace не зовём; но если ещё аноним и окно истекло — переводим в unresolved
                if st["employee_id"] is None:
                    st["executor_attribution"] = self._anon_or_unresolved(st, now_ms, retro_win_ms)

            self._state[lineage] = st
            floor = self.cfg["employee_confidence_floor"]
            weak = st["employee_confidence"] is not None and st["employee_confidence"] < floor
            out.append({
                "track_lineage_id": lineage,
                "employee_id": st["employee_id"],
                "employee_confidence": round(st["employee_confidence"], 4) if st["employee_confidence"] is not None else None,
                "executor_attribution": st["executor_attribution"],
                "retro_update": st["retro_update"],   # одноразовый сигнал downstream дописать идентичность
                "weak": weak,
            })

        self._gc(now_ms)
        return out

    def _anon_or_unresolved(self, st, now_ms, retro_win_ms):
        st["ever_anonymous"] = True
        if (now_ms - st["first_seen_ms"]) <= retro_win_ms:
            return "anonymous_track"   # ещё в окне — можем распознать позже
        return "unresolved"            # окно прошло, исполнителя не установить

    def _gc(self, now_ms):
        ttl_ms = self.cfg["state_ttl_sec"] * 1000.0
        for lin in [l for l, s in self._state.items() if now_ms - s["last_seen_ms"] > ttl_ms]:
            self._state.pop(lin, None)  # инвалидация кэша при потере трека (§11.6 п.3)

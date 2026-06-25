# kitchen_vision/face/face_db.py — галерея лиц (задача ML-03)
"""
Галерея эмбеддингов сотрудников + сопоставление с МАРЖОЙ top1−top2 (§11.4, анти-FP §7.2).

Что доработано против прошлой версии:
  * МУЛЬТИ-ЭМБЕДДИНГ на сотрудника (раньше один вектор — перезаписывался при каждом add).
    Несколько ракурсов -> устойчивее распознавание.
  * Решение по ДВУМ условиям: top1 >= threshold И (top1 − top2) >= margin. Раньше брался просто
    лучший по порогу -> при двух похожих людях легко перепутать. Маржа это закрывает.
  * Флаг consent на сотрудника (§11.4, Д-11): несогласные не матчатся (фактически в галерею
    попадают только согласившиеся; флаг оставлен для явности).
  * Обратносовместимая загрузка старого формата {id: vector}.

NOTE: параметра МАРЖИ (top1−top2) в П1 нет — предлагаю внести `identity_margin_min` (владелец ML).
"""
import logging
import os

import numpy as np

logger = logging.getLogger("face_db")


def _normalize(v):
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


class FaceDatabase:
    def __init__(self, path, threshold=0.45, margin=0.05):
        self.path = path
        self.threshold = float(threshold)   # min косинус для принятия (FACE_DATABASE_THRESHOLD, П1)
        self.margin = float(margin)         # min разрыв top1−top2 между РАЗНЫМИ сотрудниками
        self.data = {}  # employee_id -> {"embeddings": [np(512)...], "consented": bool, "name": str}
        if os.path.exists(path):
            self.load(path)
        else:
            logger.info("[FaceDatabase] файла нет, создаю новую галерею: %s", path)
            self.save()

    def load(self, path=None):
        path = path or self.path
        try:
            raw = np.load(path, allow_pickle=True)
            data = raw.get("faces", {})
            data = data.item() if hasattr(data, "item") else dict(data)
            fixed = {}
            for eid, val in data.items():
                if isinstance(val, dict):                       # новый формат
                    embs = [_normalize(e) for e in val.get("embeddings", [])]
                    fixed[eid] = {"embeddings": embs,
                                  "consented": bool(val.get("consented", True)),
                                  "name": val.get("name", str(eid))}
                else:                                           # старый формат {id: vector}
                    fixed[eid] = {"embeddings": [_normalize(val)], "consented": True, "name": str(eid)}
            self.data = fixed
            logger.info("[FaceDatabase] загружено сотрудников: %d", len(self.data))
        except Exception:
            logger.exception("[FaceDatabase] не удалось загрузить %s", path)
            self.data = {}

    def save(self):
        np.savez(self.path, faces=self.data)

    def add(self, employee_id, embedding, consented=True, name=None):
        rec = self.data.setdefault(employee_id, {"embeddings": [], "consented": consented,
                                                 "name": name or str(employee_id)})
        rec["embeddings"].append(_normalize(embedding))   # мульти-эмбеддинг: добавляем, не перезаписываем
        rec["consented"] = consented
        self.save()
        logger.info("[FaceDatabase] %s: эмбеддингов теперь %d", employee_id, len(rec["embeddings"]))

    def match(self, embedding):
        """Сопоставление с маржой.

        Возвращает dict: {employee_id, score, margin, decision}.
        decision: 'confirmed' | 'low_score' | 'ambiguous' | 'empty'.
        - confirmed: top1>=threshold И (top1−top2)>=margin И сотрудник согласен;
        - low_score: лучший ниже порога;
        - ambiguous: порог взят, но маржа мала (двое похожи) -> НЕ присваиваем (анти-FP).
        """
        if not self.data:
            return {"employee_id": None, "score": 0.0, "margin": 0.0, "decision": "empty"}
        q = _normalize(embedding)
        # лучший косинус на сотрудника = max по его эмбеддингам; учитываем только согласившихся
        scores = []
        for eid, rec in self.data.items():
            if not rec.get("consented", True) or not rec["embeddings"]:
                continue
            best = max(float(np.dot(q, e)) for e in rec["embeddings"])
            scores.append((eid, best))
        if not scores:
            return {"employee_id": None, "score": 0.0, "margin": 0.0, "decision": "empty"}
        scores.sort(key=lambda x: -x[1])
        top_id, top = scores[0]
        second = scores[1][1] if len(scores) > 1 else -1.0
        margin = top - second
        if top < self.threshold:
            return {"employee_id": None, "score": top, "margin": margin, "decision": "low_score"}
        if margin < self.margin:
            return {"employee_id": None, "score": top, "margin": margin, "decision": "ambiguous"}
        return {"employee_id": top_id, "score": top, "margin": margin, "decision": "confirmed"}

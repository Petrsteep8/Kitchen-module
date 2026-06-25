# kitchen_vision/face/arcface.py — ArcFace (задача ML-03)
"""
Распознавание лиц через InsightFace (buffalo_l, 512-d), §11.4.

Отличия от прошлой версии (что доработано):
  * НЕ берём слепо faces[0] — выбираем ЛУЧШЕЕ лицо по качеству (гейтинг §11.6 п.3 в духе анти-FP §7.2):
    det_score, размер лица в пикселях, резкость (variance Лапласиана). Мутные/мелкие/боковые лица
    отбрасываются, чтобы не плодить ложные совпадения.
  * extract() сохранён для совместимости с utils/register_face.py.
"""
import logging

import cv2
import numpy as np

logger = logging.getLogger("arcface")

try:
    from insightface.app import FaceAnalysis
    INSIGHTFACE_AVAILABLE = True
except Exception:
    INSIGHTFACE_AVAILABLE = False
    logger.error("insightface НЕ установлен -> идентификация отключена. `pip install insightface onnxruntime`.")


# Пороги качества лица. NOTE: в П1 их нет — предлагаю ВНЕСТИ (владелец ML, калибровка как Д-03).
QUALITY_DEFAULTS = {
    "min_det_score": 0.60,   # уверенность детектора лица InsightFace
    "min_face_px": 40,       # min(w,h) бокса лица в пикселях (мелкое лицо -> ненадёжный embedding)
    "min_blur_var": 30.0,    # variance Лапласиана: ниже -> смазано
}


class ArcFaceRecognizer:
    def __init__(self, device="cuda", quality=None):
        self.quality = dict(QUALITY_DEFAULTS)
        if quality:
            self.quality.update(quality)
        self.app = None
        if INSIGHTFACE_AVAILABLE:
            self.app = FaceAnalysis(
                name="buffalo_l",
                providers=["CUDAExecutionProvider"] if device == "cuda" else ["CPUExecutionProvider"],
            )
            self.app.prepare(ctx_id=0 if device == "cuda" else -1)

    @staticmethod
    def _blur_var(crop_bgr):
        try:
            g = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
            return float(cv2.Laplacian(g, cv2.CV_64F).var())
        except Exception:
            return 0.0

    def detect(self, image_bgr):
        """Все лица на изображении с метаданными качества."""
        if self.app is None or image_bgr is None or image_bgr.size == 0:
            return []
        out = []
        for f in self.app.get(image_bgr):
            x1, y1, x2, y2 = [int(v) for v in f.bbox]
            w, h = max(0, x2 - x1), max(0, y2 - y1)
            crop = image_bgr[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
            out.append({
                "embedding": np.asarray(f.embedding, dtype=np.float32),
                "det_score": float(getattr(f, "det_score", 0.0)),
                "bbox": [x1, y1, x2, y2],
                "size_px": min(w, h),
                "blur_var": self._blur_var(crop) if crop.size else 0.0,
            })
        return out

    def _passes_quality(self, face):
        q = self.quality
        return (face["det_score"] >= q["min_det_score"]
                and face["size_px"] >= q["min_face_px"]
                and face["blur_var"] >= q["min_blur_var"])

    def best_embedding(self, image_bgr):
        """ЛУЧШЕЕ лицо, прошедшее гейтинг качества -> (embedding|None, quality_dict).

        Возвращает None как embedding, если качественного лица нет (тогда трек остаётся анонимным).
        """
        faces = [f for f in self.detect(image_bgr) if self._passes_quality(f)]
        if not faces:
            return None, {"reason": "no_quality_face"}
        best = max(faces, key=lambda f: (f["det_score"], f["size_px"]))
        return best["embedding"], {"det_score": best["det_score"],
                                   "size_px": best["size_px"], "blur_var": best["blur_var"]}

    # --- совместимость со старым кодом (register_face.py) ---
    def extract(self, face_img):
        emb, _ = self.best_embedding(face_img)
        return emb

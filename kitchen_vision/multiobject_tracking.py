# kitchen_vision/multiobject_tracking.py — несущий слой кросс-камерной ре-идентификации (§12.1)
"""
Слияние линий треков МЕЖДУ камерами под единым track_lineage_id + перенос идентичности фронт→тыл.

Состав задачи (что входит и здесь реализовано):
  1) APPEARANCE-ЭМБЕДДИНГ человека (AppearanceEncoder) — дескриптор внешнего вида, устойчивый к
     ракурсу, чтобы один человек на разных камерах давал похожие векторы.
  2) СЛИЯНИЕ под одним track_lineage_id (CrossCameraLinker) — связывание покамерных линий в ГЛОБАЛЬНУЮ
     линию по СЛИЯННОМУ сигналу: appearance-схожесть + равенство employee_id + пространственно-временная
     смежность камер; порог track_reid_similarity_min (П1 §12.5).
  3) ПЕРЕНОС ИДЕНТИЧНОСТИ фронт→тыл — как только на ЛЮБОЙ камере линия получила employee_id (ArcFace
     увидел лицо), все треки этой линии его наследуют; тыловая камера, не видящая лиц, получает
     идентичность через слитую линию.

⚠ ВАЖНАЯ ОГОВОРКА (расхождение «ТЗ vs реальность кухни): §12.1 предполагает re-id по внешнему виду,
   но НА КУХНЕ ВСЕ В ОДИНАКОВОЙ ФОРМЕ -> appearance по одежде слабо различает людей. Поэтому мост
   фронт↔тыл реалистично держится в первую очередь на ПРОСТРАНСТВЕННО-ВРЕМЕННОЙ смежности (человек
   уходит из края кадра камеры A и появляется в крае кадра камеры B в пределах окна), а appearance
   и идентичность — вспомогательные. Веса сигналов вынесены в конфиг; identity и spatial по умолчанию
   приоритетны над appearance. Это надо учесть при настройке на пилоте.

⚠ APPEARANCE-МОДЕЛЬ: подключён OSNet через torchreid (класс OSNetEncoder ниже). torchreid (код)
   и веса автора — MIT. НО: сильные веса OSNet обучены на reid-датасетах (Market-1501/MSMT17/
   DukeMTMC), у которых лицензия RESEARCH-ONLY (Market-1501 прямо запрещает коммерцию; DukeMTMC
   отозван) -> конкретный публичный чекпойнт в КОММЕРЧЕСКОМ продукте юридически рискован. Безопасно:
   обучить OSNet на СВОИХ кадрах кухни (будут после MVP) или на коммерчески-чистом датасете;
   ImageNet-предобучение + дообучение — запасной путь. Финальный выбор чекпойнта — за юристом.
   Фолбэк AppearanceEncoder (HSV) остаётся для деградации, если torchreid/веса недоступны.

ГДЕ В ПАЙПЛАЙНЕ: это ОТДЕЛЬНАЯ стадия ПОСЛЕ перцепции, потребляющая пакеты со ВСЕХ камер
(facts + identities + frame_bgr из ai_perception). Она ПЕРЕЗАПИСЫВАЕТ fact.track_lineage_id с
покамерного (локального) на глобальный и сливает идентичность. Покамерный LineageManager в
ai_perception остаётся «локальным».
"""
import logging
import uuid

import numpy as np

logger = logging.getLogger("multiobject_tracking")

try:                                  # OSNet (appearance re-id). Код torchreid — MIT.
    from torchreid.reid.utils import FeatureExtractor
    TORCHREID_AVAILABLE = True
except Exception:
    try:
        from torchreid.reid.utils import FeatureExtractor   # альтернативный путь в некоторых версиях
        TORCHREID_AVAILABLE = True
    except Exception:
        TORCHREID_AVAILABLE = False
        FeatureExtractor = None

CONFIG_DEFAULTS = {
    "track_reid_similarity_min": 0.6,   # П1 §12.5 — порог слияния линий
    "cross_camera_window_sec": 5.0,     # сколько линия остаётся слияемой после last_seen (хендофф камер)
    "appearance_memory": 10,            # сколько эмбеддингов внешнего вида держать на линию
    "spatial_bonus": 0.2,               # прибавка к схожести, если камеры смежны и в окне хендоффа
    "state_ttl_sec": 30.0,
    # camera_adjacency: {cam: [смежные камеры]} — между ними возможен хендофф. None => все смежны.
    "camera_adjacency": None,
}


def _norm(v):
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


class AppearanceEncoder:
    """Дескриптор внешнего вида человека. Фолбэк: HSV-гистограмма (СЛАБО; для прода — ReID-модель)."""

    def __init__(self, model=None):
        self.model = model  # сюда подключается OSNet/torchreid: model(crop_bgr)->np.array

    def encode(self, frame_bgr, bbox):
        if frame_bgr is None:
            return None
        x1, y1, x2, y2 = [int(v) for v in bbox]
        h, w = frame_bgr.shape[:2]
        x1 = max(0, min(w - 1, x1)); x2 = max(0, min(w, x2))
        y1 = max(0, min(h - 1, y1)); y2 = max(0, min(h, y2))
        if x2 <= x1 or y2 <= y1:
            return None
        crop = frame_bgr[y1:y2, x1:x2]
        if self.model is not None:
            try:
                return _norm(self.model(crop))
            except Exception:
                logger.exception("ReID-модель упала — фолбэк на гистограмму")
        # фолбэк: нормированная HSV-гистограмма (грубо; плохо различает форму)
        try:
            import cv2
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
            return _norm(hist.flatten())
        except Exception:
            return None


class OSNetEncoder:
    """Appearance-эмбеддинг через OSNet (torchreid). Drop-in замена AppearanceEncoder (тот же encode()).

    Лицензия: torchreid (код) и веса автора — MIT. ВНИМАНИЕ: публичные сильные веса OSNet обучены на
    research-only датасетах (Market-1501/MSMT17/DukeMTMC) -> для коммерции конкретный чекпойнт рискован;
    обучить на своих кадрах/коммерчески-чистом датасете. Выбор чекпойнта — за юристом.
    """

    def __init__(self, model_name="osnet_x1_0", model_path="", device="cuda", extractor=None):
        if extractor is not None:
            self.extractor = extractor                 # DI для тестов / своей обёртки
        else:
            if not TORCHREID_AVAILABLE:
                raise RuntimeError("torchreid не установлен: `pip install torchreid` (или из репозитория)")
            # model_path="" -> ImageNet-предобучение (слабее для reid); путь к reid-весам -> сильнее
            self.extractor = FeatureExtractor(model_name=model_name, model_path=model_path, device=device)

    def encode(self, frame_bgr, bbox):
        if frame_bgr is None:
            return None
        x1, y1, x2, y2 = [int(v) for v in bbox]
        h, w = frame_bgr.shape[:2]
        x1 = max(0, min(w - 1, x1)); x2 = max(0, min(w, x2))
        y1 = max(0, min(h - 1, y1)); y2 = max(0, min(h, y2))
        if x2 <= x1 or y2 <= y1:
            return None
        crop = frame_bgr[y1:y2, x1:x2]
        try:
            import cv2
            rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)    # torchreid ждёт RGB (внутри ToPILImage)
        except Exception:
            rgb = crop[..., ::-1]
        feat = self.extractor([rgb])                        # (1, D)
        try:
            v = feat[0].detach().cpu().numpy()
        except Exception:
            v = np.asarray(feat)[0]
        return _norm(v)


def build_appearance_encoder(prefer="osnet", model_path="", device="cuda"):
    """Фабрика: OSNet, если доступен; иначе громкий фолбэк на HSV-гистограмму."""
    if prefer == "osnet" and TORCHREID_AVAILABLE:
        try:
            logger.info("Appearance-энкодер: OSNet (torchreid)")
            return OSNetEncoder(model_path=model_path, device=device)
        except Exception:
            logger.exception("OSNet недоступен — фолбэк на HSV-гистограмму")
    logger.warning("Appearance-энкодер: HSV-фолбэк (СЛАБО различает форму). "
                   "Для прода: torchreid + reid-чекпойнт (см. оговорку о лицензии весов).")
    return AppearanceEncoder()


class CrossCameraLinker:
    def __init__(self, encoder=None, config=None):
        self.encoder = encoder or AppearanceEncoder()
        self.cfg = dict(CONFIG_DEFAULTS)
        if config:
            self.cfg.update(config)
        self._lineages = {}   # gid -> {...}
        self._bind = {}       # (camera_id, local_lineage) -> gid

    def _adjacent(self, cam_a, cam_b):
        adj = self.cfg["camera_adjacency"]
        if adj is None:
            return True
        return cam_b in set(adj.get(cam_a, [])) or cam_a in set(adj.get(cam_b, []))

    def _appearance_sim(self, emb, lineage):
        if emb is None or not lineage["appearance"]:
            return 0.0
        return max(float(np.dot(emb, e)) for e in lineage["appearance"])

    def _find_lineage(self, camera_id, emb, employee_id, now_ms):
        win_ms = self.cfg["cross_camera_window_sec"] * 1000.0
        best_gid, best_score = None, -1.0
        for gid, L in self._lineages.items():
            if now_ms - L["last_seen_ms"] > win_ms:
                continue
            # не сливать с линией, у которой ЖИВ другой трек на ЭТОЙ ЖЕ камере (это другой человек)
            live = L["cameras_live"].get(camera_id)
            if live is not None and (now_ms - live[1]) <= win_ms and live[0] is not None:
                # есть живой трек на этой камере; если это не наш — линия неподходящая
                continue
            score = self._appearance_sim(emb, L)
            if employee_id is not None and L["employee_id"] == employee_id:
                score = max(score, 1.0)  # совпадение идентичности форсит связь
            if self._adjacent(camera_id, L.get("last_camera")) and L.get("last_camera") != camera_id:
                score += self.cfg["spatial_bonus"]
            if score > best_score:
                best_gid, best_score = gid, score
        if best_gid is not None and best_score >= self.cfg["track_reid_similarity_min"]:
            return best_gid
        return None

    def _new_lineage(self, now_ms, camera_id):
        gid = str(uuid.uuid4())
        self._lineages[gid] = {"members": set(), "appearance": [], "employee_id": None,
                               "employee_confidence": None, "executor_attribution": "anonymous_track",
                               "last_seen_ms": now_ms, "last_camera": camera_id, "cameras_live": {}}
        return gid

    def process(self, packet):
        """Вход: пакет ai_perception {camera_id, facts, identities, frame_bgr, timestamp}.
        Возвращает тот же пакет с глобальными track_lineage_id и слитой идентичностью."""
        facts = packet.get("facts", [])
        if not facts:
            return packet
        camera_id = packet.get("camera_id")
        now_ms = packet.get("timestamp") or max(int(f.get("timestamp", 0)) for f in facts)
        frame = packet.get("frame_bgr")

        id_by_local = {i["track_lineage_id"]: i for i in packet.get("identities", [])}
        # appearance можно передать заранее (для тестов) или посчитать энкодером из кадра
        injected = packet.get("track_appearance", {})

        # один person-факт на локальную линию
        persons = {}
        for f in facts:
            if f.get("object_class") == "person":
                persons.setdefault(f.get("track_lineage_id"), f)

        local_to_global = {}
        merged_identities = []
        for local_lin, pf in persons.items():
            key = (camera_id, local_lin)
            ident = id_by_local.get(local_lin, {})
            employee_id = ident.get("employee_id")

            emb = injected.get(local_lin)
            if emb is None:
                emb = self.encoder.encode(frame, pf["bbox"])
            emb = _norm(emb) if emb is not None else None

            gid = self._bind.get(key)
            if gid is None or gid not in self._lineages:
                gid = self._find_lineage(camera_id, emb, employee_id, now_ms) or self._new_lineage(now_ms, camera_id)
                self._bind[key] = gid
            L = self._lineages[gid]

            # обновление линии
            L["members"].add(key)
            if emb is not None:
                L["appearance"].append(emb)
                L["appearance"] = L["appearance"][-self.cfg["appearance_memory"]:]
            L["last_seen_ms"] = now_ms
            L["last_camera"] = camera_id
            L["cameras_live"][camera_id] = (local_lin, now_ms)

            # слияние/перенос идентичности
            cross_assigned = False
            if employee_id is not None:
                # эта камера дала идентичность -> кладём в линию (фронт)
                L["employee_id"] = employee_id
                L["employee_confidence"] = ident.get("employee_confidence")
                L["executor_attribution"] = ident.get("executor_attribution", "confirmed")
            elif L["employee_id"] is not None:
                # своей идентичности нет, но линия её знает -> НАСЛЕДУЕМ (тыл получает от фронта)
                employee_id = L["employee_id"]
                ident = {**ident, "employee_id": employee_id,
                         "employee_confidence": L["employee_confidence"],
                         "executor_attribution": L["executor_attribution"]}
                cross_assigned = True

            local_to_global[local_lin] = gid
            merged_identities.append({
                "track_lineage_id": gid,
                "employee_id": employee_id,
                "employee_confidence": ident.get("employee_confidence"),
                "executor_attribution": ident.get("executor_attribution", "anonymous_track"),
                "cross_camera_assigned": cross_assigned,   # идентичность пришла с другой камеры
            })

        # перезапись локальных линий на глобальные во ВСЕХ фактах (не только person)
        for f in facts:
            g = local_to_global.get(f.get("track_lineage_id"))
            if g is not None:
                f["track_lineage_id"] = g

        packet["identities"] = merged_identities
        self._gc(now_ms)
        return packet

    def _gc(self, now_ms):
        ttl = self.cfg["state_ttl_sec"] * 1000.0
        dead = [g for g, L in self._lineages.items() if now_ms - L["last_seen_ms"] > ttl]
        for g in dead:
            self._lineages.pop(g, None)
            for k in [k for k, v in self._bind.items() if v == g]:
                self._bind.pop(k, None)

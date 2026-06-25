# ai_perception.py — Kitchen Module, слой перцепции (задача ML-02)
"""
Перцепция: видеокадр -> детекция -> трекинг -> ФАКТЫ НАБЛЮДЕНИЯ (§13.1).

ЧТО ДЕЛАЕТ (ядро ML-02):
  * детекция YOLOv8s (+ кастомная glove-модель), классы приводятся к КАНОНУ §11.2
    через class_mapping.yaml (а не хардкод SYNONYM_MAP, как было — принцип §1.2 п.5);
  * трекинг ByteTrack, ОТДЕЛЬНЫЙ экземпляр на каждую камеру -> стабильный track_id
    (это и есть фундамент: прошлый SimpleTracker раздавал новый id каждый кадр);
  * устойчивый track_lineage_id (§12.3), переживающий короткие пропадания трека;
  * выход СТРОГО в схеме фактов §13.1 (без идентичности), с zone_id из резолвера зон (ML-04).

ЧЕГО НЕ ДЕЛАЕТ ЗДЕСЬ (вынесено в отдельные модули — это НЕ заглушки):
  * идентификацию ArcFace -> face/identity.py (IdentityEnricher инжектится через identity_enricher=).
    Факт §13.1 identity-free: распознавание лица — обогащение трека (§12.1), отдельный поток.
    Если энричер не передан, по умолчанию NullIdentityEnricher (пустой поток идентичности).
  * кросс-камерное слияние линий и перенос идентичности -> multiobject_tracking.CrossCameraLinker
    (стадия ПОСЛЕ перцепции). LineageManager здесь даёт ЛОКАЛЬНУЮ (покамерную) линию.

Батчинг детектора (§11.6 п.1) и шардинг камера→GPU РЕАЛИЗОВАНЫ: detect_batch() / perception_sharder.py.
"""
import base64
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import cv2
import numpy as np
import yaml

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai_perception")

# Версия кода модуля -> попадает в model_versions факта (§34.2). Бамп при значимых правках.
CODE_VERSION = "ai_perception-2.1.0"

# ---------------------------------------------------------------------------
# Параметры несущего слоя (§12.5) и порогов входа (§27.3).
# NOTE: по принципу §1.2 п.5 эти значения — КОНФИГ (реестр П1), не код. Здесь оставлены
# как DEFAULTS и должны переопределяться из config.yaml при интеграции. Значения = П1.
# ---------------------------------------------------------------------------
DEFAULTS = {
    "fps": 8,                       # частота кадров потока (ML-01); влияет на буфер ByteTrack
    "track_max_gap_sec": 2.0,       # П1 §12.5 — до этого пропадания трек сливается под той же линией
    "track_reid_similarity_min": 0.6,  # П1 §12.5 — порог re-id для слияния (нужен appearance-слой)
    "det_conf_enter": 0.5,          # П1 §27.3 — порог активации трека ByteTrack
    "bytetrack_match_thresh": 0.8,  # IoU-порог ассоциации ByteTrack (тюнится)
    "batch_max": 8,                 # §11.6 п.1 — макс кадров в батче детектора
    "batch_timeout_sec": 0.04,      # окно сборки батча; держать << latency-бюджета ACTION_EVENT (p95<=300мс)
}

# ---------------------------------------------------------------------------
# Необязательные зависимости — грузим мягко, но ГРОМКО предупреждаем (fail-loud, §1.2 п.3).
# ---------------------------------------------------------------------------
try:
    from ultralytics import YOLO
    ULTRALYTICS_AVAILABLE = True
except Exception:
    ULTRALYTICS_AVAILABLE = False
    logger.error("ultralytics НЕ установлен -> детекция отключена. `pip install ultralytics`.")

try:
    import supervision as sv  # ByteTrack со стабильным API; per-camera инстансы (см. handoff: +supervision)
    SUPERVISION_AVAILABLE = True
except Exception:
    SUPERVISION_AVAILABLE = False
    logger.error("supervision НЕ установлен -> ТРЕКИНГА НЕТ (только покадровые id). `pip install supervision`.")


def select_device():
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


DEVICE = select_device()
logger.info("Perception device: %s", DEVICE)


# ===========================================================================
# Канонический справочник классов (§11.2) из class_mapping.yaml
# ===========================================================================
class ClassMap:
    """Обёртка над class_mapping.yaml. Единственный источник канонических имён и порогов."""

    def __init__(self, path):
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"class_mapping.yaml не найден: {path}. "
                f"Это контракт имён §11.2 — без него перцепция не запускается."
            )
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        self.version = cfg.get("version", "0")
        self.markup_version = cfg.get("markup_version", "")
        self.canonical = cfg.get("canonical_classes", {}) or {}
        self.synonyms = {str(k).lower(): v for k, v in (cfg.get("synonyms", {}) or {}).items()}
        self.hand_state = cfg.get("hand_state", {}) or {}
        thr = cfg.get("thresholds", {}) or {}
        self.default_min_conf = float(thr.get("default_min_confidence", 0.35))
        self.min_conf_violation_source = float(thr.get("min_confidence_for_violation_source", 0.70))

        # стабильный int-id на канонический класс — нужен ByteTrack (class_id)
        self.classes = sorted(self.canonical.keys())
        self.cls_to_id = {c: i for i, c in enumerate(self.classes)}
        self.id_to_cls = {i: c for c, i in self.cls_to_id.items()}

        # порог детекции на класс
        self.min_conf = {
            c: float(self.canonical[c].get("min_confidence", self.default_min_conf))
            for c in self.classes
        }
        # минимальный порог по всем классам — используем как conf-пол для predict()
        self.conf_floor = min(list(self.min_conf.values()) + [self.default_min_conf])
        logger.info("ClassMap v%s: %d канонических классов, conf_floor=%.2f",
                    self.version, len(self.classes), self.conf_floor)

    def to_canonical(self, raw_name):
        """raw имя детектора -> канон или None (None => DROP, явный отброс неизвестного)."""
        if raw_name is None:
            return None
        name = str(raw_name).lower().strip()
        if name in self.cls_to_id:
            return name
        if name in self.synonyms:
            return self.synonyms[name]
        alt = name.replace("_", " ")
        if alt in self.synonyms:
            return self.synonyms[alt]
        return None  # ⚠ раньше неизвестное имя молча проходило «как есть»; теперь отбрасываем

    def glove_state_of(self, raw_name):
        """Для рук: 'gloved' / 'bare' / None — по hand_state из конфига."""
        if raw_name is None:
            return None
        rec = self.hand_state.get(str(raw_name).lower().strip())
        return rec.get("glove_state") if rec else None


# ===========================================================================
# Линии треков (§12.3): (camera_id, track_id) -> track_lineage_id (UUID v4)
# ===========================================================================
class LineageManager:
    """
    Стабильный track_lineage_id, переживающий короткие разрывы.

    Короткие пропадания (<= track_max_gap_sec) ByteTrack закрывает САМ за счёт lost-buffer,
    переиспользуя тот же track_id -> линия сохраняется автоматически. После более долгого
    разрыва ByteTrack может переиспользовать тот же int id для ДРУГОГО объекта, поэтому
    запись устаревает по track_max_gap_sec и следующая встреча даёт НОВУЮ линию (правило §12.3 п.2).

    ЗДЕСЬ — ЛОКАЛЬНАЯ (покамерная) линия: после долгого разрыва даётся новая. Слияние линий по
    внешнему виду (track_reid_similarity_min), в т.ч. КРОСС-КАМЕРНО, и перенос идентичности делает
    отдельный слой multiobject_tracking.CrossCameraLinker (§12.1) ПОСЛЕ перцепции — он и
    перезаписывает track_lineage_id на глобальный.
    """

    def __init__(self, gap_sec):
        self.gap_sec = float(gap_sec)
        self._map = {}  # (cam, tid) -> {"lineage": str, "last": float_sec}

    def lineage_for(self, camera_id, track_id, now_ms):
        key = (camera_id, int(track_id))
        now = now_ms / 1000.0
        rec = self._map.get(key)
        if rec is not None and (now - rec["last"]) <= self.gap_sec:
            rec["last"] = now
            return rec["lineage"]
        # точка вставки appearance-reid: попытаться найти недавно потерянную линию с reid >= порога
        lineage = str(uuid.uuid4())
        self._map[key] = {"lineage": lineage, "last": now}
        return lineage

    def gc(self, now_ms):
        now = now_ms / 1000.0
        for k in [k for k, v in self._map.items() if now - v["last"] > self.gap_sec * 3]:
            self._map.pop(k, None)


def _make_bytetrack(fps, det_conf_enter, gap_sec, match_thresh):
    """Создать ByteTrack робастно к версии supervision (новые/старые имена аргументов)."""
    buf = max(1, int(round(gap_sec * fps)))  # lost-buffer в кадрах ~ track_max_gap_sec
    try:
        return sv.ByteTrack(track_activation_threshold=det_conf_enter,
                            lost_track_buffer=buf,
                            minimum_matching_threshold=match_thresh,
                            frame_rate=int(fps))
    except TypeError:
        return sv.ByteTrack(track_thresh=det_conf_enter,
                            track_buffer=buf,
                            match_thresh=match_thresh,
                            frame_rate=int(fps))


def iou_xyxy(a, b):
    xA, yA = max(a[0], b[0]), max(a[1], b[1])
    xB, yB = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, xB - xA) * max(0.0, yB - yA)
    areaA = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    areaB = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = areaA + areaB - inter
    return inter / union if union > 0 else 0.0


def _to_ms_utc(ts):
    """Любой timestamp -> int мс UTC (§12.4: время в UTC, мс)."""
    if ts is None:
        return int(time.time() * 1000)
    if isinstance(ts, (int, float)):
        return int(ts if ts > 1e12 else ts * 1000)  # эвристика сек/мс
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            return int(time.time() * 1000)
    return int(time.time() * 1000)


def foot_point(bbox):
    """Опорная точка человека/объекта = низ-центр bbox (для зон, §14).

    ⚠ ОТКЛОНЕНИЕ/ИСПРАВЛЕНИЕ: §14 не фиксирует, какой точкой проверять зону, а прошлый код брал
    ЦЕНТР bbox (action_detector.py:436) -> ложный left_zone при наклоне корпуса. foot-point —
    решение из контекст-файла (§8) и оно корректнее: человек «находится» там, где стоит.
    """
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, y2)


# ===========================================================================
# Дефолтный no-op энричер идентичности.
# Реальная идентификация (ArcFace, кэш по линии, маржа top1-top2, ретро §12.2) — в
# face/identity.py:IdentityEnricher, передаётся через identity_enricher= (см. ml_pipeline.py).
# ===========================================================================
class NullIdentityEnricher:
    """Пустой поток идентичности (когда энричер не передан). Рабочий дефолт, не висяк."""

    def enrich(self, facts, frame_bgr):
        return []  # [{track_lineage_id, employee_id, employee_confidence, executor_attribution}]


# ===========================================================================
# PerceptionWorker
# ===========================================================================
class PerceptionWorker(threading.Thread):
    def __init__(self, in_queue, out_queue=None, class_map_path="class_mapping.yaml",
                 zones_path=None, model_dir="./models", identity_enricher=None,
                 config=None, model_versions=None, device=None):
        super().__init__(daemon=True)
        self.in_queue = in_queue
        self.out_queue = out_queue
        self.model_dir = model_dir
        self.device = device or DEVICE   # GPU-пин для шардинга (ML-01): "cuda:0", "cuda:1", ...
        self.stop_event = threading.Event()

        cfg = dict(DEFAULTS)
        if config:
            cfg.update(config)
        self.cfg = cfg

        self.cmap = ClassMap(class_map_path)

        # резолвер зон (ML-04). Может отсутствовать до калибровки -> zone_id = None (поле опционально, §13.1).
        self.zone_resolver = None
        if zones_path and os.path.exists(zones_path):
            try:
                from kitchen_vision.zones import ZoneResolver  # модуль ML-04
                self.zone_resolver = ZoneResolver(zones_path)
                logger.info("ZoneResolver загружен из %s", zones_path)
            except Exception:
                logger.exception("Не удалось загрузить ZoneResolver — zone_id будет None.")
        else:
            logger.warning("zones.yaml не задан/не найден -> zone_id=None до калибровки (норма до пилота).")

        self.identity = identity_enricher or NullIdentityEnricher()
        if identity_enricher is None:
            logger.info("Идентификация не подключена (NullIdentityEnricher). "
                        "Реальный энричер — face/identity.py через identity_enricher=.")

        self.primary_yolo = None
        self.extra_models = []  # [(model, names_dict)]
        self.class_names_primary = {}
        self.load_models()

        # версии моделей для факта (§34.2). Подмена детектора/трекера -> бамп вручную/из конфига.
        self.model_versions = model_versions or {
            "detector": f"yolov8s+glove@{self._weights_fingerprint()}",
            "tracker": "bytetrack-supervision" if SUPERVISION_AVAILABLE else "none",
            "code_version": CODE_VERSION,
        }
        # NOTE/КЛАРИФИКАЦИЯ: §13.1 требует model_versions на ФАКТЕ, §34.2 описывает поле на СОБЫТИИ
        # и перечисляет arcface/segmentation/classifier. На перцептивном факте осмысленны только
        # detector/tracker/code_version (факт identity-free, без сегментации/классификации). Остальные
        # ключи §34.2 добавляются downstream на событиях, которые их реально использовали.

        # трекинг: по экземпляру ByteTrack на камеру (см. пояснение лиду)
        self._trackers = {}
        self.lineage = LineageManager(cfg["track_max_gap_sec"])

    # ----- модели -----
    def _weights_fingerprint(self):
        try:
            p = os.path.join(self.model_dir, "yolov8s.pt")
            return str(int(os.path.getmtime(p))) if os.path.exists(p) else "unknown"
        except Exception:
            return "unknown"

    def load_models(self):
        self.primary_yolo = None
        self.extra_models = []
        self.class_names_primary = {}
        if not ULTRALYTICS_AVAILABLE:
            return
        try:
            primary_path = os.path.join(self.model_dir, "yolov8s.pt")
            self.primary_yolo = YOLO(primary_path)
            names = getattr(self.primary_yolo, "names", {}) or {}
            self.class_names_primary = {int(k): str(v) for k, v in dict(names).items()}
        except Exception:
            logger.exception("Не удалось загрузить yolov8s.pt из %s", self.model_dir)
        try:
            glove_path = os.path.join(self.model_dir, "best_glove_model.pt")
            if os.path.exists(glove_path):
                m = YOLO(glove_path)
                names = getattr(m, "names", {}) or {}
                self.extra_models.append((m, {int(k): str(v) for k, v in dict(names).items()}))
                logger.info("Загружена glove-модель: %s", glove_path)
        except Exception:
            logger.exception("Не удалось загрузить glove-модель.")

    def decode_frame(self, b64jpeg):
        try:
            arr = np.frombuffer(base64.b64decode(b64jpeg), dtype=np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception:
            return None

    # ----- детекция (БАТЧЕМ, §11.6 п.1: запрещён один predict на кадр) -----
    def _predict_batch(self, model, names_dict, frames):
        """YOLO.predict по СПИСКУ кадров -> список (по кадру) детекций. Трекинг делаем отдельно."""
        per_frame = [[] for _ in frames]
        try:
            results = model.predict(frames, imgsz=640, conf=self.cmap.conf_floor,
                                    device=self.device, verbose=False)
            for i, r in enumerate(results):
                boxes = getattr(r, "boxes", None)
                if boxes is None:
                    continue
                for box in boxes:
                    xyxy = box.xyxy[0].tolist() if hasattr(box.xyxy, "tolist") else list(box.xyxy[0])
                    conf = float(box.conf[0])
                    cls_idx = int(box.cls[0])
                    raw = names_dict.get(cls_idx, str(cls_idx))
                    canonical = self.cmap.to_canonical(raw)
                    if canonical is None:
                        continue  # неизвестный/нерелевантный класс — отброс
                    if conf < self.cmap.min_conf.get(canonical, self.cmap.default_min_conf):
                        continue  # покласный порог ДЕТЕКЦИИ (не §32.4 — тот в интерпретации)
                    per_frame[i].append({
                        "bbox": [float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])],
                        "confidence": conf, "canonical": canonical, "raw": raw,
                    })
        except Exception:
            logger.exception("predict (batch) упал на одной модели")
        return per_frame

    def detect_batch(self, frames):
        """Список кадров -> список merged-детекций по кадру. Детекция stateless -> батчится свободно."""
        if not frames:
            return []
        prim = (self._predict_batch(self.primary_yolo, self.class_names_primary, frames)
                if self.primary_yolo is not None else [[] for _ in frames])
        extra = [self._predict_batch(m, names, frames) for m, names in self.extra_models]
        out = []
        for i in range(len(frames)):
            dets = list(prim[i])
            for ex in extra:
                dets += ex[i]
            merged = []
            for d in sorted(dets, key=lambda x: -x["confidence"]):
                if not any(d["canonical"] == m["canonical"] and iou_xyxy(d["bbox"], m["bbox"]) > 0.5
                           for m in merged):
                    merged.append(d)
            out.append(merged)
        return out

    def detect(self, frame):
        """Совместимость: одиночный кадр через батч-путь."""
        return self.detect_batch([frame])[0]

    # ----- трекинг -----
    def _get_tracker(self, camera_id):
        if camera_id not in self._trackers:
            if not SUPERVISION_AVAILABLE:
                self._trackers[camera_id] = None
            else:
                self._trackers[camera_id] = _make_bytetrack(
                    self.cfg["fps"], self.cfg["det_conf_enter"],
                    self.cfg["track_max_gap_sec"], self.cfg["bytetrack_match_thresh"])
        return self._trackers[camera_id]

    def track(self, camera_id, merged):
        """merged -> список с track_id. Возвращает [{'bbox','confidence','canonical','raw','track_id'}]."""
        if not merged:
            return []
        tracker = self._get_tracker(camera_id)
        if tracker is None:
            # fail-loud: трекинга нет, отдаём покадровые псевдо-id и шумим в лог
            logger.warning("[%s] нет supervision -> нестабильные track_id", camera_id)
            return [{**d, "track_id": -1} for d in merged]

        xyxy = np.array([d["bbox"] for d in merged], dtype=float)
        conf = np.array([d["confidence"] for d in merged], dtype=float)
        cls_id = np.array([self.cmap.cls_to_id[d["canonical"]] for d in merged], dtype=int)
        sv_det = sv.Detections(xyxy=xyxy, confidence=conf, class_id=cls_id)
        tracked = tracker.update_with_detections(sv_det)

        result = []
        for i in range(len(tracked)):
            tb = [float(v) for v in tracked.xyxy[i]]
            tid = tracked.tracker_id[i]
            if tid is None:
                continue
            canonical = self.cmap.id_to_cls.get(int(tracked.class_id[i]))
            # восстановить raw (gloved/bare) по IoU с дотрекинговой детекцией того же класса
            raw = None
            best, best_iou = None, 0.3
            for d in merged:
                if d["canonical"] == canonical:
                    j = iou_xyxy(tb, d["bbox"])
                    if j > best_iou:
                        best_iou, best, raw = j, d, d["raw"]
            result.append({
                "bbox": tb,
                "confidence": float(tracked.confidence[i]) if tracked.confidence is not None else (best["confidence"] if best else 0.0),
                "canonical": canonical,
                "raw": raw,
                "track_id": int(tid),
            })
        return result

    # ----- факты §13.1 -----
    def build_facts(self, camera_id, ts_ms, tracked):
        facts = []
        for t in tracked:
            cls = t["canonical"]
            if cls is None:
                continue
            tid = t["track_id"]
            lineage = self.lineage.lineage_for(camera_id, tid, ts_ms)
            zone_id = None
            if self.zone_resolver is not None:
                try:
                    zone_id = self.zone_resolver.resolve(camera_id, foot_point(t["bbox"]),
                                                          track_key=(camera_id, tid))
                except Exception:
                    logger.debug("zone resolve failed", exc_info=True)

            def _fact(object_class, conf):
                return {
                    "fact_id": str(uuid.uuid4()),
                    "timestamp": ts_ms,
                    "camera_id": camera_id,
                    "track_id": str(tid),
                    "track_lineage_id": lineage,
                    "object_class": object_class,
                    "bbox": [round(v, 2) for v in t["bbox"]],
                    "confidence": round(float(conf), 4),
                    "zone_id": zone_id,
                    "model_versions": dict(self.model_versions),
                }

            facts.append(_fact(cls, t["confidence"]))

            # ⚠ ОТКЛОНЕНИЕ/РЕШЕНИЕ (рука в перчатке -> ДВА факта): схема §13.1 фиксирована и не
            # имеет поля «glove_state». Чтобы downstream мог восстановить сигнал no_gloves, не
            # вводя внесхемных полей, я СО-локирую факт `glove` с фактом `hands` для руки в перчатке
            # (оба класса есть в §11.2). bare-рука -> только `hands`; no_gloves = `hands` в рабочей
            # зоне БЕЗ со-локированного `glove` (выводится в слое интерпретации, контур §18).
            if cls == "hands" and self.cmap.glove_state_of(t.get("raw")) == "gloved":
                facts.append(_fact("glove", t["confidence"]))
        return facts

    # ----- compat-представление (старый формат objects) -----
    @staticmethod
    def _compat_objects(facts):
        # DEPRECATED: для кода, ещё читающего packet['objects'] с полями id/class/bbox.
        # Канон — packet['facts'] (§13.1). Убрать после миграции downstream на факты.
        return [{"id": f["track_id"], "class": f["object_class"],
                 "bbox": f["bbox"], "confidence": f["confidence"],
                 "zone_id": f["zone_id"]} for f in facts]

    def _drain_batch(self):
        """Собрать батч кадров: 1 блокирующе + добор до batch_max за batch_timeout_sec."""
        batch = []
        try:
            first = self.in_queue.get(timeout=1.0)
        except Exception:
            return batch
        pkts = [first]
        deadline = time.time() + self.cfg["batch_timeout_sec"]
        while len(pkts) < self.cfg["batch_max"] and time.time() < deadline:
            try:
                pkts.append(self.in_queue.get_nowait())
            except Exception:
                break
        for pkt in pkts:
            frame = self.decode_frame(pkt.get("frame")) if pkt.get("frame") else None
            if frame is None:
                logger.warning("[%s] кадр не декодирован", pkt.get("camera_id", "unknown"))
                continue
            batch.append((pkt.get("camera_id", "unknown"), _to_ms_utc(pkt.get("timestamp")), frame))
        return batch

    def run(self):
        logger.info("PerceptionWorker запущен (batch_max=%d).", self.cfg["batch_max"])
        while not self.stop_event.is_set():
            batch = self._drain_batch()
            if not batch:
                continue
            try:
                frames = [b[2] for b in batch]
                per_frame_dets = self.detect_batch(frames)  # ОДИН проход детектора на батч (§11.6 п.1)
            except Exception:
                logger.exception("detect_batch упал")
                continue
            # трекинг и факты — покадрово, ByteTrack-состояние на камеру сохраняется
            for (camera_id, ts_ms, frame), merged in zip(batch, per_frame_dets):
                try:
                    tracked = self.track(camera_id, merged)
                    facts = self.build_facts(camera_id, ts_ms, tracked)
                    identities = self.identity.enrich(facts, frame)  # ML-03 seam; по умолчанию []
                    self.lineage.gc(ts_ms)
                    out_pkt = {
                        "camera_id": camera_id,
                        "timestamp": ts_ms,                       # мс UTC (§12.4)
                        "facts": facts,                           # КАНОН §13.1
                        "identities": identities,                 # поток идентичности (§12.1), ML-03
                        "objects": self._compat_objects(facts),   # DEPRECATED-совместимость
                        "frame_bgr": frame,                       # кадр отдельно (не в фактах, §13.4)
                    }
                    if self.out_queue is not None:
                        try:
                            self.out_queue.put_nowait(out_pkt)
                        except Exception:
                            logger.debug("out_queue полна — пакет отброшен")
                except Exception:
                    logger.exception("Ошибка обработки кадра [%s]", camera_id)
        logger.info("PerceptionWorker остановлен.")

    def stop(self):
        self.stop_event.set()

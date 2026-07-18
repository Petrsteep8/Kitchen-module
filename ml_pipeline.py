# ml_pipeline.py — сборка ML-конвейера end-to-end (ML-01..04 + идентичность + кросс-камера)
"""
Чистая ML-сторона конвейера, продуцирующая ПОТОК ФАКТОВ §13.1:

  камеры (CameraWorker, fps>=8)
     -> frame_queue
     -> PerceptionSharder  (PerceptionWorker на GPU: батч-детекция + ByteTrack + факты §13.1 + zone_id)
     -> perception_queue
     -> CrossCameraLinker  (глобальный track_lineage_id + перенос идентичности фронт→тыл)
     -> SINK (callback): сюда backend подключает слой ИНТЕРПРЕТАЦИИ (FSM/правила/DBI).

Это НЕ заменяет run_multi_camera.py backend-а: тот мигрирует на потребление этого потока фактов
(сейчас он завязан на старый формат `objects` и хардкод `test_key`). Здесь — только ML.

Секреты: ключ DBI берётся из ENV (KITCHEN_DBI_KEY), НЕ из кода.
"""
import logging
import os
import queue
import threading

from kitchen_vision.video_ingestion import CameraWorker
from kitchen_vision.ai_perception import PerceptionWorker  # noqa: F401  (через sharder)
from kitchen_vision.perception_sharder import PerceptionSharder
from kitchen_vision.multiobject_tracking import CrossCameraLinker, build_appearance_encoder
from facts_consumer import FactsConsumer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ml_pipeline")

consumer = FactsConsumer()

def build_identity_enricher(device="cuda", gallery_path="kitchen_vision/face/faces_db.npz"):
    """Собрать IdentityEnricher (ML-03). None, если идентичность не нужна/insightface отсутствует."""
    try:
        from kitchen_vision.face.arcface import ArcFaceRecognizer
        from kitchen_vision.face.face_db import FaceDatabase
        from kitchen_vision.face.identity import IdentityEnricher
        arc = ArcFaceRecognizer(device=device)
        gallery = FaceDatabase(gallery_path)
        return IdentityEnricher(arc, gallery)
    except Exception:
        logger.exception("IdentityEnricher не собран — идентификация отключена")
        return None

class MLPipeline:
    def __init__(self, cameras, devices=("cuda:0",), class_map_path="class_mapping.yaml",
                 zones_path=None, fps=8, sink=None, reid_model_path="",
                 camera_adjacency=None, enable_identity=True):
        """
        cameras: [{"camera_id":..., "source": rtsp/idx}, ...]
        devices: ["cuda:0","cuda:1"] либо ["cpu"]
        sink   : callable(packet) — потребитель фактов (по умолчанию — лог)
        """
        self.cameras = cameras
        self.fps = fps
        self.sink = sink or self._default_sink
        self.frame_q = queue.Queue(maxsize=64)
        self.perception_q = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._workers = []

        _arc_device = "cuda" if (devices and "cuda" in devices[0]) else "cpu"
        identity = build_identity_enricher(device=_arc_device) if enable_identity else None

        self.sharder = PerceptionSharder(
            camera_ids=[c["camera_id"] for c in cameras], devices=list(devices),
            out_queue=self.perception_q, class_map_path=class_map_path,
            zones_path=zones_path, identity_enricher=identity, config={"fps": fps},
        )
        self.linker = CrossCameraLinker(
            encoder=build_appearance_encoder(prefer="osnet", model_path=reid_model_path,
                                             device=devices[0] if devices else "cpu"),
            config={"camera_adjacency": camera_adjacency} if camera_adjacency else None,
        )
        # ключ DBI — из ENV (для backend-стадии); демонстрируем, что хардкода нет
        self.dbi_key = os.environ.get("KITCHEN_DBI_KEY")
        if not self.dbi_key:
            logger.warning("KITCHEN_DBI_KEY не задан в ENV — backend-стадия не сможет слать в DBI")

    @staticmethod
    def _default_sink(packet):
        # logger.info("[facts] cam=%s n_facts=%d n_ident=%d",
        #             packet.get("camera_id"), len(packet.get("facts", [])), len(packet.get("identities", [])))
        consumer.put_facts(packet)

    def _consume(self):
        while not self._stop.is_set():
            try:
                pkt = self.perception_q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                pkt = self.linker.process(pkt)   # глобальные линии + перенос идентичности
                self.sink(pkt)
            except Exception:
                logger.exception("Ошибка в стадии линкера/сейка")

    def start(self):
        for cam in self.cameras:
            w = CameraWorker(camera_id=cam["camera_id"], source=cam["source"],
                             out_queue=self.frame_q, target_fps=self.fps,
                             target_resolution=cam.get("resolution", (1280, 720)))
            w.daemon = True
            w.start()
            self._workers.append(w)
            logger.info("Камера %s запущена (fps=%d)", cam["camera_id"], self.fps)

        self.sharder.start(source_queue=self.frame_q)
        threading.Thread(target=self._consume, daemon=True).start()
        logger.info("ML-конвейер запущен.")

    def stop(self):
        self._stop.set()
        self.sharder.stop()
        for w in self._workers:
            try:
                w.stop()
            except Exception:
                pass


if __name__ == "__main__":
    # пример: 2 камеры, 1 GPU, зоны не калиброваны (zone_id=None — норма до пилота)
    pipe = MLPipeline(
        cameras=[{"camera_id": "Kitchen_1", "source": 0}],
        devices=["cuda:0"], class_map_path="class_mapping.yaml", zones_path="zones.yaml", fps=8,
        camera_adjacency={},
    )
    pipe.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pipe.stop()

# perception_sharder.py — Kitchen Module, шардинг камер по GPU (задача ML-01)
"""
Распределение камер по нескольким перцептивным воркерам, каждый ПРИШПИЛЕН к своему GPU.

Зачем: прошлая схема лила ВСЕ камеры в одну очередь -> один PerceptionWorker -> один GPU.
Это потолок и по FPS, и по числу камер. Здесь — K шардов (по одному на устройство), диспетчер
маршрутизирует кадры по camera_id в нужный шард, выходы сливаются в общую out_queue.

⚠ СОСТОЯНИЕ ML-01: КОД ГОТОВ, ПРОВИЖН INFRA-PENDING. Реальное число GPU и то, один это сервер
или несколько, — решение INFRA (задача заблокирована INFRA-01/INFRA-09 на доске). Модуль
параметризован списком устройств: с одним GPU работает как раньше (1 шард), с N — раскладывает
камеры детерминированно. fps>=8 (NFR §6) задаётся в CameraWorker.target_fps (а не здесь).

Каждый PerceptionWorker уже держит ОТДЕЛЬНЫЙ ByteTrack на camera_id, поэтому камеры одного шарда
не путают треки между собой; шардинг лишь добавляет ось «камера -> устройство».
"""
import logging
import queue
import threading

from ai_perception import PerceptionWorker

logger = logging.getLogger("perception_sharder")


def assign_cameras(camera_ids, devices):
    """Детерминированно разложить камеры по устройствам (round-robin).

    camera_ids: ["Kitchen_1", "Kitchen_2", ...]; devices: ["cuda:0","cuda:1"] или ["cpu"].
    Возвращает {device: [camera_id, ...]}. Камера всегда попадает на одно и то же устройство
    (стабильно при рестарте — важно, чтобы состояние трекера не «переезжало» между GPU).
    """
    if not devices:
        devices = ["cpu"]
    mapping = {d: [] for d in devices}
    for i, cam in enumerate(sorted(camera_ids)):
        mapping[devices[i % len(devices)]].append(cam)
    return mapping


class PerceptionSharder:
    def __init__(self, camera_ids, devices, out_queue, class_map_path="class_mapping.yaml",
                 zones_path=None, model_dir="./models", identity_enricher=None,
                 config=None, model_versions=None, shard_queue_size=32):
        """
        camera_ids   : список всех camera_id точки
        devices      : ["cuda:0", "cuda:1", ...] либо ["cpu"]; len(devices) = число шардов
        out_queue    : ОБЩАЯ очередь фактов на выходе (сюда сливаются все шарды)
        остальное    : пробрасывается в каждый PerceptionWorker
        """
        self.out_queue = out_queue
        self.cam_to_device = {}
        self.shards = {}          # device -> {"worker", "queue", "cams"}
        self._stop = threading.Event()

        plan = assign_cameras(camera_ids, devices)
        logger.info("Шардинг камер по устройствам: %s", {d: c for d, c in plan.items()})

        # NOTE (INFRA): пин процесса/воркера к GPU здесь — на уровне device= в predict.
        # Если GPU на РАЗНЫХ хостах, одного процесса мало -> нужен запуск шардов по хостам
        # (отдельные процессы + общий брокер очередей вместо in-memory queue). Уточнить топологию.
        for device, cams in plan.items():
            if not cams:
                continue
            in_q = queue.Queue(maxsize=shard_queue_size)
            worker = PerceptionWorker(
                in_queue=in_q, out_queue=out_queue, class_map_path=class_map_path,
                zones_path=zones_path, model_dir=model_dir, identity_enricher=identity_enricher,
                config=config, model_versions=model_versions, device=device,
            )
            self.shards[device] = {"worker": worker, "queue": in_q, "cams": cams}
            for c in cams:
                self.cam_to_device[c] = device

        self._dispatcher = None

    def start(self, source_queue):
        """source_queue: общая очередь кадров от всех CameraWorker'ов (как сейчас в run_multi_camera)."""
        for device, shard in self.shards.items():
            shard["worker"].start()
            logger.info("Шард на %s запущен (камеры: %s)", device, shard["cams"])

        def _dispatch():
            while not self._stop.is_set():
                try:
                    pkt = source_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                cam = pkt.get("camera_id")
                device = self.cam_to_device.get(cam)
                if device is None:
                    # камера не была в плане шардинга (добавлена на лету) -> кладём в наименее
                    # загруженный шард и логируем; полноценно — пересобрать план.
                    device = min(self.shards, key=lambda d: self.shards[d]["queue"].qsize())
                    self.cam_to_device[cam] = device
                    logger.warning("Камера %s вне плана шардинга -> временно на %s", cam, device)
                q = self.shards[device]["queue"]
                try:
                    q.put_nowait(pkt)
                except queue.Full:
                    logger.debug("Очередь шарда %s полна — кадр %s отброшен", device, cam)

        self._dispatcher = threading.Thread(target=_dispatch, daemon=True)
        self._dispatcher.start()
        logger.info("Диспетчер кадров запущен.")

    def stop(self):
        self._stop.set()
        for shard in self.shards.values():
            try:
                shard["worker"].stop()
            except Exception:
                pass

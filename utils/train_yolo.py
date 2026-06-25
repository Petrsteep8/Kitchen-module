#!/usr/bin/env python3
# utils/train_yolo.py — дообучение детектора (бутстрап, раздел 7)
"""
Портативный скрипт дообучения YOLOv8 с заморозкой слоёв (замена версии с хардкодом D:/-путей).

Данные: YOLO-формат (Roboflow «YOLOv8» экспортирует именно его) — data.yaml с train/val/names.
Лицензия: Ultralytics — Enterprise (см. NOTICE). Кастомные веса остаются продуктом проекта.

Пример:
  python utils/train_yolo.py --weights models/best_glove_model.pt \\
      --data datasets/board/data.yaml --epochs 100 --freeze 20 --device 0
"""
import argparse
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description="Fine-tune YOLOv8 (freeze backbone).")
    ap.add_argument("--weights", default="models/yolov8s.pt", help="стартовые веса (.pt)")
    ap.add_argument("--data", required=True, help="путь к data.yaml (YOLO/Roboflow формат)")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr0", type=float, default=5e-4)
    ap.add_argument("--freeze", type=int, default=20, help="сколько слоёв заморозить (бутстрап на мало данных)")
    ap.add_argument("--device", default="0", help="'0' / '0,1' / 'cpu'")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--project", default="runs/finetune")
    ap.add_argument("--name", default="exp")
    args = ap.parse_args()

    weights = Path(args.weights)
    data = Path(args.data)
    if not weights.exists():
        raise SystemExit(f"Веса не найдены: {weights}")
    if not data.exists():
        raise SystemExit(f"data.yaml не найден: {data}")

    from ultralytics import YOLO  # импорт здесь, чтобы --help работал без установленного пакета
    model = YOLO(str(weights))
    model.train(
        data=str(data), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch,
        lr0=args.lr0, freeze=args.freeze, device=args.device, workers=args.workers,
        project=args.project, name=args.name,
    )
    # NOTE: после обучения прогнать quality_gate на приёмочном датасете (§8.4) — релиз только без
    # деградации Precision/Recall относительно предыдущего. Это CI-шаг, не часть этого скрипта.


if __name__ == "__main__":
    main()

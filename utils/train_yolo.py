from ultralytics import YOLO

if __name__ == '__main__':
    # 1. Загружаем модель с перчатками
    model = YOLO("D:/PycharmProjects/kitchen_cv_last/models/best_glove_model.pt")

    # 2. Обучаем с заморозкой слоёв
    model.train(
        data="D:/PycharmProjects/kitchen_cv_last/board_dataset/data.yaml",  # твой датасет с досками
        epochs=100,
        imgsz=640,
        batch=16,
        lr0=0.0005,
        freeze=20, # заморозка почти всей модели
        workers=0
    )

# register_face.py
import cv2
from kitchen_vision.face.arcface import ArcFaceRecognizer
from kitchen_vision.face.face_db import FaceDatabase

def main():
    # Инициализация ArcFace и базы
    arcface = ArcFaceRecognizer(device="cpu")  # или "cuda", если доступно
    face_db = FaceDatabase("../kitchen_vision/face/faces_db.npz", threshold=0.45)

    cam = cv2.VideoCapture(0)
    if not cam.isOpened():
        print("Не удалось открыть камеру")
        return

    print("Нажмите SPACE, чтобы зарегистрировать лицо, ESC для выхода")

    while True:
        ret, frame = cam.read()
        if not ret:
            continue

        cv2.imshow("Register Face", frame)
        key = cv2.waitKey(1) & 0xFF

        if key == 27:  # ESC
            print("Выход")
            break
        elif key == 32:  # SPACE
            # извлекаем эмбеддинг лица
            emb = arcface.extract(frame)
            if emb is not None:
                name = input("Введите имя/ID сотрудника: ").strip()
                if name:
                    face_db.add(name, emb)
                    print(f"[OK] Лицо {name} добавлено в базу!")
                else:
                    print("[ERROR] Имя не введено, регистрация отменена")
            else:
                print("[ERROR] Лицо не обнаружено, попробуйте снова")

    cam.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()

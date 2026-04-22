from ultralytics import YOLO
import cv2

model_path = "../models/yolo11n.pt"
model = YOLO(model_path)

cap = cv2.VideoCapture(0)  # 0 — вебкамера, или путь к видеофайлу
while True:
    ret, frame = cap.read()
    if not ret:
        break

    results = model.predict(frame, imgsz=640, conf=0.05)
    annotated_frame = results[0].plot()  # вернёт кадр с детекциями

    cv2.imshow("YOLOv8 Detection", annotated_frame)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()

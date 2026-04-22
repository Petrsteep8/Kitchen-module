# face_db.py
import os
import numpy as np

class FaceDatabase:
    def __init__(self, path, threshold=0.5):
        """
        path: путь к файлу базы (.npz)
        threshold: порог сходства для идентификации
        """
        self.path = path
        self.threshold = threshold
        self.data = {}  # {employee_id: embedding}

        if os.path.exists(path):
            self.load(path)
        else:
            print(f"[FaceDatabase] Файл не найден, создаем новую базу: {path}")
            self.save()

    def load(self, path=None):
        path = path or self.path
        try:
            data = np.load(path, allow_pickle=True)
            self.data = data.get("faces", {}).item() if "faces" in data else {}
            print(f"[FaceDatabase] Загружено {len(self.data)} лиц из {path}")
        except Exception as e:
            print(f"[FaceDatabase] Не удалось загрузить файл {path}: {e}")
            self.data = {}

    def save(self):
        np.savez(self.path, faces=self.data)
        print(f"[FaceDatabase] База сохранена: {self.path} (лиц: {len(self.data)})")

    def add(self, employee_id, embedding):
        """
        employee_id: уникальный идентификатор сотрудника
        embedding: вектор из ArcFace
        """
        self.data[employee_id] = embedding
        self.save()
        print(f"[FaceDatabase] Добавлено лицо: {employee_id}")

    def match(self, embedding):
        """
        Сравнивает embedding с базой.
        Возвращает (employee_id, score) если найдено, иначе (None, None)
        """
        if not self.data:
            return None, None

        best_id = None
        best_score = -1

        for eid, emb in self.data.items():
            # Косинусная схожесть
            score = np.dot(embedding, emb) / (np.linalg.norm(embedding) * np.linalg.norm(emb))
            if score > best_score:
                best_score = score
                best_id = eid

        if best_score >= self.threshold:
            return best_id, best_score
        else:
            return None, best_score

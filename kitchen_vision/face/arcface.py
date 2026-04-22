# ai_perception/face/arcface.py
import numpy as np
from insightface.app import FaceAnalysis

class ArcFaceRecognizer:
    def __init__(self, device="cuda"):
        self.app = FaceAnalysis(
            name="buffalo_l",
            providers=["CUDAExecutionProvider"] if device == "cuda" else ["CPUExecutionProvider"]
        )
        self.app.prepare(ctx_id=0 if device == "cuda" else -1)

    def extract(self, face_img):
        """
        face_img: BGR numpy image (cropped face)
        return: 512-d embedding or None
        """
        faces = self.app.get(face_img)
        if not faces:
            return None
        return faces[0].embedding

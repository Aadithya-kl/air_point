# core/face_tracker.py
"""
MediaPipe FaceLandmarker wrapper (Tasks API) for real-time lip and facial landmark tracking.
Extracts facial landmarks and calculates mouth center and scale for lip-touch gestures.
"""

import logging
import os
import time
from dataclasses import dataclass

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python.vision import FaceLandmarker, FaceLandmarkerOptions, RunningMode
import numpy as np

from config import MP_FACE_MODEL_PATH, LIP_MUTE_FACE_CONFIDENCE_MIN
from core.tracker import Landmark

logger = logging.getLogger(__name__)

# Face Mesh landmark indices
LM_UPPER_LIP_INNER = 13
LM_LOWER_LIP_INNER = 14
LM_UPPER_LIP_OUTER = 0
LM_LOWER_LIP_OUTER = 17
LM_MOUTH_CORNER_LEFT = 61   # Viewer left / anatomical right
LM_MOUTH_CORNER_RIGHT = 291 # Viewer right / anatomical left
LM_CHEEK_LEFT = 234         # Face width reference
LM_CHEEK_RIGHT = 454


@dataclass
class FaceResult:
    """Represents facial tracking results for one frame."""
    detected: bool = False
    lip_center: tuple[float, float, float] | None = None   # (x, y, z)
    mouth_width: float = 0.0                               # Scale normalization
    face_width: float = 0.0
    right_ear_pos: tuple[float, float, float] | None = None  # Anatomical right ear (viewer right)
    left_ear_pos: tuple[float, float, float] | None = None   # Anatomical left ear (viewer left)
    landmarks: list[Landmark] | None = None
    confidence: float = 0.0


class FaceTracker:
    """
    Wraps MediaPipe FaceLandmarker for face and lip region extraction.
    Supports video mode and graceful fallback when model or camera is unavailable.
    """

    def __init__(self, model_path: str = MP_FACE_MODEL_PATH) -> None:
        self._detector: FaceLandmarker | None = None
        self._is_ready: bool = False

        if not os.path.exists(model_path):
            logger.warning("Face landmarker model not found at %s. Lip gesture will be disabled.", model_path)
            return

        try:
            base_options = python.BaseOptions(model_asset_path=model_path)
            options = FaceLandmarkerOptions(
                base_options=base_options,
                running_mode=RunningMode.VIDEO,
                num_faces=1,
                min_face_detection_confidence=LIP_MUTE_FACE_CONFIDENCE_MIN,
                min_face_presence_confidence=LIP_MUTE_FACE_CONFIDENCE_MIN,
                min_tracking_confidence=LIP_MUTE_FACE_CONFIDENCE_MIN,
            )
            self._detector = FaceLandmarker.create_from_options(options)
            self._is_ready = True
            logger.info("FaceLandmarker initialized successfully (%s)", model_path)
        except Exception as e:
            logger.error("Failed to initialize FaceLandmarker: %s", e)
            self._detector = None
            self._is_ready = False

    @property
    def is_ready(self) -> bool:
        return self._is_ready

    def process(self, frame: np.ndarray, timestamp_ms: int | None = None) -> FaceResult:
        """
        Processes a BGR frame (same mirrored frame as HandTracker) and returns FaceResult.
        """
        if not self._is_ready or self._detector is None:
            return FaceResult(detected=False)

        if timestamp_ms is None:
            timestamp_ms = int(time.perf_counter() * 1000)

        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = self._detector.detect_for_video(mp_image, timestamp_ms)
        except Exception as e:
            logger.debug("Face tracking frame error: %s", e)
            return FaceResult(detected=False)

        if not result.face_landmarks or len(result.face_landmarks) == 0:
            return FaceResult(detected=False)

        raw_lms = result.face_landmarks[0]
        lms = [
            Landmark(x=lm.x, y=lm.y, z=lm.z, visibility=getattr(lm, "visibility", 0.0) or 0.0)
            for lm in raw_lms
        ]

        # Calculate lip center between upper and lower inner lips
        lip_x = (lms[LM_UPPER_LIP_INNER].x + lms[LM_LOWER_LIP_INNER].x) / 2.0
        lip_y = (lms[LM_UPPER_LIP_INNER].y + lms[LM_LOWER_LIP_INNER].y) / 2.0
        lip_z = (lms[LM_UPPER_LIP_INNER].z + lms[LM_LOWER_LIP_INNER].z) / 2.0

        # Scale metrics
        mw = ((lms[LM_MOUTH_CORNER_LEFT].x - lms[LM_MOUTH_CORNER_RIGHT].x)**2 +
              (lms[LM_MOUTH_CORNER_LEFT].y - lms[LM_MOUTH_CORNER_RIGHT].y)**2)**0.5
        mouth_width = max(mw, 1e-4)

        fw = ((lms[LM_CHEEK_LEFT].x - lms[LM_CHEEK_RIGHT].x)**2 +
              (lms[LM_CHEEK_LEFT].y - lms[LM_CHEEK_RIGHT].y)**2)**0.5
        face_width = max(fw, 1e-4)

        # On the mirrored frame (selfie view), user's anatomical right ear is on viewer right (higher x),
        # and user's anatomical left ear is on viewer left (lower x).
        chk_a = lms[LM_CHEEK_LEFT]   # 234
        chk_b = lms[LM_CHEEK_RIGHT]  # 454
        if chk_a.x > chk_b.x:
            right_ear = (chk_a.x, chk_a.y, chk_a.z)
            left_ear = (chk_b.x, chk_b.y, chk_b.z)
        else:
            right_ear = (chk_b.x, chk_b.y, chk_b.z)
            left_ear = (chk_a.x, chk_a.y, chk_a.z)

        return FaceResult(
            detected=True,
            lip_center=(lip_x, lip_y, lip_z),
            mouth_width=mouth_width,
            face_width=face_width,
            right_ear_pos=right_ear,
            left_ear_pos=left_ear,
            landmarks=lms,
            confidence=1.0,
        )

    def close(self) -> None:
        if self._detector:
            try:
                self._detector.close()
            except Exception:
                pass
            self._detector = None
            self._is_ready = False

    def __enter__(self) -> "FaceTracker":
        return self

    def __exit__(self, *_) -> None:
        self.close()

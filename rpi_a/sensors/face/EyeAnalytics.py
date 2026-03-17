"""
EyeAnalytics.py
Handles gaze estimation and classification using iris offsets + head pose fusion.

Wraps:
    - Iris offset estimation (lx, ly, rx, ry)
    - GazeCalibrator (5-point calibration + classify)

HeadPose (yaw, pitch) is computed separately in HeadPose.py and passed in for fusion in main.py.

Usage:
    eye = EyeAnalytics(screen_w, screen_h)

    # Calibration loop
    while not eye.calibration_done:
        status = eye.update_calibration(landmarks, img_w, img_h, yaw, pitch, space_pressed)
        canvas = eye.draw_calibration_screen()
        cv2.imshow("Calibration", canvas)

    # Analytics loop
    gaze_quadrant = eye.process(landmarks, img_w, img_h, yaw, pitch)
"""
 
import numpy as np
from .GazeCalibrator import GazeCalibrator
 
# ---------------------------------------------------------------------------
# Iris / eye landmark indices (MediaPipe FaceMesh with refine_landmarks=True)
# ---------------------------------------------------------------------------
_LEFT_EYE_INDICES  = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398]
_RIGHT_EYE_INDICES = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]

_LEFT_IRIS_INDICES  = [468, 469, 470, 471, 472]
_RIGHT_IRIS_INDICES = [473, 474, 475, 476, 477]


def _estimate_iris_offset(landmarks, img_w, img_h):
    """
    Computes normalised iris offset relative to eye width for both eyes.
    Returns (left_gaze_x, left_gaze_y, right_gaze_x, right_gaze_y) where positive x = right, positive y = down.
    """
    def _offset(iris_ids, eye_ids):
        iris_center = np.mean(
            [[landmarks[i].x * img_w, landmarks[i].y * img_h] for i in iris_ids],
            axis=0,
        )
        eye_inner = np.array([landmarks[eye_ids[0]].x * img_w, landmarks[eye_ids[0]].y * img_h])
        eye_outer = np.array([landmarks[eye_ids[8]].x * img_w, landmarks[eye_ids[8]].y * img_h])
        eye_width = np.linalg.norm(eye_outer - eye_inner) + 1e-6
        eye_mid   = (eye_inner + eye_outer) / 2
        offset    = (iris_center - eye_mid) / eye_width
        return float(offset[0]), float(offset[1])
 
    lx, ly = _offset(_LEFT_IRIS_INDICES,  _LEFT_EYE_INDICES)
    rx, ry = _offset(_RIGHT_IRIS_INDICES, _RIGHT_EYE_INDICES)
    return lx, ly, rx, ry

class EyeAnalytics:
    def __init__(self, screen_w, screen_h, collect_seconds=5.0):
        self._calibrator   = GazeCalibrator(screen_w, screen_h, collect_seconds=collect_seconds)
        self._screen_w     = screen_w
        self._screen_h     = screen_h
    
    # Expose calibrator methods for external control (e.g. from main.py)
    @property
    def calibration_done(self):
        return self._calibrator.done
    
    def update_calibration(self, landmarks, img_w, img_h, yaw, pitch, space_pressed) -> str:
        """
        Call every frame during calibration loop.
        space_pressed: bool from main.py (cv2.waitKey)
        Returns status string to display on webcam feed.
        """
        lx, ly, rx, ry = _estimate_iris_offset(landmarks, img_w, img_h)
        avg_gx = (lx + rx) / 2.0
        avg_gy = (ly + ry) / 2.0
        
        return self._calibrator.update(avg_gx, avg_gy, space_pressed, yaw, pitch)
    
    def draw_calibration_screen(self):
        """Returns calibration canvas (BGR image) for cv2.imshow."""
        
        return self._calibrator.draw_calibration_screen(self._screen_w, self._screen_h)
    
    # Analytics after calibration is done
    def process(self, landmarks, img_w, img_h, yaw, pitch) -> str:
        """
        Classify gaze quadrant for current frame.
        Must call after calibration_done is True.
 
        Args:
            landmarks   : result.multi_face_landmarks[0].landmark
            img_w/img_h : frame dimensions
            yaw, pitch  : from HeadPose.estimate(), passed in from main.py
 
        Returns:
            gaze_quadrant : str — "CENTER", "TOP-LEFT", "TOP-RIGHT",
                                  "BOTTOM-LEFT", "BOTTOM-RIGHT", or "UNCALIBRATED"
        """
        lx, ly, rx, ry = _estimate_iris_offset(landmarks, img_w, img_h)
        gaze_quadrant, _, _, _, _ = self._calibrator.classify(lx, ly, rx, ry, yaw, pitch)
        return gaze_quadrant
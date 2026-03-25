"""
FaceAnalytics.py
Facial signal processing — blink detection, brow furrow, emotion classification,
head pose direction, and frustration/attention scoring.
 
HeadPose (pitch, yaw, roll) is computed in main.py via HeadPose.py and passed in.
FaceMesh inference is done OUTSIDE this module (in main.py).
Landmarks are passed in directly.
 
Usage:
    face = FaceAnalytics()
 
    # Calibration loop (passive — no SPACE needed, just collect for N seconds)
    face.add_calibration_sample(landmarks, pitch, yaw, roll)
    # after N seconds:
    ok = face.finish_calibration()
 
    # Analytics loop
    result = face.process(landmarks, img_w, img_h, pitch, yaw, roll, dt, elapsed)
    # result keys: emotion, direction, avg_ear, blink_count, blink_rate,
    #              brow_delta, mouth_delta, frust_accumulator, pose_accumulator,
    #              attention_score, frustration_score
"""
 
import numpy as np

# CONFIGURABLE PARAMETERS
_EAR_THRESHOLD = 0.20
_BLINK_CONSEC_FRAMES = 2
_LEFT_EYE_EAR  = [362, 385, 387, 263, 373, 380]
_RIGHT_EYE_EAR = [33,  160, 158, 133, 153, 144]

# Head pose direction thresholds (degrees, applied after baseline subtraction)
_YAW_THRESHOLD   = 20
_PITCH_THRESHOLD = 10
_ROLL_THRESHOLD  = 20 # reserved — roll not currently used in direction detection
_POSE_SMOOTH_N   = 10

# Brow / emotion smoothing + thresholds
_BROW_SMOOTH_N        = 15
_CONFUSED_THRESHOLD   = 0.25
_FRUSTRATED_THRESHOLD = 0.45
 
# Normal blink rate range (blinks/min)
_NORMAL_BLINK_MIN = 12.0
_NORMAL_BLINK_MAX = 20.0
 
# Frustration accumulator
_FRUST_FILL_RATE  = 8.0
_FRUST_DRAIN_RATE = 3.0
_FRUST_MAX        = 100.0
 
# Pose accumulator
_POSE_FILL_RATE  = 10.0
_POSE_DRAIN_RATE = 4.0
_POSE_MAX        = 100.0

# ----------------------------------------------------------------------------
# PRIVATE UTILITY FUNCTIONS
# ----------------------------------------------------------------------------

def _calculate_ear(landmarks, eye_indices, img_w, img_h):
    pts = [(landmarks[i].x * img_w, landmarks[i].y * img_h) for i in eye_indices]
    v1 = np.linalg.norm(np.array(pts[1]) - np.array(pts[5]))
    v2 = np.linalg.norm(np.array(pts[2]) - np.array(pts[4]))
    h  = np.linalg.norm(np.array(pts[0]) - np.array(pts[3]))
    return round((v1 + v2) / (2.0 * h), 3) if h > 0 else 0.0

def _get_brow_furrow(landmarks):
    try:
        brow_y = (landmarks[4].y + landmarks[9].y) / 2
        nose_y = landmarks[6].y
        return round(abs(brow_y - nose_y) * 100, 3)
    except Exception:
        return None
    
def _get_mouth_frown(landmarks):
    try:
        mouth_corner_y = (landmarks[61].y + landmarks[291].y) / 2
        chin_y         = landmarks[152].y
        return round(abs(mouth_corner_y - chin_y) * 100, 3)
    except Exception:
        return None
    
def _get_eye_squint(landmarks):
    try:
        left_eye_v = abs(landmarks[159].y - landmarks[145].y)
        left_eye_h = abs(landmarks[33].x  - landmarks[133].x)
        return round((left_eye_v / max(left_eye_h, 0.001)) * 100, 3)
    except Exception:
        return None
    
def _classify_emotion(brow_delta, mouth_delta):
    frustration_signal = brow_delta * 0.7 + max(-mouth_delta, 0) * 0.3
    # print(f"  [DEBUG] brow_delta:{brow_delta:.3f} mouth_delta:{mouth_delta:.3f} signal:{frustration_signal:.3f}")
    if frustration_signal >= _FRUSTRATED_THRESHOLD:
        return "FRUSTRATED", frustration_signal
    elif frustration_signal >= _CONFUSED_THRESHOLD:
        return "CONFUSED", frustration_signal
    else:
        return "NEUTRAL", frustration_signal
    
def _get_direction(yaw, pitch):
    dirs = []
    if abs(pitch) > _YAW_THRESHOLD:   dirs.append("LEFT" if pitch < 0 else "RIGHT")
    if abs(yaw)   > _PITCH_THRESHOLD: dirs.append("DOWN" if yaw > 0 else "UP")
    return " + ".join(dirs) if dirs else "FORWARD"


class FaceAnalytics:
    def __init__(self):
        # Calibration sample accumulators
        self._cal_pitches = []
        self._cal_yaws = []
        self._cal_rolls = []
        self._cal_brow = []
        self._cal_mouth = []
        self._cal_eye = []
 
        # Baselines (set after finish_calibration)
        self._baseline_pitch = 0.0
        self._baseline_yaw = 0.0
        self._baseline_roll = 0.0
        self._baseline_brow = 0.0
        self._baseline_mouth = 0.0
        self._baseline_eye = 0.0
        self.calibrated = False
 
        # Smoothing buffers
        self._pitch_buf = []
        self._yaw_buf = []
        self._roll_buf = []
        self._brow_buf = []
        self._mouth_buf = []
        self._eye_buf = []
 
        # Blink state
        self._blink_count = 0
        self._consec_frames = 0
        self._eye_closed = False
        self._min_ear_val = 1.0
        self._blink_timestamps = []  # elapsed-time stamps
 
        # Accumulators
        self._frust_acc = 0.0
        self._pose_acc = 0.0
        
    # Calibration
    def add_calibration_sample(self, landmarks, pitch, yaw, roll):
        """
        Call every frame during calibration phase.
        pitch, yaw, roll: raw values from HeadPose.estimate()
        """
        if pitch is not None:
            self._cal_pitches.append(pitch)
            self._cal_yaws.append(yaw)
            self._cal_rolls.append(roll)
 
        b = _get_brow_furrow(landmarks)
        m = _get_mouth_frown(landmarks)
        e = _get_eye_squint(landmarks)
        if b is not None: self._cal_brow.append(b)
        if m is not None: self._cal_mouth.append(m)
        if e is not None: self._cal_eye.append(e)
        
    def finish_calibration(self) -> bool:
        """
        Compute baselines from collected samples.
        Returns True on success, False if insufficient data.
        """
        if not self._cal_pitches or not self._cal_brow:
            return False
 
        self._baseline_pitch = np.mean(self._cal_pitches)
        self._baseline_yaw = np.mean(self._cal_yaws)
        self._baseline_roll = np.mean(self._cal_rolls)
        self._baseline_brow = np.mean(self._cal_brow)
        self._baseline_mouth = np.mean(self._cal_mouth)
        self._baseline_eye = np.mean(self._cal_eye)
        self.calibrated = True
 
        print(f"[FaceAnalytics] Calibration done.")
        print(f"  Pose baseline  -> P:{self._baseline_pitch:.1f} Y:{self._baseline_yaw:.1f} R:{self._baseline_roll:.1f}")
        print(f"  Brow baseline  -> brow:{self._baseline_brow:.3f} mouth:{self._baseline_mouth:.3f} eye:{self._baseline_eye:.3f}")
        return True
    
    # Analytics
    def process(self, landmarks, img_w, img_h, pitch, yaw, roll, dt, elapsed) -> dict:
        """
        Process one frame. Call finish_calibration() before using this.
 
        Args:
            landmarks       : result.multi_face_landmarks[0].landmark
            img_w, img_h    : frame dimensions
            pitch, yaw, roll: raw angles from HeadPose.estimate()
            dt              : seconds since last frame
            elapsed         : seconds since analytics phase started
 
        Returns dict:
            emotion             : str  "NEUTRAL" | "CONFUSED" | "FRUSTRATED" | "N/A"
            direction           : str  "FORWARD" | "LEFT" | "RIGHT" | "UP" | "DOWN" | combinations
            avg_ear             : float
            blink_count         : int
            blink_rate          : float  blinks/min over last 60s
            brow_delta          : float | None
            mouth_delta         : float | None
            frust_accumulator   : float
            pose_accumulator    : float
            attention_score     : float  0-100
            frustration_score   : float  0-100
        """
        # ---- Blink ----
        left_ear = _calculate_ear(landmarks, _LEFT_EYE_EAR,  img_w, img_h)
        right_ear = _calculate_ear(landmarks, _RIGHT_EYE_EAR, img_w, img_h)
        avg_ear = round((left_ear + right_ear) / 2.0, 3)
 
        if avg_ear < _EAR_THRESHOLD:
            self._consec_frames += 1
            self._eye_closed = True
            self._min_ear_val = min(self._min_ear_val, avg_ear)
        else:
            if self._eye_closed and self._consec_frames >= _BLINK_CONSEC_FRAMES:
                self._blink_count += 1
                self._blink_timestamps.append(elapsed)
                print(f"  BLINK #{self._blink_count} at {elapsed:.1f}s (min EAR: {self._min_ear_val:.3f})")
            self._consec_frames = 0
            self._eye_closed = False
            self._min_ear_val = 1.0
 
        recent = [t for t in self._blink_timestamps if elapsed - t <= 60.0]
        blink_rate = round(len(recent) / min(elapsed, 60.0) * 60, 1) if elapsed > 0 else 0.0
 
        # ---- Head pose (apply baseline + smooth) ----
        direction = "N/A"
 
        if pitch is not None:
            self._pitch_buf.append(pitch - self._baseline_pitch)
            self._yaw_buf.append(yaw - self._baseline_yaw)
            self._roll_buf.append(roll - self._baseline_roll)
            if len(self._pitch_buf) > _POSE_SMOOTH_N: self._pitch_buf.pop(0)
            if len(self._yaw_buf) > _POSE_SMOOTH_N: self._yaw_buf.pop(0)
            if len(self._roll_buf) > _POSE_SMOOTH_N: self._roll_buf.pop(0)
 
            smoothed_pitch = round(np.mean(self._pitch_buf), 1)
            smoothed_yaw = round(np.mean(self._yaw_buf),   1)
            direction = _get_direction(smoothed_yaw, smoothed_pitch)
 
        # ---- Brow / emotion ----
        emotion = "N/A"
        frust_signal = 0.0
        brow_delta = None
        mouth_delta = None
 
        raw_brow = _get_brow_furrow(landmarks)
        raw_mouth = _get_mouth_frown(landmarks)
        raw_eye = _get_eye_squint(landmarks)
 
        if raw_brow is not None:
            self._brow_buf.append(raw_brow)
            self._mouth_buf.append(raw_mouth)
            self._eye_buf.append(raw_eye)
            if len(self._brow_buf) > _BROW_SMOOTH_N: self._brow_buf.pop(0)
            if len(self._mouth_buf) > _BROW_SMOOTH_N: self._mouth_buf.pop(0)
            if len(self._eye_buf) > _BROW_SMOOTH_N: self._eye_buf.pop(0)
 
            brow_delta  = round(np.mean(self._brow_buf) - self._baseline_brow,  3)
            mouth_delta = round(np.mean(self._mouth_buf) - self._baseline_mouth, 3)
            emotion, frust_signal = _classify_emotion(brow_delta, mouth_delta)
 
        # ---- Accumulators ----
        if emotion == "FRUSTRATED":
            self._frust_acc = min(self._frust_acc + _FRUST_FILL_RATE * dt, _FRUST_MAX)
        elif emotion == "CONFUSED":
            self._frust_acc = min(self._frust_acc + (_FRUST_FILL_RATE * 0.5) * dt, _FRUST_MAX)
        else:
            self._frust_acc = max(self._frust_acc - _FRUST_DRAIN_RATE * dt, 0.0)
 
        if direction not in ("FORWARD", "N/A"):
            self._pose_acc = min(self._pose_acc + _POSE_FILL_RATE * dt, _POSE_MAX)
        else:
            self._pose_acc = max(self._pose_acc - _POSE_DRAIN_RATE * dt, 0.0)
 
        # ---- Composite scores ----
        optimal = (_NORMAL_BLINK_MIN + _NORMAL_BLINK_MAX) / 2.0
        blink_score = max(0.0, 100.0 - abs(blink_rate - optimal) * 4.0) if elapsed > 30 else 100.0
        pose_score = max(0.0, 100.0 - self._pose_acc)
        attention_score = round(min(max(blink_score * 0.4 + pose_score * 0.6, 0.0), 100.0), 1)
 
        if blink_rate < _NORMAL_BLINK_MIN:
            blink_anomaly = min((_NORMAL_BLINK_MIN - blink_rate) * 5.0, 100.0)
        elif blink_rate > _NORMAL_BLINK_MAX * 2:
            blink_anomaly = min((blink_rate - _NORMAL_BLINK_MAX * 2) * 3.0, 100.0)
        else:
            blink_anomaly = 0.0
        frustration_score = round(
            min(max(self._frust_acc * 0.7 + blink_anomaly * 0.3, 0.0), 100.0), 1
        )
 
        return {
            "emotion": emotion,
            "direction": direction,
            "avg_ear": avg_ear,
            "blink_count": self._blink_count,
            "blink_rate": blink_rate,
            "brow_delta": brow_delta,
            "mouth_delta": mouth_delta,
            "frust_accumulator": round(self._frust_acc, 1),
            "pose_accumulator": round(self._pose_acc,  1),
            "attention_score": attention_score,
            "frustration_score": frustration_score,
        }
"""
face_sensor.py
Unified face pipeline — owns CameraStream, HeadPose, EyeAnalytics, FaceAnalytics.
 
Handles:
    - Camera warmup
    - FaceAnalytics passive baseline calibration (timed, no SPACE needed)
    - EyeAnalytics 5-point gaze calibration (SPACE-triggered)
    - Per-frame processing (single FaceMesh call shared across all modules)
 
Usage:
    face = FaceSensor(screen_w, screen_h)
    face.calibrate()        # blocks until both calibrations complete
    
    # main loop
    result = face.update()  # returns dict or None if no face detected
    face.stop()             # cleanup
    
Output dict keys:
    face_detected       : bool
    emotion             : str   "NEUTRAL" | "CONFUSED" | "FRUSTRATED" | "N/A"
    direction           : str   "FORWARD" | "LEFT" | "RIGHT" | "UP" | "DOWN" | combinations
    avg_ear             : float
    blink_count         : int
    blink_rate          : float blinks/min over last 60s
    brow_delta          : float | None
    mouth_delta         : float | None
    frust_accumulator   : float
    pose_accumulator    : float
    attention_score     : float 0-100
    frustration_score   : float 0-100
    gaze_quadrant       : str   "CENTER" | "TOP-LEFT" | "TOP-RIGHT" |
                                "BOTTOM-LEFT" | "BOTTOM-RIGHT" | "UNCALIBRATED"
"""

import time
import cv2
import mediapipe
import numpy as np
 
from face.HeadPose import HeadPose
from face.EyeAnalytics  import EyeAnalytics
from face.FaceAnalytics import FaceAnalytics
from utils.CameraStream import CameraStream


# CONFIGURABLE PARAMETERS
_WIDTH            = 640
_HEIGHT           = 480
_WARMUP_SECS      = 2.0
_FACE_CALIB_SECS  = 10.0   # passive face/brow baseline collection
_GAZE_COLLECT_SECS = 5.0   # per-point gaze collection in EyeAnalytics

_FONT = cv2.FONT_HERSHEY_SIMPLEX

class FaceSensor:
    def __init__(self, screen_w: int, screen_h: int, debug: bool = False):
        self._screen_w = screen_w
        self._screen_h = screen_h
        self._debug = debug
        
        # Camera Setup
        self._stream = CameraStream(0)
        if not self._stream.ret:
            raise RuntimeError("[FaceSensor] Camera not opened.")
        print(f"[FaceSensor] Camera ready: "
              f"{self._stream.get(cv2.CAP_PROP_FRAME_WIDTH)}x"
              f"{self._stream.get(cv2.CAP_PROP_FRAME_HEIGHT)}")
        
        # FaceMesh Setup (shared across all modules)
        self._face_mesh = mediapipe.solutions.face_mesh.FaceMesh(
                    max_num_faces=1,
                    refine_landmarks=True,
                    min_detection_confidence=0.6,
                    min_tracking_confidence=0.7,
                )
        
        # Analytics Modules
        self._head_pose: HeadPose | None = None # Initialized after face calibration
        self._eye_analytics = EyeAnalytics(screen_w, screen_h, collect_seconds=_GAZE_COLLECT_SECS)
        self._face_analytics = FaceAnalytics()
        
        # Analytics timing
        self._analytics_start  = 0.0
        self._last_frame_time  = 0.0
        
        
    # CALIBRATION
    def calibrate(self):
        """
        Blocks until both calibrations are complete:
        - FaceAnalytics: passive N-second baseline collection
        - EyeAnalytics: interactive 5-point SPACE-triggered gaze calibration
        Both run concurrently in the same loop.
        """
        
        # Camera warmup (discard initial frames for consistent performance)
        print(f"\n[FaceSensor] Warming up camera ({_WARMUP_SECS}s)...")
        warmup_start = time.time()
        while time.time() - warmup_start < _WARMUP_SECS:
            self._stream.read()
            
        # PHASE 1: Gaze calibration 
        print(f"[FaceSensor] Phase 1: Gaze calibration - follow the dots, press SPACE per point\n")
        while not self._eye_analytics.calibration_done:
            ret, frame = self._stream.read()
            if not ret:
                continue
 
            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
 
            results = self._face_mesh.process(rgb)
            frame = cv2.flip(frame, 1)
 
            # Init HeadPose on first frame
            if self._head_pose is None:
                self._head_pose = HeadPose(w, h)
 
            key = cv2.waitKey(1) & 0xFF
            space_pressed = key == ord(" ")
 
            if results.multi_face_landmarks:
                lm = results.multi_face_landmarks[0].landmark
                pitch, yaw, roll, _, _ = self._head_pose.estimate(lm)
                status = self._eye_analytics.update_calibration(
                    lm, w, h, yaw, pitch, space_pressed
                )
 
            # Only show dot screen during gaze calibration
            calib_canvas = self._eye_analytics.draw_calibration_screen()
            cv2.imshow("Gaze Calibration", calib_canvas)
 
            if key == ord("q"):
                print("[FaceSensor] Quit during gaze calibration.")
                self.stop()
                raise SystemExit
 
        cv2.destroyWindow("Gaze Calibration")
        print("[FaceSensor] Gaze calibration complete.\n")
        
        
        # PHASE 2: Face analytics calibration
        # Ensure HeadPose was initialised during Phase 1
        if self._head_pose is None:
            ret, frame = self._stream.read()
            h, w = frame.shape[:2]
            self._head_pose = HeadPose(w, h)
    
        print(f"[FaceSensor] Phase 2: Face calibration - relax face, look at camera ({_FACE_CALIB_SECS}s)\n")
        face_calib_done = False
        face_calib_start = time.time()

        while not face_calib_done:
            ret, frame = self._stream.read()
            if not ret:
                continue
 
            h, w = frame.shape[:2]
            rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
 
            results = self._face_mesh.process(rgb)
            frame = cv2.flip(frame, 1)
 
            key = cv2.waitKey(1) & 0xFF
 
            if results.multi_face_landmarks:
                lm = results.multi_face_landmarks[0].landmark
                pitch, yaw, roll, _, _ = self._head_pose.estimate(lm)
 
                self._face_analytics.add_calibration_sample(lm, pitch, yaw, roll)
                elapsed_cal = time.time() - face_calib_start
                remaining   = max(0.0, _FACE_CALIB_SECS - elapsed_cal)
 
                if elapsed_cal >= _FACE_CALIB_SECS:
                    ok = self._face_analytics.finish_calibration()
                    if ok:
                        face_calib_done = True
                    else:
                        print("[FaceSensor] WARNING: Face calibration failed - retrying...")
                        face_calib_start = time.time()
 
                cv2.rectangle(frame, (0, 0), (w, 42), (0, 140, 255), -1)
                cv2.putText(frame,
                    f"Relax face, look at camera ({remaining:.1f}s)",
                    (10, 28), _FONT, 0.6, (255, 255, 255), 2)
            else:
                cv2.putText(frame, "No face detected", (10, 28), _FONT, 0.7, (0, 0, 255), 2)
 
            cv2.imshow("UAT", frame)
 
            if key == ord("q"):
                print("[FaceSensor] Quit during face calibration.")
                self.stop()
                raise SystemExit
        self._analytics_start = time.time()
        self._last_frame_time = time.time()
        print("\n[FaceSensor] Calibration complete. Ready for analytics.\n")
            
    # Per-frame processing after calibration
    def update(self) -> dict | None:
        """
        Read one frame, run all analytics, return result dict.
        Returns None if no frame or no face detected.
        Call after calibrate().
        """
        ret, frame = self._stream.read()
        if not ret:
            return None

        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Single FaceMesh inference
        results = self._face_mesh.process(rgb)
        frame = cv2.flip(frame, 1)

        # Timing
        now = time.time()
        dt = now - self._last_frame_time
        elapsed = now - self._analytics_start
        self._last_frame_time = now

        if not results.multi_face_landmarks:
            return {"face_detected": False}

        lm = results.multi_face_landmarks[0].landmark
        
        if self._head_pose is None:
            return None

        # HeadPose — single call, results shared
        pitch, yaw, roll, _, _ = self._head_pose.estimate(lm)

        # EyeAnalytics
        gaze_quadrant = self._eye_analytics.process(lm, w, h, yaw, pitch)

        # FaceAnalytics
        face_result = self._face_analytics.process(lm, w, h, pitch, yaw, roll, dt, elapsed)
        
        if self._debug:
            cv2.putText(frame, f"Gaze: {gaze_quadrant}", (10, 28), _FONT, 0.6, (0, 255, 255), 2)
            cv2.putText(frame, f"Emotion: {face_result['emotion']}", (10, 55), _FONT, 0.6, (0, 255, 0), 2)
            cv2.putText(frame, f"Frust: {face_result['frustration_score']}", (10, 82), _FONT, 0.6, (0, 165, 255), 2)
            cv2.putText(frame, f"Attn: {face_result['attention_score']}", (10, 109), _FONT, 0.6, (255, 255, 0), 2)
            cv2.imshow("UAT", frame)
            cv2.waitKey(1)
        
        return {
            "face_detected": True,
            "gaze_quadrant": gaze_quadrant,
            **face_result,
        }
        
    # Cleanup
    def stop(self):
        """Release all resources."""
        self._stream.stop()
        self._face_mesh.close()
        cv2.destroyAllWindows()
        print("[FaceSensor] Stopped.")
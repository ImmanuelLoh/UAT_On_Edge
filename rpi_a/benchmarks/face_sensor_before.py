"""
face_sensor_before.py
PASO Benchmark: BEFORE optimisation.

Full production pipeline (HeadPose + EyeAnalytics + FaceAnalytics) with
CameraStreamBefore: blocking cap.read() - no background thread.
Camera I/O stalls the main loop every frame (~17-19ms at 30 FPS).

Run:
    python -m rpi_a.benchmarks.face_sensor_before

Measurement window: 5s warmup, then 30s of data collection (post-calibration).
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import time
import cv2
import mediapipe
import numpy as np
import psutil

from rpi_a.sensors.face.HeadPose import HeadPose
from rpi_a.sensors.face.EyeAnalytics import EyeAnalytics
from rpi_a.sensors.face.FaceAnalytics import FaceAnalytics
from rpi_a.sensors.utils.CameraStreamBefore import CameraStreamBefore


# ---------------------------------------------------------------------------
# CONFIGURABLE PARAMETERS
# ---------------------------------------------------------------------------
_WIDTH = 640
_HEIGHT = 480
_WARMUP_SECS = 2.0
_FACE_CALIB_SECS = 10.0
_GAZE_COLLECT_SECS = 5.0

_FONT = cv2.FONT_HERSHEY_SIMPLEX

WARMUP = 5     # benchmark warmup after calibration (s)
MEASURE = 30    # benchmark measurement window (s)
DEBUG = True   # show debug info and visualization during calibration and benchmarking


# ---------------------------------------------------------------------------
# FACE SENSOR (uses CameraStreamBefore - blocking reads)
# ---------------------------------------------------------------------------
class FaceSensor:
    def __init__(self, screen_w: int, screen_h: int, debug: bool = False):
        self._screen_w = screen_w
        self._screen_h = screen_h
        self._debug = debug

        self._stream = CameraStreamBefore(0, width=_WIDTH, height=_HEIGHT)
        if not self._stream.ret:
            raise RuntimeError("[FaceSensor] Camera not opened.")
        print(f"[FaceSensor] Camera ready: "
              f"{self._stream.get(cv2.CAP_PROP_FRAME_WIDTH)}x"
              f"{self._stream.get(cv2.CAP_PROP_FRAME_HEIGHT)}")

        self._face_mesh = mediapipe.solutions.face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.6,
            min_tracking_confidence=0.7,
        )

        self._head_pose: HeadPose | None = None
        self._eye_analytics = EyeAnalytics(screen_w, screen_h, collect_seconds=_GAZE_COLLECT_SECS)
        self._face_analytics = FaceAnalytics()

        self._analytics_start = 0.0
        self._last_frame_time = 0.0

    def calibrate(self):
        print(f"\n[FaceSensor] Warming up camera ({_WARMUP_SECS}s)...")
        warmup_start = time.time()
        while time.time() - warmup_start < _WARMUP_SECS:
            self._stream.read()

        cv2.namedWindow("UAT Calibration", cv2.WINDOW_NORMAL)
        cv2.setWindowProperty("UAT Calibration", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

        if self._debug:
            print(f"[FaceSensor] Phase 1: Gaze calibration - follow the dots, press SPACE per point\n")
            while not self._eye_analytics.calibration_done:
                ret, frame = self._stream.read()
                if not ret or frame is None:
                    continue

                h, w = frame.shape[:2]
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = self._face_mesh.process(rgb)
                frame = cv2.flip(frame, 1)

                if self._head_pose is None:
                    self._head_pose = HeadPose(w, h)

                key = cv2.waitKey(1) & 0xFF
                space_pressed = key == ord(" ")

                if results.multi_face_landmarks:
                    lm = results.multi_face_landmarks[0].landmark
                    pitch, yaw, roll, _, _ = self._head_pose.estimate(lm)
                    self._eye_analytics.update_calibration(lm, w, h, yaw, pitch, space_pressed)

                cv2.imshow("UAT Calibration", self._eye_analytics.draw_calibration_screen())
                if key == ord("q"):
                    self.stop()
                    raise SystemExit

            print("[FaceSensor] Gaze calibration complete.\n")

        # Phase 2: Face analytics calibration
        if self._head_pose is None:
            ret, frame = self._stream.read()
            assert ret and frame is not None
            h, w = frame.shape[:2]
            self._head_pose = HeadPose(w, h)

        print(f"[FaceSensor] Phase 2: Face calibration - relax face, look at camera ({_FACE_CALIB_SECS}s)\n")
        face_calib_done = False
        face_calib_start = time.time()
        remaining = _FACE_CALIB_SECS

        while not face_calib_done:
            ret, frame = self._stream.read()
            if not ret or frame is None:
                continue

            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self._face_mesh.process(rgb)
            frame = cv2.flip(frame, 1)
            key = cv2.waitKey(1) & 0xFF

            if results.multi_face_landmarks:
                lm = results.multi_face_landmarks[0].landmark
                pitch, yaw, roll, _, _ = self._head_pose.estimate(lm)
                self._face_analytics.add_calibration_sample(lm, pitch, yaw, roll)

                elapsed_cal = time.time() - face_calib_start
                remaining = max(0.0, _FACE_CALIB_SECS - elapsed_cal)

                if elapsed_cal >= _FACE_CALIB_SECS:
                    ok = self._face_analytics.finish_calibration()
                    if ok:
                        face_calib_done = True
                    else:
                        print("[FaceSensor] WARNING: Face calibration failed - retrying...")
                        face_calib_start = time.time()

                canvas = np.zeros((self._screen_h, self._screen_w, 3), dtype=np.uint8)
                text1 = "Calibrating..."
                text2 = f"Relax your face and look at the camera ({remaining:.1f}s)"
                (t1_w, _), _ = cv2.getTextSize(text1, _FONT, 1.5, 3)
                (t2_w, _), _ = cv2.getTextSize(text2, _FONT, 0.8, 2)
                cv2.putText(canvas, text1, (self._screen_w//2 - t1_w//2, self._screen_h//2 - 40), _FONT, 1.5, (255, 255, 255), 3)
                cv2.putText(canvas, text2, (self._screen_w//2 - t2_w//2, self._screen_h//2 + 20), _FONT, 0.8, (200, 200, 200), 2)
                cv2.imshow("UAT Calibration", canvas)

            if key == ord("q"):
                self.stop()
                raise SystemExit

        self._analytics_start = time.time()
        self._last_frame_time = time.time()
        cv2.destroyWindow("UAT Calibration")
        print("\n[FaceSensor] Calibration complete. Ready for analytics.\n")

    def stop(self):
        self._stream.stop()
        self._face_mesh.close()
        cv2.destroyAllWindows()
        print("[FaceSensor] Stopped.")


# ---------------------------------------------------------------------------
# BENCHMARK ENTRYPOINT
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    screen_w, screen_h = 1920, 1080

    face_sensor = FaceSensor(screen_w, screen_h, debug=DEBUG)
    face_sensor.calibrate()

    # ---------------------------------------------------------------------------
    # METRIC LISTS
    # ---------------------------------------------------------------------------
    fps_list = []
    lat_total_list = []
    lat_capture_list = []
    lat_facemesh_list = []
    lat_headpose_list = []
    lat_eye_list = []
    lat_face_list = []
    cpu_list = []
    ram_list = []

    t_start = time.perf_counter()
    t_measure_start = None
    t_prev = time.perf_counter()

    print(f"[Benchmark] Warming up ({WARMUP}s)...")

    # ---------------------------------------------------------------------------
    # BENCHMARK LOOP
    # ---------------------------------------------------------------------------
    stream = face_sensor._stream
    face_mesh = face_sensor._face_mesh
    head_pose = face_sensor._head_pose
    assert head_pose is not None, "calibrate() must be called before benchmarking"
    eye_analytics = face_sensor._eye_analytics
    face_analytics = face_sensor._face_analytics

    while True:
        t_frame_start = time.perf_counter()

        # --- 1. Camera read (BLOCKING — stalls here until new frame ready) ---
        t0 = time.perf_counter()
        ret, frame = stream.read()
        t1 = time.perf_counter()
        capture_ms = (t1 - t0) * 1000

        if not ret or frame is None:
            continue

        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # --- 2. FaceMesh inference ---
        t0 = time.perf_counter()
        results = face_mesh.process(rgb)
        t1 = time.perf_counter()
        facemesh_ms = (t1 - t0) * 1000

        frame = cv2.flip(frame, 1)

        headpose_ms = eye_ms = face_ms = 0.0
        now = time.perf_counter()

        if results.multi_face_landmarks:
            lm = results.multi_face_landmarks[0].landmark

            dt = now - face_sensor._last_frame_time
            elapsed = now - face_sensor._analytics_start

            # --- 3. HeadPose ---
            t0 = time.perf_counter()
            pitch, yaw, roll, _, _ = head_pose.estimate(lm)
            t1 = time.perf_counter()
            headpose_ms = (t1 - t0) * 1000

            # --- 4. EyeAnalytics ---
            t0 = time.perf_counter()
            gaze_quadrant = eye_analytics.process(lm, w, h, yaw, pitch)
            t1 = time.perf_counter()
            eye_ms = (t1 - t0) * 1000

            # --- 5. FaceAnalytics ---
            t0 = time.perf_counter()
            face_result = face_analytics.process(lm, w, h, pitch, yaw, roll, dt, elapsed)
            t1 = time.perf_counter()
            face_ms = (t1 - t0) * 1000

            face_sensor._last_frame_time = time.perf_counter()

            if DEBUG:
                cv2.putText(frame, f"Gaze: {gaze_quadrant}", (10, 28), _FONT, 0.6, (0, 255, 255), 2)
                cv2.putText(frame, f"Emotion: {face_result['emotion']}", (10, 55), _FONT, 0.6, (0, 255, 0), 2)
                cv2.putText(frame, f"[BEFORE] blocking cap.read()", (10, 82), _FONT, 0.5, (0, 100, 255), 2)

        # --- Total ---
        t_frame_end  = time.perf_counter()
        total_ms = (t_frame_end - t_frame_start) * 1000
        fps = 1.0 / max(t_frame_end - t_prev, 1e-9)
        t_prev = t_frame_end
        bench_elapsed = t_frame_end - t_start

        if bench_elapsed >= WARMUP:
            if t_measure_start is None:
                t_measure_start = t_frame_end
                print(f"[Benchmark] Measuring for {MEASURE}s...")

            fps_list.append(fps)
            lat_total_list.append(total_ms)
            lat_capture_list.append(capture_ms)
            lat_facemesh_list.append(facemesh_ms)
            lat_headpose_list.append(headpose_ms)
            lat_eye_list.append(eye_ms)
            lat_face_list.append(face_ms)
            cpu_list.append(psutil.cpu_percent(interval=None))
            ram_list.append(psutil.virtual_memory().percent)

            if (t_frame_end - t_measure_start) >= MEASURE:
                break

        if DEBUG:
            cv2.imshow("Benchmark: BEFORE (blocking cap.read)", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    face_sensor.stop()

    # ---------------------------------------------------------------------------
    # BENCHMARK SUMMARY
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 55)
    print("  BENCHMARK SUMMARY — BEFORE (blocking cap.read)")
    print("  Full pipeline: HeadPose + EyeAnalytics + FaceAnalytics")
    print("=" * 55)
    print(f"Resolution : {_WIDTH}x{_HEIGHT} @ 30 FPS")
    print(f"Camera     : CameraStreamBefore (no thread, blocking read)")
    print(f"Frames     : {len(fps_list)}")

    if fps_list:
        print("\n--- System Performance ---")
        print(f"Avg FPS : {np.mean(fps_list):.2f}  |  Min: {np.min(fps_list):.2f}  |  Max: {np.max(fps_list):.2f}")
        print(f"Avg CPU : {np.mean(cpu_list):.1f}%")
        print(f"Avg RAM : {np.mean(ram_list):.1f}%")

        print("\n--- Per-Module Latency (ms) ---")
        header = f"{'Module':<26} {'Avg':>7} {'P50':>7} {'P95':>7} {'Max':>7}"
        print(header)
        print("-" * len(header))

        modules = [
            ("Total (pipeline)", lat_total_list),
            ("  Camera capture", lat_capture_list),
            ("  FaceMesh infer", lat_facemesh_list),
            ("  HeadPose", lat_headpose_list),
            ("  EyeAnalytics", lat_eye_list),
            ("  FaceAnalytics", lat_face_list),
        ]
        for name, data in modules:
            arr = np.array(data)
            if len(arr) == 0:
                print(f"{name:<26} {'N/A':>7}")
                continue
            print(
                f"{name:<26} {np.mean(arr):>7.2f} {np.median(arr):>7.2f} "
                f"{np.percentile(arr, 95):>7.2f} {np.max(arr):>7.2f}"
            )

        print("\nNOTE: Camera capture dominates total latency.")
        print("      Each read() blocks the pipeline until the camera delivers a new frame.")
        print("      Run face_sensor_after.py (AFTER) to compare with CameraStream.")
    else:
        print("No frames captured during measurement window.")
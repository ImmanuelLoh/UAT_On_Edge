import time, psutil, subprocess, os
import cv2
import mediapipe
from math import sqrt
import numpy as np

from rpi_a.sensors.face.CameraStream import CameraStream  # Force-release any lingering handle on the device
from rpi_a.sensors.face.GazeCalibrator import GazeCalibrator

# Pin entire process to core 0 only
proc = psutil.Process(os.getpid())
print(f"Default cores: {proc.cpu_affinity()}")
print(f"Currently on core: {proc.cpu_num()}")

proc.cpu_affinity([0])
print(f"Pinned to core: {proc.cpu_affinity()}")

core_usage = psutil.cpu_percent(percpu=True)
print(f"All cores: {core_usage}")

# ---------------------------------------------
# CAM SETUP
# ---------------------------------------------
# WARMUP = 5
# MEASURE = 30
WIDTH = 640
HEIGHT = 480

stream = CameraStream(0)

# Force MJPEG (important)
if not stream.ret:
    print("ERROR: Camera not opened")
    exit()

# Verify settings were applied
actual_w = stream.get(cv2.CAP_PROP_FRAME_WIDTH)
actual_h = stream.get(cv2.CAP_PROP_FRAME_HEIGHT)
actual_fps = stream.get(cv2.CAP_PROP_FPS)
print(f"Camera configured: {actual_w}x{actual_h} @ {actual_fps} FPS")

# Get screen size for gaze calibration
def get_screen_resolution():
    try:
        out = (
            subprocess.check_output("xrandr | grep '*' | awk '{print $1}'", shell=True)
            .decode()
            .strip()
            .split("\n")[0]
        )
        w, h = map(int, out.split("x"))
        return w, h
    except Exception:
        return 1920, 1080


screen_w, screen_h = get_screen_resolution()
print(f"Detected screen: {screen_w}x{screen_h}")
calibrator = GazeCalibrator(screen_w, screen_h, collect_seconds=2.0)

# ---------------------------------------------
# CONFIG
# ---------------------------------------------

# MediaPipe Face Mesh landmark indices
LEFT_EYE_INDICES = [
    362,
    382,
    381,
    380,
    374,
    373,
    390,
    249,
    263,
    466,
    388,
    387,
    386,
    385,
    384,
    398,
]
RIGHT_EYE_INDICES = [
    33,
    7,
    163,
    144,
    145,
    153,
    154,
    155,
    133,
    173,
    157,
    158,
    159,
    160,
    161,
    246,
]

# Iris landmarks (MediaPipe Face Mesh with refine_landmarks=True)
LEFT_IRIS_INDICES = [468, 469, 470, 471, 472]
RIGHT_IRIS_INDICES = [473, 474, 475, 476, 477]

# EAR landmarks
# p1=inner corner, p2=upper-inner, p3=upper-outer, p4=outer corner, p5=lower-outer, p6=lower-inner
LEFT_EYE_EAR = [362, 385, 387, 263, 373, 380]
RIGHT_EYE_EAR = [33, 160, 158, 133, 153, 144]

# Head pose: 6-point 3D model (nose, chin, left/right eye corners, left/right mouth corners)
FACE_3D_MODEL = np.array(
    [
        [0.0, 0.0, 0.0],  # Nose tip                    - 1
        [0.0, -330.0, -65.0],  # Chin                   - 152
        [-225.0, 170.0, -135.0],  # Left eye corner     - 33
        [225.0, 170.0, -135.0],  # Right eye corner     - 263
        [-150.0, -150.0, -125.0],  # Left mouth corner  - 61
        [150.0, -150.0, -125.0],  # Right mouth corner  - 291
    ],
    dtype=np.float64,
)

FACE_LANDMARK_IDS = [1, 152, 33, 263, 61, 291]

BLINK_THRESHOLD = 0.21  # EAR below this = blink
BLINK_CONSEC_FRAMES = 2

FONT = cv2.FONT_HERSHEY_SIMPLEX


# ---------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------
# Euclidean distance to calculate the distance between the two points
def euclideanDistance(p1, p2):
    x, y = p1
    x1, y1 = p2
    distance = sqrt((x1 - x) ** 2 + (y1 - y) ** 2)
    return distance


# ---------------------------------------------
# MODULES
# ---------------------------------------------
def eye_aspect_ratio(landmarks, ear_indices, img_w, img_h):
    """
    Calculates Eye Aspect Ratio (EAR) for blink detection.
    EAR = (||p2-p6|| + ||p3-p5||) / (2 * ||p1-p4||)
    """
    pts = np.array(
        [[landmarks[i].x * img_w, landmarks[i].y * img_h] for i in ear_indices],
        dtype=np.float64,
    )
    A = np.linalg.norm(pts[1] - pts[5])  # upper-inner to lower-inner
    B = np.linalg.norm(pts[2] - pts[4])  # upper-outer to lower-outer
    C = np.linalg.norm(pts[0] - pts[3])  # inner corner to outer corner
    return (A + B) / (2.0 * C) if C > 0 else 0.0


def detect_blink(landmarks, img_w, img_h, blink_counter, blink_total):
    """
    Detects blink using EAR on both eyes.
    Returns (left_ear, right_ear, avg_ear, is_blinking, updated_counter, updated_total).
    """
    left_ear = eye_aspect_ratio(landmarks, LEFT_EYE_EAR, img_w, img_h)
    right_ear = eye_aspect_ratio(landmarks, RIGHT_EYE_EAR, img_w, img_h)
    avg_ear = (left_ear + right_ear) / 2.0

    if avg_ear < BLINK_THRESHOLD:
        blink_counter += 1
    else:
        if blink_counter >= BLINK_CONSEC_FRAMES:
            blink_total += 1
        blink_counter = 0

    is_blinking = avg_ear < BLINK_THRESHOLD
    return left_ear, right_ear, avg_ear, is_blinking, blink_counter, blink_total


def estimate_gaze(landmarks, img_w, img_h):
    """
    Estimates gaze direction from iris center relative to eye corner midpoint.
    Returns (left_gaze_x, left_gaze_y, right_gaze_x, right_gaze_y) � normalised offsets.
    """

    def iris_offset(iris_ids, eye_ids):
        iris_center = np.mean(
            [[landmarks[i].x * img_w, landmarks[i].y * img_h] for i in iris_ids], axis=0
        )
        # eye_ids[0] = inner_corner, eye_ids[8] = outer corner
        eye_inner = np.array(
            [landmarks[eye_ids[0]].x * img_w, landmarks[eye_ids[0]].y * img_h]
        )
        eye_outer = np.array(
            [landmarks[eye_ids[8]].x * img_w, landmarks[eye_ids[8]].y * img_h]
        )
        eye_width = np.linalg.norm(eye_outer - eye_inner) + 1e-6
        eye_mid = (eye_inner + eye_outer) / 2
        offset = (iris_center - eye_mid) / eye_width
        return offset[0], offset[1]

    lx, ly = iris_offset(LEFT_IRIS_INDICES, LEFT_EYE_INDICES)
    rx, ry = iris_offset(RIGHT_IRIS_INDICES, RIGHT_EYE_INDICES)
    return lx, ly, rx, ry


def estimate_head_pose(landmarks, img_w, img_h, cam_matrix, dist_coeffs):
    """
    Estimates head pose using solvePnP.
    Returns (success, pitch, yaw, roll) in degrees.
    """
    face_2d = np.array(
        [[landmarks[i].x * img_w, landmarks[i].y * img_h] for i in FACE_LANDMARK_IDS],
        dtype=np.float64,
    )

    success, rot_vec, _ = cv2.solvePnP(
        FACE_3D_MODEL, face_2d, cam_matrix, dist_coeffs, flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not success:
        return False, 0.0, 0.0, 0.0

    rmat, _ = cv2.Rodrigues(rot_vec)
    angles, _, _, _, _, _ = cv2.RQDecomp3x3(rmat)
    pitch = angles[0] * 360
    yaw = angles[1] * 360
    roll = angles[2] * 360
    return True, pitch, yaw, roll


# ---------------------------------------------
# MEDIAPIPE FACEMESH
# ---------------------------------------------
mediapipe_face_mesh = mediapipe.solutions.face_mesh
face_mesh = mediapipe_face_mesh.FaceMesh(
    max_num_faces=1,
    refine_landmarks=True,  # Iris Landmarks
    min_detection_confidence=0.6,
    min_tracking_confidence=0.7,
)

# ---------------------------------------------
# METRIC LIST
# ---------------------------------------------
fps_list = []
lat_total_list = []  # full pipeline latency per frame
lat_capture_list = []  # camera read only
lat_facemesh_list = []  # MediaPipe FaceMesh only
lat_blink_list = []  # blink ratio calculation only
lat_gaze_list = []  # gaze direction calculation only
lat_pose_list = []
cpu_list = []
ram_list = []
gaze_quadrant = "UNCALIBRATED"

BLINK_COUNTER = 0
TOTAL_BLINKS = 0
FONT = cv2.FONT_HERSHEY_SIMPLEX

t_start = time.perf_counter()
t_measure_start = None
t_prev = time.perf_counter()

# ---------------------------------------------
# MAIN LOOP
# ---------------------------------------------
while True:
    t_frame_start = time.perf_counter()

    # --- 1. Camera Capture ---
    t0 = time.perf_counter()
    ret, frame = stream.read()
    if not ret:
        break
    t1 = time.perf_counter()
    capture_ms = (t1 - t0) * 1000
    
    h, w = frame.shape[:2]
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    # Camera matrix: estimated from frame size (no calibration file needed)
    # Focal length approximated as image width; principal point at image center
    cam_matrix = np.array([[w, 0, w / 2], [0, w, h / 2], [0, 0, 1]], dtype=np.float64)
    dist_coeffs = np.zeros((4, 1), dtype=np.float64)

    # --- 2. FaceMesh Inference ---
    t0 = time.perf_counter()
    results = face_mesh.process(rgb)
    t1 = time.perf_counter()
    facemesh_ms = (t1 - t0) * 1000
    
    frame = cv2.flip(frame, 1) # Mirror for more natural interaction

    #  Only when face detected ---
    blink_ms = 0.0
    gaze_ms = 0.0
    pose_ms = 0.0

    avg_ear = 0.0
    pitch = yaw = roll = 0.0
    lx = ly = rx = ry = 0.0
    pose_ok = False

    if results.multi_face_landmarks:
        lm = results.multi_face_landmarks[0].landmark  # 1 FACE ONLY

        # -- 3. Blink Detection --
        t0 = time.perf_counter()
        left_ear, right_ear, avg_ear, is_blinking, BLINK_COUNTER, TOTAL_BLINKS = (
            detect_blink(lm, w, h, BLINK_COUNTER, TOTAL_BLINKS)
        )
        t1 = time.perf_counter()
        blink_ms = (t1 - t0) * 1000

        # --- 4. Gaze Estimation
        t0 = time.perf_counter()
        lx, ly, rx, ry = estimate_gaze(lm, w, h)
        t1 = time.perf_counter()
        gaze_ms = (t1 - t0) * 1000

        # --- 5. Head Pose Estimation
        t0 = time.perf_counter()
        pose_ok, pitch, yaw, roll = estimate_head_pose(
            lm, w, h, cam_matrix, dist_coeffs
        )
        t1 = time.perf_counter()
        pose_ms = (t1 - t0) * 1000
        
        # Calibration or classification
        if not calibrator.done:
            avg_gx = (lx + rx) / 2.0
            avg_gy = (ly + ry) / 2.0
            triggered = cv2.waitKey(1) & 0xFF == ord(" ")
            status = calibrator.update(avg_gx, avg_gy, triggered, yaw, pitch)

            # Show fullscreen calibration window
            calib_canvas = calibrator.draw_calibration_screen(screen_w, screen_h)
            cv2.imshow("Calibration", calib_canvas)

            # Still show webcam feed so operator can see face is detected
            cv2.putText(frame, "CALIBRATING...", (10, 30), FONT, 0.8, (0, 0, 255), 2)
            cv2.putText(frame, status, (10, 60), FONT, 0.5, (255, 255, 0), 1)
            cv2.imshow("Benchmark: Combined", frame)

        else:
            # Hide calibration window once done
            cv2.destroyWindow("Calibration")

            gaze_quadrant, gx, gy, px, py = calibrator.classify(
                lx, ly, rx, ry, yaw, pitch
            )
            cv2.putText(
                frame, f"Gaze: {gaze_quadrant}", (10, 80), FONT, 0.55, (0, 255, 255), 2
            )
            prev_x = int(px * w / screen_w)
            prev_y = int(py * h / screen_h)
            cv2.circle(frame, (prev_x, prev_y), 6, (255, 0, 255), -1)
            cv2.imshow("Benchmark: Combined", frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        # Overlay on frame
        status = "BLINK" if is_blinking else "OPEN"
        cv2.putText(
            frame,
            f"EAR: {avg_ear:.2f}  [{status}]",
            (10, 30),
            FONT,
            0.6,
            (0, 255, 0),
            2,
        )
        cv2.putText(
            frame, f"Blinks: {TOTAL_BLINKS}", (10, 55), FONT, 0.6, (0, 255, 0), 2
        )
        
        if pose_ok:
            cv2.putText(
                frame,
                f"P:{pitch:.1f} Y:{yaw:.1f} R:{roll:.1f}",
                (10, 125),
                FONT,
                0.55,
                (255, 200, 0),
                2,
            )

    # -------- Total pipeline latency & FPS
    t_frame_end = time.perf_counter()
    total_ms = (t_frame_end - t_frame_start) * 1000
    fps = 1.0 / (t_frame_end - t_prev)
    t_prev = t_frame_end
    elapsed = t_frame_end - t_start

    # If no face detected, still need to show frame + handle quit
    if not results.multi_face_landmarks:
        cv2.imshow("Benchmark: Combined", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

backend = stream.getBackendName()
stream.stop()
cv2.destroyAllWindows()
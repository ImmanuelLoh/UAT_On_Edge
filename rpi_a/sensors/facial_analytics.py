import cv2
import mediapipe as mp
import numpy as np
import time
import json

# CONFIG
RESOLUTION       = (640, 480)
CALIBRATION_SECS = 10
PUBLISH_INTERVAL = 1.0  # seconds between data outputs

# Blink Detection
EAR_THRESHOLD       = 0.20
BLINK_CONSEC_FRAMES = 2
LEFT_EYE  = [362, 385, 387, 263, 373, 380]
RIGHT_EYE = [33,  160, 158, 133, 153, 144]

# Head Pose
YAW_THRESHOLD   = 20
PITCH_THRESHOLD = 10
ROLL_THRESHOLD  = 20
POSE_SMOOTH_N   = 10

FACE_3D_POINTS = np.array([
    [0.0,    0.0,    0.0   ],
    [0.0,   -63.6,  -12.5  ],
    [-43.3,  32.7,  -26.0  ],
    [43.3,   32.7,  -26.0  ],
    [-28.9, -28.9,  -24.1  ],
    [28.9,  -28.9,  -24.1  ],
], dtype=np.float64)
FACE_2D_INDICES = [1, 152, 263, 33, 291, 61]

# Brow Furrow & Emotion
BROW_SMOOTH_N        = 15
CONFUSED_THRESHOLD   = 0.15
FRUSTRATED_THRESHOLD = 0.35

# Blinks/min rate considered normal
NORMAL_BLINK_MIN = 12.0  
NORMAL_BLINK_MAX = 20.0

# Accumulator variables
FRUST_FILL_RATE  = 8.0    
FRUST_DRAIN_RATE = 3.0    
FRUST_MAX        = 100.0  

POSE_FILL_RATE   = 10.0   
POSE_DRAIN_RATE  = 4.0    
POSE_MAX         = 100.0  

# Blink detection
def calculate_ear(landmarks, eye_indices, img_w, img_h):
    pts = []
    for idx in eye_indices:
        lm = landmarks[idx]
        pts.append((lm.x * img_w, lm.y * img_h))
    v1 = np.linalg.norm(np.array(pts[1]) - np.array(pts[5]))
    v2 = np.linalg.norm(np.array(pts[2]) - np.array(pts[4]))
    h  = np.linalg.norm(np.array(pts[0]) - np.array(pts[3]))
    return round((v1 + v2) / (2.0 * h), 3) if h > 0 else 0.0

# Head pose
def get_camera_matrix(img_w, img_h):
    f = img_w
    return np.array([
        [f, 0, img_w / 2],
        [0, f, img_h / 2],
        [0, 0, 1         ]
    ], dtype=np.float64)

def get_head_pose(landmarks, img_w, img_h):
    face_2d = np.array([
        [landmarks[idx].x * img_w, landmarks[idx].y * img_h]
        for idx in FACE_2D_INDICES
    ], dtype=np.float64)

    cam_matrix  = get_camera_matrix(img_w, img_h)
    dist_coeffs = np.zeros((4, 1), dtype=np.float64)

    success, rot_vec, trans_vec = cv2.solvePnP(
        FACE_3D_POINTS, face_2d, cam_matrix, dist_coeffs,
        flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not success:
        return None, None, None, None, None

    rot_mat, _ = cv2.Rodrigues(rot_vec)
    pitch = np.degrees(np.arcsin(-rot_mat[2, 0]))
    yaw   = np.degrees(np.arctan2(rot_mat[2, 1], rot_mat[2, 2]))
    roll  = np.degrees(np.arctan2(rot_mat[1, 0], rot_mat[0, 0]))
    return round(pitch, 1), round(yaw, 1), round(roll, 1), rot_vec, trans_vec

def get_direction(yaw, pitch):
    directions = []
    if abs(pitch) > YAW_THRESHOLD:   directions.append("LEFT" if pitch < 0 else "RIGHT")
    if abs(yaw)   > PITCH_THRESHOLD: directions.append("DOWN" if yaw > 0 else "UP")
    return " + ".join(directions) if directions else "FORWARD"

# Brow furrow / emotion
def get_brow_furrow_score(landmarks):
    try:
        brow_y = (landmarks[4].y + landmarks[9].y) / 2
        nose_y = landmarks[6].y
        return round(abs(brow_y - nose_y) * 100, 3)
    except Exception:
        return None

def get_mouth_frown_score(landmarks):
    try:
        mouth_corner_y = (landmarks[61].y + landmarks[291].y) / 2
        chin_y         = landmarks[152].y
        return round(abs(mouth_corner_y - chin_y) * 100, 3)
    except Exception:
        return None

def get_eye_squint_score(landmarks):
    try:
        left_eye_v = abs(landmarks[159].y - landmarks[145].y)
        left_eye_h = abs(landmarks[33].x  - landmarks[133].x)
        return round((left_eye_v / max(left_eye_h, 0.001)) * 100, 3)
    except Exception:
        return None

def classify_emotion(brow_delta, mouth_delta):
    frustration_signal = (
        brow_delta * 0.7 +
        max(-mouth_delta, 0) * 0.3
    )
    if frustration_signal >= FRUSTRATED_THRESHOLD:
        return "FRUSTRATED", frustration_signal
    elif frustration_signal >= CONFUSED_THRESHOLD:
        return "CONFUSED", frustration_signal
    else:
        return "NEUTRAL", frustration_signal

# Scoring
def compute_attention_score(blink_rate, pose_accumulator, elapsed):
    # Blink rate component (40%)
    optimal = (NORMAL_BLINK_MIN + NORMAL_BLINK_MAX) / 2.0
    blink_score = max(0.0, 100.0 - abs(blink_rate - optimal) * 4.0) if elapsed > 30 else 100.0

    # Head pose component (60%) 
    pose_score = max(0.0, 100.0 - pose_accumulator)

    attention = (blink_score * 0.4) + (pose_score * 0.6)
    return round(min(max(attention, 0.0), 100.0), 1)

def compute_frustration_score(frust_accumulator, blink_rate):
    # Emotion accumulator component (70%)
    emotion_score = frust_accumulator

    # Blink anomaly component (30%)
    if blink_rate < NORMAL_BLINK_MIN:
        blink_anomaly = min((NORMAL_BLINK_MIN - blink_rate) * 5.0, 100.0)
    elif blink_rate > NORMAL_BLINK_MAX * 2:
        blink_anomaly = min((blink_rate - NORMAL_BLINK_MAX * 2) * 3.0, 100.0)
    else:
        blink_anomaly = 0.0

    frustration = (emotion_score * 0.7) + (blink_anomaly * 0.3)
    return round(min(max(frustration, 0.0), 100.0), 1)

# Main
def run():
    mp_face_mesh = mp.solutions.face_mesh
    face_mesh = mp_face_mesh.FaceMesh(
        static_image_mode=False,
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5
    )

    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  RESOLUTION[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, RESOLUTION[1])

    if not cap.isOpened():
        print("ERROR: Cannot open webcam.")
        return

    print("[UAT Analytics] Warming up (2s)...")
    warmup_start = time.time()
    while (time.time() - warmup_start) < 2.0:
        cap.read()

    # Calibration
    print(f"\n[Calibration] Relax your face and look directly at the camera.")
    print(f"  Calibrating for {CALIBRATION_SECS}s...\n")

    cal_pitches, cal_yaws, cal_rolls = [], [], []
    cal_brow, cal_mouth, cal_eye     = [], [], []
    cal_start = time.time()
    

    while (time.time() - cal_start) < CALIBRATION_SECS:
        ret, frame = cap.read()
        if not ret:
            continue

        h, w   = frame.shape[:2]
        rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = face_mesh.process(rgb)

        if result.multi_face_landmarks:
            lm = result.multi_face_landmarks[0].landmark

            # Head pose calibration
            p, y, r, _, _ = get_head_pose(lm, w, h)
            if p is not None:
                cal_pitches.append(p)
                cal_yaws.append(y)
                cal_rolls.append(r)

            # Brow furrow calibration
            b = get_brow_furrow_score(lm)
            m = get_mouth_frown_score(lm)
            e = get_eye_squint_score(lm)
            if b is not None: cal_brow.append(b)
            if m is not None: cal_mouth.append(m)
            if e is not None: cal_eye.append(e)

        remaining = CALIBRATION_SECS - (time.time() - cal_start)
        cv2.rectangle(frame, (0, 0), (w, 50), (0, 140, 255), -1)
        cv2.putText(frame, f"CALIBRATING -- relax face, look at camera ({remaining:.1f}s)",
                    (10, 33), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.imshow("UAT Analytics -- Press Q to quit", frame)
        cv2.waitKey(1)

    if not cal_pitches or not cal_brow:
        print("[Calibration] Failed -- no face detected. Exiting.")
        cap.release()
        face_mesh.close()
        return

    baseline_pitch = np.mean(cal_pitches)
    baseline_yaw   = np.mean(cal_yaws)
    baseline_roll  = np.mean(cal_rolls)
    baseline_brow  = np.mean(cal_brow)
    baseline_mouth = np.mean(cal_mouth)
    baseline_eye   = np.mean(cal_eye)

    print(f"[Calibration] Done.")
    print(f"  Head pose baseline -> Pitch: {baseline_pitch:.1f}  Yaw: {baseline_yaw:.1f}  Roll: {baseline_roll:.1f}")
    print(f"  Brow baseline      -> Brow: {baseline_brow:.3f}  Mouth: {baseline_mouth:.3f}  Eye: {baseline_eye:.3f}\n")

    # Main Loop
    print("[UAT Analytics] Running. Press Q to quit.\n")

    start_time = time.time()

    # Blink state
    blink_count      = 0
    consec_frames    = 0
    eye_closed       = False
    min_ear_val      = 1.0
    blink_timestamps = []

    # Head pose smoothing
    pitch_buf, yaw_buf, roll_buf = [], [], []

    # Brow furrow smoothing
    brow_buf, mouth_buf, eye_buf = [], [], []

    # Accumulators
    frust_accumulator = 0.0
    pose_accumulator  = 0.0
    last_frame_time   = time.time()
    fps = 0.0
    fps_buf = []
    last_publish_time = time.time()

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        h, w   = frame.shape[:2]
        rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = face_mesh.process(rgb)   

        elapsed       = time.time() - start_time
        dt            = time.time() - last_frame_time   # time since last frame
        if dt > 0:
            fps_buf.append(1.0 / dt)
            if len(fps_buf) > 30: fps_buf.pop(0)
            fps = round(np.mean(fps_buf), 1)
        last_frame_time = time.time()
        face_detected = result.multi_face_landmarks is not None

        # defaults
        left_ear = right_ear = avg_ear = None
        pitch = yaw = roll = None
        direction    = "N/A"
        brow_score   = mouth_score = eye_score = None
        brow_delta   = mouth_delta = eye_delta = None
        emotion      = "N/A"
        frust_signal = 0.0
        attention_score   = 0.0
        frustration_score = 0.0

        if face_detected:
            lm = result.multi_face_landmarks[0].landmark

            # Blink Detection
            left_ear  = calculate_ear(lm, LEFT_EYE,  w, h)
            right_ear = calculate_ear(lm, RIGHT_EYE, w, h)
            avg_ear   = round((left_ear + right_ear) / 2.0, 3)

            if avg_ear < EAR_THRESHOLD:
                consec_frames += 1
                eye_closed     = True
                min_ear_val    = min(min_ear_val, avg_ear)
            else:
                if eye_closed and consec_frames >= BLINK_CONSEC_FRAMES:
                    blink_count += 1
                    blink_timestamps.append(elapsed)
                    print(f"  BLINK #{blink_count} at {elapsed:.1f}s  (min EAR: {min_ear_val:.3f})")
                consec_frames = 0
                eye_closed    = False
                min_ear_val   = 1.0

            # Head Pose
            raw_pitch, raw_yaw, raw_roll, rot_vec, trans_vec = get_head_pose(lm, w, h)
            if raw_pitch is not None:
                raw_pitch -= baseline_pitch
                raw_yaw   -= baseline_yaw
                raw_roll  -= baseline_roll

                pitch_buf.append(raw_pitch)
                yaw_buf.append(raw_yaw)
                roll_buf.append(raw_roll)
                if len(pitch_buf) > POSE_SMOOTH_N: pitch_buf.pop(0)
                if len(yaw_buf)   > POSE_SMOOTH_N: yaw_buf.pop(0)
                if len(roll_buf)  > POSE_SMOOTH_N: roll_buf.pop(0)

                pitch = round(np.mean(pitch_buf), 1)
                yaw   = round(np.mean(yaw_buf),   1)
                roll  = round(np.mean(roll_buf),  1)
                direction = get_direction(yaw, pitch)

                # Nose arrow
                nose    = lm[1]
                nose_2d = (int(nose.x * w), int(nose.y * h))
                cam_matrix  = get_camera_matrix(w, h)
                dist_coeffs = np.zeros((4, 1), dtype=np.float64)
                proj_pts, _ = cv2.projectPoints(
                    np.array([[0.0, 0.0, -100.0]]),
                    rot_vec, trans_vec, cam_matrix, dist_coeffs
                )
                proj_end = (int(proj_pts[0][0][0]), int(proj_pts[0][0][1]))
                cv2.arrowedLine(frame, nose_2d, proj_end, (0, 255, 0), 3)

            # Brow Furrow / Emotion
            raw_brow  = get_brow_furrow_score(lm)
            raw_mouth = get_mouth_frown_score(lm)
            raw_eye   = get_eye_squint_score(lm)

            if raw_brow is not None:
                brow_buf.append(raw_brow)
                mouth_buf.append(raw_mouth)
                eye_buf.append(raw_eye)
                if len(brow_buf)  > BROW_SMOOTH_N: brow_buf.pop(0)
                if len(mouth_buf) > BROW_SMOOTH_N: mouth_buf.pop(0)
                if len(eye_buf)   > BROW_SMOOTH_N: eye_buf.pop(0)

                brow_score  = round(np.mean(brow_buf),  3)
                mouth_score = round(np.mean(mouth_buf), 3)
                eye_score   = round(np.mean(eye_buf),   3)

                brow_delta  = round(brow_score  - baseline_brow,  3)
                mouth_delta = round(mouth_score - baseline_mouth, 3)
                eye_delta   = round(eye_score   - baseline_eye,   3)

                emotion, frust_signal = classify_emotion(brow_delta, mouth_delta)

            # Frustration accumulator
            if emotion == "FRUSTRATED":
                frust_accumulator = min(frust_accumulator + FRUST_FILL_RATE * dt, FRUST_MAX)
            elif emotion == "CONFUSED":
                frust_accumulator = min(frust_accumulator + (FRUST_FILL_RATE * 0.5) * dt, FRUST_MAX)
            else:
                frust_accumulator = max(frust_accumulator - FRUST_DRAIN_RATE * dt, 0.0)

            # Head pose accumulator
            if direction not in ("FORWARD", "N/A"):
                pose_accumulator = min(pose_accumulator + POSE_FILL_RATE * dt, POSE_MAX)
            else:
                pose_accumulator = max(pose_accumulator - POSE_DRAIN_RATE * dt, 0.0)

            recent_blinks = [t for t in blink_timestamps if elapsed - t <= 60.0]
            blink_rate    = round(len(recent_blinks) / min(elapsed, 60.0) * 60, 1) \
                            if elapsed > 0 else 0.0

            attention_score = compute_attention_score(blink_rate, pose_accumulator, elapsed)
            frustration_score = compute_frustration_score(frust_accumulator, blink_rate)

        # Blink rate for display
        recent_blinks = [t for t in blink_timestamps if elapsed - t <= 60.0]
        blink_rate    = round(len(recent_blinks) / min(elapsed, 60.0) * 60, 1) \
                        if elapsed > 0 else 0.0

        # display
        display = frame.copy()
        status_color = (0, 200, 0) if face_detected else (0, 0, 200)
        cv2.rectangle(display, (0, 0), (w, 40), status_color, -1)
        cv2.putText(display, "Face Detected" if face_detected else "No Face Detected",
                    (10, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

        y = 55
        ear_color = (0, 0, 255) if (avg_ear is not None and avg_ear < EAR_THRESHOLD) else (200, 200, 255)
        cv2.putText(display, f"Avg EAR: {avg_ear:.3f}" if avg_ear else "Avg EAR: --",
                    (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, ear_color, 1)
        cv2.putText(display, f"Blinks: {blink_count}   Rate: {blink_rate:.1f}/min",
                    (10, y+22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 100), 1)

        # Head pose
        if pitch is not None:
            dir_color = (0, 0, 255) if direction != "FORWARD" else (0, 200, 0)
            cv2.putText(display, f"Pose: Y{yaw:+.0f} P{pitch:+.0f} R{roll:+.0f}",
                        (10, y+50), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 255, 200), 1)
            cv2.putText(display, f"Direction: {direction}",
                        (10, y+72), cv2.FONT_HERSHEY_SIMPLEX, 0.6, dir_color, 2)

        # Brow / emotion
        if brow_delta is not None:
            cv2.putText(display, f"Brow delta: {brow_delta:+.3f}  Signal: {frust_signal:.3f}",
                        (10, y+100), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 255), 1)
            emo_colors = {
                "NEUTRAL":    (0, 200, 0),
                "CONFUSED":   (0, 165, 255),
                "FRUSTRATED": (0, 0, 255),
                "N/A":        (150, 150, 150)
            }
            cv2.putText(display, f"Emotion: {emotion}",
                        (10, y+122), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                        emo_colors.get(emotion, (255, 255, 255)), 2)
            cv2.putText(display, f"Frust acc: {frust_accumulator:.1f}  Pose acc: {pose_accumulator:.1f}",
                        (10, y+148), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)

        score_x = w - 220
        cv2.rectangle(display, (score_x - 10, 45), (w - 5, 200), (30, 30, 30), -1)
        cv2.putText(display, "SCORES", (score_x, 68),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

        # Attention score bar
        att_color = (0, 200, 0) if attention_score >= 60 else \
                    (0, 165, 255) if attention_score >= 30 else (0, 0, 255)
        cv2.putText(display, f"Attention", (score_x, 95),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
        cv2.rectangle(display, (score_x, 100), (w - 10, 118), (60, 60, 60), -1)
        bar_w = int((attention_score / 100.0) * (w - 10 - score_x))
        cv2.rectangle(display, (score_x, 100), (score_x + bar_w, 118), att_color, -1)
        cv2.putText(display, f"{attention_score:.0f}", (score_x, 135),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, att_color, 2)

        # Frustration score bar
        fru_color = (0, 200, 0) if frustration_score <= 30 else \
                    (0, 165, 255) if frustration_score <= 60 else (0, 0, 255)
        cv2.putText(display, f"Frustration", (score_x, 158),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
        cv2.rectangle(display, (score_x, 163), (w - 10, 181), (60, 60, 60), -1)
        bar_w2 = int((frustration_score / 100.0) * (w - 10 - score_x))
        cv2.rectangle(display, (score_x, 163), (score_x + bar_w2, 181), fru_color, -1)
        cv2.putText(display, f"{frustration_score:.0f}", (score_x, 198),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, fru_color, 2)

        cv2.putText(display, f"FPS: {fps:.1f}", (10, h - 25),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)
        
        cv2.putText(display, f"Elapsed: {elapsed:.1f}s", (10, h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)

        # Print data
        if time.time() - last_publish_time >= PUBLISH_INTERVAL:
            if face_detected:
                payload = {
                    "timestamp": round(elapsed, 1),
                    "attention_score": attention_score,
                    "frustration_score": frustration_score,
                    "blink_rate": blink_rate,
                    "blink_count": blink_count,
                    "emotion": emotion,
                    "direction": direction,
                    "frust_accumulator": round(frust_accumulator, 1),
                    "pose_accumulator": round(pose_accumulator, 1),
                    "avg_ear": avg_ear,
                    "fps": fps
                }
            else:
                payload = {
                    "timestamp": round(elapsed, 1),
                    "status": "NO_FACE"
                }
            print(f"DATA: {json.dumps(payload)}")
            last_publish_time = time.time()

        cv2.imshow("UAT Analytics -- Press Q to quit", display)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    total_time = time.time() - start_time
    print(f"\n=== Session Summary ===")
    print(f"  Duration      : {total_time:.1f}s")
    print(f"  Total blinks  : {blink_count}")
    print(f"  Avg blink rate: {blink_count / total_time * 60:.1f} blinks/min")

    cap.release()
    face_mesh.close()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    run()
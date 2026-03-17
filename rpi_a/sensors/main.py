"""
main.py
UAT Edge Analytics — main orchestrator.
 
Current:
    - FaceSensor (camera, gaze, face analytics)
"""
 
import os
import time
import subprocess
import json
import psutil
from collections import Counter
import cv2
 
from face_sensor import FaceSensor
 
# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
PUBLISH_INTERVAL = 1.0      # seconds between data publish
DEBUG = True     # set False in production (hides webcam feed)

# ---------------------------------------------------------------------------
# SETUP
# ---------------------------------------------------------------------------
 
# Pin process to core 0
proc = psutil.Process(os.getpid())
proc.cpu_affinity([0])
print(f"[Setup] Pinned to cores: {proc.cpu_affinity()}")
 
# Screen resolution (needed for gaze calibration dot placement)
def _get_screen_resolution():
    try:
        out = (
            subprocess.check_output("xrandr | grep '*' | awk '{print $1}'", shell=True)
            .decode().strip().split("\n")[0]
        )
        w, h = map(int, out.split("x"))
        return w, h
    except Exception:
        return 1920, 1080
 
screen_w, screen_h = _get_screen_resolution()
print(f"[Setup] Screen: {screen_w}x{screen_h}")
 
# ---------------------------------------------------------------------------
# MODULE INIT
# ---------------------------------------------------------------------------
 
# Face sensor
face_sensor = FaceSensor(screen_w, screen_h, debug=DEBUG)

 
# ---------------------------------------------------------------------------
# CALIBRATION
# ---------------------------------------------------------------------------
face_sensor.calibrate()
 
# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------
print("[Main] Starting analytics loop. Press Q to quit.\n")
 
last_publish_time = time.time()
 
while True:
    # ---- Face ----
    face_result = face_sensor.update()
    
    # ---- Publish ----
    now = time.time()
    if now - last_publish_time >= PUBLISH_INTERVAL:
        if face_result and face_result.get("face_detected"):
            payload = {
                "timestamp":        round(now, 1),
                # Face signals
                "frustration_score": face_result["frustration_score"],
                "attention_score":   face_result["attention_score"],
                "emotion":           face_result["emotion"],
                "direction":         face_result["direction"],
                "gaze_quadrant":     face_result["gaze_quadrant"],
                "blink_rate":        face_result["blink_rate"],
                "avg_ear":           face_result["avg_ear"],
            }
        else:
            payload = {
                "timestamp": round(now, 1),
                "status":    "NO_FACE",
            }
 
        print(f"DATA: {json.dumps(payload)}")
 
        last_publish_time = now
        
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break
 
# ---------------------------------------------------------------------------
# TEARDOWN
# ---------------------------------------------------------------------------
face_sensor.stop()
print("[Main] Done.")
"""
CameraStreamBefore.py
PASO Benchmark: CameraStream with NO background thread.
 
Used by face_sensor_before.py, which is a single-threaded camera I/O baseline with the full production pipeline.
"""

import cv2
import subprocess
import time


class CameraStreamBefore:
    """
    Camera stream class without a background thread. Every read() blocks the caller until a new frame arrives.
    """
    
    def __init__(self, src=0, width=320, height=240, fps=30):
        subprocess.run(["fuser", "-k", "/dev/video0"], capture_output=True)
        time.sleep(1)

        # Lock exposure for consistent 30 FPS regardless of lighting
        subprocess.run(
            [
                "v4l2-ctl",
                "--device=/dev/video0",
                "--set-ctrl=exposure_dynamic_framerate=0",
            ]
        )
        subprocess.run(
            ["v4l2-ctl", "--device=/dev/video0", "--set-ctrl=auto_exposure=1"]
        )
        subprocess.run(
            ["v4l2-ctl", "--device=/dev/video0", "--set-ctrl=gain=95"]
        )  # Amplifies the signal (Add brightness, grains/noise)
        subprocess.run(
            ["v4l2-ctl", "--device=/dev/video0", "--set-ctrl=brightness=150"]
        )  # Shifts pixel values up/down

        self.cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)

        # Drain stale buffer frames
        for _ in range(10):
            self.cap.read()

        self.ret, self.frame = self.cap.read()
        
    def read(self):
        """
        Blocking read: calls cap.read() directly.
        Caller stalls here until the camera delivers a new frame (~17-19ms at 30 FPS).
        Compare to CameraStream.read() which returns instantly from a cached frame.
        """
        self.ret, self.frame = self.cap.read()
        return self.ret, self.frame.copy() if self.frame is not None else (False, None)

    def get(self, prop):
        return self.cap.get(prop)

    def getBackendName(self):
        return self.cap.getBackendName()

    def stop(self):
        self.cap.release()

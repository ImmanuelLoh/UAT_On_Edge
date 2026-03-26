import numpy as np
import time
import cv2


class GazeCalibrator:
    def __init__(self, screen_w, screen_h, collect_seconds=5.0):
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.collect_seconds = collect_seconds

        # Quadrant centers as screen fractions
        # Each tuple is (x_fraction, y_fraction)
        self.calibration_points = {
            "TOP-LEFT": (0.25, 0.25),
            "TOP-RIGHT": (0.75, 0.25),
            "BOTTOM-LEFT": (0.25, 0.75),
            "BOTTOM-RIGHT": (0.75, 0.75),
            "CENTER": (0.50, 0.50),
        }
        self.order = ["CENTER", "TOP-LEFT", "TOP-RIGHT", "BOTTOM-LEFT", "BOTTOM-RIGHT"]
        self.collected_gaze = {}  # gaze samples
        self.collected_pose = {}  # head pose samples (yaw, pitch)
        self.boundaries = None
        self.quadrant_bounds = None  # pixel boundaries for each quadrant
        self._fused_centers = None  # fused (gaze+pose) centres per corner, for NN classify
        self.current_idx = 0
        self.collecting = False
        self.collect_start = None
        self.current_samples = []
        self.done = False

    def get_dot_pixel(self, label=None):
        """Returns pixel (x, y) for a calibration point label."""
        if label is None:
            label = self.get_current_target()
        if label is None:
            return None
        fx, fy = self.calibration_points[label]
        return (int(fx * self.screen_w), int(fy * self.screen_h))

    def get_current_target(self):
        if self.current_idx < len(self.order):
            return self.order[self.current_idx]
        return None

    def update(self, gaze_x, gaze_y, triggered, yaw=0.0, pitch=0.0):
        if self.done:
            return "Calibration complete"

        label = self.get_current_target()
        if label is None:
            self._compute_boundaries()
            self.done = True
            return "Calibration complete"

        if not self.collecting:
            if triggered:
                self.collecting = True
                self.collect_start = time.perf_counter()
                self.current_samples = []
            return f"Look at {label} dot — press SPACE to collect"

        self.current_samples.append((gaze_x, gaze_y, yaw, pitch))
        elapsed = time.perf_counter() - self.collect_start
        remaining = self.collect_seconds - elapsed

        if elapsed >= self.collect_seconds:
            samples = np.array(self.current_samples)
            self.collected_gaze[label] = np.mean(samples[:, :2], axis=0).tolist()
            self.collected_pose[label] = np.mean(samples[:, 2:], axis=0).tolist()
            print(f"  [{label}] gaze mean = {self.collected_gaze[label]}")
            print(f"  [{label}] pose mean (yaw, pitch) = {self.collected_pose[label]}")
            self.current_idx += 1
            self.collecting = False
            if self.current_idx >= len(self.order):
                self._compute_boundaries()
                self.done = True
                return "Calibration complete"
            next_label = self.order[self.current_idx]
            return f"Got {label}! Next: {next_label} — press SPACE"

        return f"Collecting {label}... {remaining:.1f}s"

    def _compute_boundaries(self):
        """
        Compute gaze x/y split thresholds from calibrated points.
        Also compute pixel quadrant boundaries for the actual screen.
        """
        # Gaze Boundaries
        left_x = np.mean(
            [self.collected_gaze["TOP-LEFT"][0], self.collected_gaze["BOTTOM-LEFT"][0]]
        )
        right_x = np.mean(
            [
                self.collected_gaze["TOP-RIGHT"][0],
                self.collected_gaze["BOTTOM-RIGHT"][0],
            ]
        )
        top_y = np.mean(
            [self.collected_gaze["TOP-LEFT"][1], self.collected_gaze["TOP-RIGHT"][1]]
        )
        bot_y = np.mean(
            [
                self.collected_gaze["BOTTOM-LEFT"][1],
                self.collected_gaze["BOTTOM-RIGHT"][1],
            ]
        )

        self.boundaries = {
            "x_split": (left_x + right_x) / 2.0,
            "y_split": (top_y + bot_y) / 2.0,
        }
        
        # Pose boundaries
        left_yaw = np.mean(
            [self.collected_pose["TOP-LEFT"][0], self.collected_pose["BOTTOM-LEFT"][0]]
        )
        right_yaw = np.mean(
            [
                self.collected_pose["TOP-RIGHT"][0],
                self.collected_pose["BOTTOM-RIGHT"][0],
            ]
        )
        top_pitch = np.mean(
            [self.collected_pose["TOP-LEFT"][1], self.collected_pose["TOP-RIGHT"][1]]
        )
        bot_pitch = np.mean(
            [
                self.collected_pose["BOTTOM-LEFT"][1],
                self.collected_pose["BOTTOM-RIGHT"][1],
            ]
        )
        
        self.pose_boundaries = {
            "yaw_split": (left_yaw + right_yaw) / 2.0,
            "pitch_split": (top_pitch + bot_pitch) / 2.0,
            "yaw_range": [left_yaw, right_yaw],
            "pitch_range": [top_pitch, bot_pitch],
        }
        print(f"Pose boundaries: {self.pose_boundaries}")

        # Pixel quadrant boundaries on the actual screen
        self.quadrant_bounds = {
            "TOP-LEFT": (0, 0, self.screen_w // 2, self.screen_h // 2),
            "TOP-RIGHT": (self.screen_w // 2, 0, self.screen_w, self.screen_h // 2),
            "BOTTOM-LEFT": (0, self.screen_h // 2, self.screen_w // 2, self.screen_h),
            "BOTTOM-RIGHT": (
                self.screen_w // 2,
                self.screen_h // 2,
                self.screen_w,
                self.screen_h,
            ),
        }
        
        # Detect CENTER zone
        center_gaze_x = self.collected_gaze["CENTER"][0]
        center_gaze_y = self.collected_gaze["CENTER"][1]
        center_yaw = self.collected_pose["CENTER"][0]
        center_pitch = self.collected_pose["CENTER"][1]
        
        # Tolerance = fraction of the range from center to edge
        gaze_x_half = abs(right_x - left_x) / 2.0
        gaze_y_half = abs(bot_y   - top_y)  / 2.0
        
        self.center_zone = {
            "gaze_x_min": center_gaze_x - gaze_x_half * 0.25,
            "gaze_x_max": center_gaze_x + gaze_x_half * 0.25,
            "gaze_y_min": center_gaze_y - gaze_y_half * 0.25,
            "gaze_y_max": center_gaze_y + gaze_y_half * 0.25,
        }
        
        # Pre-compute fused centres (iris + head pose) for each corner.
        # Used by classify() for nearest-neighbour comparison so that both
        # the live signal and the reference points are in the same fused space.
        GAZE_WEIGHT = 0.7
        HEAD_WEIGHT = 0.3
        gaze_x_range = [left_x, right_x]
        gaze_y_range = [top_y, bot_y]
        yaw_range  = self.pose_boundaries["yaw_range"]
        pitch_range = self.pose_boundaries["pitch_range"]
        self._fused_centers = {}
        for label in ["TOP-LEFT", "TOP-RIGHT", "BOTTOM-LEFT", "BOTTOM-RIGHT"]:
            gx, gy = self.collected_gaze[label]
            cal_yaw, cal_pitch = self.collected_pose[label]
            norm_yaw   = np.interp(cal_yaw,   yaw_range,   gaze_x_range)
            norm_pitch = np.interp(cal_pitch, pitch_range, gaze_y_range)
            self._fused_centers[label] = (
                GAZE_WEIGHT * gx + HEAD_WEIGHT * norm_yaw,
                GAZE_WEIGHT * gy + HEAD_WEIGHT * norm_pitch,
            )

        print(f"Center zone: {self.center_zone}")
        print(f"Gaze boundaries: {self.boundaries}")
        print(f"Fused centres: {self._fused_centers}")
        print(f"Screen: {self.screen_w}x{self.screen_h}")
        print(f"Quadrant pixel bounds: {self.quadrant_bounds}")

    def classify(self, lx, ly, rx, ry, yaw=0.0, pitch=0.0):
        """
        Infers gaze quadrant and estimated screen pixel from current gaze and head pose.
        Returns (quadrant_label, avg_gaze_x, avg_gaze_y, pixel_x, pixel_y).
        pixel_x/y is the estimated gaze point on screen.
        """
        if self.boundaries is None or self._fused_centers is None:
            return "UNCALIBRATED", 0, 0, 0, 0

        avg_gaze_x = (lx + rx) / 2.0
        avg_gaze_y = (ly + ry) / 2.0

        # Normalise pose to same scale as gaze using calibbrated ranges
        yaw_range = self.pose_boundaries["yaw_range"]
        pitch_range = self.pose_boundaries["pitch_range"]

        gaze_x_range = [
            np.mean(
                [
                    self.collected_gaze["TOP-LEFT"][0],
                    self.collected_gaze["BOTTOM-LEFT"][0],
                ]
            ),
            np.mean(
                [
                    self.collected_gaze["TOP-RIGHT"][0],
                    self.collected_gaze["BOTTOM-RIGHT"][0],
                ]
            ),
        ]
        gaze_y_range = [
            np.mean(
                [
                    self.collected_gaze["TOP-LEFT"][1],
                    self.collected_gaze["TOP-RIGHT"][1],
                ]
            ),
            np.mean(
                [
                    self.collected_gaze["BOTTOM-LEFT"][1],
                    self.collected_gaze["BOTTOM-RIGHT"][1],
                ]
            ),
        ]

        # Map pose to gaze scale using calibrated ranges
        norm_yaw = np.interp(yaw, yaw_range, gaze_x_range)
        norm_pitch = np.interp(pitch, pitch_range, gaze_y_range)

        # Weighted fusion
        GAZE_WEIGHT = 0.7
        HEAD_WEIGHT = 0.3
        fused_x = GAZE_WEIGHT * avg_gaze_x + HEAD_WEIGHT * norm_yaw
        fused_y = GAZE_WEIGHT * avg_gaze_y + HEAD_WEIGHT * norm_pitch
        
        # Check if within CENTER zone first
        center_zone = self.center_zone
        if (center_zone["gaze_x_min"] <= fused_x <= center_zone["gaze_x_max"] and
            center_zone["gaze_y_min"] <= fused_y <= center_zone["gaze_y_max"]):
            quadrant = "CENTER"
        else:
            # Nearest-neighbour in fused space: compare (fused_x, fused_y) against
            # pre-computed fused centres so both sides use the same coordinate space.
            best, best_dist = None, float("inf")
            for label, (cx, cy) in self._fused_centers.items():
                d = (fused_x - cx) ** 2 + (fused_y - cy) ** 2
                if d < best_dist:
                    best_dist = d
                    best = label
            quadrant = best


        px = int(np.interp(fused_x, gaze_x_range, [0, self.screen_w]))
        py = int(np.interp(fused_y, gaze_y_range, [0, self.screen_h]))

        return quadrant, avg_gaze_x, avg_gaze_y, px, py

    def draw_calibration_screen(self, screen_w, screen_h):
        """
        Returns a fullscreen calibration canvas (not the webcam frame).
        """
        canvas = np.zeros((screen_h, screen_w, 3), dtype=np.uint8)

        # Draw all already-collected points in green
        for label in self.order[: self.current_idx]:
            gx, gy = self.get_dot_pixel(label)
            cv2.circle(canvas, (gx, gy), 20, (0, 255, 0), -1)
            cv2.putText(
                canvas,
                label,
                (gx + 25, gy + 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2,
            )

        # Draw current target dot
        current = self.get_current_target()
        if current:
            dot = self.get_dot_pixel(current)
            color = (0, 0, 255) if not self.collecting else (0, 165, 255)
            cv2.circle(canvas, dot, 25, color, -1)
            cv2.circle(canvas, dot, 28, (255, 255, 255), 2)  # white ring

            # Crosshair lines to help user aim
            cv2.line(
                canvas, (dot[0] - 40, dot[1]), (dot[0] + 40, dot[1]), (255, 255, 255), 1
            )
            cv2.line(
                canvas, (dot[0], dot[1] - 40), (dot[0], dot[1] + 40), (255, 255, 255), 1
            )

        # Status text at bottom center
        if not self.done:
            label = self.get_current_target()
            msg = f"Look at the dot ({label}) — press SPACE to collect"
            if self.collecting:
                elapsed = time.perf_counter() - self.collect_start
                remaining = self.collect_seconds - elapsed
                msg = f"Collecting {label}... {remaining:.1f}s"
            text_x = screen_w // 2 - 350
            text_y = screen_h - 60
            cv2.putText(
                canvas,
                msg,
                (text_x, text_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (255, 255, 0),
                2,
            )

        return canvas

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
        self.boundaries = None
        self.quadrant_bounds = None  # pixel boundaries for each quadrant
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

    def update(self, gaze_x, gaze_y, triggered):
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

        self.current_samples.append((gaze_x, gaze_y))
        elapsed = time.perf_counter() - self.collect_start
        remaining = self.collect_seconds - elapsed

        if elapsed >= self.collect_seconds:
            samples = np.array(self.current_samples)
            self.collected_gaze[label] = np.mean(samples[:, :2], axis=0).tolist()
            print(f"  [{label}] gaze mean = {self.collected_gaze[label]}")
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
        print(f"Gaze boundaries: {self.boundaries}")
        print(f"Screen: {self.screen_w}x{self.screen_h}")
        print(f"Quadrant pixel bounds: {self.quadrant_bounds}")

    def classify(self, lx, ly, rx, ry):
        """
        Returns (quadrant_label, avg_gaze_x, avg_gaze_y, pixel_x, pixel_y).
        pixel_x/y is the estimated gaze point on screen.
        """
        if self.boundaries is None:
            return "UNCALIBRATED", 0, 0, 0, 0

        avg_x = (lx + rx) / 2.0
        avg_y = (ly + ry) / 2.0

        h = "LEFT" if avg_x < self.boundaries["x_split"] else "RIGHT"
        v = "TOP" if avg_y < self.boundaries["y_split"] else "BOTTOM"
        quadrant = f"{v}-{h}"

        # Map gaze offset to estimated screen pixel
        # Linear interpolation between calibrated left/right and top/bottom gaze values
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

        px = np.interp(avg_x, [left_x, right_x], [0, self.screen_w])
        py = np.interp(avg_y, [top_y, bot_y], [0, self.screen_h])

        return quadrant, avg_x, avg_y, int(px), int(py)

    def draw_calibration_screen(self, screen_w, screen_h):
        """
        Returns a fullscreen calibration canvas (not the webcam frame).
        """
        cv2.namedWindow("Calibration", cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(
            "Calibration", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN
        )

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

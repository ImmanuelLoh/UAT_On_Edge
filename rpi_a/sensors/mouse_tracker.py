import tkinter as tk
from pynput import mouse

import time
import threading
from collections import Counter
import math

class MouseTracker:
    def __init__(self, idle_threshold: int, interval: float) -> None:
        root = tk.Tk()
        self.screen_width = root.winfo_screenwidth()
        self.screen_height = root.winfo_screenheight()
        root.destroy()

        self.running = True

        self.idle_threshold = idle_threshold
        self.interval = interval

        self.start_time = self.get_time_now()
        self.last_activity = self.get_time_now()

        self.total_clicks = 0
        self.click_logs = []

        self.current_click_count = 0
        self.current_quadrant_count = []

    def get_time_now(self):
        return math.floor(time.time() * 1000)

    def get_quadrant(self, x, y):
        mid_x = self.screen_width / 2
        mid_y = self.screen_height / 2

        if x <= mid_x and y <= mid_y:   return "Top-Left"
        elif x > mid_x and y <= mid_y:  return "Top-Right"
        elif x <= mid_x and y > mid_y:  return "Bottom-Left"
        else:                           return "Bottom-Right"

    def on_mouse_activity(self, *args) -> None:
        self.last_activity = self.get_time_now()

    def on_mouse_click(self, x, y, button, pressed) -> None:
        if pressed:
            timestamp = self.get_time_now()
            quadrant = self.get_quadrant(x, y)

            self.total_clicks += 1
            self.last_activity = timestamp

            self.current_click_count += 1
            self.current_quadrant_count.append(quadrant)

            click_record = {
                "timestamp": timestamp,
                "quadrant": quadrant,
                "x": x,
                "y": y,
            }
            self.click_logs.append(click_record)

    def generate_metrics(self):
        if len(self.current_quadrant_count) > 0:
            quadrant_stats = Counter(self.current_quadrant_count)
            top_quadrant, top_quadrant_count = quadrant_stats.most_common(1)[0]
        else:
            quadrant_stats = Counter(self.current_quadrant_count)
            top_quadrant, top_quadrant_count = None, 0

        now = self.get_time_now()
        idle_time = math.floor((now - self.last_activity) / 1000)
        total_time = math.floor((now - self.start_time) / 1000)

        return {
            "quadrant_stats": dict(quadrant_stats),
            "top_quadrant": top_quadrant,
            "top_quadrant_count": top_quadrant_count,
            "idle_time": idle_time,
            "mouse_status": "IDLE" if idle_time > self.idle_threshold else "ACTIVE",
            "interval_clicks_per_second": round(self.current_click_count / self.interval),
            "overall_clicks_per_second": round(self.total_clicks / total_time),
        }

    def tracking_loop(self):
        while self.running:
            time.sleep(self.interval)
            
            current_stat = self.generate_metrics()
            print(current_stat)

            self.current_click_count = 0
            self.current_quadrant_count = []

    def start(self) -> None:
        print("[DEBUG] Starting Mouse Tracker...")
        self.thread = threading.Thread(target=self.tracking_loop, daemon=True)
        self.thread.start()

        try:    
            with mouse.Listener(
                on_click=self.on_mouse_click, 
                on_move=self.on_mouse_activity, on_scroll=self.on_mouse_activity
            ) as listener:
                listener.join()

        except Exception as e:
            print(f"[ERROR] Mouse tracking failed: {e}")

if __name__ == "__main__":
    try:
        tracker = MouseTracker(idle_threshold=5, interval=1)
        tracker.start()
    except KeyboardInterrupt:
        pass

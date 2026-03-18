import time
import json
import math
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

from .uat_monitor import UATMonitor, UATTask  # Correct import route


class WebTracker:
    def __init__(
        self, uat_monitor: UATMonitor, interval: float, url="http://127.0.0.1:5000"
    ):
        self.url = url
        self.uat_monitor = uat_monitor
        self.interval = interval

        self.last_retrieval_time = self.get_time_now()

        binary_path = "/usr/bin/chromium"
        driver_path = "/usr/bin/chromedriver"

        options = Options()
        options.binary_location = binary_path
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--start-maximized")

        service = Service(driver_path)

        self.driver = webdriver.Chrome(service=service, options=options)
        self._inject_listener()

    def get_time_now(self):
        return math.floor(time.time() * 1000)

    def _inject_listener(self):
        """Injects a JS queue to store multiple clicks in localStorage."""
        script = """
            // Initialize the queue if it doesn't exist
            if (!localStorage.getItem('clickQueue')) {
                localStorage.setItem('clickQueue', JSON.stringify([]));
            }

            document.addEventListener('click', function(e) {
                var el = e.target;
                var clickData = {
                    tag: el.tagName,
                    id: el.id,
                    className: el.className,
                    text: el.innerText ? el.innerText.substring(0, 20).replace(/\\n/g, ' ') : "No Text",
                    timestamp: Date.now()
                };
                
                // Get existing list, push new click, and save back
                var queue = JSON.parse(localStorage.getItem('clickQueue') || '[]');
                queue.push(clickData);
                localStorage.setItem('clickQueue', JSON.stringify(queue));
            }, true);
        """

        # Page.addScriptToEvaluateOnNewDocument ensures this persists across refreshes
        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": script}
        )

    def start(self):
        """Starts the browser and processes the click queue."""
        try:
            self.driver.get(self.url)
            print(f"\n[ACTIVE] Tracking clicks on: {self.url}")

            while True:
                raw_data = self.driver.execute_script(
                    """
                    var data = localStorage.getItem('clickQueue');
                    localStorage.setItem('clickQueue', JSON.stringify([]));
                    return data;
                """
                )

                current_poll_time = self.get_time_now()
                if raw_data:
                    click_list = json.loads(raw_data)

                    if click_list:
                        for click in click_list:
                            self.uat_monitor.process_click(click)

                self.uat_monitor.get_current_window_stats()
                self.last_retrieval_time = current_poll_time
                time.sleep(self.interval)

        except KeyboardInterrupt:
            print("\n[STOPPED] Tracking ended.")
        finally:
            self.driver.quit()


if __name__ == "__main__":
    uat_monitor = UATMonitor()

    task1 = UATTask(
        task_name="Start Session", target_ids=[], success_id="btn-start-task"
    )
    uat_monitor.add_task(task1)

    task2 = UATTask(task_name="Click the Color", target_ids=[], success_id="color-blue")
    uat_monitor.add_task(task2)

    task3 = UATTask(
        task_name="Number Selections",
        target_ids=["label-1", "label-3", "label-7"],
        success_id="btn-submit-selection",
        selection_ids=["label-1", "label-3", "label-7"],
    )
    uat_monitor.add_task(task3)

    tracker = WebTracker(uat_monitor, 1)
    tracker.start()

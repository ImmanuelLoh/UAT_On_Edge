import subprocess
import time


class ProcessSupervisor:
    def __init__(self, name, start_func, restart_delay=2):
        self.name = name
        self.start_func = start_func
        self.restart_delay = restart_delay
        self.process = None
        self.last_restart_time = 0

    def ensure_running(self):
        now = time.time()

        # If process is not running, start it
        if self.process is None:
            self.process = self.start_func()
            self.last_restart_time = now
            return

        # If process has exited, restart it (with delay to prevent rapid restarts)
        exit_code = self.process.poll()
        if exit_code is not None and (now - self.last_restart_time) >= self.restart_delay:
            print(f"[WARN] {self.name} exited with code {exit_code}. Restarting...")
            self.process = self.start_func()
            self.last_restart_time = now

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
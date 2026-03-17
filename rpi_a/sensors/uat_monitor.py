import time
from typing import List
import math

class UATTask:
    def __init__(self, task_name, target_ids, success_id, selection_ids = []):
        self.task_name = task_name
        self.target_ids = target_ids
        self.success_id = success_id
        self.selection_ids = sorted(selection_ids)
        
        self.current_selections = []

        self.correct_clicks = []
        self.wrong_clicks = []

        self.correct_count = 0
        self.wrong_count = 0

        self.start_time = self.get_time_now()
        self.end_time = None
        self.total_duration = None

        self.is_completed = False

    def get_time_now(self):
        return math.floor(time.time() * 1000)

    def update_current_selection(self, element_id):
        if element_id not in self.selection_ids:
            return

        if element_id in self.current_selections:
            self.current_selections.remove(element_id)
        else:
            self.current_selections.append(element_id)

        self.current_selections.sort()

    def record_click(self, element):
        clicked_element = {
            "timetamp": element["timestamp"],
            "id": element["id"],
            "tag": element["tag"],
            "className": element["className"]
        }

        # Check a Empty ID
        element_id = element["id"]
        if element_id == "":
            self.wrong_clicks.append(clicked_element)
            self.wrong_count += 1
            return

        # Validate Element ID 
        if element_id in self.target_ids:
            self.correct_clicks.append(clicked_element)
            self.correct_count += 1
        else:
            self.wrong_clicks.append(clicked_element)
            self.wrong_count += 1

        # Confirm & Valiidate Selection
        if self.selection_ids is not None:
            self.update_current_selection(element_id)

        # Prevent Completion of Task when incorrect selection made
        if self.current_selections != self.selection_ids and self.selection_ids is not None:
            return

        # Update Task Completion
        if element_id == self.success_id:
            self.is_completed = True
            self.end_time = self.get_time_now()
            self.total_duration = self.end_time - self.start_time

    def reset_count(self):
        self.correct_count = 0
        self.wrong_count = 0

class UATMonitor:
    def __init__(self):
        self.tasks: List[UATTask] = []
        self.active_task_index = 0
        self.session_log = []

    def get_time_now(self):
        return math.floor(time.time() * 1000)

    def add_task(self, task_instance):
        self.tasks.append(task_instance)

    def switch_task(self, index = None):
        if index == None:
            next_index = self.active_task_index + 1
        else:
            next_index = index

        self.active_task_index = min(next_index, len(self.tasks)-1)

    def reset_UAT(self, index = 0):
        self.active_task_index = min(index, len(self.tasks) - 1)

    def process_click(self, element):
        current_task: UATTask = self.tasks[self.active_task_index]

        log_entry = {
            "taskName": current_task.task_name,
            "timestamp": element["timestamp"],
            "id": element["id"],
            "tag": element["tag"],
            "className": element["className"]
        }
        self.session_log.append(log_entry)
        current_task.record_click(log_entry)

        if current_task.is_completed:
            self.switch_task()
            if self.active_task_index < len(self.tasks) - 1:
                self.tasks[self.active_task_index].start_time = self.get_time_now()

    def generate_metrics(self):
        current_task: UATTask = self.tasks[self.active_task_index]

        metrics = {"tasks": {}}
        
        for task in self.tasks:
            metrics["tasks"][task.task_name] =  {
                "startTime": task.start_time,
                "endTime": task.end_time,
                "totalDuration": task.total_duration,
            }
        
        metrics["currentTask"] = {
            "taskName": current_task.task_name,
            "correct_click": current_task.correct_count,
            "wrong_click": current_task.wrong_count,
        }

        return metrics

    def get_current_window_stats(self):
        """Prints a final report of all tasks with start, end, and duration."""
        print("\n" + "="*50)
        print(f"{'Web Activity Summary':^50}")

        current_stat = self.generate_metrics()
        print(current_stat)
        
        print("="*50)

        current_task: UATTask = self.tasks[self.active_task_index]
        # current_task.reset_count()

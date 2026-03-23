"""
firebase_client.py
Supervisor machine — Firebase Firestore uploader.

Firestore path:
    {LAB_ID}/{computer_id}/sessions/{session_id}/meta
    {LAB_ID}/{computer_id}/sessions/{session_id}/events/chunk_00
    {LAB_ID}/{computer_id}/sessions/{session_id}/events/chunk_01
    ...
    {LAB_ID}/{computer_id}/sessions/{session_id}/summary

    e.g. Lab1/Computer_1/sessions/2026-03-23_14-32-01/events/chunk_00

Write pattern:
    - meta:    1 write on session start
    - chunks:  1 write per CHUNK_INTERVAL seconds (default 60s), accumulates ticks in memory
    - summary: 1 write on session end (flushes remaining buffer first)

    For a 30-min session: ~32 writes total (1 meta + 30 chunks + 1 summary)

Setup:
    pip install firebase-admin
    Place serviceAccountKey.json next to this file.
    (Firebase Console → Project Settings → Service Accounts → Generate new private key)
"""

import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
LAB_ID               = "Lab1"                     # hardcoded per supervisor machine
SERVICE_ACCOUNT_PATH = Path("serviceAccountKey.json")
LOCAL_BUFFER_DIR     = Path("session_logs")
CHUNK_INTERVAL       = 60                         # seconds between chunk writes
MAX_QUEUE_SIZE       = 500
RETRY_INTERVAL       = 30                         # seconds between retry sweeps
SGT                  = timezone(timedelta(hours=8))

# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


class FirebaseClient:
    """
    Thread-safe Firestore client for a single computer's session stream.

    Ticks are accumulated in memory and flushed to Firestore as a chunk
    document every CHUNK_INTERVAL seconds. No per-tick writes.

    Typical flow (called from mqtt_dashboard.py):
        client = FirebaseClient(computer_id=5000)
        client.push(payload)           # called on every tick — buffered in memory
        client.push_summary(summary)   # called when uat/summary arrives
        client.stop()
    """

    def __init__(
        self,
        computer_id: str,
        lab_id: str = LAB_ID,
        service_account_path: Path = SERVICE_ACCOUNT_PATH,
        local_buffer_dir: Path = LOCAL_BUFFER_DIR,
    ):
        self.computer_id  = computer_id.replace(" ", "_")  # sanitize for Firestore paths
        self.lab_id       = lab_id
        self.local_buffer_dir = local_buffer_dir
        self.local_buffer_dir.mkdir(parents=True, exist_ok=True)

        self.session_id: str | None = None
        self._local_log: Path | None = None
        self._session_lock = threading.Lock()

        # Chunk accumulation
        self._event_buffer: list[dict] = []
        self._chunk_index: int = 0
        self._last_chunk_write: float = 0
        self._buffer_lock = threading.Lock()

        # Upload queue (chunks + meta + summary)
        self._queue: queue.Queue[dict] = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

        # Init Firebase — safe across multiple instances
        if not firebase_admin._apps:
            cred = credentials.Certificate(str(service_account_path))
            firebase_admin.initialize_app(cred)

        self._db = firestore.client()
        logger.info(f"[Firebase] Ready — {self.lab_id}/{self.computer_id}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push(self, payload: dict) -> None:
        """
        Accept a tick payload. Saves locally and buffers in memory.
        Flushes a chunk to Firestore every CHUNK_INTERVAL seconds.
        Automatically starts a new session on first call.
        """
        with self._session_lock:
            if not self.session_id:
                self._start_session_async()

        enriched = {
            "lab_id":      self.lab_id,
            "computer_id": self.computer_id,
            "session_id":  self.session_id,
            "uploaded_at": datetime.now(SGT).isoformat(),
            **payload,
        }

        # Always save locally first
        self._save_locally(enriched)

        # Accumulate in memory buffer
        with self._buffer_lock:
            self._event_buffer.append(enriched)

            # Flush chunk if interval has elapsed
            if time.time() - self._last_chunk_write >= CHUNK_INTERVAL:
                self._enqueue_chunk(force=False)

    def push_summary(self, summary: dict) -> None:
        """
        Flush remaining buffered ticks as a final chunk, then write the
        summary document and close the session.
        Called when uat/summary MQTT message arrives from the Pi.
        """
        with self._session_lock:
            if not self.session_id:
                logger.warning(f"[Firebase] {self.computer_id}: push_summary with no active session")
                return
            session_to_close = self.session_id

        # Flush any remaining ticks as a final partial chunk
        with self._buffer_lock:
            if self._event_buffer:
                self._enqueue_chunk(force=True)

        # Wait for all queued chunks to upload before writing summary
        self._queue.join()

        ended_at = datetime.now(SGT).isoformat()
        summary_doc = {
            "lab_id":      self.lab_id,
            "computer_id": self.computer_id,
            "session_id":  session_to_close,
            "ended_at":    ended_at,
            **summary,
        }

        # Save locally first
        self._save_locally({"_type": "summary", **summary_doc})

        try:
            self._session_doc_ref(session_to_close).set({
                **summary_doc,
                "status":   "completed",
                "ended_at": ended_at,
            }, merge=True)
            logger.info(f"[Firebase] {self.computer_id}: session {session_to_close} completed")
        except Exception as e:
            logger.warning(f"[Firebase] {self.computer_id}: summary upload failed, buffered: {e}")

        # Reset — next push() will open a new session
        with self._session_lock:
            self.session_id = None
            self._local_log = None

        with self._buffer_lock:
            self._chunk_index = 0
            self._last_chunk_write = 0

    def stop(self) -> None:
        """Flush remaining buffer and shut down background threads."""
        with self._buffer_lock:
            if self._event_buffer:
                self._enqueue_chunk(force=True)

        self._stop_event.set()
        if self._worker_thread:
            self._worker_thread.join(timeout=5)

    # ------------------------------------------------------------------
    # Internal — chunk management
    # ------------------------------------------------------------------

    def _enqueue_chunk(self, force: bool = False) -> None:
        """
        Package the current event buffer as a chunk and put it on the
        upload queue. Must be called with _buffer_lock held.
        force=True bypasses the interval check (used for final flush).
        """
        if not self._event_buffer:
            return
        if not force and time.time() - self._last_chunk_write < CHUNK_INTERVAL:
            return

        chunk = {
            "_type":       "chunk",
            "session_id":  self.session_id,
            "chunk_id":    f"chunk_{self._chunk_index:02d}",
            "chunk_index": self._chunk_index,
            "ticks":       list(self._event_buffer),
            "from_ts":     self._event_buffer[0].get("timestamp"),
            "to_ts":       self._event_buffer[-1].get("timestamp"),
            "tick_count":  len(self._event_buffer),
        }

        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            logger.warning(f"[Firebase] {self.computer_id}: chunk queue full, dropping chunk {chunk['chunk_id']}")

        self._event_buffer = []
        self._chunk_index += 1
        self._last_chunk_write = time.time()

    # ------------------------------------------------------------------
    # Internal — session management
    # ------------------------------------------------------------------

    def _start_session_async(self) -> None:
        """
        Assigns session_id immediately so push() can continue without delay.
        Writes the meta document to Firestore in a background thread.
        Must be called with _session_lock held.
        """
        self.session_id = datetime.now(SGT).strftime("%Y-%m-%d_%H-%M-%S")
        self._local_log = (
            self.local_buffer_dir
            / f"{self.lab_id}_{self.computer_id}_{self.session_id}.jsonl"
        )

        # Reset chunk state for new session
        with self._buffer_lock:
            self._event_buffer = []
            self._chunk_index = 0
            self._last_chunk_write = time.time()

        # Start worker threads if not running
        if not self._worker_thread or not self._worker_thread.is_alive():
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._upload_worker, daemon=True,
                name=f"firebase-worker-{self.computer_id}"
            )
            self._worker_thread.start()
            threading.Thread(
                target=self._retry_loop, daemon=True,
                name=f"firebase-retry-{self.computer_id}"
            ).start()

        meta = {
            "lab_id":      self.lab_id,
            "computer_id": self.computer_id,
            "session_id":  self.session_id,
            "started_at":  datetime.now(SGT).isoformat(),
            "status":      "active",
        }

        logger.info(f"[Firebase] {self.computer_id}: new session {self.session_id}")
        threading.Thread(
            target=self._write_meta, args=(meta, self.session_id),
            daemon=True, name=f"firebase-meta-{self.computer_id}"
        ).start()

    def _write_meta(self, meta: dict, session_id: str) -> None:
        try:
            self._session_doc_ref(session_id).set(meta)
        except Exception as e:
            logger.warning(f"[Firebase] {self.computer_id}: meta write failed: {e}")
            self._save_locally({"_type": "meta", **meta})

    # ------------------------------------------------------------------
    # Internal — Firestore references
    # ------------------------------------------------------------------

    def _computer_ref(self):
        """Lab1/Computer_1"""
        return self._db.collection(self.lab_id).document(self.computer_id)

    def _session_ref(self, session_id: str | None = None):
        """Lab1/Computer_1/sessions/{session_id}"""
        sid = session_id or self.session_id
        return self._computer_ref().collection("sessions").document(sid)

    def _session_doc_ref(self, session_id: str):
        return (
            self._db
            .collection(self.lab_id)        # collection
            .document(self.computer_id)     # document
            .collection("sessions")         # collection
            .document(session_id)           # document (DocumentReference)
        )
    # ------------------------------------------------------------------
    # Internal — upload worker
    # ------------------------------------------------------------------

    def _upload_worker(self) -> None:
        """Background thread — drains queue and writes chunks to Firestore."""
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                record_type = item.get("_type")
                sid = item.get("session_id") or self.session_id

                if record_type == "chunk":
                    self._session_doc_ref(sid) \
                        .collection("events") \
                        .document(item["chunk_id"]) \
                        .set(item)
                    logger.debug(
                        f"[Firebase] {self.computer_id}: wrote {item['chunk_id']} "
                        f"({item['tick_count']} ticks)"
                    )

                elif record_type == "meta":
                    self._session_doc_ref(sid).set(item)

                elif record_type == "summary":
                    self._session_doc_ref(sid).set(item, merge=True)

                self._queue.task_done()

            except Exception as e:
                logger.warning(f"[Firebase] {self.computer_id}: upload failed, retrying: {e}")
                self._queue.task_done()
                self._save_locally({**item, "_retry": True})

    # ------------------------------------------------------------------
    # Internal — local persistence + retry
    # ------------------------------------------------------------------

    def _save_locally(self, data: dict) -> None:
        path = self._local_log or (
            self.local_buffer_dir / f"{self.lab_id}_{self.computer_id}_unsessioned.jsonl"
        )
        try:
            with open(path, "a") as f:
                f.write(json.dumps(data) + "\n")
        except Exception as e:
            logger.error(f"[Firebase] {self.computer_id}: local save failed: {e}")

    def _retry_loop(self) -> None:
        """Re-queues _retry-flagged records from local JSONL every RETRY_INTERVAL seconds."""
        while not self._stop_event.is_set():
            time.sleep(RETRY_INTERVAL)

            pattern = f"{self.lab_id}_{self.computer_id}_*.jsonl"
            for log_file in self.local_buffer_dir.glob(pattern):
                retry_file = log_file.with_suffix(".retrying.jsonl")
                try:
                    log_file.rename(retry_file)
                except Exception:
                    continue

                archive_lines = []
                retried = 0

                with open(retry_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            if record.get("_retry") or record.get("_type") in ("meta", "summary", "chunk"):
                                self._queue.put_nowait(record)
                                retried += 1
                            else:
                                archive_lines.append(line)
                        except Exception:
                            archive_lines.append(line)

                if archive_lines:
                    with open(log_file, "a") as f:
                        for line in archive_lines:
                            f.write(line + "\n")

                retry_file.unlink(missing_ok=True)

                if retried:
                    logger.info(
                        f"[Firebase] {self.computer_id}: retried {retried} records "
                        f"from {log_file.name}"
                    )


# ---------------------------------------------------------------------------
# Factory function for FirebaseClient instances
# ---------------------------------------------------------------------------
def make_client(computer_id: str) -> FirebaseClient:
    return FirebaseClient(computer_id=computer_id)
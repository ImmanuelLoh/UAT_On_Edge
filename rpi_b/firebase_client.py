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
    - replay:  1 write per CHUNK_SIZE ticks (replaces existing chunks with full timeline)

    For a 30-min session: ~32 writes total (1 meta + 30 chunks + 1 summary)
    On replay:           ceil(total_ticks / REPLAY_CHUNK_SIZE) writes, replacing old chunks

NEW — Replay reassembly (uat/replay topic)
------------------------------------------
RPI_A sends the complete snapshot timeline as numbered QoS-1 fragments after
the session ends.  mqtt_dashboard calls ingest_replay_fragment() for each one.

When all fragments for a session arrive (or a timeout fires), FirebaseClient:
  1. Stitches the fragments back into a sorted, deduplicated tick list.
  2. Merges any ticks only seen in the local JSONL log (fills gaps from when
     RPI_B was down or a chunk write failed).
  3. Re-chunks the merged timeline at REPLAY_CHUNK_SIZE ticks.
  4. Replaces every existing events/* document in Firestore with the new chunks
     so the final timeline is always complete and authoritative.

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
LAB_ID               = "Lab1"
SERVICE_ACCOUNT_PATH = Path("serviceAccountKey.json")
LOCAL_BUFFER_DIR     = Path("session_logs")
CHUNK_INTERVAL       = 60          # seconds between live chunk writes
MAX_QUEUE_SIZE       = 500
RETRY_INTERVAL       = 30          # seconds between retry sweeps
SGT                  = timezone(timedelta(hours=8))

# Replay-specific
REPLAY_CHUNK_SIZE    = 120         # ticks per Firestore document in the final timeline
REPLAY_TIMEOUT       = 60.0        # seconds to wait for all fragments before using what we have

# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


class FirebaseClient:
    """
    Thread-safe Firestore client for a single computer's session stream.

    Ticks are accumulated in memory and flushed to Firestore as a chunk
    document every CHUNK_INTERVAL seconds. No per-tick writes.

    Typical flow (called from mqtt_dashboard.py):
        client = FirebaseClient(computer_id=5000)
        client.push(payload)                        # every tick — buffered in memory
        client.push_summary(summary)                # on uat/summary
        client.ingest_replay_fragment(session_id, seq, total, ticks)  # on uat/replay
        client.stop()
    """

    def __init__(
        self,
        computer_id: str,
        lab_id: str = LAB_ID,
        service_account_path: Path = SERVICE_ACCOUNT_PATH,
        local_buffer_dir: Path = LOCAL_BUFFER_DIR,
    ):
        self.computer_id  = str(computer_id).replace(" ", "_")
        self.lab_id       = lab_id
        self.local_buffer_dir = local_buffer_dir
        self.local_buffer_dir.mkdir(parents=True, exist_ok=True)

        self.session_id: str | None = None
        self._local_log: Path | None = None
        self._session_lock = threading.Lock()

        # Chunk accumulation (live ticks)
        self._event_buffer: list[dict] = []
        self._chunk_index: int = 0
        self._last_chunk_write: float = 0
        self._buffer_lock = threading.Lock()

        # Upload queue (chunks + meta + summary)
        self._queue: queue.Queue[dict] = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

        # Replay reassembly — keyed by session_id
        # { session_id: {"total": N, "fragments": {seq: [ticks]}, "timer": Timer} }
        self._replay_state: dict[str, dict] = {}
        self._replay_lock = threading.Lock()

        # Init Firebase — safe across multiple instances
        if not firebase_admin._apps:
            cred = credentials.Certificate(str(service_account_path))
            firebase_admin.initialize_app(cred)

        self._db = firestore.client()
        logger.info(f"[Firebase] Ready — {self.lab_id}/{self.computer_id}")

    # ------------------------------------------------------------------
    # Public API — live ticks
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

        self._save_locally(enriched)

        with self._buffer_lock:
            self._event_buffer.append(enriched)
            if time.time() - self._last_chunk_write >= CHUNK_INTERVAL:
                self._enqueue_chunk(force=False)

    def push_summary(self, summary: dict) -> None:
        """
        Flush remaining buffered ticks as a final chunk, then write the
        summary document and close the session.
        """
        with self._session_lock:
            if not self.session_id:
                logger.warning(f"[Firebase] {self.computer_id}: push_summary with no active session")
                return
            session_to_close = self.session_id

        with self._buffer_lock:
            if self._event_buffer:
                self._enqueue_chunk(force=True)

        self._queue.join()

        ended_at = datetime.now(SGT).isoformat()
        summary_doc = {
            "lab_id":      self.lab_id,
            "computer_id": self.computer_id,
            "session_id":  session_to_close,
            "ended_at":    ended_at,
            **summary,
        }

        self._save_locally({"_type": "summary", **summary_doc})

        try:
            self._session_doc_ref(session_to_close).set({
                **summary_doc,
                "status":   "completed",
                "ended_at": ended_at,
            }, merge=True)
            logger.info(f"[Firebase] {self.computer_id}: session {session_to_close} completed")
        except Exception as e:
            logger.warning(f"[Firebase] {self.computer_id}: summary upload failed: {e}")

        with self._session_lock:
            self.session_id = None
            self._local_log = None

        with self._buffer_lock:
            self._chunk_index = 0
            self._last_chunk_write = 0

    def stop(self) -> None:
        with self._buffer_lock:
            if self._event_buffer:
                self._enqueue_chunk(force=True)

        self._stop_event.set()
        if self._worker_thread:
            self._worker_thread.join(timeout=5)

    # ------------------------------------------------------------------
    # Public API — replay reassembly  (NEW)
    # ------------------------------------------------------------------

    def ingest_replay_fragment(self, session_id: str, seq: int,
                               total: int, ticks: list[dict]) -> None:
        """
        Called by mqtt_dashboard for every uat/replay message.

        Collects fragments until all *total* pieces arrive, then triggers
        _finalize_replay().  A watchdog timer fires after REPLAY_TIMEOUT
        seconds so a missing fragment never stalls the process.
        """
        with self._replay_lock:
            state = self._replay_state.setdefault(session_id, {
                "total":     total,
                "fragments": {},
                "timer":     None,
            })

            state["fragments"][seq] = ticks
            state["total"] = total   # keep in sync in case first fragment was lost

            received = len(state["fragments"])
            logger.info(f"[Replay] {self.computer_id}/{session_id}: "
                        f"fragment {seq + 1}/{total} ({received}/{total} received)")

            # Cancel any existing watchdog and reset it — every new fragment
            # gives us another REPLAY_TIMEOUT window.
            if state["timer"] is not None:
                state["timer"].cancel()

            all_arrived = received >= total
            if all_arrived:
                state["timer"] = None
            else:
                state["timer"] = threading.Timer(
                    REPLAY_TIMEOUT,
                    self._timeout_replay,
                    args=(session_id,),
                )
                state["timer"].daemon = True
                state["timer"].start()

        if all_arrived:
            logger.info(f"[Replay] {self.computer_id}/{session_id}: all fragments received — finalising")
            self._finalize_replay(session_id)

    # ------------------------------------------------------------------
    # Internal — replay finalisation
    # ------------------------------------------------------------------

    def _timeout_replay(self, session_id: str) -> None:
        """Watchdog: finalize with whatever fragments we have after the timeout."""
        with self._replay_lock:
            state = self._replay_state.get(session_id)
            if state is None:
                return  # already finalized
            received = len(state["fragments"])
            total    = state["total"]

        logger.warning(f"[Replay] {self.computer_id}/{session_id}: "
                       f"timeout — only {received}/{total} fragments arrived, "
                       f"patching from local log")
        self._finalize_replay(session_id)

    def _finalize_replay(self, session_id: str) -> None:
        """
        1. Stitch received fragments → sorted tick list.
        2. Load the local JSONL and merge any ticks not in the replay.
        3. Re-chunk and replace-write all events/* documents in Firestore.
        """
        with self._replay_lock:
            state = self._replay_state.pop(session_id, None)

        if state is None:
            return  # already handled (race between timer and completion)

        if state["timer"] is not None:
            state["timer"].cancel()

        # ── 1. Reassemble fragments in order ──────────────────────────
        replay_ticks: list[dict] = []
        for seq in sorted(state["fragments"]):
            replay_ticks.extend(state["fragments"][seq])

        logger.info(f"[Replay] {self.computer_id}/{session_id}: "
                    f"{len(replay_ticks)} ticks from MQTT replay")

        # ── 2. Merge with local JSONL (gap-fill) ─────────────────────
        local_ticks = self._load_local_ticks(session_id)
        merged = self._merge_ticks(replay_ticks, local_ticks)

        logger.info(f"[Replay] {self.computer_id}/{session_id}: "
                    f"{len(merged)} ticks after merge with local log "
                    f"({len(local_ticks)} local ticks inspected)")

        if not merged:
            logger.warning(f"[Replay] {self.computer_id}/{session_id}: no ticks to write")
            return

        # ── 3. Replace-write all events/* in Firestore ───────────────
        threading.Thread(
            target=self._replace_events_in_firestore,
            args=(session_id, merged),
            daemon=True,
            name=f"replay-write-{self.computer_id}-{session_id}",
        ).start()

    def _load_local_ticks(self, session_id: str) -> list[dict]:
        """Read all tick records (not meta/summary/chunk wrappers) from the local JSONL."""
        ticks: list[dict] = []
        pattern = f"{self.lab_id}_{self.computer_id}_{session_id}*.jsonl"

        for log_file in self.local_buffer_dir.glob(pattern):
            try:
                with open(log_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            # Skip wrapper records; keep raw tick dicts
                            if record.get("_type") in ("meta", "summary", "chunk"):
                                continue
                            if record.get("timestamp") is not None:
                                ticks.append(record)
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                logger.warning(f"[Replay] Could not read {log_file}: {e}")

        return ticks

    def _merge_ticks(self, replay: list[dict], local: list[dict]) -> list[dict]:
        """
        Return a deduplicated, timestamp-sorted union of *replay* and *local*.

        Deduplication key: timestamp (float).  If two ticks share the same
        timestamp, the replay version wins (it came directly from RPI_A).
        """
        by_ts: dict[float, dict] = {}

        # Local first so replay overwrites on collision
        for tick in local:
            ts = tick.get("timestamp")
            if ts is not None:
                by_ts[float(ts)] = tick

        for tick in replay:
            ts = tick.get("timestamp")
            if ts is not None:
                by_ts[float(ts)] = tick

        return sorted(by_ts.values(), key=lambda t: t["timestamp"])

    def _replace_events_in_firestore(self, session_id: str,
                                     merged_ticks: list[dict]) -> None:
        """
        Delete all existing events/* documents for *session_id*, then write
        the merged timeline as fresh chunks.

        Uses Firestore batched writes (max 500 ops per batch).
        """
        events_ref = (
            self._session_doc_ref(session_id)
            .collection("events")
        )

        # ── Delete existing chunks ────────────────────────────────────
        try:
            existing = events_ref.stream()
            batch = self._db.batch()
            count = 0
            for doc in existing:
                batch.delete(doc.reference)
                count += 1
                if count % 499 == 0:   # flush batch before hitting the 500-op limit
                    batch.commit()
                    batch = self._db.batch()
                    count = 0
            if count:
                batch.commit()
            logger.info(f"[Replay] {self.computer_id}/{session_id}: "
                        f"deleted existing event documents")
        except Exception as e:
            logger.warning(f"[Replay] {self.computer_id}/{session_id}: "
                           f"could not delete existing events: {e}")

        # ── Write merged chunks ───────────────────────────────────────
        chunk_index = 0
        for i in range(0, len(merged_ticks), REPLAY_CHUNK_SIZE):
            chunk_ticks = merged_ticks[i: i + REPLAY_CHUNK_SIZE]
            chunk_id    = f"chunk_{chunk_index:02d}"
            chunk_doc   = {
                "_type":       "chunk",
                "_source":     "replay",
                "session_id":  session_id,
                "chunk_id":    chunk_id,
                "chunk_index": chunk_index,
                "ticks":       chunk_ticks,
                "from_ts":     chunk_ticks[0].get("timestamp"),
                "to_ts":       chunk_ticks[-1].get("timestamp"),
                "tick_count":  len(chunk_ticks),
            }
            try:
                events_ref.document(chunk_id).set(chunk_doc)
                logger.info(f"[Replay] {self.computer_id}/{session_id}: "
                            f"wrote {chunk_id} ({len(chunk_ticks)} ticks)")
            except Exception as e:
                logger.warning(f"[Replay] {self.computer_id}/{session_id}: "
                               f"failed to write {chunk_id}: {e}")
                self._save_locally({**chunk_doc, "_retry": True})
            chunk_index += 1

        # ── Mark session as replay-complete ──────────────────────────
        try:
            self._session_doc_ref(session_id).set({
                "replay_completed_at": datetime.now(SGT).isoformat(),
                "replay_tick_count":   len(merged_ticks),
                "status":              "completed",
            }, merge=True)
            logger.info(f"[Replay] {self.computer_id}/{session_id}: "
                        f"replace-write done — {len(merged_ticks)} ticks in "
                        f"{chunk_index} chunks")
        except Exception as e:
            logger.warning(f"[Replay] {self.computer_id}/{session_id}: "
                           f"could not update session status: {e}")

    # ------------------------------------------------------------------
    # Internal — chunk management (live)
    # ------------------------------------------------------------------

    def _enqueue_chunk(self, force: bool = False) -> None:
        """Package current event buffer as a chunk. Must be called with _buffer_lock held."""
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
            logger.warning(f"[Firebase] {self.computer_id}: chunk queue full, "
                           f"dropping chunk {chunk['chunk_id']}")

        self._event_buffer = []
        self._chunk_index += 1
        self._last_chunk_write = time.time()

    # ------------------------------------------------------------------
    # Internal — session management
    # ------------------------------------------------------------------

    def _start_session_async(self) -> None:
        """Assigns session_id immediately; writes meta doc in background."""
        self.session_id = datetime.now(SGT).strftime("%Y-%m-%d_%H-%M-%S")
        self._local_log = (
            self.local_buffer_dir
            / f"{self.lab_id}_{self.computer_id}_{self.session_id}.jsonl"
        )

        with self._buffer_lock:
            self._event_buffer = []
            self._chunk_index = 0
            self._last_chunk_write = time.time()

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
        return self._db.collection(self.lab_id).document(self.computer_id)

    def _session_ref(self, session_id: str | None = None):
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
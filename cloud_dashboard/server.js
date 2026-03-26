/**
 * Setup:
 *   1. Add serviceAccountKey.json to this directory
 *   2. npm install firebase-admin express cors
 *   3. node server.js
 */

const express = require("express");
const cors = require("cors");
const admin = require("firebase-admin");
const path = require("path");

// ── Init Firebase Admin ──────────────────────────────────────────────────────
const serviceAccount = require(path.join(__dirname, "serviceAccountKey.json"));

admin.initializeApp({
  credential: admin.credential.cert(serviceAccount),
});

const db = admin.firestore();

// ── Express setup ────────────────────────────────────────────────────────────
const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static(__dirname));

const LAB_IDS = ["Lab1", "Lab2", "Lab3"];

const LAB_COMPUTERS = {
  Lab1: ["Computer_1", "Computer_2", "Computer_3"],
  Lab2: ["Computer1", "Computer2", "Computer3"],
  Lab3: ["Computer1", "Computer2", "Computer3"],
};

// ─────────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────────

async function getLatestSession(labId, computerId) {
  const ref = db
    .collection(labId)
    .doc(computerId)
    .collection("sessions");

  const snap = await ref
    .orderBy(admin.firestore.FieldPath.documentId(), "desc")
    .limit(1)
    .get();

  if (snap.empty) return null;

  const doc = snap.docs[0];
  return { sessionId: doc.id, ...doc.data() };
}

async function getLatestTick(labId, computerId, sessionId) {
  const ref = db
    .collection(labId)
    .doc(computerId)
    .collection("sessions")
    .doc(sessionId)
    .collection("events");

  const snap = await ref
    .orderBy(admin.firestore.FieldPath.documentId(), "desc")
    .limit(1)
    .get();

  if (snap.empty) return null;

  const data = snap.docs[0].data();
  const ticks = data.ticks || [];

  return ticks.length ? ticks[ticks.length - 1] : null;
}

// ─────────────────────────────────────────────────────────────
// LIVE DASHBOARD
// ─────────────────────────────────────────────────────────────

app.get("/api/labs", async (req, res) => {
  try {
    const result = [];

    for (const labId of LAB_IDS) {
      const computerIds = LAB_COMPUTERS[labId] || [];

      const computers = [];

      for (const computerId of computerIds) {
        const session = await getLatestSession(labId, computerId);

        if (!session) {
          computers.push({
            computerId,
            state: "no_data",
          });
          continue;
        }

        const isCompleted = session.status === "completed";

        const tick = isCompleted
          ? null
          : await getLatestTick(labId, computerId, session.sessionId);

        computers.push({
          computerId,
          state: isCompleted ? "inactive" : "active",
          sessionId: session.sessionId,
          sessionData: session,
          tick,
        });
      }

      const hasAnyData = computers.some((c) => c.sessionId);

      if (hasAnyData) {
        result.push({ labId, computers });
      }
    }

    res.json({
      ok: true,
      labs: result,
      fetchedAt: new Date().toISOString(),
    });
  } catch (err) {
    console.error(err);
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ─────────────────────────────────────────────────────────────
// HISTORY (COMPLETED SESSIONS)
// ─────────────────────────────────────────────────────────────

app.get("/api/history", async (req, res) => {
  try {
    const limit = parseInt(req.query.limit || "5");

    const result = [];

    for (const labId of LAB_IDS) {
      const computers = [];

      for (const computerId of LAB_COMPUTERS[labId] || []) {
        const ref = db
          .collection(labId)
          .doc(computerId)
          .collection("sessions");

        const snap = await ref
          .where("status", "==", "completed")
          .orderBy("ended_at", "desc")
          .limit(limit)
          .get();

        if (snap.empty) continue;

        const sessions = snap.docs.map((d) => ({
          sessionId: d.id,
          ...d.data(),
        }));

        computers.push({ computerId, sessions });
      }

      if (computers.length) {
        result.push({ labId, computers });
      }
    }

    res.json({ ok: true, labs: result });
  } catch (err) {
    console.error(err);
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ─────────────────────────────────────────────────────────────
// SESSION TIMELINE (RECONSTRUCT FROM CHUNKS)
// ─────────────────────────────────────────────────────────────

app.get("/api/session/:labId/:computerId/:sessionId", async (req, res) => {
  const { labId, computerId, sessionId } = req.params;

  try {
    const sessionRef = db
      .collection(labId)
      .doc(computerId)
      .collection("sessions")
      .doc(sessionId);

    const sessionSnap = await sessionRef.get();

    if (!sessionSnap.exists) {
      return res.status(404).json({ ok: false, error: "Session not found" });
    }

    const sessionData = sessionSnap.data();

    // Chunks are stored as documents under the 'chunks' subcollection,
    // each containing a 'ticks' array of snapshots in chronological order.
    const chunksSnap = await sessionRef.collection("events").get();

    const chunks = chunksSnap.docs
      .map((d) => ({ chunkId: d.id, ...d.data() }))
      .sort((a, b) => a.chunkId.localeCompare(b.chunkId));

    // Reconstruct full timeline by concatenating ticks from each chunk in order.
    let timeline = [];
    for (const c of chunks) {
      if (Array.isArray(c.ticks)) {
        timeline = timeline.concat(c.ticks);
      }
    }
    timeline.sort((a, b) => a.timestamp - b.timestamp);

    // Use pre-computed aggregates from the session document — don't recalculate.
    const aggregates = sessionData.aggregates || {};

    // Build per-task summary from the timeline.
    // Each task segment: first/last timestamp, max wrong_click, max correct_click, LLM activated.
    const taskMap = new Map();
    timeline.forEach((p) => {
      const task = p.browser?.task || 'Unknown';
      if (!taskMap.has(task)) {
        taskMap.set(task, {
          task,
          startTs: p.timestamp,
          endTs:   p.timestamp,
          wrongClicks:   0,
          correctClicks: 0,
          llmActivated:  false,
          peakFrustration: 0,
          peakFrustrationTs: null,
          firstLlmTs: null,
        });
      }
      const t = taskMap.get(task);
      t.endTs = p.timestamp;
      // wrong_click and correct_click are cumulative counters — take the max seen
      t.wrongClicks   = Math.max(t.wrongClicks,   Number(p.browser?.wrong_click   || 0));
      t.correctClicks = Math.max(t.correctClicks, Number(p.browser?.correct_click || 0));
      if (p.llm?.llm_activated && !t.firstLlmTs) {
        t.llmActivated = true;
        t.firstLlmTs   = p.timestamp;
      }
      const fr = Number(p.face?.frustration_score || 0);
      if (fr > t.peakFrustration) {
        t.peakFrustration   = fr;
        t.peakFrustrationTs = p.timestamp;
      }
    });
    const taskSummary = Array.from(taskMap.values()).map(t => ({
      ...t,
      durationSeconds: t.endTs - t.startTs,
    }));

    // Derive events: first LLM activation per task, peak frustration per task.
    const events = [];
    taskSummary.forEach((t) => {
      if (t.peakFrustrationTs && t.peakFrustration >= 70) {
        events.push({ timestamp: t.peakFrustrationTs, task: t.task, label: `Peak frustration: ${t.peakFrustration.toFixed(1)}`, type: 'frustration' });
      }
      if (t.firstLlmTs) {
        events.push({ timestamp: t.firstLlmTs, task: t.task, label: 'LLM activated', type: 'llm' });
      }
    });
    events.sort((a, b) => a.timestamp - b.timestamp);

    res.json({
      ok: true,
      sessionId,
      started_at: sessionData.started_at || null,
      ended_at:   sessionData.ended_at   || null,
      meta:       sessionData.meta       || { total_snapshots: timeline.length },
      aggregates,
      chunks,
      timeline,
      taskSummary,
      events,
    });
  } catch (err) {
    console.error(err);
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ─────────────────────────────────────────────────────────────

const PORT = 3000;
app.listen(PORT, () => {
  console.log(`Server running on http://localhost:${PORT}`);
  console.log(`View dashboard on: http://localhost:${PORT}/lab_dashboard.html`)
});
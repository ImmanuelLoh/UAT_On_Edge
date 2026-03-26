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

// Serve the dashboard HTML from same folder
app.use(express.static(__dirname));

const LAB_IDS = ["Lab1", "Lab2", "Lab3"];

// Known computer IDs per lab.
// Update these lists if lab layout changes.
const LAB_COMPUTERS = {
  Lab1: ["Computer_1", "Computer_2", "Computer_3"],
  Lab2: ["Computer1", "Computer2", "Computer3"],
  Lab3: ["Computer1", "Computer2", "Computer3"],
};

// ── Helper: get known computers for a lab ────────────────────────────────────
function getKnownComputers(labId) {
  return LAB_COMPUTERS[labId] || [];
}

// ── Helper: get latest session for a computer ────────────────────────────────
// Sessions are stored at:
//   /{labId}/{computerId}/sessions/{sessionId}
// Session IDs are timestamp-like strings, so ordering by document ID
// descending should return the newest one.
async function getLatestSession(labId, computerId) {
  const sessionsRef = db
    .collection(labId)
    .doc(computerId)
    .collection("sessions");

  const snap = await sessionsRef
    .orderBy(admin.firestore.FieldPath.documentId(), "desc")
    .limit(1)
    .get();

  if (snap.empty) return null;

  const doc = snap.docs[0];
  return {
    sessionId: doc.id,
    ...doc.data(),
  };
}

// ── Helper: get latest tick from latest chunk ────────────────────────────────
// Event chunks are stored at:
//   /{labId}/{computerId}/sessions/{sessionId}/events/{chunkId}
// Example chunk IDs: chunk_00, chunk_01, ...
// Read the latest chunk doc, then take the last tick from its ticks array.
async function getLatestTick(labId, computerId, sessionId) {
  const eventsRef = db
    .collection(labId)
    .doc(computerId)
    .collection("sessions")
    .doc(sessionId)
    .collection("events");

  const snap = await eventsRef
    .orderBy(admin.firestore.FieldPath.documentId(), "desc")
    .limit(1)
    .get();

  if (snap.empty) return null;

  const latestChunkDoc = snap.docs[0];
  const chunkData = latestChunkDoc.data() || {};
  const ticks = Array.isArray(chunkData.ticks) ? chunkData.ticks : [];

  if (ticks.length === 0) return null;

  return ticks[ticks.length - 1];
}

// ── Helper: build one computer summary ───────────────────────────────────────
async function buildComputerSummary(labId, computerId) {
  try {
    const session = await getLatestSession(labId, computerId);

    if (!session) {
      return {
        computerId,
        sessionId: null,
        sessionData: null,
        tick: null,
      };
    }

    const { sessionId, ...sessionData } = session;
    const tick = await getLatestTick(labId, computerId, sessionId);

    return {
      computerId,
      sessionId,
      sessionData,
      tick,
    };
  } catch (err) {
    console.warn(`Skipping ${labId}/${computerId}: ${err.message}`);
    return {
      computerId,
      sessionId: null,
      sessionData: null,
      tick: null,
      error: err.message,
    };
  }
}

// ── GET /api/labs — returns all labs and their computers ─────────────────────
app.get("/api/labs", async (req, res) => {
  try {
    const result = [];

    for (const labId of LAB_IDS) {
      const computerIds = getKnownComputers(labId);
      if (computerIds.length === 0) continue;

      const computers = [];
      for (const computerId of computerIds) {
        const computerSummary = await buildComputerSummary(labId, computerId);
        computers.push(computerSummary);
      }

      const hasAnyData = computers.some(
        (c) => c.sessionId || c.sessionData || c.tick
      );

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
    console.error("Error in /api/labs:", err);
    res.status(500).json({
      ok: false,
      error: err.message,
    });
  }
});

// ── GET /api/lab/:labId — single lab ─────────────────────────────────────────
app.get("/api/lab/:labId", async (req, res) => {
  const { labId } = req.params;

  try {
    const computerIds = getKnownComputers(labId);
    if (computerIds.length === 0) {
      return res.json({
        ok: true,
        labId,
        computers: [],
        fetchedAt: new Date().toISOString(),
      });
    }

    const computers = [];
    for (const computerId of computerIds) {
      const computerSummary = await buildComputerSummary(labId, computerId);
      computers.push(computerSummary);
    }

    const hasAnyData = computers.some(
      (c) => c.sessionId || c.sessionData || c.tick
    );

    res.json({
      ok: true,
      labId,
      computers: hasAnyData ? computers : [],
      fetchedAt: new Date().toISOString(),
    });
  } catch (err) {
    console.error(`Error in /api/lab/${labId}:`, err);
    res.status(500).json({
      ok: false,
      error: err.message,
    });
  }
});

// ── Optional debug endpoint ──────────────────────────────────────────────────
app.get("/api/debug/firestore", async (req, res) => {
  try {
    const collections = await db.listCollections();
    res.json({
      ok: true,
      projectId: serviceAccount.project_id,
      collections: collections.map((c) => c.id),
    });
  } catch (err) {
    res.status(500).json({
      ok: false,
      error: err.message,
    });
  }
});

// ── Start ─────────────────────────────────────────────────────────────────────
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`\nLabWatch proxy running at http://localhost:${PORT}`);
  console.log(`Dashboard: http://localhost:${PORT}/lab_dashboard.html`);
  console.log(`API:       http://localhost:${PORT}/api/labs\n`);
});
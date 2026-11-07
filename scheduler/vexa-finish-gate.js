"use strict";

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const WORKER_RETRY_MS = 10 * 60 * 1000;

function read(file) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return null; }
}

function inspectVexaFinish(meetingsDir, eventId) {
  if (!eventId || eventId === "." || eventId === ".." || /[/\\]/.test(eventId)) throw new Error("Invalid event ID");
  const dir = path.join(meetingsDir, eventId);
  const state = read(path.join(dir, "transcription.state.json"));
  const joining = read(path.join(dir, ".joining"));
  const status = read(path.join(dir, "status.json"));
  const lifecycle = read(path.join(dir, "vexa.lifecycle.json"));
  const isVexa = state?.vexa_bot_id || joining?.backend?.startsWith("vexa") || status?.source === "vexa_authenticated";
  if (!isVexa) return { ready: true, reason: "legacy-meeting" };
  const noJoin = require("./vexa-no-join").inspectVexaNoJoin(dir);
  if (noJoin) return { ready: false, ...noJoin };
  const promotion = read(path.join(dir, "transcript.capture-promotion.json"));
  if (fs.existsSync(path.join(dir, "captures")) && promotion?.version !== "transcript.capture-promotion.v1") {
    // A preserved capture is only a conflict until the post-call worker promotes a
    // finished transcript out of it. After promotion the event is publishable.
    return { ready: false, reason: "prior-capture-needs-review", dir };
  }
  const triage = fs.existsSync(path.join(dir, "triage-context.json"));
  if (fs.existsSync(path.join(dir, ".finalized"))) {
    return { ready: true, reason: "publication-checkpoint" };
  }
  if (triage) {
    // A triage context alone is not a finished publication: the chat deliveries are
    // the last step and the only proof the team was actually told.
    const general = read(path.join(dir, ".slack-general.json"));
    const organizer = read(path.join(dir, ".triage-slack.json"));
    // A confidential event records `skipped` instead of a channel post.
    if (["sent", "skipped"].includes(general?.status) && organizer?.status === "sent") {
      return { ready: true, reason: "publication-checkpoint" };
    }
    return { ready: false, reason: "publication-reconcile-pending", dir };
  }
  const botId = state?.vexa_bot_id;
  const current = id => id != null && (!botId || String(id) === String(botId));
  const ended = (status?.source === "vexa_authenticated" && status.exited === true && current(status.vexaMeetingId)) ||
    (current(lifecycle?.meeting?.id) && ["completed", "failed"].includes(lifecycle.meeting.status));
  if (!ended) return { ready: false, reason: "vexa-still-capturing", dir };
  const signalReady = read(path.join(dir, ".vexa-signal-ready.json"));
  if (signalReady?.version === "vexa-signal-ready.v1" && signalReady.capped === true) {
    return { ready: false, reason: "signal-capped-needs-review", dir };
  }
  const signalComplete = signalReady?.version === "vexa-signal-ready.v1" && signalReady.complete === true &&
    String(signalReady.meeting_id) === String(botId) &&
    Array.isArray(signalReady.files) && signalReady.files.some(item => item?.name?.endsWith(".captured-signal.jsonl"));
  if (!signalComplete) {
    const terminalAt = Math.max(Number(status?.endedAt || 0) * 1000, Date.parse(lifecycle?.created_at || "") || 0);
    return { ready: false, reason: terminalAt && Date.now() - terminalAt > 10 * 60 * 1000 ? "signal-materialization-needs-review" : "signal-materializing", dir };
  }
  const terminal = ["completed", "disabled", "failed_nonretryable", "retry_exhausted"];
  const readabilityTerminal = ["completed", "completed_degraded", "canonical_fallback", "disabled"];
  if (state?.finalization_ready === true && ["live_status", "batch_status", "canonical_status"].every(k => terminal.includes(state[k])) && readabilityTerminal.includes(state.readability_status)) {
    const publication = read(path.join(dir, ".vexa-publication.json"));
    const hasPage = fs.existsSync(path.join(dir, "notion-page.json"));
    if (publication?.status === "needs_review") {
      if (hasPage && publication.reason === "approval-handoff-incomplete") {
        return { ready: false, reason: "publication-reconcile-pending", dir };
      }
      return { ready: false, reason: "publication-needs-review", dir };
    }
    if (publication?.status === "completed") return { ready: false, reason: "publication-needs-review", dir };
    if (publication?.status === "needs_agent") {
      if (hasPage) return { ready: false, reason: "publication-reconcile-pending", dir };
      const failurePath = path.join(dir, ".dispatch-finish.json");
      const failure = read(failurePath);
      const publicationMtime = fs.statSync(path.join(dir, ".vexa-publication.json")).mtimeMs;
      if (failure?.status === "failed" && fs.statSync(failurePath).mtimeMs > publicationMtime) {
        return { ready: false, reason: "publication-reconcile-pending", dir };
      }
      return { ready: true, reason: "publication-agent-needed" };
    }
    return { ready: true, reason: "publication-agent-needed" };
  }
  const worker = read(path.join(dir, ".vexa-postcall.json"));
  return { ready: false, reason: worker?.status === "exhausted" ? "postcall-needs-review" : "postcall-pending", dir, attempts: worker?.attempt };
}

function processIsRunning(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    const fields = fs.readFileSync(`/proc/${pid}/stat`, "utf8").split(" ");
    return fields[2] !== "Z";
  } catch { return false; }
}

function claimWorkerLaunch(dir, kind, nowMs = Date.now()) {
  const marker = path.join(dir, `.vexa-${kind}-launch.json`);
  const previous = read(marker);
  if (previous && processIsRunning(Number(previous.pid))) return false;
  try {
    if (nowMs - fs.statSync(marker).mtimeMs < WORKER_RETRY_MS) return false;
    fs.unlinkSync(marker);
  } catch (error) {
    if (error.code !== "ENOENT") return false;
  }
  try {
    const fd = fs.openSync(marker, "wx", 0o600);
    fs.writeSync(fd, JSON.stringify({ status: "launching", kind, launched_at: new Date(nowMs).toISOString() }));
    fs.closeSync(fd);
    return marker;
  } catch { return false; }
}

function recordWorkerPid(marker, pid) {
  fs.writeFileSync(marker, JSON.stringify({ status: "running", pid, launched_at: new Date().toISOString() }), { mode: 0o600 });
}

function startPostcallWorker(dir) {
  const marker = claimWorkerLaunch(dir, "postcall-worker");
  if (!marker) return false;
  const child = spawn("python3", [path.join(__dirname, "vexa-postcall-worker.py"), "--meeting-dir", dir], {
    detached: true, stdio: "ignore", env: { ...process.env, HERMES_HOME: path.resolve(dir, "../../..") },
  });
  child.on("error", () => { try { fs.unlinkSync(marker); } catch {} }); // No ready state is fabricated when the worker cannot start.
  recordWorkerPid(marker, child.pid);
  child.unref();
  return true;
}

function startPublicationWorker(dir) {
  // Do not run the internal deterministic Notion/Slack publisher. Allow original finish webhook to resume.
  const receipt = path.join(dir, ".vexa-publication.json");
  fs.writeFileSync(receipt, JSON.stringify({status:"needs_agent",updated_at:new Date().toISOString()}), {mode:0o600});
  return false;
}

module.exports = { inspectVexaFinish, claimWorkerLaunch, startPostcallWorker, startPublicationWorker };

"use strict";
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { inspectVexaFinish, claimWorkerLaunch } = require("./vexa-finish-gate");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "vexa-finish-test-"));
const dir = path.join(root, "event");fs.mkdirSync(dir);
const write = (name, value) => fs.writeFileSync(path.join(dir, name), JSON.stringify(value));
try {
  assert.strictEqual(inspectVexaFinish(root, "event").ready, true);
  write(".joining", { backend: "vexa-primary", pid: null });
  write("transcription.state.json", { vexa_bot_id: "10", live_status: "running" });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "vexa-still-capturing");
  // Legacy exit is not evidence that the remote Vexa bot has left.
  write("status.json", { exited: true, source: "google-captions" });
  assert.strictEqual(inspectVexaFinish(root, "event").ready, false);
  write("vexa.lifecycle.json", { meeting: { id: 9, status: "completed" } });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "vexa-still-capturing");
  write("vexa.lifecycle.json", { created_at: new Date(Date.now() - 11 * 60 * 1000).toISOString(), meeting: { id: 10, status: "completed" } });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "signal-materialization-needs-review");
  write("vexa.lifecycle.json", { created_at: new Date().toISOString(), meeting: { id: 10, status: "completed" } });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "signal-materializing");
  write(".vexa-signal-ready.json", { version: "vexa-signal-ready.v1", complete: true, meeting_id: "10",
    files: [{ name: "trace.captured-signal.jsonl" }] });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "postcall-pending");
  write(".vexa-signal-ready.json", { version: "vexa-signal-ready.v1", complete: false, capped: true, meeting_id: "10",
    files: [{ name: "trace.captured-signal.jsonl" }] });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "signal-capped-needs-review");
  write(".vexa-signal-ready.json", { version: "vexa-signal-ready.v1", complete: true, capped: false, meeting_id: "10",
    files: [{ name: "trace.captured-signal.jsonl" }] });
  write("transcription.state.json", { vexa_bot_id: "10", finalization_ready: true, live_status: "running" });
  assert.strictEqual(inspectVexaFinish(root, "event").ready, false);
  write(".vexa-postcall.json", { status: "exhausted" });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "postcall-needs-review");
  write("transcription.state.json", { vexa_bot_id: "10", finalization_ready: true, live_status: "completed", batch_status: "completed", canonical_status: "completed", readability_status: "completed_degraded" });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "publication-agent-needed");
  write(".vexa-publication.json", { status: "needs_agent" });
  assert.strictEqual(inspectVexaFinish(root, "event").ready, true);
  write("notion-page.json", { page_id: "page" });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "publication-reconcile-pending");
  fs.rmSync(path.join(dir, "notion-page.json"));
  fs.utimesSync(path.join(dir, ".vexa-publication.json"), new Date(Date.now() - 1000), new Date(Date.now() - 1000));
  write(".dispatch-finish.json", { status: "failed", outcome: "notion-attachment-failed" });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "publication-reconcile-pending");
  write(".vexa-publication.json", { status: "needs_review", reason: "approval-handoff-incomplete" });
  write("notion-page.json", { page_id: "page" });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "publication-reconcile-pending");
  write(".vexa-publication.json", { status: "needs_review", reason: "page-reconciliation-ambiguous" });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "publication-needs-review");
  write("triage-context.json", { approval_status: "pending" });
  write("transcription.state.json", { vexa_bot_id: "10", live_status: "running" });
  // A triage context without delivered chat messages is not a finished publication.
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "publication-reconcile-pending");
  write(".triage-slack.json", { status: "sent" });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "publication-reconcile-pending");
  write(".slack-general.json", { status: "skipped", reason: "private-delivery-policy" });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "publication-checkpoint");
  write(".slack-general.json", { status: "sent" });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "publication-checkpoint");
  const marker = claimWorkerLaunch(dir, "publication-worker", Date.now());
  assert.ok(marker);
  assert.strictEqual(claimWorkerLaunch(dir, "publication-worker", Date.now() + 1000), false);
  fs.utimesSync(marker, new Date(0), new Date(0));
  assert.ok(claimWorkerLaunch(dir, "publication-worker", Date.now()));
  assert.throws(() => inspectVexaFinish(root, "../event"));

  // A preserved capture defers publication until a finished transcript is promoted
  // out of it; after promotion the event publishes normally.
  fs.rmSync(path.join(dir, "triage-context.json"));
  for (const name of [".vexa-publication.json", "notion-page.json", ".dispatch-finish.json"]) {
    if (fs.existsSync(path.join(dir, name))) fs.rmSync(path.join(dir, name));
  }
  fs.mkdirSync(path.join(dir, "captures", "vexa-meeting-9"), { recursive: true });
  write("transcription.state.json", { vexa_bot_id: "10", live_status: "completed", batch_status: "completed",
    canonical_status: "completed", readability_status: "completed", finalization_ready: true });
  write(".vexa-signal-ready.json", { version: "vexa-signal-ready.v1", complete: true, meeting_id: "10",
    files: [{ name: "trace.captured-signal.jsonl" }] });
  write("vexa.lifecycle.json", { created_at: new Date().toISOString(), meeting: { id: 10, status: "completed" } });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "prior-capture-needs-review");
  write("transcript.capture-promotion.json", { version: "transcript.capture-promotion.v1", bot_id: "10",
    promoted_from_meeting_id: "9",
    files: [{ name: "transcript.canonical.jsonl", size: 10, sha256: "sha256:x" }] });
  assert.strictEqual(inspectVexaFinish(root, "event").reason, "publication-agent-needed");

  console.log("PASS Vexa terminal/postcall publication gate");
} finally { fs.rmSync(root, { recursive: true, force: true }); }

"use strict";

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const REJOIN_OVERRUN_GRACE_MS = 2 * 60 * 60 * 1000;

function readJson(file) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return null; }
}

function filterRejoinOverrunMeetings(events, nowMs = Date.now()) {
  return events.filter(event => {
    const endMs = Date.parse(event.end?.dateTime || event.end || "");
    return Number.isFinite(endMs) && endMs <= nowMs && nowMs < endMs + REJOIN_OVERRUN_GRACE_MS;
  });
}

function shouldStartVexaRejoin(meetingsDir, payload, nowMs = Date.now()) {
  const dir = path.join(meetingsDir, payload.id);
  const endMs = Date.parse(payload.end || "");
  if (!Number.isFinite(endMs) || nowMs >= endMs + REJOIN_OVERRUN_GRACE_MS) {
    return { start: false, dir, reason: "rejoin-window-ended" };
  }
  const receipt = readJson(path.join(dir, ".vexa-rejoin.json"));
  if ([".finalized", "triage-context.json", "notion-page.json", ".vexa-publication.json"].some(name => fs.existsSync(path.join(dir, name)))) {
    return { start: false, dir, reason: "publication-exists-needs-review" };
  }
  if (receipt?.status === "dispatch_pending") {
    if (nowMs >= endMs && receipt.source_completion_reason !== "evicted") return { start: false, dir, reason: "left-alone-after-end" };
    return { start: true, dir, reason: "dispatch-pending" };
  }
  if (["needs_review", "skipped"].includes(receipt?.status) || (receipt?.handled_meeting_ids?.length ?? 0) >= 1) {
    return { start: false, dir, reason: receipt?.status === "skipped" ? (receipt.reason || "rejoin-skipped") : "rejoin-bounded" };
  }
  const lifecycle = readJson(path.join(dir, "vexa.lifecycle.json"));
  const meeting = lifecycle?.meeting;
  const reason = meeting?.completion_reason || meeting?.data?.completion_reason;
  if (reason === "left_alone" && nowMs >= endMs) return { start: false, dir, reason: "left-alone-after-end" };
  if (meeting?.status !== "completed" || !["left_alone", "evicted"].includes(reason)) {
    return { start: false, dir, reason: "not-rejoin-eligible" };
  }
  if (readJson(path.join(dir, ".vexa-postcall.json"))?.status !== "completed") {
    return { start: false, dir, reason: "capture-not-preserved" };
  }
  return { start: true, dir, reason: reason === "evicted" ? "unexpected-eviction" : "early-left-alone" };
}

function startVexaRejoinWorker(dir) {
  const worker = path.join(__dirname, "vexa-rejoin-worker.py");
  const child = spawn("python3", [worker, "--meeting-dir", dir], {
    detached: true,
    stdio: "ignore",
    env: process.env,
  });
  child.unref();
  return child.pid;
}

module.exports = { REJOIN_OVERRUN_GRACE_MS, filterRejoinOverrunMeetings, shouldStartVexaRejoin, startVexaRejoinWorker };

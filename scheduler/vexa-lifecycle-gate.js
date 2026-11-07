"use strict";

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const { atomicWriteJson } = require("./dispatch-lease");

function startLifecycleWorker(home, phase, payload) {
  if (!["prep", "start"].includes(phase)) throw new Error(`unsupported lifecycle phase: ${phase}`);
  const eventId = String(payload?.event_id || payload?.id || "");
  if (!eventId || !payload?._dispatch?.delivery_id) throw new Error("invalid lifecycle dispatch payload");
  const eventDir = path.join(home, "workspace", "meetings", eventId);
  fs.mkdirSync(eventDir, { recursive: true, mode: 0o700 });
  fs.chmodSync(eventDir, 0o700);
  const jobPath = path.join(eventDir, `.vexa-${phase}-${payload._dispatch.delivery_id}.job.json`);
  atomicWriteJson(jobPath, payload);
  fs.chmodSync(jobPath, 0o600);
  const worker = path.join(__dirname, "vexa-lifecycle-worker.py");
  const child = spawn("python3", [worker, "--job", jobPath], {
    cwd: __dirname,
    env: { ...process.env, HERMES_HOME: home },
    detached: true,
    stdio: "ignore",
  });
  child.unref();
  return { pid: child.pid, jobPath };
}

module.exports = { startLifecycleWorker };

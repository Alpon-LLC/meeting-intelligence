"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn, spawnSync } = require("child_process");
const { claimPathFor, receiptPathFor } = require("./dispatch-lease");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "meeting-executor-receipt-test-"));
const claimRoot = path.join(root, "workspace", "meeting-trigger", "dispatch-claims");
const meetingsDir = path.join(root, "workspace", "meetings");
const claimPath = claimPathFor(claimRoot, "start", "event");
const receiptPath = receiptPathFor(meetingsDir, "event", "start");
const cli = path.join(__dirname, "executor-receipt.js");
const env = { ...process.env, HERMES_HOME: root };
const common = [
  "--claim-path", claimPath,
  "--receipt-path", receiptPath,
  "--key", "start:event",
  "--phase", "start",
];
fs.mkdirSync(path.dirname(claimPath), { recursive: true });
fs.writeFileSync(claimPath, JSON.stringify({
  key: "start:event",
  phase: "start",
  event_id: "event",
  attempt: 2,
  delivery_id: "delivery-2",
  status: "accepted",
}));

function run(command, extra = []) {
  return spawnSync(process.execPath, [cli, command, ...common, "--attempt", "2", "--delivery-id", "delivery-2", ...extra], { encoding: "utf8", env });
}

async function runConcurrent(commands) {
  return Promise.all(commands.map(([command, extra]) => new Promise(resolve => {
    const child = spawn(process.execPath, [cli, command, ...common, "--attempt", "2", "--delivery-id", "delivery-2", ...extra], { env });
    child.on("exit", code => resolve(code));
  })));
}

(async () => {
  try {
    const begin = run("begin");
    assert.strictEqual(begin.status, 0, begin.stderr);
    assert.strictEqual(JSON.parse(fs.readFileSync(receiptPath, "utf8")).status, "running");

    const stale = spawnSync(process.execPath, [cli, "finish", ...common, "--attempt", "1", "--delivery-id", "delivery-1", "--status", "succeeded", "--outcome", "old-attempt"], { encoding: "utf8", env });
    assert.strictEqual(stale.status, 3);
    assert.strictEqual(JSON.parse(fs.readFileSync(receiptPath, "utf8")).status, "running");

    const hostileReceipt = path.join(root, "attacker-receipt.json");
    const hostile = spawnSync(process.execPath, [cli, "finish", ...common.slice(0, 2), "--receipt-path", hostileReceipt, ...common.slice(4), "--attempt", "2", "--delivery-id", "delivery-2", "--status", "succeeded", "--outcome", "escape"], { encoding: "utf8", env });
    assert.strictEqual(hostile.status, 3);
    assert.strictEqual(fs.existsSync(hostileReceipt), false);

    const hostileClaimDir = path.join(root, "outside-claim-root");
    const hostileClaim = path.join(hostileClaimDir, "claim.json");
    const hostileClaimRun = spawnSync(process.execPath, [
      cli, "begin", ...common,
      "--claim-path", hostileClaim,
      "--attempt", "2", "--delivery-id", "delivery-2",
    ], { encoding: "utf8", env });
    assert.strictEqual(hostileClaimRun.status, 3);
    assert.strictEqual(fs.existsSync(`${hostileClaim}.mutex`), false, "invalid claim paths must not create mutexes");
    assert.strictEqual(fs.existsSync(hostileClaimDir), false, "invalid claim paths must not create parent directories");

    fs.writeFileSync(claimPath, JSON.stringify({
      key: "start:event", phase: "start", event_id: "event", attempt: 2,
      delivery_id: "delivery-2", status: "exhausted",
    }));
    const exhausted = run("heartbeat");
    assert.strictEqual(exhausted.status, 3);

    fs.writeFileSync(claimPath, JSON.stringify({
      key: "start:event", phase: "start", event_id: "event", attempt: 2,
      delivery_id: "delivery-2", status: "accepted",
    }));
    const finish = run("finish", ["--status", "succeeded", "--outcome", "vexa-active"]);
    assert.strictEqual(finish.status, 0, finish.stderr);
    let receipt = JSON.parse(fs.readFileSync(receiptPath, "utf8"));
    assert.strictEqual(receipt.status, "succeeded");
    assert.strictEqual(receipt.outcome, "vexa-active");
    assert.strictEqual(receipt.delivery_id, "delivery-2");

    const lateBegin = run("begin");
    assert.strictEqual(lateBegin.status, 3, "begin must not overwrite a terminal receipt");
    receipt = JSON.parse(fs.readFileSync(receiptPath, "utf8"));
    assert.strictEqual(receipt.status, "succeeded");

    fs.rmSync(receiptPath, { force: true });
    const codes = await runConcurrent([
      ["finish", ["--status", "succeeded", "--outcome", "race-finished"]],
      ["begin", []],
    ]);
    assert(codes.includes(0));
    receipt = JSON.parse(fs.readFileSync(receiptPath, "utf8"));
    assert.strictEqual(receipt.status, "succeeded", "concurrent begin must not regress a terminal receipt");

    console.log("executor receipt: PASS");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
})().catch(error => {
  fs.rmSync(root, { recursive: true, force: true });
  throw error;
});

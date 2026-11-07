"use strict";

const fs = require("fs");
const path = require("path");
const { claimPathFor, receiptPathFor, withMutex, writeExecutorReceipt } = require("./dispatch-lease");

const HERMES_HOME = path.resolve(process.env.HERMES_HOME || require("path").join(require("os").homedir(), ".hermes"));
const CLAIM_ROOT = path.join(HERMES_HOME, "workspace", "meeting-trigger", "dispatch-claims");
const MEETINGS_DIR = path.join(HERMES_HOME, "workspace", "meetings");

function samePath(left, right) {
  return path.resolve(left) === path.resolve(right);
}

function isWithin(filePath, rootPath) {
  const resolved = path.resolve(filePath);
  const root = path.resolve(rootPath);
  return resolved.startsWith(`${root}${path.sep}`);
}

function parseArgs(argv) {
  const [command, ...rest] = argv;
  const values = { command };
  for (let index = 0; index < rest.length; index += 2) {
    const key = rest[index]?.replace(/^--/, "").replace(/-/g, "_");
    if (!key || rest[index + 1] === undefined) throw new Error(`missing value for ${rest[index]}`);
    values[key] = rest[index + 1];
  }
  return values;
}

function fail(message, code = 2) {
  process.stderr.write(`${message}\n`);
  process.exit(code);
}

const args = parseArgs(process.argv.slice(2));
if (!args.command || !["begin", "heartbeat", "finish"].includes(args.command)) fail("usage: executor-receipt.js <begin|heartbeat|finish> ...");
for (const key of ["claim_path", "receipt_path", "key", "phase", "attempt", "delivery_id"]) {
  if (!args[key]) fail(`missing --${key.replace(/_/g, "-")}`);
}
if (!['prep', 'start', 'finish'].includes(args.phase)) fail(`invalid phase: ${args.phase}`, 3);
if (!isWithin(args.claim_path, path.join(CLAIM_ROOT, args.phase)) || !isWithin(args.receipt_path, MEETINGS_DIR)) {
  fail("dispatch paths outside canonical roots", 3);
}
const attempt = Number(args.attempt);
const status = args.command === "finish" ? args.status : "running";
const outcome = args.command === "finish" ? args.outcome : `executor-${args.command}`;
if (!status || !outcome) fail("finish requires --status and --outcome");
if (!["running", "succeeded", "skipped", "failed"].includes(status)) fail(`invalid status: ${status}`);

let result = null;
const lockDeadline = Date.now() + 5_000;
do {
  result = withMutex(args.claim_path, () => {
  const claim = (() => {
    try { return JSON.parse(fs.readFileSync(args.claim_path, "utf8")); } catch { return null; }
  })();
  const eventId = claim?.event_id;
  let expectedClaimPath = null;
  let expectedReceiptPath = null;
  try {
    expectedClaimPath = claim && claimPathFor(CLAIM_ROOT, args.phase, eventId);
    expectedReceiptPath = claim && receiptPathFor(MEETINGS_DIR, eventId, args.phase);
  } catch {}
  if (
    !claim ||
    claim.key !== args.key ||
    claim.key !== `${args.phase}:${eventId}` ||
    claim.phase !== args.phase ||
    Number(claim.attempt) !== attempt ||
    claim.delivery_id !== args.delivery_id ||
    !expectedClaimPath ||
    !expectedReceiptPath ||
    !samePath(args.claim_path, expectedClaimPath) ||
    !samePath(args.receipt_path, expectedReceiptPath) ||
    ["completed", "exhausted"].includes(claim.status)
  ) return { fenced: true };

  const existing = (() => {
    try { return JSON.parse(fs.readFileSync(args.receipt_path, "utf8")); } catch { return null; }
  })();
  const sameGeneration = existing &&
    existing.key === args.key && existing.phase === args.phase &&
    Number(existing.attempt) === attempt && existing.delivery_id === args.delivery_id;
  if (sameGeneration && ["succeeded", "skipped", "failed"].includes(existing.status)) {
    return { fenced: true, existing };
  }

  writeExecutorReceipt({
    receiptPath: args.receipt_path,
    dispatch: { key: args.key, phase: args.phase, attempt, delivery_id: args.delivery_id },
    status,
    outcome,
    retryable: args.retryable === "true",
  });
    return { written: true };
  });
  if (result?.reason === "claim-busy" && Date.now() < lockDeadline) {
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);
  } else {
    break;
  }
} while (Date.now() < lockDeadline);

if (!result?.written) {
  process.stdout.write(`${JSON.stringify({ status: "fenced", attempt, delivery_id: args.delivery_id })}\n`);
  process.exit(3);
}
process.stdout.write(`${JSON.stringify({ status, outcome, attempt, delivery_id: args.delivery_id })}\n`);

"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const DEFAULT_POLICY = Object.freeze({
  leaseMs: 6 * 60 * 1000,
  hardTimeoutMs: 18 * 60 * 1000,
  overallTimeoutMs: 40 * 60 * 1000,
  maxAttempts: 3,
  retryBackoffMs: 2 * 60 * 1000,
});


function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return null;
  }
}

function atomicWriteJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const tempPath = `${filePath}.${process.pid}.${crypto.randomBytes(4).toString("hex")}.tmp`;
  const fd = fs.openSync(tempPath, "wx", 0o600);
  try {
    fs.writeFileSync(fd, `${JSON.stringify(value, null, 2)}\n`);
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  fs.renameSync(tempPath, filePath);
}

function safeEventId(eventId) {
  const readable = String(eventId).replace(/[^A-Za-z0-9._-]/gu, "_").slice(0, 120);
  const hash = crypto.createHash("sha256").update(String(eventId)).digest("hex").slice(0, 12);
  return `${readable || "event"}-${hash}`;
}

function claimPathFor(claimRoot, phase, eventId) {
  return path.join(claimRoot, phase, `${safeEventId(eventId)}.json`);
}

function receiptPathFor(meetingsDir, eventId, phase) {
  const eventDir = path.resolve(meetingsDir, String(eventId));
  const meetingsRoot = path.resolve(meetingsDir);
  if (eventDir === meetingsRoot || !eventDir.startsWith(`${meetingsRoot}${path.sep}`)) {
    throw new Error("event id escapes meetings root");
  }
  return path.join(eventDir, `.dispatch-${phase}.json`);
}

function deliveryIdFor(phase, eventId, attempt) {
  const hash = crypto.createHash("sha256")
    .update(`${phase}:${eventId}:${attempt}`)
    .digest("hex")
    .slice(0, 24);
  return `meeting-${phase}-${hash}`;
}

function acquireMutex(claimPath) {
  const mutexPath = `${claimPath}.mutex`;
  fs.mkdirSync(path.dirname(claimPath), { recursive: true });
  let fd;
  try {
    fd = fs.openSync(
      mutexPath,
      fs.constants.O_RDWR | fs.constants.O_CREAT | fs.constants.O_NOFOLLOW,
      0o600,
    );
  } catch (error) {
    if (error.code === "EISDIR") return { acquired: false, mutexPath, fd: null };
    throw error;
  }

  const result = spawnSync("/usr/bin/flock", ["-n", "3"], {
    stdio: ["ignore", "pipe", "pipe", fd],
    encoding: "utf8",
  });
  if (result.error) {
    fs.closeSync(fd);
    throw result.error;
  }
  if (result.status === 0) return { acquired: true, mutexPath, fd };
  fs.closeSync(fd);
  if (result.status === 1) return { acquired: false, mutexPath, fd: null };
  throw new Error(`flock failed (${result.status}): ${(result.stderr || "").trim()}`);
}

function releaseMutex(lock) {
  if (!lock?.acquired) return;
  fs.closeSync(lock.fd);
}

function withMutex(claimPath, fn) {
  const lock = acquireMutex(claimPath);
  if (!lock.acquired) return { action: "skip", reason: "claim-busy", claimPath };
  try {
    return fn();
  } finally {
    releaseMutex(lock);
  }
}

function historyEntry(status, atMs, extra = {}) {
  return { status, at: new Date(atMs).toISOString(), ...extra };
}

function terminalFromReceipt(receipt, claim) {
  if (!claim || !receipt || !["succeeded", "skipped", "failed"].includes(receipt.status)) return null;
  if (receipt.key !== claim.key || receipt.phase !== claim.phase) return null;
  if (Number(receipt.attempt) !== Number(claim.attempt)) return null;
  if (receipt.delivery_id !== claim.delivery_id) return null;
  return {
    status: receipt.status,
    outcome: receipt.outcome || receipt.reason || `executor-${receipt.status}`,
    retryable: receipt.retryable === true,
  };
}

function newestMtime(paths) {
  let newest = 0;
  for (const filePath of paths) {
    try {
      newest = Math.max(newest, fs.statSync(filePath).mtimeMs);
    } catch {}
  }
  return newest || null;
}

function isPidAlive(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function findLastEventLogLine(logPath, eventId) {
  try {
    const marker = `event_id=${eventId}`;
    const lines = fs.readFileSync(logPath, "utf8").split(/\r?\n/);
    for (let index = lines.length - 1; index >= 0; index -= 1) {
      if (lines[index].includes(marker)) return lines[index];
    }
  } catch {}
  return null;
}

function inspectStartEvidence({ eventId, meetingsDir, activeLogPath, nowMs, leaseMs }) {
  const eventDir = path.join(meetingsDir, eventId);
  const activePath = path.join(meetingsDir, "active", `${eventId}.lock`);
  const joiningPath = path.join(eventDir, ".joining");
  const statusPath = path.join(eventDir, "status.json");
  const metaPath = path.join(eventDir, "event_meta.json");
  const vexaResponsePath = path.join(eventDir, "vexa.bot.response.json");
  const active = readJson(activePath);
  const joining = readJson(joiningPath);
  const status = readJson(statusPath);
  const progressMtimeMs = newestMtime([activePath, joiningPath, statusPath, metaPath, vexaResponsePath]);
  const noJoin = require("./vexa-no-join").inspectVexaNoJoin(eventDir);
  if (noJoin?.noJoin) return { terminal: { status: "skipped", outcome: noJoin.reason }, progressMtimeMs, reason: noJoin.reason };

  if (active && ["active", "admitted", "joined", "covered-by-existing-bot"].includes(active.status)) {
    return { terminal: { status: "succeeded", outcome: active.backend === "vexa-primary" ? "vexa-active" : "bot-active" }, progressMtimeMs, reason: "active lock" };
  }
  if (fs.existsSync(path.join(eventDir, ".cancelled"))) {
    return { terminal: { status: "skipped", outcome: "cancelled" }, progressMtimeMs, reason: "cancelled marker" };
  }
  if (fs.existsSync(path.join(eventDir, ".no-join"))) {
    return { terminal: { status: "skipped", outcome: "meeting-room-not-active" }, progressMtimeMs, reason: "no-join marker" };
  }
  if (fs.existsSync(path.join(eventDir, "AUTH_ALERT.txt"))) {
    return { terminal: { status: "skipped", outcome: "auth-blocked" }, progressMtimeMs, reason: "auth alert" };
  }
  if (joining?.status === "admitted") {
    return { terminal: { status: "succeeded", outcome: "bot-admitted" }, progressMtimeMs, reason: "joining admitted" };
  }
  if (status && (status.inCall === true || status.in_call === true || status.joinedAt || status.joined_at)) {
    return { terminal: { status: "succeeded", outcome: status.source === "vexa_authenticated" ? "vexa-admitted" : "bot-admitted" }, progressMtimeMs, reason: "status joined" };
  }
  if (joining?.status === "exited") {
    const terminalReasons = new Set([
      "auth-failed-sign-in-redirect", "auth-wrong-account", "host-denied", "host-denied-or-room-inactive",
      "no-join-buttons", "knockout",
    ]);
    if (terminalReasons.has(joining.exit_reason)) {
      return { terminal: { status: "skipped", outcome: joining.exit_reason }, progressMtimeMs, reason: "terminal joining exit" };
    }
  }
  if (joining?.status === "booting") {
    const ageMs = progressMtimeMs == null ? Infinity : nowMs - progressMtimeMs;
    const remote = String(joining.backend || "").startsWith("vexa");
    if (ageMs < leaseMs && (remote || isPidAlive(joining.pid))) {
      return { terminal: null, running: true, progressMtimeMs, reason: remote ? "fresh-vexa-boot" : "live-local-boot" };
    }
  }

  const logLine = findLastEventLogLine(activeLogPath, eventId);
  if (logLine) {
    if (logLine.includes("meeting-auto-join joined") ||
        /skip-reason=(duplicate-delivery|bot-already-joining)/.test(logLine) && /(?:active|handled|covered|joining)/.test(logLine)) {
      return { terminal: { status: "succeeded", outcome: "join-log-covered" }, progressMtimeMs, reason: "terminal join log" };
    }
    const match = logLine.match(/skip-reason=([^\s]+)/);
    const transient = new Set(["cross-meeting-profile-contention", "lobby-timeout-early-join", "profile-locked"]);
    if (match && !transient.has(match[1])) {
      return { terminal: { status: "skipped", outcome: match[1] }, progressMtimeMs, reason: "terminal skip log" };
    }
  }

  if (progressMtimeMs != null && nowMs - progressMtimeMs < leaseMs) {
    return { terminal: null, running: true, progressMtimeMs, reason: "fresh start artifact" };
  }
  return { terminal: null, running: false, progressMtimeMs, reason: "no fresh start evidence" };
}

function inspectPrepEvidence({ eventId, meetingsDir, prepMeetingsDir, nowMs, leaseMs }) {
  const eventDir = path.join(prepMeetingsDir || meetingsDir, eventId);
  const lockPath = path.join(eventDir, ".lock");
  const summaryPath = path.join(eventDir, "summary.json");
  const lock = readJson(lockPath);
  const summary = readJson(summaryPath);
  const progressMtimeMs = newestMtime([lockPath, summaryPath]);
  if (lock && (lock.status === "done" || !lock.status) || summary?.status === "done") {
    return { terminal: { status: "succeeded", outcome: "prep-done" }, progressMtimeMs, reason: "prep done" };
  }
  if (lock?.status === "prepping" && progressMtimeMs != null && nowMs - progressMtimeMs < leaseMs) {
    return { terminal: null, running: true, progressMtimeMs, reason: "prep in progress" };
  }
  return { terminal: null, running: false, progressMtimeMs, reason: "no fresh prep evidence" };
}

function inspectFinishEvidence({ eventId, meetingsDir, nowMs, leaseMs }) {
  const eventDir = path.join(meetingsDir, eventId);
  const noJoin = require("./vexa-no-join").inspectVexaNoJoin(eventDir);
  if (noJoin?.noJoin) return { terminal: { status: "skipped", outcome: noJoin.reason }, progressMtimeMs: null, reason: noJoin.reason };
  const finalizedPath = path.join(eventDir, ".finalized");
  const triagePath = path.join(eventDir, "triage-context.json");
  const reviewPath = path.join(eventDir, ".review-flag.txt");
  const statusPath = path.join(eventDir, "status.json");
  const finalizingPath = path.join(eventDir, ".finalizing");
  const progressMtimeMs = newestMtime([finalizedPath, triagePath, reviewPath, statusPath, finalizingPath]);
  if (fs.existsSync(finalizedPath)) return { terminal: { status: "succeeded", outcome: "finalized" }, progressMtimeMs, reason: "finalized marker" };
  if (fs.existsSync(triagePath)) return { terminal: { status: "succeeded", outcome: "awaiting-triage" }, progressMtimeMs, reason: "triage context" };
  if (fs.existsSync(reviewPath)) {
    const hasNewerCapture = fs.existsSync(statusPath) && fs.statSync(statusPath).mtimeMs > fs.statSync(reviewPath).mtimeMs;
    if (!hasNewerCapture) return { terminal: { status: "skipped", outcome: "human-review" }, progressMtimeMs, reason: "review flag" };
  }
  if (fs.existsSync(finalizingPath) && progressMtimeMs != null && nowMs - progressMtimeMs < leaseMs) {
    return { terminal: null, running: true, progressMtimeMs, reason: "finalization in progress" };
  }
  return { terminal: null, running: false, progressMtimeMs, reason: "no fresh finish evidence" };
}

function inspectPhaseEvidence({ phase, eventId, meetingsDir, prepMeetingsDir, claim, receiptPath, activeLogPath, nowMs = Date.now(), leaseMs = DEFAULT_POLICY.leaseMs }) {
  const receipt = readJson(receiptPath || receiptPathFor(meetingsDir, eventId, phase));
  const receiptTerminal = terminalFromReceipt(receipt, claim);
  if (receiptTerminal) {
    const retryableFailure = receiptTerminal.status === "failed" && receiptTerminal.retryable;
    return {
      terminal: retryableFailure ? null : receiptTerminal,
      retryableFailure,
      failure: retryableFailure ? receiptTerminal : null,
      generation: {
        key: receipt.key,
        phase: receipt.phase,
        attempt: Number(receipt.attempt),
        delivery_id: receipt.delivery_id,
      },
      progressMtimeMs: newestMtime([receiptPath]),
      reason: retryableFailure ? "executor retryable failure" : "executor receipt",
    };
  }
  const runningReceiptMatches = claim && (
    receipt?.key === claim.key &&
    receipt?.phase === claim.phase &&
    Number(receipt?.attempt) === Number(claim.attempt) &&
    receipt?.delivery_id === claim.delivery_id
  );
  if (receipt?.status === "running" && runningReceiptMatches) {
    const progressMtimeMs = newestMtime([receiptPath]);
    if (progressMtimeMs != null && nowMs - progressMtimeMs < leaseMs) {
      const ingressOnly = receipt.outcome === "ingress-accepted";
      return {
        terminal: null,
        running: true,
        extendLease: !ingressOnly,
        generation: {
          key: receipt.key,
          phase: receipt.phase,
          attempt: Number(receipt.attempt),
          delivery_id: receipt.delivery_id,
        },
        progressMtimeMs,
        reason: ingressOnly ? "ingress accepted" : "executor heartbeat",
      };
    }
  }
  const args = { eventId, meetingsDir, prepMeetingsDir, activeLogPath, nowMs, leaseMs };
  if (phase === "start") return inspectStartEvidence(args);
  if (phase === "prep") return inspectPrepEvidence(args);
  if (phase === "finish") return inspectFinishEvidence(args);
  return { terminal: null, running: false, progressMtimeMs: null, reason: "unknown phase" };
}

function createClaim({ phase, eventId, payload, nowMs, policy, attempt = 1, previous = null }) {
  const deliveryId = deliveryIdFor(phase, eventId, attempt);
  const firstClaimedAtMs = previous?.first_claimed_at_ms ?? nowMs;
  const overallExpiresAtMs = previous?.overall_expires_at_ms ?? firstClaimedAtMs + policy.overallTimeoutMs;
  const hardExpiresAtMs = Math.min(nowMs + policy.hardTimeoutMs, overallExpiresAtMs);
  return {
    version: 1,
    key: `${phase}:${eventId}`,
    phase,
    event_id: eventId,
    title: payload?.title || null,
    payload: previous?.payload || payload,
    status: "pending",
    attempt,
    delivery_id: deliveryId,
    first_claimed_at_ms: firstClaimedAtMs,
    first_claimed_at: new Date(firstClaimedAtMs).toISOString(),
    claimed_at_ms: nowMs,
    claimed_at: new Date(nowMs).toISOString(),
    lease_expires_at_ms: Math.min(nowMs + policy.leaseMs, hardExpiresAtMs),
    lease_expires_at: new Date(Math.min(nowMs + policy.leaseMs, hardExpiresAtMs)).toISOString(),
    hard_expires_at_ms: hardExpiresAtMs,
    hard_expires_at: new Date(hardExpiresAtMs).toISOString(),
    overall_expires_at_ms: overallExpiresAtMs,
    overall_expires_at: new Date(overallExpiresAtMs).toISOString(),
    last_progress_at_ms: previous?.last_progress_at_ms || null,
    next_retry_at_ms: null,
    history: [...(previous?.history || []), historyEntry("pending", nowMs, { attempt, delivery_id: deliveryId })].slice(-20),
  };
}

function acquireDispatch({ claimRoot, phase, eventId, payload, evidence = null, nowMs = Date.now(), policy = DEFAULT_POLICY }) {
  const resolvedPolicy = { ...DEFAULT_POLICY, ...policy };
  const claimPath = claimPathFor(claimRoot, phase, eventId);
  return withMutex(claimPath, () => {
    let claim = readJson(claimPath);
    if (!claim && fs.existsSync(claimPath)) {
      throw new Error(`malformed claim: ${claimPath}`);
    }
    if (evidence?.generation) {
      const generation = evidence.generation;
      const matchesCurrentClaim = claim &&
        generation.key === claim.key &&
        generation.phase === claim.phase &&
        Number(generation.attempt) === Number(claim.attempt) &&
        generation.delivery_id === claim.delivery_id;
      if (!matchesCurrentClaim) evidence = null;
    }
    if (!claim && evidence?.running) {
      claim = createClaim({ phase, eventId, payload, nowMs, policy: resolvedPolicy });
      claim.status = "accepted";
      claim.last_progress_at_ms = Number(evidence.progressMtimeMs || nowMs);
      claim.last_progress_at = new Date(claim.last_progress_at_ms).toISOString();
      claim.history = [...(claim.history || []), historyEntry("progress", nowMs, { reason: evidence.reason, adopted: true })].slice(-20);
      atomicWriteJson(claimPath, claim);
      return { action: "skip", reason: "workflow-running", claim, claimPath };
    }
    if (evidence?.terminal) {
      if (!claim) claim = createClaim({ phase, eventId, payload, nowMs, policy: resolvedPolicy });
      claim.status = "completed";
      claim.outcome = evidence.terminal.outcome;
      claim.terminal_status = evidence.terminal.status;
      claim.completed_at = new Date(nowMs).toISOString();
      claim.history = [...(claim.history || []), historyEntry("completed", nowMs, { outcome: claim.outcome })].slice(-20);
      atomicWriteJson(claimPath, claim);
      return { action: "skip", reason: "workflow-terminal", claim, claimPath };
    }

    if (!claim) {
      claim = createClaim({ phase, eventId, payload, nowMs, policy: resolvedPolicy });
      atomicWriteJson(claimPath, claim);
      return { action: "dispatch", reason: "claim-created", claim, claimPath, deliveryId: claim.delivery_id };
    }
    if (evidence?.retryableFailure && claim.status !== "failed") {
      claim.status = "failed";
      claim.last_error = evidence.failure?.outcome || "executor retryable failure";
      claim.next_retry_at_ms = nowMs + resolvedPolicy.retryBackoffMs;
      claim.next_retry_at = new Date(claim.next_retry_at_ms).toISOString();
      claim.history = [...(claim.history || []), historyEntry("failed", nowMs, { attempt: claim.attempt, error: claim.last_error })].slice(-20);
      atomicWriteJson(claimPath, claim);
    }
    if (["completed", "exhausted"].includes(claim.status)) {
      const exhaustedReason = claim.history?.slice().reverse().find(entry => entry.status === "exhausted")?.reason;
      return {
        action: "skip",
        reason: claim.status === "completed" ? "workflow-terminal" : exhaustedReason || "attempts-exhausted",
        claim,
        claimPath,
      };
    }

    if (evidence?.running) {
      const progressMs = Number(evidence.progressMtimeMs || 0);
      if (progressMs > Number(claim.last_progress_at_ms || 0)) {
        claim.last_progress_at_ms = progressMs;
        claim.last_progress_at = new Date(progressMs).toISOString();
        if (evidence.extendLease !== false) {
          claim.lease_expires_at_ms = Math.min(nowMs + resolvedPolicy.leaseMs, claim.hard_expires_at_ms);
          claim.lease_expires_at = new Date(claim.lease_expires_at_ms).toISOString();
        }
        claim.history = [...(claim.history || []), historyEntry("progress", nowMs, { reason: evidence.reason })].slice(-20);
        atomicWriteJson(claimPath, claim);
      }
      if (nowMs < claim.hard_expires_at_ms && nowMs < claim.lease_expires_at_ms) {
        return { action: "skip", reason: "workflow-running", claim, claimPath };
      }
    }

    const hardTimedOut = nowMs >= claim.hard_expires_at_ms;
    const overallTimedOut = nowMs >= Number(claim.overall_expires_at_ms || claim.hard_expires_at_ms);
    const attemptReadyAtMs = claim.status === "failed"
      ? Number(claim.next_retry_at_ms || claim.lease_expires_at_ms)
      : Number(claim.lease_expires_at_ms);
    const attemptsExhausted = claim.attempt >= resolvedPolicy.maxAttempts && nowMs >= attemptReadyAtMs;
    if (overallTimedOut || attemptsExhausted) {
      claim.status = "exhausted";
      claim.exhausted_at = new Date(nowMs).toISOString();
      claim.history = [...(claim.history || []), historyEntry("exhausted", nowMs, { attempt: claim.attempt, reason: overallTimedOut ? "hard-timeout" : "attempts-exhausted" })].slice(-20);
      atomicWriteJson(claimPath, claim);
      return { action: "skip", reason: overallTimedOut ? "hard-timeout" : "attempts-exhausted", claim, claimPath };
    }
    if (hardTimedOut) {
      claim = createClaim({ phase, eventId, payload, nowMs, policy: resolvedPolicy, attempt: claim.attempt + 1, previous: claim });
      atomicWriteJson(claimPath, claim);
      return { action: "dispatch", reason: "attempt-hard-timeout", claim, claimPath, deliveryId: claim.delivery_id };
    }
    if (claim.status === "failed" && nowMs < Number(claim.next_retry_at_ms || 0)) {
      return { action: "skip", reason: "retry-backoff", claim, claimPath };
    }
    if (["pending", "accepted"].includes(claim.status) && nowMs < claim.lease_expires_at_ms) {
      return { action: "skip", reason: "lease-active", claim, claimPath };
    }

    claim = createClaim({ phase, eventId, payload, nowMs, policy: resolvedPolicy, attempt: claim.attempt + 1, previous: claim });
    atomicWriteJson(claimPath, claim);
    return { action: "dispatch", reason: "lease-expired", claim, claimPath, deliveryId: claim.delivery_id };
  });
}

function listOutstandingClaims(claimRoot) {
  const claims = [];
  for (const phase of ["prep", "start", "finish"]) {
    const phaseDir = path.join(claimRoot, phase);
    let names = [];
    try {
      names = fs.readdirSync(phaseDir);
    } catch {
      continue;
    }
    for (const name of names) {
      if (!name.endsWith(".json")) continue;
      const claimPath = path.join(phaseDir, name);
      const claim = readJson(claimPath);
      if (!claim) throw new Error(`malformed claim: ${claimPath}`);
      if (claim.status === "completed") continue;
      if (claim.status === "exhausted" && claim.alert_sent_at) continue;
      claims.push({ ...claim, claimPath });
    }
  }
  return claims;
}

function mutateClaim(claimPath, attempt, mutate) {
  return withMutex(claimPath, () => {
    const claim = readJson(claimPath);
    if (!claim || Number(claim.attempt) !== Number(attempt) || ["completed", "exhausted"].includes(claim.status)) return false;
    mutate(claim);
    atomicWriteJson(claimPath, claim);
    return true;
  }) === true;
}

function markDispatchAccepted(claimPath, attempt, nowMs = Date.now(), response = {}) {
  return mutateClaim(claimPath, attempt, claim => {
    claim.status = "accepted";
    claim.accepted_at = new Date(nowMs).toISOString();
    claim.last_http_status = response.status ?? null;
    claim.gateway_delivery_id = response.body?.delivery_id || claim.delivery_id;
    claim.history = [...(claim.history || []), historyEntry("accepted", nowMs, { attempt, http_status: claim.last_http_status })].slice(-20);
  });
}

function markDispatchFailed(claimPath, attempt, nowMs = Date.now(), error = "delivery failed", nextRetryAtMs = null) {
  return mutateClaim(claimPath, attempt, claim => {
    claim.status = "failed";
    claim.failed_at = new Date(nowMs).toISOString();
    claim.last_error = String(error).slice(0, 500);
    claim.next_retry_at_ms = nextRetryAtMs ?? nowMs + DEFAULT_POLICY.retryBackoffMs;
    claim.next_retry_at = new Date(claim.next_retry_at_ms).toISOString();
    claim.history = [...(claim.history || []), historyEntry("failed", nowMs, { attempt, error: claim.last_error })].slice(-20);
  });
}

function claimDispatchAlert(claimPath, attempt, nowMs = Date.now()) {
  const result = withMutex(claimPath, () => {
    const claim = readJson(claimPath);
    if (!claim || Number(claim.attempt) !== Number(attempt) || !["completed", "exhausted"].includes(claim.status) || claim.alert_sent_at) {
      return null;
    }
    const claimedAtMs = claim.alert_claimed_at ? Date.parse(claim.alert_claimed_at) : 0;
    if (claimedAtMs && nowMs - claimedAtMs < DEFAULT_POLICY.leaseMs) return null;
    const token = crypto.randomBytes(16).toString("hex");
    claim.alert_claimed_at = new Date(nowMs).toISOString();
    claim.alert_claim_token = token;
    claim.history = [...(claim.history || []), historyEntry("alert-claimed", nowMs, { attempt, token })].slice(-20);
    atomicWriteJson(claimPath, claim);
    return token;
  });
  return typeof result === "string" ? result : null;
}

function markDispatchAlertResult(claimPath, attempt, token, nowMs = Date.now(), sent = false, error = null) {
  return withMutex(claimPath, () => {
    const claim = readJson(claimPath);
    if (
      !claim ||
      Number(claim.attempt) !== Number(attempt) ||
      !claim.alert_claimed_at ||
      !token ||
      claim.alert_claim_token !== token
    ) return false;
    claim.alert_sent_at = sent ? new Date(nowMs).toISOString() : null;
    claim.alert_error = error ? String(error).slice(0, 500) : null;
    claim.alert_claim_token = null;
    claim.history = [...(claim.history || []), historyEntry(sent ? "alert-sent" : "alert-failed", nowMs, { attempt })].slice(-20);
    atomicWriteJson(claimPath, claim);
    return true;
  }) === true;
}

function buildDispatchPayload(payload, decision, meetingsDir) {
  return {
    ...payload,
    _dispatch: {
      key: decision.claim.key,
      phase: decision.claim.phase,
      attempt: decision.claim.attempt,
      delivery_id: decision.claim.delivery_id,
      lease_expires_at: decision.claim.lease_expires_at,
      hard_expires_at: decision.claim.hard_expires_at,
      claim_path: decision.claimPath,
      receipt_path: receiptPathFor(meetingsDir, decision.claim.event_id, decision.claim.phase),
    },
  };
}

function writeExecutorReceipt({ receiptPath, dispatch, status, outcome, retryable = false, nowMs = Date.now(), details = null }) {
  if (!dispatch || !dispatch.key || !dispatch.phase || !Number.isInteger(Number(dispatch.attempt)) || !dispatch.delivery_id) {
    return false;
  }
  const receipt = {
    version: 1,
    key: dispatch.key,
    phase: dispatch.phase,
    attempt: Number(dispatch.attempt),
    delivery_id: dispatch.delivery_id,
    status,
    outcome,
    retryable: retryable === true,
    updated_at: new Date(nowMs).toISOString(),
  };
  if (details) receipt.details = details;
  atomicWriteJson(receiptPath, receipt);
  return true;
}

module.exports = {
  DEFAULT_POLICY,
  acquireDispatch,
  atomicWriteJson,
  buildDispatchPayload,
  claimDispatchAlert,
  claimPathFor,
  deliveryIdFor,
  inspectPhaseEvidence,
  listOutstandingClaims,
  markDispatchAccepted,
  markDispatchAlertResult,
  markDispatchFailed,
  readJson,
  receiptPathFor,
  withMutex,
  writeExecutorReceipt,
};

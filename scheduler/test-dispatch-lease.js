"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const {
  acquireDispatch,
  claimDispatchAlert,
  inspectPhaseEvidence,
  listOutstandingClaims,
  markDispatchAccepted,
  markDispatchAlertResult,
  markDispatchFailed,
  withMutex,
  writeExecutorReceipt,
} = require("./dispatch-lease");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "meeting-dispatch-test-"));
const claimRoot = path.join(root, "claims");
const meetingsDir = path.join(root, "meetings");
const eventId = "event_20261001T070000Z";
const payload = { id: eventId, title: "Test meeting", description: "full calendar context" };
const policy = { leaseMs: 60_000, hardTimeoutMs: 1_000_000, maxAttempts: 3, retryBackoffMs: 20_000 };

try {
  const mutexClaimPath = path.join(claimRoot, "start", "mutex-contract.json");
  let mutexObserved = false;
  const mutexResult = withMutex(mutexClaimPath, () => {
    const mutexPath = `${mutexClaimPath}.mutex`;
    const stat = fs.statSync(mutexPath);
    assert(stat.isFile(), "the mutex must use one persistent lock file");
    assert.strictEqual(
      withMutex(mutexClaimPath, () => "overlap").reason,
      "claim-busy",
      "a contender must not enter while the kernel lock is held",
    );
    mutexObserved = true;
    return true;
  });
  assert.strictEqual(mutexResult, true);
  assert.strictEqual(mutexObserved, true);
  assert.strictEqual(fs.existsSync(`${mutexClaimPath}.mutex`), true,
    "the lock file persists; kernel ownership, not file presence, is authoritative");
  assert.strictEqual(withMutex(mutexClaimPath, () => true), true,
    "closing the owner fd must release the lock immediately");

  assert.throws(
    () => require("./dispatch-lease").receiptPathFor(meetingsDir, "../outside", "start"),
    /escapes meetings root/,
  );

  const adoptedEvent = "already-running";
  const adopted = acquireDispatch({
    claimRoot, phase: "start", eventId: adoptedEvent, payload: { id: adoptedEvent }, nowMs: 10, policy,
    evidence: { progressMtimeMs: 9, running: true, reason: "fresh-vexa-boot" },
  });
  assert.strictEqual(adopted.action, "skip");
  assert.strictEqual(adopted.reason, "workflow-running");
  assert.strictEqual(adopted.claim.status, "accepted");

  const first = acquireDispatch({ claimRoot, phase: "start", eventId, payload, nowMs: 0, policy });
  assert.strictEqual(first.action, "dispatch");
  assert.strictEqual(first.claim.attempt, 1);

  const ingressOnlyEvent = "ingress-only";
  const ingressOnly = acquireDispatch({ claimRoot, phase: "start", eventId: ingressOnlyEvent, payload: { id: ingressOnlyEvent }, nowMs: 0, policy });
  const ingressReceiptPath = path.join(meetingsDir, ingressOnlyEvent, ".dispatch-start.json");
  writeExecutorReceipt({
    receiptPath: ingressReceiptPath,
    dispatch: { key: ingressOnly.claim.key, phase: "start", attempt: 1, delivery_id: ingressOnly.deliveryId },
    status: "running", outcome: "ingress-accepted", nowMs: 1,
  });
  const ingressEvidence = inspectPhaseEvidence({
    phase: "start", eventId: ingressOnlyEvent, meetingsDir, claim: ingressOnly.claim,
    receiptPath: ingressReceiptPath, activeLogPath: path.join(root, "none.log"), nowMs: 20_000, leaseMs: 60_000,
  });
  assert.strictEqual(ingressEvidence.extendLease, false, "HTTP ingress receipt must not extend the executor lease");
  const ingressObserved = acquireDispatch({
    claimRoot, phase: "start", eventId: ingressOnlyEvent, payload: { id: ingressOnlyEvent },
    nowMs: 20_000, policy, evidence: ingressEvidence,
  });
  assert.strictEqual(ingressObserved.claim.lease_expires_at_ms, 60_000);
  const ingressRetry = acquireDispatch({
    claimRoot, phase: "start", eventId: ingressOnlyEvent, payload: { id: ingressOnlyEvent },
    nowMs: 60_001, policy,
  });
  assert.strictEqual(ingressRetry.action, "dispatch");
  assert.strictEqual(ingressRetry.claim.attempt, 2);

  const concurrent = acquireDispatch({ claimRoot, phase: "start", eventId, payload, nowMs: 1, policy });
  assert.strictEqual(concurrent.action, "skip");
  assert.strictEqual(concurrent.reason, "lease-active");
  const outstanding = listOutstandingClaims(claimRoot);
  assert(outstanding.some(item => item.key === `start:${eventId}`), "pending claims must remain discoverable after the calendar window closes");
  assert(!outstanding.some(item => item.status === "completed" || item.status === "exhausted"));
  assert.strictEqual(outstanding.find(item => item.key === `start:${eventId}`).payload.description, "full calendar context");

  const malformedPath = path.join(claimRoot, "start", "malformed.json");
  fs.mkdirSync(path.dirname(malformedPath), { recursive: true });
  fs.writeFileSync(malformedPath, "{");
  assert.throws(() => listOutstandingClaims(claimRoot), /malformed claim/, "corrupt durable state must fail closed instead of silently disappearing");
  fs.unlinkSync(malformedPath);

  const corruptEvent = "corrupt-event";
  const corruptClaimPath = require("./dispatch-lease").claimPathFor(claimRoot, "start", corruptEvent);
  fs.writeFileSync(corruptClaimPath, "{");
  assert.throws(
    () => acquireDispatch({ claimRoot, phase: "start", eventId: corruptEvent, payload: { id: corruptEvent }, nowMs: 1, policy }),
    /malformed claim/,
    "a corrupt canonical claim must fail closed instead of being overwritten",
  );
  fs.unlinkSync(corruptClaimPath);

  const orphanEvent = "orphan-event";
  const orphan = acquireDispatch({ claimRoot, phase: "start", eventId: orphanEvent, payload: { id: orphanEvent }, nowMs: 0, policy });
  fs.unlinkSync(orphan.claimPath);
  const reclaimed = acquireDispatch({ claimRoot, phase: "start", eventId: orphanEvent, payload: { id: orphanEvent }, nowMs: 31_001, policy });
  assert.strictEqual(reclaimed.action, "dispatch", "an unlocked persistent mutex file must be reusable");
  assert(fs.existsSync(reclaimed.claimPath));

  const retry = acquireDispatch({ claimRoot, phase: "start", eventId, payload, nowMs: 60_001, policy });
  assert.strictEqual(retry.action, "dispatch");
  assert.strictEqual(retry.claim.attempt, 2);
  assert.notStrictEqual(retry.deliveryId, first.deliveryId);

  const raceEvent = "stale-evidence-race";
  const raceFirst = acquireDispatch({ claimRoot, phase: "start", eventId: raceEvent, payload: { id: raceEvent }, nowMs: 0, policy });
  const raceReceiptPath = path.join(meetingsDir, raceEvent, ".dispatch-start.json");
  writeExecutorReceipt({
    receiptPath: raceReceiptPath,
    dispatch: { key: raceFirst.claim.key, phase: "start", attempt: 1, delivery_id: raceFirst.deliveryId },
    status: "succeeded", outcome: "old-generation", nowMs: 59_999,
  });
  const staleGenerationEvidence = inspectPhaseEvidence({
    phase: "start", eventId: raceEvent, meetingsDir, claim: raceFirst.claim,
    receiptPath: raceReceiptPath, activeLogPath: path.join(root, "none.log"), nowMs: 59_999, leaseMs: 60_000,
  });
  const raceSecond = acquireDispatch({ claimRoot, phase: "start", eventId: raceEvent, payload: { id: raceEvent }, nowMs: 60_001, policy });
  assert.strictEqual(raceSecond.claim.attempt, 2);
  const staleClosure = acquireDispatch({
    claimRoot, phase: "start", eventId: raceEvent, payload: { id: raceEvent },
    nowMs: 60_002, policy, evidence: staleGenerationEvidence,
  });
  assert.notStrictEqual(staleClosure.claim.status, "completed", "attempt 1 evidence must not close attempt 2");
  assert.strictEqual(staleClosure.claim.attempt, 2);

  assert.strictEqual(markDispatchAccepted(first.claimPath, 1, 60_002, { status: 202 }), false,
    "a superseded attempt must not overwrite the current generation");
  assert.strictEqual(markDispatchAccepted(retry.claimPath, 2, 60_002, { status: 202 }), true);

  const progressed = acquireDispatch({
    claimRoot, phase: "start", eventId, payload, nowMs: 120_002, policy,
    evidence: { progressMtimeMs: 120_001, running: true, reason: "fresh-vexa-boot" },
  });
  assert.strictEqual(progressed.action, "skip");
  assert.strictEqual(progressed.reason, "workflow-running");

  const completed = acquireDispatch({
    claimRoot, phase: "start", eventId, payload, nowMs: 180_003, policy,
    evidence: { terminal: { status: "succeeded", outcome: "vexa-active" } },
  });
  assert.strictEqual(completed.action, "skip");
  assert.strictEqual(completed.claim.status, "completed");
  assert.strictEqual(completed.claim.outcome, "vexa-active");
  assert.strictEqual(markDispatchAccepted(completed.claimPath, 2, 180_004, { status: 202 }), false,
    "late webhook return must not reopen a terminal claim");

  const failedEvent = "failed-event";
  const failed = acquireDispatch({ claimRoot, phase: "start", eventId: failedEvent, payload: { id: failedEvent }, nowMs: 0, policy });
  assert.strictEqual(markDispatchFailed(failed.claimPath, 1, 10, "HTTP 503", 20_010), true);
  assert.strictEqual(acquireDispatch({ claimRoot, phase: "start", eventId: failedEvent, payload: { id: failedEvent }, nowMs: 20_000, policy }).reason, "retry-backoff");
  assert.strictEqual(acquireDispatch({ claimRoot, phase: "start", eventId: failedEvent, payload: { id: failedEvent }, nowMs: 20_011, policy }).claim.attempt, 2);

  const exhaustedEvent = "exhausted-event";
  assert.strictEqual(acquireDispatch({ claimRoot, phase: "start", eventId: exhaustedEvent, payload: { id: exhaustedEvent }, nowMs: 0, policy }).claim.attempt, 1);
  assert.strictEqual(acquireDispatch({ claimRoot, phase: "start", eventId: exhaustedEvent, payload: { id: exhaustedEvent }, nowMs: 60_001, policy }).claim.attempt, 2);
  assert.strictEqual(acquireDispatch({ claimRoot, phase: "start", eventId: exhaustedEvent, payload: { id: exhaustedEvent }, nowMs: 120_002, policy }).claim.attempt, 3);
  const exhausted = acquireDispatch({ claimRoot, phase: "start", eventId: exhaustedEvent, payload: { id: exhaustedEvent }, nowMs: 180_003, policy });
  assert.strictEqual(exhausted.action, "skip");
  assert.strictEqual(exhausted.claim.status, "exhausted");

  const failedExhaustionEvent = "failed-exhaustion-event";
  const failedAttempt1 = acquireDispatch({ claimRoot, phase: "start", eventId: failedExhaustionEvent, payload: { id: failedExhaustionEvent }, nowMs: 0, policy });
  markDispatchFailed(failedAttempt1.claimPath, 1, 1, "HTTP 503", 2);
  const failedAttempt2 = acquireDispatch({ claimRoot, phase: "start", eventId: failedExhaustionEvent, payload: { id: failedExhaustionEvent }, nowMs: 3, policy });
  markDispatchFailed(failedAttempt2.claimPath, 2, 4, "HTTP 503", 5);
  const failedAttempt3 = acquireDispatch({ claimRoot, phase: "start", eventId: failedExhaustionEvent, payload: { id: failedExhaustionEvent }, nowMs: 6, policy });
  markDispatchFailed(failedAttempt3.claimPath, 3, 7, "HTTP 503", 8);
  const failedExhaustion = acquireDispatch({ claimRoot, phase: "start", eventId: failedExhaustionEvent, payload: { id: failedExhaustionEvent }, nowMs: 9, policy });
  assert.strictEqual(failedExhaustion.action, "skip");
  assert.strictEqual(failedExhaustion.reason, "attempts-exhausted");
  assert.strictEqual(failedExhaustion.claim.attempt, 3);
  assert(
    listOutstandingClaims(claimRoot).some(item => item.key === `start:${failedExhaustionEvent}`),
    "an exhausted claim must remain discoverable until its alert is delivered",
  );
  const alertNow = Date.now();
  assert.strictEqual(withMutex(failedExhaustion.claimPath, () => {
    assert.strictEqual(claimDispatchAlert(failedExhaustion.claimPath, 3, alertNow), null,
      "a busy claim mutex must not masquerade as an alert token");
    return true;
  }), true);
  const alertToken1 = claimDispatchAlert(failedExhaustion.claimPath, 3, alertNow);
  assert.strictEqual(typeof alertToken1, "string");
  assert.strictEqual(claimDispatchAlert(failedExhaustion.claimPath, 3, alertNow + 1), null,
    "a fresh terminal failure alert claim must serialize scheduler runs");
  const alertToken2 = claimDispatchAlert(failedExhaustion.claimPath, 3, alertNow + 6 * 60 * 1000 + 1);
  assert.strictEqual(typeof alertToken2, "string");
  assert.notStrictEqual(alertToken2, alertToken1);
  assert.strictEqual(
    markDispatchAlertResult(failedExhaustion.claimPath, 3, alertToken1, alertNow + 6 * 60 * 1000 + 2, true, null),
    false,
    "a reclaimed alert lease must fence the prior sender's late result",
  );
  assert.strictEqual(markDispatchAlertResult(failedExhaustion.claimPath, 3, alertToken2, alertNow + 6 * 60 * 1000 + 3, true, null), true,
    "a crashed alert sender must be retried after the alert lease");
  assert(
    !listOutstandingClaims(claimRoot).some(item => item.key === `start:${failedExhaustionEvent}`),
    "an exhausted claim with a sent alert must leave the watchdog queue",
  );

  const hardTimeoutPolicy = { ...policy, leaseMs: 60_000, hardTimeoutMs: 150_000, overallTimeoutMs: 150_000, maxAttempts: 99 };
  const hardTimeoutEvent = "hard-timeout-event";
  assert.strictEqual(acquireDispatch({ claimRoot, phase: "start", eventId: hardTimeoutEvent, payload: { id: hardTimeoutEvent }, nowMs: 0, policy: hardTimeoutPolicy }).claim.attempt, 1);
  assert.strictEqual(acquireDispatch({ claimRoot, phase: "start", eventId: hardTimeoutEvent, payload: { id: hardTimeoutEvent }, nowMs: 60_001, policy: hardTimeoutPolicy }).claim.attempt, 2);
  assert.strictEqual(acquireDispatch({ claimRoot, phase: "start", eventId: hardTimeoutEvent, payload: { id: hardTimeoutEvent }, nowMs: 120_002, policy: hardTimeoutPolicy }).claim.attempt, 3);
  const hardTimeout = acquireDispatch({ claimRoot, phase: "start", eventId: hardTimeoutEvent, payload: { id: hardTimeoutEvent }, nowMs: 150_001, policy: hardTimeoutPolicy });
  assert.strictEqual(hardTimeout.action, "skip");
  assert.strictEqual(hardTimeout.reason, "hard-timeout");
  assert.strictEqual(hardTimeout.claim.status, "exhausted");

  const receiptEvent = "receipt-event";
  const receiptDispatch = acquireDispatch({ claimRoot, phase: "start", eventId: receiptEvent, payload: { id: receiptEvent }, nowMs: 0, policy });
  const receiptPath = path.join(meetingsDir, receiptEvent, ".dispatch-start.json");
  const staleReceipt = writeExecutorReceipt({
    receiptPath,
    dispatch: { key: `start:${receiptEvent}`, phase: "start", attempt: 0, delivery_id: "stale" },
    status: "succeeded",
    outcome: "stale-attempt",
    nowMs: 5,
  });
  assert.strictEqual(staleReceipt, true);
  const staleEvidence = inspectPhaseEvidence({
    phase: "start", eventId: receiptEvent, meetingsDir, claim: receiptDispatch.claim, receiptPath,
    activeLogPath: path.join(root, "none.log"), nowMs: 10, leaseMs: 60_000,
  });
  assert.strictEqual(staleEvidence.terminal, null, "stale generation receipt must be fenced out");
  const noClaimEvidence = inspectPhaseEvidence({
    phase: "start", eventId: receiptEvent, meetingsDir, claim: null, receiptPath,
    activeLogPath: path.join(root, "none.log"), nowMs: 10, leaseMs: 60_000,
  });
  assert.strictEqual(noClaimEvidence.terminal, null, "a receipt without its scheduler claim must never become authoritative");
  writeExecutorReceipt({
    receiptPath,
    dispatch: { key: "finish:wrong-event", phase: "finish", attempt: 1, delivery_id: receiptDispatch.deliveryId },
    status: "succeeded",
    outcome: "wrong-workflow",
    nowMs: 10,
  });
  const wrongWorkflowEvidence = inspectPhaseEvidence({
    phase: "start", eventId: receiptEvent, meetingsDir, claim: receiptDispatch.claim, receiptPath,
    activeLogPath: path.join(root, "none.log"), nowMs: 10, leaseMs: 60_000,
  });
  assert.strictEqual(wrongWorkflowEvidence.terminal, null, "wrong key/phase receipt must be fenced out");
  writeExecutorReceipt({
    receiptPath,
    dispatch: { key: "finish:wrong-event", phase: "finish", attempt: 1, delivery_id: receiptDispatch.deliveryId },
    status: "running",
    outcome: "wrong-workflow",
    nowMs: 10,
  });
  const wrongRunningEvidence = inspectPhaseEvidence({
    phase: "start", eventId: receiptEvent, meetingsDir, claim: receiptDispatch.claim, receiptPath,
    activeLogPath: path.join(root, "none.log"), nowMs: 11, leaseMs: 60_000,
  });
  assert.notStrictEqual(wrongRunningEvidence.reason, "executor heartbeat", "wrong key/phase heartbeat must be fenced out");
  writeExecutorReceipt({
    receiptPath,
    dispatch: { key: receiptDispatch.claim.key, phase: "start", attempt: 1, delivery_id: receiptDispatch.deliveryId },
    status: "succeeded",
    outcome: "vexa-active",
    nowMs: 11,
  });
  const receiptEvidence = inspectPhaseEvidence({
    phase: "start", eventId: receiptEvent, meetingsDir, claim: receiptDispatch.claim, receiptPath,
    activeLogPath: path.join(root, "none.log"), nowMs: 12, leaseMs: 60_000,
  });
  assert.deepStrictEqual(receiptEvidence.terminal, { status: "succeeded", outcome: "vexa-active", retryable: false });

  const retryReceiptEvent = "retry-receipt-event";
  const retryReceiptDispatch = acquireDispatch({ claimRoot, phase: "start", eventId: retryReceiptEvent, payload: { id: retryReceiptEvent }, nowMs: 0, policy });
  const retryReceiptPath = path.join(meetingsDir, retryReceiptEvent, ".dispatch-start.json");
  writeExecutorReceipt({
    receiptPath: retryReceiptPath,
    dispatch: { key: retryReceiptDispatch.claim.key, phase: "start", attempt: 1, delivery_id: retryReceiptDispatch.deliveryId },
    status: "failed",
    outcome: "agent-crashed",
    retryable: true,
    nowMs: 10,
  });
  const retryFailureEvidence = inspectPhaseEvidence({
    phase: "start", eventId: retryReceiptEvent, meetingsDir, claim: retryReceiptDispatch.claim,
    receiptPath: retryReceiptPath, activeLogPath: path.join(root, "none.log"), nowMs: 11, leaseMs: 60_000,
  });
  assert.strictEqual(retryFailureEvidence.retryableFailure, true);
  const failedFromReceipt = acquireDispatch({
    claimRoot, phase: "start", eventId: retryReceiptEvent, payload: { id: retryReceiptEvent },
    nowMs: 11, policy, evidence: retryFailureEvidence,
  });
  assert.strictEqual(failedFromReceipt.action, "skip");
  assert.strictEqual(failedFromReceipt.reason, "retry-backoff");
  const retryFromReceipt = acquireDispatch({
    claimRoot, phase: "start", eventId: retryReceiptEvent, payload: { id: retryReceiptEvent },
    nowMs: 20_012, policy,
  });
  assert.strictEqual(retryFromReceipt.action, "dispatch");
  assert.strictEqual(retryFromReceipt.claim.attempt, 2);

  const reviewEvent = "review-event";
  const reviewDir = path.join(meetingsDir, reviewEvent);
  fs.mkdirSync(reviewDir, { recursive: true });
  const reviewFlagPath = path.join(reviewDir, ".review-flag.txt");
  const reviewStatusPath = path.join(reviewDir, "status.json");
  fs.writeFileSync(reviewFlagPath, "review\n");
  fs.writeFileSync(reviewStatusPath, JSON.stringify({ inCall: false, transcriptLines: 3 }));
  fs.utimesSync(reviewFlagPath, new Date(1_000), new Date(1_000));
  fs.utimesSync(reviewStatusPath, new Date(2_000), new Date(2_000));
  const staleReviewEvidence = inspectPhaseEvidence({
    phase: "finish", eventId: reviewEvent, meetingsDir, claim: null,
    receiptPath: path.join(root, "none"), activeLogPath: path.join(root, "none.log"), nowMs: 3_000, leaseMs: 60_000,
  });
  assert.strictEqual(staleReviewEvidence.terminal, null, "a newer capture must supersede an older review flag");

  fs.utimesSync(reviewFlagPath, new Date(4_000), new Date(4_000));
  const currentReviewEvidence = inspectPhaseEvidence({
    phase: "finish", eventId: reviewEvent, meetingsDir, claim: null,
    receiptPath: path.join(root, "none"), activeLogPath: path.join(root, "none.log"), nowMs: 5_000, leaseMs: 60_000,
  });
  assert.deepStrictEqual(currentReviewEvidence.terminal, { status: "skipped", outcome: "human-review" });

  const prepEvent = "prep-event";
  const prepMeetingsDir = path.join(root, "meetings-prep");
  fs.mkdirSync(path.join(prepMeetingsDir, prepEvent), { recursive: true });
  fs.writeFileSync(path.join(prepMeetingsDir, prepEvent, ".lock"), JSON.stringify({ status: "done" }));
  const prepDone = inspectPhaseEvidence({
    phase: "prep", eventId: prepEvent, meetingsDir, prepMeetingsDir, claim: null,
    receiptPath: path.join(root, "none"), activeLogPath: path.join(root, "none.log"), nowMs: 10, leaseMs: 60_000,
  });
  assert.deepStrictEqual(prepDone.terminal, { status: "succeeded", outcome: "prep-done" });

  const vexaId = "vexa-event";
  const vexaDir = path.join(meetingsDir, vexaId);
  fs.mkdirSync(vexaDir, { recursive: true });
  const joining = path.join(vexaDir, ".joining");
  fs.writeFileSync(joining, JSON.stringify({ status: "booting", backend: "vexa-primary", pid: null }));
  fs.utimesSync(joining, new Date(100_000), new Date(100_000));
  const freshVexa = inspectPhaseEvidence({
    phase: "start", eventId: vexaId, meetingsDir, claim: null, receiptPath: path.join(root, "none"),
    activeLogPath: path.join(root, "none.log"), nowMs: 120_000, leaseMs: 60_000,
  });
  assert.strictEqual(freshVexa.running, true, "fresh remote boot with pid:null is live, not dead");
  assert.strictEqual(freshVexa.terminal, null);

  const legacyRemoteEvent = "legacy-remote-sentinel";
  const legacyRemoteDir = path.join(meetingsDir, legacyRemoteEvent);
  fs.mkdirSync(legacyRemoteDir, { recursive: true });
  const legacyRemoteJoining = path.join(legacyRemoteDir, ".joining");
  fs.writeFileSync(legacyRemoteJoining, JSON.stringify({ status: "booting", pid: null }));
  fs.utimesSync(legacyRemoteJoining, new Date(100_000), new Date(100_000));
  const freshLegacyRemote = inspectPhaseEvidence({
    phase: "start", eventId: legacyRemoteEvent, meetingsDir, claim: null, receiptPath: path.join(root, "none"),
    activeLogPath: path.join(root, "none.log"), nowMs: 120_000, leaseMs: 60_000,
  });
  assert.strictEqual(freshLegacyRemote.running, true, "fresh atomic pid:null sentinel is live before backend selection");
  assert.strictEqual(freshLegacyRemote.terminal, null);

  for (const exitReason of ["profile-locked", "cross-meeting-profile-contention", "lobby-timeout-early-join"]) {
    const transientEvent = `transient-${exitReason}`;
    const transientDir = path.join(meetingsDir, transientEvent);
    fs.mkdirSync(transientDir, { recursive: true });
    const transientJoining = path.join(transientDir, ".joining");
    fs.writeFileSync(transientJoining, JSON.stringify({ status: "exited", exit_reason: exitReason }));
    fs.utimesSync(transientJoining, new Date(100_000), new Date(100_000));
    const transientEvidence = inspectPhaseEvidence({
      phase: "start", eventId: transientEvent, meetingsDir, claim: null, receiptPath: path.join(root, "none"),
      activeLogPath: path.join(root, "none.log"), nowMs: 120_000, leaseMs: 60_000,
    });
    assert.strictEqual(transientEvidence.terminal, null, `${exitReason} must remain retryable`);
  }

  fs.mkdirSync(path.join(meetingsDir, "active"), { recursive: true });
  fs.writeFileSync(path.join(meetingsDir, "active", `${vexaId}.lock`), JSON.stringify({ status: "active", backend: "vexa-primary", bot_id: 6 }));
  const activeVexa = inspectPhaseEvidence({
    phase: "start", eventId: vexaId, meetingsDir, claim: null, receiptPath: path.join(root, "none"),
    activeLogPath: path.join(root, "none.log"), nowMs: 121_000, leaseMs: 60_000,
  });
  assert.deepStrictEqual(activeVexa.terminal, { status: "succeeded", outcome: "vexa-active" });

  console.log("dispatch lease: PASS");
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}

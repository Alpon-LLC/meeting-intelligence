/**
 * meeting-trigger/run.js
 *
 * Local cron replacement for GCP Cloud Run main.js.
 * Run every 2 min via Hermes cron (no_agent mode) or system cron.
 *
 * Differences from main.js:
 *   - No Cloud Run / functions-framework wrapper — runs as a plain Node script.
 *   - No GCP Secret Manager — reads env vars from .env in this directory.
 *   - Always uses OAuth (OAuthCalendarProvider).
 *   - Daily UTC rotating log files instead of console.log.
 *   - Stateful deferred finalization: when a meeting ends but the bot's
 *     lastCaptionAt is < 5 min ago the finished webhook is held back and
 *     retried every 15 min until the meeting goes quiet.
 *
 * State file:  ~/.hermes/workspace/meeting-trigger/state.json
 * Logs:        ~/.hermes/workspace/meeting-trigger/logs/YYYY-MM-DD.log  (UTC)
 * Env:         ./. env  (see .env.example)
 */

"use strict";

require("dotenv").config({ path: require("path").join(__dirname, "../.env") });
require("./public-config").load();

const { google } = require("googleapis");
const fetch = require("node-fetch");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const os = require("os");
const {
  DEFAULT_POLICY,
  acquireDispatch,
  buildDispatchPayload,
  claimDispatchAlert,
  claimPathFor,
  inspectPhaseEvidence,
  listOutstandingClaims,
  markDispatchAccepted,
  markDispatchAlertResult,
  markDispatchFailed,
  readJson,
  receiptPathFor,
} = require("./dispatch-lease");

// ========================
// PATHS
// ========================
const SCRIPT_DIR = __dirname;
const HERMES_HOME = process.env.HERMES_HOME || path.join(os.homedir(), ".hermes");
const WORKSPACE_DIR = path.join(HERMES_HOME, "workspace", "meeting-trigger");
const LOG_DIR = path.join(WORKSPACE_DIR, "logs");
const STATE_FILE = path.join(WORKSPACE_DIR, "state.json");
const MEETINGS_DIR = path.join(HERMES_HOME, "workspace", "meetings");
const PREP_MEETINGS_DIR = path.join(HERMES_HOME, "workspace", "meetings-prep");
const DISPATCH_CLAIM_DIR = path.join(WORKSPACE_DIR, "dispatch-claims");
const ACTIVE_JOIN_LOG = path.join(MEETINGS_DIR, "active", "meeting-auto-join.log");

const ACTIVITY_THRESHOLD_MS = 5 * 60 * 1000;    // 5 min silence → meeting over
const BACKOFF_MS = 15 * 60 * 1000;               // re-check interval
const STALE_DEFERRED_TTL_MS = 24 * 60 * 60 * 1000; // evict after 24 h
const LOCK_STALE_MS = 5 * 60 * 1000;             // stale lock threshold (mirrors skill rules)
const AUTH_RE_ALERT_INTERVAL_MS = 2 * 60 * 60 * 1000; // re-alert on auth failure every 2h
const DISPATCH_POLICY = Object.freeze({
  ...DEFAULT_POLICY,
  leaseMs: 6 * 60 * 1000,          // three missed 2-minute ticks before retry
  hardTimeoutMs: 18 * 60 * 1000,   // bounded recovery window
  maxAttempts: 3,
  retryBackoffMs: 2 * 60 * 1000,
});
const WEBHOOK_TIMEOUT_MS = Number(process.env.MEETING_WEBHOOK_TIMEOUT_MS || 30_000);
const DIRECT_VEXA_START = true; // Prep and finish stay signed agent webhooks.

function dispatchPolicyFor(phase, payload, nowMs = Date.now()) {
  if (phase !== "start") return DISPATCH_POLICY;
  const eventEndMs = Date.parse(payload?.end || "");
  if (!Number.isFinite(eventEndMs) || eventEndMs <= nowMs) return DISPATCH_POLICY;
  const waitMs = Math.max(DISPATCH_POLICY.hardTimeoutMs, Math.min(2 * 60 * 60 * 1000, eventEndMs - nowMs + 5 * 60 * 1000));
  return Object.freeze({ ...DISPATCH_POLICY, hardTimeoutMs: waitMs, overallTimeoutMs: waitMs });
}

const CHROME_PROFILE_DIR = path.join(HERMES_HOME, "workspace", "meetings", "chrome-profile");
const AUTH_HEALTH_MARKER = path.join(HERMES_HOME, "workspace", "meetings", ".auth_health_alerted");


// ========================
// LOGGING — daily UTC rotating files
// ========================
function _utcDateStr() {
  return new Date().toISOString().slice(0, 10); // YYYY-MM-DD
}

function _ts() {
  return new Date().toISOString(); // full ISO-8601 UTC
}

function log(level, ...args) {
  const msg = `${_ts()} ${level.padEnd(7)} ${args.join(" ")}`;

  // Append to today's UTC log file
  fs.mkdirSync(LOG_DIR, { recursive: true });
  fs.appendFileSync(path.join(LOG_DIR, `${_utcDateStr()}.log`), msg + "\n");

  // Echo INFO+ to stdout so Hermes cron output captures it
  if (level !== "DEBUG") {
    process.stdout.write(msg + "\n");
  }
}

function debugLog(step, data) {
  let msg = `[DEBUG] === ${step} ===`;
  if (data !== undefined) {
    msg += "\n" + (typeof data === "object" ? JSON.stringify(data, null, 2) : String(data));
  }
  log("DEBUG", msg);
}

// ========================
// STATE MANAGEMENT
// ========================
function loadState() {
  try {
    if (fs.existsSync(STATE_FILE)) {
      return JSON.parse(fs.readFileSync(STATE_FILE, "utf-8"));
    }
  } catch (e) {
    log("WARN", `state load failed (${e.message}) — starting fresh`);
  }
  return { deferred_finalizations: {} };
}

function saveState(state) {
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  const tmp = STATE_FILE + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2));
  fs.renameSync(tmp, STATE_FILE);
  debugLog("state saved", { deferred: Object.keys(state.deferred_finalizations).length });
}

// ========================
// SECRET PROVIDER  (env-only — no GCP Secret Manager needed locally)
// ========================
class EnvSecretProvider {
  async get(name) {
    debugLog(`EnvSecretProvider: Fetching ${name}`);
    return process.env[name];
  }
}

// ========================
// CALENDAR PROVIDER  (OAuth only — same as main.js staging path)
// ========================
class OAuthCalendarProvider {
  constructor(secrets) {
    this.secrets = secrets;
  }

  async init() {
    debugLog("Initializing OAuthCalendarProvider");

    const clientId = await this.secrets.get("CLIENT_ID");
    const clientSecret = await this.secrets.get("CLIENT_SECRET");
    const refreshToken = await this.secrets.get("REFRESH_TOKEN");

    const auth = new google.auth.OAuth2(clientId, clientSecret);
    auth.setCredentials({ refresh_token: refreshToken });

    this.calendar = google.calendar({ version: "v3", auth });

    debugLog("OAuthCalendarProvider init complete");
  }

  async getEventsBetween(calendarId, timeMin, timeMax) {
    debugLog(`Fetching events for calendar: ${calendarId}`, {
      timeMin: timeMin.toISOString(),
      timeMax: timeMax.toISOString(),
    });

    const res = await this.calendar.events.list({
      calendarId,
      timeMin: timeMin.toISOString(),
      timeMax: timeMax.toISOString(),
      singleEvents: true,
      orderBy: "startTime",
    });

    debugLog("Raw events fetched", res.data.items?.length || 0);
    return res.data.items || [];
  }
}

// ========================
// EMAIL / INVOLVEMENT LOGIC  — verbatim from main.js
// ========================
function normalizeEmail(email) {
  return typeof email === "string" ? email.trim().toLowerCase() : null;
}

function isOurCalendarInvolved(event, calendarOwnerEmail) {
  const ourEmail = normalizeEmail(calendarOwnerEmail);

  if (!ourEmail) {
    debugLog("Calendar owner email missing. Skipping involvement check.");
    return false;
  }

  const organizerEmail = normalizeEmail(event.organizer?.email);
  const creatorEmail = normalizeEmail(event.creator?.email);

  const attendeeEmails = (event.attendees || [])
    .map((attendee) => normalizeEmail(attendee.email))
    .filter(Boolean);

  const involved =
    organizerEmail === ourEmail ||
    creatorEmail === ourEmail ||
    attendeeEmails.includes(ourEmail);

  if (!involved) {
    debugLog(
      `Event "${event.summary}" skipped — calendar owner not involved`,
      { calendarOwnerEmail: ourEmail, organizerEmail, creatorEmail, attendeeEmails },
    );
  }

  return involved;
}

// ========================
// TIME WINDOW LOGIC  — verbatim from main.js
// ========================
function isWithinJoinWindow(event) {
  if (!event.start?.dateTime) return false;

  const now = Date.now();
  const start = new Date(event.start.dateTime).getTime();
  const diffMin = (start - now) / (1000 * 60);
  const isWithin = diffMin <= 8 && diffMin >= -10;

  if (isWithin) {
    debugLog(`Event "${event.summary}" falls within join window. diffMin`, diffMin);
  }

  return isWithin;
}

function isWithinPrepWindow(event) {
  if (!event.start?.dateTime) return false;

  const now = Date.now();
  const start = new Date(event.start.dateTime).getTime();
  const minutesUntilStart = (start - now) / (1000 * 60);
  const isWithin = minutesUntilStart >= 30 && minutesUntilStart <= 40;

  debugLog(`Prep window check for "${event.summary}"`, {
    eventStart: event.start.dateTime,
    minutesUntilStart,
    isWithin,
  });

  return isWithin;
}

function isWithinFinishedDelayWindow(event) {
  if (!event.end?.dateTime) return false;

  const now = Date.now();
  const end = new Date(event.end.dateTime).getTime();
  const minutesAfterEnd = (now - end) / (1000 * 60);

  // Trigger only from 30 to 40 minutes after scheduled end.
  const isWithin = minutesAfterEnd >= 1  && minutesAfterEnd <= 40;

  debugLog(`Finished window check for "${event.summary}"`, {
    eventEnd: event.end.dateTime,
    minutesAfterEnd,
    isWithin,
  });

  return isWithin;
}

// ========================
// FILTERS  — verbatim from main.js
// ========================
function consentEligible(event) {
  if (process.env.MEETING_RECORDING_CONSENT !== "true") return false;
  const marker = process.env.MEETING_CONSENT_MARKER || "[record]";
  if (!String(event.summary || "").includes(marker)) return false;
  if ((event.attendees || []).some(a => a.self && a.responseStatus === "declined")) return false;
  return true;
}

function hasVideoMeeting(event) {
  if (!consentEligible(event)) return false;
  return Boolean(
    event.hangoutLink ||
    event.conferenceData?.entryPoints?.some(
      (entry) => entry.entryPointType === "video",
    ),
  );
}

function filterPrepMeetings(events, calendarOwnerEmail) {
  debugLog("Filtering prep meetings", { totalEvents: events.length });

  return events.filter((event) => {
    if (event.status === "cancelled") return false;
    if (!isOurCalendarInvolved(event, calendarOwnerEmail)) return false;
    if (!isWithinPrepWindow(event)) return false;
    if (!hasVideoMeeting(event)) return false;
    debugLog(`Event "${event.summary}" passed prep meeting filters.`);
    return true;
  });
}

function filterStartingMeetings(events, calendarOwnerEmail) {
  debugLog("Filtering starting meetings", { totalEvents: events.length });

  return events.filter((event) => {
    if (event.status === "cancelled") return false;
    if (!isOurCalendarInvolved(event, calendarOwnerEmail)) return false;
    if (!isWithinJoinWindow(event)) return false;
    if (!hasVideoMeeting(event)) return false;
    debugLog(`Event "${event.summary}" passed starting meeting filters.`);
    return true;
  });
}

function filterInProgressMeetings(events, calendarOwnerEmail, nowMs = Date.now()) {
  return events.filter((event) => {
    if (event.status === "cancelled" || !isOurCalendarInvolved(event, calendarOwnerEmail) || !hasVideoMeeting(event)) return false;
    const startMs = Date.parse(event.start?.dateTime || "");
    const endMs = Date.parse(event.end?.dateTime || "");
    return Number.isFinite(startMs) && Number.isFinite(endMs) && startMs <= nowMs && nowMs < endMs;
  });
}

function filterFinishedMeetings(events, calendarOwnerEmail) {
  debugLog("Filtering finished meetings", { totalEvents: events.length });

  return events.filter((event) => {
    if (event.status === "cancelled") return false;
    if (!isOurCalendarInvolved(event, calendarOwnerEmail)) return false;
    if (!hasVideoMeeting(event)) return false;
    if (!isWithinFinishedDelayWindow(event)) return false;
    debugLog(`Event "${event.summary}" passed finished meeting filters.`);
    return true;
  });
}

// ========================
// NORMALIZER  — verbatim from main.js
// ========================
function normalizeEvent(e) {
  const rawAttendees = e.attendees || [];

  const organizerEmail = e.organizer?.email || null;

  const organizerAsAttendeeExists = rawAttendees.some(
    (attendee) =>
      normalizeEmail(attendee.email) === normalizeEmail(organizerEmail),
  );

  const attendees = rawAttendees.map((attendee) => ({
    email: attendee.email || null,
    display_name: attendee.displayName || null,
    response_status: attendee.responseStatus || null,
    organizer: attendee.organizer || false,
    self: attendee.self || false,
    optional: attendee.optional || false,
  }));

  if (organizerEmail && !organizerAsAttendeeExists) {
    attendees.unshift({
      email: organizerEmail,
      display_name: e.organizer?.displayName || null,
      response_status: "accepted",
      organizer: true,
      self: e.organizer?.self || false,
      optional: false,
    });
  }

  const normalized = {
    id: e.id,
    title: e.summary || "(No title)",
    start: e.start?.dateTime || null,
    end: e.end?.dateTime || null,

    meet_url:
      e.hangoutLink ||
      e.conferenceData?.entryPoints?.find(
        (entry) => entry.entryPointType === "video",
      )?.uri ||
      null,

    organizer: {
      email: organizerEmail,
      display_name: e.organizer?.displayName || null,
      self: e.organizer?.self || false,
    },

    attendees,
  };

  debugLog("Normalized Event Payload", normalized);
  return normalized;
}

// ========================
// WEBHOOK  — verbatim from main.js
// ========================
function signPayload(payload, secret) {
  return crypto
    .createHmac("sha256", secret)
    .update(JSON.stringify(payload))
    .digest("hex");
}

async function sendWebhook(url, payload, secret, deliveryId) {
  if (!url) {
    throw new Error("Webhook URL is missing");
  }

  debugLog(`Preparing to send webhook to: ${url}`);

  const signature = signPayload(payload, secret);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), WEBHOOK_TIMEOUT_MS);
  let res;
  let bodyText;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
        "X-Request-ID": deliveryId,
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    bodyText = await res.text();
  } catch (error) {
    if (error.name === "AbortError") {
      const timeoutError = new Error(`Webhook timed out after ${WEBHOOK_TIMEOUT_MS}ms`);
      timeoutError.ambiguous = true;
      throw timeoutError;
    }
    error.ambiguous = true;
    throw error;
  } finally {
    clearTimeout(timeout);
  }
  let responseBody = null;
  try {
    responseBody = bodyText ? JSON.parse(bodyText) : null;
  } catch {
    responseBody = null;
  }

  if (!res.ok) {
    throw new Error(`Webhook failed with status ${res.status}: ${bodyText}`);
  }
  if (res.status !== 202 || responseBody?.status !== "accepted") {
    throw new Error(`Webhook expected 202 Accepted, got ${res.status}: ${bodyText}`);
  }
  if (responseBody.delivery_id !== deliveryId) {
    throw new Error(`Webhook delivery ID mismatch: expected ${deliveryId}`);
  }

  debugLog("Webhook sent. Response", { status: res.status, deliveryId, body: responseBody });
  return { status: res.status, body: responseBody };
}

async function dispatchPhaseWebhook({ phase, eventId, payload, url, secret, mainLog, onExhausted = null }) {
  if (phase === "finish") {
    const { inspectVexaFinish, startPostcallWorker, startPublicationWorker } = require("./vexa-finish-gate");
    const readiness = inspectVexaFinish(MEETINGS_DIR, eventId);
    if (!readiness.ready) {
      if (readiness.reason === "postcall-pending") startPostcallWorker(readiness.dir);
      if (readiness.reason === "publication-reconcile-pending") startPublicationWorker(readiness.dir);
      if (["postcall-needs-review", "signal-materialization-needs-review", "publication-needs-review", "prior-capture-needs-review"].includes(readiness.reason) && onExhausted) {
        const alertPath = path.join(readiness.dir, readiness.reason === "publication-needs-review" ? ".vexa-publication-alert.json" : ".vexa-postcall-alert.json");
        let fd = null;
        try { fd = fs.openSync(alertPath, "wx", 0o600); } catch (error) {
          if (error.code !== "EEXIST") throw error;
        }
        if (fd !== null) {
          fs.writeSync(fd, JSON.stringify({ status: "sending", created_at: new Date().toISOString() }));
          fs.closeSync(fd);
          try {
            await onExhausted({ reason: readiness.reason, claim: { attempt: readiness.attempts } });
            fs.writeFileSync(alertPath, JSON.stringify({ status: "sent", updated_at: new Date().toISOString() }));
          } catch (error) {
            // Do not resend an ambiguous Slack delivery automatically.
            fs.writeFileSync(alertPath, JSON.stringify({ status: "delivery-unverified", updated_at: new Date().toISOString() }));
            throw error;
          }
        }
      }
      mainLog("finish deferred until Vexa evidence is terminal", { eventId, reason: readiness.reason });
      return { sent: false, decision: { action: "skip", reason: readiness.reason } };
    }
  }
  const currentClaim = readJson(claimPathFor(DISPATCH_CLAIM_DIR, phase, eventId));
  const receiptPath = receiptPathFor(MEETINGS_DIR, eventId, phase);
  const evidence = inspectPhaseEvidence({
    phase,
    eventId,
    meetingsDir: MEETINGS_DIR,
    prepMeetingsDir: PREP_MEETINGS_DIR,
    claim: currentClaim,
    receiptPath,
    activeLogPath: ACTIVE_JOIN_LOG,
    nowMs: Date.now(),
    leaseMs: DISPATCH_POLICY.leaseMs,
  });
  const phasePolicy = dispatchPolicyFor(phase, payload);
  const decision = acquireDispatch({
    claimRoot: DISPATCH_CLAIM_DIR,
    phase,
    eventId,
    payload,
    evidence,
    nowMs: Date.now(),
    policy: phasePolicy,
  });

  if (decision.action !== "dispatch") {
    mainLog(`${phase} webhook skipped by dispatch lease`, {
      eventId,
      reason: decision.reason,
      attempt: decision.claim?.attempt,
      status: decision.claim?.status,
      evidence: evidence.reason,
    });
    const alertToken = (
      ["attempts-exhausted", "hard-timeout"].includes(decision.reason) &&
      onExhausted &&
      claimDispatchAlert(decision.claimPath, decision.claim.attempt, Date.now())
    );
    if (alertToken) {
      try {
        await onExhausted(decision);
        markDispatchAlertResult(decision.claimPath, decision.claim.attempt, alertToken, Date.now(), true, null);
      } catch (error) {
        markDispatchAlertResult(decision.claimPath, decision.claim.attempt, alertToken, Date.now(), false, error.message);
        throw error;
      }
    }
    return { sent: false, decision };
  }
  const dispatchPayload = buildDispatchPayload(payload, decision, MEETINGS_DIR);
  if (DIRECT_VEXA_START && phase === "start") {
    try {
      const { startLifecycleWorker } = require("./vexa-lifecycle-gate");
      const worker = startLifecycleWorker(HERMES_HOME, phase, dispatchPayload);
      const response = { status: 202, body: { status: "accepted", delivery_id: decision.deliveryId, worker_pid: worker.pid } };
      const accepted = markDispatchAccepted(decision.claimPath, decision.claim.attempt, Date.now(), response);
      mainLog(`meeting ${phase} deterministic worker started`, {
        eventId, attempt: decision.claim.attempt, deliveryId: decision.deliveryId, workerPid: worker.pid,
      });
      return { sent: accepted, decision, response, superseded: !accepted };
    } catch (error) {
      markDispatchFailed(decision.claimPath, decision.claim.attempt, Date.now(), error.message,
        Date.now() + phasePolicy.retryBackoffMs);
      throw error;
    }
  }
  if (!url) {
    markDispatchFailed(
      decision.claimPath,
      decision.claim.attempt,
      Date.now(),
      "webhook URL missing",
      Date.now() + DISPATCH_POLICY.retryBackoffMs,
    );
    throw new Error(`${phase} webhook URL is missing`);
  }

  mainLog(`sending meeting ${phase} webhook`, {
    eventId,
    title: payload.title,
    attempt: decision.claim.attempt,
    deliveryId: decision.deliveryId,
    leaseReason: decision.reason,
  });
  try {
    const response = await sendWebhook(url, dispatchPayload, secret, decision.deliveryId);
    const accepted = markDispatchAccepted(decision.claimPath, decision.claim.attempt, Date.now(), response);
    if (!accepted) {
      mainLog(`meeting ${phase} webhook accepted after attempt was superseded`, {
        eventId,
        attempt: decision.claim.attempt,
        deliveryId: decision.deliveryId,
      });
      return { sent: false, decision, response, superseded: true };
    }
    mainLog(`meeting ${phase} webhook accepted`, {
      eventId,
      attempt: decision.claim.attempt,
      deliveryId: decision.deliveryId,
      httpStatus: response.status,
    });
    return { sent: true, decision, response };
  } catch (error) {
    if (!error.ambiguous) {
      markDispatchFailed(
        decision.claimPath,
        decision.claim.attempt,
        Date.now(),
        error.message,
        Date.now() + DISPATCH_POLICY.retryBackoffMs,
      );
    }
    throw error;
  }
}

// ========================
// BOT ACTIVITY CHECK  (new — reads meet_bot status.json)
// ========================
function isMeetingStillActive(eventId) {
  const { inspectVexaFinish } = require("./vexa-finish-gate");
  if (inspectVexaFinish(MEETINGS_DIR, eventId).reason === "vexa-still-capturing") return true;
  const statusPath = path.join(MEETINGS_DIR, eventId, "status.json");

  if (!fs.existsSync(statusPath)) {
    debugLog(`Activity check ${eventId}: no status.json — not active`);
    return false;
  }

  try {
    const status = JSON.parse(fs.readFileSync(statusPath, "utf-8"));

    if (status.exited) {
      debugLog(`Activity check ${eventId}: bot exited`);
      return false;
    }
    if (!status.inCall) {
      debugLog(`Activity check ${eventId}: bot not inCall`);
      return false;
    }
    if (status.lastCaptionAt === null || status.lastCaptionAt === undefined) {
      debugLog(`Activity check ${eventId}: no lastCaptionAt`);
      return false;
    }

    // lastCaptionAt is a Unix epoch float written by meet_bot.py
    const ageMs = Date.now() - status.lastCaptionAt * 1000;
    const active = ageMs < ACTIVITY_THRESHOLD_MS;

    debugLog(`Activity check ${eventId}`, {
      lastCaptionAtAgoMin: (ageMs / 60000).toFixed(1),
      active,
    });

    return active;
  } catch (e) {
    log("WARN", `Activity check failed for ${eventId}: ${e.message}`);
    return false;
  }
}

// ========================
// IDEMPOTENCY CHECKS — read skill/bot lock files before sending webhooks
// ========================

function readJsonFile(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf-8"));
  } catch {
    return null;
  }
}

function isPidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

// Returns { skip: boolean, reason: string }
function checkPrepLock(eventId) {
  const lockPath = path.join(PREP_MEETINGS_DIR, eventId, ".lock");
  const lock = readJsonFile(lockPath);
  if (!lock) return { skip: false, reason: "no prep lock" };

  if (lock.status === "done") {
    return { skip: true, reason: "prep already completed" };
  }
  if (lock.status === "prepping" && lock.prepping_since) {
    const ageMs = Date.now() - new Date(lock.prepping_since).getTime();
    if (ageMs < LOCK_STALE_MS) {
      return { skip: true, reason: `prep in progress (age ${Math.floor(ageMs / 1000)}s)` };
    }
    return { skip: false, reason: `prep lock stale (age ${Math.floor(ageMs / 60000)}min) — retry` };
  }
  return { skip: false, reason: "unknown prep lock state — proceed" };
}

// Returns { skip: boolean, reason: string, hardBlock: boolean, authFailReason?: string }
function checkJoinLock(eventId) {
  const joiningPath = path.join(MEETINGS_DIR, eventId, ".joining");
  const joining = readJsonFile(joiningPath);
  if (!joining) return { skip: false, reason: "no joining lock", hardBlock: false };

  if (joining.status === "admitted") {
    return { skip: true, reason: "bot already admitted to call", hardBlock: false };
  }

  if (joining.status === "booting") {
    const pid = joining.pid;
    if (pid && isPidAlive(pid)) {
      return { skip: true, reason: `bot booting — PID ${pid} alive`, hardBlock: false };
    }
    // PID is dead (or null) — retry regardless of lock age
    return { skip: false, reason: `bot booting but PID dead — retry`, hardBlock: false };
  }

  if (joining.status === "exited") {
    const AUTH_BLOCKS = ["auth-failed-sign-in-redirect", "auth-wrong-account"];
    if (AUTH_BLOCKS.includes(joining.exit_reason)) {
      return { skip: true, reason: `auth failure: ${joining.exit_reason}`, hardBlock: true, authFailReason: joining.exit_reason };
    }
    if (joining.exit_reason === "knockout") {
      return { skip: true, reason: "bot exited with knockout — meeting covered", hardBlock: false };
    }
    if (joining.exit_reason === "profile-locked") {
      return { skip: true, reason: "chrome profile in use by another bot — skip this tick", hardBlock: false };
    }
    // null / "disconnected" / other: allow retry
    return { skip: false, reason: `bot exited (${joining.exit_reason || "no reason"}) — retry`, hardBlock: false };
  }

  return { skip: false, reason: "unknown join lock state — proceed", hardBlock: false };
}

// Returns { skip: boolean, reason: string }
function checkFinalizeLock(eventId) {
  const workspaceDir = path.join(MEETINGS_DIR, eventId);
  const finalizedPath = path.join(workspaceDir, ".finalized");
  const finalizingPath = path.join(workspaceDir, ".finalizing");
  const triageContextPath = path.join(workspaceDir, "triage-context.json");

  if (fs.existsSync(finalizedPath)) {
    return { skip: true, reason: "meeting already finalized (.finalized exists)" };
  }

  // Triage: skill wrote triage-context.json and is waiting for human approval.
  // .finalizing will look stale after 5+ min but the flow is legitimately in-flight.
  if (fs.existsSync(triageContextPath)) {
    return { skip: true, reason: "meeting awaiting triage approval (triage-context.json exists)" };
  }

  // Suppress retries while a human-review flag describes the current capture.
  // A later bot run supersedes the flag only by updating status.json after it.
  const reviewFlagPath = path.join(workspaceDir, ".review-flag.txt");
  if (fs.existsSync(reviewFlagPath)) {
    const statusPath = path.join(workspaceDir, "status.json");
    const hasNewerCapture =
      fs.existsSync(statusPath) &&
      fs.statSync(statusPath).mtimeMs > fs.statSync(reviewFlagPath).mtimeMs;
    if (!hasNewerCapture) {
      return { skip: true, reason: "meeting flagged for human review (.review-flag.txt is current)" };
    }
  }

  if (fs.existsSync(finalizingPath)) {
    const finalizing = readJsonFile(finalizingPath);
    const since = finalizing?.prepping_since || finalizing?.locked_at;
    // Fall back to file mtime when .finalizing is 0 bytes or has no timestamp field
    const sinceMs = since ? new Date(since).getTime() : fs.statSync(finalizingPath).mtimeMs;
    const ageMs = Date.now() - sinceMs;
    if (ageMs < LOCK_STALE_MS) {
      return { skip: true, reason: `finalization in progress (age ${Math.floor(ageMs / 1000)}s)` };
    }
    return { skip: false, reason: `finalizing lock stale (age ${Math.floor(ageMs / 60000)}min) — retry` };
  }

  return { skip: false, reason: "no finalize lock" };
}

// Returns { skip: boolean, reason: string }
// Skip finalization when the bot never joined the call — no transcript was captured,
// so there is nothing for the finalize skill to process.
const NEVER_JOINED_EXIT_REASONS = ["auth-failed-sign-in-redirect", "auth-wrong-account"];
function checkBotNeverJoined(eventId) {
  const vexa = require("./vexa-no-join").inspectVexaNoJoin(path.join(MEETINGS_DIR, eventId));
  if (vexa) return { skip: vexa.noJoin, reason: vexa.reason };
  const noJoin = readJsonFile(path.join(MEETINGS_DIR, eventId, ".no-join"));
  if (noJoin) {
    return { skip: true, reason: `bot never joined (${noJoin.reason || "no-join"}) — nothing to finalize` };
  }
  const joiningPath = path.join(MEETINGS_DIR, eventId, ".joining");
  const joining = readJsonFile(joiningPath);
  if (!joining) return { skip: false, reason: "no joining file" };
  if (joining.status === "exited" && NEVER_JOINED_EXIT_REASONS.includes(joining.exit_reason)) {
    return { skip: true, reason: `bot never joined (exit_reason: ${joining.exit_reason}) — nothing to finalize` };
  }
  return { skip: false, reason: "bot joined or exit reason does not block finalization" };
}

// ========================
// CHROME PROFILE HEALTH CHECK
// ========================

// Returns { valid: boolean, reason: string }
// Checks that the persistent chrome-profile used by meet_bot.py is initialised.
// The profile is set up once via VNC (sign in as your configured bot account) and then
// reused across all bot launches via launch_persistent_context. A missing profile
// means the VNC setup has not been done or the profile directory was wiped.
function checkChromeProfileHealth() {
  const cookiesFile = path.join(CHROME_PROFILE_DIR, "Default", "Cookies");
  if (!fs.existsSync(cookiesFile)) {
    return { valid: false, reason: `chrome-profile not initialised — ${cookiesFile} missing. VNC setup required.` };
  }
  return { valid: true, reason: "chrome-profile present" };
}

// ========================
// SLACK ALERTS
// ========================
async function sendSlackAlert(token, channelId, text) {
  if (!token || !channelId) {
    log("WARN", "Slack alert skipped: SLACK_BOT_TOKEN or SLACK_CHANNEL_ID not configured");
    return false;
  }
  const res = await fetch("https://slack.com/api/chat.postMessage", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ channel: channelId, text }),
  });
  const body = await res.json();
  if (!body.ok) {
    throw new Error(`Slack API error: ${body.error}`);
  }
  return true;
}

// ========================
// MAIN
// ========================
async function main() {
  let mainStep = 0;
  function mainLog(step, data) {
    mainStep += 1;
    const prefix = `(${mainStep}) ${step}`;
    if (data !== undefined) {
      log("INFO", `${prefix}  ${typeof data === "object" ? JSON.stringify(data) : data}`);
    } else {
      log("INFO", prefix);
    }
  }

  mainLog("run start");
  await require("./vexa-status").syncVexaStatus(HERMES_HOME);

  const secrets = new EnvSecretProvider();

  const calendarId = await secrets.get("CALENDAR_ID");
  const configuredOwnerEmail = await secrets.get("CALENDAR_OWNER_EMAIL");
  const calendarOwnerEmail = configuredOwnerEmail || calendarId;

  mainLog("calendar ID loaded", { calendarId });
  mainLog("calendar owner email resolved", { configuredOwnerEmail, calendarOwnerEmail });

  if (!calendarOwnerEmail || calendarOwnerEmail === "primary") {
    log("ERROR", 'CALENDAR_OWNER_EMAIL required when CALENDAR_ID is "primary"');
    process.exit(1);
  }

  const meetingStartedWebhookUrl = "direct:vexa";
  const meetingPrepWebhookUrl = await secrets.get("MEETING_PREP_URL");
  const meetingFinishedWebhookUrl = await secrets.get("MEETING_FINISHED_WEBHOOK_URL");
  const hmacSecret = await secrets.get("HERMES_WEBHOOK_SECRET");
  const dispatchSlackToken = await secrets.get("SLACK_BOT_TOKEN");
  const dispatchSlackChannel = await secrets.get("SLACK_CHANNEL_ID");
  async function alertDispatchExhausted(phase, payload, decision) {
    const heading = ["postcall-needs-review", "signal-materialization-needs-review", "signal-capped-needs-review", "publication-needs-review", "prior-capture-needs-review"].includes(decision.reason) ? "Vexa post-call processing needs review" : "Meeting workflow dispatch exhausted";
    const text = `${heading}\nPhase: ${phase}\nEvent: ${payload.title || "Untitled"}\nEvent ID: ${payload.id}\nAttempts: ${decision.claim?.attempt || "unknown"}\nReason: ${decision.reason}`;
    const sent = await sendSlackAlert(dispatchSlackToken, dispatchSlackChannel, text);
    if (!sent) throw new Error("Slack dispatch alert not configured");
    mainLog("dispatch exhaustion alert sent", { phase, eventId: payload.id, reason: decision.reason });
  }

  if (!hmacSecret) {
    log("ERROR", "HERMES_WEBHOOK_SECRET is required");
    process.exit(1);
  }

  mainLog("webhook URLs and HMAC secret loaded", {
    hasMeetingStartedWebhookUrl: !!meetingStartedWebhookUrl,
    hasMeetingPrepWebhookUrl: !!meetingPrepWebhookUrl,
    hasMeetingFinishedWebhookUrl: !!meetingFinishedWebhookUrl,
    hasHmacSecret: !!hmacSecret,
  });

  // Claims outlive calendar scan windows and calendar-provider outages.
  const outstandingClaims = listOutstandingClaims(DISPATCH_CLAIM_DIR);
  for (const claim of outstandingClaims) {
    const claimedPayload = claim.payload || { id: claim.event_id, title: claim.title || "Untitled meeting" };
    const urls = {
      prep: meetingPrepWebhookUrl,
      start: meetingStartedWebhookUrl,
      finish: meetingFinishedWebhookUrl,
    };
    try {
      await dispatchPhaseWebhook({
        phase: claim.phase,
        eventId: claim.event_id,
        payload: claimedPayload,
        url: urls[claim.phase],
        secret: hmacSecret,
        mainLog,
        onExhausted: decision => alertDispatchExhausted(claim.phase, claimedPayload, decision),
      });
    } catch (error) {
      log("ERROR", `(${mainStep}) outstanding ${claim.phase} dispatch failed  event_id=${claim.event_id}  err=${error.message}`);
    }
  }

  const calendarProvider = new OAuthCalendarProvider(secrets);
  mainLog("calendar provider created", calendarProvider.constructor.name);

  await calendarProvider.init();
  mainLog("calendar provider initialized");

  // Authenticated Vexa owns its browser profile; legacy Chrome-profile checks are not applicable.
  const loginReceipt = path.join(HERMES_HOME, "auth-readiness", "google-login-success.json");
  if (!fs.existsSync(loginReceipt)) log("WARN", "Vexa login receipt unavailable; run local Google login before joining");

  const now = new Date();
  const upcomingWindowStart = now;
  const upcomingWindowEnd = new Date(Date.now() + 10 * 60 * 1000);
  const prepWindowStart = now;
  const prepWindowEnd = new Date(Date.now() + 45 * 60 * 1000);
  const finishedWindowStart = new Date(Date.now() - 2 * 60 * 60 * 1000);
  const finishedWindowEnd = now;

  mainLog("scan windows computed", {
    now: now.toISOString(),
    upcomingWindowStart: upcomingWindowStart.toISOString(),
    upcomingWindowEnd: upcomingWindowEnd.toISOString(),
    prepWindowStart: prepWindowStart.toISOString(),
    prepWindowEnd: prepWindowEnd.toISOString(),
    finishedWindowStart: finishedWindowStart.toISOString(),
    finishedWindowEnd: finishedWindowEnd.toISOString(),
  });

  const [upcomingEvents, prepCandidateEvents, recentlyEndedEvents] = await Promise.all([
    calendarProvider.getEventsBetween(calendarId, upcomingWindowStart, upcomingWindowEnd),
    calendarProvider.getEventsBetween(calendarId, prepWindowStart, prepWindowEnd),
    calendarProvider.getEventsBetween(calendarId, finishedWindowStart, finishedWindowEnd),
  ]);

  mainLog("upcoming events fetched", { count: upcomingEvents.length });
  mainLog("prep candidate events fetched", { count: prepCandidateEvents.length });
  mainLog("recently ended events fetched", { count: recentlyEndedEvents.length });

  const prepMeetings = filterPrepMeetings(prepCandidateEvents, calendarOwnerEmail);
  const startingMeetings = filterStartingMeetings(upcomingEvents, calendarOwnerEmail);
  const inProgressMeetings = filterInProgressMeetings(upcomingEvents, calendarOwnerEmail);
  const finishedMeetings = filterFinishedMeetings(recentlyEndedEvents, calendarOwnerEmail);
  const { filterRejoinOverrunMeetings, shouldStartVexaRejoin, startVexaRejoinWorker } = require("./vexa-rejoin");
  const overrunMeetings = filterRejoinOverrunMeetings(recentlyEndedEvents.filter(event => (
    event.status !== "cancelled" && isOurCalendarInvolved(event, calendarOwnerEmail) && hasVideoMeeting(event)
  )), now.getTime());
  const rejoinMeetings = [...new Map([
    ...inProgressMeetings,
    ...overrunMeetings,
  ].map(event => [event.id, event])).values()];

  mainLog("meeting filters completed", {
    prepMeetings: prepMeetings.length,
    startingMeetings: startingMeetings.length,
    inProgressMeetings: inProgressMeetings.length,
    rejoinMeetings: rejoinMeetings.length,
    finishedMeetings: finishedMeetings.length,
  });

  const state = loadState();
  const deferred = state.deferred_finalizations || (state.deferred_finalizations = {});

  // Evict stale deferred entries older than 24 h
  const nowMs = Date.now();
  for (const [eventId, entry] of Object.entries(deferred)) {
    if (nowMs - entry.first_deferred_at > STALE_DEFERRED_TTL_MS) {
      mainLog(`evicting stale deferred entry`, { eventId, title: entry.title });
      delete deferred[eventId];
    }
  }

  // ── PREP ────────────────────────────────────────────────────────────────
  if (prepMeetings.length > 0) {
    const payload = normalizeEvent(prepMeetings[0]);
    const eventId = payload.id;
    const prepLock = checkPrepLock(eventId);

    if (prepLock.skip) {
      mainLog("prep skipped by lock", { eventId, reason: prepLock.reason });
    } else {
      if (meetingPrepWebhookUrl) {
        try {
          await dispatchPhaseWebhook({
            phase: "prep",
            eventId,
            payload,
            url: meetingPrepWebhookUrl,
            secret: hmacSecret,
            mainLog,
            onExhausted: decision => alertDispatchExhausted("prep", payload, decision),
          });
        } catch (e) {
          log("ERROR", `(${mainStep}) prep webhook failed  id=${eventId}  err=${e.message}`);
        }
      } else {
        log("WARN", `(${mainStep}) MEETING_PREP_URL not set — prep webhook skipped`);
      }
    }
  } else {
    mainLog("no actionable prep meetings found");
  }

  // ── BOUNDED LEFT-ALONE / EVICTION REJOIN ─────────────────────────────────
  // A terminal capture through the bounded overrun window gets one rejoin. The worker waits
  // until post-call evidence is preserved before archiving that capture and dispatching afresh.
  for (const event of rejoinMeetings) {
    const payload = normalizeEvent(event);
    const rejoin = shouldStartVexaRejoin(MEETINGS_DIR, payload, nowMs);
    if (rejoin.start) {
      const pid = startVexaRejoinWorker(rejoin.dir);
      mainLog("bounded Vexa rejoin worker started", { eventId: payload.id, reason: rejoin.reason, pid });
    }
  }

  // ── STARTING ────────────────────────────────────────────────────────────
  if (startingMeetings.length > 0) {
    const payload = normalizeEvent(startingMeetings[0]);
    const eventId = payload.id;
    const joinLock = checkJoinLock(eventId);

    if (joinLock.hardBlock) {
      mainLog("join permanently blocked — auth failure", { eventId, reason: joinLock.reason });
      const alertedPath = path.join(MEETINGS_DIR, eventId, ".auth_alerted");
      if (!fs.existsSync(alertedPath)) {
        const slackToken = await secrets.get("SLACK_BOT_TOKEN");
        const slackChannel = await secrets.get("SLACK_CHANNEL_ID");
        const alertMsg = `:rotating_light: *Meeting bot auth failure*\nEvent: *${payload.title}*\nEvent ID: \`${eventId}\`\nFailure: \`${joinLock.authFailReason}\`\n\nAction required: Re-authenticate Google Meet bot via VNC session.`;
        try {
          await sendSlackAlert(slackToken, slackChannel, alertMsg);
          fs.mkdirSync(path.join(MEETINGS_DIR, eventId), { recursive: true });
          fs.writeFileSync(alertedPath, JSON.stringify({ alerted_at: new Date().toISOString(), reason: joinLock.authFailReason }));
          mainLog("Slack auth failure alert sent", { eventId });
        } catch (e) {
          log("ERROR", `(${mainStep}) failed to send Slack auth alert: ${e.message}`);
        }
      } else {
        mainLog("auth failure alert already sent — not repeating", { eventId });
      }
    } else if (joinLock.skip) {
      mainLog("join skipped by lock", { eventId, reason: joinLock.reason });
    } else {
      if (meetingStartedWebhookUrl) {
        try {
          await dispatchPhaseWebhook({
            phase: "start",
            eventId,
            payload,
            url: meetingStartedWebhookUrl,
            secret: hmacSecret,
            mainLog,
            onExhausted: decision => alertDispatchExhausted("start", payload, decision),
          });
        } catch (e) {
          log("ERROR", `(${mainStep}) starting webhook failed  id=${eventId}  err=${e.message}`);
        }
      } else {
        log("WARN", `(${mainStep}) WEBHOOK_URL not set — starting webhook skipped`);
      }
    }
  } else {
    mainLog("no actionable starting meetings found");
  }

  // ── FINISHED  (with deferred backoff) ───────────────────────────────────

  // Re-check deferred entries whose backoff window has elapsed
  for (const [eventId, entry] of Object.entries(deferred)) {
    const vexaReadiness = require("./vexa-finish-gate").inspectVexaFinish(MEETINGS_DIR, eventId);
    const vexaEnded = !["legacy-meeting", "vexa-still-capturing"].includes(vexaReadiness.reason);
    if (nowMs < entry.next_check_at && !vexaEnded) {
      const remainingMin = ((entry.next_check_at - nowMs) / 60000).toFixed(1);
      debugLog(`deferred ${eventId}: next check in ${remainingMin} min`);
      continue;
    }

    // Check finalize locks before activity check — agent may have already handled it
    const finLock = checkFinalizeLock(eventId);
    if (finLock.skip) {
      if (fs.existsSync(path.join(MEETINGS_DIR, eventId, ".finalized"))) {
        mainLog("deferred: meeting already finalized — cleaning up", { eventId, title: entry.title });
        delete deferred[eventId];
      } else {
        mainLog("deferred: finalization in progress — waiting", { eventId, title: entry.title, reason: finLock.reason });
      }
      continue;
    }

    // Skip if bot never joined — no transcript, nothing to finalize
    const neverJoinedDeferred = checkBotNeverJoined(eventId);
    if (neverJoinedDeferred.skip) {
      mainLog("deferred: bot never joined — skipping finalize webhook and evicting", { eventId, title: entry.title, reason: neverJoinedDeferred.reason });
      delete deferred[eventId];
      continue;
    }

    // First-layer guard: skip cancelled meetings before touching any webhook
    if (fs.existsSync(path.join(MEETINGS_DIR, eventId, ".cancelled"))) {
      mainLog("deferred: meeting cancelled — skipping finalize webhook and evicting", { eventId, title: entry.title });
      delete deferred[eventId];
      continue;
    }

    if (isMeetingStillActive(eventId)) {
      entry.defer_count += 1;
      entry.next_check_at = nowMs + BACKOFF_MS;
      mainLog("deferred meeting still active — backoff", { eventId, title: entry.title, backoffCount: entry.defer_count, nextCheckIn: "15min" });
    } else {
      mainLog("deferred meeting now quiet — evaluating finished dispatch", { eventId, title: entry.title, totalBackoffs: entry.defer_count });
      if (meetingFinishedWebhookUrl) {
        try {
          const result = await dispatchPhaseWebhook({
            phase: "finish",
            eventId,
            payload: entry.payload,
            url: meetingFinishedWebhookUrl,
            secret: hmacSecret,
            mainLog,
            onExhausted: decision => alertDispatchExhausted("finish", entry.payload, decision),
          });
          if (result.sent || result.decision?.reason === "workflow-terminal") {
            delete deferred[eventId];
          }
        } catch (e) {
          log("ERROR", `(${mainStep}) finished webhook failed (deferred)  event_id=${eventId}  err=${e.message}`);
        }
      } else {
        log("WARN", `(${mainStep}) MEETING_FINISHED_WEBHOOK_URL not set — skipping deferred ${eventId}`);
        delete deferred[eventId];
      }
    }
  }

  // Handle newly detected finished meetings from this calendar scan
  for (const event of finishedMeetings) {
    const eventId = event.id;
    if (!eventId || eventId in deferred) continue;

    // Skip if agent already handled or is currently handling finalization
    const finLock = checkFinalizeLock(eventId);
    if (finLock.skip) {
      mainLog("finished meeting: skipped by finalize lock", { eventId, reason: finLock.reason });
      continue;
    }

    // Skip if bot never joined — no transcript, nothing to finalize
    const neverJoined = checkBotNeverJoined(eventId);
    if (neverJoined.skip) {
      mainLog("finished meeting: bot never joined — skipping finalize webhook", { eventId, reason: neverJoined.reason });
      continue;
    }

    // First-layer guard: skip cancelled meetings before touching any webhook
    if (fs.existsSync(path.join(MEETINGS_DIR, eventId, ".cancelled"))) {
      mainLog("finished meeting: cancelled — skipping finalize webhook", { eventId });
      continue;
    }

    const payload = normalizeEvent(event);

    if (isMeetingStillActive(eventId)) {
      deferred[eventId] = {
        title: payload.title,
        first_deferred_at: nowMs,
        defer_count: 1,
        next_check_at: nowMs + BACKOFF_MS,
        payload,
      };
      mainLog("meeting still active past scheduled end — deferring", { eventId, title: payload.title, nextCheckIn: "15min" });
    } else {
      mainLog("evaluating meeting finished dispatch", { eventId, title: payload.title });
      if (meetingFinishedWebhookUrl) {
        try {
          await dispatchPhaseWebhook({
            phase: "finish",
            eventId,
            payload,
            url: meetingFinishedWebhookUrl,
            secret: hmacSecret,
            mainLog,
            onExhausted: decision => alertDispatchExhausted("finish", payload, decision),
          });
        } catch (e) {
          log("ERROR", `(${mainStep}) finished webhook failed  event_id=${eventId}  err=${e.message}`);
        }
      } else {
        log("WARN", `(${mainStep}) MEETING_FINISHED_WEBHOOK_URL not set — finished webhook skipped`);
      }
    }
  }

  state.last_run_at = now.toISOString();
  saveState(state);
  mainLog("run complete", { totalSteps: mainStep });
}

if (require.main === module) {
  main().catch((err) => {
    log("ERROR", `unhandled error: ${err.message}\n${err.stack}`);
    process.exit(1);
  });
}

module.exports = { main, normalizeEvent, consentEligible, filterPrepMeetings, filterStartingMeetings, filterFinishedMeetings, isWithinPrepWindow, isWithinJoinWindow, dispatchPhaseWebhook, dispatchPolicyFor, filterInProgressMeetings, sendWebhook, checkBotNeverJoined };

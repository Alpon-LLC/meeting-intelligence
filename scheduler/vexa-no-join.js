"use strict";
const fs = require("fs");
const path = require("path");
function read(file) { try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return null; } }

// Null start_time is not evidence of non-admission. Require the producer's explicit
// verdict AND agreement with the currently requested occurrence.
function inspectVexaNoJoin(dir) {
  const state = read(path.join(dir, "transcription.state.json"));
  const response = read(path.join(dir, "vexa.bot.response.json"));
  const meeting = read(path.join(dir, "vexa.lifecycle.json"))?.meeting;
  const ids = [state?.vexa_bot_id, response?.id ?? response?.bot_id].filter(id => id != null);
  if (!meeting || !ids.length || !ids.every(id => String(id) === String(meeting.id)) ||
      !["failed", "completed"].includes(meeting.status)) return null;
  const provenance = meeting.service_provenance || meeting.data?.service_provenance;
  if (provenance?.bot_outcome !== "never_admitted" || meeting.start_time ||
      (meeting.data?.status_transition || []).some(item => item.to === "active")) return null;
  const protectedEvidence = fs.readdirSync(dir).some(name =>
    name === "captures" || name.startsWith("transcript.") || name.startsWith("transcript-") ||
    [".captured-signal.jsonl", ".transcript.jsonl", ".stt.jsonl"].some(suffix => name.endsWith(suffix)) ||
    ["notion-page.json", ".finalized", "triage-context.json", ".vexa-publication.json"].includes(name)) ||
    [meeting, meeting.data, meeting.service_provenance, meeting.data?.service_provenance]
      .some(evidence => evidence?.transcription_outcome === "served" || Number(evidence?.segments_captured) > 0);
  const reason = meeting.completion_reason || meeting.data?.completion_reason;
  return { reason: protectedEvidence ? "prior-capture-needs-review" :
    reason === "auth_session_missing" ? "vexa-auth-required-never-admitted" : "vexa-never-admitted",
    noJoin: !protectedEvidence, meetingId: String(meeting.id), dir };
}
module.exports = { inspectVexaNoJoin };

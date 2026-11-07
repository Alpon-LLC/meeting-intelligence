"use strict";
// Translate attendee-local configuration to the original scheduler/skill environment.
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');
function load() {
  const file = process.env.MEETING_CONFIG || path.join(root, 'private/config.json');
  if (!fs.existsSync(file)) return;
  const config = JSON.parse(fs.readFileSync(file, 'utf8'));
  const home = path.resolve(config.hermes_home || path.join(root, 'private/hermes'));
  const values = {
    HERMES_HOME: home,
    MEETING_RECORDING_CONSENT: config.recording_consent_confirmed === true ? 'true' : 'false',
    MEETING_CONSENT_MARKER: config.consent_marker || '[record]',
    CALENDAR_ID: config.calendar_id || 'primary',
    CALENDAR_OWNER_EMAIL: config.calendar_owner_email,
    VEXA_API_ENDPOINT: config.vexa_url,
    VEXA_API_KEY_FILE: config.vexa_key_file,
    VEXA_BOT_NAME: config.bot_name,
    HERMES_MEET_BACKEND: 'vexa-primary',
    MEETING_PREP_URL: config.prep_webhook_url || 'http://127.0.0.1:8644/webhooks/meeting-prep',
    MEETING_FINISHED_WEBHOOK_URL: config.finish_webhook_url || 'http://127.0.0.1:8644/webhooks/meeting-finished',
    MEETING_STT_URL: config.stt_url || 'http://127.0.0.1:19083',
    MEETING_STT_KEY_FILE: config.stt_key_file,
    MEETING_STT_MODEL: config.stt_model || 'whisper-1',
    MEETING_NOTION_ENABLED: config.notion_enabled ? 'true' : 'false',
    MEETING_SLACK_ENABLED: config.slack_enabled ? 'true' : 'false',
    MEETING_TASKS_ENABLED: config.tasks_enabled ? 'true' : 'false',
    NOTION_NOTES_DATA_SOURCE_ID: config.notion_notes_data_source_id,
    NOTION_TASKS_DATA_SOURCE_ID: config.notion_tasks_data_source_id,
    NOTION_PROJECTS_DATA_SOURCE_ID: config.notion_projects_data_source_id,
    NOTION_PARENT_PAGE_ID: config.notion_parent_page_id,
    MEETING_INTERNAL_DOMAIN: config.internal_domain,
    CALENDAR_TOKEN_FILE: config.calendar_token_file,
    MEETING_EXECUTOR_SCRIPT: path.join(root, 'scheduler/executor-receipt.js'),
  };
  for (const [k,v] of Object.entries(values)) if (v != null && process.env[k] == null) process.env[k] = String(v);
  for (const [key,field] of [['HERMES_WEBHOOK_SECRET','webhook_key_file'],['NOTION_API_TOKEN','notion_token_file']]) {
    if (config[field] && fs.existsSync(config[field]) && !process.env[key]) process.env[key] = fs.readFileSync(config[field],'utf8').trim();
  }
  const tokenFile = config.calendar_token_file;
  if (tokenFile && fs.existsSync(tokenFile)) {
    const token = JSON.parse(fs.readFileSync(tokenFile,'utf8'));
    process.env.CLIENT_ID ||= token.client_id || '';
    process.env.CLIENT_SECRET ||= token.client_secret || '';
    process.env.REFRESH_TOKEN ||= token.refresh_token || '';
  }
  // Persist only named non-secret env entries; actual keys remain profile-local files/env.
  fs.mkdirSync(path.join(home,'workspace','meetings'),{recursive:true,mode:0o700});
}
module.exports = { load };

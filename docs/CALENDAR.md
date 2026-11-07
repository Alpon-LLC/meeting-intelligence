# Your Google Calendar authorization

The scheduler uses ordinary OAuth, not Domain-Wide Delegation or X.509. Vexa's Google login only lets its browser join Meet; it does not authorize Calendar API calls.

Human steps (Devin may guide, not impersonate you):
1. In your own Google Cloud project, enable Google Calendar API.
2. Configure the OAuth consent screen. Choose the audience appropriate for your account. In testing mode add your Google account as a test user.
3. Create an OAuth client of type **Desktop app**. Download its JSON to `private/google-oauth-client.json` (gitignored).
4. Run `meeting_app.py calendar-login --client-file private/google-oauth-client.json` using the installed app venv. Approve `calendar.events.readonly` in your browser.
5. Keep `private/calendar-token.json` private. OAuth test-mode refresh-token expiry and organization account policies may require reauthorization. Do not bypass an organization's restrictions.
6. The default calendar is `primary`; change `calendar_id` in `private/config.json` only to a calendar your authorized account can read.

Use the same Google account for Vexa and Calendar if convenient, but these are separate grants. Google sign-in/MFA, Cloud project/client creation and consent cannot be silently completed by Devin. Do not use a shared organizer OAuth client embedded in this public repo; each attendee supplies their own unless the event owner separately establishes a properly authorized application.

Only timed events with a valid HTTPS Google Meet URL and `[record]` in the title are eligible. Cancelled and self-declined events are skipped. Add the marker only after actual participant agreement. It is an opt-in selector, not an automatic legal-consent mechanism. The original JS scheduler runs via npm start while the process/device is awake.

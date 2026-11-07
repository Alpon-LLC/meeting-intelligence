# Security

This is a local development release candidate, not a hardened multi-tenant service. Do not expose API, admin, object storage or VNC ports to the internet. Host-facing ports bind loopback. Vexa runtime receives the Docker socket and therefore effectively controls that Docker host. Use an isolated laptop/development engine only.

Run setup with ordinary user permissions; Docker permission is explicitly privileged. Do not disable command approvals globally. Human performs Google sign-in/MFA and account grants. Keep .env/private/.build/venvs out of Git and public archives. The login viewer is loopback-only but not password-authenticated; stop it when done and don't run it on a shared host.

Treat meeting titles, transcripts and model output as untrusted data. Consent and remote-publication checks are distinct. Unknown write outcomes stop for review. Model output is a draft, not an authorized task list.

Report issues privately to the repository maintainer through the GitHub private vulnerability-reporting feature if enabled; otherwise request a private contact without posting exploit details, tokens or real meeting data. No security certification is claimed. Public release requires dependency/license/secret review and fresh live acceptance.

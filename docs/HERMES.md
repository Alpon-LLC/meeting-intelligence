# Isolated Hermes setup

Hermes is the only supported agent target. Use a new local HERMES_HOME, never copy the authors' profile.

The upstream commit selected for this draft is `93dd24359dc011283a02098af84f63404f72ad15` from
https://github.com/NousResearch/hermes-agent. It has not yet had a full clean-install rehearsal with this package.
The install helper below fetches that exact source, creates an isolated venv and installs it. Do not install from an unreviewed curl script.

Run `python3 scripts/install_hermes.py`, then follow the printed `hermes setup` command to authorize your own model provider.
Model API calls may incur cost and transmit transcripts to that provider. Local Vexa transcription does not make the summary model local.
The model must support tools and file access. No Notion, Slack or Google admin credentials belong in Hermes chat.

Run `source ./activate.sh` in the same terminal before meeting_app.py so the scheduler and spawned Hermes use the isolated home.
The three generic skills are installed there. There is no legacy google_meet plugin dependency: the scheduler talks to Vexa explicitly.

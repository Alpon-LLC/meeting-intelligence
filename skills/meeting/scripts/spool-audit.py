#!/usr/bin/env python3
"""Audit the meet-bot audio spool + capture health for recent meetings.

Answers: "is the audio spool blocking recording right now, for how long, and which
calls already lost their recording?"

Mirrors AudioSpool._pending_recordings() in
plugins/google_meet/transcription_pipeline.py so the printed count matches what the
bot's capacity check sees.

Usage:
    python3 spool-audit.py [meetings_root] [--limit N]

Defaults: ~/.hermes/workspace/meetings, N=20.
Exit code 0 = capacity available; 1 = spool full (capture is being declined).
"""

from pathlib import Path
import json
import pathlib
import shutil
import sys

AUDIO_FILENAME = "meeting-audio.webm"
AUDIO_PART_FILENAME = f"{AUDIO_FILENAME}.part"
AUDIO_READY_FILENAME = "audio.ready.json"
MAX_PENDING_RECORDINGS = 5  # AudioSpoolPolicy default; env-overridable in the plugin




def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = pathlib.Path(args[0] if args else str(Path.home()/".hermes/workspace/meetings"))
    limit = 20
    for i, a in enumerate(sys.argv[1:]):
        if a == "--limit" and i + 2 <= len(sys.argv) - 1:
            limit = int(sys.argv[i + 2])

    if not root.is_dir():
        print(f"meetings root not found: {root}")
        return 2

    # --- spool occupancy (exactly what the bot counts) ---
    counted = set(root.glob(f"*/{AUDIO_FILENAME}")) | set(root.glob(f"*/{AUDIO_PART_FILENAME}"))
    counted = sorted(p for p in counted if p.is_file())
    pending = len(counted)

    print(f"== SPOOL ({pending}/{MAX_PENDING_RECORDINGS}) — root {root}")
    for p in counted:
        d = p.parent
        ready = (d / AUDIO_READY_FILENAME).is_file()
        part = p.name.endswith(".part")
        print(f"   {p.stat().st_size:>12,} B  {p}"
              f"\n        finalized={ready} orphan_partial={part}"
              f"{'  <-- never finalizable, reaping candidate' if part and not ready else ''}")
    if pending >= MAX_PENDING_RECORDINGS:
        print("   >>> SPOOL FULL — every new meeting's audio capture is being DECLINED.")
        print("   >>> Clearing is a DELETION of captured data: audit, report, ASK before removing.")

    # --- per-meeting capture health, newest first ---
    dirs = sorted((d for d in root.iterdir() if d.is_dir() and d.name != "chrome-profile"),
                  key=lambda d: d.stat().st_mtime, reverse=True)[:limit]

    print(f"\n== RECENT MEETINGS (newest {len(dirs)})")
    lost = []
    for d in dirs:
        status_path = d / "status.json"
        st = {}
        if status_path.is_file():
            try:
                st = json.loads(status_path.read_text())
            except ValueError:
                st = {}
        tj = d / "transcript.json"
        lines = 0
        if tj.is_file():
            lines = sum(1 for ln in tj.read_text().splitlines() if ln.strip())
        err = st.get("audioRecordingError")
        rec = st.get("audioRecording")
        print(f"   {d.name}"
              f"\n        inCall={st.get('inCall')} exited={st.get('exited')}"
              f" leaveReason={st.get('leaveReason')}"
              f" transcript_lines={lines}"
              f"\n        audioRecording={rec} bytes={st.get('audioRecordingBytes')}")
        if err:
            print(f"        audioRecordingError={err.splitlines()[0]}")
            lost.append(d.name)
        if st.get("lastCaptionAt"):
            print(f"        lastCaptionAt={st['lastCaptionAt']}  (compare to meeting end)")

    print(f"\n== SUMMARY: {pending}/{MAX_PENDING_RECORDINGS} spool slots used; "
          f"{len(lost)} recent meeting(s) with a declined/failed recording")
    for name in lost:
        print(f"   recording lost: {name}")

    try:
        print(f"\n== DISK free={shutil.disk_usage(root).free:,} B")
    except OSError:
        pass

    return 1 if pending >= MAX_PENDING_RECORDINGS else 0


if __name__ == "__main__":
    sys.exit(main())

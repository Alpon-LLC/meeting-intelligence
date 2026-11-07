#!/usr/bin/env python3
"""Build bounded Notion-derived vocabulary for a meeting without blocking bot admission."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "meeting-stt-context-input.v1"
NOTION_VERSION = "2025-09-03"
TASKS_DATA_SOURCE_ID = os.environ.get("NOTION_TASKS_DATA_SOURCE_ID", "")
INTERNAL_DOMAINS = tuple("@" + v.strip() for v in os.getenv("MEETING_INTERNAL_DOMAINS", "").split(",") if v.strip())
BROAD_MEETING = re.compile(r"\b(daily|standup|sprint|backlog|retrospective|retro|planning)\b", re.I)
GENERIC_TITLE_WORDS = frozenset({
    "and", "backlog", "call", "daily", "demo", "meeting", "planning", "retro",
    "retrospective", "sprint", "standup", "sync", "the", "weekly", "with",
})


def request_json(token: str, path: str, body: dict[str, Any] | None = None, timeout: int = 5) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.notion.com/v1/{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method="POST" if body is not None else "GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
            "User-Agent": "ExampleOrg-Vexa-Context/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise ValueError("Notion returned a non-object response")
    return value


def plain_title(properties: dict[str, Any]) -> str | None:
    preferred = ("Project name", "Client name", "Name", "Task name", "Title")
    ordered = [properties.get(name) for name in preferred]
    ordered.extend(value for key, value in properties.items() if key not in preferred)
    for prop in ordered:
        if not isinstance(prop, dict):
            continue
        title = prop.get("title")
        if isinstance(title, list):
            text = "".join(str(item.get("plain_text") or "") for item in title if isinstance(item, dict)).strip()
            if text:
                return text
    return None


def clean_term(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+-\s+created by John\s*$", "", value, flags=re.I)
    text = re.sub(r"^(?:\s*\[[^]]+\])+\s*", "", text)
    text = " ".join(text.split()).strip(" -:;")
    return text if 2 <= len(text) <= 80 else None


def title_tokens(value: str) -> set[str]:
    return {
        token.casefold() for token in re.findall(r"[A-Za-z0-9]+", value)
        if len(token) >= 3 and token.casefold() not in GENERIC_TITLE_WORDS
    }


def relevant(value: str, meeting_tokens: set[str], broad: bool) -> bool:
    return broad or bool(title_tokens(value) & meeting_tokens)


def build_terms(
    event: dict[str, Any],
    users: dict[str, str],
    tasks: list[dict[str, Any]],
    projects: dict[str, dict[str, Any]],
    clients: dict[str, dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    title = str(event.get("title") or "")
    broad = bool(BROAD_MEETING.search(title))
    mode = "attendee_tasks" if broad else "title_matched"
    meeting_tokens = title_tokens(title)
    attendee_ids = {
        users[email.casefold()]
        for attendee in event.get("attendees", []) if isinstance(attendee, dict)
        for email in [attendee.get("email")]
        if isinstance(email, str) and email.casefold().endswith(INTERNAL_DOMAINS) and email.casefold() in users
    }
    candidates: list[dict[str, Any]] = []
    matched_projects: set[str] = set()

    for task in tasks:
        if not attendee_ids.intersection(task.get("assignee_ids", [])):
            continue
        task_title = clean_term(task.get("title"))
        project_ids = [str(value) for value in task.get("project_ids", [])]
        project_pairs = [
            (pid, clean_term(plain_title(projects[pid].get("properties", {}))))
            for pid in project_ids if pid in projects
        ]
        project_names = [name for _, name in project_pairs]
        task_relevant = bool(task_title and relevant(task_title, meeting_tokens, broad))
        project_relevant = any(name and relevant(name, meeting_tokens, broad) for name in project_names)
        if not (task_relevant or project_relevant):
            continue
        if task_title:
            candidates.append({"text": task_title, "source": "agenda", "priority": 72})
        for pid, project_name in project_pairs:
            if not project_name:
                continue
            matched_projects.add(pid)
            candidates.append({"text": project_name, "source": "project", "priority": 94})

    for project_id in matched_projects:
        properties = projects.get(project_id, {}).get("properties", {})
        for name, prop in properties.items():
            if "client" not in name.casefold() and "customer" not in name.casefold():
                continue
            for relation in prop.get("relation", []) if isinstance(prop, dict) else []:
                client = clients.get(str(relation.get("id"))) if isinstance(relation, dict) else None
                client_name = clean_term(plain_title(client.get("properties", {}))) if client else None
                if client_name:
                    candidates.append({"text": client_name, "source": "external_organization", "priority": 97})

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in sorted(candidates, key=lambda item: -item["priority"]):
        folded = candidate["text"].casefold()
        if folded in seen:
            continue
        seen.add(folded)
        selected.append(candidate)
        if len(selected) >= 40:
            break
    return mode, selected


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def approved_preflight(event: dict[str, Any], path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("operator_preflight") is not True or value.get("version") != VERSION:
        return None
    if (value.get("event_id"), value.get("event_start"), value.get("event_title")) != (
        event.get("event_id") or event.get("id"), event.get("start"), event.get("title")
    ) or not all(value.get(k) for k in ("event_id", "event_start", "event_title")):
        return None
    terms = value.get("terms")
    if not isinstance(terms, list) or not 1 <= len(terms) <= 40:
        return None
    if any(not isinstance(t, dict) or t.get("source") not in {"project", "organization", "product"}
           or not isinstance(t.get("text"), str) or not 2 <= len(t["text"]) <= 80
           or any(ord(c) < 32 or ord(c) == 127 for c in t["text"])
           or type(t.get("priority")) is not int or not 0 <= t["priority"] <= 100 for t in terms):
        return None
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-meta", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=5)
    args = parser.parse_args()
    event = json.loads(args.event_meta.read_text())
    preflight = approved_preflight(event, args.output)
    if preflight:
        print(json.dumps({"status": "completed", "mode": "approved_event_preflight", "term_count": len(preflight["terms"])}))
        return 0
    token = os.environ.get("NOTION_API_TOKEN") or os.environ.get("NOTION_API_KEY")
    if not token:
        print(json.dumps({"status": "unavailable", "reason": "notion_token_missing"}))
        return 2
    users_response = request_json(token, "users?page_size=100", timeout=args.timeout)
    users = {
        str(item.get("person", {}).get("email")).casefold(): str(item.get("id"))
        for item in users_response.get("results", []) if isinstance(item, dict)
        if item.get("type") == "person" and item.get("person", {}).get("email") and item.get("id")
    }
    attendee_ids = sorted({
        users[email.casefold()]
        for attendee in event.get("attendees", []) if isinstance(attendee, dict)
        for email in [attendee.get("email")]
        if isinstance(email, str) and email.casefold() in users
    })
    if not attendee_ids:
        print(json.dumps({"status": "completed", "mode": "none", "term_count": 0}))
        return 0
    task_filter: dict[str, Any] = {"property": "Assignee", "people": {"contains": attendee_ids[0]}}
    if len(attendee_ids) > 1:
        task_filter = {"or": [{"property": "Assignee", "people": {"contains": value}} for value in attendee_ids]}
    tasks_response = request_json(token, f"data_sources/{TASKS_DATA_SOURCE_ID}/query", {
        "filter": {"and": [
            task_filter,
            {"property": "Status", "status": {"does_not_equal": "Done"}},
            {"property": "Status", "status": {"does_not_equal": "Archived"}},
        ]},
        "page_size": 100,
    }, args.timeout)
    tasks: list[dict[str, Any]] = []
    project_ids: set[str] = set()
    for row in tasks_response.get("results", []):
        if not isinstance(row, dict):
            continue
        props = row.get("properties", {})
        title = plain_title(props)
        assignees = [str(value.get("id")) for value in props.get("Assignee", {}).get("people", []) if isinstance(value, dict)]
        relations = [str(value.get("id")) for value in props.get("Project", {}).get("relation", []) if isinstance(value, dict)]
        project_ids.update(relations)
        tasks.append({"title": title, "assignee_ids": assignees, "project_ids": relations})
    projects = {pid: request_json(token, f"pages/{pid}", timeout=args.timeout) for pid in sorted(project_ids)[:20]}
    client_ids: set[str] = set()
    for project in projects.values():
        for name, prop in project.get("properties", {}).items():
            if isinstance(prop, dict) and ("client" in name.casefold() or "customer" in name.casefold()):
                client_ids.update(str(value.get("id")) for value in prop.get("relation", []) if isinstance(value, dict))
    clients = {cid: request_json(token, f"pages/{cid}", timeout=args.timeout) for cid in sorted(client_ids)[:20]}
    mode, terms = build_terms(event, users, tasks, projects, clients)
    source_ids = sorted({str(row.get("id")) for row in tasks_response.get("results", []) if isinstance(row, dict)})
    value = {
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": mode,
        "terms": terms,
        "source_fingerprint": "sha256:" + hashlib.sha256(json.dumps(source_ids, separators=(",", ":")).encode()).hexdigest(),
    }
    atomic_json(args.output, value)
    print(json.dumps({"status": "completed", "mode": mode, "term_count": len(terms)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Text the student their Groupie status: next deadlines, their tasks, and the next meeting.

Reads the token saved by link.py (~/.config/groupie/token), GETs <base>/api/cubbie/me with it, and prints a short
plain-text summary (under 1,500 characters) for an iMessage reply. With --unlink it disconnects instead
(DELETE <base>/api/cubbie/link) and deletes the saved token. The token is never printed.

    python3 scripts/status.py
    python3 scripts/status.py --unlink
    python3 scripts/status.py --sample   # prints the bundled sample, reads and sends nothing

Exit codes:
  0 printed the status (or unlinked)
  2 bad arguments, or the sample file is unreadable
  5 Groupie's server could not be reached (after one retry)
  10 too many checks, wait a few minutes (429)
  11 any other server error
  12 not linked: no saved token, or Groupie no longer accepts it (the saved token is then deleted)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import groupie_link as g  # noqa: E402

send = g.send
SAMPLE = Path(__file__).resolve().parent.parent / "assets" / "sample_status.json"
MAX_CHARS = 1500
MAX_TASKS = 5
MAX_ITEMS = 5
STATUS_WORDS = {"todo": "to do", "on_track": "on track", "stuck": "stuck", "done": "done"}
MODE_WORDS = {"zoom": "Zoom", "meet": "Google Meet", "in_person": "in person"}


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _list(value) -> list:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def group_lines(group: dict) -> list:
    title = _text(group.get("title")) or "Your group"
    assignment = _text(group.get("assignmentTitle"))
    lines = [f"{title}: {assignment}" if assignment else title]
    due, days = _text(group.get("dueLabel")), group.get("daysLeft")
    if due:
        left = ""
        if isinstance(days, int):
            left = " (today)" if days == 0 else f" ({days} days left)" if days > 1 else " (1 day left)" if days == 1 else " (overdue)"
        lines.append(f"Due {due}{left}")
    tasks = _list(group.get("myTasks"))
    open_tasks = [t for t in tasks if t.get("status") != "done"]
    done = len(tasks) - len(open_tasks)
    if tasks:
        lines.append(f"My tasks ({len(open_tasks)} left):")
        for t in open_tasks[:MAX_TASKS]:
            word = STATUS_WORDS.get(str(t.get("status")), str(t.get("status") or ""))
            lines.append(f"- {_text(t.get('title'))} ({t.get('points', '?')} pt, {word})")
        if len(open_tasks) > MAX_TASKS:
            lines.append(f"- and {len(open_tasks) - MAX_TASKS} more")
        if done:
            lines.append(f"- {done} done")
    else:
        lines.append("No tasks for me yet.")
    meeting = group.get("nextMeeting")
    if isinstance(meeting, dict) and _text(meeting.get("label")):
        mode = MODE_WORDS.get(str(meeting.get("mode")), "")
        where = _text(meeting.get("location")) if meeting.get("mode") == "in_person" else ""
        extra = ", ".join(x for x in (mode, where) if x)
        lines.append(f"Next meeting: {_text(meeting.get('label'))}{', ' + extra if extra else ''}")
        link = _text(meeting.get("joinUrl"))
        if link:
            lines.append(link)
    return lines


def summary(me: dict) -> str:
    """The reply text: one block per group, then what is due in the next 7 days. Under MAX_CHARS."""
    blocks = []
    head = _text(me.get("summary"))
    blocks.append([f"Groupie: {head}" if head else "Groupie"])
    upcoming = _list(me.get("upcoming"))
    if upcoming:
        items = ["Due in the next 7 days:"]
        for item in upcoming[:MAX_ITEMS]:
            course = _text(item.get("course"))
            items.append(f"- {course + ': ' if course else ''}{_text(item.get('title'))}, {_text(item.get('dueLabel'))}")
        if len(upcoming) > MAX_ITEMS:
            items.append(f"- and {len(upcoming) - MAX_ITEMS} more")
        blocks.append(items)
    groups = _list(me.get("groups"))
    for group in groups:
        blocks.append(group_lines(group))
    if not groups and not upcoming:
        blocks.append(["Nothing due in the next 7 days, and no group projects yet."])
    text = "\n\n".join("\n".join(b) for b in blocks)
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS - 40].rsplit("\n", 1)[0] + "\n(more in Telegram)"
    return text


def fetch(method: str, base: str, path: str, token: str) -> tuple:
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "groupie-import-skill/1"}
    status, raw = g.request(method, base + path, headers)
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        data = {}
    return status, data if isinstance(data, dict) else {}


def failure(status: int) -> tuple:
    if status == 401:
        g.forget_token()
        return g.EXIT_NOT_LINKED, g.NOT_LINKED
    if status == 429:
        return g.EXIT_RATE, "Too many checks. Wait a few minutes and try again."
    return g.EXIT_SERVER, f"Groupie's server answered HTTP {status}. Try again later."


def run(argv=None) -> tuple:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--unlink", action="store_true", help="disconnect this Cubbie from Groupie")
    parser.add_argument("--sample", action="store_true", help="print the bundled sample; no token, no network")
    parser.add_argument("--url", help=f"Groupie base URL (default $GROUPIE_URL or {send.DEFAULT_URL})")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return (g.EXIT_OK if exc.code == 0 else g.EXIT_ARGS), ""
    if args.sample:
        try:
            return g.EXIT_OK, summary(json.loads(SAMPLE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return g.EXIT_ARGS, f"Cannot read {SAMPLE.name}."
    base = g.base_url(args.url)
    token = g.read_token()
    if args.unlink:
        if not token:
            return g.EXIT_OK, "This Cubbie was not linked to Groupie."
        status, _ = fetch("DELETE", base, g.LINK_PATH, token)
        if 200 <= status < 300 or status == 401:
            g.forget_token()
            return g.EXIT_OK, "Unlinked from Groupie. Send /imessage in Telegram to link again."
        return failure(status)
    if not token:
        return g.EXIT_NOT_LINKED, g.NOT_LINKED
    status, data = fetch("GET", base, g.ME_PATH, token)
    if 200 <= status < 300:
        return g.EXIT_OK, summary(data)
    return failure(status)


def main(argv=None) -> int:
    try:
        code, line = run(argv)
    except send.Stop as stop:
        code, line = stop.code, str(stop)
    if line:
        print(line[:MAX_CHARS + 100], file=sys.stdout if code == g.EXIT_OK else sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())

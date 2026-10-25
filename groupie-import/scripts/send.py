#!/usr/bin/env python3
"""Send one Canvas assignment, read by Cubbie, to the student's Groupie group.

Cubbie reads the assignment from Canvas, writes it to a JSON file shaped like assets/sample_payload.json,
and runs this script only after the student says yes. The script checks the file against Groupie's import
contract, signs the exact bytes it sends with HMAC-SHA256, and POSTs them to <base>/api/cubbie/import.
Standard library only, Python 3.9 or newer.

    python3 scripts/send.py --file /tmp/groupie-import.json
    python3 scripts/send.py --file /tmp/groupie-import.json --target 2   # linked, after "Which group?"
    python3 scripts/send.py --sample                # checks the bundled sample, sends nothing
    python3 scripts/send.py --sample --url http://localhost:3000

Linked mode: when the file has no pair code and the student linked this chat ("Groupie link CODE", token saved
by link.py in ~/.config/groupie/token), the script sends to <base>/api/cubbie/import/linked with that token as a
bearer instead. If Groupie cannot tell which group, it prints "Which group? 1. <name> 2. <name> 3. Just me" and
saves the choices; rerun with --target <number> (or --target me, or --target <group id>). With no code and no link,
it asks for the code. A file with a code always uses the code. The token is never printed.

Output: stdout holds only what Cubbie texts the student, word for word (one line, or two when big files were
dropped and the text was sent instead). stderr holds short notes for Cubbie that are never read aloud: what to fix
for exit codes 2 and 4, and the server's error code and requestId. Secrets are never printed.

Exit codes (one per class; the student line is the same for 5, 6 and 11):
  0  sent (or, with --sample and no --url, the sample checked out)
  2  bad arguments, or the payload file is missing or not JSON (nothing on stdout; fix and rerun)
  3  the pair code is not 6 letters and digits from Groupie's alphabet
  4  the payload is missing something or breaks a size limit (nothing on stdout; fix and rerun)
  5  Groupie's server could not be reached or timed out (after one retry): "hit a snag" line
  6  invalid_payload: Groupie could not use the payload: "hit a snag" line
  7  bad_signature: the Skill's signing key is out of date: reinstall line
  8  code_expired or code_unknown: "That code expired" line
  9  not_expecting: the group already has its assignment: /reset line
  10 rate_limited: "Too many tries" line
  11 server_error, and any other or missing error code, or a body that is not JSON: "hit a snag" line
  12 code_used: "That code was already used" line
  13 payload_too_large, still too large after the one automatic retry without attachments (or there were no
     attachments to drop): "hit a snag" line
  14 choose_target (linked mode): "Which group?" line; rerun with --target after the student answers
  15 bad_link (linked mode): the link was revoked or is unknown: relink line (the saved token is deleted)
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_URL = "https://devhacks-groupie-production.up.railway.app"
IMPORT_PATH = "/api/cubbie/import"
LINKED_PATH = "/api/cubbie/import/linked"
SAMPLE = Path(__file__).resolve().parent.parent / "assets" / "sample_payload.json"
TIMEOUT_SECONDS = 20

# Public by design: Cubbie cannot host team keys, so anyone who installs the Skill can read this value.
# It is integrity and spam friction only; the single-use, 30 minute pair code is the real gate.
# It must equal the server's CUBBIE_PUBLIC_SIGNING_KEY; paste it in when publishing (see README.md). Never print it.
GROUPIE_SIGNING_KEY = os.environ.get("GROUPIE_SIGNING_KEY") or "5a820a40127e62cd1966b6362bae344235e40f4de2e45f39fabe0bc29d013e98"

# Mirrors packages/shared/src/canvas.ts.
PAIR_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PAIR_CODE = re.compile(r"^[%s]{6}$" % PAIR_CODE_ALPHABET)
MAX_ATTACHMENTS = 5
MAX_TEXT = 60_000
MAX_BASE64 = math.ceil((10 * 1024 * 1024 * 4) / 3) + 8
# ISO 8601 with seconds and an offset (Z or +HH:MM). A regex, since fromisoformat rejects "Z" before 3.11.
ISO_WITH_OFFSET = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
URL = re.compile(r"^https?://[^\s/]+\S*$", re.IGNORECASE)

EXIT_OK, EXIT_ARGS, EXIT_CODE, EXIT_PAYLOAD, EXIT_NETWORK = 0, 2, 3, 4, 5
EXIT_BAD_PAYLOAD, EXIT_SIGNATURE, EXIT_FRESH_CODE, EXIT_NOT_EXPECTING, EXIT_RATE, EXIT_SERVER = 6, 7, 8, 9, 10, 11
EXIT_CODE_USED, EXIT_TOO_LARGE = 12, 13
EXIT_CHOOSE, EXIT_BAD_LINK = 14, 15
# Exit codes whose line is a note for Cubbie (fix the file and rerun), not something to text the student.
CUBBIE_ONLY = (EXIT_ARGS, EXIT_PAYLOAD)

USER_AGENT = "groupie-import-skill/3"

# What Cubbie texts the student, word for word. Keep these in step with SKILL.md.
LINE_EXPIRED = "That code expired. Send /canvas in your Groupie group for a new one, then text me again."
LINE_USED = "That code was already used. Grab a fresh one with /canvas."
LINE_TOO_BIG = "The files are too big to send. I'll send the assignment text instead."
LINE_SNAG = "Groupie hit a snag on its side. Your code still works, try again in a minute."
LINE_RATE = "Too many tries, wait a minute and try again."
LINE_OUTDATED = ("This Groupie skill is out of date. Reinstall it with: Install this skill: "
                 "https://github.com/katsdivi/groupie-cubbie-skill/tree/main/groupie-import")
LINE_NOT_EXPECTING = ("That group already has its assignment. Send /reset in the group to start over, "
                      "then /canvas for a new code.")
LINE_BAD_CODE = "That code doesn't look right. Copy it again from the Groupie message in your group."
LINE_SENT = "Sent to Groupie. Check your group."
LINE_BAD_LINK = "Your Groupie link expired. Send /imessage in Groupie to relink."
LINE_NEED_CODE = ("I need the code from Groupie for this one. Send /canvas in your Groupie group (or /imessage "
                  "in Groupie to link me once), then text me again.")
LINE_BAD_TARGET = "That isn't one of the choices. Text me the number from the list."

# Server error code -> (exit code, student line). Anything else, or no code at all, is server_error.
ERRORS = {
    "code_expired": (EXIT_FRESH_CODE, LINE_EXPIRED),
    "code_unknown": (EXIT_FRESH_CODE, LINE_EXPIRED),
    "code_used": (EXIT_CODE_USED, LINE_USED),
    "bad_signature": (EXIT_SIGNATURE, LINE_OUTDATED),
    "payload_too_large": (EXIT_TOO_LARGE, LINE_SNAG),
    "invalid_payload": (EXIT_BAD_PAYLOAD, LINE_SNAG),
    "rate_limited": (EXIT_RATE, LINE_RATE),
    "server_error": (EXIT_SERVER, LINE_SNAG),
    "not_expecting": (EXIT_NOT_EXPECTING, LINE_NOT_EXPECTING),
    "bad_link": (EXIT_BAD_LINK, LINE_BAD_LINK),
    "choose_target": (EXIT_CHOOSE, "Which group?"),
}
# Older server names for the same errors, so a Skill update and a server deploy can land in either order.
ALIASES = {"unknown_code": "code_unknown", "bad_payload": "invalid_payload"}
REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")


class Stop(Exception):
    """One line (for the student, or for Cubbie with exit codes 2 and 4), the exit code, and an optional note."""

    def __init__(self, message: str, code: int, detail: str = ""):
        super().__init__(message)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------- checking the payload


def _string(value, field: str, problems: list, low: int = 0, high: int = 10_000, required: bool = True) -> None:
    if value is None:
        if required:
            problems.append(f"{field} is missing")
        return
    if not isinstance(value, str):
        problems.append(f"{field} must be text")
        return
    size = len(value.strip())
    if size < low:
        problems.append(f"{field} is empty")
    elif size > high:
        problems.append(f"{field} is longer than {high} characters")


def _url(value, field: str, problems: list) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not URL.match(value) or len(value) > 2000:
        problems.append(f"{field} is not a web link")


def _number(value, field: str, problems: list, high: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= high:
        problems.append(f"{field} must be a number from 0 to {high:g}")


def valid_due(value) -> bool:
    """ISO 8601 with an offset, like 2026-11-20T23:59:00-07:00, or null."""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    match = ISO_WITH_OFFSET.match(value)
    if not match:
        return False
    try:
        datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    offset = match.group(3)
    return offset == "Z" or (int(offset[1:3]) <= 23 and int(offset[4:6]) <= 59)


def normalize_code(value) -> str | None:
    """The code trimmed and uppercased, or None when it is not a Groupie pair code."""
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if PAIR_CODE.match(code) else None


def valid_target(target) -> bool:
    if not isinstance(target, dict):
        return False
    if target.get("kind") == "me":
        return True
    group_id = target.get("groupId")
    return target.get("kind") == "group" and isinstance(group_id, int) and not isinstance(group_id, bool)


def validate(payload, linked: bool = False) -> list:
    """Problems with the payload, as short phrases. Empty means it matches CubbieImportPayload (or, linked, the
    linked payload: no pair code, optional target)."""
    if not isinstance(payload, dict):
        return ["the payload is not an object"]
    problems = []
    if not linked and normalize_code(payload.get("pairCode")) is None:
        problems.append("pairCode is not a 6 character Groupie code")
    target = payload.get("target")
    if linked and target is not None and not valid_target(target):
        problems.append("target must be {kind: me} or {kind: group, groupId: <number>}")
    course = payload.get("course")
    if not isinstance(course, dict):
        problems.append("course is missing")
    else:
        _string(course.get("name"), "course.name", problems, 1, 200)
        _string(course.get("code"), "course.code", problems, 0, 60, required=False)
    assignment = payload.get("assignment")
    if not isinstance(assignment, dict):
        problems.append("assignment is missing")
    else:
        _string(assignment.get("title"), "assignment.title", problems, 1, 300)
        if "dueAt" not in assignment:
            problems.append("assignment.dueAt is missing (use null when Canvas has no due date)")
        elif not valid_due(assignment.get("dueAt")):
            problems.append("assignment.dueAt must be ISO 8601 with a time zone offset, or null")
        if assignment.get("pointsPossible") is not None:
            _number(assignment["pointsPossible"], "assignment.pointsPossible", problems, 100_000)
        _string(assignment.get("descriptionText"), "assignment.descriptionText", problems, 0, MAX_TEXT, required=False)
        _url(assignment.get("url"), "assignment.url", problems)
    rubric = payload.get("rubric")
    if rubric is not None:
        criteria = rubric.get("criteria") if isinstance(rubric, dict) else None
        if not isinstance(criteria, list) or not 1 <= len(criteria) <= 40:
            problems.append("rubric.criteria must list 1 to 40 criteria")
        else:
            for i, item in enumerate(criteria):
                where = f"rubric.criteria[{i}]"
                if not isinstance(item, dict):
                    problems.append(f"{where} is not an object")
                    continue
                _string(item.get("name"), f"{where}.name", problems, 1, 200)
                _number(item.get("points"), f"{where}.points", problems, 1000)
                ratings = item.get("ratings", [])
                if not isinstance(ratings, list) or len(ratings) > 10:
                    problems.append(f"{where}.ratings must be a list of at most 10")
                else:
                    for j, rating in enumerate(ratings):
                        _string(rating, f"{where}.ratings[{j}]", problems, 0, 300)
    attachments = payload.get("attachments")
    if attachments is not None:
        if not isinstance(attachments, list) or len(attachments) > MAX_ATTACHMENTS:
            problems.append(f"attachments must be a list of at most {MAX_ATTACHMENTS}")
        else:
            for i, item in enumerate(attachments):
                where = f"attachments[{i}]"
                if not isinstance(item, dict):
                    problems.append(f"{where} is not an object")
                    continue
                _string(item.get("filename"), f"{where}.filename", problems, 1, 200)
                _string(item.get("mimeType"), f"{where}.mimeType", problems, 0, 120, required=False)
                text, raw, link = item.get("text"), item.get("base64"), item.get("url")
                if text is not None and (not isinstance(text, str) or len(text) > MAX_TEXT):
                    problems.append(f"{where}.text must be text up to {MAX_TEXT} characters")
                if raw is not None and (not isinstance(raw, str) or len(raw) > MAX_BASE64):
                    problems.append(f"{where}.base64 is larger than 10 MB")
                _url(link, f"{where}.url", problems)
                if not (text or raw or link):
                    problems.append(f"{where} needs text, base64 or url")
    return problems


# ---------------------------------------------------------------- signing and sending


def sign(key: bytes, body: bytes) -> str:
    """The X-Groupie-Signature header value: sha256=<hex HMAC-SHA256(key, body)>."""
    return "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()


def encode(payload: dict) -> bytes:
    """The exact bytes that are signed and sent."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def post(url: str, body: bytes, headers: dict) -> tuple:
    """POST once and return (HTTP status, response bytes). Network failures raise OSError."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def send(base_url: str, payload: dict, key: str, token=None) -> tuple:
    """Sign and send the payload, retrying once on a network error. Returns (status, parsed JSON body or None).
    With a link token it goes to the linked import with the token as a bearer (never printed)."""
    body = encode(payload)
    headers = {
        "Content-Type": "application/json",
        "X-Groupie-Signature": sign(key.encode("utf-8"), body),
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = base_url.rstrip("/") + (LINKED_PATH if token else IMPORT_PATH)
    for attempt in (1, 2):
        try:
            status, raw = post(url, body, headers)
            break
        except (OSError, socket.timeout) as exc:  # URLError, timeouts and refused connections are all OSError
            if attempt == 2:
                raise Stop(LINE_SNAG, EXIT_NETWORK, f"error: unreachable ({type(exc).__name__})") from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:  # includes UnicodeDecodeError
        return status, None
    return status, data if isinstance(data, dict) else None


def error_code(status: int, data) -> str:
    """The server's error code, normalized. A 413 with no JSON (a proxy's body limit) is payload_too_large."""
    error = data.get("error") if isinstance(data, dict) else None
    error = ALIASES.get(error, error) if isinstance(error, str) else None
    if error in ERRORS:
        return error
    return "payload_too_large" if status == 413 else "server_error"


def request_id(data) -> str:
    """The server's requestId when it looks like one, else empty (never echo arbitrary server text)."""
    value = data.get("requestId") if isinstance(data, dict) else None
    return value if isinstance(value, str) and REQUEST_ID.match(value) else ""


def note(status: int, data) -> str:
    """The short machine note for stderr: error code, HTTP status and requestId."""
    rid = request_id(data)
    return f"error: {error_code(status, data)} (HTTP {status})" + (f"\nrequestId: {rid}" if rid else "")


def outcome(status: int, data) -> tuple:
    """Map the server's answer to (exit code, student line)."""
    if 200 <= status < 300 and isinstance(data, dict) and data.get("ok") is True:
        message = data.get("message")
        return EXIT_OK, message if isinstance(message, str) and message.strip() else LINE_SENT
    return ERRORS[error_code(status, data)]


def without_attachments(payload: dict) -> dict:
    """The same payload with its attachments dropped (the assignment text stays)."""
    return {k: v for k, v in payload.items() if k != "attachments"}


def deliver(base: str, payload: dict, key: str, token=None) -> tuple:
    """Send, and on payload_too_large retry once without attachments. Returns (exit code, stdout lines, notes)."""
    lines, notes = [], []
    try:
        status, data = send(base, payload, key, token)
    except Stop as stop:
        return stop.code, [str(stop)], [stop.detail]
    code, line = outcome(status, data)
    if code == EXIT_CHOOSE:
        return choose(data, notes + [note(status, data)])
    if code == EXIT_BAD_LINK:
        forget_token()
    if code != EXIT_OK:
        notes.append(note(status, data))
    if code == EXIT_TOO_LARGE and payload.get("attachments"):
        lines.append(LINE_TOO_BIG)
        try:
            status, data = send(base, without_attachments(payload), key, token)
        except Stop as stop:
            return stop.code, lines + [str(stop)], notes + [stop.detail]
        code, line = outcome(status, data)
        if code == EXIT_CHOOSE:
            return choose(data, notes + ["retry without attachments: " + note(status, data)], lines)
        if code != EXIT_OK:
            notes.append("retry without attachments: " + note(status, data))
    return code, lines + [line], notes


# ---------------------------------------------------------------- linked mode


def token_path() -> Path:
    """~/.config/groupie/token: where link.py saves the student's link token (mode 600)."""
    return Path.home() / ".config" / "groupie" / "token"


def targets_path() -> Path:
    """The last "Which group?" choices, so --target 2 means the second one. Ids and titles only."""
    return token_path().with_name("targets.json")


def read_token():
    """The saved link token, or None when this chat is not linked."""
    try:
        token = token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def forget_token() -> None:
    try:
        token_path().unlink()
    except OSError:
        pass


def clean_options(data) -> list:
    """The server's choose_target options, kept to the documented shape."""
    options = data.get("options") if isinstance(data, dict) else None
    out = []
    for item in options if isinstance(options, list) else []:
        if not isinstance(item, dict):
            continue
        title = item.get("title") if isinstance(item.get("title"), str) else ""
        title = " ".join(title.split())[:80]
        if item.get("kind") == "me":
            out.append({"kind": "me", "title": title or "Just me"})
        elif item.get("kind") == "group" and isinstance(item.get("groupId"), int) and not isinstance(item.get("groupId"), bool):
            out.append({"kind": "group", "groupId": item["groupId"], "title": title or "Your group"})
    if not any(o["kind"] == "me" for o in out):
        out.append({"kind": "me", "title": "Just me"})
    return out


def choose(data, notes: list, lines=None) -> tuple:
    """The "Which group?" question, numbered from the server's options, which are saved for --target N."""
    options = clean_options(data)
    try:
        path = targets_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(options).encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        notes.append("error: could not save the choices; use --target me or --target <group id>")
    question = "Which group? " + " ".join(f"{i}. {o['title']}" for i, o in enumerate(options, 1))
    for i, o in enumerate(options, 1):
        notes.append(f"target {i}: {o['groupId'] if o['kind'] == 'group' else 'me'} ({o['title']})")
    return EXIT_CHOOSE, (lines or []) + [question], notes


def parse_target(value: str):
    """--target: "me", a number from the last "Which group?" list, or a group id. None when it fits none."""
    value = value.strip().lower().rstrip(".")
    if value in ("me", "just me"):
        return {"kind": "me"}
    try:
        number = int(value)
    except ValueError:
        return None
    try:
        saved = json.loads(targets_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = []
    if isinstance(saved, list) and 1 <= number <= len(saved) and isinstance(saved[number - 1], dict):
        choice = saved[number - 1]
        if choice.get("kind") == "me":
            return {"kind": "me"}
        if isinstance(choice.get("groupId"), int):
            return {"kind": "group", "groupId": choice["groupId"]}
    if abs(number) > 100:  # a Telegram group id, not a list number
        return {"kind": "group", "groupId": number}
    return None


# ---------------------------------------------------------------- command line


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise Stop(f"Cannot read {path}.", EXIT_ARGS) from None
    except ValueError:
        raise Stop(f"{path} is not valid JSON.", EXIT_ARGS) from None


def run(argv=None) -> tuple:
    """Returns (exit code, stdout lines, stderr notes); raises Stop for problems found before sending."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="the payload JSON Cubbie wrote")
    source.add_argument("--sample", action="store_true", help="use assets/sample_payload.json; sends only with --url")
    parser.add_argument("--code", help="pair code to use instead of the one in the file")
    parser.add_argument("--target", help='linked mode: "me", a number from the "Which group?" list, or a group id')
    parser.add_argument("--url", help=f"Groupie base URL (default $GROUPIE_URL or {DEFAULT_URL})")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return (EXIT_OK if exc.code == 0 else EXIT_ARGS), [], []

    payload = load(SAMPLE if args.sample else args.file)
    if not isinstance(payload, dict):
        raise Stop("The payload file must hold one JSON object.", EXIT_PAYLOAD)
    if args.code is not None:
        payload["pairCode"] = args.code
    base = args.url or os.environ.get("GROUPIE_URL") or DEFAULT_URL
    raw_code = payload.get("pairCode")
    if args.code is None and (raw_code is None or (isinstance(raw_code, str) and not raw_code.strip())):
        return run_linked(payload, args, base)
    code = normalize_code(payload.get("pairCode"))
    if code is None:
        raise Stop(LINE_BAD_CODE, EXIT_CODE, "error: the pair code must be 6 letters and digits, like K7Q2MX")
    payload["pairCode"] = code
    problems = validate(payload)
    if problems:
        shown = "; ".join(problems[:5]) + (f" (and {len(problems) - 5} more)" if len(problems) > 5 else "")
        raise Stop(f"The assignment is not ready to send: {shown}.", EXIT_PAYLOAD)

    if args.sample and not args.url:
        size = len(encode(payload))
        return EXIT_OK, [(f"Sample OK: would send {payload['assignment']['title']!r} ({payload['course']['name']}), "
                          f"{size} bytes, to {base.rstrip('/')}{IMPORT_PATH}. Nothing was sent.")], []
    return deliver(base, payload, GROUPIE_SIGNING_KEY)


def run_linked(payload: dict, args, base: str) -> tuple:
    """No code in the file: send with the student's link token, or ask for a code when not linked."""
    token = read_token()
    if not token:
        raise Stop(LINE_NEED_CODE, EXIT_CODE, "error: no pair code and this chat is not linked")
    payload = {k: v for k, v in payload.items() if k != "pairCode"}
    if args.target is not None:
        target = parse_target(args.target)
        if target is None:
            raise Stop(LINE_BAD_TARGET, EXIT_CHOOSE, "error: --target is not me, a list number or a group id")
        payload["target"] = target
    problems = validate(payload, linked=True)
    if problems:
        shown = "; ".join(problems[:5]) + (f" (and {len(problems) - 5} more)" if len(problems) > 5 else "")
        raise Stop(f"The assignment is not ready to send: {shown}.", EXIT_PAYLOAD)
    if args.sample and not args.url:
        return EXIT_OK, [f"Sample OK: would send {payload['assignment']['title']!r} with this chat's Groupie link to "
                         f"{base.rstrip('/')}{LINKED_PATH}. Nothing was sent."], []
    return deliver(base, payload, GROUPIE_SIGNING_KEY, token)


def main(argv=None) -> int:
    try:
        code, lines, notes = run(argv)
    except Stop as stop:
        code, lines, notes = stop.code, [str(stop)], [stop.detail]
    out = sys.stderr if code in CUBBIE_ONLY else sys.stdout
    for line in lines:
        if line:
            print(line[:500], file=out)
    for item in notes:
        if item:
            print(item[:300], file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())

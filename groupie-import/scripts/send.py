#!/usr/bin/env python3
"""Send one Canvas assignment, read by Cubbie, to the student's Groupie group.

Cubbie reads the assignment from Canvas, writes it to a JSON file shaped like assets/sample_payload.json,
and runs this script only after the student says yes. The script checks the file against Groupie's import
contract, signs the exact bytes it sends with HMAC-SHA256, and POSTs them to <base>/api/cubbie/import.
Standard library only, Python 3.9 or newer.

    python3 scripts/send.py --file /tmp/groupie-import.json
    python3 scripts/send.py --sample                # checks the bundled sample, sends nothing
    python3 scripts/send.py --sample --url http://localhost:3000

Exit codes:
  0 sent (or, with --sample and no --url, the sample checked out)
  2 bad arguments, or the payload file is missing or not JSON
  3 the pair code is not 6 letters and digits from Groupie's alphabet
  4 the payload is missing something or breaks a size limit
  5 Groupie's server could not be reached (after one retry)
  6 the server rejected the payload (400)
  7 the server rejected the signature (401): the Skill's signing key is out of date
  8 the code is unknown, already used or expired (404, or 409 code_used / code_expired)
  9 the group is not expecting an assignment (409 not_expecting)
  10 too many imports, try later (429)
  11 any other server error
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
SAMPLE = Path(__file__).resolve().parent.parent / "assets" / "sample_payload.json"
TIMEOUT_SECONDS = 20

# Public by design: Cubbie cannot host team keys, so anyone who installs the Skill can read this value.
# It is integrity and spam friction only; the single-use, 15 minute pair code is the real gate.
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


class Stop(Exception):
    """One line for the student's assistant, and the exit code that goes with it."""

    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


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


def validate(payload) -> list:
    """Problems with the payload, as short phrases. Empty means it matches CubbieImportPayload."""
    if not isinstance(payload, dict):
        return ["the payload is not an object"]
    problems = []
    if normalize_code(payload.get("pairCode")) is None:
        problems.append("pairCode is not a 6 character Groupie code")
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


def send(base_url: str, payload: dict, key: str) -> tuple:
    """Sign and send the payload, retrying once on a network error. Returns (status, parsed JSON body)."""
    body = encode(payload)
    headers = {
        "Content-Type": "application/json",
        "X-Groupie-Signature": sign(key.encode("utf-8"), body),
        "User-Agent": "groupie-import-skill/1",
    }
    url = base_url.rstrip("/") + IMPORT_PATH
    for attempt in (1, 2):
        try:
            status, raw = post(url, body, headers)
            break
        except (OSError, socket.timeout) as exc:  # URLError, timeouts and refused connections are all OSError
            if attempt == 2:
                raise Stop(f"Could not reach Groupie's server ({type(exc).__name__}). Try again in a minute.",
                           EXIT_NETWORK) from None
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        data = {}
    return status, data if isinstance(data, dict) else {}


def outcome(status: int, data: dict) -> tuple:
    """Map the server's answer to (exit code, one line)."""
    error = str(data.get("error") or "")
    if 200 <= status < 300 and data.get("ok") is True:
        return EXIT_OK, str(data.get("message") or "Sent to Groupie. Check your group.")
    if status == 400:
        return EXIT_BAD_PAYLOAD, "Groupie could not read this assignment (bad_payload)."
    if status == 401:
        return EXIT_SIGNATURE, "Groupie rejected the Skill's signature; the Skill needs an update."
    if status == 404 or (status == 409 and error in ("code_used", "code_expired")):
        return EXIT_FRESH_CODE, f"That pair code is {'unknown' if status == 404 else error.replace('code_', '')}."
    if status == 409:
        return EXIT_NOT_EXPECTING, "That group is not expecting an assignment right now."
    if status == 429:
        return EXIT_RATE, "Too many imports in the last hour. Try again later."
    return EXIT_SERVER, f"Groupie's server answered HTTP {status}. Try again later."


# ---------------------------------------------------------------- command line


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise Stop(f"Cannot read {path}.", EXIT_ARGS) from None
    except ValueError:
        raise Stop(f"{path} is not valid JSON.", EXIT_ARGS) from None


def run(argv=None) -> tuple:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="the payload JSON Cubbie wrote")
    source.add_argument("--sample", action="store_true", help="use assets/sample_payload.json; sends only with --url")
    parser.add_argument("--code", help="pair code to use instead of the one in the file")
    parser.add_argument("--url", help=f"Groupie base URL (default $GROUPIE_URL or {DEFAULT_URL})")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return (EXIT_OK if exc.code == 0 else EXIT_ARGS), ""

    payload = load(SAMPLE if args.sample else args.file)
    if not isinstance(payload, dict):
        raise Stop("The payload file must hold one JSON object.", EXIT_PAYLOAD)
    if args.code is not None:
        payload["pairCode"] = args.code
    code = normalize_code(payload.get("pairCode"))
    if code is None:
        raise Stop("The pair code must be 6 letters and digits, like K7Q2MX.", EXIT_CODE)
    payload["pairCode"] = code
    problems = validate(payload)
    if problems:
        shown = "; ".join(problems[:5]) + (f" (and {len(problems) - 5} more)" if len(problems) > 5 else "")
        raise Stop(f"The assignment is not ready to send: {shown}.", EXIT_PAYLOAD)

    base = args.url or os.environ.get("GROUPIE_URL") or DEFAULT_URL
    if args.sample and not args.url:
        size = len(encode(payload))
        return EXIT_OK, (f"Sample OK: would send {payload['assignment']['title']!r} ({payload['course']['name']}), "
                         f"{size} bytes, to {base.rstrip('/')}{IMPORT_PATH}. Nothing was sent.")
    status, data = send(base, payload, GROUPIE_SIGNING_KEY)
    return outcome(status, data)


def main(argv=None) -> int:
    try:
        code, line = run(argv)
    except Stop as stop:
        code, line = stop.code, str(stop)
    if line:
        print(line[:500], file=sys.stdout if code == EXIT_OK else sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())

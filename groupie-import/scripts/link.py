#!/usr/bin/env python3
"""Link the student's Cubbie to Groupie, so "Groupie status" works in iMessage.

The student sends /imessage to the Groupie bot in Telegram, gets a one-time code, and texts Cubbie
"Groupie link CODE". This script sends that code to <base>/api/cubbie/link (signed like the import) and saves the
token Groupie returns to ~/.config/groupie/token with mode 600. The token is never printed.

    python3 scripts/link.py --code K7Q2MX
    python3 scripts/link.py --code K7Q2MX --sample   # checks the code, sends and saves nothing

Exit codes:
  0 linked (or, with --sample, the code checked out)
  2 bad arguments
  3 the code is not 6 letters and digits from Groupie's alphabet
  5 Groupie's server could not be reached (after one retry)
  7 the server rejected the signature (401): the Skill's signing key is out of date
  8 the code is unknown, already used or expired
  10 too many tries, wait a few minutes (429)
  11 any other server error, or the token could not be saved
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import groupie_link as g  # noqa: E402

send = g.send


def outcome(status: int, data: dict) -> tuple:
    """Map the server's answer to (exit code, one line). Saves the token on success."""
    error = str(data.get("error") or "")
    token = data.get("token")
    if 200 <= status < 300 and data.get("ok") is True and isinstance(token, str) and token:
        try:
            g.save_token(token)
        except OSError:
            return g.EXIT_SERVER, "Linked, but the token could not be saved here. Send /imessage again for a new code."
        name = data.get("name")
        hello = f"Linked to Groupie, {name}." if isinstance(name, str) and name.strip() else "Linked to Groupie."
        return g.EXIT_OK, f"{hello} Text me \"Groupie status\" any time."
    if status == 401:
        return g.EXIT_SIGNATURE, "Groupie rejected the Skill's signature; the Skill needs an update."
    if status == 404 or (status == 409 and error in ("code_used", "code_expired")):
        return g.EXIT_FRESH_CODE, f"That link code is {'unknown' if status == 404 else error.replace('code_', '')}."
    if status == 429:
        return g.EXIT_RATE, "Too many tries. Wait a few minutes and try again."
    return g.EXIT_SERVER, f"Groupie's server answered HTTP {status}. Try again later."


def run(argv=None) -> tuple:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--code", required=True, help="the link code from /imessage, like K7Q2MX")
    parser.add_argument("--url", help=f"Groupie base URL (default $GROUPIE_URL or {send.DEFAULT_URL})")
    parser.add_argument("--sample", action="store_true", help="check the code only; send and save nothing")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return (g.EXIT_OK if exc.code == 0 else g.EXIT_ARGS), ""
    code = send.normalize_code(args.code)
    if code is None:
        raise send.Stop("The link code must be 6 letters and digits, like K7Q2MX.", g.EXIT_CODE)
    base = g.base_url(args.url)
    if args.sample:
        return g.EXIT_OK, f"Sample OK: would link with code {code} at {base}{g.LINK_PATH}. Nothing was sent or saved."
    body = json.dumps({"pairCode": code}, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Groupie-Signature": send.sign(send.GROUPIE_SIGNING_KEY.encode("utf-8"), body),
        "User-Agent": send.USER_AGENT,
    }
    status, raw = g.request("POST", base + g.LINK_PATH, headers, body)
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        data = {}
    return outcome(status, data if isinstance(data, dict) else {})


def main(argv=None) -> int:
    try:
        code, line = run(argv)
    except send.Stop as stop:
        code, line = stop.code, str(stop)
    if line:
        print(line[:500], file=sys.stdout if code == g.EXIT_OK else sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())

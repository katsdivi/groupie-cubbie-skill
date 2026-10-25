"""Shared helpers for link.py and status.py: where the Groupie token lives and how it is read and written.

The token is Groupie's own token, made for this student when they texted "Groupie link CODE". It is saved only
in the student's Cubbie sandbox, under HOME, readable by the student alone (mode 600). It is never printed.
Standard library only, Python 3.9 or newer.
"""

from __future__ import annotations

import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import send  # noqa: E402  (same folder; carries the signing key, the default URL and the code check)

LINK_PATH = "/api/cubbie/link"
ME_PATH = "/api/cubbie/me"
TIMEOUT_SECONDS = 20
NOT_LINKED = "Not linked yet. Send /imessage to @CubbieGroupieBot in Telegram, then text me the code it gives you."

EXIT_OK, EXIT_ARGS, EXIT_CODE, EXIT_NETWORK = 0, 2, 3, 5
EXIT_SIGNATURE, EXIT_FRESH_CODE, EXIT_RATE, EXIT_SERVER, EXIT_NOT_LINKED = 7, 8, 10, 11, 12


def token_path() -> Path:
    """~/.config/groupie/token, under the HOME of whoever runs the script (the same file send.py reads)."""
    return send.token_path()


def save_token(token: str) -> Path:
    """Writes the token with mode 600 (folder 700), replacing any old one in one step."""
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name(".token.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def read_token():
    """The saved token, or None when there is none."""
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


def base_url(flag) -> str:
    return (flag or os.environ.get("GROUPIE_URL") or send.DEFAULT_URL).rstrip("/")


def request(method: str, url: str, headers: dict, body=None) -> tuple:
    """One HTTP call, retried once on a network error. Returns (status, raw bytes); raises send.Stop when down."""
    for attempt in (1, 2):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (OSError, socket.timeout) as exc:
            if attempt == 2:
                raise send.Stop(f"Could not reach Groupie's server ({type(exc).__name__}). Try again in a minute.",
                                EXIT_NETWORK) from None
    raise send.Stop("Could not reach Groupie's server.", EXIT_NETWORK)

# groupie-import (Cubbie Skill)

A Cubbie Skill that carries one Canvas assignment into a Groupie group chat. Groupie is our Telegram bot that
splits group projects; Cubbie is the iMessage assistant that already has the student's Canvas.

## What it does

1. In the Telegram group, a teammate types `/canvas`. Groupie replies with a one-time code (6 characters, single
   use, 30 minutes) and the exact text to send Cubbie, such as `Groupie import K7Q2MX`.
2. The student texts that to Cubbie, optionally with the assignment name: `Groupie import K7Q2MX Project 2`.
3. Cubbie reads the assignment from Canvas (course, title, due date with time zone, points if shown, description,
   link) and the text of its attached files, shows the student a short summary, and asks before sending.
4. After the student's yes, `scripts/send.py` checks the data, signs it, and posts it to
   `https://devhacks-groupie-production.up.railway.app/api/cubbie/import`. Groupie turns it into tasks in the
   group, and Cubbie replies "Sent to Groupie. Check your group."

Personal assignments work the same way: `/canvas` in a private chat with the bot makes a code with no group.
Groupie plans that assignment for one person, DMs the student the steps, adds the due date to their Upcoming list
(so reminders and private chat see it), and Cubbie replies "Sent to Groupie. Check your Telegram messages."

## iMessage link

1. In a private chat with @CubbieGroupieBot, the student sends `/imessage`. Groupie replies with a one-time link
   code (same alphabet, single use, 30 minutes): "Text Cubbie: Groupie link K7Q2MX".
2. The student texts that to Cubbie. `scripts/link.py` sends the signed code to `POST /api/cubbie/link`, and
   Groupie returns a token once. The script saves it to `~/.config/groupie/token` (mode 600, folder 700) in the
   student's own Cubbie sandbox and never prints it. Groupie DMs the student that the link worked.
3. "Groupie status" (or "what's due on Groupie", "my Groupie tasks") runs `scripts/status.py`, which calls
   `GET /api/cubbie/me` with the token and prints a plain-text summary under 1,500 characters: deadlines in the
   next 7 days, the student's own tasks per group, and each group's next meeting with its link.
4. "Groupie unlink" runs `scripts/status.py --unlink` (revokes the token on the server and deletes the file).
   `/unlink` in Telegram revokes every token of that student; the next status check then deletes the file too.

The token is Groupie's own credential, made for this student, not a Canvas or Telegram login, so the rule
against sending a student's credentials to a team server does not apply: it only ever goes back to Groupie.

## Install

Text the Skill's GitHub folder link to Cubbie (for example
`https://github.com/<owner>/groupie-cubbie-skill/tree/main/groupie-import`) and say yes when Cubbie asks to fetch
it. There is no setup: the script uses only the Python 3.9+ standard library, and there is no key or sign-in for
the student. Canvas must be connected to Cubbie.

## Privacy

- **What is sent:** the pair code, course name and code, assignment title, due date, points possible, the
  description text, the Canvas assignment link, the extracted text of up to five attached files, and rubric
  criteria only if Canvas returned them with the assignment.
- **Where:** Groupie's server at `devhacks-groupie-production.up.railway.app` (HTTPS), which posts the task list in
  the Telegram group the code was made for.
- **When:** once, and only after the student sees the summary and says yes. Nothing is sent without that yes,
  and nothing runs in the background or on a schedule.
- **Never sent:** Canvas logins or tokens, the Canvas calendar feed link, grades, other assignments, or the raw
  files. `send.py` saves nothing on the student's drive.
- **Personal imports** (code made in a private chat) are stored on Groupie's server like group imports; the
  assignment title, course, due date and Canvas link also become one item in the student's own Upcoming list.
  Only that student sees it.
- **iMessage link:** `link.py` sends only the 6 character link code. Groupie stores only the sha256 of the token
  it returns (never the token), plus when it was made, last used and revoked. The token itself is stored in one
  place: `~/.config/groupie/token` in the student's Cubbie sandbox, readable only by them.
- **Status:** `status.py` sends only the token. Groupie answers with that student's own data: their groups'
  titles, assignments and due dates, their own tasks, each group's next meeting, and their own calendar items for
  the next 7 days. Never other members' calendars, memory, chats or profiles. Nothing from the answer is saved.

## Not sent because Cubbie can't do it reliably (UNCLEAR in `docs/CANVAS_RECON.md`)

- **Raw files.** Nothing documents Cubbie handing a Canvas file's bytes to a script, so attachments go as extracted
  text. The server also accepts `base64` and `url` attachments, but this Skill never produces them.
- **A separate rubric lookup.** The kit doesn't list rubrics as a Canvas ability. The Skill includes criteria only
  when Canvas returned them with points as part of the assignment.
- **The calendar feed.** A Canvas feed link is a personal credential, and Skills may not send credentials to a
  team's server, so there is no "Groupie calendar" trigger. Students paste the feed link in the Mini App.

## The signing key is public by design

`scripts/send.py` signs every request with HMAC-SHA256 in the `X-Groupie-Signature: sha256=<hex>` header. The key
is the constant `GROUPIE_SIGNING_KEY` (read from the `GROUPIE_SIGNING_KEY` environment variable if set, else the
placeholder `REPLACE_AT_PUBLISH`), and it must equal the Groupie server's `CUBBIE_PUBLIC_SIGNING_KEY`. Cubbie cannot host a team's keys, so anyone who installs the Skill can read the
key. It is integrity and spam friction only; the real gate is the single-use, 30 minute pair code plus the
server's rate limits (10 imports per user per hour, 30 per IP per hour). The script never prints the key.

## Publishing

1. Copy this folder into a public GitHub repository as a folder named `groupie-import` (for example
   `katsdivi/groupie-cubbie-skill/groupie-import`). Our main repo is private, and Cubbie installs only from a
   public repo. Copy `scripts/` (`send.py`, `link.py`, `status.py`, `groupie_link.py`), `assets/`, `SKILL.md` and
   `README.md`; `tests/` is optional (it needs the repo's `fixtures/` folder).
2. In the copy, replace `"REPLACE_AT_PUBLISH"` in `scripts/send.py` with the server's `CUBBIE_PUBLIC_SIGNING_KEY`.
   `link.py` reads the key from `send.py`, so this is the only place. Without it every import and link fails with
   exit code 7 (bad signature). Use a value made only for this purpose, since it becomes public.
3. Run the kit validator: `python3 <kit>/skills/cubbie-skill-creator/scripts/validate_cubbie_skill.py groupie-import`.
4. Commit, push, and text the branch link to Cubbie to test (the checklist is in the kit's `cubbie-testing.md`).

## Running it locally

```bash
python3 scripts/send.py --sample                             # checks the bundled sample, sends nothing
python3 scripts/link.py --code K7Q2MX --sample               # checks the code, sends and saves nothing
python3 scripts/status.py --sample                           # prints assets/sample_status.json as a reply
HOME=$(mktemp -d) GROUPIE_URL=http://localhost:3000 python3 scripts/link.py --code K7Q2MX
python3 scripts/send.py --sample --url http://localhost:3000 # sends the sample to a local server
GROUPIE_URL=http://localhost:3000 python3 scripts/send.py --file payload.json --code K7Q2MX
```

The base URL is `--url`, else `$GROUPIE_URL`, else production. Requests time out after 20 seconds and are retried
once on a network error (never on an HTTP error).

`send.py` exit codes: 0 sent, 2 bad arguments or unreadable file, 3 bad pair code, 4 payload missing something or over a
limit, 5 network error, 6 server said 400, 7 server said 401 (signature), 8 code unknown (404) or used or expired
(409), 9 group not expecting an assignment (409 `not_expecting`), 10 rate limited (429), 11 other server error.
The 409 cases are told apart by the `error` field of the server's `{ "error", "message" }` body.

`link.py` exit codes: 0 linked, 2 bad arguments, 3 bad code, 5 network error, 7 bad signature, 8 code unknown,
used or expired, 10 rate limited, 11 other server error or the token could not be saved. `status.py` exit codes:
0 printed (or unlinked), 2 bad arguments, 5 network error, 10 rate limited, 11 other server error, 12 not linked
(no token, or the server said 401, in which case the saved token is deleted).

## Tests

From the repo root:

```bash
python3 -m unittest discover -s cubbie-skill/tests   # signing, validation, token file mode, exit codes
python3 cubbie-skill/tests/harness.py                # offline: stub server for import, link, status, unlink
python3 cubbie-skill/tests/harness.py --url http://localhost:3000 --code K7Q2MX --key "$CUBBIE_PUBLIC_SIGNING_KEY"
```

The offline harness starts a stub HTTP server on a random local port that recomputes the signature over the raw
body, enforces single-use codes, and returns contract-shaped responses (`ZZZZZZ` unknown, `XXXXXX` expired,
`NNNNNN` not expecting, `RRRRRR` rate limited). It then links with a throwaway HOME, checks status, unlinks,
and checks that the token is never printed and the token file is gone. Against a real server, codes are single use, so only the first
fixture can succeed per code.

## Assumptions

- Server error bodies are `{ "error": "<code>", "message": "..." }`, like the rest of the Groupie API, with the
  error codes listed in `API.md` (`bad_payload`, `bad_signature`, `unknown_code`, `code_used`, `code_expired`,
  `not_expecting`, `rate_limited`).
- `SKILL.md` describes the payload fields in plain words and points Cubbie at `assets/sample_payload.json` as the
  template, because kit rules keep JSON out of `SKILL.md`.
- Due dates must include seconds and an offset (`Z` or `+HH:MM`), slightly stricter than the server, so a
  payload that passes the script also passes the server.

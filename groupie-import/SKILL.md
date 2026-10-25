---
name: groupie-import
description: Sends a Canvas assignment to Groupie (the Telegram bot for group projects and personal plans) and texts the student their Groupie tasks, deadlines and next meeting. Use when the student texts "Groupie import" followed by a 6 character code, optionally with an assignment name, for example "Groupie import K7Q2MX Project 2", or "Groupie import" with just an assignment name once this chat is linked, for example "Groupie import Project 2", or pastes a Groupie pair code and asks to send an assignment; when they text "Groupie link" with a code; or when they ask "Groupie status", "what's due on Groupie", "my Groupie tasks", or "Groupie unlink".
metadata:
  version: "3"
---

# Groupie Import

Groupie is a Telegram bot that splits a group project among teammates. A teammate types /canvas in the group chat,
Groupie shows a one-time code, and the student texts that code here. This Skill reads the assignment from the
student's Canvas and, after the student says yes, sends it to Groupie's server so the group gets a task list.
A student can also send /canvas in a private chat with Groupie for their own assignment: the code works the same
way, and Groupie sends them a step-by-step plan in Telegram.

The same Skill links this chat to Groupie ("Groupie link CODE"), so the student can ask "Groupie status" here and
get their tasks, deadlines and next meeting. See "Groupie link and status" below.

No setup: the script uses only Python's standard library.

## When to use (import)

- "Groupie import K7Q2MX"
- "Groupie import K7Q2MX Project 2"
- "groupie import k7q2mx the CSE 340 final project"
- "Groupie import Project 2" (no code: only works once this chat is linked, see "Groupie link and status")

The code is 6 characters from the letters and digits ABCDEFGHJKMNPQRSTUVWXYZ23456789 (no 0, O, 1, I or L).
Treat lowercase as uppercase. Everything after the code is the assignment name, which is optional. A code works
once and lasts 30 minutes.

Code or no code:

- If the message has a 6 character code, use it, even when this chat is linked.
- If it has no code, the script uses this chat's Groupie link instead (the linked import). Groupie then works out
  the group itself, or asks "Which group?". If this chat is not linked, the script prints a line asking for the
  code; text it to the student.

Do not use it for reminders, study plans, grades, calendar links or anything that changes Canvas. This Skill never
handles the Canvas calendar feed; if the student asks about it, tell them to paste the feed link in the Groupie
Mini App themselves.

## Steps

1. Check the code. If there is one and it is not 6 characters from the alphabet above, ask the student to copy it
   again from the Groupie message in their group, and stop. If there is no code at all, go on: the script decides
   (linked import, or it asks for a code).
2. Read the assignment with Cubbie's Canvas ability. Use the name the student gave; with no name, look at
   assignments due in the next 30 days. Get the course name and course code, the assignment title, the due date
   and time with its time zone, the points possible if Canvas shows them, the full description as plain text,
   and the assignment's Canvas link.
   - If Canvas is not connected, ask the student to connect Canvas and stop here.
   - If several assignments match (or the student gave no name and several are due), list up to three (course,
     title, due date) and ask which one. One question. If the answer still fits more than one, ask again the same
     way until one assignment is left. Never pick one for the student.
   - If nothing matches, say so and ask for the exact assignment name.
3. Read the files attached to that assignment (PDFs, documents) with Cubbie's Canvas and files abilities, and keep
   the text of each one, up to five files. Send only the text, never the file itself. Skip a file whose text
   cannot be read and mention it in the summary.
4. Do not look up a rubric separately. Only if Canvas returned rubric criteria with points as part of the
   assignment, keep each criterion's name, points and rating descriptions.
5. Show the student a short summary and ask, in these words:

   ```text
   CSE 340: Project 2: Campus Food Finder
   Due Fri Nov 20, 11:59 pm MST, 100 points
   1 attached file (project2-spec.pdf)

   Send this to your Groupie group? It goes to Groupie's server (groupie's website) so your group can split it.
   ```

   If the student made the code in a private chat with Groupie (they said it is for themselves, or it is not a
   group project), ask "Send this to Groupie? It goes to Groupie's server so Groupie can send you a plan in
   Telegram." instead.

   Wait for the student's answer. Send nothing unless they say yes. If they say no, say it was not sent.
6. After the yes, write a file such as `/tmp/groupie-import.json` with the same shape as
   `assets/sample_payload.json` (copy that file and replace its values):
   - the pair code, in capitals; with no code, leave `pairCode` out of the file entirely
   - the course name, and the course code if Canvas has one
   - the assignment title; the due date as an ISO 8601 date and time with the offset from Canvas, such as
     2026-11-20T23:59:00-07:00, or null when Canvas has no due date (never guess one); the points possible, or
     null; the full description as plain text; the Canvas link
   - one attachment per file, with its file name, its type (such as application/pdf) and its text
   - the rubric criteria only when step 4 found them; otherwise leave the rubric out
7. Run from this Skill's folder:

   ```bash
   python3 scripts/send.py --file /tmp/groupie-import.json
   ```

   With no code and a linked chat, the same command sends the linked import.

8. Text the student exactly the line or lines the script printed, word for word, whatever the exit code. Do not
   reword them, add to them, or sum up a failure as "rejected"; each line already says what happened and what to
   do next. On success it prints "Sent to Groupie. Check your group." for a group code, or "Sent to Groupie. Check
   your Telegram messages." for a personal code.
   - If the files were too big, the script already sent the assignment text without them, on its own. It prints
     "The files are too big to send. I'll send the assignment text instead." and then the result of that second
     try. Send both lines, and do not run it again.
   - The script also prints short notes for you, such as the error name and a request id. Every note line begins
     with "error:", "requestId:", "retry without attachments:" or "target". Never text a line that begins with
     one of those to the student; text only the other lines.
   - Linked import, exit code 14: the script printed a question like "Which group? 1. CSE 340 Team 2. HST 100
     3. Just me". Text it as is and wait. When the student answers with a number (or a name from the list, which
     you turn into its number), run the same command again with `--target` and that number, for example
     `python3 scripts/send.py --file /tmp/groupie-import.json --target 2`. Do not ask "Send this?" again; they
     already said yes. Then text what it prints, as above.
   - Linked import, exit code 15: the link is gone. Text the line it printed ("Your Groupie link expired. Send
     /imessage in Groupie to relink.") and stop.

To try the script without sending anything, run `python3 scripts/send.py --sample`.

## Groupie link and status

### When to use

- "Groupie link K7Q2MX" links this chat to the student's Groupie account.
- "Groupie status", "what's due on Groupie", "my Groupie tasks", "what are my Groupie tasks" show their status.
- "Groupie unlink" disconnects this chat from Groupie.

The link code uses the same 6 character alphabet as the import code. It comes from sending /imessage to
@CubbieGroupieBot in Telegram.

### Steps

1. "Groupie link CODE": run `python3 scripts/link.py --code CODE` from this Skill's folder. It sends the code to
   Groupie and saves the token Groupie returns under the student's home folder (`~/.config/groupie/token`, only
   the student can read it). Nothing else is needed from the student: the token is made by Groupie for them, it is
   not a password or a Canvas login, so never ask for one. On exit code 0, reply with the line it printed.
   Once linked, "Groupie import <assignment name>" works without a code (see "Code or no code" above).
2. "Groupie status" and the other status questions: run `python3 scripts/status.py`. On exit code 0, send what it
   printed as it is (it is already short plain text, with the meeting link on its own line).
3. "Groupie unlink": run `python3 scripts/status.py --unlink` and reply with the line it printed.

If no token is saved, status.py exits with 12; then text: "Send /imessage to @CubbieGroupieBot in Telegram first,
then text me the code it gives you." Never show the token or the contents of `~/.config/groupie/` to anyone.

To try them without sending anything: `python3 scripts/link.py --code K7Q2MX --sample` and
`python3 scripts/status.py --sample`.

### When link or status fails

| Exit code | Meaning | What to text the student |
| --- | --- | --- |
| 3 | The link code is not a Groupie code | "That code doesn't look right. Copy it again from Groupie's message in Telegram." |
| 5 | Groupie's server is not reachable | "I couldn't reach Groupie right now. Want me to try again in a minute?" |
| 7 | This Skill is out of date | "This Groupie Skill needs an update. Text me its GitHub link again to update it." |
| 8 | The link code was already used, expired or is unknown | "That code is used or expired. Send /imessage to @CubbieGroupieBot in Telegram for a fresh one." |
| 10 | Too many tries | "Groupie says too many tries right now. Try again in a few minutes." |
| 11 | Other server error | "Groupie had a problem on its end. Try again later." |
| 12 | Not linked, or the link was turned off in Telegram with /unlink | "Send /imessage to @CubbieGroupieBot in Telegram first, then text me the code it gives you." |

## What is sent

Only what the student saw in the summary: course, assignment details, due date, points, description, the text of
the attached files and rubric criteria if Canvas returned them. It goes to Groupie's server at
devhacks-groupie-production.up.railway.app and is posted in the student's Groupie group (or, for a personal
code, sent to the student in Telegram). No Canvas login, Canvas token or calendar link is ever sent. Linking sends
only the link code; status and the linked import send only Groupie's own token, back to Groupie.

## Replies

Short, plain text, one idea per message, no Markdown. Put links on their own line. The success reply is a single
line. Every failure reply is one line that tells the student what to do next.

## When something fails

The script prints the student's line itself, so there is nothing to look up: text it exactly. The table is only so
you know what each exit code means.

| Exit code | Meaning | What the script prints for the student |
| --- | --- | --- |
| 2 | The payload file is missing or not readable | Nothing. Rewrite the file from step 6 and run the script once more; if it fails again, say the import did not work |
| 3 | The code is not a Groupie code, or there is no code and this chat is not linked | "That code doesn't look right. Copy it again from the Groupie message in your group." (with no code: a line asking for the code) |
| 4 | Something required is missing or too long | Nothing. Fix what the script named (for example a missing title, or a description over 60,000 characters, which you shorten) and run once more |
| 5 | Groupie's server is not reachable | "Groupie hit a snag on its side. Your code still works, try again in a minute." |
| 6 | Groupie could not use the assignment | "Groupie hit a snag on its side. Your code still works, try again in a minute." |
| 7 | This Skill is out of date | "This Groupie skill is out of date. Reinstall it with: Install this skill: https://github.com/katsdivi/groupie-cubbie-skill/tree/main/groupie-import" |
| 8 | The code expired or is unknown | "That code expired. Send /canvas in your Groupie group for a new one, then text me again." |
| 9 | The group already has its assignment | "That group already has its assignment. Send /reset in the group to start over, then /canvas for a new code." |
| 10 | Too many tries | "Too many tries, wait a minute and try again." |
| 11 | Other server error | "Groupie hit a snag on its side. Your code still works, try again in a minute." |
| 12 | The code was already used | "That code was already used. Grab a fresh one with /canvas." |
| 13 | Too big even without the files | "Groupie hit a snag on its side. Your code still works, try again in a minute.", after "The files are too big to send. I'll send the assignment text instead." when there were files to drop |
| 14 | Linked import: Groupie needs to know which group | The "Which group? 1. ... 2. ... 3. Just me" question. Rerun with `--target <number>` after the answer (step 8) |
| 15 | Linked import: the link was turned off or is unknown | "Your Groupie link expired. Send /imessage in Groupie to relink." |

Run the script again only for exit codes 2 and 4, after fixing the file, and only once, and for exit code 14 with
the student's choice. For every other code, wait
for the student to text you again. Never say it was sent unless the script exited with 0.

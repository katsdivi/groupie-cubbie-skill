# Groupie import (Cubbie Skill)

A [Cubbie](https://cubbie.ai) Skill for the Cubbie Hackathon. Text Cubbie "Groupie import <CODE>" and it reads that assignment from your Canvas (with your yes) and sends it to your Groupie group chat on Telegram, where Groupie splits the work fairly.

The Skill is in [`groupie-import/`](groupie-import/). To install, text Cubbie this link:

https://github.com/katsdivi/groupie-cubbie-skill/tree/main/groupie-import

Get a code by sending `/canvas` in your Groupie group on Telegram (@CubbieGroupieBot). The signing key in `groupie-import/scripts/send.py` is public by design: Cubbie cannot keep team secrets, so single-use pair codes and rate limits are what protect imports.

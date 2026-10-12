#!/usr/bin/env python3
"""Export what only Sleeper's private endpoint has: league chat and official weekly scores.

Uses Sleeper's private GraphQL endpoint, which is undocumented and may change.
Needs a logged-in user's token in the SLEEPER_TOKEN environment variable (copy
the `authorization` header from any graphql request on sleeper.com).

It also saves each week's official matchup record to data/raw/<season>/matchup_legs/.
Those hold the score as it counted at the time; the public API re-scores old
weeks after a mid-season rule change, so the two can disagree.

The raw chat is other people's conversation: it is written to ../iooi-chat-private/
and must never be committed. Only facts extracted from it belong in data/manual/.

    SLEEPER_TOKEN=... python3 scripts/fetch_chat.py
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT.parent / "iooi-chat-private"
FIELDS = ("message_id parent_id created edited author_id author_display_name author_is_bot "
          "text text_map attachment reactions pinned")


def graphql(query, field="messages"):
    # Read-only by construction: refuse anything that is not a GraphQL query.
    if not query.lstrip().startswith("query "):
        raise ValueError("only read queries are allowed against Sleeper")
    req = urllib.request.Request(
        "https://sleeper.com/graphql",
        data=json.dumps({"operationName": field, "variables": {}, "query": query}).encode(),
        headers={
            "authorization": os.environ["SLEEPER_TOKEN"],
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
    if body.get("errors"):
        raise RuntimeError(json.dumps(body["errors"]))
    return body["data"][field]


LEG_FIELDS = "roster_id matchup_id points custom_points max_points proj_points players starters"


def fetch_legs(league):
    """Official weekly matchup records, weeks 1-17. Finished seasons are fetched once."""
    out = RAW / league["season"] / "matchup_legs"
    for week in range(1, 18):
        path = out / f"{week:02d}.json"
        if path.exists() and league["status"] == "complete":
            continue
        legs = graphql(f'query matchup_legs {{ matchup_legs(league_id: "{league["league_id"]}", round: {week}) {{ {LEG_FIELDS} }} }}', "matchup_legs")
        out.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(legs, separators=(",", ":")))
        time.sleep(0.15)


def fetch_channel(league_id):
    """All messages in a league chat, oldest first. The API pages newest first."""
    messages, before = [], None
    while True:
        cursor = f', before: "{before}"' if before else ""
        page = graphql(f'query messages {{ messages(parent_id: "{league_id}"{cursor}) {{ {FIELDS} }} }}')
        page = [m for m in page if m["message_id"] != before]
        if not page:
            break
        messages.extend(page)
        before = min(page, key=lambda m: int(m["message_id"]))["message_id"]
        time.sleep(0.25)
    unique = {m["message_id"]: m for m in messages}
    return sorted(unique.values(), key=lambda m: int(m["message_id"]))


def fetch_polls(messages, polls):
    """Vote tallies from each trade's review poll. Only counts are exposed, not who voted."""
    for message in messages:
        attachment = message.get("attachment")
        if not isinstance(attachment, dict) or attachment.get("type") != "transactions":
            continue
        for item in attachment.get("data") or []:
            poll_id, trade_id = item.get("poll_id"), item.get("transaction_id")
            if not poll_id or polls.get(trade_id, {}).get("status") in ("complete", "vetoed", "cancelled"):
                continue
            poll = graphql(f'query get_poll {{ get_poll(poll_id: "{poll_id}") {{ choices votes metadata }} }}', "get_poll")
            if poll:
                polls[trade_id] = {
                    "poll_id": poll_id, "status": (poll.get("metadata") or {}).get("status"),
                    "votes": {poll["choices"][key].lower(): count for key, count in (poll.get("votes") or {}).items()},
                }
            time.sleep(0.15)


def main():
    if not os.environ.get("SLEEPER_TOKEN"):
        sys.exit("Set SLEEPER_TOKEN first (see the docstring).")
    OUT.mkdir(exist_ok=True)
    polls_path = RAW / "trade_polls.json"
    polls = json.loads(polls_path.read_text()) if polls_path.exists() else {}
    for league_file in sorted(RAW.glob("*/league.json")):
        league = json.loads(league_file.read_text())
        messages = fetch_channel(league["league_id"])
        (OUT / f"{league['season']}.json").write_text(json.dumps(messages, ensure_ascii=False, indent=1))
        fetch_legs(league)
        fetch_polls(messages, polls)
        polls_path.write_text(json.dumps(polls, separators=(",", ":")))
        print(f"{league['season']}: {len(messages)} messages, weekly records saved")


if __name__ == "__main__":
    main()

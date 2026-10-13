#!/usr/bin/env python3
"""Daily top-up of everything that needs a logged-in Sleeper token.

Three things come only from Sleeper's private endpoint:

  1. League-office events in the league chat: team renames, nickname changes,
     scoring changes, and trades going to review, passing, or being vetoed.
  2. The vote tally on each trade's review poll.
  3. The official weekly scores as they counted at the time.

This reads only the chat messages posted since the last run, keeps the system
events among them (stripped of profile details, see extract_chat.py), and adds
them to data/chat_events/. The messages themselves are never written anywhere,
so this is safe to run on a public build machine. Every event carries Sleeper's
own timestamp, so two changes in one day are two events, and a run missed
because the token had expired is made up in full by the next run that works.

    SLEEPER_TOKEN=... python3 scripts/sync_chat.py

Exit codes: 0 done, 2 no token or the token was refused (nothing changed on disk).
Everything here is a read query. Nothing in the league is ever modified.
"""
import json
import os
import sys
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import extract_chat
import fetch_chat

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
EVENTS = ROOT / "data" / "chat_events"
CURSOR = EVENTS / "cursor.json"   # per season: the newest chat message already read
SEASONS_BACK = 2                  # the current league and the one before it; older chats are closed


def new_messages(league_id, after):
    """Messages newer than `after`, oldest first. The API pages newest first, so stop at the first page that reaches it."""
    found, before = [], None
    while True:
        cursor = f', before: "{before}"' if before else ""
        page = fetch_chat.graphql(f'query messages {{ messages(parent_id: "{league_id}"{cursor}) {{ {fetch_chat.FIELDS} }} }}')
        page = [m for m in page if m["message_id"] != before]
        if not page:
            break
        found.extend(m for m in page if int(m["message_id"]) > after)
        before = min(page, key=lambda m: int(m["message_id"]))["message_id"]
        if int(before) <= after:
            break
        time.sleep(0.25)
    return sorted({m["message_id"]: m for m in found}.values(), key=lambda m: int(m["message_id"]))


def sync_events(season, league_id, cursor):
    path = EVENTS / f"{season}.json"
    events = json.loads(path.read_text()) if path.exists() else []
    known = {e["message_id"] for e in events}
    after = int(cursor.get(season) or max((int(e["message_id"]) for e in events), default=0))
    messages = new_messages(league_id, after)
    fresh = [event for m in messages if (event := extract_chat.event_from(m)) and event["message_id"] not in known]
    if fresh:
        events = sorted(events + fresh, key=lambda e: int(e["message_id"]))
        path.write_text(json.dumps(events, ensure_ascii=False, separators=(",", ":")))
    if messages:
        cursor[season] = messages[-1]["message_id"]
    return events, len(messages), fresh


def sync_polls(events, polls):
    """Vote tallies for every trade whose review poll was still open, or unseen, at the last run."""
    changed = 0
    for event in events:
        if event["type"] != "trade_status":
            continue
        for item in event["data"] or []:
            poll_id, trade_id = item.get("poll_id"), item.get("transaction_id")
            if not poll_id or polls.get(trade_id, {}).get("status") in ("complete", "vetoed", "cancelled"):
                continue
            poll = fetch_chat.graphql(f'query get_poll {{ get_poll(poll_id: "{poll_id}") {{ choices votes metadata }} }}', "get_poll")
            if poll:
                entry = {"poll_id": poll_id, "status": (poll.get("metadata") or {}).get("status"),
                         "votes": {poll["choices"][key].lower(): count for key, count in (poll.get("votes") or {}).items()}}
                changed += polls.get(trade_id) != entry
                polls[trade_id] = entry
            time.sleep(0.15)
    return changed


def main():
    if not os.environ.get("SLEEPER_TOKEN"):
        print("sync_chat: no SLEEPER_TOKEN set; league-office events, vote tallies and official scores were not refreshed")
        return 2
    cursor = json.loads(CURSOR.read_text()) if CURSOR.exists() else {}
    polls_path = RAW / "trade_polls.json"
    polls = json.loads(polls_path.read_text()) if polls_path.exists() else {}
    leagues = [json.loads(p.read_text()) for p in sorted(RAW.glob("*/league.json"))][-SEASONS_BACK:]
    try:
        for league in leagues:
            events, read, fresh = sync_events(league["season"], league["league_id"], cursor)
            tallies = sync_polls(events, polls)
            if league["status"] not in ("pre_draft", "drafting"):   # no weekly scores before the season is set up
                fetch_chat.fetch_legs(league)
            kinds = ", ".join(f"{sum(1 for e in fresh if e['type'] == k)} {k}" for k in sorted({e["type"] for e in fresh}))
            print(f"{league['season']}: {read} new messages read, {len(fresh)} events kept{f' ({kinds})' if kinds else ''}, {tallies} vote tallies updated")
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            print(f"sync_chat: Sleeper refused the token (HTTP {err.code}); it has probably expired. Nothing was changed.")
            return 2
        raise
    except RuntimeError as err:
        if any(word in str(err).lower() for word in ("auth", "token", "login", "permission", "forbidden")):
            print(f"sync_chat: Sleeper refused the token ({err}); it has probably expired. Nothing was changed.")
            return 2
        raise
    polls_path.write_text(json.dumps(polls, separators=(",", ":")))
    CURSOR.write_text(json.dumps({**cursor, "synced": datetime.now(timezone.utc).isoformat(timespec="minutes")}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

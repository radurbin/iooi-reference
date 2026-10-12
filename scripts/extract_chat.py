#!/usr/bin/env python3
"""Pull league-office events out of the private chat export into data/chat_events/.

Only the Sleeper system messages the build reads are kept: team renames, player
nickname changes, scoring changes and trade review outcomes. Anything a person
typed, plus polls, photos and GIFs, stays in the private export and is never
written here. These files are committed to a public repository, so each event is
also stripped of Sleeper profile details (usernames, avatars, settings); only
the numeric user id is kept, to tie an event to a manager.

    python3 scripts/extract_chat.py
"""
import html
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAT = ROOT.parent / "iooi-chat-private"
OUT = ROOT / "data" / "chat_events"
KEEP = {"team_name_update", "scoring_settings_change", "player_nickname_update"}


def scrub(value):
    """Reduce every embedded user profile to its id."""
    if isinstance(value, dict):
        return {k: {"user_id": v.get("user_id")} if k == "user" and isinstance(v, dict) else scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def is_trade_status(attachment):
    data = attachment.get("data")
    return attachment.get("type") == "transactions" and bool(data) and data[0].get("type") == "trade"


def event_from(message):
    """The league-office event in a chat message, stripped for publication, or None for anything else."""
    attachment = message.get("attachment")
    if not isinstance(attachment, dict):
        return None
    kind = attachment.get("type")
    if kind not in KEEP and not is_trade_status(attachment):
        return None
    created = datetime.fromtimestamp(message["created"] / 1000, tz=timezone.utc)
    return {
        "message_id": message["message_id"],
        "created": created.isoformat(timespec="seconds"),
        "type": "trade_status" if kind == "transactions" else kind,
        # Trade posts say only "pending", "vetoed" and so on; the others name a user, so their text is dropped.
        "text": html.unescape(message.get("text") or "") if kind == "transactions" else "",
        "data": scrub(attachment.get("data")),
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for chat_file in sorted(CHAT.glob("*.json")):
        events = [event for message in json.loads(chat_file.read_text()) if (event := event_from(message))]
        (OUT / chat_file.name).write_text(json.dumps(events, ensure_ascii=False, separators=(",", ":")))
        print(f"{chat_file.stem}: {len(events)} events")


if __name__ == "__main__":
    main()

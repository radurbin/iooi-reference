#!/usr/bin/env python3
"""Dump raw Sleeper API responses for every iooi season into data/raw/.

Completed seasons are frozen: once dumped they are skipped unless --force is
passed. The in-progress season is always re-fetched.

    python3 scripts/fetch.py            # refresh current season, fill gaps
    python3 scripts/fetch.py --force    # re-fetch everything
    python3 scripts/fetch.py --players  # also refresh the NFL player list

Weekly NFL stat lines and projections come from Sleeper's undocumented stats
service. They are large, so they go to data/cache/ (not committed); the build
step writes the trimmed versions the site uses.

Everything here is a GET request. Nothing in the league is ever modified.
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.sleeper.app/v1"
# Newest league; older seasons are found by following previous_league_id.
CURRENT_LEAGUE_ID = "1314038209503567872"   # the 2026 league; only a starting point, see newest_league()
COMMISSIONER_ID = "961678741954990080"       # Sleeper user whose league list is searched for each new season
STATS_API = "https://api.sleeper.com"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
CACHE = RAW.parent / "cache"
NFL_WEEKS = range(1, 19)
POSITIONS = "".join(f"&position[]={p}" for p in ("QB", "RB", "WR", "TE", "K", "DEF"))
MATCHUP_WEEKS = range(1, 19)
TRANSACTION_WEEKS = range(0, 19)


def get(path, base=API):
    request = urllib.request.Request(f"{base}/{path}", headers={"user-agent": "Mozilla/5.0"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=60) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
        finally:
            time.sleep(0.1)


PLAYER_POSITIONS = {"QB", "RB", "FB", "WR", "TE", "K", "DEF"}
PLAYER_FIELDS = ("player_id", "full_name", "first_name", "last_name", "position", "fantasy_positions", "team", "number", "age", "birth_date",
                 "height", "weight", "college", "years_exp", "status", "active", "metadata")


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))


def fetch_season(league):
    league_id = league["league_id"]
    out = RAW / league["season"]
    # Sleeper attaches the league chat's latest message to this record; none of it belongs in a public repository.
    save(out / "league.json", {k: v for k, v in league.items() if not k.startswith("last_")})
    for name in ("users", "rosters", "winners_bracket", "losers_bracket", "traded_picks"):
        save(out / f"{name}.json", get(f"league/{league_id}/{name}"))
    for week in MATCHUP_WEEKS:
        save(out / "matchups" / f"{week:02d}.json", get(f"league/{league_id}/matchups/{week}"))
    for week in TRANSACTION_WEEKS:
        save(out / "transactions" / f"{week:02d}.json", get(f"league/{league_id}/transactions/{week}"))
    drafts = get(f"league/{league_id}/drafts")
    save(out / "drafts.json", drafts)
    for draft in drafts:
        draft_id = draft["draft_id"]
        save(out / "drafts" / f"{draft_id}.json", get(f"draft/{draft_id}"))
        save(out / "drafts" / f"{draft_id}_picks.json", get(f"draft/{draft_id}/picks"))
        save(out / "drafts" / f"{draft_id}_traded_picks.json", get(f"draft/{draft_id}/traded_picks"))


def fetch_nfl_weeks(season, last_week, refresh_from, force):
    """Stat lines and projections for every NFL player, weeks 1..last_week.

    Weeks before refresh_from are final and are kept if already on disk.
    """
    for kind in ("stats", "projections"):
        for week in range(1, last_week + 1):
            path = CACHE / kind / season / f"{week:02d}.json"
            if path.exists() and week < refresh_from and not force:
                continue
            save(path, get(f"{kind}/nfl/{season}/{week}?season_type=regular{POSITIONS}", base=STATS_API))


def newest_league():
    """The league id of the newest season.

    Sleeper gives each season a new league id that points back to the one before, but not forward. So start from the
    newest season already on disk and look, among the commissioner's leagues for each following year, for the one that
    points back to it.
    """
    saved = sorted(RAW.glob("*/league.json"))
    league = json.loads(saved[-1].read_text()) if saved else get(f"league/{CURRENT_LEAGUE_ID}")
    while True:
        later = get(f"user/{COMMISSIONER_ID}/leagues/nfl/{int(league['season']) + 1}") or []
        successor = next((entry for entry in later if entry.get("previous_league_id") == league["league_id"]), None)
        if not successor:
            return league["league_id"]
        print(f"found the {successor['season']} league")
        league = successor


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="re-fetch completed seasons too")
    parser.add_argument("--players", action="store_true", help="refresh the NFL player list (large; at most once a day)")
    args = parser.parse_args()

    nfl_state = get("state/nfl")
    save(RAW / "nfl_state.json", nfl_state)

    league_id = newest_league()
    while league_id and league_id != "0":
        league = get(f"league/{league_id}")
        season = league["season"]
        saved_path = RAW / season / "league.json"
        # Frozen only if the copy on disk was itself taken after the season ended.
        frozen = saved_path.exists() and json.loads(saved_path.read_text()).get("status") == "complete"
        if frozen and not args.force:
            print(f"{season}: complete, already dumped, skipping")
        else:
            print(f"{season}: fetching ({league['status']})")
            fetch_season(league)
        if league["status"] == "complete":
            # Once a finished season's trimmed copy is in data/nfl/, its 36 large weekly files are not needed again.
            if args.force or not frozen or not (RAW.parent / "nfl" / f"{season}.json").exists():
                fetch_nfl_weeks(season, NFL_WEEKS[-1], NFL_WEEKS[-1] + 1, args.force)
        elif season == nfl_state["season"] and nfl_state.get("season_type") in ("regular", "post"):
            # Re-pull last week too, to pick up stat corrections. In the NFL playoffs the week counter restarts,
            # so take the whole regular season; in the preseason and offseason there is nothing to take.
            week = min(nfl_state["week"], NFL_WEEKS[-1]) if nfl_state["season_type"] == "regular" else NFL_WEEKS[-1]
            fetch_nfl_weeks(season, week, week - 1, args.force)
        league_id = league.get("previous_league_id")

    players_path = RAW / "players_nfl.json"
    if args.players or not players_path.exists():
        print("players: fetching NFL player list")
        # The full list is 14 MB; keep only skill players, kickers and defenses, and only the fields the build reads.
        save(players_path, {pid: {k: p[k] for k in PLAYER_FIELDS if p.get(k) is not None}
                            for pid, p in get("players/nfl").items() if PLAYER_POSITIONS & {p.get("position"), *(p.get("fantasy_positions") or [])}})
    # Stamp the run so the site can say how fresh its data is.
    save(RAW / "fetched.json", {"at": datetime.now(timezone.utc).isoformat(timespec="minutes")})


if __name__ == "__main__":
    main()

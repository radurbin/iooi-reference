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

Injuries come from nflverse's public weekly NFL roster statuses and official
injury reports (see fetch_injuries). Only the rows the site needs are kept, in
data/raw/<season>/injuries.json.

Everything here is a GET request. Nothing in the league is ever modified.
"""
import argparse
import csv
import gzip
import io
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
                 "height", "weight", "college", "years_exp", "status", "active", "metadata", "injury_status", "injury_body_part", "injury_start_date")


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


NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
INJURY_POSITIONS = {"QB", "RB", "FB", "WR", "TE"}


def nflverse_csv(path):
    """Rows of one nflverse release file, or None if it is not published (yet). The gzipped copy is a twentieth of the
    size, but not every season has one, so the plain file is the fallback."""
    rows = nflverse_get(f"{path}.gz", gzip.decompress)
    return rows if rows is not None else nflverse_get(path, lambda b: b)


def nflverse_get(path, unpack):
    request = urllib.request.Request(f"{NFLVERSE}/{path}", headers={"user-agent": "Mozilla/5.0"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=120) as resp:
                return list(csv.DictReader(io.StringIO(unpack(resp.read()).decode("utf-8-sig"))))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(2 ** attempt)


def fetch_injuries(season, complete, force):
    """Each skill player's NFL roster status and injury report line, for every week he was anything but plainly active.

    Two nflverse files per season: weekly rosters (status: active, inactive on game day, injured reserve, PUP and so on;
    they carry Sleeper ids, and a team on its bye has no rows that week) and the official injury reports (Out,
    Doubtful, Questionable and the injury, keyed by NFL id, matched to Sleeper through the rosters). A season is kept
    once it was saved after the league season ended. A failed or odd download never replaces a good file: the site
    keeps the last one, and the run says so.
    """
    path = RAW / season / "injuries.json"
    if path.exists() and not force and json.loads(path.read_text()).get("complete"):
        return
    try:
        rosters = nflverse_csv(f"weekly_rosters/roster_weekly_{season}.csv")
        reports = nflverse_csv(f"injuries/injuries_{season}.csv")
        if rosters is None:
            # Normal before a season's first games. Once a season has injury data, a missing file means nflverse moved it.
            print(f"::warning::{season} nflverse weekly rosters not found; keeping the copy on disk" if path.exists()
                  else f"{season}: no nflverse weekly rosters yet, injuries skipped")
            return
        need = {"week", "team", "position", "status", "status_description_abbr", "gsis_id", "sleeper_id", "game_type"}
        if not need <= set(rosters[0]) or (reports and not {"week", "gsis_id", "report_status", "report_primary_injury", "practice_primary_injury"} <= set(reports[0])):
            raise ValueError("nflverse columns changed")
        sleeper_of = {r["gsis_id"]: r["sleeper_id"] for r in rosters if r["gsis_id"] and r["sleeper_id"]}
        report = {}
        for r in reports or []:
            pid = sleeper_of.get(r["gsis_id"])
            if pid and r.get("game_type", "REG") == "REG" and (r["report_status"] or r["report_primary_injury"] or r["practice_primary_injury"]):
                report[(r["week"], pid)] = (r["report_status"], r["report_primary_injury"] or r["practice_primary_injury"])
        weeks = {}
        for r in rosters:
            pid, week = r["sleeper_id"], r["week"]
            if not pid or r["game_type"] != "REG" or r["position"] not in INJURY_POSITIONS:
                continue
            status, injury = report.get((week, pid), ("", ""))
            if r["status"] == "ACT" and not status and not injury:
                continue
            weeks.setdefault(str(int(week)), {})[pid] = [r["team"], r["status"], r["status_description_abbr"], status, injury]
        if not weeks:
            raise ValueError("no rows")
        # The NFL report names nothing for a player who goes straight to injured reserve. Sleeper's injury note for him
        # is kept as a sixth field: taken today for the latest week, and carried over from the last run for earlier
        # weeks, since Sleeper only ever shows the note as it is now.
        old = json.loads(path.read_text())["weeks"] if path.exists() else {}
        for week, rows in weeks.items():
            for pid, row in rows.items():
                kept = (old.get(week, {}).get(pid) or [])[5:6]
                if kept:
                    row.append(kept[0])
        players_path = RAW / "players_nfl.json"
        notes = json.loads(players_path.read_text()) if players_path.exists() else {}
        latest = max(weeks, key=int)
        for pid, row in weeks[latest].items():
            info = notes.get(pid) or {}
            if len(row) == 5 and info.get("injury_status") and info.get("injury_body_part"):
                row.append(info["injury_body_part"])
    except Exception as e:   # never fatal: the rest of the refresh goes on
        print(f"::warning::{season} injuries not refreshed ({e}); keeping the copy on disk")
        return
    save(path, {"season": season, "complete": complete, "source": "nflverse weekly rosters and injury reports",
                "weeks": {w: dict(sorted(rows.items())) for w, rows in sorted(weeks.items(), key=lambda x: int(x[0]))}})
    print(f"{season}: injuries for {len(weeks)} weeks")


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
    injury_seasons = []
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
        injury_seasons.append((season, league["status"] == "complete"))
        league_id = league.get("previous_league_id")

    players_path = RAW / "players_nfl.json"
    if args.players or not players_path.exists():
        print("players: fetching NFL player list")
        # The full list is 14 MB; keep only skill players, kickers and defenses, and only the fields the build reads.
        save(players_path, {pid: {k: p[k] for k in PLAYER_FIELDS if p.get(k) is not None}
                            for pid, p in get("players/nfl").items() if PLAYER_POSITIONS & {p.get("position"), *(p.get("fantasy_positions") or [])}})
    # After the player list, so the injury file can keep today's Sleeper injury notes.
    for season, complete in injury_seasons:
        fetch_injuries(season, complete, args.force)
        # Stamp the run so the site can say how fresh its data is.
    save(RAW / "fetched.json", {"at": datetime.now(timezone.utc).isoformat(timespec="minutes")})


if __name__ == "__main__":
    main()

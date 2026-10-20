#!/usr/bin/env python3
"""Turn the raw Sleeper dump into the JSON files the site reads (data/site/).

    python3 scripts/build.py

Inputs:  data/raw/ (fetch.py), data/chat_events/ (extract_chat.py), data/manual/
         (hand-edited), and data/cache/ when present.
Outputs: data/nfl/<season>.json   trimmed weekly points and projections (committed,
                                  so the build still works without the cache)
         data/site/               league, franchises, managers, seasons, games, players
"""
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
RAW, CACHE, MANUAL = ROOT / "data" / "raw", ROOT / "data" / "cache", ROOT / "data" / "manual"
EVENTS, NFL, SITE = ROOT / "data" / "chat_events", ROOT / "data" / "nfl", ROOT / "data" / "site"

WEEKS = range(1, 18)          # the league plays NFL weeks 1-17; week 18 is ignored
REGULAR_WEEKS = range(1, 15)
FLEX = {"FLEX": {"RB", "WR", "TE"}, "SUPER_FLEX": {"QB", "RB", "WR", "TE"}}
WINNERS_LABEL = {(1, None): "First Round", (2, None): "Semifinal", (2, 5): "Fifth Place Game",
                 (3, 1): "Dynasty Championship", (3, 3): "Third Place Game"}
TOILET_LABEL = {(1, None): "Toilet Bowl First Round", (2, None): "Toilet Bowl Semifinal", (2, 5): "Seventh Place Game",
                (3, 1): "Toilet Bowl Final", (3, 3): "Ninth Place Game"}
FINISH_LABEL = {1: "Champion", 2: "Runner-up", 12: "Ultimate Loser"}


def load(path):
    return json.loads(Path(path).read_text())


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def r2(x):
    return round(x + 1e-9, 2)


def day_of(ms):
    """Calendar day of a Sleeper timestamp in league time (US Central)."""
    return datetime.fromtimestamp(ms / 1000, tz=ZoneInfo("America/Chicago")).date().isoformat()


def pct(w, l, t=0):
    return round((w + t / 2) / (w + l + t), 3) if w + l + t else None


# ---------------------------------------------------------------- NFL weekly data

def scoring_for_week(scoring, changes, week_start):
    """League scoring as it stood when this week was played.

    The league file holds the season's final settings. Any change made after the
    week finished (Tuesday) is undone here so old weeks keep the score that counted.
    """
    week_end = (date.fromisoformat(week_start) + timedelta(days=5)).isoformat()
    scoring = dict(scoring)
    for change in sorted(changes, key=lambda c: c["created"], reverse=True):
        if change["created"][:10] > week_end:
            for key, (old, _new) in change["data"].items():
                scoring[key] = old or 0
    return scoring


def fired_subs(starters, players, subs, played, points, official=None):
    """The lineup after Sleeper's auto-subs, and the swaps made, as [(starter, sub)].

    subs is the manager's plan for the week, {starter: bench player}; it is kept whether or not a sub was needed.
    - Week finished: Sleeper has already put the sub in the lineup it reports, so the swap is only noted.
    - Week in progress: Sleeper still lists the starter. The sub goes in once the starter has missed his game and
      the sub has played; until then, or if the starter plays, nothing changes.
    played: players with an NFL game that week; points: {player: points}."""
    lineup, swaps, made = list(starters), [], []
    for starter, sub in (subs or {}).items():
        if starter not in lineup and sub in lineup:
            swaps.append((starter, sub))   # already applied by Sleeper
        elif starter in lineup and sub in players and sub not in lineup and starter not in played and sub in played:
            lineup[lineup.index(starter)] = sub
            made.append((starter, sub))
    # A swap made here must agree with Sleeper's official score once there is one.
    if made and official is not None and abs(sum(points.get(pid, 0.0) for pid in lineup if pid != "0") - official) > 0.01:
        return list(starters), swaps
    return lineup, swaps + made


def trim_nfl(season, final_scoring, changes):
    """Score every NFL player's stat line and projection under league rules."""
    out_path = NFL / f"{season}.json"
    if not (CACHE / "stats" / season).exists():
        # A new season has no NFL weeks yet.
        return load(out_path) if out_path.exists() else {"season": season, "weeks": {}}
    weeks, team_in = {}, defaultdict(dict)   # team_in: player -> {week: NFL team he played for}
    upcoming = None   # the first week with projections but no games played yet (Tuesday to Thursday)
    for week in WEEKS:
        stats_path = CACHE / "stats" / season / f"{week:02d}.json"
        rows = load(stats_path) if stats_path.exists() else []
        if not rows:
            proj_path = CACHE / "projections" / season / f"{week:02d}.json"
            ahead = load(proj_path) if proj_path.exists() and upcoming is None else []
            if ahead and any(r.get("date") for r in ahead):
                start = min(r["date"] for r in ahead if r.get("date"))
                scoring = scoring_for_week(final_scoring, changes, start)
                proj = {r["player_id"]: r2(sum(v * scoring.get(k, 0) for k, v in (r.get("stats") or {}).items())) for r in ahead}
                upcoming = {"week": week, "start": start, "proj": {pid: pts for pid, pts in proj.items() if pts}}
            continue
        start = min(r["date"] for r in rows if r.get("date"))
        scoring = scoring_for_week(final_scoring, changes, start)

        def score(row):
            return r2(sum(v * scoring.get(k, 0) for k, v in (row.get("stats") or {}).items()))

        proj_path = CACHE / "projections" / season / f"{week:02d}.json"
        proj = {r["player_id"]: score(r) for r in load(proj_path)} if proj_path.exists() else {}
        for r in rows:
            if (r["stats"].get("gp") or 0) > 0 and r.get("team"):
                team_in[r["player_id"]][week] = r["team"]
        weeks[str(week)] = {
            "start": start,
            # Only players who actually played; Sleeper also lists inactive players with empty lines.
            "points": {r["player_id"]: score(r) for r in rows if (r["stats"].get("gp") or 0) > 0 or score(r)},
            "proj": {pid: pts for pid, pts in proj.items() if pts},
            # NFL teams that have played this week, so a player who scored 0 shows 0 rather than "not yet".
            "played": sorted({r["team"] for r in rows if (r["stats"].get("gp") or 0) > 0 and r.get("team")}),
        }
    # NFL bye weeks: a player is on a bye when the team he last played for (or next plays for) has no game that week.
    for key, entry in weeks.items():
        week = int(key)
        playing = {teams[week] for teams in team_in.values() if week in teams}
        entry["bye"] = sorted(pid for pid, teams in team_in.items()
                              if week not in teams and teams[min(teams, key=lambda w: (abs(w - week), w > week))] not in playing)
    # NFL team(s) each player appeared for, in order, so a season line can say who he played for.
    teams = {pid: list(dict.fromkeys(by_week[w] for w in sorted(by_week))) for pid, by_week in sorted(team_in.items())}
    data = {"season": season, "weeks": weeks, "teams": teams, **({"upcoming": upcoming} if upcoming else {})}
    dump(out_path, data)
    return data


# ---------------------------------------------------------------- people

class People:
    def __init__(self):
        self.managers = load(MANUAL / "managers.json")
        self.by_user = {uid: slug for slug, m in self.managers.items() for uid in m["sleeper_user_ids"]}
        ownership = load(MANUAL / "ownership.json")
        self.stints = {int(k): v for k, v in ownership.items() if not k.startswith("_")}

    def owners_on(self, franchise, day):
        return [s["manager"] for s in self.stints[franchise] if s["from"] <= day and (s["to"] is None or day < s["to"])]


# ---------------------------------------------------------------- lineups

def best_lineup(points, positions, slots):
    """Highest possible score from these players (Max PF)."""
    pool = sorted(points.items(), key=lambda kv: -kv[1])
    used, total = set(), 0.0
    for slot in sorted(slots, key=lambda s: (s in FLEX, s == "SUPER_FLEX")):
        allowed = FLEX.get(slot, {slot})
        for pid, pts in pool:
            if pid not in used and allowed & positions.get(pid, set()):
                used.add(pid)
                total += max(pts, 0)
                break
    return r2(total)


# ---------------------------------------------------------------- season loading

def rookie_year_of(raw, state):
    """The year a player entered the NFL. Sleeper's own record where it has one; otherwise counted back from his
    years of experience, which can be a year out in the offseason while Sleeper rolls its figures over."""
    if raw.get("position") == "DEF":
        return None
    recorded = (raw.get("metadata") or {}).get("rookie_year")
    if recorded and str(recorded).isdigit():
        return int(recorded)
    return int(state["season"]) - raw["years_exp"] if raw.get("years_exp") is not None else None


def weeks_played(state, season, status):
    """How many fantasy weeks of a season are finished. Preseason and offseason weeks never count as games."""
    if status == "complete":
        return WEEKS[-1]
    if state["season"] != season or status in ("pre_draft", "drafting"):
        return 0
    kind = state.get("season_type", "regular")
    if kind == "post":
        return WEEKS[-1]
    return max(0, min(WEEKS[-1], state["week"] - 1)) if kind == "regular" else 0


def load_season(season, state, players):
    league = load(RAW / season / "league.json")
    events = load(EVENTS / f"{season}.json") if (EVENTS / f"{season}.json").exists() else []
    nfl = trim_nfl(season, league["scoring_settings"], [e for e in events if e["type"] == "scoring_settings_change"])
    last_week = weeks_played(state, season, league["status"])
    slots = [p for p in league["roster_positions"] if p != "BN"]
    users = {u["user_id"]: u for u in load(RAW / season / "users.json")}
    rosters = load(RAW / season / "rosters.json")
    names = {}
    for roster in rosters:
        user = users.get(roster["owner_id"]) or {}
        names[roster["roster_id"]] = ((user.get("metadata") or {}).get("team_name") or f"Team {user.get('display_name', roster['roster_id'])}").strip()

    brackets = {}
    for kind, labels in (("winners", WINNERS_LABEL), ("toilet", TOILET_LABEL)):
        for m in load(RAW / season / f"{'winners' if kind == 'winners' else 'losers'}_bracket.json"):
            if isinstance(m.get("t1"), int) and isinstance(m.get("t2"), int):
                brackets[(14 + m["r"], frozenset((m["t1"], m["t2"])))] = (kind, labels[(m["r"], m.get("p"))], m.get("p"))

    sides, games = {}, []
    for week in range(1, last_week + 1):
        rows = load(RAW / season / "matchups" / f"{week:02d}.json")
        # The private weekly record, when we have it, is the official score and lineup.
        legs_path = RAW / season / "matchup_legs" / f"{week:02d}.json"
        legs = {leg["roster_id"]: leg for leg in load(legs_path)} if legs_path.exists() else {}
        as_played = nfl["weeks"].get(str(week), {}).get("points", {})
        for row in rows:
            leg = legs.get(row["roster_id"], {})
            official = leg.get("custom_points") if leg.get("custom_points") is not None else leg.get("points")
            players = leg.get("players") or row.get("players") or []
            starters = leg.get("starters") or row.get("starters") or []
            rescored = row.get("players_points") or {}
            points = {pid: as_played.get(pid, rescored.get(pid, 0.0)) for pid in set(players) | set(starters)}
            listed = starters
            starters, swaps = fired_subs(starters, players, leg.get("subs"), as_played, points, official)
            pending = [(a, b) for a, b in swaps if a in listed]   # swaps Sleeper has not applied yet (week in progress)
            sides[(week, row["roster_id"])] = {
                "players": players, "starters": starters, "subs": [{"out": a, "in": b} for a, b in swaps],
                "points": points,
                "total": official if official is not None else r2((row.get("points") or 0.0) + sum(points[b] - points[a] for a, b in pending)),
                "sleeper_max": leg.get("max_points"), "sleeper_proj": leg.get("proj_points"),
            }
        # A player traded mid-week can be listed on his new team while his points counted
        # for the old one (2023 week 12). He belongs to whoever started him.
        started = {pid: rid for rid in legs or [r["roster_id"] for r in rows] for pid in sides[(week, rid)]["starters"]}
        for row in rows:
            side = sides[(week, row["roster_id"])]
            side["players"] = [pid for pid in side["players"] if started.get(pid, row["roster_id"]) == row["roster_id"]]
            side["players"] += [pid for pid in side["starters"] if pid != "0" and pid not in side["players"]]
        by_matchup = defaultdict(list)
        for row in rows:
            if row.get("matchup_id"):
                by_matchup[row["matchup_id"]].append(row["roster_id"])
        for pair in by_matchup.values():
            if len(pair) != 2:
                continue
            kind, label, place = ("regular", "Regular Season", None)
            if week not in REGULAR_WEEKS:
                if (week, frozenset(pair)) not in brackets:
                    continue
                kind, label, place = brackets[(week, frozenset(pair))]
            games.append({"week": week, "teams": sorted(pair), "kind": kind, "label": label, "place": place})
    return {
        "season": season, "league": league, "slots": slots, "rosters": rosters, "names": names, "nfl": nfl,
        "last_week": last_week, "sides": sides, "games": games,
        "week_starts": {int(w): d["start"] for w, d in nfl["weeks"].items()},
        "events": events,
    }


def season_summary(season, s, rows):
    rows = list(rows.values())
    complete = s["league"]["status"] == "complete"
    by_finish = {r["finish"]: r["franchise"] for r in rows if r["finish"]} if complete else {}
    top = lambda key: max(rows, key=key)["franchise"] if s["last_week"] else None
    return {"season": season, "status": s["league"]["status"], "weeks_played": s["last_week"],
            "champion": by_finish.get(1), "runner_up": by_finish.get(2), "third": by_finish.get(3), "ultimate_loser": by_finish.get(12),
            "top_seed": top(lambda r: -r["seed"]), "most_points": top(lambda r: r["pf"]),
            "teams": {r["franchise"]: r["team"] for r in rows}}


# ---------------------------------------------------------------- trades

def nfl_ppg(seasons, pid, season, weeks, value=None, pos=None):
    """A player's NFL fantasy scoring under league rules over a span of weeks, whoever rostered him.
    With value (annotate_value's notes) and his position, also his WAR over those games: points above that season's
    replacement level for the position, turned into wins at the league's rate."""
    data = seasons.get(season)
    scores = [data["nfl"]["weeks"][str(w)]["points"][pid] for w in weeks
              if data and str(w) in data["nfl"]["weeks"] and pid in data["nfl"]["weeks"][str(w)]["points"]]
    out = {"g": len(scores), "ppg": r2(sum(scores) / len(scores)) if scores else None}
    baseline = (value or {}).get("replacement", {}).get(season, {}).get(pos)
    if scores and baseline is not None:
        out["war"] = r2(sum(x - baseline for x in scores) * value["wins_per_point"])
    return out


TRADE_EVEN_WAR = 0.25   # a trade is even until one side is this many wins ahead (about 21 points)
JUDGED = ("complete", "vetoed", "cancelled")   # trades given a value verdict; a vetoed or withdrawn one as if it had gone through


def nfl_war_since(seasons, pid, day, value, pos):
    """A player's NFL wins above replacement in every game from `day` on, weeks 1-17, whoever rostered him."""
    war, games = 0.0, 0
    for season, data in seasons.items():
        baseline = value["replacement"].get(season, {}).get(pos)
        if baseline is None:
            continue
        for week, entry in data["nfl"]["weeks"].items():
            if int(week) in WEEKS and entry["start"] >= day and pid in entry["points"]:
                war += entry["points"][pid] - baseline
                games += 1
    return {"g": games, "war": r2(war * value["wins_per_point"])}


def rosters_today(season, s):
    """{franchise: players held now}, from the current rosters, so a player moved since the last game week is on his new team."""
    path = RAW / season / "rosters.json"
    if path.exists():
        held = {r["roster_id"]: set(r.get("players") or []) for r in load(path)}
    else:
        held = {fid: set(s["sides"].get((s["last_week"], fid), {"players": []})["players"]) for fid in range(1, 13)}
    return {fid: held.get(fid, set()) for fid in range(1, 13)}


def build_trades(all_moves, adjustments, polls, seasons, people, name_on, pick_result, appearances, game_log, value, players):
    by_id = {m["transaction_id"]: m for m in all_moves}
    absorbed = {child for adj in adjustments.values() for child in adj.get("absorb", [])}

    def since(pid, fid, day):
        rows = [a for a in appearances.get(pid, []) if a["franchise"] == fid and (a["date"] or "") >= day]
        starts = [a for a in rows if a["started"]]
        return {"g": len(rows), "gs": len(starts), "pts": r2(sum(a["pts"] for a in starts)), "wins": sum(1 for a in starts if a["won"]),
                "war": r2(sum(a.get("war") or 0 for a in starts))}

    def describe(trade_id, season, day, status, received, processed=None):
        s = seasons[season]
        week = sum(1 for start in s["week_starts"].values() if start < day)
        if week == 0:
            spans = ((str(int(season) - 1), WEEKS, f"{int(season) - 1} season"), (season, WEEKS, f"{season} season"))
        else:
            spans = ((season, range(1, week + 1), f"{season} weeks 1-{week}"), (season, range(week + 1, 18), f"{season} weeks {week + 1}-17"))
        adj = adjustments.get(trade_id, {})
        sides = []
        for fid in sorted(received):
            got = received[fid]
            total = {"gs": 0, "pts": 0.0, "wins": 0, "war": 0.0}
            assets = []
            for item in got["players"]:
                row = {"type": "player", "player": item["player"], "from": item.get("from"),
                       "before": nfl_ppg(seasons, item["player"], spans[0][0], spans[0][1], value, (players.get(item["player"]) or {}).get("pos")),
                       "after": nfl_ppg(seasons, item["player"], spans[1][0], spans[1][1], value, (players.get(item["player"]) or {}).get("pos"))}
                if status == "complete":
                    row["team_since"] = since(item["player"], fid, day)
                if status in JUDGED:
                    row["value"] = nfl_war_since(seasons, item["player"], day, value, (players.get(item["player"]) or {}).get("pos"))
                assets.append(row)
            for pick in got["picks"]:
                became = pick_result.get((pick["season"], pick["round"], pick["original"]))
                row = {"type": "pick", "season": pick["season"], "round": pick["round"], "original": pick["original"], "from": pick.get("from"),
                       "moved_on": pick.get("moved_on"), "became": became}
                if status == "complete" and became and became["franchise"] == fid:
                    row["team_since"] = since(became["player"], fid, day)
                if became:   # what the player the pick became did after the trade, whoever drafted him
                    pos = (players.get(became["player"]) or {}).get("pos")
                    row["after"] = nfl_ppg(seasons, became["player"], spans[1][0], spans[1][1], value, pos)
                    if status in JUDGED:
                        row["value"] = nfl_war_since(seasons, became["player"], max(day, became["date"]), value, pos)
                assets.append(row)
            for row in assets:
                for key in total:
                    total[key] += row.get("team_since", {}).get(key, 0)
            split = None
            if 1 <= week < REGULAR_WEEKS[-1]:
                split = {}
                for label, weeks in (("before", range(1, week + 1)), ("after", range(week + 1, REGULAR_WEEKS[-1] + 1))):
                    mine = [(t, g) for g in game_log if g["season"] == season and g["kind"] == "regular" and g["week"] in weeks
                            for t in g["teams"] if t["franchise"] == fid]
                    split[label] = {"w": sum(1 for t, g in mine if g["winner"] == fid), "l": sum(1 for t, g in mine if g["winner"] not in (fid, None)),
                                    "ppg": r2(sum(t["points"] for t, g in mine) / len(mine)) if mine else None}
            sides.append({"franchise": fid, "team": name_on(season, fid, day), "managers": people.owners_on(fid, day),
                          "assets": assets, "total": {**total, "pts": r2(total["pts"]), "war": r2(total["war"])},
                          # An asset below replacement counts as nothing, not as a loss: the team could simply not start him.
                          "value": r2(sum(max(0.0, (a.get("value") or {}).get("war", 0.0)) for a in assets)), "team_split": split})
        best = sorted(sides, key=lambda x: -x["value"])
        return {"id": trade_id, "status": status, "season": season, "date": day, "processed": processed, "week": week,
                "spans": [spans[0][2], spans[1][2]], "votes": (polls.get(trade_id) or {}).get("votes"),
                "notes": adj.get("notes", []), "conditional_swap": adj.get("conditional_swap"),
                # The verdict: NFL WAR of what each side received, from the trade on. Later deals do not enter into it.
                # A vetoed or withdrawn trade is judged the same way, as if it had gone through.
                "leader": best[0]["franchise"] if status in JUDGED and len(best) > 1 and best[0]["value"] - best[1]["value"] >= TRADE_EVEN_WAR else None,
                "margin": r2(best[0]["value"] - best[1]["value"]) if status in JUDGED and len(best) > 1 else None,
                "sides": sides}

    out = []
    for move in all_moves:
        trade_id = move["transaction_id"]
        if move["type"] != "trade" or move["status"] != "complete" or trade_id in absorbed:
            continue
        adj = adjustments.get(trade_id, {})
        received = {fid: {"players": [], "picks": []} for fid in move["roster_ids"]}
        for source in [move] + [by_id[c] for c in adj.get("absorb", []) if c in by_id]:
            if source["type"] == "commissioner":   # the bookkeeping half of a deal already described by its picks
                continue
            for pid, to in (source.get("adds") or {}).items():
                received[to]["players"].append({"player": pid, "from": (source.get("drops") or {}).get(pid)})
            for pick in source.get("draft_picks") or []:
                received[pick["owner_id"]]["picks"].append({"season": pick["season"], "round": pick["round"], "original": pick["roster_id"], "from": pick["previous_owner_id"]})
        for pick in adj.get("add_picks", []):
            received[pick["to"]]["picks"].append({k: pick[k] for k in ("season", "round", "original", "from", "moved_on")})
        out.append(describe(trade_id, move["season"], move["day"], "complete", received, day_of(move["status_updated"])))

    # Vetoed and cancelled trades survive only in the league chat's review posts.
    for season, s in seasons.items():
        pending = {}
        for event in s["events"]:
            if event["type"] != "trade_status":
                continue
            item = event["data"][0]
            closed = "vetoed" in event["text"] or "cancelled" in event["text"]
            if "pending" in event["text"] and not closed:
                pending[item["transaction_id"]] = event
            elif closed:
                offer = pending.get(item["transaction_id"])
                if not offer:
                    continue
                received = {}
                for fid, part in offer["data"][0]["transactions_by_roster"].items():
                    received[int(fid)] = {
                        "players": [{"player": p["player_id"]} for p in part.get("adds") or []],
                        "picks": [{"season": p["season"], "round": p["round"], "original": p["roster_id"], "from": p.get("previous_owner_id")} for p in part.get("added_picks") or []]}
                status = "vetoed" if "vetoed" in event["text"] else "cancelled"
                out.append(describe(item["transaction_id"], season, day_of(datetime.fromisoformat(offer["created"]).timestamp() * 1000), status, received,
                                    day_of(datetime.fromisoformat(event["created"]).timestamp() * 1000)))
        # Still under review: posted for the league vote, with no result yet in the chat or in Sleeper's transactions.
        settled = {t["id"] for t in out}
        for trade_id, offer in pending.items():
            posted = datetime.fromisoformat(offer["created"])
            if trade_id in settled or datetime.now(timezone.utc) - posted > timedelta(days=REVIEW_DAYS + 2):
                continue
            received = {}
            for fid, part in offer["data"][0]["transactions_by_roster"].items():
                received[int(fid)] = {
                    "players": [{"player": p["player_id"]} for p in part.get("adds") or []],
                    "picks": [{"season": p["season"], "round": p["round"], "original": p["roster_id"], "from": p.get("previous_owner_id")} for p in part.get("added_picks") or []]}
            out.append(describe(trade_id, season, day_of(posted.timestamp() * 1000), "pending", received))
            out[-1]["review_ends"] = day_of((posted + timedelta(days=REVIEW_DAYS)).timestamp() * 1000)
    # Where each acquired asset went next, if the acquiring team later traded it on.
    done = sorted((t for t in out if t["status"] == "complete"), key=lambda t: (t["date"], t["id"]))
    key_of = lambda a: ("player", a["player"]) if a["type"] == "player" else ("pick", a["season"], a["round"], a["original"])
    for i, trade in enumerate(done):
        for side in trade["sides"]:
            for asset in side["assets"]:
                for later in done[i + 1:]:
                    hit = next((s2 for s2 in later["sides"] for a2 in s2["assets"]
                                if key_of(a2) == key_of(asset) and a2.get("from") == side["franchise"]), None)
                    if hit:
                        asset["later"] = {"id": later["id"], "date": later["date"], "to": hit["franchise"], "to_team": hit["team"]}
                        break
    # Trade trees: follow everything a side received through later trades, drafts and releases.
    latest = max(seasons)
    holding = rosters_today(latest, seasons[latest])
    releases = defaultdict(list)     # (player, franchise) -> days he was released outright
    for move in all_moves:
        if move["status"] == "complete" and move["type"] != "trade":
            for pid, frm in (move.get("drops") or {}).items():
                if pid not in (move.get("adds") or {}):
                    releases[(pid, frm)].append(move["day"])

    def stint(pid, fid, start, end):
        rows = [a for a in appearances.get(pid, []) if a["franchise"] == fid and a["started"] and (a["date"] or "") >= start and (end is None or a["date"] < end)]
        return {"gs": len(rows), "pts": r2(sum(a["pts"] for a in rows)), "wins": sum(1 for a in rows if a["won"]), "war": r2(sum(a.get("war") or 0 for a in rows))}

    def next_trade(key, fid, day, seen):
        for later in done:
            if later["id"] in seen or later["date"] < day:
                continue
            if any(key_of(a2) == key and a2.get("from") == fid for s2 in later["sides"] for a2 in s2["assets"]):
                return later
        return None

    by_trade = {t["id"]: t for t in done}

    def grow(asset, fid, day, seen, shown, depth=0):
        key = key_of(asset)
        node = {"type": asset["type"], "acquired": day}
        node.update({k: asset[k] for k in ("player", "season", "round", "original") if k in asset})
        later = next_trade(key, fid, day, seen) if depth < 8 else None
        if later:
            mine = next(s2 for s2 in later["sides"] if s2["franchise"] == fid)
            sent = sum(1 for s2 in later["sides"] for a2 in s2["assets"] if a2.get("from") == fid)
            node["outcome"] = {"type": "traded", "trade": later["id"], "date": later["date"], "with": sent - 1,
                               "to": [{"franchise": s2["franchise"], "team": s2["team"]} for s2 in later["sides"] if any(key_of(a2) == key and a2.get("from") == fid for a2 in s2["assets"])][0]}
            # Several assets can leave in one deal; list what came back only under the first of them.
            if later["id"] in shown:
                node["outcome"]["repeat"] = True
            else:
                shown.add(later["id"])
                node["children"] = [grow(a2, fid, later["date"], seen | {later["id"]}, shown, depth + 1) for a2 in mine["assets"]]
            if asset["type"] == "player":
                node["stint"] = stint(asset["player"], fid, day, later["date"])
            return node
        if asset["type"] == "pick":
            became = pick_result.get((asset["season"], asset["round"], asset["original"]))
            if not became:
                node["outcome"] = {"type": "pending"}
            elif became["franchise"] == fid:
                node["outcome"] = {"type": "drafted", "label": became["label"]}
                node["children"] = [grow({"type": "player", "player": became["player"]}, fid, day, seen, shown, depth + 1)]
            else:
                node["outcome"] = {"type": "used_elsewhere", "label": became["label"], "player": became["player"], "franchise": became["franchise"]}
            return node
        pid = asset["player"]
        cut = min((d for d in releases[(pid, fid)] if d >= day), default=None)
        node["stint"] = stint(pid, fid, day, cut)
        node["outcome"] = {"type": "released", "date": cut} if cut else {"type": "roster"} if pid in holding[fid] else {"type": "gone"}
        return node

    def total(nodes):
        gs = pts = 0
        for node in nodes:
            st = node.get("stint") or {}
            sub = total(node.get("children", []))
            gs += st.get("gs", 0) + sub[0]
            pts += st.get("pts", 0.0) + sub[1]
        return gs, pts

    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node.get("children", []))

    def mark_shared(nodes, sent_by, shared=False):
        """Flag everything that came back from a deal which also cost assets from outside this tree."""
        for node in nodes:
            node["shared"] = shared
            o = node["outcome"]
            below = shared
            if o["type"] == "traded":
                o["outside"] = sent_by[o["trade"]]["sent"] - sent_by[o["trade"]]["mine"]
                below = shared or o["outside"] > 0
            mark_shared(node.get("children", []), sent_by, below)

    def realized(asset, receiver, day):
        """What an asset went on to be worth to the team that got it: WAR as that team's starter until he left it, never below zero.
        A pick is worth what the player it became did for the team that drafted him."""
        if asset["type"] == "pick":
            became = pick_result.get((asset["season"], asset["round"], asset["original"]))
            if not became:
                return 0.0
            pid, receiver, day = became["player"], became["franchise"], became["date"]
        else:
            pid = asset["player"]
        later = next_trade(("player", pid), receiver, day, set())
        cut = min((d for d in releases[(pid, receiver)] if d >= day), default=None)
        end = min((d for d in (later and later["date"], cut) if d), default=None)
        return max(0.0, stint(pid, receiver, day, end)["war"])

    def credit(nodes, fid):
        """A side's credit from its whole tree. When a branch left in a later deal alongside assets from outside the tree,
        what came back is shared out by what each asset sent in that deal went on to be worth; evenly if none has done anything yet."""
        share = {}
        for node in walk(nodes):
            o = node["outcome"]
            if o["type"] == "traded" and o.get("outside") and o["trade"] not in share:
                later = by_trade[o["trade"]]
                mine = {key_of(n) for n in walk(nodes) if n["outcome"]["type"] == "traded" and n["outcome"]["trade"] == later["id"]}
                sent = [(key_of(a2), realized(a2, s2["franchise"], later["date"])) for s2 in later["sides"] for a2 in s2["assets"] if a2.get("from") == fid]
                worth = sum(v for _, v in sent)
                share[later["id"]] = (sum(v for k, v in sent if k in mine) / worth) if worth > 0 else len(mine) / len(sent)
        def add(nodes, frac):
            war = pts = 0.0
            for node in nodes:
                st = node.get("stint") or {}
                war, pts = war + frac * st.get("war", 0.0), pts + frac * st.get("pts", 0.0)
                o = node["outcome"]
                below = frac
                if o["type"] == "traded" and o["trade"] in share:
                    o["credit"] = round(share[o["trade"]], 3)
                    below = frac * share[o["trade"]]
                sub = add(node.get("children", []), below)
                war, pts = war + sub[0], pts + sub[1]
            return war, pts
        war, pts = add(nodes, 1.0)
        return {"war": r2(war), "pts": r2(pts)}

    def today(nodes):
        """Where the tree stands now: players still on the roster and picks still to be used."""
        roster, picks = [], []
        for node in walk(nodes):
            kind = node["outcome"]["type"]
            if kind == "roster":
                roster.append({"player": node["player"], "shared": node["shared"], **node["stint"]})
            elif kind == "pending":
                picks.append({k: node[k] for k in ("season", "round", "original", "shared")})
        return {"roster": sorted(roster, key=lambda r: -r["pts"]), "picks": sorted(picks, key=lambda p: (p["season"], p["round"]))}

    for trade in done:
        for side in trade["sides"]:
            shown = set()
            fid = side["franchise"]
            side["tree"] = [grow(asset, fid, trade["date"], {trade["id"]}, shown) for asset in side["assets"]]
            # For each later deal a branch left through: how many assets the team sent in all, and how many of them belong to this tree.
            sent_by = defaultdict(lambda: {"sent": 0, "mine": 0})
            for node in walk(side["tree"]):
                if node["outcome"]["type"] == "traded":
                    sent_by[node["outcome"]["trade"]]["mine"] += 1
            for later in done:
                if later["id"] in sent_by:
                    sent_by[later["id"]]["sent"] = sum(1 for s2 in later["sides"] for a2 in s2["assets"] if a2.get("from") == fid)
            mark_shared(side["tree"], sent_by)
            gs, pts = total(side["tree"])
            side["tree_total"] = {"gs": gs, "pts": r2(pts)}
            direct = [node for node in walk(side["tree"]) if not node["shared"]]
            side["tree_direct"] = {"gs": sum((n.get("stint") or {}).get("gs", 0) for n in direct), "pts": r2(sum((n.get("stint") or {}).get("pts", 0.0) for n in direct))}
            side["tree_today"] = today(side["tree"])
            side["credit"] = credit(side["tree"], fid)   # what the trade led to for this team; shown, not used for the verdict
    # How lopsided each judged trade is, by WAR margin, against the completed trades all-time and that season.
    # A vetoed or withdrawn trade gets the place it would have taken among them.
    finished = [t for t in out if t["status"] == "complete" and t["margin"] is not None]
    for t in (t for t in out if t["margin"] is not None):
        place = lambda pool: {"rank": 1 + sum(1 for o in pool if o["margin"] > t["margin"]), "of": len(pool)}
        t["lopsided"] = {"all": place(finished), "season": place([o for o in finished if o["season"] == t["season"]])}
    return sorted(out, key=lambda t: (t["date"], t["id"]), reverse=True)


# ---------------------------------------------------------------- players

def build_players(players, raw_players, seasons, appearances, all_moves, adjustments, drafted, trades, state, name_on, nicknames):
    # NFL-wide fantasy seasons under league scoring, with rank at the position.
    nfl = {}
    for season, s in seasons.items():
        totals = defaultdict(lambda: [0, 0.0])
        for week in WEEKS:
            for pid, pts in s["nfl"]["weeks"].get(str(week), {}).get("points", {}).items():
                totals[pid][0] += 1
                totals[pid][1] += pts
        by_pos = defaultdict(list)
        for pid, (games, pts) in totals.items():
            pos = players[pid]["pos"] if pid in players else (raw_players.get(pid) or {}).get("position")
            by_pos[pos].append((pts, pid))
        # Equal points share a rank: 1 plus the number of players at the position who scored more.
        ranks = {pid: 1 + sum(1 for other, _ in rows if other > pts) for rows in by_pos.values() for pts, pid in rows}
        nfl[season] = {pid: {"g": g, "pts": r2(pts), "rank": ranks[pid]} for pid, (g, pts) in totals.items()}
    nfl_teams = lambda season, pid: "/".join(seasons[season]["nfl"].get("teams", {}).get(pid, [])) or None

    absorbed = {child: parent for parent, adj in adjustments.items() for child in adj.get("absorb", [])}
    trade_ids = {t["id"] for t in trades}
    history = defaultdict(list)
    for pid, pick in drafted.items():
        history[pid].append({"date": pick["date"], "type": "draft", "to": pick["franchise"], "to_team": name_on(pick["season"], pick["franchise"], pick["date"]), "detail": f"{pick['season']} {pick['kind']} draft, pick {pick['label']} ({pick['overall']} overall)"})
    for move in all_moves:
        if move["status"] != "complete":
            continue
        parent = absorbed.get(move["transaction_id"], move["transaction_id"])
        if move["type"] == "commissioner" and move["transaction_id"] in absorbed:
            continue
        adds, drops = move.get("adds") or {}, move.get("drops") or {}
        for pid, to in adds.items():
            kind = {"free_agent": "signed", "waiver": "claimed", "trade": "traded", "commissioner": "reassigned"}[move["type"]]
            giver = drops.get(pid)
            history[pid].append({"date": move["day"], "type": kind, "to": to, "to_team": name_on(move["season"], to, move["day"]),
                                 "from": giver, "from_team": name_on(move["season"], giver, move["day"]) if giver else None,
                                 "trade": parent if parent in trade_ids else None})
        for pid, frm in drops.items():
            if pid not in adds:
                history[pid].append({"date": move["day"], "type": "released", "from": frm, "from_team": name_on(move["season"], frm, move["day"])})

    for pid, info in players.items():
        raw = raw_players.get(pid) or {}
        rows = appearances.get(pid, [])
        groups = defaultdict(list)
        for a in rows:
            groups[(a["season"], a["franchise"])].append(a)
        career = []
        for (season, fid), group in sorted(groups.items(), key=lambda kv: (kv[0][0], min(a["week"] for a in kv[1]))):
            starts = [a for a in group if a["started"]]
            career.append({"season": season, "franchise": fid, "team": group[-1]["team"], "nfl": nfl_teams(season, pid), "g": len(group), "gs": len(starts),
                           "pts": r2(sum(a["pts"] for a in starts)), "bench_pts": r2(sum(a["pts"] for a in group if not a["started"] and a["game"])),
                           "ppg": r2(sum(a["pts"] for a in starts) / len(starts)) if starts else None,
                           "best": max((a["pts"] for a in starts), default=None), "wins": sum(1 for a in starts if a["won"]),
                           **value_sum(starts)})
        starts = [a for a in rows if a["started"]]
        totals = {"g": len(rows), "gs": len(starts), "pts": r2(sum(a["pts"] for a in starts)),
                  "bench_pts": r2(sum(a["pts"] for a in rows if not a["started"] and a["game"])),
                  "ppg": r2(sum(a["pts"] for a in starts) / len(starts)) if starts else None,
                  "best": max((a["pts"] for a in starts), default=None), "wins": sum(1 for a in starts if a["won"]), **value_sum(starts)}
        info.update(paa=totals["paa"], vor=totals["vor"], war=totals["war"], g=totals["g"], gs=totals["gs"], pts=totals["pts"], bench_pts=totals["bench_pts"], best=totals["best"],
                    franchises=len({a["franchise"] for a in rows}), seasons=len({a["season"] for a in rows}))
        dump(SITE / "players" / f"{pid}.json", {
            "id": pid, **{k: info[k] for k in ("name", "pos", "nfl_team", "birth_date", "honors")}, **({"positions": info["positions"]} if info.get("positions") else {}),
            "college": raw.get("college"), "rookie_year": rookie_year_of(raw, state),
            "height": raw.get("height"), "weight": raw.get("weight"),
            "drafted": drafted.get(pid), "career": career, "totals": totals,
            "nfl": [{"season": season, "team": nfl_teams(season, pid), **nfl[season][pid]} for season in seasons if pid in nfl[season]],
            "history": sorted(history.get(pid, []), key=lambda e: e["date"]),
            "nicknames": [n for n in nicknames if n["player"] == pid],
            "log": rows,
        })


def build_nicknames(seasons, people, players, name_on):
    """Player nicknames from Sleeper's chat notices, oldest first. A blank nickname is a removal."""
    current, rows = {}, []
    events = sorted((e for s in seasons.values() for e in s["events"] if e["type"] == "player_nickname_update"), key=lambda e: e["created"])
    season_of = {e["message_id"]: season for season, s in seasons.items() for e in s["events"]}
    for event in events:
        slug = people.by_user.get(str(event["data"]["user"]["user_id"]))
        day = event["created"][:10]
        fid = next((f for f in people.stints if slug in people.owners_on(f, day)), None)
        for change in event["data"]["changes"]:
            pid = str(change["player"]["player_id"])
            nickname = (change.get("nickname") or "").strip() or None
            # Sleeper repeats a team's unchanged nicknames when several are saved together.
            if current.get((slug, pid)) == nickname:
                continue
            previous = current.get((slug, pid))
            current[(slug, pid)] = nickname
            raw = change["player"]
            rows.append({"date": day, "player": pid if pid in players else None,
                         "name": players[pid]["name"] if pid in players else f"{raw.get('first_name', '')} {raw.get('last_name', '')}".strip(),
                         "pos": players[pid]["pos"] if pid in players else raw.get("position"),
                         "nickname": nickname, "previous": previous, "manager": slug, "franchise": fid,
                         "team": name_on(season_of[event["message_id"]], fid, day) if fid else None})
    return rows


def build_split_draft(split, players, all_picks, all_moves, appearances, people, name_on, on_roster):
    """The 2024 draft in which Ritesh and Elis divided the pooled rosters and picks of franchises 3 and 10."""
    day = split["date"]
    by_name = {}
    for pid, p in players.items():
        by_name.setdefault(p["name"].lower(), pid)

    def find(name):
        key = name.lower().replace(" d/st", "")
        if key not in by_name and key.endswith((" jr.", " sr.")):
            key = key[:-4]
        if key in by_name:
            return by_name[key]
        close = [pid for full, pid in by_name.items() if full.startswith(key + " ")]
        if len(close) != 1:
            raise SystemExit(f"split draft: cannot place {name!r}")
        return close[0]

    rookie_picks = {p["label"]: p for p in all_picks if p["season"] == day[:4]}
    # A player the commissioner moved after the draft came from the other roster.
    moved = {pid for m in all_moves if m["type"] == "commissioner" and m["status"] == "complete" and day <= m["day"] <= "2024-06-30"
             for pid, to in (m.get("adds") or {}).items() if to in split["franchises"].values()}
    selections = []
    order = [split["first"]] + [slug for slug in split["franchises"] if slug != split["first"]]
    for row in split["rounds"]:
        # Snake order: the manager who opened the draft picks first in odd rounds.
        for turn, slug in enumerate(order if row["round"] % 2 else order[::-1]):
            fid = split["franchises"][slug]
            text = row[slug]
            other = next(f for f in split["franchises"].values() if f != fid)
            entry = {"round": row["round"], "overall": (row["round"] - 1) * len(order) + turn + 1, "manager": slug, "franchise": fid, "team": name_on(day[:4], fid, day)}
            if " pick " in text:
                label = text.split(" pick ")[1]
                pick = rookie_picks[label]
                pid = pick["player"] if pick["franchise"] == fid else None
                entry.update(kind="pick", label=label, text=f"{day[:4]} rookie pick {label}", original=pick["original"], became=pick["player"],
                             used_by=pick["franchise"], used_team=pick["team"])
            else:
                pid = find(text)
                entry.update(kind="player", player=pid, text=players[pid]["name"], original=other if pid in moved else fid)
            target = pid if entry["kind"] == "player" else entry["became"] if entry["used_by"] == fid else None
            entry["pos"] = players[entry.get("player") or entry["became"]]["pos"]
            starts = [a for a in appearances.get(target, []) if a["started"] and a["franchise"] == fid and a["date"] >= day] if target else []
            entry.update(gs=len(starts), pts=r2(sum(a["pts"] for a in starts)), war=round(sum(a.get("war") or 0 for a in starts), 2),
                         kept=bool(target and target in on_roster[fid]))
            selections.append(entry)
    totals = []
    for slug, fid in split["franchises"].items():
        mine = [e for e in selections if e["manager"] == slug]
        totals.append({"manager": slug, "franchise": fid, "team": name_on(day[:4], fid, day), "selections": len(mine),
                       "players": sum(1 for e in mine if e["kind"] == "player"), "picks": sum(1 for e in mine if e["kind"] == "pick"),
                       "switched": sum(1 for e in mine if e["original"] != fid),
                       "gs": sum(e["gs"] for e in mine), "pts": r2(sum(e["pts"] for e in mine)), "war": round(sum(e["war"] for e in mine), 2),
                       "kept": sum(1 for e in mine if e["kept"])})
    return {"date": day, "summary": split["summary"], "rounds": len(split["rounds"]), "selections": selections, "totals": totals}


# ---------------------------------------------------------------- rankings and lists

HIT_STARTS = 10   # a draft pick "hits" once he has started this many iooi games for anyone


def draft_analysis(all_picks, on_roster, latest):
    """Each pick against the average for its round in the same draft, plus hit rates."""
    groups = defaultdict(list)
    for p in all_picks:
        groups[(p["season"], p["round"])].append(p)
    rows = []
    for (season, rnd), group in groups.items():
        average = sum(p["pts_league"] for p in group) / len(group)
        for p in group:
            rows.append({**p, "round_avg": r2(average), "vs_avg": r2(p["pts_league"] - average),
                         "hit": p["starts_league"] >= HIT_STARTS, "retained": p["player"] in on_roster[p["franchise"]]})
    rookie = [r for r in rows if r["kind"] == "rookie"]
    startup = [r for r in rows if r["kind"] == "startup"]
    judged = [r for r in rookie if r["season"] != latest]   # the newest class has barely played

    def summary(items, key_of):
        table = defaultdict(lambda: {"picks": 0, "hits": 0, "pts": 0.0, "pts_team": 0.0, "vs_avg": 0.0, "retained": 0})
        for r in items:
            for key in key_of(r):
                t = table[key]
                t["picks"] += 1
                t["hits"] += r["hit"]
                t["pts"] += r["pts_league"]
                t["pts_team"] += r["pts_team"]
                t["vs_avg"] += r["vs_avg"]
                t["retained"] += r["retained"]
        return [{"key": k, **t, "pts": r2(t["pts"]), "pts_team": r2(t["pts_team"]), "vs_avg": r2(t["vs_avg"]),
                 "hit_rate": round(t["hits"] / t["picks"], 3), "pts_per_pick": r2(t["pts"] / t["picks"])} for k, t in table.items()]

    by_round = {}
    for rnd in sorted({r["round"] for r in rookie}):
        in_round = sorted((r for r in rookie if r["round"] == rnd), key=lambda r: -r["vs_avg"])
        by_round[rnd] = {"best": in_round[:10], "worst": in_round[::-1][:10]}
    ranked = sorted(startup, key=lambda r: -r["vs_avg"])
    return {
        "hit_starts": HIT_STARTS, "rookie_by_round": by_round,
        "rookie_top_points": sorted(rookie, key=lambda r: -r["pts_league"])[:25],
        "startup_steals": [r for r in ranked if r["round"] >= 6][:25], "startup_best": ranked[:25],
        "startup_busts": sorted((r for r in startup if r["round"] <= 5), key=lambda r: r["vs_avg"])[:25],
        "rounds": sorted(summary(judged, lambda r: [r["round"]]), key=lambda t: t["key"]),
        "managers": sorted(summary(judged, lambda r: r["managers"]), key=lambda t: -t["vs_avg"]),
        "franchises": sorted(summary(judged, lambda r: [r["franchise"]]), key=lambda t: -t["vs_avg"]),
        "startup_franchises": sorted(summary(startup, lambda r: [r["franchise"]]), key=lambda t: -t["pts"]),
        "classes": sorted(summary(rookie, lambda r: [r["season"]]), key=lambda t: t["key"]),
    }


def waiver_report(all_moves, appearances, people, name_on, absorbed_ids, players):
    """What every waiver claim and free-agent signing produced, and what released players did elsewhere."""
    shift = lambda day, days: (date.fromisoformat(day) + timedelta(days=days)).isoformat()
    exits = defaultdict(list)       # (player, franchise) -> (timestamp, day) of each time he left that franchise
    for move in all_moves:
        if move["status"] == "complete":
            for pid, frm in (move.get("drops") or {}).items():
                exits[(pid, frm)].append((move["created"], move["day"]))

    def window(pid, start, end, keep):
        """Weeks from a move onward. A week belongs to the move if its games (Thursday to Monday) were still to come."""
        return [a for a in appearances.get(pid, []) if a["date"] and shift(a["date"], 4) >= start and (end is None or a["date"] < end) and keep(a)]

    pickups, drops = [], []
    activity = defaultdict(lambda: {"claims": 0, "signings": 0, "failed_claims": 0, "releases": 0})
    counts = defaultdict(lambda: {"added": 0, "released": 0})
    for move in all_moves:
        if move["type"] not in ("waiver", "free_agent") or move["transaction_id"] in absorbed_ids:
            continue
        owners = [fid for fid in move["roster_ids"]]
        if move["status"] != "complete":
            if move["type"] == "waiver":
                for fid in owners:
                    for slug in people.owners_on(fid, move["day"]):
                        activity[("manager", slug)]["failed_claims"] += 1
                    activity[("franchise", fid)]["failed_claims"] += 1
            continue
        adds, released = move.get("adds") or {}, move.get("drops") or {}
        for pid, fid in adds.items():
            # The stint ends at his next exit, which can be later the same day.
            end = min(((at, day) for at, day in exits[(pid, fid)] if at > move["created"]), default=(None, None))[1]
            stint = window(pid, move["day"], end, lambda a: a["franchise"] == fid)
            starts = [a for a in stint if a["started"]]
            pickups.append({"date": move["day"], "season": move["season"], "type": "claim" if move["type"] == "waiver" else "signing",
                            "player": pid, "franchise": fid, "team": name_on(move["season"], fid, move["day"]), "managers": people.owners_on(fid, move["day"]),
                            "until": end, "g": len(stint), "gs": len(starts), "pts": r2(sum(a["pts"] for a in starts)),
                            "wins": sum(1 for a in starts if a["won"]), "best": max((a["pts"] for a in starts), default=None)})
            counts[pid]["added"] += 1
            key = "claims" if move["type"] == "waiver" else "signings"
            activity[("franchise", fid)][key] += 1
            for slug in people.owners_on(fid, move["day"]):
                activity[("manager", slug)][key] += 1
        for pid, fid in released.items():
            later = window(pid, move["day"], None, lambda a: a["franchise"] != fid and a["started"])
            drops.append({"date": move["day"], "season": move["season"], "player": pid, "franchise": fid,
                          "team": name_on(move["season"], fid, move["day"]), "managers": people.owners_on(fid, move["day"]),
                          "gs": len(later), "pts": r2(sum(a["pts"] for a in later)),
                          "next": later[0]["franchise"] if later else None, "next_team": later[0]["team"] if later else None})
            counts[pid]["released"] += 1
            activity[("franchise", fid)]["releases"] += 1
            for slug in people.owners_on(fid, move["day"]):
                activity[("manager", slug)]["releases"] += 1

    def totals(kind):
        out = []
        for (k, key), a in activity.items():
            if k != kind:
                continue
            mine = [p for p in pickups if (key in p["managers"] if kind == "manager" else p["franchise"] == key)]
            out.append({"key": key, **a, "adds": a["claims"] + a["signings"], "gs": sum(p["gs"] for p in mine), "pts": r2(sum(p["pts"] for p in mine)),
                        "pts_per_add": r2(sum(p["pts"] for p in mine) / len(mine)) if mine else None})
        return sorted(out, key=lambda r: -r["pts"])

    # Kickers and defenses are streamed week to week; they get their own pickup list and stay off the others.
    skill = lambda row: players.get(row["player"], {}).get("pos") not in ("K", "DEF")
    ranked = sorted(pickups, key=lambda p: -p["pts"])
    best = [p for p in ranked if skill(p)]
    seasons_seen = sorted({p["season"] for p in pickups})
    by_season = {}
    for season in seasons_seen:
        rows = []
        for fid in sorted({p["franchise"] for p in pickups} | {d["franchise"] for d in drops}):
            mine = [p for p in pickups if p["season"] == season and p["franchise"] == fid]
            rows.append({"franchise": fid, "claims": sum(1 for p in mine if p["type"] == "claim"), "signings": sum(1 for p in mine if p["type"] == "signing"), "adds": len(mine),
                         "releases": sum(1 for d in drops if d["season"] == season and d["franchise"] == fid),
                         "gs": sum(p["gs"] for p in mine), "pts": r2(sum(p["pts"] for p in mine)), "pts_per_add": r2(sum(p["pts"] for p in mine) / len(mine)) if mine else None})
        by_season[season] = sorted(rows, key=lambda r: -r["pts"])
    return {
        "log": {"pickups": pickups, "drops": drops},
        "by_season": by_season,
        "counts": {"pickups": len(pickups), "claims": sum(1 for p in pickups if p["type"] == "claim"), "releases": len(drops)},
        "best": best[:50], "best_kdef": [p for p in ranked if not skill(p)][:25], "best_by_season": {s: [p for p in best if p["season"] == s][:10] for s in seasons_seen},
        # If a team released the same player more than once, only the final release cost them what followed.
        "worst_drops": sorted((d for d in {(d["player"], d["franchise"]): d for d in drops}.values() if skill(d)), key=lambda d: -d["pts"])[:40],
        "managers": totals("manager"), "franchises": totals("franchise"),
        "most_added": sorted(({"player": pid, **c} for pid, c in counts.items() if skill({"player": pid})), key=lambda c: -c["added"])[:25],
    }


def trade_records(trades, people):
    done = [t for t in trades if t["status"] == "complete"]
    table = defaultdict(lambda: {"trades": 0, "w": 0, "l": 0, "even": 0, "war_in": 0.0, "war_out": 0.0, "pts_in": 0.0, "pts_out": 0.0, "vetoed": 0})
    partners = defaultdict(int)
    margins, player_counts = [], defaultdict(int)
    for t in trades:
        for side in t["sides"]:
            for key in [("franchise", side["franchise"])] + [("manager", m) for m in side["managers"]]:
                row = table[key]
                if t["status"] != "complete":
                    row["vetoed"] += t["status"] == "vetoed"
                    continue
                others = [o for o in t["sides"] if o is not side]
                row["trades"] += 1
                row["w" if t["leader"] == side["franchise"] else "l" if t["leader"] else "even"] += 1
                row["war_in"] += side["value"]
                row["war_out"] += sum(o["value"] for o in others)
                row["pts_in"] += side["total"]["pts"]
                row["pts_out"] += sum(o["total"]["pts"] for o in others)
        if t["status"] != "complete":
            continue
        ids = sorted(s["franchise"] for s in t["sides"])
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                partners[(a, b)] += 1
        for side in t["sides"]:
            for asset in side["assets"]:
                if asset["type"] == "player":
                    player_counts[asset["player"]] += 1
        if len(t["sides"]) == 2:
            a, b = sorted(t["sides"], key=lambda x: -x["value"])
            margins.append({"id": t["id"], "date": t["date"], "season": t["season"], "winner": a["franchise"], "winner_team": a["team"], "winner_war": a["value"],
                            "loser": b["franchise"], "loser_team": b["team"], "loser_war": b["value"], "margin": r2(a["value"] - b["value"])})

    def rows(kind):
        return sorted(({"key": key, **r, **{f: r2(r[f]) for f in ("war_in", "war_out", "pts_in", "pts_out")}, "net": r2(r["war_in"] - r["war_out"])}
                       for (k, key), r in table.items() if k == kind), key=lambda r: -r["net"])

    return {"managers": rows("manager"), "franchises": rows("franchise"),
            "partners": [{"a": a, "b": b, "trades": n} for (a, b), n in sorted(partners.items(), key=lambda kv: -kv[1])],
            "lopsided": sorted(margins, key=lambda m: -m["margin"])[:25],
            "lopsided_by_season": {season: sorted((m for m in margins if m["season"] == season), key=lambda m: -m["margin"])[:10]
                                   for season in sorted({m["season"] for m in margins}, reverse=True)},
            "vetoed": [{"id": t["id"], "date": t["date"], "season": t["season"], "status": t["status"], "votes": t["votes"], "leader": t["leader"], "margin": t["margin"], "lopsided": t.get("lopsided"),
                        "sides": [{"franchise": s["franchise"], "team": s["team"], "managers": s["managers"], "value": s["value"]} for s in sorted(t["sides"], key=lambda s: -s["value"])]}
                       for t in sorted(trades, key=lambda t: t["date"], reverse=True) if t["status"] in ("vetoed", "cancelled")],
            "most_traded": [{"player": pid, "trades": n} for pid, n in sorted(player_counts.items(), key=lambda kv: -kv[1]) if n >= 2][:30]}


def head_to_head(game_log):
    def grid(keys_of):
        cells = defaultdict(lambda: {"w": 0, "l": 0, "pf": 0.0, "pa": 0.0, "playoff_w": 0, "playoff_l": 0, "last": None})
        for g in game_log:
            a, b = g["teams"]
            for me, them in ((a, b), (b, a)):
                for mine in keys_of(me):
                    for theirs in keys_of(them):
                        if mine == theirs:
                            continue
                        c = cells[(mine, theirs)]
                        won = g["winner"] == me["franchise"]
                        c["w" if won else "l"] += 1
                        c["pf"] += me["points"]
                        c["pa"] += them["points"]
                        if g["kind"] == "winners":
                            c["playoff_w" if won else "playoff_l"] += 1
                        c["last"] = g["id"]
        return [{"a": a, "b": b, **c, "pf": r2(c["pf"]), "pa": r2(c["pa"])} for (a, b), c in cells.items()]
    return {"franchises": grid(lambda side: [side["franchise"]]), "managers": grid(lambda side: side["managers"])}


# ---------------------------------------------------------------- player value, leaders, ratings

REPLACEMENT_RANK = {"QB": 24, "RB": 30, "WR": 30, "TE": 12, "K": 12, "DEF": 12}   # starters league-wide at each position


def annotate_value(appearances, players, raw_players, seasons, season_rows):
    """Tag every start with points above average, value over replacement and wins added.

    Average: the mean iooi starter at that position that week.
    Replacement: per-game scoring of the first NFL player outside the league-wide starter pool that season.
    Wins: value over replacement times the league's wins-per-point rate (from team seasons).
    """
    by_week = defaultdict(list)
    for pid, rows in appearances.items():
        for a in rows:
            if a["started"]:
                by_week[(a["season"], a["week"], players[pid]["pos"])].append(a["pts"])
    average = {key: sum(v) / len(v) for key, v in by_week.items()}
    baseline = {}
    for season, s in seasons.items():
        totals = defaultdict(lambda: [0, 0.0])
        for week in WEEKS:
            for pid, pts in s["nfl"]["weeks"].get(str(week), {}).get("points", {}).items():
                totals[pid][0] += 1
                totals[pid][1] += pts
        by_pos = defaultdict(list)
        for pid, (games, pts) in totals.items():
            pos = players[pid]["pos"] if pid in players else (raw_players.get(pid) or {}).get("position")
            by_pos[pos].append((pts, games))
        for pos, rank in REPLACEMENT_RANK.items():
            ranked = sorted(by_pos.get(pos, []), reverse=True)
            if len(ranked) > rank and ranked[rank][1]:
                baseline[(season, pos)] = ranked[rank][0] / ranked[rank][1]
    done = [r for season, rows in season_rows.items() if seasons[season]["league"]["status"] == "complete" for r in rows.values()]
    mean_pf, mean_w = sum(r["pf"] for r in done) / len(done), sum(r["w"] for r in done) / len(done)
    rate = sum((r["pf"] - mean_pf) * (r["w"] - mean_w) for r in done) / sum((r["pf"] - mean_pf) ** 2 for r in done)
    for pid, rows in appearances.items():
        pos = players[pid]["pos"]
        for a in rows:
            if a["started"]:
                a["paa"] = round(a["pts"] - average[(a["season"], a["week"], pos)], 2)
                a["vor"] = round(a["pts"] - baseline.get((a["season"], pos), 0.0), 2)
                a["war"] = a["vor"] * rate
    return {"wins_per_point": round(rate, 5), "points_per_win": r2(1 / rate),
            "replacement": {season: {pos: r2(v) for (yr, pos), v in baseline.items() if yr == season} for season in seasons},
            "replacement_rank": REPLACEMENT_RANK}


def value_sum(starts):
    return {"paa": r2(sum(a["paa"] for a in starts)), "vor": r2(sum(a["vor"] for a in starts)), "war": r2(sum(a["war"] for a in starts))}


def year_span(rows):
    """"2023" for one season, "2023-2026" for several (first and last season on an iooi roster)."""
    first, last = min(a["season"] for a in rows), max(a["season"] for a in rows)
    return first if first == last else f"{first}-{last}"


def margin_list(game_log, widest, n=25):
    """The closest games or the biggest blowouts, each with the winner (or first team, in a tie) first."""
    rows = []
    for g in game_log:
        a, b = sorted(g["teams"], key=lambda t: -t["points"])
        rows.append({"game": g["id"], "season": g["season"], "week": g["week"], "kind": g["kind"], "margin": r2(a["points"] - b["points"]), "tie": g["winner"] is None,
                     **{f"{k}_{f}": t[f] for k, t in (("w", a), ("l", b)) for f in ("franchise", "team", "managers", "points")}})
    return sorted(rows, key=lambda r: (-r["margin"] if widest else r["margin"], r["season"], r["week"]))[:n]


def build_leaders(appearances, players):
    career, seasons_ = [], []
    for pid, rows in appearances.items():
        starts = [a for a in rows if a["started"]]
        if not starts:
            continue
        base = {"player": pid, "pos": players[pid]["pos"]}
        teams = lambda group: list(dict.fromkeys(a["franchise"] for a in group))   # in the order he played for them
        career.append({**base, "gs": len(starts), "pts": r2(sum(a["pts"] for a in starts)), **value_sum(starts), "franchises": teams(starts),
                       "years": year_span(rows), "best": max(a["pts"] for a in starts),
                       "bench_pts": r2(sum(a["pts"] for a in rows if not a["started"] and a["game"]))})
        by_season = defaultdict(list)
        for a in starts:
            by_season[a["season"]].append(a)
        for season, group in by_season.items():
            seasons_.append({**base, "season": season, "gs": len(group), "pts": r2(sum(a["pts"] for a in group)), **value_sum(group), "franchises": teams(group)})
    top = lambda rows, key, n=25: sorted(rows, key=lambda r: -r[key])[:n]
    return {
        "career": {key: top(career, key) for key in ("pts", "gs", "vor", "paa", "bench_pts")},
        "career_by_pos": {pos: top([r for r in career if r["pos"] == pos], "pts", 10) for pos in REPLACEMENT_RANK},
        "season": {key: top(seasons_, key) for key in ("pts", "vor", "paa")},
        "season_by_pos": {pos: top([r for r in seasons_ if r["pos"] == pos], "pts", 10) for pos in REPLACEMENT_RANK},
    }


def franchise_leaders(fid, appearances, players):
    rows = []
    for pid, apps in appearances.items():
        mine = [a for a in apps if a["franchise"] == fid]
        starts = [a for a in mine if a["started"]]
        if starts:
            rows.append({"player": pid, "pos": players[pid]["pos"], "years": year_span(mine), "g": len(mine), "gs": len(starts),
                         "pts": r2(sum(a["pts"] for a in starts)), "best": max(a["pts"] for a in starts), "wins": sum(1 for a in starts if a["won"]), **value_sum(starts)})
    rows.sort(key=lambda r: -r["pts"])
    return {"overall": rows[:20], "by_pos": {pos: [r for r in rows if r["pos"] == pos][:3] for pos in REPLACEMENT_RANK}}


REVIEW_DAYS = 2   # a trade is open to a league vote for two days before it is processed
ELO_START, ELO_K, ELO_CARRY = 1500, 24, 2 / 3


def build_elo(game_log, seasons):
    """Elo ratings from regular-season and playoff games. Toilet Bowl games are left out; losing them is the point."""
    rating = {fid: float(ELO_START) for fid in range(1, 13)}
    history = {fid: [] for fid in rating}
    upsets, last_season = [], None
    for game in sorted(game_log, key=lambda g: (g["season"], g["week"])):
        if game["kind"] == "toilet":
            continue
        if game["season"] != last_season:
            if last_season is not None:   # a new year pulls everyone a third of the way back to average
                for fid in rating:
                    rating[fid] = ELO_START + (rating[fid] - ELO_START) * ELO_CARRY
            last_season = game["season"]
        a, b = game["teams"]
        expect_a = 1 / (1 + 10 ** ((rating[b["franchise"]] - rating[a["franchise"]]) / 400))
        result_a = 1.0 if game["winner"] == a["franchise"] else 0.0 if game["winner"] == b["franchise"] else 0.5
        margin = abs(a["points"] - b["points"])
        swing = ELO_K * (1 + margin / 50) ** 0.5 * (result_a - expect_a)
        winner, loser, chance = (a, b, expect_a) if result_a == 1 else (b, a, 1 - expect_a)
        upsets.append({"game": game["id"], "season": game["season"], "week": game["week"], "kind": game["kind"],
                       "winner": winner["franchise"], "winner_team": winner["team"], "winner_pts": winner["points"], "winner_elo": round(rating[winner["franchise"]]),
                       "loser": loser["franchise"], "loser_team": loser["team"], "loser_pts": loser["points"], "loser_elo": round(rating[loser["franchise"]]),
                       "chance": round(chance, 3)})
        rating[a["franchise"]] += swing
        rating[b["franchise"]] -= swing
        for side in (a, b):
            history[side["franchise"]].append({"season": game["season"], "week": game["week"], "elo": round(rating[side["franchise"]]), "team": side["team"]})
    table = []
    for fid, points in history.items():
        peak, low = max(points, key=lambda p: p["elo"]), min(points, key=lambda p: p["elo"])
        ends = {}
        for p in points:
            ends[p["season"]] = p["elo"]
        this_year = [p for p in points if p["season"] == points[-1]["season"]]
        table.append({"franchise": fid, "elo": points[-1]["elo"], "peak": peak, "low": low, "season_end": ends,
                      "change": points[-1]["elo"] - (points[-len(this_year) - 1]["elo"] if len(points) > len(this_year) else ELO_START)})
    return {"settings": {"start": ELO_START, "k": ELO_K, "carry": round(ELO_CARRY, 3)},
            "table": sorted(table, key=lambda r: -r["elo"]), "history": history,
            "upsets": sorted(upsets, key=lambda u: u["chance"])[:25]}


def transaction_wire(season, all_moves, adjustments, people, name_on, trades):
    """Every completed move in a league year, newest first, in front-office language."""
    absorbed = {c for a in adjustments.values() for c in a.get("absorb", [])}
    trade_ids = {t["id"] for t in trades}
    out = []
    for move in all_moves:
        if move["season"] != season or move["status"] != "complete" or move["transaction_id"] in absorbed:
            continue
        adds, drops = move.get("adds") or {}, move.get("drops") or {}
        if move["type"] == "trade":
            out.append({"date": move["day"], "type": "trade", "franchises": move["roster_ids"],
                        "teams": {fid: name_on(season, fid, move["day"]) for fid in move["roster_ids"]},
                        "trade": move["transaction_id"] if move["transaction_id"] in trade_ids else None,
                        "players": [{"player": pid, "from": drops.get(pid), "to": to} for pid, to in adds.items()],
                        "picks": [{"season": p["season"], "round": p["round"], "to": p["owner_id"]} for p in move.get("draft_picks") or []]})
            continue
        for fid in move["roster_ids"]:
            out.append({"date": move["day"], "type": move["type"], "franchises": [fid], "teams": {fid: name_on(season, fid, move["day"])},
                        "added": [pid for pid, to in adds.items() if to == fid], "released": [pid for pid, frm in drops.items() if frm == fid and pid not in adds],
                        "swapped": [{"player": pid, "from": drops[pid], "to": to} for pid, to in adds.items() if pid in drops] if move["type"] == "commissioner" else []})
    return out[::-1]


# ---------------------------------------------------------------- awards, timeline, lottery

ALL_IOOI_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "K", "DEF"]
ALL_IOOI_MIN_STARTS = 8   # about half of the 17 playable weeks; while a season is in progress, half the weeks played so far
SCORING_LABELS = {
    "pass_int": "Interception thrown", "ff": "Forced fumble (player)", "def_st_ff": "Special teams forced fumble", "def_st_fum_rec": "Special teams fumble recovery",
    "st_fum_rec": "Special teams player fumble recovery", "pts_allow_0": "0 points allowed", "pts_allow_1_6": "1-6 points allowed", "pts_allow_7_13": "7-13 points allowed",
    "yds_allow_0_100": "Under 100 yards allowed", "yds_allow_100_199": "100-199 yards allowed", "yds_allow_200_299": "200-299 yards allowed",
    "yds_allow_350_399": "350-399 yards allowed", "yds_allow_400_449": "400-449 yards allowed", "yds_allow_450_499": "450-499 yards allowed",
    "yds_allow_500_549": "500-549 yards allowed", "yds_allow_550p": "550+ yards allowed", "fgm": "Field goal made", "fgm_0_19": "Field goal 0-19 yards",
    "fgm_20_29": "Field goal 20-29 yards", "fgm_30_39": "Field goal 30-39 yards", "fgm_40_49": "Field goal 40-49 yards", "fgm_50p": "Field goal 50+ yards",
    "fgm_50_59": "Field goal 50-59 yards", "fgm_60p": "Field goal 60+ yards", "fgm_yds_over_30": "Field goal yard beyond 30", "rush_att": "Carry",
    "def_4_and_stop": "Turnover on downs forced", "idp_tkl_solo": "Solo tackle", "idp_tkl_ast": "Assisted tackle", "idp_tkl_loss": "Tackle for loss",
    "idp_sack": "Sack (player)", "idp_int": "Interception (player)", "idp_int_ret_yd": "Interception return yard", "idp_ff": "Forced fumble (defender)",
    "idp_fum_rec": "Fumble recovery (player)", "idp_fum_ret_yd": "Fumble return yard", "idp_def_td": "Defensive touchdown (player)", "idp_safe": "Safety (player)",
    "idp_pass_def": "Pass defended", "idp_blk_kick": "Blocked kick (player)",
}


def build_awards(seasons, season_rows, game_log, players, raw_players, rookie_year):
    # Points above average: each start measured against the average starter at that position that week.
    by_week = defaultdict(list)
    for game in game_log:
        for side in game["teams"]:
            for line in side["starters"]:
                by_week[(game["season"], game["week"], players[line["player"]]["pos"])].append(line["pts"])
    average = {key: sum(v) / len(v) for key, v in by_week.items()}
    lines = defaultdict(lambda: {"gs": 0, "pts": 0.0, "paa": 0.0, "teams": {}})
    for game in game_log:
        for side in game["teams"]:
            for line in side["starters"]:
                pos = players[line["player"]]["pos"]
                row = lines[(game["season"], line["player"])]
                row["gs"] += 1
                row["pts"] += line["pts"]
                row["paa"] += line["pts"] - average[(game["season"], game["week"], pos)]
                row["teams"][side["franchise"]] = side["team"]
    official = {k: v for k, v in load(MANUAL / "young_sexy.json").items() if not k.startswith("_")}
    honors = defaultdict(list)
    out = []
    for season, s in seasons.items():
        if not s["last_week"]:
            continue
        complete = s["league"]["status"] == "complete"
        mine = sorted(({"player": pid, "pos": players[pid]["pos"], "gs": r["gs"], "pts": r2(r["pts"]), "paa": r2(r["paa"]),
                        "teams": [{"franchise": f, "team": t} for f, t in r["teams"].items()]}
                       for (yr, pid), r in lines.items() if yr == season), key=lambda r: -r["paa"])
        min_starts = ALL_IOOI_MIN_STARTS if complete else min(ALL_IOOI_MIN_STARTS, -(-s["last_week"] // 2))
        taken, teams = set(), []
        for label in ("First Team", "Second Team"):
            picks = []
            for slot in sorted(ALL_IOOI_SLOTS, key=lambda x: (x in FLEX, x == "SUPER_FLEX")):
                allowed = FLEX.get(slot, {slot})
                choice = next((r for r in mine if r["player"] not in taken and r["pos"] in allowed and r["gs"] >= min_starts), None)
                if choice:
                    taken.add(choice["player"])
                    picks.append({"slot": slot, **choice})
            teams.append({"label": label, "picks": sorted(picks, key=lambda p: ALL_IOOI_SLOTS.index(p["slot"]))})
        mvp = [r for r in mine if r["gs"] >= min_starts][:5]   # the same starts minimum as the All-iooi teams
        rookies = [r for r in mine if rookie_year.get(r["player"]) == int(season) and r["gs"] >= min_starts]   # by PAA, like the MVP
        # Young Sexy Awards: top-scoring NFL rookie at each position, weeks 1-17, whoever rostered him.
        totals = defaultdict(float)
        for week in WEEKS:
            for pid, pts in s["nfl"]["weeks"].get(str(week), {}).get("points", {}).items():
                if rookie_year.get(pid) == int(season):
                    totals[pid] += pts
        holders = {pid: rid for (week, rid), side in s["sides"].items() if week == s["last_week"] for pid in side["players"]}
        young = []
        for pos in ("QB", "RB", "WR", "TE", "K"):
            ranked = sorted(((pts, pid) for pid, pts in totals.items() if (raw_players.get(pid) or {}).get("position") == pos), reverse=True)
            if not ranked:
                continue
            pts, pid = ranked[0]
            name = players[pid]["name"] if pid in players else (raw_players[pid].get("full_name") or pid)
            listed = official.get(season, {}).get(pos)
            young.append({"pos": pos, "player": pid if pid in players else None, "name": name, "pts": r2(pts),
                          "franchise": holders.get(pid), "team": name_of_holder(s, holders.get(pid)),
                          "official": listed, "matches_official": listed is None or listed == name})
        rows = list(season_rows[season].values())
        by_finish = {r["finish"]: r for r in rows if r["finish"]} if complete else {}
        team = lambda r: {"franchise": r["franchise"], "team": r["team"]} if r else None
        entry = {
            "season": season, "complete": complete,
            "champion": team(by_finish.get(1)), "runner_up": team(by_finish.get(2)), "ultimate_loser": team(by_finish.get(12)),
            "top_seed": team(min(rows, key=lambda r: r["seed"])), "scoring_title": {**team(max(rows, key=lambda r: r["pf"])), "value": max(r["pf"] for r in rows)},
            "efficiency": {**team(max(rows, key=lambda r: r["efficiency"] or 0)), "value": max(r["efficiency"] or 0 for r in rows)},
            "luckiest": {**team(max(rows, key=lambda r: r["luck"])), "value": max(r["luck"] for r in rows)},
            "unluckiest": {**team(min(rows, key=lambda r: r["luck"])), "value": min(r["luck"] for r in rows)},
            "mvp": mvp, "rookie": rookies[:3], "all_iooi": teams, "all_iooi_min_starts": min_starts, "young_sexy": young,
        }
        # Championship MVP: the top scorer in the winner's starting lineup in the title game, team defenses excepted.
        title = next((g for g in game_log if g["season"] == season and g["label"] == "Dynasty Championship"), None) if complete else None
        side = next(t for t in title["teams"] if t["franchise"] == title["winner"]) if title else None
        best = max((x for x in side["starters"] if players[x["player"]]["pos"] != "DEF"), key=lambda x: x["pts"], default=None) if side else None
        entry["championship_mvp"] = best and {"player": best["player"], "pos": players[best["player"]]["pos"], "pts": r2(best["pts"]),
                                              "franchise": side["franchise"], "team": side["team"], "game": title["id"]}
        out.append(entry)
        if complete:
            honors[mvp[0]["player"]].append(f"{season} iooi MVP")
            if best:
                honors[best["player"]].append(f"{season} Championship MVP")
            if entry["rookie"]:
                honors[entry["rookie"][0]["player"]].append(f"{season} Rookie of the Year")
            for squad in teams:
                for pick in squad["picks"]:
                    honors[pick["player"]].append(f"{season} All-iooi {squad['label']}")
            for y in young:
                if y["player"]:
                    honors[y["player"]].append(f"{season} Young Sexy {y['pos']}")
            if title:
                for line in side["starters"] + side["bench"]:
                    honors[line["player"]].append(f"{season} champion ({side['team']})")
    return out[::-1], honors


def name_of_holder(s, roster_id):
    return s["names"].get(roster_id) if roster_id else None


def build_timeline(seasons, people, franchises, name_history, draft_index, trades, constitution, game_log, players, name_on):
    events = []
    add = lambda day, kind, text: events.append({"date": day, "kind": kind, "text": text})
    def team(fid, day):
        """Link to a franchise under the name it carried that day."""
        season = day[:4] if day[:4] in seasons else max(seasons)
        return f"[[f:{fid}|{name_on(season, fid, day)}]]"

    founding = min(st["from"] for sts in people.stints.values() for st in sts)
    founders = sorted(((st["from"], fid, st["manager"]) for fid, sts in people.stints.items() for st in sts if st["from"] <= "2023-08-15"))
    add(founding, "league", "Dynasty iooi is founded. " + "; ".join(f"[[m:{m}]] takes {team(fid, seasons['2023']['week_starts'][1])}" for _, fid, m in founders) + ".")
    for fid, sts in people.stints.items():
        for st in sts:
            if st["from"] > "2023-08-15":
                add(st["from"], "ownership", f"[[m:{st['manager']}]] acquires a stake in {team(fid, st['from'])}.")
            if st["to"]:
                add(st["to"], "ownership", f"[[m:{st['manager']}]] gives up his stake in {team(fid, st['to'])}.")
    split = load(MANUAL / "split_draft_2024.json")
    add(split["date"], "league", f"The split draft: [[m:ritesh]] and [[m:elis]], co-managers of {team(10, split['date'])} in 2023, divide the pooled rosters and 2024 picks of {team(3, split['date'])} and {team(10, split['date'])} in a 24-round draft ([[x:split_draft.html|results]]).")
    for d in draft_index:
        who = players[d["first_pick"]]["name"]
        add(d["date"], "draft", f"{d['season']} {d['kind']} draft ([[d:{d['season']}]]). First pick: [[p:{d['first_pick']}|{who}]], by {team(d['first_pick_franchise'], d['date'])}.")
    for game in game_log:
        if game["label"] in ("Dynasty Championship", "Toilet Bowl Final"):
            a, b = sorted(game["teams"], key=lambda t: -t["points"])
            day = (date.fromisoformat(game["date"]) + timedelta(days=4)).isoformat()
            if game["label"] == "Dynasty Championship":
                add(day, "title", f"[[f:{a['franchise']}|{a['team']}]] win the {game['season']} Dynasty Championship, beating [[f:{b['franchise']}|{b['team']}]] {a['points']:.2f} to {b['points']:.2f} ([[g:{game['id']}]]).")
            else:
                add(day, "toilet", f"[[f:{b['franchise']}|{b['team']}]] lose the {game['season']} Toilet Bowl final and are named Ultimate Loser ([[g:{game['id']}]]).")
    for bill in constitution["bills"]:
        names = "; ".join(f"{a['number']}. {a['name']} ({a['summary']})" for a in bill["amendments"])
        add(bill["ratified"], "rules", f"{bill['title']} is ratified ([[c:amendment-{bill['amendments'][0]['number']}]]). Amendments: {names}.")
    for season, s in seasons.items():
        first_week = s["week_starts"].get(1)
        for event in s["events"]:
            if event["type"] == "scoring_settings_change":
                changes = [f"{SCORING_LABELS.get(k, k)} {old or 0:g} to {new or 0:g}" for k, (old, new) in sorted(event["data"].items()) if (old or 0) != (new or 0)]
                if changes:
                    when = "" if not first_week or event["created"][:10] < first_week else " (mid-season)"
                    add(event["created"][:10], "scoring", f"Scoring settings changed{when}: " + "; ".join(changes) + ".")
    for t in trades:
        if t["status"] == "vetoed":
            a, b = t["sides"][0], t["sides"][1]
            votes = t["votes"] or {}
            add(t["processed"], "veto", f"A trade between [[f:{a['franchise']}|{a['team']}]] and [[f:{b['franchise']}|{b['team']}]] is vetoed, {votes.get('veto', 0)} votes to {votes.get('pass', 0)} ([[t:{t['id']}]]).")
    renames = []
    for fid, history in name_history.items():
        previous = None
        for item in history:
            renames.append({"date": item["date"], "franchise": fid, "from": previous, "to": item["name"], "by": item["set_by"]})
            previous = item["name"]
    return {"events": sorted(events, key=lambda e: e["date"]), "renames": sorted(renames, key=lambda r: r["date"], reverse=True)}


LOTTERY_ODDS = [30.0, 25.0, 20.0, 15.0, 7.0, 3.0]   # worst team first (2nd Amendment)


def build_lottery(seasons, season_rows, name_on):
    out = []
    for season in seasons:
        prev = str(int(season) - 1)
        if prev not in seasons:
            continue
        meta = next((d for d in load(RAW / season / "drafts.json") if d.get("status") == "complete"), None)   # a draft not yet held has no order to report
        if not meta:
            continue
        draft = load(RAW / season / "drafts" / f"{meta['draft_id']}.json")
        day = day_of(draft["start_time"])
        slots = {int(k): v for k, v in draft["slot_to_roster_id"].items()}
        rows = season_rows[prev]
        bottom = [r for r in rows.values() if r["finish"] > 6]
        lottery = int(season) >= 2026
        if lottery:   # from 2027 the odds follow Max PF (5th Amendment); 2026 used regular-season standing
            order = sorted(bottom, key=(lambda r: r["max_pf"]) if int(season) >= 2027 else (lambda r: -r["seed"]))
        elif season == "2024":   # the first rookie draft went in reverse order of final finish, Ultimate Loser first
            order = sorted(bottom, key=lambda r: -r["finish"])
        else:         # Article VI: fewest Max PF picks first
            order = sorted(bottom, key=lambda r: r["max_pf"])
        expected = {r["franchise"]: i + 1 for i, r in enumerate(order)}
        picks = []
        for slot in range(1, 13):
            fid = slots[slot]
            r = rows[fid]
            row = {"pick": slot, "franchise": fid, "team": name_on(season, fid, day), "prev_team": r["team"], "record": f"{r['w']}-{r['l']}",
                   "seed": r["seed"], "finish": r["finish"], "result": r["result"], "max_pf": r["max_pf"]}
            if fid in expected:
                row["rank"] = expected[fid]
                row["odds"] = LOTTERY_ODDS[expected[fid] - 1] if lottery else None
                row["moved"] = expected[fid] - slot
            picks.append(row)
        method = ("Weighted lottery among the six non-playoff teams, odds by regular-season standing (2nd Amendment). Picks 7-12 by playoff finish (3rd Amendment)."
                  if season == "2026" else
                  "Weighted lottery among the six non-playoff teams, odds by Max Points For (5th Amendment). Picks 7-12 by playoff finish." if lottery else
                  "No lottery. Non-playoff teams picked in reverse order of final finish, the Ultimate Loser first; playoff teams followed, champion last." if season == "2024" else
                  "No lottery. Non-playoff teams picked in order of fewest Max Points For; playoff teams in reverse order of regular-season standing, champion last (Article VI).")
        out.append({"season": season, "date": day, "lottery": lottery, "method": method, "picks": picks})
    return out[::-1]


# ---------------------------------------------------------------- build

def main():
    state = load(RAW / "nfl_state.json")
    raw_players = load(RAW / "players_nfl.json")
    people = People()
    season_ids = sorted(p.name for p in RAW.iterdir() if p.is_dir())

    rostered = set()
    for season in season_ids:
        for path in (RAW / season / "matchups").glob("*.json"):
            for row in load(path):
                rostered.update(row.get("players") or [])
    for path in RAW.glob("*/drafts/*_picks.json"):
        rostered.update(p["player_id"] for p in load(path) if p.get("player_id"))
    # Anyone on a roster today, so a player picked up since the last game week has a page and a team.
    if (RAW / season_ids[-1] / "rosters.json").exists():
        rostered.update(pid for r in load(RAW / season_ids[-1] / "rosters.json") for pid in (r.get("players") or []))
    players, positions = {}, {}
    for pid in sorted(rostered):
        p = raw_players.get(pid) or {}
        name = p.get("full_name") or " ".join(filter(None, [p.get("first_name"), p.get("last_name")])) or pid
        pos = p.get("fantasy_positions") or ([p["position"]] if p.get("position") else [])
        # Show the position he fills in this league (Travis Hunter is listed by the NFL as a DB).
        fantasy_pos = next((x for x in pos if x in ("QB", "RB", "WR", "TE", "K", "DEF")), p.get("position"))
        players[pid] = {"name": name, "pos": fantasy_pos, "nfl_team": p.get("team"), "birth_date": p.get("birth_date")}
        if len(pos) > 1:   # every position Sleeper lists, for display; "pos" stays the one the stats use
            players[pid]["positions"] = list(pos)
        positions[pid] = set(pos)

    seasons = {s: load_season(s, state, players) for s in season_ids}

    # ---- franchise name history from Sleeper's rename messages
    name_history = defaultdict(list)
    for season, s in seasons.items():
        primary = {r["owner_id"]: r["roster_id"] for r in s["rosters"]}
        for event in s["events"]:
            if event["type"] == "team_name_update":
                user_id = str(event["data"]["user"]["user_id"])
                name = (event["data"].get("team_name") or "").strip()
                if user_id in primary and name:
                    name_history[primary[user_id]].append({"date": event["created"][:10], "name": name, "set_by": people.by_user.get(user_id)})
    for history in name_history.values():
        history.sort(key=lambda n: n["date"])

    def name_on(season, fid, day):
        """The team's name on a given day. Sleeper's per-season name can reflect a later rename."""
        earlier = [n["name"] for n in name_history[fid] if day and n["date"] <= day]
        return earlier[-1] if earlier else seasons[season]["names"][fid]


    # ---- transactions: completed trades, waiver claims and free-agent moves
    all_moves = sorted(({**move, "season": season, "day": day_of(move["created"])}
                        for season in season_ids for path in (RAW / season / "transactions").glob("*.json") for move in load(path)),
                       key=lambda m: m["created"])
    franchise_moves = defaultdict(lambda: {"transactions": 0, "trades": 0})
    manager_moves = defaultdict(lambda: {"transactions": 0, "trades": 0})
    # A correction the commissioner filed as its own trade belongs to the trade it corrected, and is not counted twice.
    folded = {child for adj in load(MANUAL / "trade_adjustments.json")["trades"].values() for child in adj.get("absorb", [])}
    for move in all_moves:
        if move["status"] != "complete" or move["type"] not in ("trade", "waiver", "free_agent") or move["transaction_id"] in folded:
            continue
        for roster_id in move["roster_ids"]:
            targets = [franchise_moves[(move["season"], roster_id)]] + [manager_moves[(slug, move["season"], roster_id)] for slug in people.owners_on(roster_id, move["day"])]
            for target in targets:
                target["transactions"] += 1
                target["trades"] += move["type"] == "trade"

    # ---- games and box scores
    season_rows = defaultdict(dict)        # season -> franchise -> stats row
    game_log = []
    for season, s in seasons.items():
        box = {}
        weekly_scores = defaultdict(dict)
        for (week, roster_id), side in s["sides"].items():
            if week in REGULAR_WEEKS:
                weekly_scores[week][roster_id] = side["total"]
        for roster_id in s["names"]:
            season_rows[season][roster_id] = {
                "season": season, "franchise": roster_id, "team": name_on(season, roster_id, s["week_starts"].get(s["last_week"])),
                "w": 0, "l": 0, "t": 0, "pf": 0.0, "pa": 0.0, "max_pf": 0.0,
                "all_play_w": 0, "all_play_l": 0, "expected_w": 0.0,
                "playoff_w": 0, "playoff_l": 0, "toilet_w": 0, "toilet_l": 0, "finish": None,
                **franchise_moves[(season, roster_id)],
            }
        for week, scores in weekly_scores.items():
            for roster_id, pts in scores.items():
                beaten = sum(1 for other, o in scores.items() if other != roster_id and pts > o)
                lost = sum(1 for other, o in scores.items() if other != roster_id and pts < o)
                row = season_rows[season][roster_id]
                row["all_play_w"] += beaten
                row["all_play_l"] += lost
                row["expected_w"] += (beaten + (len(scores) - 1 - beaten - lost) / 2) / (len(scores) - 1)

        for game in s["games"]:
            week, game_id = game["week"], f"{season}-{game['week']:02d}-{game['teams'][0]}-{game['teams'][1]}"
            day = s["week_starts"].get(week)
            teams = []
            for roster_id in game["teams"]:
                side = s["sides"][(week, roster_id)]
                proj = s["nfl"]["weeks"].get(str(week), {}).get("proj", {})
                subbed = {x["in"]: x["out"] for x in side.get("subs", [])}   # auto-subs: the bench player who came in, and for whom
                starters = [{"slot": slot, "player": pid, "pts": side["points"].get(pid, 0.0), "proj": proj.get(pid), **({"sub_for": subbed[pid]} if pid in subbed else {})}
                            for slot, pid in zip(s["slots"], side["starters"]) if pid != "0"]
                rest = [pid for pid in side["players"] if pid not in side["starters"]]
                bench = [{"player": pid, "pts": side["points"].get(pid, 0.0), "proj": proj.get(pid)} for pid in rest]
                teams.append({
                    "franchise": roster_id, "team": name_on(season, roster_id, day), "managers": people.owners_on(roster_id, day),
                    "points": r2(side["total"]), "proj": r2(sum(x["proj"] or 0 for x in starters)),
                    # Sleeper's own Max PF; it counts every rostered player, taxi squad included.
                    "max_points": r2(side["sleeper_max"]) if side["sleeper_max"] is not None else best_lineup({pid: side["points"].get(pid, 0.0) for pid in side["players"]}, positions, s["slots"]),
                    "starters": starters,
                    "bench": sorted(bench, key=lambda x: -x["pts"]),
                })
            a, b = teams
            winner = a["franchise"] if a["points"] > b["points"] else b["franchise"] if b["points"] > a["points"] else None
            box[game_id] = {"id": game_id, "season": season, "week": week, "date": day, "kind": game["kind"],
                            "label": game["label"], "winner": winner, "teams": teams}
            game_log.append(box[game_id])
            for me, them in ((a, b), (b, a)):
                row = season_rows[season][me["franchise"]]
                won = winner == me["franchise"]
                if game["kind"] == "regular":
                    row["w" if won else "t" if winner is None else "l"] += 1
                    row["pf"] += me["points"]
                    row["pa"] += them["points"]
                    row["max_pf"] += me["max_points"]
                elif game["kind"] == "winners":
                    row["playoff_w" if won else "playoff_l"] += 1
                    if game["place"]:
                        row["finish"] = game["place"] + (0 if won else 1)
                else:
                    row["toilet_w" if won else "toilet_l"] += 1
                    if game["place"]:   # in the Toilet Bowl the loser advances; winning the placement game is the better finish
                        row["finish"] = (12 - game["place"]) if won else (13 - game["place"])
        dump(SITE / "games" / f"{season}.json", box)

        # seeds: record, then points. From 2026 the sixth seed is the top scorer among the rest (4th Amendment).
        rows = sorted(season_rows[season].values(), key=lambda r: (-(r["w"] + r["t"] / 2), -r["pf"]))
        if int(season) >= 2026:
            sixth = max(rows[5:], key=lambda r: r["pf"])
            rows = rows[:5] + [sixth] + [r for r in rows[5:] if r is not sixth]
        for seed, row in enumerate(rows, 1):
            games = row["w"] + row["l"] + row["t"]
            row.update(seed=seed, pf=r2(row["pf"]), pa=r2(row["pa"]), max_pf=r2(row["max_pf"]),
                       efficiency=round(row["pf"] / row["max_pf"], 4) if row["max_pf"] else None,
                       expected_w=r2(row["expected_w"]), luck=r2(row["w"] + row["t"] / 2 - row["expected_w"]),
                       ppg=r2(row["pf"] / games) if games else None,
                       pct=round((row["w"] + row["t"] / 2) / games, 3) if games else None,
                       result=FINISH_LABEL.get(row["finish"]) or (f"{row['finish']}th" if row["finish"] and row["finish"] > 3 else "3rd" if row["finish"] == 3 else None))
        complete = s["league"]["status"] == "complete"
        by_finish = {r["finish"]: r["franchise"] for r in rows if r["finish"]}
        swap = {}
        for a in s["names"]:
            swap[a] = {}
            for b in s["names"]:
                wins = losses = 0
                for week in REGULAR_WEEKS:
                    opponent = next((x for g in s["games"] if g["week"] == week and b in g["teams"] for x in g["teams"] if x != b), None)
                    if opponent is None or (week, a) not in s["sides"]:
                        continue
                    foe = b if opponent == a else opponent      # with B's schedule, A meets B when B was due to play A
                    mine, theirs = s["sides"][(week, a)]["total"], s["sides"][(week, foe)]["total"]
                    wins += mine > theirs
                    losses += mine < theirs
                swap[a][b] = [wins, losses]
        dump(SITE / "seasons" / f"{season}.json", {
            "schedule_swap": swap,
            "season": season, "status": s["league"]["status"], "weeks_played": s["last_week"],
            "champion": by_finish.get(1) if complete else None, "runner_up": by_finish.get(2) if complete else None,
            "ultimate_loser": by_finish.get(12) if complete else None,
            "standings": rows,
            "games": [{k: g[k] for k in ("id", "week", "date", "kind", "label", "winner")}
                      | {"teams": [{"franchise": t["franchise"], "points": t["points"]} for t in g["teams"]]}
                      for g in box.values()],
        })

    # ---- franchises
    latest = season_ids[-1]
    franchises = {}
    for fid in range(1, 13):
        rows = [season_rows[s][fid] for s in season_ids]
        done = [r for r in rows if seasons[r["season"]]["league"]["status"] == "complete"]
        franchises[fid] = {
            "id": fid, "name": name_on(latest, fid, date.today().isoformat()),
            "owners": people.owners_on(fid, date.today().isoformat()),
            "ownership": [{**st, "name": people.managers[st["manager"]]["name"]} for st in people.stints[fid]],
            "names": name_history[fid],
            "seasons": rows,
            "totals": {
                "w": sum(r["w"] for r in rows), "l": sum(r["l"] for r in rows), "t": sum(r["t"] for r in rows),
                "pct": pct(sum(r["w"] for r in rows), sum(r["l"] for r in rows), sum(r["t"] for r in rows)),
                "pf": r2(sum(r["pf"] for r in rows)), "pa": r2(sum(r["pa"] for r in rows)),
                "transactions": sum(r["transactions"] for r in rows), "trades": sum(r["trades"] for r in rows),
                "toilet_w": sum(r["toilet_w"] for r in rows), "toilet_l": sum(r["toilet_l"] for r in rows),
                "playoff_w": sum(r["playoff_w"] for r in rows), "playoff_l": sum(r["playoff_l"] for r in rows),
                "playoff_appearances": sum(1 for r in done if r["finish"] and r["finish"] <= 6),
                "championships": [r["season"] for r in done if r["finish"] == 1],
                "runner_up": [r["season"] for r in done if r["finish"] == 2],
                "ultimate_loser": [r["season"] for r in done if r["finish"] == 12],
            },
        }
        dump(SITE / "franchises" / f"{fid}.json", franchises[fid])

    # ---- managers: credited game by game, for whoever held a stake that week
    manager_rows = defaultdict(lambda: defaultdict(lambda: {"w": 0, "l": 0, "t": 0, "pf": 0.0, "pa": 0.0, "playoff_w": 0, "playoff_l": 0}))
    for game in game_log:
        a, b = game["teams"]
        for me, them in ((a, b), (b, a)):
            for slug in me["managers"]:
                row = manager_rows[slug][(game["season"], me["franchise"])]
                won = game["winner"] == me["franchise"]
                if game["kind"] == "regular":
                    row["w" if won else "t" if game["winner"] is None else "l"] += 1
                    row["pf"] += me["points"]
                    row["pa"] += them["points"]
                elif game["kind"] == "winners":
                    row["playoff_w" if won else "playoff_l"] += 1
    all_managers = []
    for slug, info in people.managers.items():
        rows = []
        for (season, fid), row in sorted(manager_rows[slug].items()):
            full = season_rows[season][fid]
            games = row["w"] + row["l"] + row["t"]
            whole_season = games == full["w"] + full["l"] + full["t"]
            rows.append({"season": season, "franchise": fid, "team": full["team"], **row, "pf": r2(row["pf"]), "pa": r2(row["pa"]),
                         "pct": pct(row["w"], row["l"], row["t"]), **manager_moves[(slug, season, fid)],
                         "partial_season": not whole_season,
                         "result": full["result"] if people.owners_on(fid, seasons[season]["week_starts"].get(seasons[season]["last_week"], "")) .count(slug) else None})
        totals = {k: r2(sum(r[k] for r in rows)) if k in ("pf", "pa") else sum(r[k] for r in rows) for k in ("w", "l", "t", "pf", "pa", "playoff_w", "playoff_l")}
        totals["pct"] = pct(totals["w"], totals["l"], totals["t"])
        # Offseason moves count too, even in a year the manager played no games for that team.
        for key in ("transactions", "trades"):
            totals[key] = sum(v[key] for (who, _s, _f), v in manager_moves.items() if who == slug)
        manager = {
            "id": slug, "name": info["name"],
            "stints": [{"franchise": fid, **st} for fid, sts in people.stints.items() for st in sts if st["manager"] == slug],
            "seasons": rows, "totals": totals,
            "championships": [r["season"] for r in rows if r["result"] == "Champion"],
            "runner_up": [r["season"] for r in rows if r["result"] == "Runner-up"],
            "ultimate_loser": [r["season"] for r in rows if r["result"] == "Ultimate Loser"],
        }
        all_managers.append(manager)
        dump(SITE / "managers" / f"{slug}.json", manager)

    # ---- all-time tables and the record book
    dump(SITE / "all_time.json", {
        "franchises": [{"id": f["id"], "name": f["name"], "owners": f["owners"], **f["totals"]} for f in franchises.values()],
        "managers": [{"id": m["id"], "name": m["name"], "franchises": sorted({st["franchise"] for st in m["stints"]}),
                      "active": any(st["to"] is None for st in m["stints"]), **m["totals"],
                      "championships": m["championships"], "runner_up": m["runner_up"], "ultimate_loser": m["ultimate_loser"]} for m in all_managers],
    })
    team_games, player_games = [], []
    for game in game_log:
        a, b = game["teams"]
        for me, them in ((a, b), (b, a)):
            base = {"game": game["id"], "season": game["season"], "week": game["week"], "kind": game["kind"],
                    "franchise": me["franchise"], "team": me["team"], "managers": me["managers"]}
            team_games.append({**base, "points": me["points"], "opponent": them["franchise"], "opponent_points": them["points"],
                               "won": game["winner"] == me["franchise"]})
            for role in ("starters", "bench"):
                for line in me[role]:
                    player_games.append({**base, "player": line["player"], "pos": players[line["player"]]["pos"], "points": line["pts"], "role": role})

    def best_per(rows, key_of):
        best = {}
        for row in rows:
            for key in key_of(row):
                if key not in best or row["points"] > best[key]["points"]:
                    best[key] = row
        return [{"key": k, **v} for k, v in sorted(best.items(), key=lambda kv: -kv[1]["points"])]

    by_points = sorted(team_games, key=lambda g: -g["points"])
    started = sorted((p for p in player_games if p["role"] == "starters"), key=lambda p: -p["points"])
    dump(SITE / "records.json", {
        "team_high": by_points[:50], "team_low": by_points[::-1][:25],
        "franchise_best": best_per(team_games, lambda g: [g["franchise"]]),
        "manager_best": best_per(team_games, lambda g: g["managers"]),
        "player_high": started[:50],
        "player_high_by_pos": {pos: [p for p in started if p["pos"] == pos][:10] for pos in ("QB", "RB", "WR", "TE", "K", "DEF")},
        "player_high_bench": sorted((p for p in player_games if p["role"] != "starters"), key=lambda p: -p["points"])[:25],
        # Margins, winner first; a tie (margin 0) counts among the closest.
        "closest": margin_list(game_log, False), "blowouts": margin_list(game_log, True),
        "franchise_player_best": best_per(started, lambda p: [p["franchise"]]),
        "manager_player_best": best_per(started, lambda p: p["managers"]),
    })

    # ---- drafts: the 2023 startup draft and each rookie draft, with what every pick has done since
    adjustments = load(MANUAL / "trade_adjustments.json")["trades"]
    credit = {(c["season"], c["pick_no"]): c["franchise"] for adj in adjustments.values() for c in adj.get("draft_credit", [])}
    career = defaultdict(lambda: {"starts": 0, "pts": 0.0})      # (player, franchise) and (player, None) for league-wide
    for game in game_log:
        for side in game["teams"]:
            for line in side["starters"]:
                for key in ((line["player"], side["franchise"]), (line["player"], None)):
                    career[key]["starts"] += 1
                    career[key]["pts"] += line["pts"]
    draft_index, pick_result, drafted, all_picks = [], {}, {}, []
    for season in season_ids:
        for meta in load(RAW / season / "drafts.json"):
            if meta.get("status") != "complete":   # scheduled or under way; it joins the site once it is finished
                continue
            draft = load(RAW / season / "drafts" / f"{meta['draft_id']}.json")
            day = day_of(draft["start_time"])
            original = {int(slot): rid for slot, rid in (draft.get("slot_to_roster_id") or {}).items()}
            teams = len(original) or 12
            picks = []
            for pick in sorted(load(RAW / season / "drafts" / f"{meta['draft_id']}_picks.json"), key=lambda p: p["pick_no"]):
                fid = credit.get((season, pick["pick_no"]), pick["roster_id"])
                pid = pick["player_id"]
                in_round = pick["pick_no"] - (pick["round"] - 1) * teams
                mine, league = career[(pid, fid)], career[(pid, None)]
                pick_result[(season, pick["round"], original.get(pick["draft_slot"]))] = {
                    "season": season, "date": day, "label": f"{pick['round']}.{in_round:02d}", "overall": pick["pick_no"], "player": pid, "franchise": fid}
                drafted[pid] = {"season": season, "date": day, "label": f"{pick['round']}.{in_round:02d}", "overall": pick["pick_no"], "franchise": fid,
                                "kind": "startup" if draft["type"] == "snake" else "rookie"}
                picks.append({
                    "round": pick["round"], "pick": in_round, "overall": pick["pick_no"], "label": f"{pick['round']}.{in_round:02d}",
                    "franchise": fid, "team": name_on(season, fid, day), "managers": people.owners_on(fid, day),
                    "original": original.get(pick["draft_slot"]), "player": pid, "pos": players[pid]["pos"],
                    "starts_team": mine["starts"], "pts_team": r2(mine["pts"]), "starts_league": league["starts"], "pts_league": r2(league["pts"]),
                })
            kind = "startup" if draft["type"] == "snake" else "rookie"
            dump(SITE / "drafts" / f"{season}.json", {"season": season, "kind": kind, "date": day, "rounds": draft["settings"]["rounds"],
                                                       "format": draft["type"], "picks": picks})
            all_picks += [{**p, "season": season, "kind": kind} for p in picks]
            draft_index.append({"season": season, "kind": kind, "date": day, "rounds": draft["settings"]["rounds"],
                                "first_pick": picks[0]["player"] if picks else None, "first_pick_franchise": picks[0]["franchise"] if picks else None})

    # ---- every week a player spent on an iooi roster
    appearances = defaultdict(list)
    for game in game_log:
        for side in game["teams"]:
            won = game["winner"] == side["franchise"]
            other = next(t for t in game["teams"] if t is not side)
            for started, lines in ((True, side["starters"]), (False, side["bench"])):
                for line in lines:
                    appearances[line["player"]].append({
                        "season": game["season"], "week": game["week"], "date": game["date"], "game": game["id"], "kind": game["kind"],
                        "franchise": side["franchise"], "team": side["team"], "opp": other["franchise"], "opp_team": other["team"], "slot": line.get("slot"),
                        "started": started, "pts": line["pts"], "proj": line.get("proj"), "won": won})
    # Weeks a team had no game (playoff byes, eliminated) still count as time on the roster.
    for season, s in seasons.items():
        played = {(a["season"], a["week"], a["franchise"]) for rows in appearances.values() for a in rows}
        for (week, roster_id), side in s["sides"].items():
            if (season, week, roster_id) in played:
                continue
            for pid in side["players"]:
                appearances[pid].append({"season": season, "week": week, "date": s["week_starts"].get(week), "game": None, "kind": "idle",
                                         "franchise": roster_id, "team": name_on(season, roster_id, s["week_starts"].get(week)),
                                         "started": False, "pts": side["points"].get(pid, 0.0), "proj": None, "won": None})
    for rows in appearances.values():
        rows.sort(key=lambda a: (a["season"], a["week"]))
    value_notes = annotate_value(appearances, players, raw_players, seasons, season_rows)

    polls = load(RAW / "trade_polls.json") if (RAW / "trade_polls.json").exists() else {}
    trades = build_trades(all_moves, adjustments, polls, seasons, people, name_on, pick_result, appearances, game_log, value_notes, players)
    current = seasons[latest]
    on_roster = rosters_today(latest, current)
    dump(SITE / "draft_analysis.json", draft_analysis(all_picks, on_roster, latest))
    waivers = waiver_report(all_moves, appearances, people, name_on, {c for a in adjustments.values() for c in a.get("absorb", [])}, players)
    log = waivers.pop("log")
    for entry in log["pickups"] + log["drops"]:   # a player who never spent a game week on a roster has no page; carry his name
        if entry["player"] not in players and (raw_players.get(entry["player"]) or {}).get("full_name"):
            entry["name"], entry["pos"] = raw_players[entry["player"]]["full_name"], raw_players[entry["player"]].get("position")
    dump(SITE / "waiver_log.json", log)   # every pickup and release; the team-season pages draw on it
    dump(SITE / "waivers.json", waivers)
    dump(SITE / "trade_records.json", trade_records(trades, people))
    dump(SITE / "head_to_head.json", head_to_head(game_log))
    dump(SITE / "trades.json", trades)
    rookie_year = {pid: year for pid, p in raw_players.items() if (year := rookie_year_of(p, state)) is not None}
    awards, honors = build_awards(seasons, season_rows, game_log, players, raw_players, rookie_year)
    dump(SITE / "awards.json", awards)
    for pid, info in players.items():
        info["honors"] = honors.get(pid, [])
    constitution = load(MANUAL / "constitution.json")
    dump(SITE / "constitution.json", constitution)
    dump(SITE / "timeline.json", build_timeline(seasons, people, franchises, name_history, draft_index, trades, constitution, game_log, players, name_on))
    dump(SITE / "lottery.json", build_lottery(seasons, season_rows, name_on))
    nicknames = build_nicknames(seasons, people, players, name_on)
    dump(SITE / "nicknames.json", nicknames)
    for row in nicknames:
        if row["player"] and row["nickname"]:
            names = players[row["player"]].setdefault("nicknames", [])
            if row["nickname"] not in names:
                names.append(row["nickname"])
    split = load(MANUAL / "split_draft_2024.json")
    dump(SITE / "split_draft.json", build_split_draft(split, players, all_picks, all_moves, appearances, people, name_on, on_roster))
    build_players(players, raw_players, seasons, appearances, all_moves, adjustments, drafted, trades, state, name_on, nicknames)
    dump(SITE / "leaders.json", {**build_leaders(appearances, players), "value": value_notes})
    dump(SITE / "elo.json", build_elo(game_log, seasons))
    for season in season_ids:
        wire = transaction_wire(season, all_moves, adjustments, people, name_on, trades)
        # A player added and released between games never reaches players.json; carry his name on the entry instead.
        for entry in wire:
            moved = entry.get("added", []) + entry.get("released", []) + [m["player"] for m in entry.get("players", []) + entry.get("swapped", [])]
            unknown = {pid: (raw_players.get(pid) or {}).get("full_name") for pid in moved if pid not in players}
            if any(unknown.values()):
                entry["names"] = {pid: name for pid, name in unknown.items() if name}
        dump(SITE / "transactions" / f"{season}.json", wire)
    for fid, franchise in franchises.items():
        franchise["leaders"] = franchise_leaders(fid, appearances, players)
        dump(SITE / "franchises" / f"{fid}.json", franchise)

    dump(SITE / "players.json", players)
    # When the data was last pulled: the fetch script's stamp, or failing that the age of the league state file.
    stamp = RAW / "fetched.json"
    updated = load(stamp)["at"] if stamp.exists() else datetime.fromtimestamp((RAW / "nfl_state.json").stat().st_mtime, timezone.utc).isoformat(timespec="minutes")
    dump(SITE / "league.json", {
        "name": "Dynasty iooi", "founded": 2023, "commissioner": "riley", "current_season": latest,
        # The fantasy week now under way, 1-17; 0 in the offseason, the preseason and after the final.
        "current_week": (seasons[latest]["last_week"] + 1 if seasons[latest]["league"]["status"] == "in_season" and seasons[latest]["last_week"] < WEEKS[-1]
                         and state["season"] == latest and state.get("season_type", "regular") == "regular" else 0),
        "updated": updated,
        "seasons": [season_summary(s, seasons[s], season_rows[s]) for s in season_ids],
        "drafts": draft_index,
        "split_draft": {"date": split["date"]},
        "franchises": [{"id": f["id"], "name": f["name"], "owners": f["owners"]} for f in franchises.values()],
        "managers": [{"id": slug, "name": m["name"]} for slug, m in people.managers.items()],
    })
    import extras
    extras.main()
    print(f"built {len(game_log)} games, {len(players)} players, {len(season_ids)} seasons")


if __name__ == "__main__":
    main()

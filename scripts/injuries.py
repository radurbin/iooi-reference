"""Games missed to injury, from nflverse's weekly NFL roster statuses and injury reports (fetched to
data/raw/<season>/injuries.json by fetch.py).

A game counts as missed to injury when the player's NFL team played that week, he did not play, and he was on
injured reserve, PUP or the non-football injury list, or was listed Out, Doubtful or Questionable, or was a game-day
inactive with an injury. Bye weeks, healthy scratches, suspensions and illnesses on the reserve list do not count,
nor do report lines that are not injuries (rest, personal matters). Weeks 1-17, as the league plays; a week counts
once his team's game has been scored. Kickers and defenses are left out.

Team, franchise and manager totals count every player on the roster that week, starters, bench, taxi and IR alike.
VOR lost estimates what each missed game cost: the player's points per game that season (with the season before
filled in when he played fewer than four games) above a replacement-level player at his position, the same replacement
level as the site's VOR and WAR. A backup who would not have started counts for little or nothing.
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW, NFL, SITE, MANUAL = ROOT / "data" / "raw", ROOT / "data" / "nfl", ROOT / "data" / "site", ROOT / "data" / "manual"
WEEKS = range(1, 18)
RESERVE = {"R01": "IR", "R48": "IR", "R04": "PUP", "R05": "NFI"}   # injured reserve (and designated to return), PUP, non-football injury
REPORT = ("Out", "Doubtful", "Questionable")
NOT_INJURY = re.compile(r"not injury|personal|\brest\b|resting|coach|load management", re.I)
TEAM = {"LA": "LAR"}   # nflverse team codes that Sleeper writes differently
MIN_GAMES = 4          # games in a season before his own points per game stands alone
TOP = 25
CURRENT = {"IR", "Out", "Doubtful", "PUP", "Questionable"}   # Sleeper statuses shown as currently hurt


def load(path):
    return json.loads(Path(path).read_text())


def dump(path, data):
    Path(path).write_text(json.dumps(data, indent=1, ensure_ascii=False))


def r2(x):
    return round(x + 0.0, 2)


def label(text):
    """Injury names as written, with all-lower or all-caps ones put in sentence case so they count together."""
    text = (text or "").strip()
    if text.lower() in ("undisclosed", "unknown", "n/a", "na"):
        return ""
    if text.islower() or text.isupper():
        text = text.lower()
    return text[:1].upper() + text[1:]


def missed_games(seasons, logs, players):
    """Every game missed to injury: {season, week, player, nfl, kind, injury, franchise, team, lost}."""
    leaders = SITE / "leaders.json"
    replacement = load(leaders)["value"]["replacement"] if leaders.exists() else {}
    out = []
    nfl_by_season = {season: load(NFL / f"{season}.json") for season in seasons if (NFL / f"{season}.json").exists()}
    on = {(pid, a["season"], a["week"]): a for pid, log in logs.items() for a in log}
    for season in seasons:
        path, nfl = RAW / season / "injuries.json", nfl_by_season.get(season)
        if not path.exists() or not nfl:
            continue
        rows = load(path)["weeks"]
        prev = nfl_by_season.get(str(int(season) - 1))

        def ppg(pid):
            games = [w["points"][pid] for wk, w in nfl["weeks"].items() if int(wk) in WEEKS and pid in w["points"]]
            if len(games) < MIN_GAMES and prev:
                games += [w["points"][pid] for wk, w in prev["weeks"].items() if int(wk) in WEEKS and pid in w["points"]]
            return sum(games) / len(games) if games else 0.0

        rate, last_injury = {}, {}
        floor = replacement.get(season, {})
        for week in WEEKS:
            week_nfl = nfl["weeks"].get(str(week)) or {}
            if not week_nfl.get("points"):
                continue   # not scored yet
            played_teams, scored = set(week_nfl.get("played", [])), week_nfl["points"]
            for pid, row in sorted((rows.get(str(week)) or {}).items()):
                team, status, code, report, injury = row[:5]
                if pid not in players or players[pid]["pos"] in ("K", "DEF"):
                    continue
                # The report's injury, else Sleeper's note kept by fetch.py for that week.
                injury = label(injury) or label(row[5] if len(row) > 5 else "")
                if injury and not NOT_INJURY.search(injury):
                    last_injury[pid] = injury
                team = TEAM.get(team, team)
                if team not in played_teams:
                    continue
                if status == "RES":
                    kind = RESERVE.get(code)
                elif pid in scored and status != "INA":
                    kind = None   # he played
                elif report in REPORT:
                    kind = report
                elif status == "INA" and injury:
                    kind = "Inactive"
                else:
                    kind = None
                if not kind or (injury and NOT_INJURY.search(injury)):
                    continue
                if pid not in rate:   # points per game above a replacement-level player at his position
                    rate[pid] = max(0.0, ppg(pid) - floor.get(players[pid]["pos"], 0.0))
                a = on.get((pid, season, week))
                out.append({"season": season, "week": week, "player": pid, "nfl": team, "kind": kind,
                            "injury": injury if injury and not NOT_INJURY.search(injury) else last_injury.get(pid, ""),
                            "franchise": a["franchise"] if a else None, "team": a["team"] if a else None, "lost": r2(rate[pid])})
    return out, nfl_by_season


def stints(misses, nfl_by_season):
    """Runs of games missed in a row within a season; his team's bye weeks are skipped."""
    by = defaultdict(list)
    for m in misses:
        by[(m["player"], m["season"])].append(m)
    runs = []
    for (pid, season), rows in by.items():
        rows.sort(key=lambda m: m["week"])
        weeks = (nfl_by_season.get(season) or {}).get("weeks", {})
        run = [rows[0]]
        for m in rows[1:]:
            gap = range(run[-1]["week"] + 1, m["week"])
            if all(run[-1]["nfl"] not in (weeks.get(str(w)) or {}).get("played", []) for w in gap):
                run.append(m)
            else:
                runs.append(run)
                run = [m]
        runs.append(run)
    return runs


def summary_of(rows):
    injuries = Counter(m["injury"] for m in rows if m["injury"])
    return {"missed": len(rows), "lost": r2(sum(m["lost"] for m in rows)), "injuries": [k for k, _ in injuries.most_common()],
            "weeks": [m["week"] for m in rows], "kinds": sorted({m["kind"] for m in rows})}


def build(league, players, logs):
    seasons = [s["season"] for s in league["seasons"]]
    current = league["current_season"]
    misses, nfl_by_season = missed_games(seasons, logs, players)
    raw_players = load(RAW / "players_nfl.json") if (RAW / "players_nfl.json").exists() else {}
    # A current absence the NFL report never named (a season-opening stay on IR, say) takes Sleeper's injury note.
    for m in misses:
        note = label((raw_players.get(m["player"]) or {}).get("injury_body_part"))
        if m["season"] == current and not m["injury"] and note and not NOT_INJURY.search(note) and (raw_players[m["player"]].get("injury_status") or "") != "":
            m["injury"] = note
    franchise_name = {f["id"]: f["name"] for f in league["franchises"]}
    rostered = [m for m in misses if m["franchise"] is not None]
    runs = stints(misses, nfl_by_season)
    # Weeks of a stint the report never named (a long stay on IR, say) take the injury named elsewhere in that stint;
    # failing that, the one he was last reported with just before it: in the two weeks before, or, for a stint from
    # week 1, the last weeks of the season before (a player carried over on PUP).
    reported = defaultdict(list)
    for season in seasons:
        path = RAW / season / "injuries.json"
        for week, rows in (load(path)["weeks"].items() if path.exists() else ()):
            for pid, row in rows.items():
                text = label(row[4])
                if text and not NOT_INJURY.search(text):
                    reported[pid].append((int(season), int(week), text))
    for run in runs:
        named = Counter(m["injury"] for m in run if m["injury"])
        if not named:
            start = (int(run[0]["season"]), run[0]["week"])
            window = (start[0] - 1, 14) if start[1] == 1 else (start[0], start[1] - 2)
            before = [x for x in reported[run[0]["player"]] if window <= x[:2] < start]
            if before:
                named = Counter([max(before)[2]])
        if named:
            for m in run:
                m["injury"] = m["injury"] or named.most_common(1)[0][0]
    # Failing all that, the injury filled in by hand in data/manual/injury_notes.json.
    notes = load(MANUAL / "injury_notes.json")["absences"] if (MANUAL / "injury_notes.json").exists() else []
    for run in runs:
        if run[0]["injury"]:
            continue
        weeks = {m["week"] for m in run}
        for n in notes:
            lo, _, hi = n["weeks"].partition("-")
            if n.get("injury") and n["player"] == run[0]["player"] and n["season"] == run[0]["season"] and weeks & set(range(int(lo), int(hi or lo) + 1)):
                for m in run:
                    m["injury"] = n["injury"].strip()   # as written, "Undisclosed" included
                break
    # New absences on iooi rosters with no name yet, for the daily job's log: add them to data/manual/injury_notes.json.
    unnamed = [run for run in runs if not run[0]["injury"] and any(m["franchise"] is not None for m in run)]
    for run in unnamed:
        print(f"::notice::injury not named: {players[run[0]['player']]['name']} ({run[0]['player']}), {run[0]['season']} weeks "
              f"{run[0]['week']}-{run[-1]['week']}; add it to data/manual/injury_notes.json")
    who = lambda pid: {"player": pid, "name": players[pid]["name"], "pos": players[pid]["pos"]}
    team_files = {(season, f["id"]): SITE / "teams" / f"{season}-{f['id']}.json" for season in seasons for f in league["franchises"]}
    team_data = {k: load(p) for k, p in team_files.items() if p.exists()}

    # ---- team seasons: every player on the roster that week
    by_team = defaultdict(list)
    for m in rostered:
        by_team[(m["season"], m["franchise"])].append(m)
    team_rows = []
    for (season, fid), t in sorted(team_data.items()):
        rows = by_team.get((season, fid), [])
        by_player = defaultdict(list)
        for m in rows:
            by_player[m["player"]].append(m)
        worst = max(by_player.items(), key=lambda x: (sum(m["lost"] for m in x[1]), len(x[1]), x[0]), default=None)
        team_rows.append({"season": season, "franchise": fid, "team": t["team"], "managers": t["managers"], "w": t["row"]["w"], "l": t["row"]["l"],
                          "finish": t["row"].get("finish"), "games": len(rows), "players": len(by_player), "lost": r2(sum(m["lost"] for m in rows)),
                          "worst": {**who(worst[0]), "missed": len(worst[1]), "lost": r2(sum(m["lost"] for m in worst[1]))} if worst else None})
    for season in seasons:
        mine = [r for r in team_rows if r["season"] == season]
        for r in mine:
            r["rank"] = 1 + sum(1 for o in mine if o["lost"] > r["lost"])
            r["games_rank"] = 1 + sum(1 for o in mine if o["games"] > r["games"])
            r["of"] = len(mine)

    # ---- players: per season and career, while on an iooi roster
    player_season = defaultdict(list)
    for m in rostered:
        player_season[(m["player"], m["season"])].append(m)

    def season_row(pid, season, rows):
        teams = list(dict.fromkeys((m["franchise"], m["team"]) for m in rows))
        return {**who(pid), "season": season, **summary_of(rows), "teams": [{"franchise": f, "team": t} for f, t in teams]}
    season_list = [season_row(pid, season, rows) for (pid, season), rows in player_season.items()]
    career = defaultdict(list)
    for m in rostered:
        career[m["player"]].append(m)
    career_list = [{**who(pid), **summary_of(rows), "seasons": len({m["season"] for m in rows}),
                    "franchises": len({m["franchise"] for m in rows})} for pid, rows in career.items()]
    for r in career_list:
        del r["weeks"]

    def run_row(run):
        injuries = Counter(m["injury"] for m in run if m["injury"])
        teams = list(dict.fromkeys((m["franchise"], m["team"]) for m in run if m["franchise"] is not None))
        return {**who(run[0]["player"]), "season": run[0]["season"], "from": run[0]["week"], "to": run[-1]["week"], "missed": len(run),
                "nfl": "/".join(dict.fromkeys(m["nfl"] for m in run)), "weeks": [m["week"] for m in run],
                "injury": injuries.most_common(1)[0][0] if injuries else "", "kinds": sorted({m["kind"] for m in run}),
                "lost": r2(sum(m["lost"] for m in run if m["franchise"] is not None)), "teams": [{"franchise": f, "team": t} for f, t in teams],
                "rostered": sum(1 for m in run if m["franchise"] is not None)}
    run_rows = [run_row(run) for run in runs]
    rostered_runs = [r for r in run_rows if r["rostered"]]

    # ---- the latest scored week of each NFL team, so "out now" streaks end where the data does
    latest = max(((s, w) for s, nfl in nfl_by_season.items() for w, e in nfl["weeks"].items() if e.get("points") and int(w) in WEEKS),
                 key=lambda x: (x[0], int(x[1])), default=None)
    out_now = []
    for pid, info in raw_players.items():
        st = (info.get("injury_status") or "")
        st = {"IR": "IR", "OUT": "Out", "DOUBTFUL": "Doubtful", "PUP": "PUP", "QUESTIONABLE": "Questionable"}.get(st.upper(), st)
        status = (players.get(pid) or {}).get("status") or {}
        if (st not in CURRENT or pid not in players or players[pid]["pos"] in ("K", "DEF") or status.get("code") != "rostered"
                or NOT_INJURY.search(info.get("injury_body_part") or "")):
            continue
        mine = [r for r in run_rows if r["player"] == pid and r["season"] == current]
        streak = mine[-1] if mine and latest and latest[0] == current and mine[-1]["to"] >= max(1, int(latest[1]) - 2) else None
        out_now.append({**who(pid), "franchise": status["franchise"], "team": (team_data.get((current, status["franchise"])) or {}).get("team", franchise_name.get(status["franchise"])), "status": st,
                        "injury": label(info.get("injury_body_part")) or (streak["injury"] if streak else ""), "since": info.get("injury_start_date"),
                        "streak": streak["missed"] if streak else 0,
                        "season_missed": len([m for m in misses if m["player"] == pid and m["season"] == current])})
    order = {"IR": 0, "PUP": 1, "Out": 2, "Doubtful": 3, "Questionable": 4}
    out_now.sort(key=lambda r: (order[r["status"]], -r["streak"], r["name"]))

    # ---- franchises and managers, all seasons
    def totals(key_rows):
        rows = list(key_rows)
        return {"games": sum(r["games"] for r in rows), "lost": r2(sum(r["lost"] for r in rows)), "seasons": len(rows),
                "per_season": r2(sum(r["lost"] for r in rows) / len(rows)) if rows else 0,
                "worst": max(rows, key=lambda r: r["lost"])["season"] if rows else None}
    franchise_rows = [{"franchise": f["id"], "name": f["name"], **totals(r for r in team_rows if r["franchise"] == f["id"])} for f in league["franchises"]]
    managers = {m["id"]: m["name"] for m in league["managers"]}
    manager_rows = [{"key": slug, "name": managers.get(slug, slug), **totals(r for r in team_rows if slug in r["managers"])}
                    for slug in sorted({s for r in team_rows for s in r["managers"]})]

    # ---- injuries and positions
    # By body part: "Knee - ACL" and "Knee - meniscus" count as Knee.
    part = lambda text: re.split(r" - | \(", text)[0]
    kinds = defaultdict(lambda: {"games": 0, "players": set(), "stints": 0})
    named = lambda text: text and text != "Undisclosed"
    for m in rostered:
        if named(m["injury"]):
            kinds[part(m["injury"])]["games"] += 1
            kinds[part(m["injury"])]["players"].add(m["player"])
    for r in rostered_runs:
        if named(r["injury"]):
            kinds[part(r["injury"])]["stints"] += 1
    injury_rows = sorted(({"injury": k, "games": v["games"], "players": len(v["players"]), "stints": v["stints"]} for k, v in kinds.items()),
                         key=lambda r: (-r["games"], r["injury"]))
    positions = []
    for season in seasons:
        mine = [m for m in rostered if m["season"] == season]
        if mine:
            count = Counter(players[m["player"]]["pos"] for m in mine)
            lost = Counter()
            for m in mine:
                lost[players[m["player"]]["pos"]] += m["lost"]
            positions.append({"season": season, **{pos: {"games": count[pos], "lost": r2(lost[pos])} for pos in ("QB", "RB", "WR", "TE")}})
    weekly = defaultdict(Counter)
    for m in rostered:
        weekly[m["season"]][m["week"]] += 1

    top = lambda rows, key: sorted(rows, key=lambda r: (-r[key], -r["lost"] if key != "lost" else -r["missed"], r["name"]))[:TOP]
    dump(SITE / "injuries.json", {
        "seasons": seasons, "current": current, "latest": {"season": latest[0], "week": int(latest[1])} if latest else None,
        "out_now": out_now,
        "player_seasons": top(season_list, "missed"), "player_seasons_lost": top(season_list, "lost"),
        "player_careers": top(career_list, "missed"), "player_careers_lost": top(career_list, "lost"),
        "stints": sorted(rostered_runs, key=lambda r: (-r["missed"], -r["lost"], r["season"], r["name"]))[:TOP],
        "team_seasons": team_rows, "franchises": franchise_rows, "managers": manager_rows,
        "injuries": injury_rows[:30], "positions": positions,
        "weekly": {season: [weekly[season].get(w, 0) for w in WEEKS] for season in seasons if weekly[season]},
        "totals": {"games": len(rostered), "lost": r2(sum(m["lost"] for m in rostered)), "players": len({m["player"] for m in rostered})},
    })

    # ---- player pages: every NFL game missed, on an iooi roster or not
    by_player = defaultdict(list)
    for m in misses:
        by_player[m["player"]].append(m)
    for pid in players:
        path = SITE / "players" / f"{pid}.json"
        if not path.exists():
            continue
        f = load(path)
        rows = by_player.get(pid, [])
        if not rows and "injuries" not in f:
            continue
        per = []
        for season in seasons:
            mine = [m for m in rows if m["season"] == season]
            if mine:
                per.append({"season": season, "nfl": "/".join(dict.fromkeys(m["nfl"] for m in mine)), **summary_of(mine),
                            "rostered": sum(1 for m in mine if m["franchise"] is not None),
                            "lost": r2(sum(m["lost"] for m in mine if m["franchise"] is not None)),
                            "games": [{k: m[k] for k in ("week", "kind", "injury", "franchise", "team")} for m in mine]})
        f["injuries"] = {"seasons": per, "stints": [{k: r[k] for k in ("season", "from", "to", "weeks", "missed", "nfl", "injury", "kinds", "teams", "rostered", "lost")} for r in run_rows if r["player"] == pid],
                         "now": next(({k: r[k] for k in ("status", "injury", "since", "streak")} for r in out_now if r["player"] == pid), None)} if rows else None
        dump(path, f)

    # ---- team-season pages
    for (season, fid), t in team_data.items():
        rows = by_team.get((season, fid), [])
        row = next(r for r in team_rows if r["season"] == season and r["franchise"] == fid)
        by_p = defaultdict(list)
        for m in rows:
            by_p[m["player"]].append(m)
        players_rows = []
        for pid, mine in by_p.items():
            gs = sum(1 for a in logs[pid] if a["season"] == season and a["franchise"] == fid and a["started"])
            players_rows.append({**who(pid), **summary_of(mine), "gs": gs})
        players_rows.sort(key=lambda r: (-r["lost"], -r["missed"], r["name"]))
        t["injuries"] = {k: row[k] for k in ("games", "players", "lost", "rank", "games_rank", "of")}
        t["injuries"]["rows"] = players_rows
        t["injuries"]["by_week"] = [sum(1 for m in rows if m["week"] == w) for w in WEEKS]
        dump(team_files[(season, fid)], t)

    # ---- franchise pages
    for f in league["franchises"]:
        path = SITE / "franchises" / f"{f['id']}.json"
        mine = [m for m in rostered if m["franchise"] == f["id"]]
        by_p = defaultdict(list)
        for m in mine:
            by_p[m["player"]].append(m)
        plist = sorted(({**who(pid), **{k: v for k, v in summary_of(rows).items() if k != "weeks"}, "seasons": sorted({m["season"] for m in rows})}
                        for pid, rows in by_p.items()), key=lambda r: (-r["missed"], -r["lost"], r["name"]))[:15]
        data = load(path)
        data["injuries"] = {"seasons": [{k: r[k] for k in ("season", "team", "games", "players", "lost", "rank", "games_rank", "of", "worst", "w", "l")}
                                        for r in team_rows if r["franchise"] == f["id"]],
                            "players": plist, "total": next(x for x in franchise_rows if x["franchise"] == f["id"])}
        dump(path, data)

    # ---- manager pages
    for slug in managers:
        path = SITE / "managers" / f"{slug}.json"
        if path.exists():
            data = load(path)
            data["injuries"] = [{k: r[k] for k in ("season", "franchise", "team", "games", "players", "lost", "rank", "of")} for r in team_rows if slug in r["managers"]]
            dump(path, data)

    # ---- season pages
    for season in seasons:
        path = SITE / "seasons" / f"{season}.json"
        data = load(path)
        mine = sorted((r for r in team_rows if r["season"] == season), key=lambda r: (r["rank"], r["team"]))
        data["injuries"] = {"teams": mine, "players": top([r for r in season_list if r["season"] == season], "missed")[:10]} if any(r["games"] for r in mine) else None
        dump(path, data)

    # ---- box scores: who each side was missing to injury that week
    for season in seasons:
        path = SITE / "games" / f"{season}.json"
        if not path.exists():
            continue
        games = load(path)
        by_week = defaultdict(list)
        for m in rostered:
            if m["season"] == season:
                by_week[(m["week"], m["franchise"])].append(m)
        for g in games.values():
            for side in g["teams"]:
                hurt = by_week.get((g["week"], side["franchise"]), [])
                side["injured"] = [{"player": m["player"], "name": players[m["player"]]["name"], "pos": players[m["player"]]["pos"],
                                    "kind": m["kind"], "injury": m["injury"]} for m in sorted(hurt, key=lambda m: -m["lost"])]
        dump(path, games)
    return misses, nfl_by_season, run_rows


def week_notes(week_data, misses, nfl_by_season):
    """This Week storylines: a starter back from missing two or more games in a row. From the weeks before this one,
    so the note holds all week."""
    season, week = week_data.get("season"), week_data.get("week")
    if not season or not week_data.get("matchups"):
        return
    nfl = (nfl_by_season.get(season) or {}).get("weeks", {})
    raw_players = load(RAW / "players_nfl.json") if (RAW / "players_nfl.json").exists() else {}
    by_player = defaultdict(dict)
    for m in misses:
        if m["season"] == season and m["week"] < week:
            by_player[m["player"]][m["week"]] = m
    for mu in week_data["matchups"]:
        for side in (mu["a"], mu["b"]):
            for line in side["lineup"]:
                pid = line.get("player")
                mine = by_player.get(pid)
                if not mine:
                    continue
                # His team's most recent game before this week must be one he missed; walk back over its games from
                # there (byes skipped) while he kept missing them.
                team = mine[max(mine)]["nfl"]
                games = [w for w in range(week - 1, 0, -1) if team in (nfl.get(str(w)) or {}).get("played", [])]
                run = []
                for w in games:
                    if w not in mine:
                        break
                    run.append(mine[w])
                still = (raw_players.get(pid) or {}).get("injury_status") in ("IR", "Out", "PUP", "Doubtful")
                if len(run) >= 2 and not still:
                    hurt = f" ({run[0]['injury'].lower()})" if run[0]["injury"] else ""
                    mu["notes"].append(f"[[p:{pid}|{line['name']}]] is back in the lineup after missing {len(run)} games in a row{hurt}.")

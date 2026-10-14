"""Second build pass: lists that are easier to derive from the finished site data.

Streaks and milestones, rivalries, season leader boards, similarity scores and team-season pages.
Called at the end of build.py; reads and writes data/site/.
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "data" / "site"
MANUAL = ROOT / "data" / "manual"

REACH = 1.5   # a points milestone is in reach this week if it is within one and a half times the projection
FRANCHISE_WINS = (10, 25, 50, 75, 100)
FRANCHISE_POINTS = (2500, 5000, 7500, 10000, 15000)
PLAYER_POINTS = (250, 500, 750, 1000, 1500, 2000, 2500)
PLAYER_STARTS = (25, 50, 75, 100)
BIG_GAME = 20          # points for a player's "big game" streak
SIMILAR_MIN_STARTS = 8  # career starts before a player is compared with others
LEADER_ROWS = 5


def load(path):
    return json.loads(path.read_text())


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))


def r2(x):
    return round(x, 2)


# ---------------------------------------------------------------- streaks and milestones

def runs(results):
    """Split a list of (won, game) into runs of consecutive wins or losses."""
    out = []
    for won, game in results:
        if out and out[-1]["won"] == won:
            out[-1]["n"] += 1
            out[-1]["end"] = game
        else:
            out.append({"won": won, "n": 1, "start": game, "end": game})
    return out


def game_ref(g):
    return {"id": g["id"], "season": g["season"], "week": g["week"]}


def team_streaks(games, keys_of):
    """Winning and losing streaks in regular-season and Dynasty Playoff games."""
    results = defaultdict(list)
    for g in games:
        if g["kind"] == "toilet":
            continue
        for side in g["teams"]:
            for key in keys_of(side):
                results[key].append((g["winner"] == side["franchise"], g, side["team"]))
    rows, current = [], []
    for key, seq in results.items():
        team_at = {g["id"]: team for _, g, team in seq}
        found = runs([(won, g) for won, g, _ in seq])
        for i, run in enumerate(found):
            row = {"key": key, "n": run["n"], "won": run["won"], "from": game_ref(run["start"]), "to": game_ref(run["end"]),
                   "team": team_at[run["end"]["id"]], "active": i == len(found) - 1}
            rows.append(row)
            if row["active"]:
                current.append(row)
    top = lambda won: sorted((r for r in rows if r["won"] == won and r["n"] >= 3), key=lambda r: (-r["n"], r["from"]["id"]))[:15]
    return {"wins": top(True), "losses": top(False), "current": sorted(current, key=lambda r: (not r["won"], -r["n"]))}


def player_streaks(players, logs):
    """Runs of starts. An NFL bye is skipped; a week on the bench or off every iooi roster ends the run."""
    byes = {(path.stem, int(week)): set(entry.get("bye", [])) for path in (ROOT / "data" / "nfl").glob("*.json") for week, entry in load(path)["weeks"].items()}
    calendar = sorted({(a["season"], a["week"]) for log in logs.values() for a in log if a["game"]})
    starts, big = [], []
    for pid, log in logs.items():
        at = {(a["season"], a["week"]): a for a in log}
        began = next(((a["season"], a["week"]) for a in log if a["game"] and a["started"]), None)
        if not began:
            continue
        run, hot = [], []

        def close(seq, into, active=False):
            if len(seq) >= 2:
                into.append({"player": pid, "name": players[pid]["name"], "pos": players[pid]["pos"], "n": len(seq), "pts": r2(sum(a["pts"] for a in seq)), "from": game_ref(seq[0] | {"id": seq[0]["game"]}),
                             "to": game_ref(seq[-1] | {"id": seq[-1]["game"]}), "teams": list(dict.fromkeys(a["team"] for a in seq)),
                             "franchise": seq[-1]["franchise"], "active": active})
        for when in calendar[calendar.index(began):]:
            a = at.get(when)
            if a and a["game"] and a["started"]:
                run.append(a)
                if a["pts"] >= BIG_GAME:
                    hot.append(a)
                else:
                    close(hot, big)
                    hot = []
            elif pid in byes.get(when, ()) or (a and not a["game"]):
                continue   # his NFL team, or his iooi team, had no game
            else:
                close(run, starts)
                close(hot, big)
                run, hot = [], []
        close(run, starts, True)
        close(hot, big, True)
    top = lambda rows: sorted(rows, key=lambda r: (-r["n"], -r["pts"]))[:20]
    return {"starts": top(starts), "big_games": top(big), "big_game": BIG_GAME}


def postseason_runs(seasons, franchises):
    done = [s for s in seasons.values() if s["status"] == "complete"]
    rows = []
    for f in franchises:
        made = [(s["season"], next(r for r in s["standings"] if r["franchise"] == f["id"])) for s in done]
        flags = [row["seed"] <= 6 for _, row in made]
        streak = 0
        for flag in reversed(flags):
            if flag != flags[-1]:
                break
            streak += 1
        titles = [season for season, row in made if row["finish"] == 1]
        best = min(row["finish"] for _, row in made)
        rows.append({"franchise": f["id"], "appearances": sum(flags), "seasons": len(flags), "in_playoffs": flags[-1], "streak": streak,
                     "titles": titles, "since_title": len(done) - 1 - [s["season"] for s in done].index(titles[-1]) if titles else None,
                     "best_finish": best, "best_seasons": [season for season, row in made if row["finish"] == best]})
    names = {f["id"]: f["name"].lower() for f in franchises}
    return sorted(rows, key=lambda r: (-r["appearances"], names[r["franchise"]]))


def milestones(games, players, logs, franchise_names, manager_names, rostered, active_managers):
    """Milestones reached, and the next ones in sight. Only current managers and players on an iooi roster are still chasing one."""
    reached, upcoming = [], []

    def walk(kind, key, name, label, marks, steps, unit):
        """steps: list of (amount, game, team) in order."""
        total, pending = 0, list(marks)
        for amount, g, team in steps:
            total += amount
            while pending and total >= pending[0]:
                reached.append({"kind": kind, "key": key, "name": name, "what": f"{pending[0]:,} {label}", "date": g["date"], "game": g["id"],
                                "season": g["season"], "week": g["week"], "team": team})
                pending.pop(0)
        if pending and total:
            upcoming.append({"kind": kind, "key": key, "name": name, "what": f"{pending[0]:,} {label}", "have": r2(total), "need": r2(pending[0] - total),
                             "share": total / pending[0], "unit": unit})

    for kind, names, keys_of in (("franchise", franchise_names, lambda side: [side["franchise"]]), ("manager", manager_names, lambda side: side["managers"])):
        wins, points = defaultdict(list), defaultdict(list)
        for g in games:
            if g["kind"] == "toilet":
                continue
            for side in g["teams"]:
                for key in keys_of(side):
                    wins[key].append((1 if g["winner"] == side["franchise"] else 0, g, side["team"]))
                    points[key].append((side["points"], g, side["team"]))
        for key in wins:
            walk(kind, key, names[key], "wins", FRANCHISE_WINS, wins[key], "wins")
            walk(kind, key, names[key], "points", FRANCHISE_POINTS, points[key], "points")
    by_id = {g["id"]: g for g in games}
    for pid, log in logs.items():
        steps = [(a["pts"], by_id[a["game"]], a["team"]) for a in log if a["started"] and a["game"]]
        if steps:
            walk("player", pid, players[pid]["name"], "points", PLAYER_POINTS, steps, "points")
            walk("player", pid, players[pid]["name"], "starts", PLAYER_STARTS, [(1, g, team) for _, g, team in steps], "starts")
    reached.sort(key=lambda m: (m["date"], m["game"]), reverse=True)
    upcoming = [u for u in upcoming if u["kind"] == "franchise" or (u["key"] in active_managers if u["kind"] == "manager" else u["key"] in rostered)]
    close = lambda kind: sorted((u for u in upcoming if u["kind"] == kind), key=lambda u: -u["share"])
    # "all" feeds the weekly storylines and is not written to the site.
    return {"reached": reached, "upcoming": close("franchise")[:12] + close("manager")[:12] + close("player")[:25], "all": upcoming}


# ---------------------------------------------------------------- rivalries

def rivalries(games, trades):
    def collect(keys_of):
        pairs = defaultdict(lambda: {"games": [], "trades": []})
        for g in games:
            a, b = g["teams"]
            for x in keys_of(a):
                for y in keys_of(b):
                    if x == y:
                        continue
                    first, second = (a, b) if str(x) < str(y) else (b, a)
                    lo, hi = sorted((x, y), key=str)
                    pairs[f"{lo}~{hi}"]["games"].append({
                        "id": g["id"], "season": g["season"], "week": g["week"], "date": g["date"], "kind": g["kind"], "label": g["label"],
                        "a_team": first["team"], "b_team": second["team"], "a_pts": first["points"], "b_pts": second["points"],
                        "a_fid": first["franchise"], "b_fid": second["franchise"]})
        for t in trades:
            if t["status"] != "complete":
                continue
            for i, a in enumerate(t["sides"]):
                for b in t["sides"][i + 1:]:
                    for x in keys_of(a):
                        for y in keys_of(b):
                            if x == y:
                                continue
                            lo, hi = sorted((x, y), key=str)
                            first, second = (a, b) if str(x) < str(y) else (b, a)
                            entry = {"id": t["id"], "date": t["date"], "a_team": first["team"], "b_team": second["team"],
                                     "a_pts": first["total"]["pts"], "b_pts": second["total"]["pts"],
                                     "a_assets": len(first["assets"]), "b_assets": len(second["assets"]), "teams": len(t["sides"])}
                            if entry not in pairs[f"{lo}~{hi}"]["trades"]:
                                pairs[f"{lo}~{hi}"]["trades"].append(entry)
        return pairs
    return {"franchises": collect(lambda side: [side["franchise"]]), "managers": collect(lambda side: side["managers"])}


# ---------------------------------------------------------------- season leaders

def season_leaders(season, players, careers, logs, weeks_played):
    rows = defaultdict(lambda: {"gs": 0, "pts": 0.0, "war": 0.0, "paa": 0.0, "vor": 0.0, "teams": []})
    for pid, career in careers.items():
        for c in career:
            if c["season"] == season and c["gs"]:
                r = rows[pid]
                r["gs"] += c["gs"]
                for k in ("pts", "war", "paa", "vor"):
                    r[k] += c[k] or 0
                r["teams"].append({"franchise": c["franchise"], "team": c["team"]})
    floor = max(1, weeks_played // 2)

    def board(title, tip, value, digits=2, keep=lambda pid, r: True):
        ranked = sorted(((value(r), pid) for pid, r in rows.items() if keep(pid, r)), reverse=True)[:LEADER_ROWS]
        return {"title": title, "tip": tip, "digits": digits, "rows": [{"player": pid, "name": players[pid]["name"], "value": round(v, digits), "teams": rows[pid]["teams"]} for v, pid in ranked]}
    best = sorted(((a["pts"], pid, a) for pid, log in logs.items() for a in log if a["season"] == season and a["started"]), key=lambda x: -x[0])[:LEADER_ROWS]
    boards = [
        board("Points", "Points as a starter", lambda r: r["pts"]),
        board("Points per Start", f"Points per start, at least {floor} starts", lambda r: r["pts"] / r["gs"], keep=lambda pid, r: r["gs"] >= floor),
        board("Wins Above Replacement", "WAR: value over a replacement-level player, in wins", lambda r: r["war"]),
        board("Points Above Average", "Points above the average iooi starter at the position", lambda r: r["paa"], 1),
        board("Games Started", "Games in a starting lineup", lambda r: r["gs"], 0),
        {"title": "Best Single Game", "tip": "Most points by a starter in one game", "digits": 2,
         "rows": [{"player": pid, "name": players[pid]["name"], "value": pts, "teams": [{"franchise": a["franchise"], "team": a["team"]}], "game": a["game"]} for pts, pid, a in best]},
    ]
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        boards.append(board(f"{pos} Points", f"Points as a starter, {pos}", lambda r: r["pts"], keep=lambda pid, r, pos=pos: players[pid]["pos"] == pos))
    return boards


# ---------------------------------------------------------------- black ink

# Points for leading the league in a completed season, after the Black Ink test used by the reference sites.
INK = {"pts": ("Points", 4), "war": ("WAR", 4), "paa": ("Points Above Average", 3), "ppg": ("Points per Start", 3), "pos": ("Position Points", 2), "best": ("Best Single Game", 1)}


def black_ink(seasons, players, careers, logs):
    """Seasons in which a player led the league, per category. Returns (ranked table, {pid: {season: [categories]}})."""
    leads = defaultdict(lambda: defaultdict(list))
    for season, s in seasons.items():
        if s["status"] != "complete":
            continue
        rows = defaultdict(lambda: {"gs": 0, "pts": 0.0, "war": 0.0, "paa": 0.0})
        for pid, career in careers.items():
            for c in career:
                if c["season"] == season and c["gs"]:
                    rows[pid]["gs"] += c["gs"]
                    for k in ("pts", "war", "paa"):
                        rows[pid][k] += c[k] or 0
        if not rows:
            continue
        floor = max(1, s["weeks_played"] // 2)
        top = lambda value, pool: max(pool, key=lambda pid: value(rows[pid]))
        everyone = list(rows)
        for cat in ("pts", "war", "paa"):
            leads[top(lambda r, cat=cat: r[cat], everyone)][season].append(cat)
        leads[top(lambda r: r["pts"] / r["gs"], [pid for pid in everyone if rows[pid]["gs"] >= floor])][season].append("ppg")
        for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
            pool = [pid for pid in everyone if players[pid]["pos"] == pos]
            if pool:
                leads[top(lambda r: r["pts"], pool)][season].append("pos")
        best = max(((a["pts"], pid) for pid, log in logs.items() for a in log if a["season"] == season and a["started"]), default=None)
        if best:
            leads[best[1]][season].append("best")
    table = []
    for pid, by_season in leads.items():
        counts = Counter(cat for cats in by_season.values() for cat in cats)
        table.append({"player": pid, "name": players[pid]["name"], "pos": players[pid]["pos"], "score": sum(INK[c][1] * n for c, n in counts.items()),
                      "counts": counts, "seasons": sorted(by_season)})
    table.sort(key=lambda r: (-r["score"], r["name"]))
    return {"weights": {k: {"label": label, "points": pts} for k, (label, pts) in INK.items()}, "table": table}, leads


# ---------------------------------------------------------------- places on the all-time lists

def list_ranks(players, careers, totals, logs, franchise_names):
    """Where each player stands on the career, position, franchise and single-season lists. Ties share a place."""
    out = defaultdict(list)

    def place(group, scope, stat, values, digits=2, link=None):
        ranked = sorted(values.items(), key=lambda kv: -kv[1])
        rank = 0
        for i, (pid, v) in enumerate(ranked):
            if i == 0 or v != ranked[i - 1][1]:
                rank = i + 1
            out[pid].append({"group": group, "scope": scope, "stat": stat, "value": round(v, digits), "digits": digits, "rank": rank, "of": len(ranked), **(link or {})})

    started = {pid: t for pid, t in totals.items() if t["gs"]}
    career_stats = (("Points", lambda t: t["pts"], 2), ("Games Started", lambda t: t["gs"], 0), ("WAR", lambda t: t["war"], 2), ("VOR", lambda t: t["vor"], 1),
                    ("PAA", lambda t: t["paa"], 1), ("Best Game", lambda t: t["best"], 2))
    for stat, value, digits in career_stats:
        place("Career", "League", stat, {pid: value(t) for pid, t in started.items()}, digits)
    place("Career", "League", f"Points per Start (min. {SIMILAR_MIN_STARTS} GS)", {pid: t["pts"] / t["gs"] for pid, t in started.items() if t["gs"] >= SIMILAR_MIN_STARTS})
    place("Career", "League", "Bench Points", {pid: t["bench_pts"] for pid, t in totals.items() if t["bench_pts"] > 0})
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        pool = {pid: t for pid, t in started.items() if players[pid]["pos"] == pos}
        for stat, value, digits in career_stats[:3]:
            place("Career", f"All {pos}s", stat, {pid: value(t) for pid, t in pool.items()}, digits)

    by_team = defaultdict(lambda: defaultdict(lambda: {"pts": 0.0, "gs": 0, "war": 0.0}))
    by_season = defaultdict(lambda: defaultdict(float))
    for pid, career in careers.items():
        for c in career:
            if c["gs"]:
                row = by_team[c["franchise"]][pid]
                row["pts"] += c["pts"]
                row["gs"] += c["gs"]
                row["war"] += c["war"] or 0
                by_season[c["season"]][pid] += c["pts"]
    for fid, rows in by_team.items():
        link = {"franchise": fid}
        scope = franchise_names[fid]
        place("Franchise", scope, "Points", {pid: r["pts"] for pid, r in rows.items()}, 2, link)
        place("Franchise", scope, "Games Started", {pid: r["gs"] for pid, r in rows.items()}, 0, link)
        place("Franchise", scope, "WAR", {pid: r["war"] for pid, r in rows.items()}, 2, link)
        for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
            place("Franchise", scope, f"Points, {pos}s", {pid: r["pts"] for pid, r in rows.items() if players[pid]["pos"] == pos}, 2, link)
    for season, rows in sorted(by_season.items()):
        link = {"season": season}
        place("Season", f"{season} League", "Points", dict(rows), 2, link)
        for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
            place("Season", f"{season} {pos}s", "Points", {pid: v for pid, v in rows.items() if players[pid]["pos"] == pos}, 2, link)
    all_seasons = {(pid, season): v for season, rows in by_season.items() for pid, v in rows.items()}
    ranked = sorted(all_seasons.items(), key=lambda kv: -kv[1])
    for i, ((pid, season), v) in enumerate(ranked):
        out[pid].append({"group": "Season", "scope": "All single seasons", "stat": f"Points, {season}", "value": round(v, 2), "digits": 2, "rank": i + 1, "of": len(ranked), "season": season})
    return out


# ---------------------------------------------------------------- similarity

def nearest(items, features):
    """items: {key: [numbers]}. Returns {key: [(score, other)]}, 1000 for identical, by distance in standard units."""
    keys = list(items)
    if len(keys) < 3:
        return {k: [] for k in keys}
    cols = list(zip(*(items[k] for k in keys)))
    means = [sum(c) / len(c) for c in cols]
    sds = [(sum((x - m) ** 2 for x in c) / len(c)) ** 0.5 or 1 for c, m in zip(cols, means)]
    z = {k: [(x - m) / s for x, m, s in zip(items[k], means, sds)] for k in keys}
    out = {}
    for k in keys:
        ranked = sorted((sum((a - b) ** 2 for a, b in zip(z[k], z[o])) ** 0.5, str(o), o) for o in keys if o != k)
        out[k] = [(max(0, round(1000 - 1000 * d / (2 * features ** 0.5))), o) for d, _, o in ranked[:6]]
    return out


def similar_players(players, totals):
    out = {}
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        pool = {pid: [t["gs"], t["pts"], t["pts"] / t["gs"], t["war"]] for pid, t in totals.items() if players[pid]["pos"] == pos and t["gs"] >= SIMILAR_MIN_STARTS}
        for pid, near in nearest(pool, 4).items():
            out[pid] = [{"player": o, "name": players[o]["name"], "score": score, "gs": totals[o]["gs"], "pts": totals[o]["pts"], "ppg": r2(totals[o]["pts"] / totals[o]["gs"]), "war": totals[o]["war"]} for score, o in near[:5]]
    return out


def similar_teams(seasons):
    pool, rows = {}, {}
    for season, s in seasons.items():
        played = [r for r in s["standings"] if r["w"] + r["l"] + r["t"] >= 4]
        if not played:
            continue
        avg = sum(r["ppg"] for r in played) / len(played)
        for r in played:
            g = r["w"] + r["l"] + r["t"]
            key = f"{season}-{r['franchise']}"
            rows[key] = r
            pool[key] = [r["pct"], r["ppg"] / avg, (r["pa"] / g) / avg, r["efficiency"], r["all_play_w"] / max(1, r["all_play_w"] + r["all_play_l"])]
    return {k: [{"season": o.split("-")[0], "franchise": rows[o]["franchise"], "team": rows[o]["team"], "score": score, "w": rows[o]["w"], "l": rows[o]["l"],
                 "ppg": rows[o]["ppg"], "result": rows[o].get("result")} for score, o in near[:5]] for k, near in nearest(pool, 5).items()}


# ---------------------------------------------------------------- team seasons

def team_season(season, s, fid, games, players, careers, draft, trades, similar, waiver_log):
    row = next(r for r in s["standings"] if r["franchise"] == fid)
    mine = [g for g in games if g["season"] == season and any(t["franchise"] == fid for t in g["teams"])]
    schedule, w, l, managers, names = [], 0, 0, [], []
    slots = defaultdict(Counter)
    slot_points = defaultdict(float)
    width = Counter()
    for g in mine:
        me = next(t for t in g["teams"] if t["franchise"] == fid)
        them = next(t for t in g["teams"] if t["franchise"] != fid)
        won = g["winner"] == fid
        if g["kind"] == "regular":
            w, l = w + won, l + (not won)
        managers += [m for m in me["managers"] if m not in managers]
        names += [me["team"]] if me["team"] not in names else []
        top = max(me["starters"], key=lambda x: x["pts"]) if me["starters"] else None
        schedule.append({"id": g["id"], "week": g["week"], "date": g["date"], "kind": g["kind"], "label": g["label"], "opp": them["franchise"], "opp_team": them["team"],
                         "pts": me["points"], "opp_pts": them["points"], "won": won, "record": f"{w}-{l}" if g["kind"] == "regular" else None,
                         "max": me.get("max_points"), "top": top and {"player": top["player"], "pts": top["pts"]}})
        seen = Counter(x["slot"] for x in me["starters"])
        for slot, n in seen.items():
            width[slot] = max(width[slot], n)
        for x in me["starters"]:
            slots[x["slot"]][x["player"]] += 1
            slot_points[(x["slot"], x["player"])] += x["pts"]
    depth = [{"slot": slot, "starters": width[slot],
              "players": [{"player": pid, "gs": n, "pts": r2(slot_points[(slot, pid)])} for pid, n in sorted(counts.items(), key=lambda kv: (-kv[1], -slot_points[(slot, kv[0])]))]}
             for slot, counts in slots.items()]
    order = ["QB", "RB", "WR", "TE", "FLEX", "SUPER_FLEX", "K", "DEF"]
    depth.sort(key=lambda d: order.index(d["slot"]) if d["slot"] in order else 99)
    roster = sorted(({"player": pid, "pos": players[pid]["pos"], "nfl": c.get("nfl"), **{k: c[k] for k in ("g", "gs", "pts", "ppg", "best", "bench_pts", "wins", "paa", "vor", "war")}}
                     for pid, career in careers.items() for c in career if c["season"] == season and c["franchise"] == fid), key=lambda r: (-r["pts"], -r["g"]))
    standings = s["standings"]
    rank = lambda key, reverse=True: sorted(standings, key=lambda r: r[key] or 0, reverse=reverse).index(row) + 1   # before week 1 some figures are blank
    return {
        "season": season, "franchise": fid, "team": row["team"], "aka": names, "managers": managers, "row": row, "status": s["status"],
        "ranks": {"pf": rank("pf"), "pa": rank("pa", False), "max_pf": rank("max_pf"), "efficiency": rank("efficiency"), "luck": rank("luck")},
        "schedule": schedule, "roster": roster, "depth": depth,
        "draft": [p for p in (draft or {}).get("picks", []) if p["franchise"] == fid], "draft_kind": (draft or {}).get("kind"),
        "trades": [{"id": t["id"], "date": t["date"], "with": [{"franchise": x["franchise"], "team": x["team"]} for x in t["sides"] if x["franchise"] != fid],
                    "got": len(next(x for x in t["sides"] if x["franchise"] == fid)["assets"])}
                   for t in trades if t["status"] == "complete" and t["season"] == season and any(x["franchise"] == fid for x in t["sides"])],
        "similar": similar.get(f"{season}-{fid}", []),
        "pickups": [p for p in waiver_log["pickups"] if p["season"] == season and p["franchise"] == fid],
        "drops": [d for d in waiver_log["drops"] if d["season"] == season and d["franchise"] == fid],
        "players": {pid: players[pid]["name"] for pid in sorted({r["player"] for r in roster} | {x["player"] for d in depth for x in d["players"]}
                                                                 | {p["player"] for p in (draft or {}).get("picks", []) if p["franchise"] == fid})},
    }


# ---------------------------------------------------------------- draft pick ledger

FUTURE_SEASONS = 3   # Sleeper lets picks be traded this many drafts ahead
DRAFT_ROUNDS = 4


def pick_ledger(trades, league, raw_traded):
    """Who holds every future pick and the path each traded pick has taken."""
    chains = defaultdict(list)
    for t in sorted((t for t in trades if t["status"] == "complete"), key=lambda t: (t["date"], t["id"])):
        team = {side["franchise"]: side["team"] for side in t["sides"]}
        for side in t["sides"]:
            for a in side["assets"]:
                if a["type"] == "pick":
                    chains[(a["season"], a["round"], a["original"])].append({
                        "date": t["date"], "trade": t["id"], "from": a["from"], "from_team": team.get(a["from"]), "to": side["franchise"], "to_team": side["team"],
                        "became": a.get("became")})
    current = int(league["current_season"])
    drafted = {d["season"] for d in league["drafts"]}
    upcoming = [str(y) for y in range(current, current + FUTURE_SEASONS + 1) if str(y) not in drafted]
    future = []
    for season in upcoming:
        for rnd in range(1, DRAFT_ROUNDS + 1):
            for f in league["franchises"]:
                moves = chains.get((season, rnd, f["id"]), [])
                future.append({"season": season, "round": rnd, "original": f["id"], "holder": moves[-1]["to"] if moves else f["id"], "moves": moves})
    # Sleeper's own list of traded picks is the check on the chains built from trades.
    held = {(p["season"], p["round"], p["roster_id"]): p["owner_id"] for p in raw_traded}
    for pick in future:
        sleeper = held.get((pick["season"], pick["round"], pick["original"]), pick["original"])
        if sleeper != pick["holder"]:
            print(f"pick ledger: {pick['season']} round {pick['round']} (franchise {pick['original']}) held by {pick['holder']} in trades, {sleeper} on Sleeper")
            pick["sleeper_holder"] = sleeper
    swaps = [{"trade": t["id"], "date": t["date"], **t["conditional_swap"]} for t in trades if t.get("conditional_swap")]
    travelled = sorted(({"season": season, "round": rnd, "original": original, "moves": moves, "became": moves[-1]["became"]}
                        for (season, rnd, original), moves in chains.items() if len(moves) >= 2), key=lambda p: (-len(p["moves"]), p["season"], p["round"]))
    counts = defaultdict(lambda: {"acquired": 0, "sent": 0})
    for moves in chains.values():
        for m in moves:
            counts[m["to"]]["acquired"] += 1
            if m["from"]:
                counts[m["from"]]["sent"] += 1
    # "chains" feeds the per-pick pages and is not written to the ledger file.
    return {"seasons": upcoming, "rounds": DRAFT_ROUNDS, "future": future, "swaps": swaps, "travelled": travelled[:40],
            "activity": [{"franchise": f["id"], **counts[f["id"]]} for f in league["franchises"]], "chains": chains}


def pick_search(league, picks, players):
    """Every pick with a page, drafted or still to come, for the search box. Searched word by word, so "2027 1st mojo" finds a pick."""
    ordinal = lambda n: f"{n}{'th' if 10 < n % 100 < 14 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
    names = {f["id"]: [f["name"]] + [s["teams"][str(f["id"])] for s in league["seasons"] if str(f["id"]) in s["teams"]] for f in league["franchises"]}
    also = lambda fid: " | ".join(dict.fromkeys(names.get(fid, [])))
    rows = []
    for d in league["drafts"]:
        for x in load(SITE / "drafts" / f"{d['season']}.json")["picks"]:
            rows.append({"name": f"{d['season']} Pick {x['label']}", "season": d["season"], "label": x["label"], "player": players.get(x["player"], {}).get("name"),
                         "franchise": x["franchise"], "team": x["team"],
                         "also": f"round {x['round']} pick {x['pick']} | {ordinal(x['round'])} round | {x['overall']} overall | {also(x['original'])}"})
    for x in picks["future"]:
        rows.append({"name": f"{x['season']} {ordinal(x['round'])}-Round Pick ({names[x['original']][0]})", "season": x["season"], "round": x["round"], "original": x["original"],
                     "franchise": x["holder"], "also": f"round {x['round']} | {also(x['original'])}"})
    return rows


# ---------------------------------------------------------------- all-time teams

LINEUP = ("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "K", "DEF")
FLEX_POS = {"FLEX": ("RB", "WR", "TE"), "SUPER_FLEX": ("QB", "RB", "WR", "TE")}


def all_time_teams(players, careers, league):
    def lineup(rows, taken):
        team = []
        for slot in LINEUP:
            pool = [pid for pid in rows if pid not in taken and players[pid]["pos"] in FLEX_POS.get(slot, (slot,))]
            if not pool:
                continue
            pid = max(pool, key=lambda pid: rows[pid]["pts"])
            taken.add(pid)
            r = rows[pid]
            team.append({"slot": slot, "player": pid, "name": players[pid]["name"], "pos": players[pid]["pos"], "gs": r["gs"], "pts": r2(r["pts"]), "war": r2(r["war"]),
                         "years": f"{min(r['seasons'])}-{max(r['seasons'])}" if len(r["seasons"]) > 1 else min(r["seasons"]), "franchises": r["franchises"]})
        return team

    def collect(keep):
        rows = defaultdict(lambda: {"pts": 0.0, "gs": 0, "war": 0.0, "seasons": set(), "franchises": []})   # franchises in the order he played for them
        for pid, career in careers.items():
            for c in career:
                if c["gs"] and keep(c):
                    r = rows[pid]
                    r["pts"] += c["pts"]
                    r["gs"] += c["gs"]
                    r["war"] += c["war"] or 0
                    r["seasons"].add(c["season"])
                    if c["franchise"] not in r["franchises"]:
                        r["franchises"].append(c["franchise"])
        return rows
    everyone = collect(lambda c: True)
    taken = set()
    return {"league": [lineup(everyone, taken), lineup(everyone, taken)],
            "franchises": {f["id"]: lineup(collect(lambda c, fid=f["id"]: c["franchise"] == fid), set()) for f in league["franchises"]}}


# ---------------------------------------------------------------- player journeys

def journeys(players, files):
    travelled, traded, moved, revenge, careers_vs, returns, loyal = [], [], [], [], [], [], []
    for pid, f in files.items():
        info = {"player": pid, "name": f["name"], "pos": f["pos"]}
        log, history = f["log"], f["history"]
        # Kickers and defenses are streamed week to week, so they would swamp every list of comings and goings.
        skill = f["pos"] not in ("K", "DEF")
        teams = list(dict.fromkeys((a["franchise"], a["team"]) for a in log))
        order = []
        for fid, team in teams:
            order = [x for x in order if x["franchise"] != fid] + [{"franchise": fid, "team": team}]
        stops = [a["franchise"] for i, a in enumerate(log) if i == 0 or log[i - 1]["franchise"] != a["franchise"]]
        if skill and len(order) >= 3:
            travelled.append({**info, "n": len(order), "teams": order, "stints": len(stops), "gs": f["totals"]["gs"], "pts": f["totals"]["pts"]})
        trades = [e for e in history if e["type"] == "traded"]
        if len(trades) >= 2:
            traded.append({**info, "n": len(trades), "first": trades[0]["date"], "last": trades[-1]["date"], "teams": len(order), "pts": f["totals"]["pts"]})
        if skill and len(history) >= 6:
            kinds = Counter(e["type"] for e in history)
            moved.append({**info, "n": len(history), "signed": kinds["signed"] + kinds["claimed"], "released": kinds["released"], "traded": kinds["traded"], "teams": len(order)})
        # A return: he rejoins a franchise he had left.
        seen = []
        for fid in stops:
            if skill and fid in seen and seen[-1] != fid:
                returns.append({**info, "franchise": fid, "team": next(t for x, t in reversed(teams) if x == fid), "stints": stops.count(fid)})
            seen.append(fid)
        # A revenge game: a start against a franchise that traded or released him.
        left = [(e["date"], e["from"], e["type"]) for e in history if e.get("from") and e["type"] in ("traded", "released")]
        mine = []
        for a in log:
            if not (skill and a["started"] and a["game"]):
                continue
            how = next((kind for day, fid, kind in reversed(left) if fid == a["opp"] and day <= a["date"]), None)
            if how and a["franchise"] != a["opp"]:
                row = {**info, "game": a["game"], "season": a["season"], "week": a["week"], "pts": a["pts"], "proj": a["proj"], "won": a["won"],
                       "franchise": a["franchise"], "team": a["team"], "opp": a["opp"], "opp_team": a["opp_team"], "how": how}
                revenge.append(row)
                mine.append(row)
        if len(mine) >= 3:
            starts = f["totals"]["gs"]
            careers_vs.append({**info, "g": len(mine), "pts": r2(sum(r["pts"] for r in mine)), "ppg": r2(sum(r["pts"] for r in mine) / len(mine)),
                               "usual": f["totals"]["ppg"], "w": sum(r["won"] for r in mine), "l": sum(not r["won"] for r in mine)})
        if len(order) == 1 and f["totals"]["gs"] >= 10:
            loyal.append({**info, "franchise": order[0]["franchise"], "team": order[0]["team"], "g": f["totals"]["g"], "gs": f["totals"]["gs"], "pts": f["totals"]["pts"],
                          "seasons": len({a["season"] for a in log}), "active": log[-1]["season"] == max(a["season"] for x in files.values() for a in x["log"][-1:])})
    unique = {(r["player"], r["franchise"]): r for r in returns}
    return {
        "travelled": sorted(travelled, key=lambda r: (-r["n"], -r["stints"], -r["pts"]))[:30],
        "traded": sorted(traded, key=lambda r: (-r["n"], -r["pts"]))[:30],
        "moved": sorted(moved, key=lambda r: (-r["n"], r["name"]))[:30],
        "revenge_best": sorted(revenge, key=lambda r: -r["pts"])[:30],
        "revenge_worst": sorted(revenge, key=lambda r: r["pts"])[:15],
        "revenge_careers": sorted(careers_vs, key=lambda r: -(r["ppg"] - (r["usual"] or 0)))[:30],
        "revenge_games": len(revenge), "revenge_wins": sum(r["won"] for r in revenge),
        "returns": sorted(unique.values(), key=lambda r: (-r["stints"], r["name"])),
        "loyal": sorted(loyal, key=lambda r: (-r["gs"], -r["pts"]))[:30],
    }


# ---------------------------------------------------------------- aging curves and rookie classes

AGE_MIN_STARTS = 10   # starts at an age and position before the average is shown


def aging(players, files):
    from datetime import date
    cells = defaultdict(lambda: {"gs": 0, "pts": 0.0, "vor": 0.0, "players": set()})
    seasons = defaultdict(lambda: {"gs": 0, "pts": 0.0})
    for pid, f in files.items():
        if not f.get("birth_date") or f["pos"] == "DEF":
            continue
        born = date.fromisoformat(f["birth_date"])
        for a in f["log"]:
            if not a["started"]:
                continue
            day = date.fromisoformat(a["date"])
            age = day.year - born.year - ((day.month, day.day) < (born.month, born.day))
            c = cells[(f["pos"], age)]
            c["gs"] += 1
            c["pts"] += a["pts"]
            c["vor"] += a.get("vor") or 0
            c["players"].add(pid)
            # Age for a whole season is his age on 1 September.
            season_age = int(a["season"]) - born.year - ((9, 1) < (born.month, born.day))
            row = seasons[(pid, a["season"], season_age)]
            row["gs"] += 1
            row["pts"] += a["pts"]
    ages = sorted({age for (_, age), c in cells.items() if c["gs"] >= AGE_MIN_STARTS})
    grid = [{"age": age, **{pos: ({"gs": c["gs"], "ppg": r2(c["pts"] / c["gs"]), "vor": r2(c["vor"] / c["gs"]), "players": len(c["players"])}
                                  if (c := cells.get((pos, age))) and c["gs"] >= AGE_MIN_STARTS else None) for pos in ("QB", "RB", "WR", "TE", "K")}} for age in ages]
    best = {}
    for (pid, season, age), row in seasons.items():
        if age not in best or row["pts"] > best[age]["pts"]:
            best[age] = {"age": age, "player": pid, "name": players[pid]["name"], "pos": players[pid]["pos"], "season": season, "gs": row["gs"], "pts": r2(row["pts"])}
    classes = defaultdict(list)
    for pid, f in files.items():
        if f.get("rookie_year") and f["totals"]["gs"]:
            classes[f["rookie_year"]].append(pid)
    rookie = []
    for year, pids in classes.items():
        ranked = sorted(pids, key=lambda pid: -files[pid]["totals"]["war"])
        rookie.append({"year": year, "players": len(pids), "gs": sum(files[p]["totals"]["gs"] for p in pids), "pts": r2(sum(files[p]["totals"]["pts"] for p in pids)),
                       "war": r2(sum(files[p]["totals"]["war"] for p in pids)),
                       "top": [{"player": p, "name": players[p]["name"], "pos": players[p]["pos"], "war": files[p]["totals"]["war"], "pts": files[p]["totals"]["pts"]} for p in ranked[:5]]})
    return {"min_starts": AGE_MIN_STARTS, "grid": grid, "best_by_age": [best[a] for a in sorted(best)], "classes": sorted(rookie, key=lambda r: -r["year"])}


# ---------------------------------------------------------------- hall of fame monitor

# Points toward the Hall of Fame Monitor. A first pass, to be tuned with the league.
# A title is worth "champion" to a player who started the championship game, "champion_regular" to one on the title roster who
# started at least half the team's games that season but not the final, and nothing to a bench player.
HOF_WEIGHTS = {"war": 10, "start": 0.25, "black_ink": 1, "mvp": 15, "first_team": 6, "second_team": 3, "champion": 4, "champion_regular": 2, "young_sexy": 2, "rookie_of_year": 1, "championship_mvp": 3}
HOF_BENCHMARK = 100


def title_roles(games):
    """{player: Counter of "champion" / "champion_regular" / "champion_bench"} across every title team."""
    roles = defaultdict(Counter)
    for final in (g for g in games if g["label"] == "Dynasty Championship"):
        side = next(t for t in final["teams"] if t["franchise"] == final["winner"])
        season_games = [g for g in games if g["season"] == final["season"] and any(t["franchise"] == side["franchise"] for t in g["teams"])]
        started = Counter(x["player"] for g in season_games for t in g["teams"] if t["franchise"] == side["franchise"] for x in t["starters"])
        for x in side["starters"]:
            roles[x["player"]]["champion"] += 1
        for x in side["bench"]:
            roles[x["player"]]["champion_regular" if started[x["player"]] * 2 >= len(season_games) else "champion_bench"] += 1
    return roles


def hof_monitor(players, files, ink, games):
    ink_score = {r["player"]: r["score"] for r in ink["table"]}
    roles = title_roles(games)
    rows = []
    for pid, f in files.items():
        t = f["totals"]
        if not t["gs"]:
            continue
        honors = Counter()
        for text in f["honors"]:
            what = text.split(" ", 1)[1] if text[:4].isdigit() else text
            key = ("championship_mvp" if what == "Championship MVP" else "mvp" if "MVP" in what else "first_team" if "First Team" in what else "second_team" if "Second Team" in what
                   else "young_sexy" if what.startswith("Young Sexy") else "rookie_of_year" if what == "Rookie of the Year" else None)
            if key:
                honors[key] += 1
        honors.update(roles.get(pid, {}))
        parts = {"war": max(0, t["war"]) * HOF_WEIGHTS["war"], "starts": t["gs"] * HOF_WEIGHTS["start"], "black_ink": ink_score.get(pid, 0) * HOF_WEIGHTS["black_ink"],
                 "awards": sum(n * HOF_WEIGHTS.get(k, 0) for k, n in honors.items())}
        rows.append({"player": pid, "name": f["name"], "pos": f["pos"], "score": round(sum(parts.values()), 1), **{k: round(v, 1) for k, v in parts.items()},
                     "gs": t["gs"], "pts": t["pts"], "career_war": t["war"], "honors": dict(honors), "seasons": len({a["season"] for a in f["log"] if a["started"]})})
    rows.sort(key=lambda r: -r["score"])
    return {"weights": HOF_WEIGHTS, "benchmark": HOF_BENCHMARK, "candidates": rows[:60]}



# ---------------------------------------------------------------- best stretches

STRETCH_SPANS = range(2, 11)


def best_windows(series, top=10):
    """series: {key: [runs]}, each run a list of (points, entry) in consecutive weeks.

    For each span length, the highest-scoring windows, never two overlapping windows for the same key.
    """
    out = {}
    for n in STRETCH_SPANS:
        found = []
        for key, runs in series.items():
            for r, run in enumerate(runs):
                for i in range(len(run) - n + 1):
                    found.append((sum(pts for pts, _ in run[i:i + n]), str(key), r, i, key))
        found.sort(key=lambda w: (-w[0], w[1], w[2], w[3]))
        kept, used = [], defaultdict(list)
        for pts, _, r, i, key in found:
            if any(r == r2_ and i < j + n and j < i + n for r2_, j in used[key]):
                continue
            used[key].append((r, i))
            kept.append((key, r2(pts), [entry for _, entry in series[key][r][i:i + n]]))
            if len(kept) == top:
                break
        out[n] = kept
    return out


def stretches(games, players, logs):
    """Most points over 2 to 10 consecutive weeks, within one season, for franchises, managers and players."""
    sides = defaultdict(lambda: defaultdict(list))   # kind -> (key, season) -> games in order
    for g in games:
        for side in g["teams"]:
            entry = (side["points"], {"game": g["id"], "season": g["season"], "week": g["week"], "team": side["team"], "franchise": side["franchise"]})
            sides["franchises"][(side["franchise"], g["season"])].append(entry)
            for slug in side["managers"]:
                sides["managers"][(slug, g["season"])].append(entry)
    out = {"spans": list(STRETCH_SPANS)}
    for kind, by_season in sides.items():
        series = defaultdict(list)
        for (key, _season), run in by_season.items():
            series[key].append(run)
        out[kind] = {n: [{"key": key, "season": seq[0]["season"], "from": seq[0], "to": seq[-1], "pts": pts, "avg": r2(pts / n),
                          "teams": list(dict.fromkeys(e["team"] for e in seq)), "franchise": seq[-1]["franchise"]} for key, pts, seq in rows]
                     for n, rows in best_windows(series).items()}
    series = defaultdict(list)
    for pid, run in start_runs(logs):
        season_runs = defaultdict(list)
        for a in run:
            season_runs[a["season"]].append((a["pts"], {"game": a["game"], "season": a["season"], "week": a["week"], "team": a["team"], "franchise": a["franchise"]}))
        series[pid].extend(season_runs.values())
    out["players"] = {n: [{"player": pid, "name": players[pid]["name"], "pos": players[pid]["pos"], "season": seq[0]["season"], "from": seq[0], "to": seq[-1],
                           "pts": pts, "avg": r2(pts / n), "teams": list(dict.fromkeys(e["team"] for e in seq)), "franchise": seq[-1]["franchise"]} for pid, pts, seq in rows]
                      for n, rows in best_windows(series).items()}
    return out


def start_runs(logs):
    """Every unbroken run of starts, as (player, [appearances]). An NFL bye is skipped; a benched or unrostered week ends the run."""
    byes = {(path.stem, int(week)): set(entry.get("bye", [])) for path in (ROOT / "data" / "nfl").glob("*.json") for week, entry in load(path)["weeks"].items()}
    calendar = sorted({(a["season"], a["week"]) for log in logs.values() for a in log if a["game"]})
    for pid, log in logs.items():
        at = {(a["season"], a["week"]): a for a in log}
        began = next(((a["season"], a["week"]) for a in log if a["game"] and a["started"]), None)
        if not began:
            continue
        run = []
        for when in calendar[calendar.index(began):]:
            a = at.get(when)
            if a and a["game"] and a["started"]:
                run.append(a)
            elif pid in byes.get(when, ()) or (a and not a["game"]):
                continue
            elif run:
                yield pid, run
                run = []
        if run:
            yield pid, run


# ---------------------------------------------------------------- player status, manager lists, franchise moves

def player_status(players, league):
    """Where a player stands today: on an iooi roster, a free agent, or out of the NFL."""
    season = league["current_season"]
    raw = load(ROOT / "data" / "raw" / "players_nfl.json")
    held = {pid: r["roster_id"] for r in load(ROOT / "data" / "raw" / season / "rosters.json") for pid in (r.get("players") or [])}
    last_played = {}
    for path in sorted((ROOT / "data" / "nfl").glob("*.json")):
        for week in load(path)["weeks"].values():
            for pid in week["points"]:
                last_played[pid] = path.stem
    out = {}
    for pid, p in players.items():
        info = raw.get(pid) or {}
        if pid in held:
            out[pid] = {"code": "rostered", "franchise": held[pid]}
        elif p["pos"] == "DEF" or info.get("team"):
            out[pid] = {"code": "free_agent"}
        else:
            out[pid] = {"code": "out", "last_nfl": last_played.get(pid)}
    return out


def manager_lists(manager, players, logs):
    """A manager's leaders and all-time team: starts made for a franchise while he held a stake in it."""
    held = lambda a: any(st["franchise"] == a["franchise"] and st["from"] <= a["date"] and (st["to"] is None or a["date"] < st["to"]) for st in manager["stints"])
    rows = []
    for pid, log in logs.items():
        mine = [a for a in log if a["date"] and held(a)]
        starts = [a for a in mine if a["started"]]
        if starts:
            seasons = sorted({a["season"] for a in starts})
            rows.append({"player": pid, "name": players[pid]["name"], "pos": players[pid]["pos"], "years": seasons[0] if len(seasons) == 1 else f"{seasons[0]}-{seasons[-1]}",
                         "g": len(mine), "gs": len(starts), "pts": r2(sum(a["pts"] for a in starts)), "best": max(a["pts"] for a in starts),
                         "wins": sum(1 for a in starts if a["won"]), "vor": r2(sum(a.get("vor") or 0 for a in starts)), "war": r2(sum(a.get("war") or 0 for a in starts))})
    rows.sort(key=lambda r: -r["pts"])
    team, taken = [], set()
    for slot in LINEUP:
        choice = next((r for r in rows if r["player"] not in taken and r["pos"] in FLEX_POS.get(slot, (slot,))), None)
        if choice:
            taken.add(choice["player"])
            team.append({"slot": slot, **choice})
    best = sorted(({"player": pid, "name": players[pid]["name"], "pos": players[pid]["pos"], "pts": a["pts"], "game": a["game"], "season": a["season"], "week": a["week"],
                    "franchise": a["franchise"], "team": a["team"]} for pid, log in logs.items() for a in log if a["started"] and a["date"] and held(a)), key=lambda r: -r["pts"])[:10]
    return {"leaders": {"overall": rows[:20], "by_pos": {pos: [r for r in rows if r["pos"] == pos][:3] for pos in ("QB", "RB", "WR", "TE", "K", "DEF")}},
            "all_time_team": team, "best_games": best}


def franchise_moves(fid, league, trades, waivers):
    """Trades and waiver-wire activity for one franchise, season by season."""
    rows = []
    for s in league["seasons"]:
        season = s["season"]
        w = next((r for r in waivers["by_season"].get(season, []) if r["franchise"] == fid), None) or {"claims": 0, "signings": 0, "adds": 0, "releases": 0, "gs": 0, "pts": 0.0}
        rows.append({"season": season, "trades": sum(1 for t in trades if t["status"] == "complete" and t["season"] == season and any(int(side["franchise"]) == fid for side in t["sides"])),
                     **{k: w[k] for k in ("claims", "signings", "adds", "releases", "gs", "pts")}})
    return rows

# ---------------------------------------------------------------- home page

HOME_FACES = 12
LOTTERY_ODDS = (30.0, 25.0, 20.0, 15.0, 7.0, 3.0)   # the six non-playoff teams, worst first (2nd Amendment)


LIST_MOVES = {"league": 10, "franchise": 3, "manager": 3}   # places on a career points list worth a front-page mention


def list_moves(season, week, logs, stints):
    """Players who climbed a career points list (league, franchise, manager) in the given week, within LIST_MOVES places."""
    before, after = defaultdict(lambda: defaultdict(float)), defaultdict(lambda: defaultdict(float))
    held = lambda st, a: any(x["franchise"] == a["franchise"] and x["from"] <= a["date"] and (x["to"] is None or a["date"] < x["to"]) for x in st)
    for pid, log in logs.items():
        for a in log:
            if not a["started"] or (a["season"], a["week"]) > (season, week):
                continue
            keys = [("league", None), ("franchise", a["franchise"])] + [("manager", m) for m, st in stints.items() if a["date"] and held(st, a)]
            for key in keys:
                after[key][pid] += a["pts"]
                if (a["season"], a["week"]) < (season, week):
                    before[key][pid] += a["pts"]
    moves = []
    for key in sorted(after, key=lambda k: (list(LIST_MOVES).index(k[0]), str(k[1]))):
        old = {pid: i + 1 for i, pid in enumerate(sorted(before[key], key=lambda q: (-before[key][q], q)))}
        order = sorted(after[key], key=lambda q: (-after[key][q], q))
        for i, pid in enumerate(order[:LIST_MOVES[key[0]]]):
            was = old.get(pid)
            passed = [q for q in order[i + 1:] if q in old and (was is None or old[q] < was)]
            if passed:   # a list with no history yet (the first week, a new manager) is not climbed
                moves.append({"list": key[0], "key": key[1], "player": pid, "from": was, "to": i + 1, "passed": passed})
    # A manager's list is mentioned only where it tells something the franchise list does not.
    seen = {(m["player"], m["from"], m["to"], m["key"]) for m in moves if m["list"] == "franchise"}
    moves = [m for m in moves if m["list"] != "manager" or not any((m["player"], m["from"], m["to"], st["franchise"]) in seen for st in stints[m["key"]])]
    return moves, {key for key, rows in before.items() if rows}


def last_week_honors(games, players, records, reached, logs, stints):
    """Accolades from the most recent completed week, as lines for the front page."""
    if not games:
        return None
    season, week = games[-1]["season"], games[-1]["week"]
    mine = [g for g in games if g["season"] == season and g["week"] == week]
    ordinal = lambda n: f"{n}{'th' if 10 < n % 100 < 14 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
    f = lambda side: f"[[f:{side['franchise']}|{side['team']}]]"
    p = lambda pid: f"[[p:{pid}|{players[pid]['name']}]]"
    box = lambda g: f"([[g:{g['id']}]])"
    box_of = lambda r: f"([[g:{r['game']}]])"

    def place(rows, match, what):
        """Where a performance stands on an all-time list, if it made the list."""
        at = next((i + 1 for i, r in enumerate(rows) if match(r)), None)
        return "" if at is None else f", the highest {what} in league history" if at == 1 else f", the {ordinal(at)}-highest {what} in league history"

    sides = [(side, g) for g in mine for side in g["teams"]]
    lines = []
    top, g = max(sides, key=lambda x: x[0]["points"])
    lines.append({"label": "Team of the Week", "text": f"{f(top)} scored {top['points']:.2f}" + place(records["team_high"][:25], lambda r: r["game"] == g["id"] and r["franchise"] == top["franchise"], "score") + f" {box(g)}."})
    starts = [(x, side, g) for side, g in sides for x in side["starters"] if x["player"] in players]
    if starts:
        x, side, g = max(starts, key=lambda t: t[0]["pts"])
        pos = players[x["player"]]["pos"]
        rank = (place(records["player_high"][:25], lambda r: r["game"] == g["id"] and r["player"] == x["player"], "game")
                or place(records["player_high_by_pos"].get(pos, [])[:10], lambda r: r["game"] == g["id"] and r["player"] == x["player"], f"game by a {pos}"))
        lines.append({"label": "Player of the Week", "text": f"{p(x['player'])} put up {x['pts']:.2f} for {f(side)}{rank} {box(g)}."})
    margin = lambda g: abs(g["teams"][0]["points"] - g["teams"][1]["points"])
    order = lambda g: sorted(g["teams"], key=lambda t: -t["points"])
    if len(mine) > 1:
        g = min(mine, key=margin)
        w, l = order(g)
        lines.append({"label": "Closest Game", "text": f"{f(w)} edged {f(l)} {w['points']:.2f}-{l['points']:.2f}, a margin of {margin(g):.2f} {box(g)}."})
        g = max(mine, key=margin)
        w, l = order(g)
        lines.append({"label": "Biggest Blowout", "text": f"{f(w)} beat {f(l)} {w['points']:.2f}-{l['points']:.2f}, a margin of {margin(g):.2f} {box(g)}."})
    low, g = min(sides, key=lambda x: x[0]["points"])
    worst = place(records["team_low"][:25], lambda r: r["game"] == g["id"] and r["franchise"] == low["franchise"], "score").replace("highest", "lowest")
    lines.append({"label": "Low Score", "text": f"{f(low)} managed {low['points']:.2f}{worst} {box(g)}."})
    bench = [(x, side, g) for side, g in sides for x in side["bench"] if x["player"] in players]
    if bench:
        x, side, g = max(bench, key=lambda t: t[0]["pts"])
        lines.append({"label": "Best on a Bench", "text": f"{p(x['player'])} scored {x['pts']:.2f} on the bench for {f(side)} {box(g)}."})
    left, g = max(sides, key=lambda x: (x[0].get("max_points") or x[0]["points"]) - x[0]["points"])
    gap = (left.get("max_points") or left["points"]) - left["points"]
    if gap > 0:
        lines.append({"label": "Most Left on the Bench", "text": f"{f(left)} left {gap:.2f} points out of the lineup {box(g)}."})
    # Records set: one clause per performance, naming every list it now tops.
    moves, history = list_moves(season, week, logs, stints)
    in_week = lambda r: r and r["season"] == season and r["week"] == week
    tops = {}
    def top(r, what):
        if in_week(r):
            tops.setdefault((r["player"], r["game"]), {"r": r, "what": []})["what"].append(what)
    top((records["player_high"] or [None])[0], "the league record for a game")
    for pos, rows in records["player_high_by_pos"].items():
        if rows and (rows[0]["player"], rows[0]["game"]) not in tops:
            top(rows[0], f"the record for a {pos}")
    top((records["player_high_bench"] or [None])[0], "the record on a bench")
    for r in records["franchise_player_best"]:
        if ("franchise", r["key"]) in history:
            top(r, f"the [[f:{r['key']}|{r['team']}]] record")
    for r in records["manager_player_best"]:   # a manager's first week sets no record worth the name
        if in_week(r) and ("manager", r["key"]) in history and not any(w.startswith("the [[f:") for w in tops.get((r["player"], r["game"]), {"what": []})["what"]):
            top(r, f"[[m:{r['key']}]]'s record")
    joined = lambda items: items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
    if tops:
        lines.append({"label": "Records", "text": "; ".join(f"{p(t['r']['player'])} set {joined(t['what'])} with {t['r']['points']:.2f} {box_of(t['r'])}" for t in tops.values()) + "."})
    # Climbing the career points lists: the league's top places, then the top of each franchise's and manager's list.
    names = lambda pids: joined([p(q) for q in pids[:3]] + ([f"{len(pids) - 3} other{'s' if len(pids) > 4 else ''}"] if len(pids) > 3 else []))
    passing = lambda m: f", passing {names(m['passed'])}" if m["passed"] else ""
    league_moves = [m for m in moves if m["list"] == "league"]
    if league_moves:
        lines.append({"label": "Career Points List", "text": "; ".join(
            f"{p(m['player'])} {'became the career points leader' if m['to'] == 1 else 'climbed to ' + ordinal(m['to'])}{passing(m)}" for m in league_moves) + "."})
    team_name = {side["franchise"]: side["team"] for side, _ in sides}
    where = lambda m: f"[[f:{m['key']}|{team_name.get(m['key']) or m['key']}]]'s" if m["list"] == "franchise" else f"[[m:{m['key']}]]'s"
    other = sorted((m for m in moves if m["list"] != "league"), key=lambda m: m["to"])
    if other:
        text = "; ".join(f"{p(m['player'])} became {where(m)} career points leader{passing(m)}" if m["to"] == 1 else
                         f"{p(m['player'])} climbed to {ordinal(m['to'])} on {where(m)} list{passing(m)}" for m in other[:6])
        lines.append({"label": "Franchise and Manager Lists", "text": text + (f"; and {len(other) - 6} more moves near the top." if len(other) > 6 else ".")})
    who = lambda m: p(m["key"]) if m["kind"] == "player" else f"[[m:{m['key']}]]" if m["kind"] == "manager" else f"[[f:{m['key']}|{m['team']}]]"
    marks = [m for m in reached if m["season"] == season and m["week"] == week]
    marks.sort(key=lambda m: ({"franchise": 0, "manager": 1, "player": 2}[m["kind"]], -int(m["what"].split(" ")[0].replace(",", ""))))
    if marks:
        lines.append({"label": "Milestones", "text": "; ".join(f"{who(m)} reached {m['what']}" for m in marks[:8]) + "."})
    return {"season": season, "week": week, "kind": mine[0]["kind"], "lines": lines}


def home(league, seasons, players, careers, picks, honors):
    """The front page's top panels: the season's leading scorers, the standings, and the lottery as it stands."""
    played = [s for s in league["seasons"] if seasons[s["season"]]["games"]]
    season = played[-1]["season"]
    s = seasons[season]
    points = defaultdict(lambda: {"pts": 0.0, "gs": 0})
    for pid, career in careers.items():
        for row in career:
            if row["season"] == season and row["gs"]:
                points[pid]["pts"] += row["pts"]
                points[pid]["gs"] += row["gs"]
    scorers = sorted(points, key=lambda pid: -points[pid]["pts"])[:HOME_FACES]
    draft = str(int(season) + 1)
    lottery = None
    if draft not in {d["season"] for d in league["drafts"]}:
        # From the 2027 draft the odds follow Max PF, lowest first (5th Amendment); before that, regular-season standing.
        out = [r for r in s["standings"] if r["seed"] > 6]
        out.sort(key=(lambda r: r["max_pf"]) if int(draft) >= 2027 else (lambda r: -r["seed"]))
        holder = {p["original"]: p["holder"] for p in picks["future"] if p["season"] == draft and p["round"] == 1}
        lottery = {"draft": draft, "basis": "max_pf" if int(draft) >= 2027 else "standing", "final": s["status"] == "complete",
                   "rows": [{"franchise": r["franchise"], "team": r["team"], "max_pf": r["max_pf"], "odds": odds, "holder": holder.get(r["franchise"], r["franchise"])}
                            for r, odds in zip(out, LOTTERY_ODDS)]}
    return {"season": season, "status": s["status"], "weeks_played": s["weeks_played"],
            "scorers": [{"player": pid, "name": players[pid]["name"], "pos": players[pid]["pos"], "pts": r2(points[pid]["pts"]), "gs": points[pid]["gs"]} for pid in scorers],
            "standings": [{k: r[k] for k in ("seed", "franchise", "team", "w", "l", "pf", "finish", "result")} for r in s["standings"]],
            "lottery": lottery, "last_week": honors}


# ---------------------------------------------------------------- this week

def this_week(league, players, files, seasons, games, streak_data, rivalry_data, waiver_log, every_milestone):
    """The current week's matchups: lineups as last fetched, projections, Elo win chances, history and storylines."""
    from datetime import datetime, timezone
    season, week = league["current_season"], league["current_week"]
    raw = ROOT / "data" / "raw" / season
    path = raw / "matchups" / f"{week:02d}.json"
    rows = [r for r in load(path) if r.get("matchup_id")] if path.exists() else []
    out = {"season": season, "week": week, "matchups": [], "fetched": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="minutes") if path.exists() else None}
    if not rows or seasons[season]["status"] == "complete":
        return out
    nfl = load(ROOT / "data" / "nfl" / f"{season}.json")["weeks"].get(str(week), {})
    proj, out["start"] = nfl.get("proj", {}), nfl.get("start")
    slots = [p for p in load(raw / "league.json")["roster_positions"] if p != "BN"]
    standings = {r["franchise"]: r for r in seasons[season]["standings"]}
    elo = {r["franchise"]: r["elo"] for r in load(SITE / "elo.json")["table"]}
    owners = {f["id"]: f["owners"] for f in league["franchises"]}
    streaks = {r["key"]: r for r in streak_data["franchises"]["current"]}
    upcoming = defaultdict(list)   # (kind, key) -> the next points, wins and starts milestones
    for u in every_milestone:
        upcoming[(u["kind"], str(u["key"]))].append(u)
    nth = lambda u: f"{u['what'].split(' ')[0]}th"
    kind = "regular" if week <= 14 else "postseason"

    def side(row):
        fid = row["roster_id"]
        st = standings[fid]
        lineup = []
        for i, pid in enumerate(row["starters"]):
            known = pid in players
            lineup.append({"slot": slots[i] if i < len(slots) else "", "player": pid if known else None, "name": players[pid]["name"] if known else "Empty",
                           "pos": players[pid]["pos"] if known else "", "proj": proj.get(pid), "pts": (row.get("players_points") or {}).get(pid)})
        streak = streaks.get(fid)
        return {"franchise": fid, "team": st["team"], "managers": owners[fid], "w": st["w"], "l": st["l"], "seed": st["seed"], "ppg": st["ppg"], "pf": st["pf"],
                "elo": elo[fid], "streak": streak and {"n": streak["n"], "won": streak["won"]}, "points": row.get("points") or 0.0,
                "proj": r2(sum(x["proj"] or 0 for x in lineup)), "lineup": lineup}

    pairs = defaultdict(list)
    for row in rows:
        pairs[row["matchup_id"]].append(row)
    for _, pair in sorted(pairs.items()):
        if len(pair) != 2:
            continue
        a, b = sorted((side(r) for r in pair), key=lambda x: x["seed"])
        a["chance"] = round(1 / (1 + 10 ** ((b["elo"] - a["elo"]) / 400)), 3)
        b["chance"] = round(1 - a["chance"], 3)
        key = "~".join(str(x) for x in sorted((a["franchise"], b["franchise"]), key=str))
        met = rivalry_data["franchises"].get(key, {"games": []})["games"]
        first = a["franchise"] if str(a["franchise"]) == key.split("~")[0] else b["franchise"]
        wins = Counter((g["a_fid"] if g["a_pts"] > g["b_pts"] else g["b_fid"]) for g in met)
        history = {"key": key, "games": len(met), "a_w": wins[a["franchise"]], "b_w": wins[b["franchise"]],
                   "last": [{"id": g["id"], "season": g["season"], "week": g["week"], "label": g["label"],
                             "a_pts": g["a_pts"] if g["a_fid"] == a["franchise"] else g["b_pts"], "b_pts": g["b_pts"] if g["a_fid"] == a["franchise"] else g["a_pts"]} for g in met[-5:][::-1]]}
        notes = []
        for x, y in ((a, b), (b, a)):
            name = f"[[f:{x['franchise']}|{x['team']}]]"
            if x["streak"] and x["streak"]["n"] >= 3:
                notes.append(f"{name} {'have won' if x['streak']['won'] else 'have lost'} {x['streak']['n']} in a row.")
            if x["l"] == 0 and x["w"] >= 3:
                notes.append(f"{name} are unbeaten at {x['w']}-0.")
            if x["w"] == 0 and x["l"] >= 3:
                notes.append(f"{name} are still looking for a first win.")
            for u in upcoming[("franchise", str(x["franchise"]))]:
                if u["unit"] == "wins" and u["need"] == 1:
                    notes.append(f"A win would be the {nth(u)} in franchise history for {name}.")
                if u["unit"] == "points" and u["need"] <= x["proj"] * REACH:
                    notes.append(f"{name} need {u['need']:.2f} points to reach {u['what']} all-time.")
            for slug in x["managers"]:
                for u in upcoming[("manager", slug)]:
                    if u["unit"] == "wins" and u["need"] == 1:
                        notes.append(f"A win would be the {nth(u)} as a manager for [[m:{slug}]].")
                    if u["unit"] == "points" and u["need"] <= x["proj"] * REACH:
                        notes.append(f"[[m:{slug}]] needs {u['need']:.2f} points to reach {u['what']} as a manager.")
            for line in x["lineup"]:
                pid = line["player"]
                if not pid:
                    continue
                left = [] if line["pos"] in ("K", "DEF") else [e for e in files[pid]["history"] if e.get("from") == y["franchise"] and e["type"] in ("traded", "released")]
                if left:
                    last = left[-1]
                    drop = next((d for d in waiver_log["drops"] if d["player"] == pid and d["franchise"] == y["franchise"] and d["date"] == last["date"]), None)
                    how = (f"[[t:{last['trade']}|traded him]]" if last["type"] == "traded" and last.get("trade") else "traded him" if last["type"] == "traded"
                           else f"[[x:team.html?y={drop['season']}&id={y['franchise']}#releases|released him]]" if drop else "released him")
                    notes.append(f"Revenge game: [[p:{pid}|{line['name']}]] faces the team that {how} in {last['date'][:4]}.")
                for m in upcoming[("player", pid)]:
                    if m["unit"] == "points" and line["proj"] and m["need"] <= line["proj"] * REACH:
                        notes.append(f"[[p:{pid}|{line['name']}]] needs {m['need']:.2f} points to reach {m['what']} in iooi lineups.")
                    if m["unit"] == "starts" and m["need"] == 1:
                        notes.append(f"[[p:{pid}|{line['name']}]] makes his {nth(m)} iooi start.")
        playoff = [g for g in met if g["kind"] == "winners"]
        if playoff:
            g = playoff[-1]
            notes.append(f"They last met in the playoffs in the {g['season']} {g['label']} ([[g:{g['id']}]]).")
        if not met:
            notes.append("First meeting between these franchises.")
        if abs(a["chance"] - 0.5) <= 0.03:
            notes.append("Elo rates this a coin flip.")
        out["matchups"].append({"kind": kind, "a": a, "b": b, "history": history, "notes": notes, "elo_gap": abs(a["elo"] - b["elo"])})
    last = [g for g in games if g["season"] == season and g["week"] == week - 1]
    out["last_week"] = [{"id": g["id"], "label": g["label"], "teams": sorted(({"franchise": t["franchise"], "team": t["team"], "points": t["points"]} for t in g["teams"]), key=lambda t: -t["points"])} for g in last]
    return out


# ---------------------------------------------------------------- main

def main():
    league = load(SITE / "league.json")
    players = load(SITE / "players.json")
    seasons = {s["season"]: load(SITE / "seasons" / f"{s['season']}.json") for s in league["seasons"]}
    games = sorted((g for season in seasons for g in load(SITE / "games" / f"{season}.json").values()), key=lambda g: (g["season"], g["week"], g["id"]))
    trades = load(SITE / "trades.json")
    files = {pid: load(SITE / "players" / f"{pid}.json") for pid in players}
    logs = {pid: f["log"] for pid, f in files.items()}
    careers = {pid: f["career"] for pid, f in files.items()}
    totals = {pid: f["totals"] for pid, f in files.items()}
    franchise_names = {f["id"]: f["name"] for f in league["franchises"]}
    manager_names = {m["id"]: m["name"] for m in league["managers"]}

    status = player_status(players, league)
    rostered = {pid for pid, st in status.items() if st["code"] == "rostered"}
    active_managers = {m["id"] for m in league["managers"] if any(st["to"] is None for st in load(SITE / "managers" / f"{m['id']}.json")["stints"])}
    streak_data = {
        "franchises": team_streaks(games, lambda side: [side["franchise"]]),
        "managers": team_streaks(games, lambda side: side["managers"]),
        "players": player_streaks(players, logs),
        "postseason": postseason_runs(seasons, league["franchises"]),
        "milestones": milestones(games, players, logs, franchise_names, manager_names, rostered, active_managers),
        "stretches": stretches(games, players, logs),
    }
    every_milestone = streak_data["milestones"].pop("all")
    dump(SITE / "streaks.json", streak_data)
    rivalry_data = rivalries(games, trades)
    dump(SITE / "rivalries.json", rivalry_data)

    ink, leads = black_ink(seasons, players, careers, logs)
    dump(SITE / "black_ink.json", ink)
    rank_of = {row["player"]: i + 1 for i, row in enumerate(ink["table"])}
    for pid, near in similar_players(players, totals).items():
        files[pid]["similar"] = near
    ranks = list_ranks(players, careers, totals, logs, franchise_names)
    for pid, p in players.items():
        p["status"] = status[pid]
        on_roster = sorted({row["season"] for row in careers[pid]})   # seasons with a week on an iooi roster, for the search box
        p["years"] = [on_roster[0], on_roster[-1]] if on_roster else None
        p["last_team"] = {k: careers[pid][-1][k] for k in ("season", "franchise", "team")} if careers[pid] else None
    dump(SITE / "players.json", players)
    for m in league["managers"]:
        stints = load(SITE / "managers" / f"{m['id']}.json")["stints"]
        m["years"] = [min(st["from"] for st in stints)[:4], max((st["to"] or league["current_season"])[:4] for st in stints)]
        m["active"] = any(st["to"] is None for st in stints)
        latest = max(stints, key=lambda st: (st["to"] is None, st["to"] or "", st["from"]))   # the stake held now, else the last one given up
        m["franchise"] = latest["franchise"]
    dump(SITE / "league.json", league)
    waivers = load(SITE / "waivers.json")
    for f in league["franchises"]:
        path = SITE / "franchises" / f"{f['id']}.json"
        dump(path, {**load(path), "moves": franchise_moves(f["id"], league, trades, waivers)})
    for m in league["managers"]:
        path = SITE / "managers" / f"{m['id']}.json"
        manager = load(path)
        dump(path, {**manager, **manager_lists(manager, players, logs)})
    for pid, f in files.items():
        f["status"] = status[pid]
        f["ranks"] = ranks.get(pid, [])
        f.setdefault("similar", [])
        f["black_ink"] = {"score": next(r["score"] for r in ink["table"] if r["player"] == pid), "rank": rank_of[pid], "leads": leads[pid]} if pid in rank_of else None
        dump(SITE / "players" / f"{pid}.json", f)

    similar = similar_teams(seasons)
    waiver_log = load(SITE / "waiver_log.json")
    for season, s in seasons.items():
        s["leaders"] = season_leaders(season, players, careers, logs, s["weeks_played"])
        dump(SITE / "seasons" / f"{season}.json", s)
        draft_path = SITE / "drafts" / f"{season}.json"
        draft = load(draft_path) if draft_path.exists() else None
        for f in league["franchises"]:
            dump(SITE / "teams" / f"{season}-{f['id']}.json", team_season(season, s, f["id"], games, players, careers, draft, trades, similar, waiver_log))

    files = {pid: load(SITE / "players" / f"{pid}.json") for pid in players}
    raw_traded = load(ROOT / "data" / "raw" / league["current_season"] / "traded_picks.json")
    picks = pick_ledger(trades, league, raw_traded)
    chains = picks.pop("chains")
    dump(SITE / "picks.json", picks)
    dump(SITE / "pick_search.json", pick_search(league, picks, players))
    # Every drafted pick carries the trades that moved it, for its own page.
    for d in league["drafts"]:
        path = SITE / "drafts" / f"{d['season']}.json"
        draft = load(path)
        for p in draft["picks"]:
            p["moves"] = [{k: v for k, v in m.items() if k != "became"} for m in chains.get((d["season"], p["round"], p["original"]), [])]
        dump(path, draft)
    stints = {m["id"]: load(SITE / "managers" / f"{m['id']}.json")["stints"] for m in league["managers"]}
    honors = last_week_honors(games, players, load(SITE / "records.json"), streak_data["milestones"]["reached"], logs, stints)
    dump(SITE / "home.json", home(league, seasons, players, careers, picks, honors))
    dump(SITE / "week.json", this_week(league, players, files, seasons, games, streak_data, rivalry_data, waiver_log, every_milestone))
    teams = all_time_teams(players, careers, league)
    dump(SITE / "all_time_teams.json", teams)
    dump(SITE / "journeys.json", journeys(players, files))
    dump(SITE / "aging.json", aging(players, files))
    dump(SITE / "hall_of_fame.json", {"inductees": load(MANUAL / "hall_of_fame.json")["inductees"], **hof_monitor(players, files, ink, games)})


if __name__ == "__main__":
    main()

"""Roster-building analysis: Team Construction and Anatomy of a Champion.

Every iooi start is credited to the way the franchise got the player for that stay: the startup draft, a rookie
draft (a traded pick counts as a draft pick), the 2024 split draft, a trade, a waiver claim or a free-agent signing.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta

SOURCES = {"startup": "Startup Draft", "rookie": "Rookie Draft", "split": "Split Draft", "trade": "Trades", "waivers": "Waivers", "fa": "Free Agency"}
ACQUIRED = {"draft", "traded", "signed", "claimed", "reassigned"}


def r2(x):
    return round(x + 0, 2)


def acquisitions(files):
    """Per start: (player, log entry, source), the source being how the franchise got him for that stay."""
    out = []
    for pid, f in files.items():
        got = sorted((e for e in f["history"] if e["type"] in ACQUIRED), key=lambda e: e["date"])
        for a in f["log"]:
            if not a["started"]:
                continue
            # A log date is the week's first game; a player picked up later that week (for Sunday) counts too.
            week_end = (date.fromisoformat(a["date"]) + timedelta(days=6)).isoformat()
            e = (next((e for e in reversed(got) if e["to"] == a["franchise"] and e["date"] <= a["date"]), None)
                 or next((e for e in got if e["to"] == a["franchise"] and a["date"] < e["date"] <= week_end), None))
            source = ("other" if e is None else ("startup" if "startup" in e["detail"] else "rookie") if e["type"] == "draft"
                      else "trade" if e["type"] == "traded" else "split" if e["type"] == "reassigned" else "waivers" if e["type"] == "claimed" else "fa")
            out.append((pid, a, source))
    return out


def construction(starts, league, seasons, players):
    """Points as a starter by source, for each franchise all-time and in each season, plus the top player from each source."""
    def table(rows):
        t = defaultdict(lambda: {"pts": 0.0, "gs": 0, "war": 0.0})
        for pid, a, source in rows:
            x = t[source]
            x["pts"] += a["pts"]
            x["gs"] += 1
            x["war"] += a.get("war") or 0
        total = sum(x["pts"] for x in t.values())
        return {"total": r2(total), "sources": {k: {"pts": r2(x["pts"]), "gs": x["gs"], "war": r2(x["war"]), "share": round(x["pts"] / total, 4) if total else 0} for k, x in t.items()}}

    by_franchise = defaultdict(list)
    for row in starts:
        by_franchise[row[1]["franchise"]].append(row)
    all_time, top = [], {}
    for f in league["franchises"]:
        rows = by_franchise[f["id"]]
        all_time.append({"franchise": f["id"], **table(rows)})
        best = defaultdict(lambda: defaultdict(float))
        for pid, a, source in rows:
            best[source][pid] += a["pts"]
        top[f["id"]] = {s: [{"player": pid, "name": players[pid]["name"], "pts": r2(v)} for pid, v in sorted(m.items(), key=lambda kv: -kv[1])[:3]] for s, m in best.items()}
    by_season = {}
    for season in seasons:
        rows = [{"franchise": f["id"], "team": next((r["team"] for r in seasons[season]["standings"] if r["franchise"] == f["id"]), f["name"]),
                 **table([x for x in by_franchise[f["id"]] if x[1]["season"] == season])} for f in league["franchises"]]
        by_season[season] = rows
    league_rows = [{"season": s, **table([x for x in starts if x[1]["season"] == s])} for s in seasons]
    return {"labels": SOURCES, "all_time": all_time, "by_season": by_season, "league": league_rows, "top": top}


def champions(starts, league, seasons, games, players, files, awards, elo):
    """Each complete season's champion measured against the league that season, then the threads they share."""
    def rank(values, fid, higher=True):
        mine = values[fid]
        return 1 + sum(1 for v in values.values() if (v > mine if higher else v < mine))

    metrics = [   # key, label, tip, format, which way is "1st"
        ("w", "Regular-season wins", "Wins in weeks 1-14", "int", True),
        ("pf", "Points for", "Regular-season points scored", "pts", True),
        ("max_pf", "Max PF", "Points the best possible lineup would have scored", "pts", True),
        ("efficiency", "Lineup efficiency", "Points scored as a share of Max PF", "pct", True),
        ("pa", "Points against", "Regular-season points allowed (1st = most)", "pts", True),
        ("luck", "Luck", "Actual wins minus expected wins from weekly scores", "signed", True),
        ("elo", "Elo entering the playoffs", "Elo rating after week 14", "int", True),
        ("trade_share", "Share of points from trades", "Points from players got in trades", "pct", True),
        ("draft_share", "Share of points from drafts", "Startup, rookie and split drafts", "pct", True),
        ("wire_share", "Share of points from the waiver wire", "Waiver claims and free-agent signings", "pct", True),
        ("trades", "Trades made", "Trades completed that season", "int", True),
        ("transactions", "Transactions", "Every add, drop and trade that season", "int", True),
        ("age", "Average starter age", "Age of the starting lineup, weighted by starts (1st = oldest)", "age", True),
        ("top3", "Top-three share", "Share of points from the team's three top scorers (1st = most top-heavy)", "pct", True),
        ("qb", "QB PAA", "Points above the average starter by the team's QBs", "signed", True),
        ("rb", "RB PAA", "Points above average by the team's RBs", "signed", True),
        ("wr", "WR PAA", "Points above average by the team's WRs", "signed", True),
        ("te", "TE PAA", "Points above average by the team's TEs", "signed", True),
        ("all_iooi", "All-iooi players", "Players on the All-iooi First or Second Team", "int", True),
    ]
    by_season_starts = defaultdict(list)
    for row in starts:
        if row[1]["kind"] == "regular":
            by_season_starts[(row[1]["season"], row[1]["franchise"])].append(row)
    award_of = {a["season"]: a for a in awards}

    def season_values(season):
        s = seasons[season]
        weeks = sorted({g["week"] for g in games if g["season"] == season and g["kind"] == "regular"})
        out = {}
        for r in s["standings"]:
            fid = r["franchise"]
            rows = by_season_starts[(season, fid)]
            total = sum(a["pts"] for _, a, _ in rows) or 1
            share = lambda keys: sum(a["pts"] for _, a, src in rows if src in keys) / total
            scorer = Counter()
            paa = Counter()
            ages, n = 0.0, 0
            for pid, a, _ in rows:
                scorer[pid] += a["pts"]
                paa[players[pid]["pos"]] += a.get("paa") or 0
                born = files[pid].get("birth_date")
                if born and players[pid]["pos"] != "DEF":
                    ages += (date.fromisoformat(a["date"]) - date.fromisoformat(born)).days / 365.25
                    n += 1
            history = [h for h in elo["history"][str(fid)] if h["season"] == season and h["week"] <= max(weeks or [0])]
            all_iooi = sum(1 for squad in award_of.get(season, {}).get("all_iooi", []) for p in squad["picks"] if any(t["franchise"] == fid for t in p["teams"]))
            out[fid] = {"team": r["team"], "w": r["w"], "pf": r["pf"], "max_pf": r["max_pf"], "efficiency": r["efficiency"], "pa": r["pa"], "luck": r["luck"],
                        "elo": history[-1]["elo"] if history else None, "trade_share": share({"trade"}), "draft_share": share({"startup", "rookie", "split"}),
                        "wire_share": share({"waivers", "fa"}), "trades": r["trades"], "transactions": r["transactions"], "age": ages / n if n else None,
                        "top3": sum(v for _, v in scorer.most_common(3)) / total, "qb": paa["QB"], "rb": paa["RB"], "wr": paa["WR"], "te": paa["TE"],
                        "all_iooi": all_iooi, "seed": r["seed"], "finish": r["finish"]}
        return out

    rounded = lambda v, how: None if v is None else round(v, 4) if how == "pct" else round(v, 1) if how == "age" else r2(v) if how in ("pts", "signed") else v
    champs, ranks_by_metric = [], defaultdict(list)
    for season, s in seasons.items():
        if s["status"] != "complete":
            continue
        values = season_values(season)
        fid = next(r["franchise"] for r in s["standings"] if r["finish"] == 1)
        me = values[fid]
        row = {"season": season, "franchise": fid, "team": me["team"], "seed": me["seed"], "metrics": {}}
        for key, _label, _tip, how, higher in metrics:
            column = {f: v[key] for f, v in values.items() if v[key] is not None}
            if me[key] is None:
                continue
            place = rank(column, fid, higher)
            avg = sum(column.values()) / len(column)
            row["metrics"][key] = {"value": rounded(me[key], how), "rank": place, "avg": rounded(avg, how)}
            ranks_by_metric[key].append(place)
        title = next(g for g in games if g["season"] == season and g["label"] == "Dynasty Championship")
        playoff = [g for g in games if g["season"] == season and g["kind"] == "winners" and any(t["franchise"] == fid for t in g["teams"])]
        mine = lambda g: next(t for t in g["teams"] if t["franchise"] == fid)["points"]
        row["playoff_ppg"] = r2(sum(mine(g) for g in playoff) / len(playoff)) if playoff else None
        row["title_game"] = title["id"]
        mvp = award_of.get(season, {}).get("mvp") or []
        row["mvp_on_team"] = bool(mvp) and any(t["franchise"] == fid for t in mvp[0]["teams"])
        champs.append(row)

    # Threads: a measure on which every champion ranked in the top or bottom third of the league.
    threads = []
    teams = len(league["franchises"])
    for key, label, _tip, _how, _higher in metrics:
        places = ranks_by_metric.get(key, [])
        if len(places) < len(champs) or not places:
            continue
        if max(places) <= teams // 3:
            threads.append({"metric": key, "label": label, "side": "top", "cut": max(places), "ranks": places})
        elif min(places) > teams - teams // 3:
            threads.append({"metric": key, "label": label, "side": "bottom", "cut": teams + 1 - min(places), "ranks": places})

    # The season in progress, measured on the same threads.
    current = league["current_season"]
    fits = []
    if seasons[current]["status"] != "complete" and seasons[current]["games"]:
        values = season_values(current)
        for fid, v in values.items():
            met = []
            for t in threads:
                column = {f: x[t["metric"]] for f, x in values.items() if x[t["metric"]] is not None}
                if v[t["metric"]] is None:
                    continue
                place = rank(column, fid, True)
                if (t["side"] == "top" and place <= t["cut"]) or (t["side"] == "bottom" and place > teams - t["cut"]):
                    met.append(t["metric"])
            fits.append({"franchise": fid, "team": v["team"], "seed": v["seed"], "w": v["w"], "met": met})
        fits.sort(key=lambda r: (-len(r["met"]), r["seed"]))
    return {"metrics": [{"key": k, "label": l, "tip": t, "format": f} for k, l, t, f, _ in metrics], "champions": champs, "threads": threads,
            "current": current, "fits": fits, "teams": teams}

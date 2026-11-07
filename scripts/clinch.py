"""Clinched playoff spots and first-round byes for the season being played, and what this week's games could clinch.

A team has clinched when no finish of the remaining regular-season games can push it out. Wins are counted only:
points break ties in record and have no ceiling, so a rival who can tie a team's win total is treated as passing it.
Seeds 1-5 go by record from 2026 (the 6th goes to the top scorer among the rest, 4th Amendment), so only a top-5
record clinches a playoff spot; before 2026 it was the top 6. The top two seeds get the bye.
No game ends tied (Official Tiebreaker), so each game left is one win for one side.
"""
import json
from itertools import combinations, product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BYE = 2


def record_spots(season):
    return 5 if int(season) >= 2026 else 6


def can_reach(target, k, wins, games):
    """True if some k teams could all finish with `target` wins or more, given their wins and the games left among them."""
    best = {f: w for f, w in wins.items()}
    for a, b in games:
        best[a] += 1
        best[b] += 1
    hopefuls = [f for f in wins if best[f] >= target]
    if len(hopefuls) < k:
        return False
    for group in combinations(hopefuls, k):
        need = {f: max(0, target - wins[f]) for f in group}
        if fits(need, [(a, b) for a, b in games if a in need or b in need]):
            return True
    return False


def fits(need, games):
    """Can each game's win go to one of its teams so every team in `need` gets the wins it needs? (Augmenting paths.)"""
    if sum(need.values()) > len(games):
        return False
    owner = [None] * len(games)   # the team each game's win goes to
    got = {f: 0 for f in need}

    def place(f, seen):
        for i, (a, b) in enumerate(games):
            if f not in (a, b) or i in seen:
                continue
            seen.add(i)
            if owner[i] is None or place(owner[i], seen):
                if owner[i] is not None:
                    got[owner[i]] -= 1
                owner[i] = f
                got[f] += 1
                return True
        return False

    for f, n in need.items():
        for _ in range(n):
            if not place(f, set()):
                return False
    return True


def status(wins, games, spots):
    """{franchise: {"playoffs", "bye"}} for these wins and games left. Each team is tested at its worst: it loses every game left."""
    out = {}
    for f in wins:
        rest = dict(wins)
        del rest[f]
        for a, b in games:
            if f in (a, b):
                rest[b if a == f else a] += 1
        theirs = [(a, b) for a, b in games if f not in (a, b)]
        out[f] = {"playoffs": not can_reach(wins[f], spots, rest, theirs), "bye": not can_reach(wins[f], BYE, rest, theirs)}
    return out


def schedule(season, played_weeks):
    """{week: [(franchise, franchise)]}: the regular-season games not yet played, from Sleeper's matchups; None if a week's file is missing."""
    raw = ROOT / "data" / "raw" / season
    last = json.load(open(raw / "league.json"))["settings"].get("playoff_week_start", 15) - 1
    out = {}
    for week in range(1, last + 1):
        path = raw / "matchups" / f"{week:02d}.json"
        if week in played_weeks:
            continue
        if not path.exists():
            return None
        pairs = {}
        for row in json.load(open(path)):
            if row.get("matchup_id"):
                pairs.setdefault(row["matchup_id"], []).append(row["roster_id"])
        out[week] = [tuple(p) for p in pairs.values() if len(p) == 2]
    return out


def complete(left, teams):
    """Every week still to play has a full slate: each team in exactly one game. Without that, nothing can be called clinched."""
    return left is not None and all(sorted(f for p in pairs for f in p) == sorted(teams) for pairs in left.values())


def season_status(season, standings, games):
    """Clinch status now, before last week, and this week's paths to a clinch for every franchise.

    Returns None when the schedule left is missing or incomplete, or a played game has no winner, else {"now": {fid: {...}}, "new": {fid: [what was clinched last week]}, "paths": {fid: {"playoffs"|"bye": sentence parts}}, "week": n}."""
    regular = [g for g in games if g["season"] == season and g["kind"] == "regular"]
    played = sorted({g["week"] for g in regular})
    left = schedule(season, set(played))
    spots = record_spots(season)
    wins = {r["franchise"]: r["w"] for r in standings}
    if not complete(left, wins) or any(g.get("winner") not in wins for g in regular):
        return None
    if not left:
        # The regular season is over: the seeds are final.
        return {"now": {r["franchise"]: {"playoffs": r["seed"] <= 6, "bye": r["seed"] <= BYE} for r in standings}, "new": {}, "paths": {}, "week": None, "points_seed": False, "by_record": {}}
    later = [p for w in sorted(left) for p in left[w]]
    now = status(wins, later, spots)

    new = {}
    if played:
        last = played[-1]
        before = dict(wins)
        last_games = [g for g in regular if g["week"] == last]
        for g in last_games:
            before[g["winner"]] -= 1
        then = status(before, [tuple(t["franchise"] for t in g["teams"]) for g in last_games] + later, spots)
        new = {f: [k for k in ("playoffs", "bye") if now[f][k] and not then[f][k]] for f in wins}

    # This week: every result of its games, then the worst case for the weeks after.
    week = min(left)
    this, after = left[week], [p for w in sorted(left) if w != week for p in left[w]]
    outcomes = []
    for picks in product((0, 1), repeat=len(this)):
        result = dict(wins)
        for pair, i in zip(this, picks):
            result[pair[i]] += 1
        outcomes.append((picks, result, status(result, after, spots)))
    pf = {r["franchise"]: r["pf"] for r in standings}
    paths = {}
    for f in wins:
        for key in ("playoffs", "bye"):
            if now[f][key]:
                continue
            path = clinch_path(f, key, this, outcomes, spots if key == "playoffs" else BYE, pf, last_week=not after)
            if path:
                paths.setdefault(f, {})[key] = path
    # A path to the bye that is also the path to a playoff spot says enough on its own.
    for f, p in paths.items():
        if p.get("bye") and p.get("playoffs") == p["bye"]:
            del p["playoffs"]
    # The season's last week, from 2026: the 6th seed goes to the top scorer among the teams outside the top five.
    points_seed = not after and int(season) >= 2026
    # Who can still make the top five by record: some result leaves fewer than five teams with more wins (a tie can go either way).
    by_record = {f: any(sum(1 for o in result if result[o] > result[f]) < spots for _, result, _ in outcomes) for f in wins} if points_seed else {}
    return {"now": now, "new": new, "paths": paths, "week": week, "points_seed": points_seed, "by_record": by_record}


def clinch_path(f, key, games, outcomes, k, pf, last_week):
    """How team f can clinch this week: {"options": [{"win", "losses"}], "ties": [{"franchise", "gap"}]}, or None.

    Each option is a smallest set of this week's results that clinches whatever else happens. In the season's last week
    a tie in record is settled by points for, so "ties" names the teams such a tie could come down to, with f's lead in
    points for over each (negative when f trails)."""
    if not any(s[f][key] for _, _, s in outcomes):
        options = []
    else:
        found = []
        # Partial results, smallest first: game index -> 0 or 1 (which side wins); None leaves the game open.
        partials = sorted(product((None, 0, 1), repeat=len(games)), key=lambda p: sum(x is not None for x in p))
        for part in partials:
            fixed = {i: x for i, x in enumerate(part) if x is not None}
            if any(all(fixed.get(i) == x for i, x in prior.items()) for prior in found):
                continue   # a smaller set of these results already clinches
            if all(s[f][key] for picks, _, s in outcomes if all(picks[i] == x for i, x in fixed.items())):
                found.append(fixed)
        options = []
        for fixed in found:
            if any(games[i][1 - x] == f for i, x in fixed.items()):
                continue   # a loss never helps; a set that names one is covered by another without it
            options.append({"win": any(games[i][x] == f for i, x in fixed.items()),
                            "losses": [games[i][1 - x] for i, x in fixed.items() if f not in games[i]]})
    ties = []
    if last_week:
        # Results where f could clinch only by winning a tie in record on points for.
        close = set()
        mine = next((i for i, g in enumerate(games) if f in g), None)
        for picks, result, s in outcomes:
            if s[f][key] or (mine is not None and games[mine][picks[mine]] != f):
                continue   # only the results where f wins
            ahead = sum(1 for o in result if o != f and result[o] > result[f])
            level = [o for o in result if o != f and result[o] == result[f]]
            if ahead < k <= ahead + len(level):
                close.update(level)
        ties = [{"franchise": o, "gap": round(pf[f] - pf[o], 2)} for o in sorted(close, key=lambda o: -pf[o])]
    if not options and not ties:
        return None
    return {"options": options, "ties": ties}

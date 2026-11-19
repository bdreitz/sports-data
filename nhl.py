"""NHL data from the official NHL API (api-web.nhle.com). Regular season only.

Files (season 2026-27 shown):
    nhl_2026-27_player_games.csv   one row per player per game (skaters and goalies who played)
    nhl_2026-27_team_games.csv     one row per team per game, with the opponent's numbers
    nhl_2026-27_status.json        when it last updated, and whether every check passed
    nhl_2026-27_changelog.txt      games added and stat corrections, newest first
"""
from common import NotFinal, minutes, to_int

NAME = "nhl"
WEB = "https://api-web.nhle.com/v1"
FINAL_STATES = {"OFF", "FINAL"}          # OFF = official final

PLAYER_COLUMNS = [
    "season", "game_id", "game_date", "team", "opponent", "home",
    "player_id", "player_name", "position",
    # skaters
    "goals", "assists", "points", "shots", "hits", "blocked_shots", "pim", "plus_minus",
    "pp_goals", "toi", "shifts", "giveaways", "takeaways", "faceoff_pct",
    # goalies
    "starter", "decision", "shots_against", "saves", "goals_against",
]
PLAYER_KEY = ("game_id", "player_id")

TEAM_COLUMNS = [
    "season", "game_id", "game_date", "team", "opponent", "home",
    "result", "decided_in", "goals_for", "goals_against", "shots_for", "shots_against",
    "hits", "blocked_shots", "pim", "pp_goals", "giveaways", "takeaways",
    "opp_hits", "opp_pp_goals",
]
TEAM_KEY = ("game_id", "team")


def season(today):
    start = today.year if today.month >= 9 else today.year - 1
    return f"{start}{start + 1}"


def file_names(season_id):
    label = f"{season_id[:4]}-{season_id[6:]}"
    return {
        "players": f"nhl_{label}_player_games.csv",
        "teams": f"nhl_{label}_team_games.csv",
        "status": f"nhl_{label}_status.json",
        "changelog": f"nhl_{label}_changelog.txt",
    }


def _text(value):
    """NHL names come as {"default": "..."}; return the plain text."""
    if isinstance(value, dict):
        return value.get("default") or ""
    return value or ""


def fetch_context(get, season_id):
    """Teams and standings (for validation), plus full player names from team rosters."""
    data = get(f"{WEB}/standings/now")
    standings = {}
    for s in data.get("standings", []):
        abbrev = _text(s.get("teamAbbrev"))
        standings[abbrev] = {
            "gp": to_int(s.get("gamesPlayed")),
            "w": to_int(s.get("wins")),
            "l": to_int(s.get("losses")),
            "otl": to_int(s.get("otLosses")),
            "gf": to_int(s.get("goalFor")),
            "ga": to_int(s.get("goalAgainst")),
        }
    if len(standings) < 30:
        raise RuntimeError(f"standings returned only {len(standings)} teams")
    names, warnings = {}, []
    for team in sorted(standings):
        try:
            roster = get(f"{WEB}/roster/{team}/{season_id}")
        except Exception as e:
            warnings.append(f"roster for {team} unavailable ({e}); box score names used")
            continue
        for group in ("forwards", "defensemen", "goalies"):
            for p in roster.get(group, []):
                full = f"{_text(p.get('firstName'))} {_text(p.get('lastName'))}".strip()
                if full:
                    names[str(p.get("id"))] = full
    return {"teams": sorted(standings), "standings": standings, "names": names, "warnings": warnings}


def fetch_schedule(get, season_id, ctx):
    """Every regular-season game, from each team's season schedule."""
    games = {}
    for team in ctx["teams"]:
        data = get(f"{WEB}/club-schedule-season/{team}/{season_id}")
        for g in data.get("games", []):
            if g.get("gameType") != 2:
                continue
            home, away = g.get("homeTeam") or {}, g.get("awayTeam") or {}
            games[str(g["id"])] = {
                "date": str(g.get("gameDate", ""))[:10],
                "final": g.get("gameState") in FINAL_STATES,
                "home": home.get("abbrev"),
                "away": away.get("abbrev"),
                "home_score": to_int(home.get("score")),
                "away_score": to_int(away.get("score")),
                "last_period": (g.get("gameOutcome") or {}).get("lastPeriodType"),
            }
    return games


def _goalie_shots(g):
    """(saves, shots against) from a goalie's box score line."""
    saves, against = to_int(g.get("saves")), to_int(g.get("shotsAgainst"))
    if saves is None or against is None:
        text = str(g.get("saveShotsAgainst") or "")
        if "/" in text:
            saves, against = (to_int(x) for x in text.split("/")[:2])
    return saves, against


def _sum(rows, field):
    return sum(r.get(field) or 0 for r in rows)


def fetch_game(get, gid, info, ctx, season_id):
    box = get(f"{WEB}/gamecenter/{gid}/boxscore")
    if box.get("gameState") not in FINAL_STATES:
        raise NotFinal(f"box score state is {box.get('gameState')}")
    decided = ((box.get("gameOutcome") or {}).get("lastPeriodType")
               or info.get("last_period")
               or (box.get("periodDescriptor") or {}).get("periodType") or "REG")
    by_game = box.get("playerByGameStats") or {}
    players, skaters = [], {}
    for side, other, is_home in (("homeTeam", "awayTeam", True), ("awayTeam", "homeTeam", False)):
        team, opp = box[side]["abbrev"], box[other]["abbrev"]
        base = {"season": season_id, "game_id": gid, "game_date": info["date"],
                "team": team, "opponent": opp, "home": is_home}
        group = by_game.get(side) or {}
        skaters[side] = []
        for p in (group.get("forwards") or []) + (group.get("defense") or []):
            pid = str(p.get("playerId"))
            row = {
                **base, "player_id": pid,
                "player_name": ctx["names"].get(pid) or _text(p.get("name")),
                "position": p.get("position"),
                "goals": to_int(p.get("goals")), "assists": to_int(p.get("assists")),
                "points": to_int(p.get("points")),
                "shots": to_int(p.get("sog", p.get("shots"))),
                "hits": to_int(p.get("hits")), "blocked_shots": to_int(p.get("blockedShots")),
                "pim": to_int(p.get("pim")), "plus_minus": to_int(p.get("plusMinus")),
                "pp_goals": to_int(p.get("powerPlayGoals")), "toi": minutes(p.get("toi")),
                "shifts": to_int(p.get("shifts")), "giveaways": to_int(p.get("giveaways")),
                "takeaways": to_int(p.get("takeaways")),
                "faceoff_pct": p.get("faceoffWinningPctg"),
            }
            players.append(row)
            skaters[side].append(row)
        for g in group.get("goalies") or []:
            toi = minutes(g.get("toi"))
            if not toi:                     # dressed as the backup but didn't play
                continue
            pid = str(g.get("playerId"))
            saves, against = _goalie_shots(g)
            players.append({
                **base, "player_id": pid,
                "player_name": ctx["names"].get(pid) or _text(g.get("name")),
                "position": "G", "toi": toi, "pim": to_int(g.get("pim")),
                "starter": g.get("starter"), "decision": g.get("decision"),
                "shots_against": against, "saves": saves,
                "goals_against": to_int(g.get("goalsAgainst")),
            })
    teams = []
    for side, other, is_home in (("homeTeam", "awayTeam", True), ("awayTeam", "homeTeam", False)):
        t, o = box[side], box[other]
        gf, ga = to_int(t.get("score")), to_int(o.get("score"))
        if gf is None or ga is None:
            raise RuntimeError("final score missing from the box score")
        mine, theirs = skaters[side], skaters[other]
        shots_for = to_int(t.get("sog"))
        shots_against = to_int(o.get("sog"))
        teams.append({
            "season": season_id, "game_id": gid, "game_date": info["date"],
            "team": t["abbrev"], "opponent": o["abbrev"], "home": is_home,
            "result": "W" if gf > ga else ("OTL" if decided in ("OT", "SO") else "L"),
            "decided_in": decided, "goals_for": gf, "goals_against": ga,
            "shots_for": shots_for if shots_for is not None else _sum(mine, "shots"),
            "shots_against": shots_against if shots_against is not None else _sum(theirs, "shots"),
            "hits": _sum(mine, "hits"), "blocked_shots": _sum(mine, "blocked_shots"),
            "pim": _sum(mine, "pim"), "pp_goals": _sum(mine, "pp_goals"),
            "giveaways": _sum(mine, "giveaways"), "takeaways": _sum(mine, "takeaways"),
            "opp_hits": _sum(theirs, "hits"), "opp_pp_goals": _sum(theirs, "pp_goals"),
        })
    return players, teams


def validate(players, teams, final_games, ctx):
    """Every team's record and goals must match the official standings, every final score
    must match the schedule, and players' goals and shots must add up to the team's."""
    errors = []
    totals = {}
    for r in teams:
        t = totals.setdefault(r["team"], {"gp": 0, "w": 0, "l": 0, "otl": 0, "gf": 0, "ga": 0})
        t["gp"] += 1
        t[{"W": "w", "L": "l", "OTL": "otl"}[r["result"]]] += 1
        t["gf"] += int(r["goals_for"])
        t["ga"] += int(r["goals_against"])
    for team, official in sorted(ctx["standings"].items()):
        mine = totals.get(team, {"gp": 0, "w": 0, "l": 0, "otl": 0, "gf": 0, "ga": 0})
        diffs = [f"{k} {mine[k]} vs {official[k]}" for k in official
                 if official[k] is not None and mine[k] != official[k]]
        if diffs:
            errors.append(f"{team} doesn't match the standings: " + ", ".join(diffs))

    for r in teams:
        info = final_games.get(r["game_id"])
        if info and info["home_score"] is not None and info["away_score"] is not None:
            expected = info["home_score"] if r["home"] == "1" else info["away_score"]
            if int(r["goals_for"]) != expected:
                errors.append(f"game {r['game_id']} {r['team']}: box score {r['goals_for']} "
                              f"goals, schedule says {expected}")

    sums = {}
    for p in players:
        if p["position"] == "G":
            continue
        s = sums.setdefault((p["game_id"], p["team"]), {"goals": 0, "shots": 0})
        s["goals"] += int(p["goals"] or 0)
        s["shots"] += int(p["shots"] or 0)
    for r in teams:
        s = sums.get((r["game_id"], r["team"]), {"goals": 0, "shots": 0})
        goals = int(r["goals_for"])
        # A shootout win adds one goal to the score that no player is credited with
        if r["decided_in"] == "SO" and r["result"] == "W":
            goals -= 1
        if s["goals"] != goals:
            errors.append(f"game {r['game_id']} {r['team']}: players' goals add up to "
                          f"{s['goals']}, expected {goals}")
        if s["shots"] != int(r["shots_for"]):
            errors.append(f"game {r['game_id']} {r['team']}: players' shots add up to "
                          f"{s['shots']}, team has {r['shots_for']}")
    return errors

"""NBA data from ESPN's public JSON (site.api.espn.com). Regular season only.

Files (season 2026-27 shown; ESPN calls it season 2027):
    nba_2026-27_player_games.csv   one row per player per game (players who played)
    nba_2026-27_team_games.csv     one row per team per game, with the opponent's numbers,
                                   possessions, pace and offensive/defensive rating
    nba_2026-27_status.json        when it last updated, and whether every check passed
    nba_2026-27_changelog.txt      games added and stat corrections, newest first

Known quirk to watch: the NBA Cup championship game (mid-December) does not count in the
standings. If ESPN lists it as a regular-season game, the standings check will flag it and
the update will stop publishing until it's excluded.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from common import NotFinal, minutes, to_int

NAME = "nba"
SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
STANDINGS = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings"
EASTERN = ZoneInfo("America/New_York")

PLAYER_COLUMNS = [
    "season", "game_id", "game_date", "team", "opponent", "home",
    "player_id", "player_name", "position", "starter",
    "minutes", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
    "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf", "plus_minus",
]
PLAYER_KEY = ("game_id", "player_id")

TEAM_COLUMNS = [
    "season", "game_id", "game_date", "team", "opponent", "home", "result",
    "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
    "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf",
    "opp_pts", "opp_fgm", "opp_fga", "opp_fg3m", "opp_fg3a", "opp_ftm", "opp_fta",
    "opp_oreb", "opp_reb", "opp_ast", "opp_tov",
    "poss", "opp_poss", "pace", "off_rating", "def_rating",
]
TEAM_KEY = ("game_id", "team")

# Box score column labels (and ESPN's longer key names) -> our names
_PLAYER_STATS = {
    "MIN": "minutes", "minutes": "minutes",
    "FG": "fg", "fieldGoalsMade-fieldGoalsAttempted": "fg",
    "3PT": "fg3", "threePointFieldGoalsMade-threePointFieldGoalsAttempted": "fg3",
    "FT": "ft", "freeThrowsMade-freeThrowsAttempted": "ft",
    "OREB": "oreb", "offensiveRebounds": "oreb",
    "DREB": "dreb", "defensiveRebounds": "dreb",
    "REB": "reb", "rebounds": "reb",
    "AST": "ast", "assists": "ast",
    "STL": "stl", "steals": "stl",
    "BLK": "blk", "blocks": "blk",
    "TO": "tov", "turnovers": "tov",
    "PF": "pf", "fouls": "pf",
    "+/-": "plus_minus", "plusMinus": "plus_minus",
    "PTS": "pts", "points": "pts",
}
_PAIRS = {"fg": ("fgm", "fga"), "fg3": ("fg3m", "fg3a"), "ft": ("ftm", "fta")}


def season(today):
    """ESPN labels a season by the year it ends: 2026-27 is 2027."""
    return str(today.year + 1 if today.month >= 8 else today.year)


def file_names(season_id):
    label = f"{int(season_id) - 1}-{season_id[2:]}"
    return {
        "players": f"nba_{label}_player_games.csv",
        "teams": f"nba_{label}_team_games.csv",
        "status": f"nba_{label}_status.json",
        "changelog": f"nba_{label}_changelog.txt",
    }


def _score(value):
    if isinstance(value, dict):
        value = value.get("value", value.get("displayValue"))
    return to_int(value)


def _eastern_date(iso):
    """'2026-11-19T00:30Z' -> '2026-11-18' (the date in US Eastern time)."""
    text = str(iso).replace("Z", "+00:00")
    try:
        when = dt.datetime.fromisoformat(text)
    except ValueError:
        return str(iso)[:10]
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.astimezone(EASTERN).date().isoformat()


def _standing_entries(node):
    """All standings entries anywhere in ESPN's nested conference/division structure."""
    found = []
    if isinstance(node, dict):
        found.extend((node.get("standings") or {}).get("entries") or [])
        for child in node.get("children") or []:
            found.extend(_standing_entries(child))
    return found


def fetch_context(get, season_id):
    data = get(f"{SITE}/teams")
    teams = {}
    for league in data.get("sports", [{}])[0].get("leagues", []):
        for item in league.get("teams", []):
            t = item.get("team", item)
            teams[str(t["id"])] = t["abbreviation"]
    if len(teams) < 30:
        raise RuntimeError(f"ESPN team list returned only {len(teams)} teams")
    standings = {}
    for entry in _standing_entries(get(STANDINGS)):
        stats = {s.get("name"): s.get("value") for s in entry.get("stats", [])}
        standings[entry["team"]["abbreviation"]] = {
            "w": to_int(stats.get("wins")), "l": to_int(stats.get("losses")),
        }
    if len(standings) < 30:
        raise RuntimeError(f"ESPN standings returned only {len(standings)} teams")
    return {"teams": teams, "standings": standings, "warnings": []}


def fetch_schedule(get, season_id, ctx):
    games = {}
    for team_id in sorted(ctx["teams"]):
        data = get(f"{SITE}/teams/{team_id}/schedule?season={season_id}&seasontype=2")
        for event in data.get("events", []):
            comp = (event.get("competitions") or [{}])[0]
            status = (comp.get("status") or {}).get("type") or {}
            sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
            home, away = sides.get("home", {}), sides.get("away", {})
            games[str(event["id"])] = {
                "date": _eastern_date(event.get("date", "")),
                "final": bool(status.get("completed")) and status.get("state") == "post",
                "home": (home.get("team") or {}).get("abbreviation"),
                "away": (away.get("team") or {}).get("abbreviation"),
                "home_score": _score(home.get("score")),
                "away_score": _score(away.get("score")),
            }
    return games


def _player_line(names, values):
    row = {}
    for label, value in zip(names, values):
        field = _PLAYER_STATS.get(label)
        if field is None:
            continue
        if field in _PAIRS:
            made, att = (str(value).split("-") + [None])[:2]
            row[_PAIRS[field][0]], row[_PAIRS[field][1]] = to_int(made), to_int(att)
        elif field == "minutes":
            row["minutes"] = minutes(value)
        else:
            row[field] = to_int(value)
    return row


def _possessions(t):
    return (t["fga"] or 0) - (t["oreb"] or 0) + (t["tov"] or 0) + 0.44 * (t["fta"] or 0)


def fetch_game(get, gid, info, ctx, season_id):
    summary = get(f"{SITE}/summary?event={gid}")
    comp = ((summary.get("header") or {}).get("competitions") or [{}])[0]
    if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
        raise NotFinal("summary is not final")
    sides = {}
    for c in comp.get("competitors", []):
        abbrev = (c.get("team") or {}).get("abbreviation")
        sides[abbrev] = {"home": c.get("homeAway") == "home", "pts": _score(c.get("score"))}
    if len(sides) != 2 or any(s["pts"] is None for s in sides.values()):
        raise RuntimeError("final score missing from the summary")
    box = summary.get("boxscore") or {}

    players, by_team = [], {abbrev: [] for abbrev in sides}
    for block in box.get("players", []):
        team = (block.get("team") or {}).get("abbreviation")
        if team not in sides:
            continue
        opp = next(a for a in sides if a != team)
        for stat_group in block.get("statistics", []):
            names = stat_group.get("labels") or stat_group.get("keys") or stat_group.get("names") or []
            for a in stat_group.get("athletes", []):
                values = a.get("stats") or []
                if a.get("didNotPlay") or not values:
                    continue
                athlete = a.get("athlete") or {}
                row = {
                    "season": season_id, "game_id": gid, "game_date": info["date"],
                    "team": team, "opponent": opp, "home": sides[team]["home"],
                    "player_id": str(athlete.get("id")),
                    "player_name": athlete.get("displayName"),
                    "position": (athlete.get("position") or {}).get("abbreviation"),
                    "starter": a.get("starter"),
                    **_player_line(names, values),
                }
                players.append(row)
                by_team[team].append(row)

    # Team totals from ESPN's team box; anything missing falls back to the players' sum
    totals = {}
    for t in box.get("teams", []):
        abbrev = (t.get("team") or {}).get("abbreviation")
        stats = {s.get("name"): s.get("displayValue") for s in t.get("statistics", [])}
        line = {}
        for name, pair in (("fieldGoalsMade-fieldGoalsAttempted", _PAIRS["fg"]),
                           ("threePointFieldGoalsMade-threePointFieldGoalsAttempted", _PAIRS["fg3"]),
                           ("freeThrowsMade-freeThrowsAttempted", _PAIRS["ft"])):
            if stats.get(name):
                made, att = (stats[name].split("-") + [None])[:2]
                line[pair[0]], line[pair[1]] = to_int(made), to_int(att)
        for field, name in (("reb", "totalRebounds"), ("oreb", "offensiveRebounds"),
                            ("dreb", "defensiveRebounds"), ("ast", "assists"), ("stl", "steals"),
                            ("blk", "blocks"), ("pf", "fouls")):
            if stats.get(name) not in (None, ""):
                line[field] = to_int(stats[name])
        tov = stats.get("totalTurnovers") or stats.get("turnovers")
        if tov not in (None, ""):
            line["tov"] = to_int(tov)
        totals[abbrev] = line

    lines = {}
    for team in sides:
        line = {f: sum(r.get(f) or 0 for r in by_team[team])
                for f in ("fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb",
                          "ast", "stl", "blk", "tov", "pf")}
        line.update({k: v for k, v in totals.get(team, {}).items() if v is not None})
        line["pts"] = sides[team]["pts"]
        line["poss"] = _possessions(line)
        lines[team] = line

    teams = []
    for team in sides:
        opp = next(a for a in sides if a != team)
        mine, theirs = lines[team], lines[opp]
        pace = (mine["poss"] + theirs["poss"]) / 2
        teams.append({
            "season": season_id, "game_id": gid, "game_date": info["date"],
            "team": team, "opponent": opp, "home": sides[team]["home"],
            "result": "W" if mine["pts"] > theirs["pts"] else "L",
            **{k: mine[k] for k in ("pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb",
                                    "dreb", "reb", "ast", "stl", "blk", "tov", "pf")},
            **{f"opp_{k}": theirs[k] for k in ("pts", "fgm", "fga", "fg3m", "fg3a", "ftm",
                                               "fta", "oreb", "reb", "ast", "tov")},
            "poss": round(mine["poss"], 1), "opp_poss": round(theirs["poss"], 1),
            "pace": round(pace, 1),
            "off_rating": round(100 * mine["pts"] / pace, 1) if pace else None,
            "def_rating": round(100 * theirs["pts"] / pace, 1) if pace else None,
        })
    return players, teams


def validate(players, teams, final_games, ctx):
    """Records must match ESPN's standings, scores must match the schedule, and players'
    points, field goals and free throws must add up to the team's."""
    errors = []
    record = {}
    for r in teams:
        w_l = record.setdefault(r["team"], {"w": 0, "l": 0})
        w_l["w" if r["result"] == "W" else "l"] += 1
    for team, official in sorted(ctx["standings"].items()):
        mine = record.get(team, {"w": 0, "l": 0})
        diffs = [f"{k} {mine[k]} vs {official[k]}" for k in ("w", "l")
                 if official[k] is not None and mine[k] != official[k]]
        if diffs:
            errors.append(f"{team} doesn't match the standings: " + ", ".join(diffs))

    for r in teams:
        info = final_games.get(r["game_id"])
        if info and info["home_score"] is not None and info["away_score"] is not None:
            expected = info["home_score"] if r["home"] == "1" else info["away_score"]
            if int(r["pts"]) != expected:
                errors.append(f"game {r['game_id']} {r['team']}: box score {r['pts']} points, "
                              f"schedule says {expected}")

    sums = {}
    for p in players:
        s = sums.setdefault((p["game_id"], p["team"]), {"pts": 0, "fgm": 0, "ftm": 0})
        for k in s:
            s[k] += int(p[k] or 0)
    for r in teams:
        s = sums.get((r["game_id"], r["team"]), {"pts": 0, "fgm": 0, "ftm": 0})
        for k in ("pts", "fgm", "ftm"):
            if s[k] != int(r[k]):
                errors.append(f"game {r['game_id']} {r['team']}: players' {k} add up to "
                              f"{s[k]}, team has {r[k]}")
    return errors

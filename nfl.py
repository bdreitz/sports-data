"""NFL data from nflverse's published files, reshaped to match the NBA and NHL files.
Regular season only.

Files (season 2026 shown):
    nfl_2026_player_games.csv   one row per player per game
    nfl_2026_team_games.csv     one row per team per game, with points and the opponent's numbers
    nfl_2026_status.json        when it last updated, and whether every check passed
    nfl_2026_changelog.txt      games added and stat corrections, newest first

Every stat column keeps nflverse's own name (passing_yards, carries, receptions, fg_made ...),
so code written against nflverse's files reads these too. The leading columns are the same
as the NBA and NHL files: season, week, game_id, game_date, team, opponent, home, and for
players player_id, player_name (nflverse's display name) and position.

nflverse publishes whole-season files, so every run re-reads the season: any correction
nflverse makes is picked up the next day and listed in the changelog.

Checks: every final game must have both teams' rows and players for both teams, every team's
record (and points, when ESPN provides them) must match ESPN's standings, which is a source
independent of nflverse, and players' passing, rushing and receiving yards must add up to the
team's.
"""
import csv
import io

NAME = "nfl"
_RELEASES = "https://github.com/nflverse/nflverse-data/releases/download"
PLAYER_URLS = [_RELEASES + "/stats_player/stats_player_week_{season}.csv",
               _RELEASES + "/stats_player/stats_player_week_{season}.csv.gz"]
TEAM_URLS = [_RELEASES + "/stats_team/stats_team_week_{season}.csv",
             _RELEASES + "/stats_team/stats_team_week_{season}.csv.gz"]
SCHEDULE_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
ESPN_STANDINGS = "https://site.api.espn.com/apis/v2/sports/football/nfl/standings"

# nflverse team code -> ESPN team code, where they differ
ESPN_CODES = {"LA": "LAR", "WAS": "WSH"}

IDENTITY = ["season", "week", "game_id", "game_date", "team", "opponent", "home"]
PLAYER_COLUMNS = IDENTITY + ["player_id", "player_name", "position"]   # + nflverse stats
TEAM_COLUMNS = IDENTITY + ["result", "points_for", "points_against"]    # + nflverse stats
PLAYER_KEY = ("game_id", "player_id")
TEAM_KEY = ("game_id", "team")

# nflverse columns covered by the leading columns above (or not useful in a stats file)
_COVERED = {"season", "week", "season_type", "team", "recent_team", "opponent_team",
            "player_id", "player_name", "player_display_name", "position", "headshot_url",
            "game_id"}
# The opponent's numbers added to each team row, when the team file has them
OPPONENT_STATS = ["passing_yards", "rushing_yards", "passing_tds", "rushing_tds",
                  "passing_interceptions", "sacks_suffered"]
# Player sums that must equal the team's numbers
_SUM_CHECKS = ["passing_yards", "rushing_yards", "receiving_yards"]


def season(today):
    return str(today.year if today.month >= 9 else today.year - 1)


def file_names(season_id):
    return {
        "players": f"nfl_{season_id}_player_games.csv",
        "teams": f"nfl_{season_id}_team_games.csv",
        "status": f"nfl_{season_id}_status.json",
        "changelog": f"nfl_{season_id}_changelog.txt",
    }


def _clean(value):
    return "" if value in (None, "NA") else value


def _read_csv(get, urls, season_id):
    """(header, rows) from the first URL that downloads."""
    problems = []
    for template in urls:
        url = template.format(season=season_id)
        try:
            text = get(url, text=True)
        except Exception as e:
            problems.append(str(e))
            continue
        reader = csv.DictReader(io.StringIO(text))
        return reader.fieldnames or [], list(reader)
    raise RuntimeError("could not download nflverse data: " + "; ".join(problems))


def _standing_entries(node):
    found = []
    if isinstance(node, dict):
        found.extend((node.get("standings") or {}).get("entries") or [])
        for child in node.get("children") or []:
            found.extend(_standing_entries(child))
    return found


def fetch_context(get, season_id):
    _, schedule_rows = _read_csv(get, [SCHEDULE_URL], season_id)
    p_header, p_rows = _read_csv(get, PLAYER_URLS, season_id)
    t_header, t_rows = _read_csv(get, TEAM_URLS, season_id)
    p_rows = [r for r in p_rows if r.get("season_type", "REG") == "REG"]
    t_rows = [r for r in t_rows if r.get("season_type", "REG") == "REG"]

    standings = {}
    for entry in _standing_entries(get(ESPN_STANDINGS)):
        stats = {s.get("name"): s.get("value") for s in entry.get("stats", [])}
        standings[entry["team"]["abbreviation"]] = {
            k: (int(stats[name]) if stats.get(name) is not None else None)
            for k, name in (("w", "wins"), ("l", "losses"), ("t", "ties"),
                            ("pf", "pointsFor"), ("pa", "pointsAgainst"))
        }
    if len(standings) < 32:
        raise RuntimeError(f"ESPN standings returned only {len(standings)} teams")

    player_stats = [c for c in p_header if c not in _COVERED]
    team_stats = [c for c in t_header if c not in _COVERED]
    return {
        "schedule_rows": schedule_rows,
        "player_rows": p_rows, "team_rows": t_rows,
        "player_columns": PLAYER_COLUMNS + player_stats,
        "team_columns": TEAM_COLUMNS + team_stats
                        + [f"opp_{c}" for c in OPPONENT_STATS if c in t_header],
        "opponent_stats": [c for c in OPPONENT_STATS if c in t_header],
        "standings": standings,
        "warnings": [],
    }


def fetch_schedule(get, season_id, ctx):
    games = {}
    for g in ctx["schedule_rows"]:
        if g.get("season") != season_id or g.get("game_type") != "REG":
            continue
        home, away = _clean(g.get("home_score")), _clean(g.get("away_score"))
        games[g["game_id"]] = {
            "date": g.get("gameday", ""),
            "week": g.get("week"),
            "home": g.get("home_team"), "away": g.get("away_team"),
            "home_score": int(home) if home != "" else None,
            "away_score": int(away) if away != "" else None,
            "final": home != "" and away != "",
        }
    return games


def fetch_all(get, season_id, ctx, final_games):
    """Every player and team row for the final games, in the shared format."""
    by_team_week = {}
    for gid, g in final_games.items():
        by_team_week[(g["week"], g["home"])] = (gid, g, True)
        by_team_week[(g["week"], g["away"])] = (gid, g, False)

    def identity(r):
        found = by_team_week.get((r.get("week"), r.get("team") or r.get("recent_team")))
        if found is None:
            return None
        gid, g, is_home = found
        return {"season": season_id, "week": g["week"], "game_id": gid, "game_date": g["date"],
                "team": g["home"] if is_home else g["away"],
                "opponent": g["away"] if is_home else g["home"], "home": is_home}

    players = []
    for r in ctx["player_rows"]:
        base = identity(r)
        if base is None:        # a game that isn't final yet (or a row with no team)
            continue
        row = {c: _clean(v) for c, v in r.items() if c not in _COVERED}
        row.update(base)
        row.update({"player_id": r.get("player_id"),
                    "player_name": _clean(r.get("player_display_name")) or _clean(r.get("player_name")),
                    "position": _clean(r.get("position"))})
        players.append(row)

    team_stats = {}
    for r in ctx["team_rows"]:
        base = identity(r)
        if base is not None:
            team_stats[(base["game_id"], base["team"])] = (base, r)
    teams = []
    for (gid, team), (base, r) in team_stats.items():
        g = final_games[gid]
        pf = g["home_score"] if base["home"] else g["away_score"]
        pa = g["away_score"] if base["home"] else g["home_score"]
        row = {c: _clean(v) for c, v in r.items() if c not in _COVERED}
        row.update(base)
        row.update({"result": "W" if pf > pa else ("L" if pf < pa else "T"),
                    "points_for": pf, "points_against": pa})
        opp = team_stats.get((gid, base["opponent"]))
        for c in ctx["opponent_stats"]:
            row[f"opp_{c}"] = _clean(opp[1].get(c)) if opp else ""
        teams.append(row)
    return players, teams


def _number(value):
    try:
        return float(value) if value not in ("", None) else 0.0
    except ValueError:
        return 0.0


def validate(players, teams, final_games, ctx):
    errors = []
    mine = {}
    for r in teams:
        t = mine.setdefault(r["team"], {"w": 0, "l": 0, "t": 0, "pf": 0, "pa": 0})
        t[r["result"].lower()] += 1
        t["pf"] += int(r["points_for"])
        t["pa"] += int(r["points_against"])
    for team, record in sorted(mine.items()):
        espn = ESPN_CODES.get(team, team)
        official = ctx["standings"].get(espn)
        if official is None:
            errors.append(f"{team} (ESPN {espn}) isn't in ESPN's standings")
            continue
        diffs = [f"{k} {record[k]} vs {official[k]}" for k in record
                 if official.get(k) is not None and record[k] != official[k]]
        if diffs:
            errors.append(f"{team} doesn't match ESPN's standings: " + ", ".join(diffs))

    sums = {}
    for p in players:
        s = sums.setdefault((p["game_id"], p["team"]), dict.fromkeys(_SUM_CHECKS, 0.0))
        for c in _SUM_CHECKS:
            s[c] += _number(p.get(c))
    for r in teams:
        s = sums.get((r["game_id"], r["team"]), dict.fromkeys(_SUM_CHECKS, 0.0))
        for c in _SUM_CHECKS:
            if c in r and r[c] != "" and abs(s[c] - _number(r[c])) > 0.01:
                errors.append(f"game {r['game_id']} {r['team']}: players' {c} add up to "
                              f"{s[c]:g}, team has {r[c]}")
    return errors

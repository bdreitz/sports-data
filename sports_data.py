"""Read the NFL, NBA and NHL files published by the "Update stats" GitHub workflow.
Standard library only, so it runs in a-Shell on iPhone.

    guide()                      how to use everything: topics, options and example output
    usage()                      the same as guide()

    status()                     when each sport last updated, and whether its checks passed

    nfl("josh allen")            a player's last 5 games, with the stats for his position
    nfl("josh allen", last=10)   more games
    nfl("josh allen buf")        add a team or position if a name is shared
    nhl("mcdavid")               goalies get their own view: nhl("shesterkin")
    nba("brunson")

    nfl_team("BUF")              a team's last 5 games (use the team code)
    nhl_team("EDM")
    nba_team("NY")

    nfl_columns()                every stat you can show, with its readable name
    nfl_columns("rush")          ...only the ones matching a word
    nfl_columns(team=True)       the team stats instead
    nhl_columns(), nba_columns() the same for the other sports

    nfl("josh allen", stats=["Pass Yards", "passing_epa"])
                                 show exactly the stats you want, by readable name or column name

    daily_report()               this morning's props report for today's games, all sports,
                                 one game at a time: Return = next game, a = all, q = quit
    daily_report(save=True)      save it to a text file instead (Files app > a-Shell), to open,
                                 scroll, or Share > Notes
    daily()                      build that report now (games=5, games="season", min_hit=0.9 ...)

Long output (daily, daily_report, the scans and the column lists) is shown a page at a time.
Add page=False to print it all at once, or save=True to save it to a file.

    nfl_injuries("BUF")          a team's injury report (nfl_injuries() for the whole league)
    nba_injuries("NY"), nhl_injuries("NYR")
                                 Player tables, scans and the daily report also show injury
                                 statuses, and leave off players who are out.

    nfl_standings()              official standings: league, then each conference
    nba_standings("east")        one conference (NFL: "afc" / "nfc"); "overall" = league only
    nhl_standings()

    nfl_leaders()                per-game player leaders (pass/rush/rec yards and TDs, receptions)
    nba_leaders(), nhl_leaders() points/rebounds/assists; saves and shots on goal
    nfl_leaders("rec yds", top=25, games=5, position="WR")

    nba_line_scan("rebounds")    rank today's players by their 80% line, highest first
    nfl_line_scan(), nhl_line_scan()

    nhl_team_scan()              NHL team props for today: team shots, team goals, game total shots

    kalshi_report()              the Kalshi NBA entry backtest: when to buy props, profit after fees

    nfl_scan()                   consistent props (80%+ hit rate) for this week's NFL games
    nba_scan(), nhl_scan()       ...for today's NBA / NHL games
    nfl_scan(games=5)            over each player's last 5 games; games="season" for the whole season
    nba_scan(team="NY")          one team; min_hit=0.9, top=50 and all_teams=True also work

Every player and team table ends with three averages: the last 5 games, the last 10 games and
the whole season.
"""
import contextlib
import csv
import datetime
import functools
import io
import json
import math
import os
import re
import textwrap
import time
import unicodedata
import urllib.error
import urllib.request
from functools import lru_cache

try:
    import shutil
except ImportError:
    shutil = None

REPO = os.environ.get("SPORTS_DATA_REPO") or "bdreitz/sports-data"

_BASE = f"https://github.com/{REPO}/releases/download/data"

# ---------------------------------------------------------------------------
# Which stats each view shows (column names in the files)
# ---------------------------------------------------------------------------

NFL_VIEWS = {
    "QB": ["attempts", "completions", "passing_yards", "passing_tds", "passing_interceptions",
           "rushing_yards", "rushing_tds", "fumbles"],
    "RB": ["carries", "rushing_yards", "rushing_tds", "targets", "receptions", "receiving_yards",
           "receiving_tds", "fumbles"],
    "WR": ["targets", "receptions", "receiving_yards", "rushing_yards", "total_tds", "fumbles"],
    "K": ["fg_made", "fg_att", "fg_long", "fg_made_40_49", "fg_made_50_plus", "pat_made", "pat_att",
          "kick_points", "fumbles"],
    "defense": ["def_tackles_solo", "def_tackle_assists", "def_sacks", "def_interceptions", "fumbles"],
}
NFL_VIEWS["FB"] = NFL_VIEWS["RB"]
NFL_VIEWS["TE"] = NFL_VIEWS["WR"]

NHL_VIEWS = {
    "G": ["shots_against", "saves", "goals_against"],
    "skater": ["goals", "assists", "points", "shots", "toi"],
}
NBA_VIEW = ["minutes", "pts", "reb", "ast", "fg3m", "stl", "blk"]

NFL_TEAM_VIEW = ["result", "points_for", "points_against", "passing_yards", "rushing_yards",
                 "opp_passing_yards", "opp_rushing_yards", "sacks_suffered", "opp_sacks_suffered"]
NHL_TEAM_VIEW = ["result", "goals_for", "goals_against", "shots_for", "shots_against"]
NBA_TEAM_VIEW = ["result", "pts", "opp_pts", "pace", "off_rating", "def_rating"]

# Stats worked out from other columns: name -> [(column, weight)]. Missing columns count as 0.
COMPUTED = {"nfl": {
    "total_tds": [("rushing_tds", 1), ("receiving_tds", 1)],
    "fg_made_50_plus": [("fg_made_50_59", 1), ("fg_made_60_", 1), ("fg_made_60_plus", 1)],
    "kick_points": [("fg_made", 3), ("pat_made", 1)],
    "fumbles": [("sack_fumbles", 1), ("rushing_fumbles", 1), ("receiving_fumbles", 1)],
    "fumbles_lost": [("sack_fumbles_lost", 1), ("rushing_fumbles_lost", 1), ("receiving_fumbles_lost", 1)],
}}

# ---------------------------------------------------------------------------
# Readable names. Columns not listed get one made from their name.
# ---------------------------------------------------------------------------

_KICKING = {
    "fg_made": "FG Made", "fg_att": "FG Att", "fg_missed": "FG Missed", "fg_blocked": "FG Blocked",
    "fg_long": "Longest FG", "fg_pct": "FG %",
    "fg_made_0_19": "FG Made <20", "fg_made_20_29": "FG Made 20-29", "fg_made_30_39": "FG Made 30-39",
    "fg_made_40_49": "FG Made 40-49", "fg_made_50_59": "FG Made 50-59", "fg_made_60_": "FG Made 60+",
    "fg_made_60_plus": "FG Made 60+", "fg_made_50_plus": "FG Made 50+",
    "fg_missed_0_19": "FG Missed <20", "fg_missed_20_29": "FG Missed 20-29",
    "fg_missed_30_39": "FG Missed 30-39", "fg_missed_40_49": "FG Missed 40-49",
    "fg_missed_50_59": "FG Missed 50-59", "fg_missed_60_": "FG Missed 60+",
    "fg_missed_60_plus": "FG Missed 60+",
    "fg_made_list": "FGs Made (yards)", "fg_missed_list": "FGs Missed (yards)",
    "fg_blocked_list": "FGs Blocked (yards)", "fg_made_distance": "FG Yards Made",
    "fg_missed_distance": "FG Yards Missed", "fg_blocked_distance": "FG Yards Blocked",
    "pat_made": "XP Made", "pat_att": "XP Att", "pat_missed": "XP Missed",
    "pat_blocked": "XP Blocked", "pat_pct": "XP %",
    "gwfg_made": "Game-Winning FG Made", "gwfg_att": "Game-Winning FG Att",
    "gwfg_missed": "Game-Winning FG Missed", "gwfg_blocked": "Game-Winning FG Blocked",
    "gwfg_distance": "Game-Winning FG Yards", "kick_points": "Kick Points",
    "fumbles": "Fumbles", "fumbles_lost": "Fumbles Lost",
}

# Player stats (and anything a team table shares with them)
LABELS = {
    "nfl": {
        **_KICKING,
        "attempts": "Pass Att", "completions": "Completions", "passing_yards": "Pass Yards",
        "passing_tds": "Pass TDs", "passing_interceptions": "INTs Thrown",
        "sacks_suffered": "Times Sacked", "carries": "Rush Att", "rushing_yards": "Rush Yards",
        "rushing_tds": "Rush TDs", "targets": "Targets", "receptions": "Receptions",
        "receiving_yards": "Rec Yards", "receiving_tds": "Rec TDs", "total_tds": "Total TDs",
        "fantasy_points": "Fantasy Points", "fantasy_points_ppr": "PPR Points",
        "def_tackles_solo": "Solo Tackles", "def_tackle_assists": "Assisted Tackles",
        "def_sacks": "Sacks", "def_interceptions": "Interceptions",
        "def_sack_yards": "Sack Yards", "def_interception_yards": "INT Return Yards",
        "def_fumbles": "Fumbles on Returns", "fumble_recovery_yards_own": "Own Fumble Return Yards",
        "fumble_recovery_yards_opp": "Fumble Return Yards", "sack_yards_lost": "Sack Yards Lost",
    },
    "nhl": {
        "goals": "Goals", "assists": "Assists", "points": "Points", "shots": "Shots on Goal",
        "toi": "Minutes", "hits": "Hits", "blocked_shots": "Blocked Shots",
        "pim": "Penalty Minutes", "plus_minus": "Plus/Minus", "pp_goals": "Power Play Goals",
        "shifts": "Shifts", "giveaways": "Giveaways", "takeaways": "Takeaways",
        "faceoff_pct": "Faceoff %", "starter": "Started", "decision": "Decision",
        "shots_against": "Shots Faced", "saves": "Saves", "goals_against": "Goals Against",
    },
    "nba": {
        "minutes": "Minutes", "pts": "Points", "reb": "Rebounds", "ast": "Assists",
        "fg3m": "3PT Made", "fg3a": "3PT Att", "stl": "Steals", "blk": "Blocks",
        "fgm": "FG Made", "fga": "FG Att", "ftm": "FT Made", "fta": "FT Att",
        "oreb": "Off Rebounds", "dreb": "Def Rebounds", "tov": "Turnovers", "pf": "Fouls",
        "plus_minus": "Plus/Minus", "starter": "Started",
    },
}

# Team stats. Columns starting with opp_ are what the team allowed: anything not listed here
# is named "<stat> Allowed", e.g. opp_passing_yards -> "Pass Yards Allowed".
TEAM_LABELS = {
    "nfl": {
        "result": "Result", "points_for": "Points Scored", "points_against": "Points Allowed",
        "sacks_suffered": "Times Sacked", "opp_sacks_suffered": "Sacks Made",
        "opp_passing_interceptions": "Interceptions Made",
        "passing_interceptions": "Interceptions Thrown",
        "def_tackles_with_assist": "Tackles With Assist", "def_tackles_for_loss": "Tackles for Loss",
        "def_tackles_for_loss_yards": "Tackle-for-Loss Yards", "def_fumbles_forced": "Fumbles Forced",
        "def_qb_hits": "QB Hits", "def_pass_defended": "Passes Defended",
        "def_tds": "Defensive TDs", "def_safeties": "Safeties",
        "fumble_recovery_opp": "Fumbles Recovered", "fumble_recovery_own": "Own Fumbles Recovered",
        "special_teams_tds": "Special Teams TDs",
    },
    "nhl": {
        "result": "Result", "decided_in": "Decided In", "goals_for": "Goals Scored",
        "goals_against": "Goals Allowed", "shots_for": "Shots on Goal",
        "shots_against": "Shots Allowed", "opp_hits": "Hits Taken",
        "opp_pp_goals": "Power Play Goals Allowed", "check_note": "Check Note",
    },
    "nba": {
        "result": "Result", "opp_pts": "Points Allowed", "opp_tov": "Turnovers Forced",
        "poss": "Possessions", "opp_poss": "Opponent Possessions", "pace": "Pace",
        "off_rating": "Offensive Rating", "def_rating": "Defensive Rating",
    },
}

_WORDS = {"td": "TD", "tds": "TDs", "yds": "Yards", "fg": "FG", "pat": "XP", "epa": "EPA",
          "ppr": "PPR", "pct": "%", "def": "Defensive", "pp": "PP", "qb": "QB", "cpoe": "CPOE",
          "wopr": "WOPR", "racr": "RACR", "pacr": "PACR", "2pt": "2PT", "misc": "Misc",
          "att": "Att", "st": "Special Teams"}

# Columns that describe the game or player rather than a stat
_NOT_STATS = {"season", "week", "game_id", "game_date", "team", "opponent", "home",
              "player_id", "player_name", "position"}


def _label(sport, column, team=False):
    """The readable name for a column (team=True for team tables)."""
    if team and column in TEAM_LABELS[sport]:
        return TEAM_LABELS[sport][column]
    if column in LABELS[sport]:
        return LABELS[sport][column]
    if team and column.startswith("opp_"):
        return _label(sport, column[4:], team=True) + " Allowed"
    return " ".join(_WORDS.get(w, w.capitalize()) for w in column.split("_"))


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

def _labels(today=None):
    today = today or datetime.date.today()
    nhl_start = today.year if today.month >= 9 else today.year - 1
    nba_start = today.year if today.month >= 8 else today.year - 1
    nfl_season = today.year if today.month >= 9 else today.year - 1
    return {"nhl": f"{nhl_start}-{str(nhl_start + 1)[2:]}",
            "nba": f"{nba_start}-{str(nba_start + 1)[2:]}",
            "nfl": str(nfl_season)}


@lru_cache(maxsize=None)
def _download(name):
    # The GitHub job points SPORTS_DATA_LOCAL at the files it just built
    local = os.environ.get("SPORTS_DATA_LOCAL")
    if local and os.path.exists(os.path.join(local, name)):
        with open(os.path.join(local, name), encoding="utf-8") as f:
            return f.read()
    request = urllib.request.Request(f"{_BASE}/{name}", headers={"User-Agent": "sports_data"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} for {name}. Has the workflow published it yet?") from e


def _rows(sport, kind):
    return list(csv.DictReader(io.StringIO(_download(f"{sport}_{_labels()[sport]}_{kind}.csv"))))


def _header(sport, kind):
    text = _download(f"{sport}_{_labels()[sport]}_{kind}.csv")
    return next(csv.reader(io.StringIO(text)))


def _normalize(text):
    return re.sub(r"\s+", " ", re.sub(r"[.'\u2019\-]", "", text.lower())).strip()


def status():
    """Print when each sport last updated and whether every check passed."""
    for sport in ("nfl", "nhl", "nba"):
        try:
            s = json.loads(_download(f"{sport}_{_labels()[sport]}_status.json"))
        except RuntimeError as e:
            print(f"{sport.upper()}: {e}")
            continue
        state = "OK" if s["ok"] else "LAST CHECK FAILED"
        print(f"{sport.upper()} {_labels()[sport]}: {state}")
        print(f"  data as of {s.get('data_updated_utc')}")
        print(f"  checked    {s.get('checked_utc')}")
        print(f"  {s.get('final_games')} games, {s.get('player_rows')} player rows")
        print(f"  last run: +{s.get('games_added')} games, {s.get('corrections')} corrections")
        for e in s.get("errors", [])[:5]:
            print(f"  ! {e}")
        for w in s.get("warnings", [])[:3]:
            print(f"  ~ {w}")


# ---------------------------------------------------------------------------
# Choosing stats and laying them out
# ---------------------------------------------------------------------------

def _value(sport, row, column):
    if column in COMPUTED.get(sport, {}):
        parts = [(row.get(c), w) for c, w in COMPUTED[sport][column]]
        if all(v in (None, "") for v, _ in parts):
            return ""
        return str(sum(float(v) * w for v, w in parts if v not in (None, "")))
    return row.get(column, "")


def _resolve_stats(sport, kind, stats):
    """Turn readable names or column names into column names. Unknown names raise an error."""
    team = kind == "team_games"
    available = [c for c in _header(sport, kind) if c not in _NOT_STATS]
    if not team:
        available += list(COMPUTED.get(sport, {}))
    by_name = {}
    for c in available:
        by_name[_normalize(c)] = c
        by_name[_normalize(c.replace("_", " "))] = c
        by_name[_normalize(_label(sport, c, team))] = c
    out = []
    for s in stats:
        column = by_name.get(_normalize(s))
        if column is None:
            # Common shorthand: "rec yds" -> "rec yards", "pass td" -> "pass tds"
            words = {"yds": "yards", "yd": "yards", "yrds": "yards", "td": "tds",
                     "touchdowns": "tds", "touchdown": "tds", "receiving": "rec",
                     "rushing": "rush", "passing": "pass"}
            column = by_name.get(" ".join(words.get(w, w) for w in _normalize(s).split()))
        if column is None:
            raise ValueError(f"'{s}' isn't one of the {sport.upper()} stats. "
                             f"Try {sport}_columns('{s.split()[0]}') to search.")
        out.append(column)
    return out


def _cell(value):
    if value in (None, ""):
        return "-"
    try:
        number = float(value)
    except ValueError:
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"


def _width():
    try:
        return max(30, min(shutil.get_terminal_size((40, 24)).columns, 100))
    except (AttributeError, ValueError, OSError):
        return 40


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _average(sport, games, column):
    """The average of a column over games ('-' if none have a number). For the Result column,
    the record instead: W-L, W-L-T (NFL ties) or W-L-OTL (NHL)."""
    if column == "result":
        counts = {}
        for g in games:
            counts[g.get("result")] = counts.get(g.get("result"), 0) + 1
        order = ["W", "L", "OTL"] if sport == "nhl" else ["W", "L", "T"]
        record = [counts.get(r, 0) for r in order]
        if order[-1] in ("T",) and not record[-1]:
            record = record[:-1]
        return "-".join(str(n) for n in record)
    values = [_number(_value(sport, g, column)) for g in games]
    values = [v for v in values if v is not None]
    if not values:
        return "-"
    return f"{sum(values) / len(values):.1f}"


def _hit_value(values, hit):
    """The highest number reached in at least `hit` of these games, or None if there are none."""
    values = sorted((v for v in values if v is not None), reverse=True)
    if not values:
        return None
    return values[max(1, math.ceil(hit * len(values) - 1e-9)) - 1]


def _hit_line(values, hit):
    """The highest number reached in at least `hit` of these games ('-' if that's 0 or less):
    the line a bet would have needed to hit that often."""
    line = _hit_value(values, hit)
    return "-" if line is None or line <= 0 else _cell(line) + "+"


def _grid(sport, title, games, columns, team=False, history=None, line=None):
    """One row per stat, one column per game, then the averages over the last 5 games, the last
    10 games and the season (taken from `history`, every game this season). Split into blocks
    that fit the screen. line=(hit rate, games to use, header label): one more column with the
    highest number reached in at least that share of those games."""
    history = history or games
    if sport == "nfl":
        heads = [f"W{g['week']}" for g in games]
    else:
        heads = [f"{int(g['game_date'][5:7])}/{int(g['game_date'][8:10])}" for g in games]
    windows = [history[-5:], history[-10:], history]
    lines = [("", heads + ["Avg", "Avg", "Avg"]),
             ("Opponent", [("" if g["home"] == "1" else "@") + g["opponent"] for g in games]
              + ["L5", "L10", "Season"])]
    if line:
        hit, pool, label = line
        lines[0][1].append(f"{hit:.0%}")
        lines[1][1].append(label)
    for c in columns:
        cells = [_cell(_value(sport, g, c)) for g in games]
        cells += [_average(sport, w, c) for w in windows]
        if line:
            cells.append(_hit_line([_number(_value(sport, g, c)) for g in pool], hit))
        lines.append((_label(sport, c, team), cells))
    label_w = max(len(label) for label, _ in lines)
    extra = 4 if line else 3
    count = len(games) + extra
    col_w = [max(len(cells[j]) for _, cells in lines) for j in range(count)]
    width = _width()
    blocks, current, used = [], [], label_w
    for j, w in enumerate(col_w[:len(games)]):
        if current and used + 1 + w > width:
            blocks.append(current)
            current, used = [], label_w
        current.append(j)
        used += 1 + w
    # The averages (and hit line) stay together: beside the last games if they fit, else in
    # their own block
    averages = [len(games) + i for i in range(extra)]
    if current and used + extra + sum(col_w[-extra:]) <= width:
        current += averages
        blocks.append(current)
    else:
        if current:
            blocks.append(current)
        blocks.append(averages)
    out = [title]
    for n, block in enumerate(blocks):
        if n:
            out.append("")
        for label, cells in lines:
            out.append((label.ljust(label_w) + "".join(" " + cells[j].rjust(col_w[j]) for j in block)).rstrip())
    print("\n".join(out))


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------

def _find_player(sport, name):
    """
    One player's games, or None (after printing why). Matching, in order:
      - a team code or position at the end narrows the search: "josh allen buf", "josh allen qb"
      - names where each word matches a whole word win over partial matches, so "josh allen"
        finds Josh Allen and not Josh Hines-Allen
      - if several players are still left, an exact full-name match wins
    """
    rows = _rows(sport, "player_games")
    words = _normalize(name).split()
    team_codes = {r["team"].lower() for r in rows}
    positions = {r["position"].lower() for r in rows if r["position"]}
    teams, wanted_positions = set(), set()
    while len(words) > 1 and (words[-1] in team_codes or words[-1] in positions):
        word = words.pop()
        (teams if word in team_codes else wanted_positions).add(word)

    players = {}
    for r in rows:
        players.setdefault(r["player_id"], []).append(r)

    def keep(games):
        if teams and not any(g["team"].lower() in teams for g in games):
            return False
        if wanted_positions and not any(g["position"].lower() in wanted_positions for g in games):
            return False
        return True

    def names(games):
        return _normalize(games[-1]["player_name"])

    whole = {pid: g for pid, g in players.items()
             if keep(g) and all(w in names(g).split() for w in words)}
    found = whole or {pid: g for pid, g in players.items()
                      if keep(g) and all(w in names(g) for w in words)}
    if len(found) > 1:
        exact = {pid: g for pid, g in found.items() if names(g) == " ".join(words)}
        found = exact or found
    if not found:
        print(f"No {sport.upper()} player matches '{name}'.")
        return None
    if len(found) > 1:
        print(f"'{name}' matches several players. Add their team or position, "
              f"e.g. '{name} {sorted(found.values(), key=names)[0][-1]['team'].lower()}':")
        for games in sorted(found.values(), key=names):
            print(f"  {games[-1]['player_name']} ({games[-1]['position']}, {games[-1]['team']})")
        return None
    return sorted(next(iter(found.values())), key=lambda r: (r["game_date"], r["game_id"]))


def _show_player(sport, name, last, view, stats, hit=0.80, window=10):
    size, span = _window(window)
    games = _find_player(sport, name)
    if not games:
        return
    latest = games[-1]
    if stats:
        columns = _resolve_stats(sport, "player_games", stats)
    else:
        available = set(_header(sport, "player_games")) | set(COMPUTED.get(sport, {}))
        columns = [c for c in view(latest["position"]) if c in available]
    title = f"{latest['player_name']} | {latest['position']} | {latest['team']} | {len(games)} games"
    status = _injury_line(sport, latest["player_name"], latest["team"])
    if status:
        title += "\n" + status
    pool = games
    if sport == "nhl" and latest["position"] == "G":
        pool = [g for g in games if g.get("starter") == "1"]      # goalies: starts only
    pool = pool if size is None else pool[-size:]
    label = "Season" if size is None else f"L{size}"
    _grid(sport, title, games[-last:], columns, history=games, line=(hit, pool, label))


def nfl(name, last=5, stats=None, hit=0.80, window=10):
    """A player's most recent games. QBs, RBs, WR/TEs, kickers and defenders each get their
    own stats; stats=[...] shows exactly the ones you name. The last column is the highest
    number he reached in at least `hit` (80%) of his last `window` (10) games: the line a bet
    would have needed to hit that often. window=5 or "season" also work."""
    _show_player("nfl", name, last, lambda pos: NFL_VIEWS.get(pos, NFL_VIEWS["defense"]), stats,
                 hit, window)


def nhl(name, last=5, stats=None, hit=0.80, window=10):
    """A player's most recent games. Goalies get shots faced, saves and goals against (their
    hit-line column uses only games they started). Options as in nfl()."""
    _show_player("nhl", name, last, lambda pos: NHL_VIEWS["G" if pos == "G" else "skater"], stats,
                 hit, window)


def nba(name, last=5, stats=None, hit=0.80, window=10):
    """A player's most recent games. Options as in nfl()."""
    _show_player("nba", name, last, lambda pos: NBA_VIEW, stats, hit, window)


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

def _show_team(sport, team, last, view, stats):
    rows = _rows(sport, "team_games")
    codes = {r["team"] for r in rows}
    want = team.strip().upper()
    if want not in codes:
        print(f"Use the team code: {', '.join(sorted(codes))}")
        return
    games = sorted((r for r in rows if r["team"] == want), key=lambda r: (r["game_date"], r["game_id"]))
    columns = _resolve_stats(sport, "team_games", stats) if stats else \
        [c for c in view if c in set(_header(sport, "team_games"))]
    title = f"{want} | {len(games)} games | {_average(sport, games, 'result')}"
    title += "\n" + "\n".join(_injury_summary_or_note(sport, [want]))
    _grid(sport, title, games[-last:], columns, team=True, history=games)


def nfl_team(team, last=5, stats=None):
    """A team's most recent games. Use the nflverse code, e.g. 'BUF', 'LA', 'WAS'."""
    _show_team("nfl", team, last, NFL_TEAM_VIEW, stats)


def nhl_team(team, last=5, stats=None):
    """A team's most recent games. Use the NHL code, e.g. 'EDM'."""
    _show_team("nhl", team, last, NHL_TEAM_VIEW, stats)


def nba_team(team, last=5, stats=None):
    """A team's most recent games. Use ESPN's code, e.g. 'NY', 'BOS', 'GS'."""
    _show_team("nba", team, last, NBA_TEAM_VIEW, stats)


# ---------------------------------------------------------------------------
# Listing the stats
# ---------------------------------------------------------------------------

def _columns(sport, search, team):
    kind = "team_games" if team else "player_games"
    columns = [c for c in _header(sport, kind) if c not in _NOT_STATS]
    if not team:
        columns += list(COMPUTED.get(sport, {}))
    if search:
        needle = _normalize(search)
        columns = [c for c in columns
                   if needle in _normalize(c.replace("_", " ")) or needle in _normalize(_label(sport, c, team))]
    if not columns:
        print(f"No {sport.upper()} {'team ' if team else ''}stats match '{search}'.")
        return
    width = max(len(_label(sport, c, team)) for c in columns)
    for c in columns:
        print(f"{_label(sport, c, team).ljust(width)}  {c}")
    print(f"({len(columns)} {'team ' if team else ''}stats. Use either name in stats=[...])")


# ---------------------------------------------------------------------------
# Paging and saving long output
# ---------------------------------------------------------------------------

PAGE_LINES = 30     # lines per page for output that isn't split by game


def _pages(text, lines):
    """Split a report into one page per game (the title rides with the first game); split
    anything else into pages of `lines` lines."""
    blocks = re.split(r"\n\n(?==)", text)
    if len(blocks) > 1:
        return ["\n\n".join(blocks[:2])] + blocks[2:]
    rows = text.split("\n")
    return ["\n".join(rows[i:i + lines]) for i in range(0, len(rows), lines)] or [""]


def _show(text, page=True, save=False, name="output"):
    if save:
        filename = f"{name}_{_today().isoformat()}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"Saved to {filename} in a-Shell's folder.")
        print("Files app > On My iPhone > a-Shell >")
        print(f"{filename}: open it to scroll, or tap")
        print("Share > Notes to copy it into a note.")
        return
    if not page:
        print(text)
        return
    pages = _pages(text, page if isinstance(page, int) and not isinstance(page, bool) and page > 1
                   else PAGE_LINES)
    for i, chunk in enumerate(pages):
        print(chunk)
        if i == len(pages) - 1:
            return
        try:
            answer = input(f"\n-- {i + 1}/{len(pages)}: Return=next a=all q=quit -- ").strip().lower()
        except EOFError:                # no keyboard (e.g. run by a script): show the rest
            answer = "a"
        if answer == "q":
            return
        if answer == "a":
            print("\n\n".join(pages[i + 1:]))
            return
        print()


def _paged(func):
    """Let a printing function take page= and save=:
    page=True (default) shows a game, or PAGE_LINES lines, at a time; page=False prints it all;
    page=20 sets the lines per page. save=True writes it to a text file instead."""
    @functools.wraps(func)
    def wrapper(*args, page=True, save=False, **kwargs):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            func(*args, **kwargs)
        _show(buffer.getvalue().rstrip("\n"), page, save, func.__name__)
    return wrapper


@_paged
def nfl_columns(search=None, team=False):
    """Every NFL stat: readable name, then column name. nfl_columns("rush"), nfl_columns(team=True)"""
    _columns("nfl", search, team)


@_paged
def nhl_columns(search=None, team=False):
    """Every NHL stat: readable name, then column name. nhl_columns("shot"), nhl_columns(team=True)"""
    _columns("nhl", search, team)


@_paged
def nba_columns(search=None, team=False):
    """Every NBA stat: readable name, then column name. nba_columns("reb"), nba_columns(team=True)"""
    _columns("nba", search, team)


# ---------------------------------------------------------------------------
# Scanner: consistent player props for the coming games
# ---------------------------------------------------------------------------

# The stats the scanner checks, by position ("*" means every other position)
SCAN_STATS = {
    "nfl": {"QB": ["passing_yards", "passing_tds", "rushing_yards"],
            "RB": ["carries", "rushing_yards", "receptions", "receiving_yards"],
            "FB": ["carries", "rushing_yards", "receptions", "receiving_yards"],
            "WR": ["receptions", "receiving_yards"],
            "TE": ["receptions", "receiving_yards"]},
    "nba": {"*": ["pts", "reb", "ast"]},
    "nhl": {"G": ["saves"], "*": ["shots"]},
}

# Lines are tried in steps of this size (1 when not listed)
SCAN_STEP = {"passing_yards": 10, "rushing_yards": 5, "receiving_yards": 5}

# The lowest line worth showing, so the list isn't full of near-certain trivial lines.
# Keys are (sport, position, stat); position "*" covers every position. Edit to taste.
SCAN_FLOOR = {
    ("nfl", "QB", "passing_yards"): 150, ("nfl", "QB", "passing_tds"): 1,
    ("nfl", "QB", "rushing_yards"): 10,
    ("nfl", "*", "carries"): 8, ("nfl", "*", "rushing_yards"): 20,
    ("nfl", "*", "receptions"): 2, ("nfl", "*", "receiving_yards"): 15,
    ("nba", "*", "pts"): 8, ("nba", "*", "reb"): 3, ("nba", "*", "ast"): 2,
    ("nhl", "G", "saves"): 15, ("nhl", "*", "shots"): 2,
}

_SCAN_NAMES = {"goals_for": "Goals",
               "shots_for": "Shots on Goal", "game_shots": "Shots on Goal",
               "passing_yards": "Pass Yards", "passing_tds": "Pass TDs", "rushing_yards": "Rush Yards",
               "carries": "Rush Att", "receptions": "Receptions", "receiving_yards": "Rec Yards",
               "pts": "Points", "reb": "Rebounds", "ast": "Assists", "saves": "Saves",
               "shots": "Shots on Goal"}


def _get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "sports_data"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_text(url):
    request = urllib.request.Request(url, headers={"User-Agent": "sports_data"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8")


# ---------------------------------------------------------------------------
# Time (US Eastern, worked out by hand so it doesn't need time zone files)
# ---------------------------------------------------------------------------

def _eastern(utc):
    """A UTC datetime -> naive US Eastern time, with daylight saving time."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(datetime.timezone.utc).replace(tzinfo=None)

    def sunday(month, nth):
        first = datetime.date(utc.year, month, 1)
        return first + datetime.timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1))
    dst_start = datetime.datetime.combine(sunday(3, 2), datetime.time(7))     # 2am Eastern
    dst_end = datetime.datetime.combine(sunday(11, 1), datetime.time(6))      # 2am Eastern
    return utc + datetime.timedelta(hours=-4 if dst_start <= utc < dst_end else -5)


def _today():
    """Today's date in US Eastern time."""
    return _eastern(datetime.datetime.now(datetime.timezone.utc)).date()


def _parse_utc(text):
    return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def _clock(start):
    if start is None:
        return "Time TBD"
    hour = start.hour % 12 or 12
    return f"{hour}:{start.minute:02d} {'PM' if start.hour >= 12 else 'AM'} ET"


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

def _games_today(sport, day):
    """Regular-season games on `day` (Eastern): [{"start", "home", "away"}], start in Eastern time."""
    games = []
    if sport == "nhl":
        data = _get_json(f"https://api-web.nhle.com/v1/schedule/{day.isoformat()}")
        for block in data.get("gameWeek", []):
            if block.get("date") != day.isoformat():
                continue
            for g in block.get("games", []):
                if g.get("gameType") != 2:
                    continue
                start = _eastern(_parse_utc(g["startTimeUTC"])) if g.get("startTimeUTC") else None
                games.append({"start": start, "home": g["homeTeam"]["abbrev"], "away": g["awayTeam"]["abbrev"]})
    elif sport == "nba":
        data = _get_json("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
                         f"?dates={day.strftime('%Y%m%d')}")
        for event in data.get("events", []):
            if (event.get("season") or {}).get("type", 2) != 2:
                continue                                  # preseason or playoffs
            sides = {c.get("homeAway"): c["team"]["abbreviation"]
                     for c in (event.get("competitions") or [{}])[0].get("competitors", [])}
            if "home" in sides and "away" in sides:
                start = _eastern(_parse_utc(event["date"])) if event.get("date") else None
                games.append({"start": start, "home": sides["home"], "away": sides["away"]})
    else:
        for g in _nfl_schedule():
            if g.get("gameday") != day.isoformat() or g.get("game_type") != "REG":
                continue
            start = None
            if re.match(r"^\d{1,2}:\d{2}$", g.get("gametime") or ""):
                hour, minute = (int(x) for x in g["gametime"].split(":"))
                start = datetime.datetime.combine(day, datetime.time(hour, minute))   # already Eastern
            games.append({"start": start, "home": g["home_team"], "away": g["away_team"]})
    return games


@lru_cache(maxsize=None)
def _nfl_schedule_text():
    return _get_text("https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv")


def _nfl_schedule():
    return list(csv.DictReader(io.StringIO(_nfl_schedule_text())))


def _slate(sport, day):
    """{team: (opponent, is_home)} for the scan functions: `day` for NBA/NHL, the next week with
    unplayed games for NFL. Returns (slate, description)."""
    slate = {}
    if sport in ("nhl", "nba"):
        for g in _games_today(sport, day):
            slate[g["home"]], slate[g["away"]] = (g["away"], True), (g["home"], False)
        return slate, f"{day.month}/{day.day}"
    season = _labels()["nfl"]
    upcoming = [g for g in _nfl_schedule() if g.get("season") == season and g.get("game_type") == "REG"
                and g.get("home_score") in ("", "NA")]
    if not upcoming:
        return {}, "no games left"
    week = min(int(g["week"]) for g in upcoming)
    for g in upcoming:
        if int(g["week"]) == week:
            slate[g["home_team"]], slate[g["away_team"]] = (g["away_team"], True), (g["home_team"], False)
    return slate, f"week {week}"


# ---------------------------------------------------------------------------
# Finding consistent lines
# ---------------------------------------------------------------------------

def _floor(sport, position, stat):
    return SCAN_FLOOR.get((sport, position, stat), SCAN_FLOOR.get((sport, "*", stat), 1))


def _best_line(values, step, floor, min_hit):
    """The highest line (a multiple of `step`, at least `floor`) he reached in at least `min_hit`
    of the games. Returns (line, hits, hit rate) or None."""
    n = len(values)
    best = None
    line = max(step, -(-floor // step) * step)
    while line <= max(values):
        hits = sum(1 for v in values if v >= line)
        if hits / n >= min_hit - 1e-9:
            best = (line, hits, hits / n)
        else:
            break
        line += step
    return best


def _short_name(name):
    parts = name.split()
    return name if len(parts) < 2 else f"{parts[0][0]}.{' '.join(parts[1:])}"


def _window(games):
    """games=5, 10 (or any number) or "season" -> (how many games, or None for all; description)."""
    if isinstance(games, str) and games.strip().lower() == "season":
        return None, "season"
    if isinstance(games, bool) or not isinstance(games, int) or games < 1:
        raise ValueError('games must be a number like 5 or 10, or "season"')
    return games, f"last {games} games"


def _candidates(sport, size, min_hit, min_games, teams=None):
    """Every player (on `teams`, or all) with a line he reached in at least min_hit of his
    games in the window: dicts with name, team, position, stat, line, hits, n, rate, avg, low."""
    rows = _rows(sport, "player_games")
    # Each team's two most recent game dates, to skip players who've been out
    team_dates = {}
    for r in rows:
        team_dates.setdefault(r["team"], set()).add(r["game_date"])
    recent = {t: sorted(d)[-2] if len(d) > 1 else sorted(d)[-1] for t, d in team_dates.items()}

    players = {}
    for r in rows:
        players.setdefault(r["player_id"], []).append(r)
    found = []
    for played in players.values():
        played.sort(key=lambda r: (r["game_date"], r["game_id"]))
        latest = played[-1]
        position, team = latest["position"], latest["team"]
        if teams is not None and team not in teams:
            continue
        if latest["game_date"] < recent.get(team, ""):
            continue                                   # missed his team's last two games
        if sport == "nhl":
            stats = SCAN_STATS["nhl"]["G" if position == "G" else "*"]
        else:
            stats = SCAN_STATS[sport].get(position) or SCAN_STATS[sport].get("*", [])
        for stat in stats:
            pool = played
            if sport == "nhl" and position == "G":
                pool = [g for g in played if g.get("starter") == "1"]
            window = pool if size is None else pool[-size:]
            values = [_number(_value(sport, g, stat)) or 0.0 for g in window]
            if len(values) < min_games:
                continue
            best = _best_line(values, SCAN_STEP.get(stat, 1), _floor(sport, position, stat), min_hit)
            if best is None:
                continue
            line, hits, rate = best
            average = sum(values) / len(values)
            found.append({"name": latest["player_name"], "team": team, "position": position,
                          "stat": stat, "line": line, "hits": hits, "n": len(values), "rate": rate,
                          "avg": average, "low": min(values), "cushion": average / line,
                          "pool": pool})
    found.sort(key=lambda c: (-c["rate"], -c["cushion"], c["name"]))
    return found


def _prop_lines(c):
    line = c["line"]
    lines = [f"{c['rate']:.0%} {_short_name(c['name'])} ({c['position']}, {c['team']})"]
    if c.get("injury"):
        lines.append(f"  ! {c['injury']}")
    return lines + [f"  {_SCAN_NAMES.get(c['stat'], c['stat'])} {line:g}+ (over {line - 0.5:g})",
                    f"  hit {c['hits']}/{c['n']} | avg {c['avg']:.1f} | low {c['low']:g}"]


def _log_lines(c, log):
    """The prop's stat in his last `log` games, oldest first: opponent and value, with * on
    the games he fell short of the line. Laid out in columns that fit the screen."""
    if not log:
        return []
    recent = c["pool"][-log:]
    if not recent:
        return []
    opponents, values = [], []
    for g in recent:
        value = _number(g.get(c["stat"])) or 0.0
        opponents.append(("" if g["home"] == "1" else "@") + g["opponent"])
        values.append(_cell(value) + ("*" if value < c["line"] - 1e-9 else " "))
    opp_w = max(len(o) for o in opponents)
    val_w = max(len(v) for v in values)
    cells = [f"{o.rjust(opp_w)} {v.rjust(val_w)}" for o, v in zip(opponents, values)]
    per_line = max(1, (min(_width(), 40) - 2 + 1) // (len(cells[0]) + 1))
    lines = [f"  last {len(recent)} (oldest first, * = missed):"]
    for i in range(0, len(cells), per_line):
        lines.append(("  " + " ".join(cells[i:i + per_line])).rstrip())
    return lines


def _scan(sport, games, min_hit, top, team, day, all_teams, min_games, log=10):
    size, span = _window(games)
    day = day or _today()
    slate, when = None, "all teams"
    if team:
        wanted = {t.strip().upper() for t in ([team] if isinstance(team, str) else team)}
        slate = {t: None for t in wanted}
        when = ", ".join(sorted(wanted))
    elif not all_teams:
        try:
            slate, when = _slate(sport, day)
        except Exception as e:
            print(f"(Couldn't load the schedule: {e}. Scanning every team.)")
            slate = None
        if slate is not None and not slate:
            print(f"No {sport.upper()} games found for {when}. Use all_teams=True to scan everyone.")
            return
    found = _candidates(sport, size, min_hit, min_games, set(slate) if slate is not None else None)
    found, dropped, injury_note = _apply_injuries(sport, found)

    print(f"{sport.upper()} scan | {when}")
    print(f"{span} | {min_hit:.0%}+ hit rate")
    print(injury_note)
    if dropped:
        print("Out, left off: " + ", ".join(_short_name(n) for n, _ in dropped))
    if not found:
        print("Nothing clears the bar. Try min_hit=0.75 or a different games= window.")
        return
    for c in found[:top]:
        lines = _prop_lines(c)
        opponent = slate.get(c["team"]) if slate else None
        if opponent:
            lines[0] += f" {'vs' if opponent[1] else '@'} {opponent[0]}"
        print("\n".join(lines + _log_lines(c, log)))
    if len(found) > top:
        print(f"({top} of {len(found)} shown; use top={len(found)} for all)")
    if sport == "nhl" and any(c["position"] == "G" for c in found[:top]):
        print("Goalie lines use only games he started: confirm he's starting tonight.")


@_paged
def nfl_scan(games=10, min_hit=0.80, top=25, team=None, all_teams=False, min_games=5, log=10):
    """
    Consistent NFL player props for the coming week: for each player, the highest line he
    reached in at least min_hit of his games (default 80%: 8 of 10, 4 of 5).
    QB: pass yards, pass TDs, rush yards. RB: rush att, rush yards, receptions, rec yards.
    WR/TE: receptions, rec yards.
    nfl_scan()                     this week's games, his last 10 games, 80%+ hit rate
    nfl_scan(games=5)              his last 5 games
    nfl_scan(games="season")       every game this season
    nfl_scan(team="BUF")           one team (or a list: team=["BUF", "KC"])
    nfl_scan(min_hit=0.9, top=50)  stricter, longer list
    nfl_scan(all_teams=True)       every team, playing this week or not
    min_games: players need at least this many games in the window (default 5)
    log: how many of his recent games to list under each prop (default 10; 0 hides it)
    """
    _scan("nfl", games, min_hit, top, team, None, all_teams, min_games, log)


@_paged
def nba_scan(games=10, min_hit=0.80, top=25, team=None, day=None, all_teams=False, min_games=5,
             log=10):
    """
    Consistent NBA player props for today's games: points, rebounds and assists, each on its
    own. day=datetime.date(2026, 11, 20) for another day. Other options as in nfl_scan.
    """
    _scan("nba", games, min_hit, top, team, day, all_teams, min_games, log)


@_paged
def nhl_scan(games=10, min_hit=0.80, top=25, team=None, day=None, all_teams=False, min_games=5,
             log=10):
    """
    Consistent NHL player props for today's games: shots on goal for skaters, saves for goalies
    (goalies are judged only on games they started). Options as in nba_scan.
    """
    _scan("nhl", games, min_hit, top, team, day, all_teams, min_games, log)


# ---------------------------------------------------------------------------
# Injuries (ESPN's injury reports, checked live)
# ---------------------------------------------------------------------------

_ESPN_PATH = {"nfl": "football/nfl", "nba": "basketball/nba", "nhl": "hockey/nhl"}
# ESPN team code -> the code in our files, where they differ (NBA files already use ESPN's)
_ESPN_TO_OURS = {"nfl": {"LAR": "LA", "WSH": "WAS"},
                 "nhl": {"TB": "TBL", "NJ": "NJD", "LA": "LAK", "SJ": "SJS", "UTAH": "UTA"},
                 "nba": {}}
INJURY_REFRESH_MINUTES = 10
_injury_cache = {}


def _person(name):
    """A name reduced for matching: no accents, punctuation or suffixes like Jr. / III."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    words = re.sub(r"[.'\u2019\-]", "", text.lower()).split()
    return " ".join(w for w in words if w not in ("jr", "sr", "ii", "iii", "iv", "v"))


def _is_out(status):
    s = (status or "").lower()
    return (s == "out" or s.startswith("out ") or "injured reserve" in s or s == "ir"
            or "suspen" in s or "reserve" in s or "unable to perform" in s)


def _injury_report(sport):
    """Every player on ESPN's injury report for the league: (list of dicts, Eastern time checked).
    Kept for INJURY_REFRESH_MINUTES, then fetched again."""
    cached = _injury_cache.get(sport)
    if cached and time.time() - cached[0] < INJURY_REFRESH_MINUTES * 60:
        return cached[1], cached[2]
    path = _ESPN_PATH[sport]
    by_id, by_name = {}, {}
    try:
        data = _get_json(f"https://site.api.espn.com/apis/site/v2/sports/{path}/teams")
        for league in (data.get("sports") or [{}])[0].get("leagues", []):
            for item in league.get("teams", []):
                t = item.get("team", item)
                by_id[str(t.get("id"))] = t.get("abbreviation")
                by_name[t.get("displayName")] = t.get("abbreviation")
    except Exception:
        pass                                    # team codes then come from the report itself
    data = _get_json(f"https://site.api.espn.com/apis/site/v2/sports/{path}/injuries")
    mapping = _ESPN_TO_OURS[sport]
    report = []
    for block in data.get("injuries", []):
        block_code = (block.get("abbreviation") or (block.get("team") or {}).get("abbreviation")
                      or by_id.get(str(block.get("id"))) or by_name.get(block.get("displayName")))
        for item in block.get("injuries", []):
            athlete = item.get("athlete") or {}
            code = (athlete.get("team") or {}).get("abbreviation") or block_code or ""
            details = item.get("details") or {}
            injury = " ".join(x for x in (details.get("side") if details.get("side") not in (None, "Not Specified") else None,
                                          details.get("type"), details.get("detail")) if x)
            status = item.get("status") or (item.get("type") or {}).get("description") or "Listed"
            report.append({
                "name": athlete.get("displayName") or athlete.get("fullName") or "",
                "team": mapping.get(code, code),
                "position": (athlete.get("position") or {}).get("abbreviation", ""),
                "status": status,
                "out": _is_out(status),
                "injury": injury.lower(),
                "return": (details.get("returnDate") or "")[:10],
                "comment": item.get("shortComment") or item.get("longComment") or "",
            })
    checked = _eastern(datetime.datetime.now(datetime.timezone.utc))
    _injury_cache[sport] = (time.time(), report, checked)
    return report, checked


def _injury_for(sport, name, team, report):
    """The report entry for this player, or None. Name first; team breaks ties."""
    key = _person(name)
    matches = [r for r in report if _person(r["name"]) == key]
    if len(matches) > 1:
        matches = [r for r in matches if r["team"] == team] or matches[:1]
    return matches[0] if matches else None


def _status_text(entry):
    text = entry["status"]
    if entry["injury"]:
        text += f" ({entry['injury']})"
    return text


def _apply_injuries(sport, found):
    """Mark each prop with its player's injury status and drop players who are out.
    Returns (kept props, [(name, team)] of dropped players, note about the check)."""
    try:
        report, checked = _injury_report(sport)
    except Exception as e:
        return found, [], f"{sport.upper()} injuries couldn't be checked ({e})"
    kept, dropped = [], []
    for c in found:
        entry = _injury_for(sport, c["name"], c["team"], report)
        if entry and entry["out"]:
            if (c["name"], c["team"]) not in dropped:
                dropped.append((c["name"], c["team"]))
            continue
        c["injury"] = _status_text(entry) if entry else ""
        kept.append(c)
    return kept, dropped, f"{sport.upper()} injuries checked {_clock(checked)}"


def _injury_line(sport, name, team):
    """A status line for the player tables ('' if he isn't on the report)."""
    try:
        report, _ = _injury_report(sport)
    except Exception:
        return "(injury report unavailable)"
    entry = _injury_for(sport, name, team, report)
    if not entry:
        return ""
    lines = [f"Status: {_status_text(entry)}"]
    if entry["return"]:
        lines.append(f"Est. return: {entry['return'][5:7].lstrip('0')}/{entry['return'][8:10].lstrip('0')}")
    if entry["comment"]:
        lines += textwrap.wrap(entry["comment"], max(30, _width() - 2))
    return "\n".join(lines)


def _injuries(sport, team):
    try:
        report, checked = _injury_report(sport)
    except Exception as e:
        print(f"Couldn't load the {sport.upper()} injury report ({e}).")
        return
    if team:
        want = team.strip().upper()
        report = [r for r in report if r["team"] == want]
        if not report:
            print(f"Nobody from {want} is on the {sport.upper()} injury report.")
            print("(Use the team code from your files, e.g. nba_injuries('NY').)")
            return
    order = {True: 0, False: 1}
    report = sorted(report, key=lambda r: (r["team"], order[r["out"]], r["status"], r["name"]))
    print(f"{sport.upper()} injury report | checked {_clock(checked)}")
    current = None
    for r in report:
        if r["team"] != current:
            current = r["team"]
            print(f"\n{current}")
        print(f"  {r['name']} ({r['position']})" if r["position"] else f"  {r['name']}")
        print(f"    {_status_text(r)}")
        if r["return"]:
            print(f"    Est. return: {r['return'][5:7].lstrip('0')}/{r['return'][8:10].lstrip('0')}")
        if r["comment"] and team:
            for line in textwrap.wrap(r["comment"], max(30, _width() - 4)):
                print(f"    {line}")


# Who counts as a regular for the injury summaries
REGULAR_NBA_MINUTES = 15
REGULAR_NHL_MINUTES = 12
RECENT_TEAM_GAMES = 3
_STATUS_ORDER = ["Out", "Doubtful", "Questionable", "Day-To-Day"]
_roles_cache = {}


def _roles(sport):
    """{(name key, team): {"regular": bool, "recent": bool}} from this season's games.
    recent = played in one of his team's last RECENT_TEAM_GAMES games."""
    if sport in _roles_cache:
        return _roles_cache[sport]
    rows = _rows(sport, "player_games")
    team_dates = {}
    for r in rows:
        team_dates.setdefault(r["team"], set()).add(r["game_date"])
    cutoff = {t: sorted(d)[-RECENT_TEAM_GAMES] if len(d) >= RECENT_TEAM_GAMES else min(d)
              for t, d in team_dates.items()}
    players = {}
    for r in rows:
        players.setdefault(r["player_id"], []).append(r)
    roles = {}
    for games in players.values():
        games.sort(key=lambda r: (r["game_date"], r["game_id"]))
        latest = games[-1]
        team = latest["team"]
        if sport == "nba":
            minutes = [_number(g.get("minutes")) or 0.0 for g in games]
            regular = sum(minutes) / len(minutes) >= REGULAR_NBA_MINUTES
        elif sport == "nhl":
            minutes = [_number(g.get("toi")) or 0.0 for g in games]
            regular = latest["position"] == "G" or sum(minutes) / len(minutes) >= REGULAR_NHL_MINUTES
        else:
            regular = True
        roles[(_person(latest["player_name"]), team)] = {
            "regular": regular, "recent": latest["game_date"] >= cutoff.get(team, "")}
    _roles_cache[sport] = roles
    return roles


def _status_group(entry):
    if entry["out"]:
        return "Out"
    for label in _STATUS_ORDER:
        if entry["status"].lower() == label.lower():
            return label
    return entry["status"]


def _injury_summary(sport, teams, report):
    """A few lines naming each team's injured regulars, grouped by status."""
    roles = _roles(sport)
    lines = ["Injuries (regulars):"]
    for team in teams:
        groups = {}
        for e in report:
            if e["team"] != team:
                continue
            role = roles.get((_person(e["name"]), team))
            if not role or not role["regular"]:
                continue
            if e["out"] and not role["recent"]:
                continue                        # long-term absence: already in the recent stats
            groups.setdefault(_status_group(e), []).append(_short_name(e["name"]))
        order = sorted(groups, key=lambda g: (_STATUS_ORDER + [g]).index(g))
        for n, group in enumerate(order):
            first = f"  {team:<4} " if n == 0 else " " * 7
            lines += textwrap.wrap(f"{group}: " + ", ".join(groups[group]), 38,
                                   initial_indent=first, subsequent_indent=" " * 9)
    if len(lines) == 1:
        return ["Injuries: none among regulars"]
    return lines


def _injury_summary_or_note(sport, teams):
    try:
        report, _ = _injury_report(sport)
        return _injury_summary(sport, teams, report)
    except Exception:
        return ["Injuries: report unavailable"]


@_paged
def nfl_injuries(team=None):
    """The NFL injury report: nfl_injuries() for the league, nfl_injuries("BUF") for one team
    (with ESPN's notes). Statuses: Out, Doubtful, Questionable, Injured Reserve ..."""
    _injuries("nfl", team)


@_paged
def nba_injuries(team=None):
    """The NBA injury report: nba_injuries() for the league, nba_injuries("NY") for one team."""
    _injuries("nba", team)


@_paged
def nhl_injuries(team=None):
    """The NHL injury report: nhl_injuries() for the league, nhl_injuries("NYR") for one team.
    It doesn't say who starts in goal: that's confirmed close to game time."""
    _injuries("nhl", team)


# ---------------------------------------------------------------------------
# Standings (official, checked live)
# ---------------------------------------------------------------------------

def _pct(value):
    """0.706 -> '.706', 1.0 -> '1.000'."""
    if value is None:
        return "-"
    text = f"{value:.3f}"
    return text[1:] if text.startswith("0") else text


def _espn_standings(sport):
    """NFL / NBA standings from ESPN: one dict per team, with its conference."""
    data = _get_json(f"https://site.api.espn.com/apis/v2/sports/{_ESPN_PATH[sport]}/standings")
    groups = []

    def walk(node, conference):
        name = node.get("name") or ""
        if "conference" in name.lower():
            conference = (name, node.get("abbreviation") or "")
        entries = (node.get("standings") or {}).get("entries")
        if entries:
            # Teams belong to the conference above them, even if ESPN nests divisions in between
            conf_name, conf_abbr = conference if conference[0] else (name, node.get("abbreviation") or "")
            groups.append((conf_name, conf_abbr, entries))
        for child in node.get("children") or []:
            walk(child, conference)
    walk(data, ("", ""))
    teams = []
    for name, abbreviation, entries in groups:
        for e in entries:
            stats = {x.get("name"): x for x in e.get("stats", [])}

            def value(key):
                return stats.get(key, {}).get("value")

            def shown(key):
                return stats.get(key, {}).get("displayValue")
            code = e["team"]["abbreviation"]
            w, l, t = int(value("wins") or 0), int(value("losses") or 0), int(value("ties") or 0)
            games = w + l + t
            pct = value("winPercent")
            if pct is None:
                pct = (w + t / 2) / games if games else 0.0
            teams.append({
                "team": _ESPN_TO_OURS[sport].get(code, code), "conference": name,
                "conf_abbr": abbreviation, "w": w, "l": l, "t": t, "pct": pct,
                "gb": shown("gamesBehind") or "-", "streak": shown("streak") or "-",
                "seed": value("playoffSeed"),
                "pf": value("pointsFor"), "pa": value("pointsAgainst"),
            })
    if len(teams) < 30:
        raise RuntimeError(f"ESPN returned only {len(teams)} teams")
    return teams


def _nhl_standings():
    data = _get_json("https://api-web.nhle.com/v1/standings/now")
    teams = []
    for x in data.get("standings", []):
        conference = x.get("conferenceName") or ""
        streak = f"{x.get('streakCode') or ''}{x.get('streakCount') or ''}" or "-"
        l10 = None
        if x.get("l10Wins") is not None:
            l10 = f"{x.get('l10Wins')}-{x.get('l10Losses')}-{x.get('l10OtLosses')}"
        teams.append({
            "team": (x.get("teamAbbrev") or {}).get("default", ""),
            "conference": conference if "conference" in conference.lower() else f"{conference} Conference",
            "conf_abbr": conference[:4], "gp": x.get("gamesPlayed") or 0,
            "w": x.get("wins") or 0, "l": x.get("losses") or 0, "otl": x.get("otLosses") or 0,
            "pts": x.get("points") or 0, "pct": x.get("pointPctg"), "gd": x.get("goalDifferential"),
            "streak": streak, "l10": l10,
            "conf_seq": x.get("conferenceSequence"), "league_seq": x.get("leagueSequence"),
        })
    if len(teams) < 30:
        raise RuntimeError(f"the NHL returned only {len(teams)} teams")
    return teams


def _table(header, rows, left=(1,)):
    """Columns sized to their contents: numbers right-aligned, columns in `left` left-aligned."""
    widths = [max(len(str(r[i])) for r in [header] + rows) for i in range(len(header))]
    lines = []
    for r in [header] + rows:
        cells = [str(c).ljust(w) if i in left else str(c).rjust(w) for i, (c, w) in enumerate(zip(r, widths))]
        lines.append(" ".join(cells).rstrip())
    return lines


def _standings_rows(sport, teams):
    if sport == "nhl":
        header = ["#", "Team", "GP", "W-L-OT", "Pts", "P%", "GD", "Strk"]
        rows = [[i, t["team"], t["gp"], f"{t['w']}-{t['l']}-{t['otl']}", t["pts"], _pct(t["pct"]),
                 f"{t['gd']:+d}" if isinstance(t["gd"], int) else "-", t["streak"]]
                for i, t in enumerate(teams, 1)]
    elif sport == "nfl":
        header = ["#", "Team", "W-L-T", "Pct", "PF", "PA", "Strk"]
        rows = [[i, t["team"], f"{t['w']}-{t['l']}-{t['t']}", _pct(t["pct"]),
                 int(t["pf"]) if t["pf"] is not None else "-", int(t["pa"]) if t["pa"] is not None else "-",
                 t["streak"]] for i, t in enumerate(teams, 1)]
    else:
        header = ["#", "Team", "W-L", "Pct", "GB", "Strk"]
        rows = [[i, t["team"], f"{t['w']}-{t['l']}", _pct(t["pct"]), t["gb"], t["streak"]]
                for i, t in enumerate(teams, 1)]
    return _table(header, rows)


def _section(title, lines):
    return ["", "=" * 34, title, "=" * 34] + lines


def _standings(sport, view):
    want = _normalize(view) if view else ""
    try:
        teams = _nhl_standings() if sport == "nhl" else _espn_standings(sport)
        checked = _eastern(datetime.datetime.now(datetime.timezone.utc))
    except Exception as e:
        _standings_from_files(sport, e)
        return
    if sport == "nhl":
        overall = sorted(teams, key=lambda t: (t["league_seq"] or 99, -t["pts"], -(t["pct"] or 0)))
        note = "Sorted by points (league order)."
    else:
        overall = sorted(teams, key=lambda t: (-t["pct"], -t["w"], t["team"]))
        note = "Overall sorted by win %."
    conferences = []
    for name in sorted({t["conference"] for t in teams if t["conference"]}):
        members = [t for t in teams if t["conference"] == name]
        if sport == "nhl":
            members.sort(key=lambda t: (t["conf_seq"] or 99, -t["pts"]))
        else:
            members.sort(key=lambda t: (t["seed"] if t["seed"] else 99, -t["pct"], -t["w"]))
        conferences.append((name, members))

    out = [f"{sport.upper()} standings | checked {_clock(checked)}"]
    show_overall = want in ("", "overall", "league", "all")
    shown = 0
    if show_overall:
        out += _section("League", _standings_rows(sport, overall))
        shown += 1
    for name, members in conferences:
        abbreviation = _normalize(members[0].get("conf_abbr") or "")
        alias = {"afc": "american", "nfc": "national"}.get(want, want)
        if want and not show_overall and alias not in _normalize(name) and want != abbreviation:
            continue
        if want in ("overall", "league"):
            continue
        out += _section(name, _standings_rows(sport, members))
        shown += 1
    if shown == 0:
        names = ", ".join(n for n, _ in conferences)
        print(f"No {sport.upper()} conference matches '{view}'. Conferences: {names}")
        return
    out += ["", note]
    if sport != "nhl":
        out.append("Conferences in playoff-seed order.")
    if sport == "nba":
        out.append("GB = games behind the leader.")
    if sport == "nhl":
        out.append("P% = points %, GD = goal difference.")
    print("\n".join(out))


def _standings_from_files(sport, error):
    """Overall standings from the published game files, when the live standings can't load."""
    by_team = {}
    for r in _rows(sport, "team_games"):
        by_team.setdefault(r["team"], []).append(r)
    rows = []
    for team, games in by_team.items():
        counts = {}
        for g in games:
            counts[g["result"]] = counts.get(g["result"], 0) + 1
        w, l = counts.get("W", 0), counts.get("L", 0)
        other = counts.get("OTL", 0) + counts.get("T", 0)
        points = 2 * w + other if sport == "nhl" else None
        pct = (w + (other / 2 if sport == "nfl" else 0)) / len(games) if games else 0.0
        rows.append((team, w, l, other, pct, points, len(games)))
    if sport == "nhl":
        rows.sort(key=lambda r: (-r[5], r[6], r[0]))
        table = _table(["#", "Team", "GP", "W-L-OT", "Pts"],
                       [[i, r[0], r[6], f"{r[1]}-{r[2]}-{r[3]}", r[5]] for i, r in enumerate(rows, 1)])
    else:
        rows.sort(key=lambda r: (-r[4], -r[1], r[0]))
        record = (lambda r: f"{r[1]}-{r[2]}-{r[3]}") if sport == "nfl" else (lambda r: f"{r[1]}-{r[2]}")
        table = _table(["#", "Team", "W-L-T" if sport == "nfl" else "W-L", "Pct"],
                       [[i, r[0], record(r), _pct(r[4])] for i, r in enumerate(rows, 1)])
    print(f"Live {sport.upper()} standings couldn't load ({error}).")
    print("Overall records from your data files")
    print("(no conferences or tiebreakers):")
    print("\n".join(_section("League", table)))


@_paged
def nfl_standings(view=None):
    """NFL standings: nfl_standings() for the league and both conferences,
    nfl_standings("afc") or ("nfc") for one conference, nfl_standings("overall") for the league."""
    _standings("nfl", view)


@_paged
def nba_standings(view=None):
    """NBA standings: nba_standings() for the league and both conferences,
    nba_standings("east") or ("west") for one conference, nba_standings("overall") for the league."""
    _standings("nba", view)


@_paged
def nhl_standings(view=None):
    """NHL standings: nhl_standings() for the league and both conferences,
    nhl_standings("east") or ("west") for one conference, nhl_standings("overall") for the league."""
    _standings("nhl", view)


# ---------------------------------------------------------------------------
# Player leaderboards
# ---------------------------------------------------------------------------

LEADER_STATS = {
    "nfl": ["passing_yards", "passing_tds", "rushing_yards", "rushing_tds", "receptions",
            "receiving_yards"],
    "nba": ["pts", "reb", "ast"],
    "nhl": ["saves", "shots"],
}
_GOALIE_STATS = {"saves", "shots_against", "goals_against"}


def _leaders(sport, stat, top, games, min_games, team, position, hit=0.80, window=10,
             totals=False):
    size, span = _window(games)
    line_size, line_span = _window(window)
    rows = _rows(sport, "player_games")
    if stat is None:
        stats = LEADER_STATS[sport]
    else:
        stats = _resolve_stats(sport, "player_games", [stat] if isinstance(stat, str) else list(stat))
    teams = None
    if team:
        teams = {t.strip().upper() for t in ([team] if isinstance(team, str) else team)}
    positions = None
    if position:
        items = [position] if isinstance(position, str) else list(position)
        positions = {p.strip().upper() for item in items for p in str(item).replace("/", ",").split(",") if p.strip()}

    team_games = {}
    for r in rows:
        team_games.setdefault(r["team"], set()).add(r["game_id"])
    players = {}
    for r in rows:
        players.setdefault(r["player_id"], []).append(r)
    for played in players.values():
        played.sort(key=lambda r: (r["game_date"], r["game_id"]))

    rule = f"min {min_games} games" if min_games is not None else "min half his team's games"
    filters = []
    if teams:
        filters.append(", ".join(sorted(teams)))
    if positions:
        filters.append("/".join(sorted(positions)))
    out = [f"{sport.upper()} leaders | {span}", rule + (f" | {' | '.join(filters)}" if filters else ""),
           f"{hit:.0%} col: line hit {hit:.0%}+ of {line_span}"]
    for st in stats:
        board = []
        for played in players.values():
            latest = played[-1]
            if teams and latest["team"] not in teams:
                continue
            if positions and (latest["position"] or "").upper() not in positions:
                continue
            if sport == "nhl" and (latest["position"] == "G") != (st in _GOALIE_STATS):
                continue                            # goalie boards: goalies; others: skaters
            window = played if size is None else played[-size:]
            possible = len(team_games.get(latest["team"], ()))
            if size is not None:
                possible = min(size, possible)
            needed = min_games if min_games is not None else max(1, possible // 2)
            if len(window) < needed:
                continue
            values = [_number(_value(sport, g, st)) or 0.0 for g in window]
            total = sum(values)
            if total == 0:
                continue
            pool = played
            if sport == "nhl" and latest["position"] == "G":
                pool = [g for g in played if g.get("starter") == "1"]   # goalies: starts only
            pool = pool if line_size is None else pool[-line_size:]
            board.append({"name": latest["player_name"], "position": latest["position"] or "-",
                          "team": latest["team"], "gp": len(window), "avg": total / len(window),
                          "total": total,
                          "line": _hit_line([_number(_value(sport, g, st)) or 0.0 for g in pool], hit)})
        board.sort(key=lambda b: (-b["avg"], -b["total"], b["name"]))
        title = f"{_label(sport, st)} per game"
        if not board:
            out += _section(title, ["  nobody qualifies"])
            continue
        header = ["#", "Player", "Pos", "Team", "GP", "Per G"] + (["Total"] if totals else []) + [f"{hit:.0%}"]
        table = _table(header,
                       [[i, _short_name(b["name"])[:14], b["position"], b["team"], b["gp"],
                         f"{b['avg']:.2f}" if b["avg"] < 2 else f"{b['avg']:.1f}"]
                        + ([_cell(b["total"])] if totals else []) + [b["line"]]
                        for i, b in enumerate(board[:top], 1)],
                       left=(1, 2, 3))
        out += _section(title, table)
    print("\n".join(out))


@_paged
def nfl_leaders(stat=None, top=10, games="season", min_games=None, team=None, position=None,
                hit=0.80, window=10, totals=False):
    """
    NFL per-game leaders: pass yards, pass TDs, rush yards, rush TDs, receptions, rec yards.
    nfl_leaders()                  every board, top 10, this season
    nfl_leaders("rec yds")         one board (readable or column name; see nfl_columns())
    nfl_leaders(top=25, games=5)   top 25 over each player's last 5 games
    nfl_leaders(position="WR,TE"), nfl_leaders(team="BUF")
    min_games: games needed to qualify (default: half his team's games)
    The last column is the 80% line: the highest number he reached in at least `hit` (80%) of
    his last `window` (10) games. hit=0.9, window=5 or window="season" change it.
    totals=True adds a Total column (wider than a phone screen).
    """
    _leaders("nfl", stat, top, games, min_games, team, position, hit, window, totals)


@_paged
def nba_leaders(stat=None, top=10, games="season", min_games=None, team=None, position=None,
                hit=0.80, window=10, totals=False):
    """NBA per-game leaders: points, rebounds, assists. Options as in nfl_leaders()."""
    _leaders("nba", stat, top, games, min_games, team, position, hit, window, totals)


@_paged
def nhl_leaders(stat=None, top=10, games="season", min_games=None, team=None, position=None,
                hit=0.80, window=10, totals=False):
    """NHL per-game leaders: saves (goalies) and shots on goal (skaters). Options as in
    nfl_leaders(); position="D" for defensemen, "C,L,R" for forwards. Goalies' 80% line uses
    only games they started."""
    _leaders("nhl", stat, top, games, min_games, team, position, hit, window, totals)


# ---------------------------------------------------------------------------
# Line scan: today's players ranked by their 80% line
# ---------------------------------------------------------------------------

def _line_scan(sport, stat, window, hit, top, team, day, all_teams, min_games, order):
    size, span = _window(window)
    if order not in ("highest", "lowest"):
        raise ValueError('order must be "highest" or "lowest"')
    stats = (LEADER_STATS[sport] if stat is None
             else _resolve_stats(sport, "player_games", [stat] if isinstance(stat, str) else list(stat)))

    # Which teams: one team, today's (this week's) games, or everyone
    slate, when = None, "all teams"
    if team:
        slate = {t.strip().upper() for t in ([team] if isinstance(team, str) else team)}
        when = ", ".join(sorted(slate))
    elif not all_teams:
        try:
            games_slate, when = _slate(sport, day or _today())
            slate = set(games_slate)
        except Exception as e:
            print(f"(Couldn't load the schedule: {e}. Scanning every team.)")
        if slate is not None and not slate:
            print(f"No {sport.upper()} games found for {when}. Use all_teams=True to scan everyone.")
            return

    rows = _rows(sport, "player_games")
    team_dates = {}
    for r in rows:
        team_dates.setdefault(r["team"], set()).add(r["game_date"])
    recent = {t: sorted(d)[-2] if len(d) > 1 else sorted(d)[-1] for t, d in team_dates.items()}
    players = {}
    for r in rows:
        players.setdefault(r["player_id"], []).append(r)

    out = [f"{sport.upper()} line scan | {when}", f"{span} | {hit:.0%} line, {order} first"]
    for st in stats:
        board = []
        for played in players.values():
            played.sort(key=lambda r: (r["game_date"], r["game_id"]))
            latest = played[-1]
            if slate is not None and latest["team"] not in slate:
                continue
            if latest["game_date"] < recent.get(latest["team"], ""):
                continue                                   # missed his team's last two games
            if sport == "nhl" and (latest["position"] == "G") != (st in _GOALIE_STATS):
                continue
            pool = played
            if sport == "nhl" and latest["position"] == "G":
                pool = [g for g in played if g.get("starter") == "1"]
            window_games = pool if size is None else pool[-size:]
            if len(window_games) < min_games:
                continue
            values = [_number(_value(sport, g, st)) or 0.0 for g in window_games]
            average = sum(values) / len(values)
            if average < _floor(sport, latest["position"], st):
                continue                                   # too small a role to matter
            line = _hit_value(values, hit)
            if line is None or line <= 0:
                continue
            board.append({"name": latest["player_name"], "position": latest["position"] or "-",
                          "team": latest["team"], "avg": average, "line": line})
        sign = -1 if order == "highest" else 1
        board.sort(key=lambda b: (sign * b["line"], sign * b["avg"], b["name"]))
        title = _label(sport, st)
        if not board:
            out += _section(title, ["  nobody qualifies"])
            continue
        table = _table(["#", "Player", "Pos", "Team", "Avg", f"{hit:.0%}"],
                       [[i, _short_name(b["name"])[:14], b["position"], b["team"],
                         f"{b['avg']:.2f}" if b["avg"] < 2 else f"{b['avg']:.1f}",
                         _cell(b["line"]) + "+"]
                        for i, b in enumerate(board[:top], 1)],
                       left=(1, 2, 3))
        out += _section(title, table)
    out += ["", f"{hit:.0%} = the highest number he reached",
            f"in at least {hit:.0%} of the games: the line",
            "a bet would have needed to hit that",
            "often (10+ = an over 9.5 bet)."]
    print("\n".join(out))


@_paged
def nfl_line_scan(stat=None, window=10, hit=0.80, top=25, team=None, all_teams=False,
                  min_games=5, order="highest"):
    """
    Rank this week's NFL players by their 80% line for a prop (the highest number they reached
    in at least 80% of their last `window` games), highest first.
    nfl_line_scan("rec yds")            one stat (default: pass/rush/rec yards and TDs, receptions)
    nfl_line_scan("rec yds", order="lowest")
    nfl_line_scan("rec yds", window=5), window="season", hit=0.9, top=50, team="BUF",
    all_teams=True (everyone, playing this week or not)
    Players averaging below the stat's minimum line (SCAN_FLOOR) are left out.
    """
    _line_scan("nfl", stat, window, hit, top, team, None, all_teams, min_games, order)


@_paged
def nba_line_scan(stat=None, window=10, hit=0.80, top=25, team=None, day=None, all_teams=False,
                  min_games=5, order="highest"):
    """The line scan for today's NBA games (points, rebounds, assists). Options as in
    nfl_line_scan(); day=datetime.date(...) for another day."""
    _line_scan("nba", stat, window, hit, top, team, day, all_teams, min_games, order)


@_paged
def nhl_line_scan(stat=None, window=10, hit=0.80, top=25, team=None, day=None, all_teams=False,
                  min_games=5, order="highest"):
    """The line scan for today's NHL games (saves for goalies, using starts only; shots on goal
    for skaters). Options as in nfl_line_scan()."""
    _line_scan("nhl", stat, window, hit, top, team, day, all_teams, min_games, order)


# ---------------------------------------------------------------------------
# Records and matchups
# ---------------------------------------------------------------------------

def _records(sport):
    """{team: season record}, e.g. "9-6", "10-5-2" (NHL W-L-OTL), "8-2-1" (NFL with a tie)."""
    by_team = {}
    for r in _rows(sport, "team_games"):
        by_team.setdefault(r["team"], []).append(r)
    return {team: _average(sport, games, "result") for team, games in by_team.items()}


_GROUP_NAMES = {"G": "guards", "F": "forwards", "C": "centers"}
_STAT_UNITS = {"pts": "pts", "reb": "reb", "ast": "ast"}


def _nba_group(position):
    """ESPN positions (G, F, C, PG, SG, SF, PF, G-F ...) -> G, F or C."""
    position = (position or "").upper()
    if position.startswith("C"):
        return "C"
    if position.startswith("F") or position in ("SF", "PF"):
        return "F"
    return "G"


def _matchup_spec(sport, position, stat):
    """(how to measure, column, what it means, the side being judged, 'defense' or 'offense')."""
    if sport == "nfl":
        if position in ("RB", "FB"):
            return "team", "opp_rushing_yards", "rush yds/g", "run D", "defense"
        if stat == "passing_tds":
            return "team", "opp_passing_tds", "pass TDs/g", "pass D", "defense"
        return "team", "opp_passing_yards", "pass yds/g", "pass D", "defense"
    if sport == "nhl":
        if position == "TEAM":
            if stat == "goals_for":
                return "team", "goals_against", "goals/g allowed", "D", "defense"
            return "team", "shots_against", "shots/g allowed", "D", "defense"
        if position == "G":
            return "team", "shots_for", "shots/g", "offense", "offense"
        return "team", "shots_against", "shots/g", "D", "defense"
    group = _nba_group(position)
    return "position", stat, f"{_STAT_UNITS.get(stat, stat)}/g to {_GROUP_NAMES[group]}", "D", "defense"


class _Matchups:
    """Per-game averages for every team, built once per sport, to judge each prop's opponent."""

    def __init__(self, sport):
        self.sport = sport
        self.team_rows = {}
        for r in _rows(sport, "team_games"):
            self.team_rows.setdefault(r["team"], []).append(r)
        self.cache = {}
        self.position_tables = None

    def _team_table(self, column):
        if column not in self.cache:
            table = {}
            for team, games in self.team_rows.items():
                values = [_number(g.get(column)) for g in games]
                values = [v for v in values if v is not None]
                if values:
                    table[team] = sum(values) / len(values)
            self.cache[column] = table
        return self.cache[column]

    def _position_table(self, stat, group):
        """{defense: stat allowed per game to players of this position group} (NBA)."""
        if self.position_tables is None:
            totals, games = {}, {}
            for r in _rows(self.sport, "player_games"):
                defense = r["opponent"]
                games.setdefault(defense, set()).add(r["game_id"])
                key = (defense, _nba_group(r["position"]))
                bucket = totals.setdefault(key, {})
                for s in ("pts", "reb", "ast"):
                    bucket[s] = bucket.get(s, 0.0) + (_number(r.get(s)) or 0.0)
            self.position_tables = (totals, {d: len(g) for d, g in games.items()})
        totals, games = self.position_tables
        return {d: totals.get((d, group), {}).get(stat, 0.0) / n for d, n in games.items() if n}

    def measure(self, position, stat, opponent):
        """The matchup as numbers: {"value", "league", "rank", "teams", "change", "verdict",
        "unit", "side"}, or None if there isn't enough data."""
        how, column, unit, side, kind = _matchup_spec(self.sport, position, stat)
        table = (self._team_table(column) if how == "team"
                 else self._position_table(stat, _nba_group(position)))
        if opponent not in table or not table:
            return None
        value = table[opponent]
        league = sum(table.values()) / len(table)
        if kind == "defense":            # 1 = allows the least (toughest)
            rank = 1 + sum(1 for v in table.values() if v < value - 1e-9)
        else:                            # 1 = shoots the most
            rank = 1 + sum(1 for v in table.values() if v > value + 1e-9)
        change = value / league - 1 if league else 0.0
        verdict = "favorable" if change >= 0.05 else ("tough" if change <= -0.05 else "neutral")
        return {"value": value, "league": league, "rank": rank, "teams": len(table),
                "change": change, "verdict": verdict, "unit": unit, "side": side}

    def describe(self, position, stat, opponent):
        """Two lines judging the matchup for an over, or [] if there isn't enough data. Defense
        numbers are what the opponent allows per game; goalie matchups use the opponent's
        offense (shots on goal per game)."""
        how, column, unit, side, kind = _matchup_spec(self.sport, position, stat)
        table = (self._team_table(column) if how == "team"
                 else self._position_table(stat, _nba_group(position)))
        if opponent not in table or not table:
            return []
        value = table[opponent]
        league = sum(table.values()) / len(table)
        if kind == "defense":            # 1 = allows the least (toughest)
            rank = 1 + sum(1 for v in table.values() if v < value - 1e-9)
        else:                            # 1 = shoots the most
            rank = 1 + sum(1 for v in table.values() if v > value + 1e-9)
        change = value / league - 1 if league else 0.0
        if change >= 0.05:
            verdict = "favorable"
        elif change <= -0.05:
            verdict = "tough"
        else:
            verdict = "neutral"
        if abs(change) < 0.005:
            compared = "right at average"
        else:
            compared = f"{abs(change):.0%} {'more' if change > 0 else 'less'} than avg"
        amount = f"{value:.0f}" if value >= 100 else f"{value:.1f}"
        return [f"  {opponent} {side}: #{rank} of {len(table)}, {amount} {unit}",
                f"  {compared} -> {verdict}"]


# ---------------------------------------------------------------------------
# Team props (NHL): team shots on goal, team goals, game total shots
# ---------------------------------------------------------------------------

# Team stats checked per sport (columns in the team file), and game totals (both teams)
TEAM_SCAN_STATS = {"nhl": ["shots_for", "goals_for"]}
GAME_SCAN_STATS = {"nhl": ["game_shots"]}
# The lowest line worth showing for each
TEAM_SCAN_FLOOR = {("nhl", "shots_for"): 20, ("nhl", "goals_for"): 2, ("nhl", "game_shots"): 45}


def _game_value(row, stat):
    """A team-file row's value for a team or game stat."""
    if stat == "game_shots":
        a, b = _number(row.get("shots_for")), _number(row.get("shots_against"))
        return None if a is None or b is None else a + b
    return _number(row.get(stat))


def _team_props(sport, size, min_hit, min_games, game):
    """Consistent team and game-total props for one game: [dicts like the player props]."""
    if sport not in TEAM_SCAN_STATS:
        return []
    by_team = {}
    for r in _rows(sport, "team_games"):
        by_team.setdefault(r["team"], []).append(r)
    for rows in by_team.values():
        rows.sort(key=lambda r: (r["game_date"], r["game_id"]))
    found = []
    for team, opponent in ((game["away"], game["home"]), (game["home"], game["away"])):
        rows = by_team.get(team, [])
        window = rows if size is None else rows[-size:]
        for stat in TEAM_SCAN_STATS[sport]:
            values = [_game_value(r, stat) or 0.0 for r in window]
            if len(values) < min_games:
                continue
            best = _best_line(values, 1, TEAM_SCAN_FLOOR.get((sport, stat), 1), min_hit)
            if best is None:
                continue
            line, hits, rate = best
            average = sum(values) / len(values)
            found.append({"kind": "team", "team": team, "opponent": opponent, "stat": stat,
                          "line": line, "hits": hits, "n": len(values), "rate": rate, "avg": average,
                          "low": min(values), "pool": rows, "name": team, "position": "TEAM"})
    # Game totals: both teams' recent games together
    for stat in GAME_SCAN_STATS.get(sport, []):
        values = []
        for team in (game["away"], game["home"]):
            rows = by_team.get(team, [])
            window = rows if size is None else rows[-size:]
            values += [_game_value(r, stat) or 0.0 for r in window]
        if len(values) < 2 * min_games:
            continue
        best = _best_line(values, 1, TEAM_SCAN_FLOOR.get((sport, stat), 1), min_hit)
        if best is None:
            continue
        line, hits, rate = best
        found.append({"kind": "game", "team": f"{game['away']} @ {game['home']}", "stat": stat,
                      "line": line, "hits": hits, "n": len(values), "rate": rate,
                      "avg": sum(values) / len(values), "low": min(values)})
    order = {game["away"]: 0, game["home"]: 1}
    stats = TEAM_SCAN_STATS[sport]
    found.sort(key=lambda c: (c["kind"] == "game", order.get(c["team"], 2),
                              stats.index(c["stat"]) if c["stat"] in stats else 0))
    return found


def _team_prop_lines(c, matchups, log, span):
    line = c["line"]
    name = _SCAN_NAMES.get(c["stat"], c["stat"])
    if c["kind"] == "game":
        return [f"{c['rate']:.0%} game total",
                f"  {name} {line:g}+ (over {line - 0.5:g})",
                f"  hit {c['hits']}/{c['n']} | both teams' {span.replace(' games', '')}",
                f"  avg {c['avg']:.1f} | low {c['low']:g}"]
    lines = [f"{c['rate']:.0%} {c['team']} team",
             f"  {name} {line:g}+ (over {line - 0.5:g})",
             f"  hit {c['hits']}/{c['n']} | avg {c['avg']:.1f} | low {c['low']:g}"]
    lines += matchups.describe("TEAM", c["stat"], c["opponent"])
    if log:
        lines += _log_lines(c, log)
    return lines


@_paged
def nhl_team_scan(games=10, min_hit=0.80, team=None, day=None, log=10, min_games=5):
    """
    Consistent NHL team props for today's games: team shots on goal, team goals, and game total
    shots (both teams' recent games together), with the opponent's defense and a recent log.
    nhl_team_scan()                 today, last 10 games, 80%+ hit rate
    nhl_team_scan(games=5), games="season", min_hit=0.9, team="NYR", log=0
    day=datetime.date(...) for another day.
    """
    size, span = _window(games)
    day = day or _today()
    try:
        todays = _games_today("nhl", day)
    except Exception as e:
        print(f"Couldn't load today's NHL schedule ({e}).")
        return
    if team:
        wanted = team.strip().upper()
        todays = [g for g in todays if wanted in (g["home"], g["away"])]
    print(f"NHL team scan | {day.month}/{day.day}")
    print(f"{span} | {min_hit:.0%}+ hit rate")
    if not todays:
        print("No NHL games found.")
        return
    matchups = _Matchups("nhl")
    records = _records("nhl")
    for g in sorted(todays, key=lambda g: (g["start"] or datetime.datetime.max, g["away"])):
        out = ["", "=" * 34, f"{_clock(g['start'])} | NHL",
               f"{g['away']} ({records.get(g['away'], '0-0')}) @ {g['home']} ({records.get(g['home'], '0-0')})",
               "=" * 34]
        props = _team_props("nhl", size, min_hit, min_games, g)
        if not props:
            out.append("  nothing clears the bar")
        for c in props:
            out += _team_prop_lines(c, matchups, log, span)
        print("\n".join(out))


# ---------------------------------------------------------------------------
# Daily record: what was flagged, and the injury report, saved each run for backtesting
# ---------------------------------------------------------------------------

_FLAGGED_COLUMNS = ["recorded_utc", "date", "sport", "start_et", "away", "home", "kind", "team",
                    "opponent", "player", "position", "stat", "line", "hit_rate", "hits", "games",
                    "avg", "low", "matchup_value", "matchup_league", "matchup_rank", "matchup_teams",
                    "matchup_verdict", "injury_status", "listed"]
_INJURY_COLUMNS = ["recorded_utc", "date", "sport", "team", "player", "position", "status", "out",
                   "injury", "return_date", "comment"]


def _append_csv(path_in, path_out, columns, rows):
    """Copy the existing month file (if any) and add rows to it."""
    existing = []
    if path_in and os.path.exists(path_in):
        with open(path_in, encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
    with open(path_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing + rows)


def save_daily_record(out_dir, state_dir=None, games=10, min_hit=0.80, min_games=5, day=None):
    """
    Save what today's report flags, and the injury report for today's teams, to monthly CSV
    files (flagged_props_YYYY-MM.csv, injuries_YYYY-MM.csv) for backtesting. Each run adds its
    rows with a timestamp, so runs through the day show how injury news changed.
    Players listed as out are recorded too (listed = 0), since the report leaves them off.
    """
    size, _ = _window(games)
    day = day or _today()
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    flagged, injuries = [], []
    for sport in ("nfl", "nhl", "nba"):
        try:
            todays = _games_today(sport, day)
        except Exception as e:
            print(f"{sport}: schedule unavailable ({e})")
            continue
        if not todays:
            continue
        teams = {g["home"] for g in todays} | {g["away"] for g in todays}
        game_of = {}
        for g in todays:
            game_of[g["home"]] = (g, g["away"])
            game_of[g["away"]] = (g, g["home"])
        try:
            found = _candidates(sport, size, min_hit, min_games, teams)
            matchups = _Matchups(sport)
        except Exception as e:
            print(f"{sport}: couldn't build props ({e})")
            continue
        try:
            report, _ = _injury_report(sport)
        except Exception as e:
            print(f"{sport}: injury report unavailable ({e})")
            report = []
        for c in found:
            g, opponent = game_of[c["team"]]
            entry = _injury_for(sport, c["name"], c["team"], report) if report else None
            m = matchups.measure(c["position"], c["stat"], opponent) or {}
            flagged.append({
                "recorded_utc": now, "date": day.isoformat(), "sport": sport,
                "start_et": g["start"].strftime("%H:%M") if g["start"] else "", "away": g["away"],
                "home": g["home"], "kind": "player", "team": c["team"], "opponent": opponent,
                "player": c["name"], "position": c["position"], "stat": c["stat"], "line": c["line"],
                "hit_rate": round(c["rate"], 4), "hits": c["hits"], "games": c["n"],
                "avg": round(c["avg"], 3), "low": c["low"],
                "matchup_value": round(m["value"], 3) if m else "", "matchup_league": round(m["league"], 3) if m else "",
                "matchup_rank": m.get("rank", ""), "matchup_teams": m.get("teams", ""),
                "matchup_verdict": m.get("verdict", ""),
                "injury_status": _status_text(entry) if entry else "",
                "listed": 0 if entry and entry["out"] else 1})
        for g in todays:
            try:
                team_props = _team_props(sport, size, min_hit, min_games, g)
            except Exception:
                team_props = []
            for c in team_props:
                m = matchups.measure("TEAM", c["stat"], c["opponent"]) if c["kind"] == "team" else None
                flagged.append({
                    "recorded_utc": now, "date": day.isoformat(), "sport": sport,
                    "start_et": g["start"].strftime("%H:%M") if g["start"] else "", "away": g["away"],
                    "home": g["home"], "kind": c["kind"], "team": c["team"], "opponent": c.get("opponent", ""),
                    "player": "", "position": "", "stat": c["stat"], "line": c["line"],
                    "hit_rate": round(c["rate"], 4), "hits": c["hits"], "games": c["n"],
                    "avg": round(c["avg"], 3), "low": c["low"],
                    "matchup_value": round(m["value"], 3) if m else "", "matchup_league": round(m["league"], 3) if m else "",
                    "matchup_rank": m.get("rank", "") if m else "", "matchup_teams": m.get("teams", "") if m else "",
                    "matchup_verdict": m.get("verdict", "") if m else "", "injury_status": "", "listed": 1})
        for e in report:
            if e["team"] in teams:
                injuries.append({"recorded_utc": now, "date": day.isoformat(), "sport": sport,
                                 "team": e["team"], "player": e["name"], "position": e["position"],
                                 "status": e["status"], "out": int(e["out"]), "injury": e["injury"],
                                 "return_date": e["return"], "comment": e["comment"]})
    os.makedirs(out_dir, exist_ok=True)
    month = day.strftime("%Y-%m")
    for stem, columns, rows in ((f"flagged_props_{month}.csv", _FLAGGED_COLUMNS, flagged),
                                (f"injuries_{month}.csv", _INJURY_COLUMNS, injuries)):
        _append_csv(os.path.join(state_dir, stem) if state_dir else None,
                    os.path.join(out_dir, stem), columns, rows)
    print(f"Recorded {len(flagged)} flagged props and {len(injuries)} injury entries for {day}.")


# ---------------------------------------------------------------------------
# Today's props across every sport
# ---------------------------------------------------------------------------

@_paged
def daily(games=10, min_hit=0.80, min_games=5, per_game=8, day=None, log=10):
    """
    Consistent props for today's NFL, NBA and NHL games, in start-time order, grouped by game.
    Each game shows both teams' records; each prop shows his hit rate and how the opponent's
    defense (or, for goalies, offense) compares with the league.
    daily()                       last 10 games, 80%+ hit rate, up to 8 props per game
    daily(games=5), daily(games="season"), daily(min_hit=0.9), daily(per_game=20)
    daily(log=20)                 list his last 20 games under each prop (log=0 hides the list)
    day=datetime.date(2026, 11, 22) for another day.
    """
    size, span = _window(games)
    day = day or _today()
    entries, notes = [], []
    for sport in ("nfl", "nhl", "nba"):
        try:
            todays = _games_today(sport, day)
        except Exception as e:
            notes.append(f"{sport.upper()}: couldn't load today's schedule ({e})")
            continue
        if not todays:
            continue
        try:
            teams = {g["home"] for g in todays} | {g["away"] for g in todays}
            found = _candidates(sport, size, min_hit, min_games, teams)
            records = _records(sport)
            matchups = _Matchups(sport)
        except Exception as e:
            notes.append(f"{sport.upper()}: couldn't build props ({e})")
            continue
        found, dropped, injury_note = _apply_injuries(sport, found)
        notes.append(injury_note)
        for g in todays:
            props = [c for c in found if c["team"] in (g["home"], g["away"])]
            injuries = _injury_summary_or_note(sport, [g["away"], g["home"]])
            try:
                team_props = _team_props(sport, size, min_hit, min_games, g)
            except Exception as e:
                team_props = []
                notes.append(f"{sport.upper()}: couldn't build team props ({e})")
            entries.append((g, sport, props[:per_game], len(props), records, matchups, injuries,
                            team_props))

    entries.sort(key=lambda e: (e[0]["start"] or datetime.datetime.max, e[1], e[0]["away"], e[0]["home"]))
    out = [f"Props for {day.strftime('%a')} {day.month}/{day.day}", f"{span} | {min_hit:.0%}+ hit rate"]
    out += notes
    if not entries:
        out.append("No NFL, NBA or NHL games today." if not notes else "")
    goalie = False
    for g, sport, props, total, records, matchups, injuries, team_props in entries:
        away, home = g["away"], g["home"]
        out += ["", "=" * 34,
                f"{_clock(g['start'])} | {sport.upper()}",
                f"{away} ({records.get(away, '0-0')}) @ {home} ({records.get(home, '0-0')})",
                "=" * 34]
        out += injuries
        if team_props:
            out.append("Team props:")
            for c in team_props:
                out += _team_prop_lines(c, matchups, log, span)
            out.append("Player props:")
        if not props:
            out.append("  nothing clears the bar")
            continue
        for c in props:
            opponent = home if c["team"] == away else away
            out += _prop_lines(c) + matchups.describe(c["position"], c["stat"], opponent) + _log_lines(c, log)
            goalie = goalie or (sport == "nhl" and c["position"] == "G")
        if total > len(props):
            out.append(f"  (+{total - len(props)} more: use per_game={total})")
    if entries:
        out += ["", "Defense ranks: #1 allows the least.",
                "Goalie matchups rank the opponent's",
                "offense: #1 shoots the most.",
                "Verdicts are for the over: 5%+ above",
                "average is favorable, 5%+ below tough."]
        out += ["! marks a player on the injury",
                "report. Out players are left off.",
                "Injuries list regulars (NBA 15+ min,",
                "NHL skaters 12+ min and goalies,",
                "NFL players with stats): anyone",
                "Doubtful, Questionable or Day-To-Day,",
                "and Out players who played in one of",
                "their team's last 3 games."]
        if goalie:
            out += ["Goalie lines use only games he",
                    "started: confirm he's starting."]
    print("\n".join(out))


@_paged
def daily_report():
    """Print the props report the GitHub job built this morning (fast: nothing to work out)."""
    _download.cache_clear()
    try:
        report = _download("daily_props.txt")
    except RuntimeError as e:
        print(f"No published report yet ({e}). Run daily() to build one now.")
        return
    today = _today()
    if f" {today.month}/{today.day} " not in report.split("\n", 1)[0] + " ":
        print("(This report is from an earlier day. Run daily() for today's.)\n")
    print(report)


# ---------------------------------------------------------------------------
# Kalshi backtest report
# ---------------------------------------------------------------------------

@_paged
def kalshi_report():
    """
    The latest Kalshi NBA entry backtest (built by the "Kalshi NBA entry backtest" workflow):
    buying shortly before tip-off vs earlier in the day and in-game dips, profit after fees.
    One section per page; kalshi_report(save=True) saves it to a file.
    """
    url = f"https://github.com/{REPO}/releases/download/kalshi-analysis/analysis_report.txt"
    try:
        print(_get_text(url).rstrip("\n"))
    except Exception as e:
        print(f"No backtest report yet ({e}).")
        print("Run the Kalshi NBA entry backtest")
        print("workflow from the Actions tab first.")


# ---------------------------------------------------------------------------
# Built-in guide: guide() lists the topics, guide("daily") shows one
# ---------------------------------------------------------------------------

GUIDE = {}

GUIDE["start"] = ("Getting started", '''
GETTING STARTED

Start the tools in a-Shell:
  python3 -i sports_data.py

Then type any command, e.g.:
  daily_report()
  nfl("josh allen")

Quit with exit() or Ctrl-D.

Help on any topic (usage() works the
same as guide()):
  guide()               list of topics
  guide("daily")        one topic
  guide("all")          everything

Most used:
  daily_report()        this morning's
                        props report
  daily()               a fresh report
  nfl("josh allen")     a player
  nfl_team("BUF")       a team
  nba_standings()       standings
  nfl_leaders()         player leaders
  nba_scan()            today's props
  nba_injuries("NY")    a team's
                        injury report
  status()              data freshness
''')

GUIDE["report"] = ("daily_report(): this morning's report", '''
DAILY_REPORT()

Prints the props report the GitHub
job built at 5-6am Eastern: today's
NFL, NBA and NHL games in start-time
order, one game at a time. Fast:
nothing is worked out on the phone.

Usage:
  daily_report()
  daily_report(save=True)
  daily_report(page=False)

Options:
  page=True    one game per page
               (default). Return = next
               game, a = all, q = quit
  page=False   print it all at once
  save=True    save to a text file in
               a-Shell's folder instead
               (Files app > a-Shell >
               open, or Share > Notes)

Injury marks are from 5-6am. Close to
game time use daily() instead.

If the report is from an earlier day,
it says so at the top.

Output: the same as daily(); see
guide("daily").
''')

GUIDE["daily"] = ("daily(): build today's report now", '''
DAILY()

Builds today's props report right now,
with a fresh injury check: every
player in today's games with a line he
reached in 80%+ of his last 10 games.

Usage:
  daily()
  daily(games=5)
  daily(games="season")
  daily(min_hit=0.9)
  daily(per_game=20, log=20)
  daily(save=True)

Options (defaults first):
  games=10      window: 5, 10, any
                number, or "season"
  min_hit=0.80  hit rate needed (0.81
                means strictly above
                80%)
  min_games=5   fewest games in the
                window to be checked
  per_game=8    most props per game
  log=10        recent games listed
                under each prop (0 =
                none, 20 = last 20)
  day=None      another day, e.g.
                datetime.date(2026, 11,
                22)
  page, save    see guide("paging")

Output (one game; numbers made up):
Props for Sun 11/22
last 10 games | 80%+ hit rate
NFL injuries checked 9:41 AM ET

==================================
4:25 PM ET | NFL
KC (7-3) @ BUF (8-2)
==================================
Injuries (regulars):
  KC   Questionable: T.Kelce
  BUF  Out: K.Shakir
90% J.Allen (QB, BUF)
  Pass Yards 250+ (over 249.5)
  hit 9/10 | avg 271.9 | low 233
  KC pass D: #18 of 32, 231 pass yds/g
  3% more than avg -> neutral
  last 10 (oldest first, * = missed):
   @NE 233*  BAL 272  @CIN 266
   PIT 261  @DEN 263    LV 288
  @LAC 310   MIA 254   @SF 266
   SEA 276

NHL games also list team props first:
team shots on goal, team goals, and
game total shots (both teams' recent
games together).

Reading it:
- 90% = hit rate; 250+ means 250 or
  more (an over 249.5 bet)
- "! Questionable (knee)" under a name
  = he's on the injury report; Out
  players are left off
- Defense rank #1 = allows the least;
  for goalies, #1 = opponent shoots
  the most
- favorable / neutral / tough: the
  opponent is 5%+ above / within 5% /
  5%+ below league average, for the
  over
- * in the log = games under the line
''')

GUIDE["players"] = ("nfl(), nba(), nhl(): a player's games", '''
PLAYERS: nfl(), nba(), nhl()

A player's recent games, one row per
stat, with averages for his last 5,
last 10 and all games this season, the
80% line, and his injury status if
he's listed.

The 80% line ("80% L10") is the
highest number he reached in at least
8 of his last 10 games: the line a bet
would have needed to hit 80% of the
time. "10+" = an over 9.5 bet. "-"
means it would be 0.

Usage:
  nfl("josh allen")
  nfl("josh allen", last=10)
  nfl("josh allen buf")
  nfl("josh allen qb")
  nba("brunson")
  nhl("mcdavid")
  nhl("shesterkin")
  nfl("josh allen",
      stats=["Pass Yards",
             "passing_epa"])
  nba("gobert", hit=0.9)
  nba("gobert", window=5)
  nba("gobert", window="season")

Options:
  last=5      games shown (averages
              always use L5, L10 and
              the season)
  stats=[..]  exactly these stats, by
              readable name or column
              name (see
              guide("columns"))
  hit=0.80    hit rate for the line
              column (0.9 = 90% line)
  window=10   games for the line
              column: 5, 10, any
              number, or "season"
              (goalies: starts only)

Names: part of a name works ("ceedee").
If a name is shared, add a team code
or position at the end:
  nhl("sebastian aho car")

Stats shown by position:
  NFL QB: pass att, completions, pass
    yards, pass TDs, INTs thrown, rush
    yards, rush TDs, fumbles
  NFL RB: rush att, rush yards, rush
    TDs, targets, receptions, rec
    yards, rec TDs, fumbles
  NFL WR/TE: targets, receptions, rec
    yards, rush yards, total TDs,
    fumbles
  NFL K: FG made/att, longest, FG
    40-49, FG 50+, XP made/att, kick
    points, fumbles
  NBA: minutes, points, rebounds,
    assists, 3PT made, steals, blocks
  NHL skaters: goals, assists, points,
    shots on goal, minutes
  NHL goalies: shots faced, saves,
    goals against

Output (numbers made up):
Josh Allen | QB | BUF | 11 games
              W9  W10 W11
Opponent      LV @LAC MIA
Pass Att      34   38  31
Completions   22   25  20
Pass Yards   288  310 254
Pass TDs       2    3   1
INTs Thrown    0    1   0
Rush Yards    41   33  57
Rush TDs       1    0   0
Fumbles        0    0   0

             Avg   Avg    Avg  80%
Opponent      L5   L10 Season  L10
Pass Att    34.0  33.6   33.4  31+
Pass Yards 279.2 271.9  269.6 254+
...
(@ = away game. NFL columns are weeks;
NBA and NHL columns are dates.)
''')

GUIDE["teams"] = ("nfl_team(), nba_team(), nhl_team(): a team", '''
TEAMS: nfl_team(), nba_team(),
nhl_team()

A team's recent games: record, scoring,
what it allows, with L5 / L10 / season
averages (records in the Result row)
and its injured regulars.

Usage:
  nfl_team("BUF")
  nfl_team("BUF", last=10)
  nba_team("NY")
  nhl_team("NYR")
  nfl_team("BUF",
      stats=["Pass Yards Allowed",
             "QB Hits"])

Options:
  last=5      games shown
  stats=[..]  exactly these stats (see
              guide("columns"), with
              team=True)

Shown by default:
  NFL: result, points scored/allowed,
    pass and rush yards, pass and rush
    yards allowed, times sacked, sacks
    made
  NBA: result, points, points allowed,
    pace, offensive and defensive
    rating
  NHL: result, goals scored/allowed,
    shots on goal, shots allowed

Use the team codes in your files; see
guide("codes").

Output (numbers made up):
BUF | 11 games | 8-3
Injuries (regulars):
  BUF  Out: K.Shakir
                    W9 W10 W11
Opponent            LV @LAC MIA
Result               W    W   L
Points Scored       31   27  20
Points Allowed      17   24  23
...
''')

GUIDE["scan"] = ("nfl_scan(), nba_scan(), nhl_scan()", '''
SCANNERS: nfl_scan(), nba_scan(),
nhl_scan()

The props from the daily report for one
sport, as one list, most reliable
first: this week's NFL games, today's
NBA and NHL games.

Checked stats:
  NFL QB: pass yards, pass TDs, rush
    yards
  NFL RB: rush att, rush yards,
    receptions, rec yards
  NFL WR/TE: receptions, rec yards
  NBA: points, rebounds, assists
  NHL: shots on goal; goalies: saves
    (games he started only)

NHL team props (team shots on goal,
team goals, game total shots):
  nhl_team_scan()
  nhl_team_scan(team="NYR", games=5)

Usage:
  nba_scan()
  nba_scan(games=5)
  nba_scan(games="season")
  nba_scan(team="NY")
  nfl_scan(team=["BUF", "KC"])
  nhl_scan(min_hit=0.9, top=50)
  nfl_scan(all_teams=True)

Options (defaults first):
  games=10      5, 10, any number, or
                "season"
  min_hit=0.80  hit rate needed
  top=25        how many to show
  team=None     one team or a list
  all_teams=False  True = every team,
                playing or not
  min_games=5   fewest games needed
  log=10        recent games listed
                under each prop
  day=None      NBA/NHL: another day
  page, save    see guide("paging")

Output (numbers made up):
NBA scan | 11/19
last 10 games | 80%+ hit rate
NBA injuries checked 6:02 PM ET
90% J.Brunson (PG, NY) vs BOS
  Points 24+ (over 23.5)
  hit 9/10 | avg 28.7 | low 19
  last 10 (oldest first, * = missed):
  ...

Each line shows the highest line he
reached in at least min_hit of the
games. Stats have minimum lines so
trivial ones don't show; see
guide("settings").
''')

GUIDE["injuries"] = ("nfl_injuries(), nba_injuries(), nhl_injuries()", '''
INJURIES: nfl_injuries(),
nba_injuries(), nhl_injuries()

ESPN's current injury report, checked
live (re-checked after 10 minutes).

Usage:
  nba_injuries("NY")   one team, with
                       ESPN's notes
  nba_injuries()       whole league
  nfl_injuries("BUF")
  nhl_injuries("NYR")

Options:
  team=None     a team code, or the
                whole league
  page, save    see guide("paging")

Statuses: Out, Injured Reserve,
Suspension (all treated as out),
Doubtful, Questionable, Day-To-Day.

Output (made up):
NBA injury report | checked 6:02 PM ET

NY
  Mitchell Robinson (C)
    Questionable (left ankle)
    Listed as questionable for
    Wednesday after missing practice.

Elsewhere: player tables show a
"Status:" line, the daily report and
scanners mark props with "!", leave off
Out players, and list each game's
injured regulars.

NHL starting goalies aren't on injury
reports: they're confirmed near game
time.
''')

GUIDE["columns"] = ("nfl_columns() etc.: find stat names", '''
STAT NAMES: nfl_columns(),
nba_columns(), nhl_columns()

Every stat in the files, with its
readable name and its column name.
Use either name in stats=[...].

Usage:
  nfl_columns()             player stats
  nfl_columns("rush")       search
  nfl_columns(team=True)    team stats
  nfl_columns("allowed", team=True)
  nba_columns("reb")
  nhl_columns("shot")

Options:
  search=None   a word to match
  team=False    True = team stats
  page, save    see guide("paging")

Output:
Rush Att    carries
Rush Yards  rushing_yards
Rush TDs    rushing_tds
(3 stats. Use either name in
stats=[...])

Then, e.g.:
  nfl("james cook",
      stats=["Rush Att", "Rush Yards"])
''')

GUIDE["status"] = ("status(): is the data up to date?", '''
STATUS()

When each sport's data last updated
and whether it passed every check.

Usage:
  status()

Output (made up):
NFL 2026: OK
  data as of 2026-11-19T10:04:11Z
  checked    2026-11-19T10:04:11Z
  150 games, 9876 player rows
  last run: +0 games, 0 corrections
NHL 2026-27: OK
  ...
  ~ game 2026020345 NJD: ...

OK = the files passed every check.
LAST CHECK FAILED = the newest update
didn't pass, so the previous (checked)
files are still in use; "!" lines say
why. "~" lines are warnings, like a
box score the NHL hasn't fixed yet.
Times are UTC.
''')

GUIDE["paging"] = ("page= and save=: long output", '''
PAGING AND SAVING

Long output is shown a page at a time:
daily_report(), daily(), the scanners,
the injury reports, the column lists
and guide().

Reports page one game at a time; the
rest 30 lines at a time.

At each pause:
  Return   next page
  a        show all the rest
  q        stop

Options on all of those:
  page=False   print it all at once
  page=20      20 lines per page
  save=True    save to a text file in
               a-Shell's folder instead

Saved files are named by command and
date, e.g. daily_report_2026-11-22.txt.
Open them in the Files app (On My
iPhone > a-Shell) to scroll, or tap
Share > Notes to copy into a note.
''')

GUIDE["settings"] = ("Settings you can change", '''
SETTINGS

Near the top or middle of
sports_data.py. Edit the file, or set
them right after starting (before
other commands), e.g.:
  REGULAR_NBA_MINUTES = 20

Scanner and report:
  SCAN_FLOOR    lowest line per stat
                (150 pass yds, 2 rec,
                8 pts, 2 shots ...)
  SCAN_STEP     line steps (10 pass
                yds, 5 rush/rec yds,
                1 otherwise)
  SCAN_STATS    stats checked per
                position
  TEAM_SCAN_STATS, GAME_SCAN_STATS,
  TEAM_SCAN_FLOOR  NHL team props
                (floors: 20 shots,
                2 goals, 45 game shots)

Injury summaries:
  REGULAR_NBA_MINUTES = 15
  REGULAR_NHL_MINUTES = 12
  RECENT_TEAM_GAMES = 3   Out players
                listed only if they
                played in these
  INJURY_REFRESH_MINUTES = 10

Tables:
  NFL_VIEWS, NBA_VIEW, NHL_VIEWS
                stats per position
  NFL_TEAM_VIEW etc.  team stats
  PAGE_LINES = 30

The morning report uses the settings
in the repository's sports_data.py;
to change it, edit that copy (or the
daily(...) call in update.yml).
''')

GUIDE["codes"] = ("Team codes", '''
TEAM CODES

Use the codes in your files:
  NFL (nflverse): BUF, KC, LA (Rams),
    LAC, WAS, JAX, LV, NE, NO, ...
  NBA (ESPN): NY, GS, SA, NO, BOS,
    LAL, UTAH, WSH, ...
  NHL (NHL): NYR, TBL, NJD, LAK, SJS,
    VGK, UTA, ...

A wrong code lists the valid ones:
  nba_team("NYK")
''')

GUIDE["standings"] = ("nfl_standings() etc.: standings", '''
STANDINGS: nfl_standings(),
nba_standings(), nhl_standings()

Official standings, checked live: the
whole league, then each conference.
One section per page.

Usage:
  nba_standings()           league and
                            conferences
  nba_standings("east")     one
                            conference
  nba_standings("overall")  league only
  nfl_standings("afc")
  nfl_standings("nfc")
  nhl_standings("west")

Options:
  view=None     "overall", or a
                conference: "east" /
                "west" (NBA, NHL),
                "afc" / "nfc" (NFL)
  page, save    see guide("paging")

Columns:
  NFL: W-L-T, Pct, PF (points for), PA
    (points against), Strk (streak)
  NBA: W-L, Pct, GB (games behind the
    leader), Strk
  NHL: GP, W-L-OT, Pts, P% (points %),
    GD (goal difference), Strk

The league table is sorted by win %
(NHL: points). Conferences are in
playoff-seed order (NHL: conference
standing).

Output (made up):
NBA standings | checked 3:05 PM ET

==================================
Eastern Conference
==================================
 # Team  W-L  Pct  GB Strk
 1 BOS  16-2 .889   -   W2
 2 NY   15-3 .833 1.0   W5
...

If the live standings can't load, it
shows overall records from your data
files instead (no conferences).
''')

GUIDE["leaders"] = ("nfl_leaders() etc.: player leaders", '''
PLAYER LEADERS: nfl_leaders(),
nba_leaders(), nhl_leaders()

Per-game leaderboards, one per page.

Boards:
  NFL: pass yards, pass TDs, rush
    yards, rush TDs, receptions, rec
    yards
  NBA: points, rebounds, assists
  NHL: saves (goalies), shots on goal
    (skaters)

Usage:
  nfl_leaders()
  nfl_leaders("rec yds")
  nfl_leaders(top=25)
  nfl_leaders(games=5)
  nfl_leaders(position="WR,TE")
  nfl_leaders(team="BUF")
  nba_leaders("assists", top=20)
  nhl_leaders(position="D")

Options (defaults first):
  stat=None     one board (readable or
                column name; see
                guide("columns")), or a
                list
  top=10        players per board
  games="season"  or 5, 10, any number:
                each player's last N
  min_games=None  games needed; default
                half his team's games
                (in the window)
  team=None     a team code or a list
  position=None  "WR", "WR,TE", "D" ...
  hit=0.80      hit rate for the last
                column (0.9 = 90% line)
  window=10     games for that column:
                5, 10, any number, or
                "season"
  totals=False  True adds a Total
                column (wider than the
                screen)
  page, save    see guide("paging")

Per game = per game played. The last
column is the 80% line: the highest
number he reached in at least 8 of his
last 10 games (see guide("players")).

Output (made up):
NFL leaders | season
min half his team's games
80% col: line hit 80%+ of last 10 games

==================================
Rec Yards per game
==================================
 # Player      Pos Team GP Per G  80%
 1 J.Chase     WR  CIN  11 104.2  81+
 2 J.Jefferson WR  MIN  11  98.7  77+
...
''')

GUIDE["lines"] = ("nfl_line_scan() etc.: rank by 80% line", '''
LINE SCANS: nfl_line_scan(),
nba_line_scan(), nhl_line_scan()

Ranks today's players by their 80%
line for a prop: the highest number
they reached in at least 8 of their
last 10 games. Highest first.

Covers today's games (NFL: this week).

Usage:
  nba_line_scan("rebounds")
  nba_line_scan()           points,
                            rebounds and
                            assists
  nba_line_scan("reb",
                order="lowest")
  nfl_line_scan("rec yds", window=5)
  nhl_line_scan("shots", team="NYR")
  nba_line_scan("pts", hit=0.9)

Options (defaults first):
  stat=None       one stat, or the
                  leader stats
  window=10       5, 10, any number,
                  or "season"
  hit=0.80        the line's hit rate
  order="highest" or "lowest"
  top=25          players per board
  team=None       a team or a list
  all_teams=False True = everyone
  min_games=5     games needed
  day=None        NBA/NHL: another day
  page, save      see guide("paging")

Players averaging below the stat's
minimum line (SCAN_FLOOR) are left
out. Goalies: starts only.

Output (made up):
NBA line scan | 11/22
last 10 games | 80% line, highest first

==================================
Rebounds
==================================
# Player   Pos Team  Avg 80%
1 R.Gobert C   MIN  11.5 11+
2 J.Allen  C   CLE  10.8 10+
...
''')

GUIDE["kalshi"] = ("kalshi_report(): entry backtest", '''
KALSHI_REPORT()

The latest backtest of when to buy
Kalshi NBA props (points, rebounds,
assists): buying 5 minutes before
tip-off vs earlier in the day, or
after in-game price dips. Profit is
per contract, in cents, after fees.

Usage:
  kalshi_report()
  kalshi_report(save=True)

Sections (one per page):
  1. Buying 5 min before tip-off, the
     baseline, by price
  2. Pregame timing: 10am, noon, 3pm
     ... vs the baseline
  3. Buying at each game minute, and
     only after a 10c+ dip
  4. Dip strategies: buy at the first
     5/10/20c dip in Q1, Q2 or Q3
  5. Why it dipped: pace, foul trouble,
     score
  6. Favorites: how far below their
     price 15 min before tip they dip
     (Q1, first half, through Q3), for
     winners and losers. Groups: 75-85c
     scanner-flagged; 85-92c with a
     last-10 hit rate of 90%+ or 100%;
     85-92c everything
  7. Favorites: buy orders 3/5/8/10/15c
     below that price: how often they
     fill, and profit per fill
  8. Selling early by price: sell when
     the bid falls to 30/20/10c, or when
     an 80c+ favorite falls to 60/50c,
     vs holding to the end
  9. Selling early by situation: when
     he needs 1.5x/2x/3x his usual rate,
     foul trouble, blowouts
  10. Collapsed 80c+ favorites, when
     the BID (what the app shows) fell
     to 40/30/20c: bid, ask, spread, how
     often they still won, buying at the
     ask vs at the bid
  11. The same when the ASK fell there
     (you could actually buy at it)

Each result shows the season, a
discovery period (earlier games) and a
check period (later games). Trust
patterns that hold up in both, with
enough entries (n), and a range that
stays above 0.

Built by the "Kalshi NBA entry
backtest" workflow (Mondays, or run it
from the Actions tab).
''')

_GUIDE_ORDER = ["start", "report", "daily", "players", "teams", "standings", "leaders",
                "scan", "lines", "kalshi", "injuries", "columns", "status", "paging", "settings",
                "codes"]
_GUIDE_ALIASES = {
    "daily_report": "report", "players": "players", "player": "players", "nfl": "players",
    "nba": "players", "nhl": "players", "team": "teams", "nfl_team": "teams",
    "nba_team": "teams", "nhl_team": "teams", "scan": "scan", "scans": "scan",
    "nfl_scan": "scan", "nba_scan": "scan", "nhl_scan": "scan", "injury": "injuries",
    "nfl_injuries": "injuries", "nba_injuries": "injuries", "nhl_injuries": "injuries",
    "column": "columns", "nfl_columns": "columns", "nba_columns": "columns",
    "nhl_columns": "columns", "stats": "columns", "page": "paging", "save": "paging",
    "setting": "settings", "code": "codes", "getting started": "start", "help": "start",
    "standing": "standings", "nfl_standings": "standings", "nba_standings": "standings",
    "nhl_standings": "standings", "records": "standings", "nhl_team_scan": "scan",
    "team props": "scan", "team_props": "scan",
    "leader": "leaders", "nfl_leaders": "leaders", "nba_leaders": "leaders",
    "nhl_leaders": "leaders", "player standings": "leaders", "player_standings": "leaders",
    "leaderboard": "leaders", "leaderboards": "leaders",
    "line": "lines", "line scan": "lines", "line_scan": "lines", "nfl_line_scan": "lines",
    "nba_line_scan": "lines", "nhl_line_scan": "lines", "gap": "lines", "gaps": "lines",
    "80%": "lines", "consistency": "lines", "kalshi_report": "kalshi", "backtest": "kalshi", "gap scan": "lines", "gap_scan": "lines",
    "nfl_gap_scan": "lines", "nba_gap_scan": "lines", "nhl_gap_scan": "lines",
    "usage": "start", "guide": "start",
}


@_paged
def guide(topic=None):
    """
    How to use these tools.
    guide()            list of topics
    guide("daily")     one topic: what it does, options, example output
    guide("all")       every topic
    Function names work too: guide("nfl_team"), guide("daily_report").
    """
    if topic is None:
        print('GUIDE: type guide("topic")')
        print('(usage("topic") works the same)')
        for key in _GUIDE_ORDER:
            print()
            print(f'  guide("{key}")')
            for line in textwrap.wrap(GUIDE[key][0], 34):
                print(f"    {line}")
        print()
        print('guide("all") shows everything.')
        return
    key = str(topic).strip().lower().replace("()", "")
    if key == "all":
        print("\n\n".join(GUIDE[k][1].strip("\n") for k in _GUIDE_ORDER))
        return
    key = _GUIDE_ALIASES.get(key, key)
    if key not in GUIDE:
        print(f"No guide for '{topic}'. Topics: {', '.join(_GUIDE_ORDER)}")
        return
    print(GUIDE[key][1].strip("\n"))


def usage(topic=None, page=True, save=False):
    """
    The same as guide(): how to use these tools.
    usage()            list of topics
    usage("daily")     one topic: what it does, options, example output
    usage("all")       every topic
    Function names work too: usage("nfl_team"), usage("daily_report").
    """
    guide(topic, page=page, save=save)

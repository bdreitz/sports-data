"""Read the NFL, NBA and NHL files published by the "Update stats" GitHub workflow.
Standard library only, so it runs in a-Shell on iPhone.

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


def _grid(sport, title, games, columns, team=False, history=None):
    """One row per stat, one column per game, then the averages over the last 5 games, the last
    10 games and the season (taken from `history`, every game this season). Split into blocks
    that fit the screen."""
    history = history or games
    if sport == "nfl":
        heads = [f"W{g['week']}" for g in games]
    else:
        heads = [f"{int(g['game_date'][5:7])}/{int(g['game_date'][8:10])}" for g in games]
    windows = [history[-5:], history[-10:], history]
    lines = [("", heads + ["Avg", "Avg", "Avg"]),
             ("Opponent", [("" if g["home"] == "1" else "@") + g["opponent"] for g in games]
              + ["L5", "L10", "Season"])]
    for c in columns:
        cells = [_cell(_value(sport, g, c)) for g in games]
        lines.append((_label(sport, c, team), cells + [_average(sport, w, c) for w in windows]))
    label_w = max(len(label) for label, _ in lines)
    count = len(games) + 3
    col_w = [max(len(cells[j]) for _, cells in lines) for j in range(count)]
    width = _width()
    blocks, current, used = [], [], label_w
    for j, w in enumerate(col_w[:len(games)]):
        if current and used + 1 + w > width:
            blocks.append(current)
            current, used = [], label_w
        current.append(j)
        used += 1 + w
    # The three averages stay together: beside the last games if they fit, else in their own block
    averages = [len(games), len(games) + 1, len(games) + 2]
    if current and used + 3 + sum(col_w[-3:]) <= width:
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


def _show_player(sport, name, last, view, stats):
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
    _grid(sport, title, games[-last:], columns, history=games)


def nfl(name, last=5, stats=None):
    """A player's most recent games. QBs, RBs, WR/TEs, kickers and defenders each get their
    own stats; stats=[...] shows exactly the ones you name."""
    _show_player("nfl", name, last, lambda pos: NFL_VIEWS.get(pos, NFL_VIEWS["defense"]), stats)


def nhl(name, last=5, stats=None):
    """A player's most recent games. Goalies get shots faced, saves and goals against."""
    _show_player("nhl", name, last, lambda pos: NHL_VIEWS["G" if pos == "G" else "skater"], stats)


def nba(name, last=5, stats=None):
    """A player's most recent games."""
    _show_player("nba", name, last, lambda pos: NBA_VIEW, stats)


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

_SCAN_NAMES = {"passing_yards": "Pass Yards", "passing_tds": "Pass TDs", "rushing_yards": "Rush Yards",
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

    print(f"{sport.upper()} scan | {when} | {span} | {min_hit:.0%}+ hit rate")
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
            entries.append((g, sport, props[:per_game], len(props), records, matchups, injuries))

    entries.sort(key=lambda e: (e[0]["start"] or datetime.datetime.max, e[1], e[0]["away"], e[0]["home"]))
    out = [f"Props for {day.strftime('%a')} {day.month}/{day.day}", f"{span} | {min_hit:.0%}+ hit rate"]
    out += notes
    if not entries:
        out.append("No NFL, NBA or NHL games today." if not notes else "")
    goalie = False
    for g, sport, props, total, records, matchups, injuries in entries:
        away, home = g["away"], g["home"]
        out += ["", "=" * 34,
                f"{_clock(g['start'])} | {sport.upper()}",
                f"{away} ({records.get(away, '0-0')}) @ {home} ({records.get(home, '0-0')})",
                "=" * 34]
        out += injuries
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

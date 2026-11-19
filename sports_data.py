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
"""
import csv
import datetime
import io
import json
import re
import urllib.error
import urllib.request
from functools import lru_cache

try:
    import shutil
except ImportError:
    shutil = None

REPO = "bdreitz/sports-data"

_BASE = f"https://github.com/{REPO}/releases/download/data"

# ---------------------------------------------------------------------------
# Which stats each view shows (column names in the files)
# ---------------------------------------------------------------------------

NFL_VIEWS = {
    "QB": ["attempts", "completions", "passing_yards", "passing_tds", "rushing_yards", "rushing_tds"],
    "RB": ["carries", "rushing_yards", "rushing_tds", "targets", "receptions", "receiving_yards",
           "receiving_tds"],
    "WR": ["targets", "receptions", "receiving_yards", "rushing_yards", "total_tds"],
    "K": ["fg_made", "fg_att", "fg_long", "fg_made_40_49", "fg_made_50_plus", "pat_made", "pat_att",
          "kick_points"],
    "defense": ["def_tackles_solo", "def_tackle_assists", "def_sacks", "def_interceptions"],
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


def _grid(sport, title, games, columns, extra_rows=(), team=False):
    """One row per stat, one column per game, split into blocks that fit the screen."""
    if sport == "nfl":
        heads = [f"W{g['week']}" for g in games]
    else:
        heads = [f"{int(g['game_date'][5:7])}/{int(g['game_date'][8:10])}" for g in games]
    lines = [("", heads), ("Opponent", [("" if g["home"] == "1" else "@") + g["opponent"] for g in games])]
    lines += list(extra_rows)
    lines += [(_label(sport, c, team), [_cell(_value(sport, g, c)) for g in games]) for c in columns]
    label_w = max(len(label) for label, _ in lines)
    col_w = [max(len(cells[j]) for _, cells in lines) for j in range(len(games))]
    blocks, current, used, width = [], [], label_w, _width()
    for j, w in enumerate(col_w):
        if current and used + 1 + w > width:
            blocks.append(current)
            current, used = [], label_w
        current.append(j)
        used += 1 + w
    if current:
        blocks.append(current)
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
    _grid(sport, title, games[-last:], columns)


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
    wins = sum(1 for g in games if g["result"] == "W")
    columns = _resolve_stats(sport, "team_games", stats) if stats else \
        [c for c in view if c in set(_header(sport, "team_games"))]
    _grid(sport, f"{want} | {len(games)} games | {wins} wins", games[-last:], columns, team=True)


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


def nfl_columns(search=None, team=False):
    """Every NFL stat: readable name, then column name. nfl_columns("rush"), nfl_columns(team=True)"""
    _columns("nfl", search, team)


def nhl_columns(search=None, team=False):
    """Every NHL stat: readable name, then column name. nhl_columns("shot"), nhl_columns(team=True)"""
    _columns("nhl", search, team)


def nba_columns(search=None, team=False):
    """Every NBA stat: readable name, then column name. nba_columns("reb"), nba_columns(team=True)"""
    _columns("nba", search, team)

"""Read the NFL, NBA and NHL files published by the "Update stats" GitHub workflow.
Standard library only, so it runs in a-Shell on iPhone.

First, set REPO below to your GitHub name and repository, e.g. "jsmith/sports-data".

    status()                 when each sport last updated, and whether its checks passed
    nfl("josh allen")        a player's last 5 games (nfl("josh allen", last=10) for more)
    nhl("mcdavid")
    nba("brunson")
    nfl_team("BUF")          a team's last 5 games (use the team code)
    nhl_team("EDM")
    nba_team("NY")
"""
import csv
import datetime
import io
import json
import re
import urllib.error
import urllib.request
from functools import lru_cache

REPO = "bdreitz/sports-data"

_BASE = f"https://github.com/{REPO}/releases/download/data"


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
    if "YOUR_GITHUB_NAME" in REPO:
        raise RuntimeError("Set REPO at the top of sports_data.py to your GitHub name/repository first.")
    request = urllib.request.Request(f"{_BASE}/{name}", headers={"User-Agent": "sports_data"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} for {name}. Has the workflow published it yet?") from e


def _rows(sport, kind):
    return list(csv.DictReader(io.StringIO(_download(f"{sport}_{_labels()[sport]}_{kind}.csv"))))


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


def _player(sport, name, last, columns):
    words = _normalize(name).split()
    found = {}
    for r in _rows(sport, "player_games"):
        if all(w in _normalize(r["player_name"]) for w in words):
            found.setdefault(r["player_id"], []).append(r)
    if not found:
        print(f"No {sport.upper()} player matches '{name}'.")
        return
    if len(found) > 1:
        print(f"'{name}' matches several players; add more of the name:")
        for games in found.values():
            print(f"  {games[-1]['player_name']} ({games[-1]['team']})")
        return
    games = sorted(next(iter(found.values())), key=lambda r: (r["game_date"], r["game_id"]))
    latest = games[-1]
    print(f"{latest['player_name']} | {latest['position']} | {latest['team']} | {len(games)} games")
    shown = games[-last:]
    header = ["date", "opp"] + [label for label, _ in columns]
    table = [[g["game_date"][5:], ("vs " if g["home"] == "1" else "@") + g["opponent"]]
             + [g[col] or "-" for _, col in columns] for g in shown]
    widths = [max(len(str(x)) for x in col) for col in zip(header, *table)]
    for line in [header] + table:
        print(" ".join(str(x).rjust(w) for x, w in zip(line, widths)))


def nhl(name, last=5):
    """A player's most recent games. Goalies show saves and shots against."""
    rows = [r for r in _rows("nhl", "player_games")
            if all(w in _normalize(r["player_name"]) for w in _normalize(name).split())]
    if rows and all(r["position"] == "G" for r in rows):
        cols = [("SV", "saves"), ("SA", "shots_against"), ("GA", "goals_against"), ("dec", "decision")]
    else:
        cols = [("G", "goals"), ("A", "assists"), ("P", "points"), ("SOG", "shots"), ("TOI", "toi")]
    _player("nhl", name, last, cols)


def nba(name, last=5):
    _player("nba", name, last, [("MIN", "minutes"), ("PTS", "pts"), ("REB", "reb"),
                                ("AST", "ast"), ("3PM", "fg3m")])


def nfl(name, last=5):
    """A player's most recent games: passing, rushing and receiving yards, and receptions."""
    _player("nfl", name, last, [("PaY", "passing_yards"), ("RuY", "rushing_yards"),
                                ("Rec", "receptions"), ("ReY", "receiving_yards")])


def _team(sport, team, last, columns):
    rows = _rows(sport, "team_games")
    codes = {r["team"] for r in rows}
    want = team.strip().upper()
    if want not in codes:
        print(f"Use the team code: {', '.join(sorted(codes))}")
        return
    games = sorted((r for r in rows if r["team"] == want), key=lambda r: r["game_date"])
    wins = sum(1 for g in games if g["result"] == "W")
    print(f"{want} | {len(games)} games | {wins} wins")
    header = ["date", "opp", "res"] + [label for label, _ in columns]
    table = [[g["game_date"][5:], ("vs " if g["home"] == "1" else "@") + g["opponent"], g["result"]]
             + [g[col] or "-" for _, col in columns] for g in games[-last:]]
    widths = [max(len(str(x)) for x in col) for col in zip(header, *table)]
    for line in [header] + table:
        print(" ".join(str(x).rjust(w) for x, w in zip(line, widths)))


def nhl_team(team, last=5):
    """A team's most recent games. Use the NHL code, e.g. 'EDM'."""
    _team("nhl", team, last, [("GF", "goals_for"), ("GA", "goals_against"),
                              ("SF", "shots_for"), ("SA", "shots_against")])


def nba_team(team, last=5):
    """A team's most recent games. Use ESPN's code, e.g. 'NY', 'BOS', 'GS'."""
    _team("nba", team, last, [("PTS", "pts"), ("OPP", "opp_pts"), ("pace", "pace"),
                              ("ORtg", "off_rating"), ("DRtg", "def_rating")])


def nfl_team(team, last=5):
    """A team's most recent games. Use the nflverse code, e.g. 'BUF', 'LA', 'WAS'."""
    _team("nfl", team, last, [("PF", "points_for"), ("PA", "points_against"),
                              ("PaY", "passing_yards"), ("RuY", "rushing_yards")])

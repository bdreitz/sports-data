"""Shared code for the daily stats update: downloads, CSV files, merging and the update loop.

Each sport module (nhl.py, nba.py) provides:
    NAME, PLAYER_COLUMNS, TEAM_COLUMNS, PLAYER_KEY, TEAM_KEY
    season(today)                     -> season id string
    file_names(season)                -> {"players", "teams", "status", "changelog"}
    fetch_context(get, season)        -> dict with teams, standings, player names ...
    fetch_schedule(get, season, ctx)  -> {game_id: {"date": "YYYY-MM-DD", "final": bool, ...}}
    fetch_game(get, game_id, info, ctx, season) -> (player rows, team rows); raises NotFinal
    validate(players, teams, final_games, ctx)  -> list of error strings

A sport whose source publishes whole-season files (NFL via nflverse) provides
    fetch_all(get, season, ctx, final_games) -> (player rows, team rows)
instead of fetch_game, and is re-read in full on every run. A sport can also set
ctx["player_columns"] / ctx["team_columns"] when its columns come from the source file.

How the safeguards work:
  * Rows are keyed (game + player, or game + team). A re-fetched game REPLACES its rows,
    so running twice never duplicates anything.
  * Only games that are final go in.
  * Every run re-fetches the last RECHECK_DAYS days of games to pick up stat corrections;
    a full rebuild (every Monday, or on request) re-fetches the whole season.
  * Before anything is published, the data is checked against the official standings,
    the final scores and its own totals. If any check fails, the data files are NOT
    written, so the previously published (good) files stay in place.
"""
import csv
import datetime as dt
import gzip
import io
import json
import os
import time
import urllib.error
import urllib.request

USER_AGENT = "sports-data-updater/1.0 (GitHub Actions)"
RECHECK_DAYS = 7
RELEASE_TAG = "data"
CHANGELOG_MAX_LINES = 3000


class NotFinal(Exception):
    """The game's box score isn't final yet."""


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

def get_json(url, tries=4, pause=0.3, text=False):
    """GET a JSON document (or, with text=True, a text file such as a CSV, gzipped or not),
    retrying temporary failures. Pauses briefly after each call."""
    last = None
    for attempt in range(tries):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "*/*" if text else "application/json"})
            with urllib.request.urlopen(request, timeout=120 if text else 30) as response:
                raw = response.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            data = raw.decode("utf-8-sig") if text else json.loads(raw.decode("utf-8"))
            time.sleep(pause)
            return data
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404):
                raise RuntimeError(f"HTTP {e.code} for {url}") from e
            last = e
        except (OSError, ValueError) as e:      # network errors, timeouts, bad JSON
            last = e
        time.sleep(2 ** attempt)
    raise RuntimeError(f"gave up after {tries} tries: {url} ({last})")


def load_published(name):
    """Text of a file published on this repo's 'data' release, or None if it doesn't exist yet.
    For local testing, set PUBLISHED_DIR to a folder holding the files instead."""
    local = os.environ.get("PUBLISHED_DIR")
    if local:
        path = os.path.join(local, name)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return None
    url = f"https://github.com/{repo}/releases/download/{RELEASE_TAG}/{name}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


# ---------------------------------------------------------------------------
# Values and CSV
# ---------------------------------------------------------------------------

def fmt(value):
    """One consistent text form per value, so unchanged stats never look 'changed'."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if value != value:          # NaN
            return ""
        if value.is_integer():
            return str(int(value))
        return repr(round(value, 4))
    return str(value).strip()


def normalize(row, columns):
    return {c: fmt(row.get(c)) for c in columns}


def read_rows(text):
    if not text:
        return []
    return list(csv.DictReader(io.StringIO(text)))


def write_rows(path, columns, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def to_int(value):
    """'12' / 12 / 12.0 -> 12; '' / None / '--' -> None."""
    if value is None or value == "" or value == "--":
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(float(str(value).replace("+", "")))
    except ValueError:
        return None


def minutes(value):
    """'15:32' -> 15.53, '32' -> 32.0, None -> None."""
    if value is None or value == "" or value == "--":
        return None
    text = str(value)
    if ":" in text:
        mins, secs = text.split(":")[:2]
        return round(int(mins) + int(secs) / 60, 2)
    try:
        return float(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Which games to fetch, and merging them in
# ---------------------------------------------------------------------------

def games_to_fetch(final_games, stored_ids, today, recheck_days=RECHECK_DAYS, full=False):
    """New final games, plus every final game from the last `recheck_days` days (or all if full)."""
    cutoff = (today - dt.timedelta(days=recheck_days)).isoformat()
    return sorted(
        gid for gid, info in final_games.items()
        if full or gid not in stored_ids or info["date"] >= cutoff
    )


def _describe(row):
    who = row.get("player_name") or row.get("player_id") or ""
    return f"{who} ({row.get('team', '')})" if who else row.get("team", "")


def diff_game(old_rows, new_rows, key, label, gid):
    """Lines describing what changed in one game between the stored and re-fetched rows."""
    def k(r):
        return tuple(r.get(c, "") for c in key)
    old = {k(r): r for r in old_rows}
    new = {k(r): r for r in new_rows}
    lines = []
    for item in sorted(old.keys() - new.keys()):
        lines.append(f"{label} {gid}: removed {_describe(old[item])}")
    for item in sorted(new.keys() - old.keys()):
        lines.append(f"{label} {gid}: added {_describe(new[item])}")
    for item in sorted(old.keys() & new.keys()):
        before, after = old[item], new[item]
        changed = [
            f"{c} {before[c] or '-'}->{after[c] or '-'}"
            for c in after if c in before and before[c] != after[c]
        ]
        if changed:
            lines.append(f"{label} {gid}: {_describe(after)}: " + ", ".join(changed))
    return lines


def merge(old_rows, new_by_game, final_ids, key, label):
    """
    Combine stored rows with re-fetched games.
    - A re-fetched game's rows replace its stored rows completely.
    - Stored games that are no longer final regular-season games are dropped.
    Returns (rows, change lines, ids of games that are new).
    """
    old_by_game = {}
    for row in old_rows:
        old_by_game.setdefault(row["game_id"], []).append(row)
    rows, changes = [], []
    for gid, stored in old_by_game.items():
        if gid not in final_ids:
            changes.append(f"{label}: removed game {gid} (no longer a final regular-season game)")
        elif gid in new_by_game:
            changes.extend(diff_game(stored, new_by_game[gid], key, label, gid))
        else:
            rows.extend(stored)
    for fresh in new_by_game.values():
        rows.extend(fresh)
    added = sorted(gid for gid in new_by_game if gid not in old_by_game)
    return rows, changes, added


def check_structure(players, teams, final_games, player_key, team_key):
    """Checks every sport gets: unique rows, two teams per game, players for both teams."""
    errors = []
    for label, rows, key in (("player", players, player_key), ("team", teams, team_key)):
        seen = set()
        for r in rows:
            item = tuple(r[c] for c in key)
            if item in seen:
                errors.append(f"duplicate {label} row {item}")
            seen.add(item)
    team_rows = {}
    for r in teams:
        team_rows.setdefault(r["game_id"], []).append(r["team"])
    player_teams = {}
    for r in players:
        player_teams.setdefault(r["game_id"], set()).add(r["team"])
    for gid, info in sorted(final_games.items()):
        teams_in_game = team_rows.get(gid, [])
        if len(teams_in_game) != 2:
            errors.append(f"game {gid} ({info['date']}) has {len(teams_in_game)} team rows, expected 2")
            continue
        missing = set(teams_in_game) - player_teams.get(gid, set())
        if missing:
            errors.append(f"game {gid} ({info['date']}) has no player rows for {sorted(missing)}")
    extra = (set(team_rows) | set(player_teams)) - set(final_games)
    for gid in sorted(extra):
        errors.append(f"game {gid} is in the data but isn't a final regular-season game")
    return errors


# ---------------------------------------------------------------------------
# The update loop for one sport
# ---------------------------------------------------------------------------

def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_sport(sport, out_dir, today, full=False, get=None, load=None, log=print):
    """Update one sport's files in out_dir. Returns True if the data passed every check."""
    get = get or get_json
    load = load or load_published
    season = sport.season(today)
    names = sport.file_names(season)

    ctx = sport.fetch_context(get, season)
    player_columns = ctx.get("player_columns", sport.PLAYER_COLUMNS)
    team_columns = ctx.get("team_columns", sport.TEAM_COLUMNS)

    previous_status = json.loads(load(names["status"]) or "{}")
    old_players = [normalize(r, player_columns) for r in read_rows(load(names["players"]))]
    old_teams = [normalize(r, team_columns) for r in read_rows(load(names["teams"]))]

    schedule = sport.fetch_schedule(get, season, ctx)
    final = {gid: info for gid, info in schedule.items() if info["final"]}
    stored = {r["game_id"] for r in old_players} & {r["game_id"] for r in old_teams}
    errors, warnings = [], list(ctx.get("warnings", []))
    new_players, new_teams = {}, {}

    if hasattr(sport, "fetch_all"):
        # Whole-season source: every final game is re-read on every run
        todo = sorted(final)
        log(f"{sport.NAME} {season}: {len(final)} final games, re-reading the season")
        p_rows, t_rows = sport.fetch_all(get, season, ctx, final)
        for r in p_rows:
            new_players.setdefault(r["game_id"], []).append(normalize(r, player_columns))
        for r in t_rows:
            new_teams.setdefault(r["game_id"], []).append(normalize(r, team_columns))
    else:
        todo = games_to_fetch(final, stored, today, full=full)
        log(f"{sport.NAME} {season}: {len(final)} final games, {len(stored)} stored, "
            f"fetching {len(todo)}{' (full rebuild)' if full else ''}")
        for n, gid in enumerate(todo, 1):
            try:
                p_rows, t_rows = sport.fetch_game(get, gid, final[gid], ctx, season)
            except Exception as e:      # NotFinal, network trouble, an unexpected response ...
                if gid in stored:
                    warnings.append(f"game {gid} ({final[gid]['date']}): {e}; kept the stored rows")
                else:
                    errors.append(f"game {gid} ({final[gid]['date']}): {e}")
                continue
            new_players[gid] = [normalize(r, player_columns) for r in p_rows]
            new_teams[gid] = [normalize(r, team_columns) for r in t_rows]
            if n % 100 == 0:
                log(f"  {n}/{len(todo)} games fetched")

    players, p_changes, added = merge(old_players, new_players, final, sport.PLAYER_KEY, "player")
    teams, t_changes, _ = merge(old_teams, new_teams, final, sport.TEAM_KEY, "team")
    players.sort(key=lambda r: (r["game_date"], r["game_id"], r["team"], r["player_id"]))
    teams.sort(key=lambda r: (r["game_date"], r["game_id"], r["team"]))

    errors += check_structure(players, teams, final, sport.PLAYER_KEY, sport.TEAM_KEY)
    errors += sport.validate(players, teams, final, ctx)
    ok = not errors

    now = _now()
    status = {
        "sport": sport.NAME,
        "season": season,
        "ok": ok,
        "checked_utc": now,
        "data_updated_utc": now if ok else previous_status.get("data_updated_utc"),
        "full_rebuild": full,
        "final_games": len(final),
        "games_fetched": len(todo),
        "games_added": len(added),
        "corrections": len(p_changes) + len(t_changes),
        "player_rows": len(players) if ok else previous_status.get("player_rows"),
        "team_rows": len(teams) if ok else previous_status.get("team_rows"),
        "errors": errors[:50],
        "warnings": warnings[:50],
    }
    os.makedirs(out_dir, exist_ok=True)
    if ok:
        write_rows(os.path.join(out_dir, names["players"]), player_columns, players)
        write_rows(os.path.join(out_dir, names["teams"]), team_columns, teams)
        block = [f"== {now} ==", f"added {len(added)} games"] + p_changes + t_changes
        old_log = (load(names["changelog"]) or "").splitlines()
        with open(os.path.join(out_dir, names["changelog"]), "w", encoding="utf-8") as f:
            f.write("\n".join((block + old_log)[:CHANGELOG_MAX_LINES]) + "\n")
        log(f"{sport.NAME}: OK - added {len(added)} games, {status['corrections']} corrections")
    else:
        log(f"{sport.NAME}: NOT PUBLISHED - {len(errors)} problem(s):")
        for e in errors[:20]:
            log(f"  {e}")
    for w in warnings[:20]:
        log(f"  warning: {w}")
    with open(os.path.join(out_dir, names["status"]), "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)
    return ok

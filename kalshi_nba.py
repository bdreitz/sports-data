"""Kalshi NBA player-prop price history, joined to in-game state, for backtesting.

For every settled Kalshi NBA points / rebounds / assists event (one event per game per stat,
holding every player's thresholds, e.g. "Jalen Brunson: 25+"), this collects:
  - Kalshi prices: hourly candles from 8am Eastern on game day, then one-minute candles from an
    hour before tip-off to the end of the game (bid and ask open/low/high/close, last trade,
    volume, open interest);
  - ESPN play-by-play for the game, rebuilt into each player's running points, rebounds,
    assists and fouls plus the score and clock, checked against ESPN's final box score;
and joins them so every price row carries the game state at that minute.

Output (Parquet, or gzipped CSV if pyarrow isn't installed):
  kalshi_nba_markets.parquet          one row per contract: player, threshold, result, final
                                      stat, prices at tip-off, flags
  kalshi_nba_minutes_YYYY-MM.parquet  one row per contract per candle, with game state
  kalshi_nba_checkpoints_YYYY-MM / kalshi_nba_windows_YYYY-MM   checkpoints and best entries
  nba_pbp_YYYY-MM.jsonl.gz            every game's play-by-play and box score (one JSON line per
                                      game), kept for building player profiles later

Modes:
  python kalshi_nba.py --mode probe      check one recent game end to end; prints, writes nothing
  python kalshi_nba.py --mode update     add settled games not yet stored (the daily run)
  python kalshi_nba.py --mode backfill   rebuild everything this season from scratch
"""
import argparse
import bisect
import datetime as dt
import gzip
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

KALSHI_HOSTS = ["https://api.elections.kalshi.com/trade-api/v2",
                "https://external-api.kalshi.com/trade-api/v2"]
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
USER_AGENT = "sports-data-kalshi-backtest/1.0"

# Kalshi series per stat. Assists isn't confirmed, so it's also looked up by title (see _series).
SERIES = {"pts": "KXNBAPTS", "reb": "KXNBAREB", "ast": "KXNBAAST"}
SERIES_TITLE_WORDS = {"pts": "points", "reb": "rebounds", "ast": "assists"}
SEASON_START = dt.date(2026, 10, 1)          # default start: the 2026-27 season (--since to change)

# Kalshi team code -> ESPN team code, where they differ
KALSHI_TO_ESPN = {"GSW": "GS", "NOP": "NO", "NYK": "NY", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH"}

PREGAME_MINUTE_CANDLES = 60                  # one-minute candles start this long before tip-off
CHECKPOINT_EVERY_GAME_MIN = 2                # in-game checkpoints every 2 minutes of game clock...
CHECKPOINT_THROUGH_GAME_MIN = 36             # ...through the end of the third quarter
PREGAME_CHECKPOINTS_ET = {"10am": 10, "noon": 12, "3pm": 15}   # game-day clock times (Eastern)
# Best-entry windows: (name, start, end). Game windows are in minutes of game clock.
GAME_WINDOWS = {"game_first_6m": 6, "game_q1": 12, "game_first_half": 24, "game_through_q3": 36}
FLAG_FLOORS = {"pts": 8, "reb": 3, "ast": 2}  # the scanner's minimum lines (SCAN_FLOOR)
POSTGAME_MINUTES = 20                        # ...and run this long after the last play
GAME_DAY_START_HOUR_ET = 8                   # hourly candles from 8am Eastern on game day
MAX_EVENTS_PER_RUN = 1200                    # safety limit; the next run continues
PAUSE = 0.25                                 # seconds between requests


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _request(url, tries=5):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            time.sleep(PAUSE)
            return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:                    # rate limited: wait and retry
                wait = float(e.headers.get("Retry-After") or 2 ** (attempt + 1))
                time.sleep(min(wait, 60))
                last = e
                continue
            if e.code in (400, 401, 403, 404):
                raise RuntimeError(f"HTTP {e.code} for {url}") from e
            last = e
        except (OSError, ValueError) as e:
            last = e
        time.sleep(2 ** attempt)
    raise RuntimeError(f"gave up after {tries} tries: {url} ({last})")


def kalshi(path, **params):
    """GET a Kalshi API path, trying each host. Params with None values are left out."""
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    errors = []
    for host in KALSHI_HOSTS:
        try:
            return _request(f"{host}{path}" + (f"?{query}" if query else ""))
        except RuntimeError as e:
            if "HTTP 404" in str(e) or "HTTP 400" in str(e):
                raise
            errors.append(str(e))
    raise RuntimeError("; ".join(errors))


def espn(path):
    return _request(f"{ESPN}{path}")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _person(name):
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    words = re.sub(r"[.'\u2019\-]", "", text.lower()).split()
    return " ".join(w for w in words if w not in ("jr", "sr", "ii", "iii", "iv", "v"))


def _ts(text):
    """ISO time -> Unix seconds (None if missing)."""
    if not text:
        return None
    try:
        return int(dt.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _eastern_offset(utc):
    """Hours from UTC to US Eastern at this UTC time (-4 in daylight saving, else -5)."""
    def sunday(month, nth):
        first = dt.date(utc.year, month, 1)
        return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1))
    start = dt.datetime.combine(sunday(3, 2), dt.time(7), tzinfo=dt.timezone.utc)
    end = dt.datetime.combine(sunday(11, 1), dt.time(6), tzinfo=dt.timezone.utc)
    return -4 if start <= utc < end else -5


def _game_day_start(day):
    """8am Eastern on `day`, as Unix seconds."""
    noon_utc = dt.datetime.combine(day, dt.time(12), tzinfo=dt.timezone.utc)
    hours = GAME_DAY_START_HOUR_ET - _eastern_offset(noon_utc)
    return int(dt.datetime.combine(day, dt.time(0), tzinfo=dt.timezone.utc).timestamp()) + hours * 3600


def parse_event_ticker(ticker):
    """'KXNBAREB-26NOV07NYKCLE' -> (date(2026, 11, 7), 'NYK', 'CLE'); None if it doesn't fit."""
    m = re.match(r"^[A-Z0-9]+-(\d{2})([A-Z]{3})(\d{2})([A-Z]{2,4}?)([A-Z]{2,4})$", ticker or "")
    if not m:
        return None
    year, month, day, first, second = m.groups()
    months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
              "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
    if month not in months:
        return None
    teams = first + second
    if len(teams) == 6:
        first, second = teams[:3], teams[3:]
    try:
        return dt.date(2000 + int(year), months[month], int(day)), first, second
    except ValueError:
        return None


def parse_market_name(market):
    """(player name, threshold) from a market, e.g. 'Jalen Brunson: 25+' -> ('Jalen Brunson', 25)."""
    for key in ("yes_sub_title", "subtitle", "title", "no_sub_title"):
        text = market.get(key) or ""
        m = re.match(r"^\s*(.+?)\s*:\s*(\d+(?:\.\d+)?)\s*\+", text)
        if m:
            value = float(m.group(2))
            return m.group(1).strip(), int(value) if value.is_integer() else value
    strike = market.get("floor_strike")
    name = market.get("yes_sub_title") or market.get("subtitle") or ""
    if strike is not None and name:
        return name.split(":")[0].strip(), int(float(strike) + 0.5)
    return None, None


def _price(block, key):
    """A candle price in dollars: 'close_dollars' (string) preferred, else 'close' in cents."""
    if not isinstance(block, dict):
        return None
    if block.get(f"{key}_dollars") not in (None, ""):
        try:
            return float(block[f"{key}_dollars"])
        except ValueError:
            return None
    if block.get(key) is not None:
        return float(block[key]) / 100
    return None


def parse_candle(c):
    out = {"ts": int(c["end_period_ts"])}
    for side, name in (("yes_bid", "bid"), ("yes_ask", "ask")):
        for key in ("open", "low", "high", "close"):
            out[f"{name}_{key}"] = _price(c.get(side), key)
    out["price_close"] = _price(c.get("price"), "close")
    out["volume"] = _number(c.get("volume"), c.get("volume_fp"))
    out["open_interest"] = _number(c.get("open_interest"), c.get("open_interest_fp"))
    return out


def _number(*values):
    """The first value that reads as a number (Kalshi sends some as fixed-point strings)."""
    for v in values:
        if v in (None, ""):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def market_result(m):
    """(1 / 0 / None, settled price, flag). A player who's active but never plays is settled at
    the last fair price before tip-off: kept, but flagged, since it isn't a yes/no outcome."""
    result = (m.get("result") or "").lower()
    if result == "yes":
        return 1, 1.0, ""
    if result == "no":
        return 0, 0.0, ""
    value = m.get("settlement_value_dollars")
    if value in (None, "") and m.get("settlement_value") is not None:
        value = float(m["settlement_value"]) / 100
    try:
        value = float(value) if value not in (None, "") else None
    except ValueError:
        value = None
    if value is not None and value in (0.0, 1.0):
        return int(value), value, ""
    return None, value, "price_settlement" if value is not None else "unsettled"


# ---------------------------------------------------------------------------
# Kalshi: series, events, candles
# ---------------------------------------------------------------------------

def find_series(stat, log=print):
    """The Kalshi series ticker for a stat: the expected one if it exists, else a title search."""
    expected = SERIES[stat]
    try:
        kalshi(f"/series/{expected}")
        return expected
    except RuntimeError as e:
        log(f"  series {expected} not found ({e}); searching by title")
    word = SERIES_TITLE_WORDS[stat]
    cursor = None
    for _ in range(20):
        page = kalshi("/series", category="Sports", cursor=cursor, limit=200)
        for s in page.get("series", []):
            title = (s.get("title") or "").lower()
            if "nba" in title and "player" in title and word in title and "protected" not in title:
                log(f"  using series {s.get('ticker')} ({s.get('title')})")
                return s.get("ticker")
        cursor = page.get("cursor")
        if not cursor:
            break
    raise RuntimeError(f"no Kalshi series found for NBA player {word}")


def settled_events(series, log=print):
    """{event_ticker: {"title", "markets": [market dicts], "archived": bool}} for settled events,
    from both the live API and the historical archive."""
    events = {}
    cursor = None
    while True:
        page = kalshi("/events", series_ticker=series, status="settled", with_nested_markets="true",
                      limit=200, cursor=cursor)
        for e in page.get("events", []):
            events[e["event_ticker"]] = {"title": e.get("title", ""), "markets": e.get("markets") or [],
                                         "archived": False}
        cursor = page.get("cursor")
        if not cursor:
            break
    cursor = None
    try:
        while True:
            page = kalshi("/historical/markets", series_ticker=series, limit=1000, cursor=cursor)
            for m in page.get("markets", []):
                entry = events.setdefault(m["event_ticker"], {"title": m.get("title", ""), "markets": [],
                                                              "archived": True})
                if all(x.get("ticker") != m["ticker"] for x in entry["markets"]):
                    entry["markets"].append(m)
                    entry["archived"] = True
            cursor = page.get("cursor")
            if not cursor:
                break
    except RuntimeError as e:
        log(f"  historical markets for {series} unavailable ({e})")
    return events


def event_candles(series, event, start, end, interval, archived, tickers):
    """{market ticker: [candle dicts]} for one event between start and end (Unix seconds)."""
    out = {t: [] for t in tickers}
    if not archived:
        cursor_start = start
        for _ in range(50):
            try:
                data = kalshi(f"/series/{series}/events/{event}/candlesticks", start_ts=cursor_start,
                              end_ts=end, period_interval=interval)
            except RuntimeError:
                break                                   # fall back to one market at a time
            if not data:
                break
            names = data.get("market_tickers") or []
            for ticker, candles in zip(names, data.get("market_candlesticks") or []):
                out.setdefault(ticker, []).extend(parse_candle(c) for c in candles or [])
            adjusted = data.get("adjusted_end_ts")
            if not adjusted or adjusted >= end or adjusted <= cursor_start:
                break
            cursor_start = adjusted
        if any(out.values()):
            return out
    # Archived events (or an empty event response): one market at a time
    for ticker in tickers:
        for path in (f"/historical/markets/{ticker}/candlesticks",
                     f"/series/{series}/markets/{ticker}/candlesticks"):
            try:
                data = kalshi(path, start_ts=start, end_ts=end, period_interval=interval)
            except RuntimeError:
                continue
            candles = [parse_candle(c) for c in data.get("candlesticks") or []]
            if candles:
                out[ticker] = candles
                break
    return out


# ---------------------------------------------------------------------------
# ESPN: find the game, rebuild the game state from play-by-play
# ---------------------------------------------------------------------------

_scoreboards = {}


def find_game(day, kalshi_a, kalshi_b, title):
    """The ESPN game for a Kalshi event: (game id, home code, away code), or None."""
    if day not in _scoreboards:
        _scoreboards[day] = espn(f"/scoreboard?dates={day.strftime('%Y%m%d')}").get("events", [])
    wanted = {KALSHI_TO_ESPN.get(kalshi_a, kalshi_a), KALSHI_TO_ESPN.get(kalshi_b, kalshi_b)}
    nicknames = {w.lower() for w in re.findall(r"([A-Za-z0-9]+)(?:\s+vs\b|\s*:)", title or "")}
    for event in _scoreboards[day]:
        comp = (event.get("competitions") or [{}])[0]
        sides = {c.get("homeAway"): c.get("team") or {} for c in comp.get("competitors", [])}
        codes = {t.get("abbreviation") for t in sides.values()}
        names = {(t.get("name") or t.get("shortDisplayName") or "").split()[-1].lower()
                 for t in sides.values() if (t.get("name") or t.get("shortDisplayName"))}
        if codes == wanted or (nicknames and nicknames <= names):
            return str(event["id"]), sides["home"].get("abbreviation"), sides["away"].get("abbreviation")
    return None


def _clock_seconds(text):
    text = str(text or "0")
    if ":" in text:
        minutes, seconds = text.split(":")[:2]
        return int(minutes) * 60 + float(seconds)
    try:
        return float(text)
    except ValueError:
        return 0.0


def _elapsed(period, remaining):
    length = 720 if period <= 4 else 300
    before = (period - 1) * 720 if period <= 4 else 2880 + (period - 5) * 300
    return before + (length - remaining)


class GameState:
    """A game's play-by-play turned into timelines that can be looked up at any moment."""

    def __init__(self, summary):
        comp = ((summary.get("header") or {}).get("competitions") or [{}])[0]
        self.team_side, self.side_of = {}, {}
        for c in comp.get("competitors", []):
            team = c.get("team") or {}
            self.team_side[str(team.get("id"))] = c.get("homeAway")
            self.side_of[team.get("abbreviation")] = c.get("homeAway")
        # Box score: final totals and names, to check the rebuild
        self.box, self.names, self.player_team = {}, {}, {}
        for block in (summary.get("boxscore") or {}).get("players", []):
            team = (block.get("team") or {}).get("abbreviation")
            for group in block.get("statistics", []):
                labels = group.get("labels") or []
                for a in group.get("athletes", []):
                    athlete = a.get("athlete") or {}
                    pid = str(athlete.get("id"))
                    self.names[pid] = athlete.get("displayName") or ""
                    self.player_team[pid] = team
                    stats = dict(zip(labels, a.get("stats") or []))
                    if stats:
                        self.box[pid] = {k: _int(stats.get(lab)) for k, lab in
                                         (("pts", "PTS"), ("reb", "REB"), ("ast", "AST"))}
        # Rebuild running totals from the plays
        self.game_times, self.game_rows = [], []
        self.player_times, self.player_rows = {}, {}
        totals = {}
        last_ts = None
        self.tip_ts = self.end_ts = None
        for play in summary.get("plays", []):
            ts = _ts(play.get("wallclock")) or last_ts
            if ts is None:
                continue
            last_ts = ts
            self.tip_ts = ts if self.tip_ts is None else self.tip_ts
            self.end_ts = ts
            kind = ((play.get("type") or {}).get("text") or "").lower()
            text = (play.get("text") or "").lower()
            parts = [str(p["athlete"]["id"]) for p in play.get("participants") or []
                     if (p.get("athlete") or {}).get("id") is not None]
            changed = set()

            def add(pid, stat, amount=1):
                t = totals.setdefault(pid, {"pts": 0, "reb": 0, "ast": 0, "fouls": 0})
                t[stat] += amount
                changed.add(pid)
            if play.get("scoringPlay") and parts:
                add(parts[0], "pts", int(play.get("scoreValue") or 0))
                if len(parts) > 1 and "assist" in text:
                    add(parts[1], "ast")
            elif "rebound" in kind and parts:
                add(parts[0], "reb")
            if kind.endswith("foul") and "technical" not in kind and parts:
                add(parts[0], "fouls")
            period = int((play.get("period") or {}).get("number") or 0)
            remaining = _clock_seconds((play.get("clock") or {}).get("displayValue"))
            self.game_times.append(ts)
            self.game_rows.append({"period": period, "clock_s": remaining,
                                   "elapsed_s": _elapsed(period, remaining) if period else 0,
                                   "home_score": _int(play.get("homeScore")),
                                   "away_score": _int(play.get("awayScore"))})
            for pid in changed:
                self.player_times.setdefault(pid, []).append(ts)
                self.player_rows.setdefault(pid, []).append(dict(totals[pid]))
        self.totals = totals

    def mismatches(self):
        """Players whose rebuilt totals don't match the box score: {id: (rebuilt, box)}."""
        bad = {}
        for pid, box in self.box.items():
            mine = self.totals.get(pid, {"pts": 0, "reb": 0, "ast": 0})
            got = {k: mine.get(k, 0) for k in ("pts", "reb", "ast")}
            if any(box[k] is not None and got[k] != box[k] for k in got):
                bad[pid] = (got, box)
        return bad

    def player_id(self, name):
        key = _person(name)
        matches = [pid for pid, n in self.names.items() if _person(n) == key]
        return matches[0] if len(matches) == 1 else None

    def ts_at(self, elapsed_s):
        """When the game clock first reached elapsed_s seconds of game time (Unix time), or None."""
        for t, row in zip(self.game_times, self.game_rows):
            if row["period"] >= 1 and row["elapsed_s"] >= elapsed_s:
                return t
        return None

    def at(self, ts, pid):
        """Game and player state at Unix time ts."""
        if self.tip_ts is None or ts < self.tip_ts:
            state = {"phase": "pregame", "period": 0, "clock_s": None, "elapsed_s": 0,
                     "home_score": 0, "away_score": 0}
        else:
            i = bisect.bisect_right(self.game_times, ts) - 1
            state = {"phase": "final" if ts > self.end_ts else "live", **self.game_rows[i]}
        times = self.player_times.get(pid, [])
        j = bisect.bisect_right(times, ts) - 1
        player = self.player_rows[pid][j] if j >= 0 else {"pts": 0, "reb": 0, "ast": 0, "fouls": 0}
        state.update({f"player_{k}": v for k, v in player.items()})
        side = self.side_of.get(self.player_team.get(pid))
        home, away = state.get("home_score") or 0, state.get("away_score") or 0
        state["margin"] = (home - away if side == "home" else away - home) if side else None
        return state


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Checkpoints and best-entry windows for one contract
# ---------------------------------------------------------------------------

def _quote_at(candles, times, t):
    """The latest quote at Unix time t: the candle ending at or before t (prices carry forward
    through minutes with no candle). Returns (candle or None, minutes since that candle)."""
    i = bisect.bisect_right(times, t) - 1
    if i < 0:
        return None, None
    return candles[i], round((t - candles[i]["ts"]) / 60, 1)


def _volume_near(candles, times, t, before=300, after=0):
    lo, hi = bisect.bisect_left(times, t - before), bisect.bisect_right(times, t + after)
    total = sum(c["volume"] or 0 for c in candles[lo:hi] if c["interval"] == 1)
    return total


def contract_checkpoints(ticker, candles, state, pid, tip, day):
    """One row per checkpoint: pregame clock times, tip-off, then every
    CHECKPOINT_EVERY_GAME_MIN minutes of game clock through CHECKPOINT_THROUGH_GAME_MIN."""
    candles = sorted(candles, key=lambda c: c["ts"])
    times = [c["ts"] for c in candles]
    points = []
    if candles:
        points.append(("first_quote", "pregame", candles[0]["ts"], None))
    if day:
        start = _game_day_start(day)                     # 8am Eastern
        for label, hour in PREGAME_CHECKPOINTS_ET.items():
            t = start + (hour - GAME_DAY_START_HOUR_ET) * 3600
            if t < tip:
                points.append((label, "pregame", t, None))
    # Spreads often widen in the last minute before tip-off, so a fair "right before the game"
    # baseline may be a few minutes earlier: tip_minus_5 / tip_minus_2 make that comparison possible.
    for label, minutes in (("tip_minus_60", 60), ("tip_minus_15", 15), ("tip_minus_5", 5),
                           ("tip_minus_2", 2), ("tip", 0)):
        points.append((label, "pregame", tip - minutes * 60, None))
    if state is not None and state.tip_ts:
        for m in range(CHECKPOINT_EVERY_GAME_MIN, CHECKPOINT_THROUGH_GAME_MIN + 1, CHECKPOINT_EVERY_GAME_MIN):
            t = state.ts_at(m * 60)
            if t is not None:
                points.append((f"game_{m:02d}m", "game", t, m))
    rows = []
    for label, kind, t, game_min in points:
        c, stale = _quote_at(candles, times, t)
        row = {"market_ticker": ticker, "checkpoint": label, "kind": kind, "game_minute": game_min,
               "ts": t, "bid": c["bid_close"] if c else None, "ask": c["ask_close"] if c else None,
               "last": c["price_close"] if c else None, "minutes_since_quote": stale,
               "volume_last_5m": _volume_near(candles, times, t)}
        if state is not None and state.tip_ts:
            row.update(state.at(t, pid))
        rows.append(row)
    return rows


def contract_windows(ticker, candles, state, pid, tip):
    """Best entry (lowest ask) in each window: the whole pregame day, the last hour before tip,
    and the first 6 minutes / first quarter / first half of game clock."""
    candles = sorted(candles, key=lambda c: c["ts"])
    times = [c["ts"] for c in candles]
    windows = [("pregame_day", times[0] - 1 if times else tip - 86400, tip),
               ("pregame_last_hour", tip - 3600, tip)]
    if state is not None and state.tip_ts:
        for name, minutes in GAME_WINDOWS.items():
            end = state.ts_at(minutes * 60)
            if end is not None:
                windows.append((name, tip, end))
    rows = []
    for name, start, end in windows:
        inside = [c for c in candles if start < c["ts"] <= end and c["ask_close"] is not None]
        row = {"market_ticker": ticker, "window": name, "start_ts": start, "end_ts": end,
               "candles": len(inside)}
        if inside:
            best = min(inside, key=lambda c: (c["ask_close"], c["ts"]))
            lows = [c["ask_low"] for c in inside if c["ask_low"] is not None]
            row.update({"min_ask": best["ask_close"], "min_ask_ts": best["ts"],
                        "min_ask_low": min(lows) if lows else None,
                        "bid_then": best["bid_close"],
                        "volume_near_min": _volume_near(candles, times, best["ts"], 120, 120)})
            if state is not None and state.tip_ts:
                row.update(state.at(best["ts"], pid))
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# One event -> rows
# ---------------------------------------------------------------------------

_games = {}
_raw_games = {}          # game id -> compact play-by-play record, saved for later player profiles


def compact_game(gid, day, summary):
    """A compact copy of an ESPN game summary: every play (time, clock, type, text, players,
    scoring, score) and every player's box score line, for building player profiles later
    (scoring by quarter, pace through the game, streaks)."""
    comp = ((summary.get("header") or {}).get("competitions") or [{}])[0]
    teams = {}
    for c in comp.get("competitors", []):
        team = c.get("team") or {}
        teams[c.get("homeAway")] = {"id": str(team.get("id")), "abbreviation": team.get("abbreviation")}
    players = []
    for block in (summary.get("boxscore") or {}).get("players", []):
        team = (block.get("team") or {}).get("abbreviation")
        for group in block.get("statistics", []):
            labels = group.get("labels") or group.get("keys") or []
            for a in group.get("athletes", []):
                athlete = a.get("athlete") or {}
                players.append({"id": str(athlete.get("id")), "name": athlete.get("displayName"),
                                "team": team, "position": (athlete.get("position") or {}).get("abbreviation"),
                                "starter": a.get("starter"), "did_not_play": a.get("didNotPlay"),
                                "stats": dict(zip(labels, a.get("stats") or []))})
    plays = []
    for p in summary.get("plays", []):
        plays.append({
            "seq": p.get("sequenceNumber"), "wallclock": p.get("wallclock"),
            "period": (p.get("period") or {}).get("number"), "clock": (p.get("clock") or {}).get("displayValue"),
            "type": (p.get("type") or {}).get("text"), "text": p.get("text"),
            "team": str((p.get("team") or {}).get("id")) if p.get("team") else None,
            "players": [str(x["athlete"]["id"]) for x in p.get("participants") or []
                        if (x.get("athlete") or {}).get("id") is not None],
            "scoring": p.get("scoringPlay"), "points": p.get("scoreValue"), "shooting": p.get("shootingPlay"),
            "home": p.get("homeScore"), "away": p.get("awayScore")})
    return {"game_id": gid, "date": day.isoformat() if day else None, "teams": teams,
            "players": players, "plays": plays}


def write_play_by_play(new_games, state_dir, out_dir, mode, log=print):
    """Add games to the monthly nba_pbp_YYYY-MM.jsonl.gz files (one JSON line per game)."""
    by_month = {}
    for g in new_games:
        by_month.setdefault((g["date"] or "unknown")[:7], []).append(g)
    os.makedirs(out_dir, exist_ok=True)
    for month, games in by_month.items():
        name = f"nba_pbp_{month}.jsonl.gz"
        existing = {}
        old_path = os.path.join(state_dir, name)
        if mode != "backfill" and os.path.exists(old_path):
            with gzip.open(old_path, "rt", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        record = json.loads(line)
                        existing[record["game_id"]] = record
        for g in games:
            existing[g["game_id"]] = g
        with gzip.open(os.path.join(out_dir, name), "wt", encoding="utf-8") as f:
            for gid in sorted(existing, key=lambda x: (existing[x]["date"] or "", x)):
                f.write(json.dumps(existing[gid], separators=(",", ":")) + "\n")
        log(f"  play-by-play: {len(games)} games added to {name} ({len(existing)} in the file)")


def process_event(stat, series, event_ticker, info, log=print):
    """(market rows, minute rows, checkpoint rows, window rows) for one settled event."""
    parsed = parse_event_ticker(event_ticker)
    markets = [m for m in info["markets"] if m.get("ticker")]
    base_flags = []
    game = None
    if parsed is None:
        base_flags.append("ticker_unparsed")
        day = None
    else:
        day, team_a, team_b = parsed
        try:
            game = find_game(day, team_a, team_b, info.get("title"))
        except RuntimeError as e:
            log(f"  {event_ticker}: ESPN scoreboard unavailable ({e})")
        if game is None:
            base_flags.append("game_unmatched")
    state = None
    if game:
        gid = game[0]
        if gid not in _games:
            try:
                summary = espn(f"/summary?event={gid}")
                _games[gid] = GameState(summary)
                if summary.get("plays"):
                    _raw_games[gid] = compact_game(gid, day, summary)
            except RuntimeError as e:
                log(f"  {event_ticker}: ESPN summary unavailable ({e})")
                _games[gid] = None
        state = _games[gid]
        if state is None or state.tip_ts is None:
            base_flags.append("no_play_by_play")

    # Time windows: hourly from 8am Eastern (or market open), minutes from an hour before tip
    opens = [_ts(m.get("open_time")) for m in markets if _ts(m.get("open_time"))]
    closes = [_ts(m.get("close_time")) for m in markets if _ts(m.get("close_time"))]
    tip = state.tip_ts if state and state.tip_ts else None
    end = (state.end_ts + POSTGAME_MINUTES * 60) if state and state.end_ts else (max(closes) if closes else None)
    if tip is None and closes:
        tip = max(closes) - 3 * 3600                      # rough fallback without play-by-play
    if tip is None or end is None:
        log(f"  {event_ticker}: no game times; skipped")
        return [], [], [], []
    day_start = _game_day_start(day) if day else tip - 12 * 3600
    hourly_start = max(day_start, min(opens) if opens else day_start)
    minute_start = tip - PREGAME_MINUTE_CANDLES * 60
    tickers = [m["ticker"] for m in markets]
    hourly = event_candles(series, event_ticker, hourly_start, minute_start, 60, info["archived"], tickers) \
        if hourly_start < minute_start else {t: [] for t in tickers}
    minute = event_candles(series, event_ticker, minute_start, end, 1, info["archived"], tickers)

    market_rows, minute_rows, checkpoint_rows, window_rows = [], [], [], []
    bad = state.mismatches() if state and state.tip_ts else {}
    for m in markets:
        ticker = m["ticker"]
        name, threshold = parse_market_name(m)
        result, settled, settle_flag = market_result(m)
        flags = list(base_flags) + ([settle_flag] if settle_flag else [])
        pid = state.player_id(name) if state and name else None
        if state and name and pid is None:
            flags.append("player_unmatched")
        if pid in bad:
            flags.append("pbp_mismatch")
        candles = [dict(c, interval=60) for c in hourly.get(ticker, [])] + \
                  [dict(c, interval=1) for c in minute.get(ticker, [])]
        if not candles:
            flags.append("no_candles")
        pre = [c for c in candles if c["ts"] <= tip]
        last_pre = pre[-1] if pre else None
        final_stat = state.box.get(pid, {}).get(stat) if state and pid else None
        market_rows.append({
            "market_ticker": ticker, "event_ticker": event_ticker, "series": series, "stat": stat,
            "game_date": day.isoformat() if day else None, "espn_game_id": game[0] if game else None,
            "home": game[1] if game else None, "away": game[2] if game else None,
            "player_name": name, "espn_player_id": pid,
            "team": state.player_team.get(pid) if state and pid else None,
            "threshold": threshold, "result": result, "settled_price": settled,
            "final_stat": final_stat, "volume": _number(m.get("volume"), m.get("volume_fp")),
            "tip_ts": tip, "end_ts": end,
            "bid_at_tip": last_pre["bid_close"] if last_pre else None,
            "ask_at_tip": last_pre["ask_close"] if last_pre else None,
            "price_at_tip": last_pre["price_close"] if last_pre else None,
            "flags": ",".join(flags),
        })
        for c in candles:
            row = {"market_ticker": ticker, **c}
            if state and state.tip_ts:
                row.update(state.at(c["ts"], pid))
            minute_rows.append(row)
        if candles:
            checkpoint_rows += contract_checkpoints(ticker, candles, state if state and state.tip_ts else None,
                                                    pid, tip, day)
            window_rows += contract_windows(ticker, candles, state if state and state.tip_ts else None, pid, tip)
    return market_rows, minute_rows, checkpoint_rows, window_rows


# ---------------------------------------------------------------------------
# What the scanner would have shown before each game (no peeking at later games)
# ---------------------------------------------------------------------------

def _nba_group(position):
    position = str(position or "").upper()
    if position.startswith("C"):
        return "C"
    if position.startswith("F") or position in ("SF", "PF"):
        return "F"
    return "G"


def add_context(markets, folder, log=print):
    """Add pre_* columns to the markets table: each player's numbers as they stood before that
    game, from the published NBA player-games files (nba_*_player_games.csv) in `folder`:
      pre_games, pre_avg_l5 / l10 / season, pre_hit_l10 / season (share of games at or above
      the contract's threshold), pre_line80_l10 (his 80% line), pre_flagged80 (the scanner
      would have shown this threshold), pre_rest_days, and the opponent's matchup then:
      pre_opp, pre_opp_allowed (per game, to his position group), pre_opp_league, pre_opp_change,
      pre_opp_rank (1 = allows the least), pre_opp_teams, pre_opp_verdict."""
    import glob
    markets = markets.drop(columns=[c for c in markets.columns if c.startswith("pre_")])
    files = sorted(glob.glob(os.path.join(folder, "nba_*_player_games.csv")))
    if not files:
        log("  no NBA player-games file found: skipping the pre-game numbers")
        return markets
    games = pd.concat([pd.read_csv(f, dtype={"player_id": str, "game_id": str}) for f in files])
    games["group"] = games["position"].map(_nba_group)
    for stat in ("pts", "reb", "ast"):
        games[stat] = pd.to_numeric(games[stat], errors="coerce").fillna(0)
    history = {pid: g.sort_values(["game_date", "game_id"]) for pid, g in games.groupby("player_id")}
    matchup_cache = {}

    def matchups_before(date):
        """{(defense, group): {stat: allowed per game}} and league averages, from games before date."""
        if date not in matchup_cache:
            prior = games[games["game_date"] < date]
            totals = prior.groupby(["opponent", "group"])[["pts", "reb", "ast"]].sum()
            played = prior.groupby("opponent")["game_id"].nunique()
            table = {}
            for (defense, group), row in totals.iterrows():
                n = played.get(defense, 0)
                if n:
                    table[(defense, group)] = {s: row[s] / n for s in ("pts", "reb", "ast")}
            matchup_cache[date] = table
        return matchup_cache[date]

    rows = []
    for m in markets.itertuples(index=False):
        out = {}
        pid, date, stat, threshold = m.espn_player_id, m.game_date, m.stat, m.threshold
        hist = history.get(str(pid)) if isinstance(pid, str) or pd.notna(pid) else None
        if hist is not None and isinstance(date, str) and pd.notna(threshold):
            before = hist[hist["game_date"] < date]
            values = before[stat].tolist()
            l5, l10 = values[-5:], values[-10:]
            out["pre_games"] = len(values)
            if values:
                floor = FLAG_FLOORS.get(stat, 1)
                ordered = sorted(l10, reverse=True)
                line80 = ordered[max(1, math.ceil(0.8 * len(l10) - 1e-9)) - 1]
                hit10 = sum(v >= threshold for v in l10) / len(l10)
                out.update({
                    "pre_avg_l5": sum(l5) / len(l5), "pre_avg_l10": sum(l10) / len(l10),
                    "pre_avg_season": sum(values) / len(values), "pre_hit_l10": hit10,
                    "pre_hit_season": sum(v >= threshold for v in values) / len(values),
                    "pre_line80_l10": line80,
                    "pre_flagged80": bool(len(l10) >= 5 and hit10 >= 0.8 - 1e-9 and threshold >= floor
                                          and sum(l10) / len(l10) >= floor),
                    "pre_rest_days": (dt.date.fromisoformat(date) -
                                      dt.date.fromisoformat(before["game_date"].iloc[-1])).days})
                team = m.team if isinstance(m.team, str) else before["team"].iloc[-1]
                opponent = m.away if team == m.home else (m.home if team == m.away else None)
                group = _nba_group(before["position"].iloc[-1])
                table = matchups_before(date)
                if opponent and (opponent, group) in table:
                    pool = [v[stat] for (d, g), v in table.items() if g == group]
                    value = table[(opponent, group)][stat]
                    league = sum(pool) / len(pool)
                    change = value / league - 1 if league else 0.0
                    out.update({"pre_opp": opponent, "pre_opp_allowed": value, "pre_opp_league": league,
                                "pre_opp_change": change,
                                "pre_opp_rank": 1 + sum(1 for v in pool if v < value - 1e-9),
                                "pre_opp_teams": len(pool),
                                "pre_opp_verdict": "favorable" if change >= 0.05 else
                                                   ("tough" if change <= -0.05 else "neutral")})
        rows.append(out)
    context = pd.DataFrame(rows, index=markets.index)
    flagged = int(context.get("pre_flagged80", pd.Series(dtype=bool)).fillna(False).sum())
    log(f"  pre-game numbers added for {context.notna().any(axis=1).sum()} of {len(markets)} contracts "
        f"({flagged} the scanner would have flagged)")
    return pd.concat([markets, context], axis=1)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _have_parquet():
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        return False


def read_table(folder, stem):
    for ext, reader in ((".parquet", pd.read_parquet), (".csv.gz", pd.read_csv)):
        path = os.path.join(folder, stem + ext)
        if os.path.exists(path):
            try:
                df = reader(path)
            except Exception as e:                     # unreadable: start over for this file
                print(f"  couldn't read {path} ({e})")
                continue
            if "flags" in df.columns:
                df["flags"] = df["flags"].fillna("")
            return df
    return None


def write_table(df, folder, stem):
    os.makedirs(folder, exist_ok=True)
    if _have_parquet():
        df.to_parquet(os.path.join(folder, stem + ".parquet"), index=False)
    else:
        df.to_csv(os.path.join(folder, stem + ".csv.gz"), index=False, compression="gzip")


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def probe(log=print):
    """Check one recent game for each stat end to end, and print what was found."""
    for stat in SERIES:
        log(f"\n=== {stat}")
        try:
            series = find_series(stat, log)
        except RuntimeError as e:
            log(f"  {e}")
            continue
        events = settled_events(series, log)
        live = sum(1 for e in events.values() if not e["archived"])
        log(f"  series {series}: {len(events)} settled events ({live} live, {len(events) - live} archived)")
        dated = sorted(((parse_event_ticker(t) or (dt.date.min,))[0], t) for t in events)
        if not dated:
            continue
        _, ticker = dated[-1]
        info = events[ticker]
        log(f"  newest: {ticker} | {info['title']} | {len(info['markets'])} markets")
        sample = info["markets"][0] if info["markets"] else {}
        log("  market fields: " + ", ".join(sorted(sample)[:40]))
        log("  first market: " + json.dumps({k: sample.get(k) for k in
            ("ticker", "title", "yes_sub_title", "subtitle", "floor_strike", "result",
             "settlement_value_dollars", "status", "open_time", "close_time", "volume")}))
        markets, minutes, checkpoints, windows = process_event(stat, series, ticker, info, log)
        frame = pd.DataFrame(minutes)
        log(f"  rows: {len(markets)} markets, {len(frame)} candles")
        if markets:
            flags = pd.Series([m["flags"] for m in markets]).value_counts().to_dict()
            log(f"  flags: {flags}")
            clean = [x for x in markets if not x["flags"]] or markets
            m = max(clean, key=lambda x: (len(frame[frame["market_ticker"] == x["market_ticker"]])
                                          if len(frame) else 0, x["volume"] or 0))
            log("  example: " + json.dumps({k: m[k] for k in ("market_ticker", "player_name", "threshold",
                "result", "final_stat", "volume", "bid_at_tip", "ask_at_tip", "flags")}))
        if len(frame) and markets:
            # How complete are the in-game minutes? (a candle every minute, or only on changes)
            live = frame[(frame["interval"] == 1) & (frame.get("phase") == "live")] if "phase" in frame else frame.iloc[0:0]
            tip, end = markets[0]["tip_ts"], markets[0]["end_ts"]
            game_minutes = max(1, (end - tip) // 60)
            per_market = live.groupby("market_ticker").size()
            clean_tickers = [x["market_ticker"] for x in markets if not x["flags"]]
            per_clean = per_market.reindex(clean_tickers).fillna(0)
            if len(per_clean):
                log(f"  in-game minute candles per active contract: median {per_clean.median():.0f}, "
                    f"min {per_clean.min():.0f}, max {per_clean.max():.0f} (game spans ~{game_minutes} minutes)")
                vol = frame["volume"].dropna()
                log(f"  candle volume: {len(vol)} of {len(frame)} candles have it; total {vol.sum():.0f}")
            first = m["market_ticker"]
            one = frame[frame["market_ticker"] == first]
            cols = [c for c in ("ts", "interval", "bid_close", "ask_close", "ask_low", "price_close",
                                "volume", "phase", "period", "clock_s", "player_pts", "player_reb",
                                "player_ast") if c in one.columns]
            log(f"  candles for {first}: {len(one)} ({(one['interval'] == 1).sum()} one-minute)")
            log(one[cols].iloc[::max(1, len(one) // 12)].to_string(index=False))
        if _raw_games:
            raw = next(iter(_raw_games.values()))
            size = len(gzip.compress(json.dumps(raw).encode()))
            log(f"  play-by-play archive: {len(raw['plays'])} plays, {len(raw['players'])} players, "
                f"~{size // 1024} KB compressed per game")
        game = next(iter(_games.values()), None)
        if game is not None:
            bad = game.mismatches()
            log(f"  play-by-play check: {len(game.box)} players, {len(bad)} don't match the box score")
            for pid, (got, box) in list(bad.items())[:5]:
                log(f"    {game.names.get(pid)}: rebuilt {got}, box {box}")
        if checkpoints and markets:
            cp = pd.DataFrame(checkpoints)
            one = cp[cp["market_ticker"] == m["market_ticker"]]
            cols = [c for c in ("checkpoint", "bid", "ask", "minutes_since_quote", "volume_last_5m", "period",
                                "clock_s", "player_pts", "player_reb", "player_ast", "player_fouls", "margin")
                    if c in one.columns]
            log(f"  checkpoints for {m['market_ticker']}:")
            log(one[cols].to_string(index=False))
            wn = pd.DataFrame(windows)
            one = wn[wn["market_ticker"] == m["market_ticker"]]
            cols = [c for c in ("window", "candles", "min_ask", "min_ask_low", "bid_then", "volume_near_min",
                                "period", "clock_s", "player_pts", "player_fouls", "margin") if c in one.columns]
            log("  best entries:")
            log(one[cols].to_string(index=False))


def run(mode, state_dir, out_dir, log=print, since=None):
    since = since or SEASON_START
    stored = None if mode == "backfill" else read_table(state_dir, "kalshi_nba_markets")
    done = set(stored["event_ticker"]) if stored is not None else set()
    new_markets, new_minutes, new_checkpoints, new_windows, processed = [], [], [], [], 0
    for stat in SERIES:
        try:
            series = find_series(stat, log)
        except RuntimeError as e:
            log(f"{stat}: {e}")
            continue
        events = settled_events(series, log)
        todo = sorted(t for t in events
                      if t not in done and (parse_event_ticker(t) or (dt.date.min,))[0] >= since)
        log(f"{stat} ({series}): {len(events)} settled events, {len(todo)} to add")
        for ticker in todo:
            if processed >= MAX_EVENTS_PER_RUN:
                log(f"reached {MAX_EVENTS_PER_RUN} events this run; the next run continues")
                break
            try:
                markets, minutes, checkpoints, windows = process_event(stat, series, ticker, events[ticker], log)
            except Exception as e:                    # one bad event shouldn't stop the run
                log(f"  {ticker}: failed ({e})")
                continue
            new_markets += markets
            new_minutes += minutes
            new_checkpoints += checkpoints
            new_windows += windows
            processed += 1
            if processed % 25 == 0:
                log(f"  {processed} events processed")
    log(f"processed {processed} events: {len(new_markets)} markets, {len(new_minutes)} candles")
    if _raw_games:
        write_play_by_play(list(_raw_games.values()), state_dir, out_dir, mode, log)
    if not new_markets:
        return
    markets = pd.DataFrame(new_markets)
    if stored is not None:
        markets = pd.concat([stored[~stored["market_ticker"].isin(markets["market_ticker"])], markets])
    markets = add_context(markets.reset_index(drop=True), state_dir, log)
    write_table(markets.sort_values(["game_date", "event_ticker", "market_ticker"]), out_dir,
                "kalshi_nba_markets")
    dates = markets.set_index("market_ticker")["game_date"]
    for name, rows, order in (("minutes", new_minutes, ["market_ticker", "ts"]),
                              ("checkpoints", new_checkpoints, ["market_ticker", "ts"]),
                              ("windows", new_windows, ["market_ticker", "start_ts", "end_ts"])):
        frame = pd.DataFrame(rows)
        if not len(frame):
            continue
        frame["month"] = frame["market_ticker"].map(dates).str[:7]
        for month, part in frame.groupby("month"):
            old = None if mode == "backfill" else read_table(state_dir, f"kalshi_nba_{name}_{month}")
            part = part.drop(columns="month")
            if old is not None:
                part = pd.concat([old[~old["market_ticker"].isin(part["market_ticker"])], part])
            write_table(part.sort_values(order), out_dir, f"kalshi_nba_{name}_{month}")
    flagged = markets["flags"].fillna("").str.len().gt(0).sum()
    log(f"stored {len(markets)} markets in total ({flagged} with flags)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["probe", "update", "backfill"], default="update")
    parser.add_argument("--state", default="kalshi_state", help="folder with files published so far")
    parser.add_argument("--out", default="kalshi_out", help="folder for the files to publish")
    parser.add_argument("--since", default="", help="first game date to include, e.g. 2025-10-01")
    args = parser.parse_args()
    since = dt.date.fromisoformat(args.since) if args.since else SEASON_START
    if not _have_parquet():
        print("(pyarrow isn't installed: writing gzipped CSV instead of Parquet)")
    if args.mode == "probe":
        probe()
    else:
        run(args.mode, args.state, args.out, since=since)


if __name__ == "__main__":
    sys.exit(main())

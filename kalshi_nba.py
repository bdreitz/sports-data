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
SEASON_START = dt.date(2026, 10, 1)          # props launched in October 2026

# Kalshi team code -> ESPN team code, where they differ
KALSHI_TO_ESPN = {"GSW": "GS", "NOP": "NO", "NYK": "NY", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH"}

PREGAME_MINUTE_CANDLES = 60                  # one-minute candles start this long before tip-off
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
    out["volume"] = c.get("volume")
    out["open_interest"] = c.get("open_interest")
    return out


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
        self.team_side = {}
        for c in comp.get("competitors", []):
            self.team_side[str((c.get("team") or {}).get("id"))] = c.get("homeAway")
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
        return state


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# One event -> rows
# ---------------------------------------------------------------------------

_games = {}


def process_event(stat, series, event_ticker, info, log=print):
    """(market rows, minute rows) for one settled event."""
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
                _games[gid] = GameState(espn(f"/summary?event={gid}"))
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
        return [], []
    day_start = _game_day_start(day) if day else tip - 12 * 3600
    hourly_start = max(day_start, min(opens) if opens else day_start)
    minute_start = tip - PREGAME_MINUTE_CANDLES * 60
    tickers = [m["ticker"] for m in markets]
    hourly = event_candles(series, event_ticker, hourly_start, minute_start, 60, info["archived"], tickers) \
        if hourly_start < minute_start else {t: [] for t in tickers}
    minute = event_candles(series, event_ticker, minute_start, end, 1, info["archived"], tickers)

    market_rows, minute_rows = [], []
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
            "final_stat": final_stat, "volume": m.get("volume"),
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
    return market_rows, minute_rows


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
        markets, minutes = process_event(stat, series, ticker, info, log)
        frame = pd.DataFrame(minutes)
        log(f"  rows: {len(markets)} markets, {len(frame)} candles")
        if markets:
            flags = pd.Series([m["flags"] for m in markets]).value_counts().to_dict()
            log(f"  flags: {flags}")
            m = next((x for x in markets if x["espn_player_id"]), markets[0])
            log("  example: " + json.dumps({k: m[k] for k in ("player_name", "threshold", "result",
                "final_stat", "bid_at_tip", "ask_at_tip", "flags")}))
        if len(frame):
            first = frame["market_ticker"].iloc[0]
            one = frame[frame["market_ticker"] == first]
            cols = [c for c in ("ts", "interval", "bid_close", "ask_close", "ask_low", "price_close",
                                "volume", "phase", "period", "clock_s", "player_pts", "player_reb",
                                "player_ast") if c in one.columns]
            log(f"  candles for {first}: {len(one)} ({(one['interval'] == 1).sum()} one-minute)")
            log(one[cols].iloc[::max(1, len(one) // 12)].to_string(index=False))
        game = next(iter(_games.values()), None)
        if game is not None:
            bad = game.mismatches()
            log(f"  play-by-play check: {len(game.box)} players, {len(bad)} don't match the box score")
            for pid, (got, box) in list(bad.items())[:5]:
                log(f"    {game.names.get(pid)}: rebuilt {got}, box {box}")


def run(mode, state_dir, out_dir, log=print):
    stored = None if mode == "backfill" else read_table(state_dir, "kalshi_nba_markets")
    done = set(stored["event_ticker"]) if stored is not None else set()
    new_markets, new_minutes, processed = [], [], 0
    for stat in SERIES:
        try:
            series = find_series(stat, log)
        except RuntimeError as e:
            log(f"{stat}: {e}")
            continue
        events = settled_events(series, log)
        todo = sorted(t for t in events
                      if t not in done and (parse_event_ticker(t) or (dt.date.min,))[0] >= SEASON_START)
        log(f"{stat} ({series}): {len(events)} settled events, {len(todo)} to add")
        for ticker in todo:
            if processed >= MAX_EVENTS_PER_RUN:
                log(f"reached {MAX_EVENTS_PER_RUN} events this run; the next run continues")
                break
            try:
                markets, minutes = process_event(stat, series, ticker, events[ticker], log)
            except Exception as e:                    # one bad event shouldn't stop the run
                log(f"  {ticker}: failed ({e})")
                continue
            new_markets += markets
            new_minutes += minutes
            processed += 1
            if processed % 25 == 0:
                log(f"  {processed} events processed")
    log(f"processed {processed} events: {len(new_markets)} markets, {len(new_minutes)} candles")
    if not new_markets:
        return
    markets = pd.DataFrame(new_markets)
    if stored is not None:
        markets = pd.concat([stored[~stored["market_ticker"].isin(markets["market_ticker"])], markets])
    write_table(markets.sort_values(["game_date", "event_ticker", "market_ticker"]), out_dir,
                "kalshi_nba_markets")
    minutes = pd.DataFrame(new_minutes)
    if len(minutes):
        dates = markets.set_index("market_ticker")["game_date"]
        minutes["month"] = minutes["market_ticker"].map(dates).str[:7]
        for month, part in minutes.groupby("month"):
            old = None if mode == "backfill" else read_table(state_dir, f"kalshi_nba_minutes_{month}")
            part = part.drop(columns="month")
            if old is not None:
                part = pd.concat([old[~old["market_ticker"].isin(part["market_ticker"])], part])
            write_table(part.sort_values(["market_ticker", "ts"]), out_dir, f"kalshi_nba_minutes_{month}")
    flagged = markets["flags"].fillna("").str.len().gt(0).sum()
    log(f"stored {len(markets)} markets in total ({flagged} with flags)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["probe", "update", "backfill"], default="update")
    parser.add_argument("--state", default="kalshi_state", help="folder with files published so far")
    parser.add_argument("--out", default="kalshi_out", help="folder for the files to publish")
    args = parser.parse_args()
    if not _have_parquet():
        print("(pyarrow isn't installed: writing gzipped CSV instead of Parquet)")
    if args.mode == "probe":
        probe()
    else:
        run(args.mode, args.state, args.out)


if __name__ == "__main__":
    sys.exit(main())

"""Backtest entry timing on Kalshi NBA player props (points, rebounds, assists).

Reads the files built by kalshi_nba.py (kalshi_nba_markets + kalshi_nba_checkpoints_YYYY-MM) and
asks: compared with buying shortly before tip-off, are there entry points (earlier in the day,
or price dips during the game) that pay off after fees?

Every result is profit per contract bought at the ask, after Kalshi's fee, in cents:
  profit = result ($1 or $0) - price paid - fee.
Uncertainty ranges come from resampling whole player-games (a player's 20+ and 25+ contracts on
the same night rise and fall together, so they count as one unit). Each study is reported for
the whole season, a discovery period (the earlier games) and a check period (the later games):
a pattern worth trusting should show up in both.

  python kalshi_analysis.py --data kalshi_state --out analysis_out
writes report.txt (phone-width, one section per page) and one CSV per study.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kalshi_nba import read_table  # noqa: E402

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

FEE_RATE = 0.07            # Kalshi taker fee per contract = FEE_RATE x price x (1 - price): any order
                           # that fills immediately, including a limit order at or past the ask
MAKER_FEE_RATE = 0.0175    # resting limit orders that fill later (maker): a quarter of the taker rate
                           # on series with maker fees, 0 on series without (kalshi.com/fee-schedule)
BASELINE = "tip_minus_5"   # "buy right before the game" = the ask 5 minutes before tip-off
DROPS = (0.05, 0.10, 0.20) # in-game dips tested: the ask this far below the baseline ask
MAIN_DROP = 0.10           # the dip size shown in the detailed tables
MAX_SPREAD = 0.06          # "liquid": bid-ask spread at most this wide...
MIN_VOLUME = 1             # ...and at least this many contracts traded in the last 5 minutes
MAX_QUOTE_AGE = 2.0        # in-game quotes older than this many minutes are ignored
SPLIT_QUANTILE = 0.7       # discovery = the first 70% of game dates, check = the rest
BOOTSTRAP = 1000
CONFIDENCE = 0.90
WINDOWS = {"Q1": (1, 12), "Q2": (13, 24), "Q3": (25, 36)}   # game-clock minutes
PREGAME = ["first_quote", "10am", "noon", "3pm", "tip_minus_60", "tip_minus_15",
           "tip_minus_5", "tip_minus_2", "tip"]
STAT_NAMES = {"pts": "points", "reb": "rebounds", "ast": "assists"}
# Sections 6-7: favorites, by their price 15 minutes before tip-off and their history.
# Each group: (name, price band at FAVORITE_CHECKPOINT, minimum last-10 hit rate at the
# contract's threshold or None, scanner-flagged only?)
FAVORITE_CHECKPOINT = "tip_minus_15"
FAVORITE_GROUPS = [
    ("75-85c, flagged 80%+", (0.75, 0.85), None, True),
    ("85-92c, last-10 90%+", (0.85, 0.92), 0.9, False),
    ("85-92c, last-10 100%", (0.85, 0.92), 1.0, False),
    ("85-92c, everything", (0.85, 0.92), None, False),
]
DIP_SIZES = (0.05, 0.10, 0.15)             # "dipped this far below the 15-minute price"
LIMIT_OFFSETS = (0.03, 0.05, 0.08, 0.10, 0.15)   # buy orders this far below it
FAVORITE_WINDOWS = {"game_q1": ("Q1", 12), "game_first_half": ("first half", 24),
                    "game_through_q3": ("through Q3", 36)}
# Section 8: selling early vs holding to the end (from the minute-by-minute prices)
SELL_STEP = 3                              # look every 3 minutes of game clock
SELL_WINDOWS = {"Q1": (1, 12), "Q2": (13, 24), "Q3": (25, 36), "Q4": (37, 47)}
SELL_BIDS = (0.30, 0.20, 0.10)             # "the bid falls to this"
NEED_RATIOS = (1.5, 2.0, 3.0)              # "needs this many times his usual rate"
COLLAPSE_FROM = 0.80                       # favorites priced at least this 15 min before tip...
COLLAPSE_TO = (0.60, 0.50)                 # ...whose bid falls to this
COLLAPSE_LEVELS = (0.40, 0.30, 0.20)       # section 10: deeper collapses, by bid and by ask
MINUTE_COLUMNS = ["market_ticker", "ts", "interval", "bid_close", "ask_close", "volume", "phase",
                  "elapsed_s", "player_pts", "player_reb", "player_ast", "player_fouls", "margin"]
PERIOD_NAMES = {"all": "Season", "discovery": "Discovery", "check": "Check"}

_rng = np.random.default_rng(7)


def fee(price, maker=False):
    return (MAKER_FEE_RATE if maker else FEE_RATE) * price * (1 - price)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load(folder):
    markets = read_table(folder, "kalshi_nba_markets")
    if markets is None:
        raise SystemExit(f"No kalshi_nba_markets file in {folder}")
    months = sorted({f.split("kalshi_nba_checkpoints_")[1].split(".")[0]
                     for f in os.listdir(folder) if f.startswith("kalshi_nba_checkpoints_")})
    parts = [read_table(folder, f"kalshi_nba_checkpoints_{m}") for m in months]
    parts = [p for p in parts if p is not None]
    if not parts:
        raise SystemExit(f"No kalshi_nba_checkpoints files in {folder}")
    months = sorted({f.split("kalshi_nba_windows_")[1].split(".")[0]
                     for f in os.listdir(folder) if f.startswith("kalshi_nba_windows_")})
    windows = [read_table(folder, f"kalshi_nba_windows_{m}") for m in months]
    windows = [w for w in windows if w is not None]
    return markets, pd.concat(parts, ignore_index=True), (pd.concat(windows, ignore_index=True)
                                                           if windows else pd.DataFrame())


def load_minutes(folder, notes=None):
    """In-game one-minute candles from the kalshi_nba_minutes files, needed columns only.
    Anything that goes wrong is added to `notes`, so the report can say why section 8 is empty."""
    notes = notes if notes is not None else []
    names = sorted(n for n in os.listdir(folder) if n.startswith("kalshi_nba_minutes_"))
    if not names:
        notes.append("no kalshi_nba_minutes files were found: check that the workflow downloads them")
    parts = []
    for name in names:
        path = os.path.join(folder, name)
        try:
            if name.endswith(".parquet"):
                import pyarrow.parquet as pq
                available = set(pq.ParquetFile(path).schema.names)
                df = pd.read_parquet(path, columns=[c for c in MINUTE_COLUMNS if c in available])
            else:
                df = pd.read_csv(path, usecols=lambda c: c in MINUTE_COLUMNS)
        except Exception as e:
            notes.append(f"couldn't read {name}: {e}")
            continue
        missing = [c for c in ("market_ticker", "bid_close", "elapsed_s", "phase", "interval") if c not in df]
        if missing:
            notes.append(f"{name} is missing columns {missing}")
            continue
        parts.append(df[(df["interval"] == 1) & (df["phase"] == "live")])
    frame = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=MINUTE_COLUMNS)
    print(f"minute data: {len(names)} files, {len(frame)} in-game candles")
    for n in notes:
        print(f"  {n}")
    return frame


def prepare(markets, checkpoints):
    """Clean contracts (settled yes/no, no flags), each checkpoint joined to its contract, plus
    the baseline quote. Returns (checkpoints, baseline, info)."""
    info = {"contracts": len(markets), "games": markets["espn_game_id"].nunique(),
            "first_date": markets["game_date"].min(), "last_date": markets["game_date"].max()}
    flags = markets["flags"].fillna("")
    info["flag_counts"] = flags[flags != ""].str.split(",").explode().value_counts().to_dict()
    clean = markets[(flags == "") & markets["result"].isin([0, 1])].copy()
    clean["result"] = clean["result"].astype(int)
    clean["unit"] = clean["espn_game_id"].astype(str) + ":" + clean["espn_player_id"].astype(str)
    if "pre_flagged80" in clean:
        clean["flagged"] = clean["pre_flagged80"].fillna(False).astype(bool)
    else:
        clean["flagged"] = False
    dates = sorted(clean["game_date"].dropna().unique())
    split = dates[min(len(dates) - 1, int(len(dates) * SPLIT_QUANTILE))] if dates else None
    info.update(clean=len(clean), flagged=int(clean["flagged"].sum()), split=split)
    info["clean_markets"] = clean
    keep = ["market_ticker", "result", "unit", "game_date", "stat", "threshold", "flagged",
            "pre_avg_l10", "pre_hit_l10", "player_name"]
    keep = [c for c in keep if c in clean.columns]
    cp = checkpoints.merge(clean[keep], on="market_ticker", how="inner")
    cp["spread"] = cp["ask"] - cp["bid"]
    cp["valid"] = cp["ask"].between(0.015, 0.985)
    cp["liquid"] = (cp["spread"] <= MAX_SPREAD + 1e-9) & (cp["volume_last_5m"].fillna(0) >= MIN_VOLUME)
    cp["period_split"] = np.where(cp["game_date"] <= split, "discovery", "check")
    base = cp[(cp["checkpoint"] == BASELINE) & cp["valid"]][["market_ticker", "ask", "bid"]]
    base = base.rename(columns={"ask": "base_ask", "bid": "base_bid"})
    cp = cp.merge(base, on="market_ticker", how="inner")
    info["with_baseline"] = cp["market_ticker"].nunique()
    return cp, base, info


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

def summarize(df, price="ask", side="buy", maker=False):
    """n, units, average price, hit rate, edge (hit - price) and profit after fees, with a
    confidence range from resampling player-games. side="sell": the value of selling at that
    price instead of holding to the end, price - fee - result (+ = selling was better)."""
    if df.empty:
        return {"n": 0, "units": 0}
    if side == "sell":
        profit = df[price] - fee(df[price], maker) - df["result"]
    else:
        profit = df["result"] - df[price] - fee(df[price], maker)
    out = {"n": len(df), "units": df["unit"].nunique(), "price": df[price].mean(),
           "hit": df["result"].mean(), "edge": df["result"].mean() - df[price].mean(),
           "profit": profit.mean()}
    sums = profit.groupby(df["unit"].to_numpy()).agg(["sum", "count"])
    s, c = sums["sum"].to_numpy(), sums["count"].to_numpy()
    if len(s) >= 5:
        idx = _rng.integers(0, len(s), size=(BOOTSTRAP, len(s)))
        means = s[idx].sum(1) / c[idx].sum(1)
        lo, hi = np.quantile(means, [(1 - CONFIDENCE) / 2, 1 - (1 - CONFIDENCE) / 2])
        out.update(low=lo, high=hi)
    return out


def by_periods(df, price="ask", side="buy", maker=False):
    """The summary for the whole season, the discovery period and the check period."""
    return {"all": summarize(df, price, side, maker),
            "discovery": summarize(df[df["period_split"] == "discovery"], price, side, maker),
            "check": summarize(df[df["period_split"] == "check"], price, side, maker)}


def rows_of(label, periods, **extra):
    return [{"label": label, "period": p, **extra, **s} for p, s in periods.items()]


# ---------------------------------------------------------------------------
# Studies
# ---------------------------------------------------------------------------

def study_baseline(cp):
    base = cp[cp["checkpoint"] == BASELINE].copy()
    rows = []
    for subset, df in (("all", base), ("flagged", base[base["flagged"]])):
        rows += rows_of("overall", by_periods(df), subset=subset)
        buckets = pd.cut(df["ask"], [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0],
                         labels=["0-10", "10-20", "20-30", "30-40", "40-50", "50-60", "60-70",
                                 "70-80", "80-90", "90-100"])
        for bucket, part in df.groupby(buckets, observed=True):
            rows += rows_of(f"{bucket}c", by_periods(part), subset=subset)
    return pd.DataFrame(rows)


def study_pregame(cp):
    rows = []
    for subset, df in (("all", cp), ("flagged", cp[cp["flagged"]])):
        for name in PREGAME:
            part = df[(df["checkpoint"] == name) & df["valid"]]
            if part.empty:
                continue
            drift = (part["ask"] - part["base_ask"]).mean()
            rows += rows_of(name, by_periods(part), subset=subset, drift=drift)
    return pd.DataFrame(rows)


def study_minutes(cp):
    """Buying at each game-clock checkpoint: every contract, and only after a dip."""
    game = cp[(cp["kind"] == "game") & cp["valid"] & (cp["minutes_since_quote"].fillna(99) <= MAX_QUOTE_AGE)]
    rows = []
    for subset, df in (("all", game), ("flagged", game[game["flagged"]])):
        for minute, part in df.groupby("game_minute"):
            rows += rows_of(f"{int(minute)}m", by_periods(part), subset=subset, drop=0.0,
                            minute=int(minute))
            for drop in DROPS:
                dipped = part[part["ask"] <= part["base_ask"] - drop + 1e-9]
                rows += rows_of(f"{int(minute)}m", by_periods(dipped), subset=subset, drop=drop,
                                minute=int(minute))
                liquid = dipped[dipped["liquid"]]
                rows += rows_of(f"{int(minute)}m", by_periods(liquid), subset=subset + "_liquid",
                                drop=drop, minute=int(minute))
    return pd.DataFrame(rows)


def first_dips(cp, start, end, drop, liquid_only=False):
    """For each contract, the first checkpoint in game minutes [start, end] where the ask is at
    least `drop` below the baseline: a strategy you could actually follow."""
    game = cp[(cp["kind"] == "game") & cp["valid"] & cp["game_minute"].between(start, end)
              & (cp["minutes_since_quote"].fillna(99) <= MAX_QUOTE_AGE)
              & (cp["ask"] <= cp["base_ask"] - drop + 1e-9)]
    if liquid_only:
        game = game[game["liquid"]]
    return game.sort_values("game_minute").groupby("market_ticker", as_index=False).first()


def study_strategies(cp):
    rows = []
    for subset, df in (("all", cp), ("flagged", cp[cp["flagged"]])):
        contracts = df["market_ticker"].nunique()
        for window, (start, end) in WINDOWS.items():
            for drop in DROPS:
                for liquid in (False, True):
                    entries = first_dips(df, start, end, drop, liquid)
                    rows += rows_of(f"{window} dip {int(drop * 100)}c", by_periods(entries),
                                    subset=subset + ("_liquid" if liquid else ""), window=window,
                                    drop=drop, triggered=len(entries) / contracts if contracts else 0)
    return pd.DataFrame(rows)


def _foul_trouble(row):
    minute, fouls = row["game_minute"], row.get("player_fouls") or 0
    return fouls >= (2 if minute <= 12 else 3 if minute <= 24 else 4)


def study_game_state(cp):
    """Why did it dip? First 10c dips in the first half, split by pace, fouls and score."""
    entries = first_dips(cp, 1, 24, MAIN_DROP)
    if entries.empty:
        return pd.DataFrame()
    stat_now = entries.apply(lambda r: r.get(f"player_{r['stat']}"), axis=1)
    expected = entries["pre_avg_l10"] * entries["game_minute"] / 48 if "pre_avg_l10" in entries else np.nan
    pace = stat_now / expected
    entries["pace"] = np.select([expected.isna() | (expected <= 0), pace < 0.5, pace <= 1.5],
                                ["unknown", "behind (<half pace)", "on pace"], "ahead (>1.5x)")
    entries["fouls"] = np.where(entries.apply(_foul_trouble, axis=1), "foul trouble", "no foul trouble")
    margin = entries["margin"].abs()
    entries["score"] = np.select([margin >= 15, margin >= 8], ["blowout (15+)", "8-14"], "close (<8)")
    rows = []
    for column in ("pace", "fouls", "score"):
        for value, part in entries.groupby(column):
            rows += rows_of(value, by_periods(part), factor=column)
    return pd.DataFrame(rows)


def study_favorites(cp, windows):
    """For each FAVORITE_GROUPS group: how far below its 15-minute price the ask went in each
    window (won / lost / all), and buy orders LIMIT_OFFSETS below that price, left open through
    Q1 or the first half. Returns (lows, limits)."""
    if windows.empty:
        return pd.DataFrame(), pd.DataFrame()
    base = cp[(cp["checkpoint"] == FAVORITE_CHECKPOINT) & cp["valid"]]
    cols = ["market_ticker", "ask", "result", "unit", "period_split", "flagged"] + \
        (["pre_hit_l10"] if "pre_hit_l10" in base else [])
    base = base[cols].rename(columns={"ask": "entry_ask"})
    game = cp[(cp["kind"] == "game") & cp["bid"].notna()]
    lows_rows, limit_rows = [], []
    for name, band, min_hit, flagged_only in FAVORITE_GROUPS:
        group = base[base["entry_ask"].between(band[0], band[1])]
        if flagged_only:
            group = group[group["flagged"]]
        if min_hit is not None:
            if "pre_hit_l10" not in group:
                continue
            group = group[group["pre_hit_l10"].fillna(-1) >= min_hit - 1e-9]
        if group.empty:
            continue
        for window, (wname, last_minute) in FAVORITE_WINDOWS.items():
            w = windows[windows["window"] == window][["market_ticker", "min_ask", "min_ask_low"]]
            df = group.merge(w, on="market_ticker", how="inner")
            df["low"] = df["min_ask_low"].fillna(df["min_ask"])
            df = df[df["low"].notna()]
            if df.empty:
                continue
            df["dip"] = df["entry_ask"] - df["low"]
            for outcome, part in (("won", df[df["result"] == 1]), ("lost", df[df["result"] == 0]), ("all", df)):
                if part.empty:
                    continue
                row = {"group": name, "window": wname, "outcome": outcome, "n": len(part),
                       "hit": df["result"].mean(), "entry_ask": part["entry_ask"].mean(),
                       "avg_low_ask": part["low"].mean(), "median_low_ask": part["low"].median(),
                       "median_dip": part["dip"].median()}
                for size in DIP_SIZES:
                    row[f"dipped_{int(size * 100)}c"] = (part["dip"] >= size - 1e-9).mean()
                lows_rows.append(row)
            if window == "game_through_q3":
                continue                                          # orders: Q1 and first half
            for offset in LIMIT_OFFSETS:
                df["limit"] = df["entry_ask"] - offset
                filled = df[df["low"] <= df["limit"] + 1e-9]
                summary = by_periods(filled, price="limit", maker=True) if len(filled) else {
                    "all": {"n": 0}, "discovery": {"n": 0}, "check": {"n": 0}}
                limit_rows += rows_of(f"-{int(offset * 100)}c", summary, group=name, window=wname,
                                      offset=offset, fill_rate=len(filled) / len(df), bets=len(df))
    return pd.DataFrame(lows_rows), pd.DataFrame(limit_rows)


def sell_points(minutes, clean, cp):
    """One row per contract every SELL_STEP minutes of game clock: the first in-game candle at or
    after that minute, with the bid, the game state, what he still needs, and how that compares
    with his usual rate (his last-10 average spread over a 48-minute game)."""
    if minutes.empty:
        return pd.DataFrame()
    m = minutes.dropna(subset=["elapsed_s", "bid_close"]).copy()
    m["slot"] = (m["elapsed_s"] // (SELL_STEP * 60)) * SELL_STEP
    m = m[(m["slot"] >= SELL_STEP) & (m["slot"] < 48)]
    m = m.sort_values(["market_ticker", "ts"]).groupby(["market_ticker", "slot"], as_index=False).first()
    keep = [c for c in ("market_ticker", "result", "unit", "game_date", "stat", "threshold", "flagged",
                        "pre_avg_l10") if c in clean.columns]
    m = m.merge(clean[keep], on="market_ticker", how="inner")
    split = cp[["market_ticker", "period_split"]].drop_duplicates("market_ticker")
    pregame = cp[cp["checkpoint"] == FAVORITE_CHECKPOINT][["market_ticker", "ask"]].rename(
        columns={"ask": "pregame_ask"})
    m = m.merge(split, on="market_ticker", how="inner").merge(pregame, on="market_ticker", how="left")
    m["stat_now"] = np.select([m["stat"] == st for st in ("pts", "reb", "ast")],
                              [m.get(f"player_{st}", pd.Series(np.nan, index=m.index))
                               for st in ("pts", "reb", "ast")], np.nan)
    m["needed"] = m["threshold"] - m["stat_now"]
    m = m[(m["needed"] > 0) & (m["bid_close"] >= 0.01)]           # already over the line: nothing to sell
    rate = m["pre_avg_l10"] / 48 if "pre_avg_l10" in m else np.nan
    expected = rate * (48 - m["slot"]).clip(lower=1)
    m["need_ratio"] = np.where(expected > 0, m["needed"] / expected, np.nan)
    m["spread"] = m["ask_close"] - m["bid_close"]
    m["liquid"] = (m["spread"] <= MAX_SPREAD + 1e-9) & (m["volume"].fillna(0) >= MIN_VOLUME)
    m["foul_trouble"] = m["player_fouls"].fillna(0) >= np.select(
        [m["slot"] < 12, m["slot"] < 24, m["slot"] < 36], [2, 3, 4], 5)
    m["blowout"] = m["margin"].abs() >= 15
    return m.rename(columns={"bid_close": "bid"})


def _first_sale(points, window, mask, liquid_only=False):
    start, end = SELL_WINDOWS[window]
    hit = points[mask & points["slot"].between(start - 1, end)]
    if liquid_only:
        hit = hit[hit["liquid"]]
    return hit.sort_values("slot").groupby("market_ticker", as_index=False).first()


def study_selling(points):
    """Sell at the first moment in a quarter that a rule is met, vs holding to the end."""
    if points.empty:
        return pd.DataFrame()
    rules = []
    for level in SELL_BIDS:
        rules.append(("price", f"bid <= {int(level * 100)}c", points["bid"] <= level + 1e-9))
    for ratio in NEED_RATIOS:
        rules.append(("needs", f"needs {ratio:g}x his rate", points["need_ratio"] >= ratio))
    for level in COLLAPSE_TO:
        rules.append(("collapse", f"{int(COLLAPSE_FROM * 100)}c+ fav, bid <= {int(level * 100)}c",
                      (points["pregame_ask"] >= COLLAPSE_FROM - 1e-9) & (points["bid"] <= level + 1e-9)))
    rules.append(("state", "foul trouble", points["foul_trouble"]))
    rules.append(("state", "blowout (15+)", points["blowout"]))
    rows = []
    for family, label, mask in rules:
        for window in SELL_WINDOWS:
            for liquid in (False, True):
                sales = _first_sale(points, window, mask, liquid)
                rows += rows_of(f"{window} {label}", by_periods(sales, price="bid", side="sell"),
                                family=family, rule=label, window=window,
                                subset="liquid" if liquid else "all")
    return pd.DataFrame(rows)


def study_collapse(points):
    """Favorites priced COLLAPSE_FROM+ before tip, at the first moment in each quarter that the
    bid ("shows") or the ask ("buyable") fell to each COLLAPSE_LEVELS price: the bid, ask, mid
    and spread then, how often they won, and profit buying at the ask vs at the bid."""
    if points.empty or "ask_close" not in points:
        return pd.DataFrame()
    fav = points[points["pregame_ask"] >= COLLAPSE_FROM - 1e-9]
    rows = []
    for trigger, column in (("shows", "bid"), ("buyable", "ask_close")):
        for level in COLLAPSE_LEVELS:
            for window in SELL_WINDOWS:
                for liquid in (False, True):
                    hit = _first_sale(fav, window, fav[column] <= level + 1e-9, liquid)
                    hit = hit[hit["ask_close"].notna() & hit["bid"].notna()]
                    row = {"trigger": trigger, "level": level, "window": window,
                           "subset": "liquid" if liquid else "all", "n": len(hit)}
                    if len(hit):
                        at_ask = by_periods(hit, price="ask_close")
                        at_bid = summarize(hit, price="bid", maker=True)
                        row.update({"bid": hit["bid"].mean(), "ask": hit["ask_close"].mean(),
                                    "mid": ((hit["bid"] + hit["ask_close"]) / 2).mean(),
                                    "spread": (hit["ask_close"] - hit["bid"]).mean(),
                                    "won": hit["result"].mean(),
                                    "buy_at_ask": at_ask["all"].get("profit"),
                                    "buy_at_ask_low": at_ask["all"].get("low"),
                                    "buy_at_ask_high": at_ask["all"].get("high"),
                                    "buy_at_ask_discovery": at_ask["discovery"].get("profit"),
                                    "buy_at_ask_check": at_ask["check"].get("profit"),
                                    "buy_at_bid": at_bid.get("profit")})
                    rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Report (40 characters wide, one section per page)
# ---------------------------------------------------------------------------

def _c(x, sign=False):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return f"{x * 100:+.1f}" if sign else f"{x * 100:.0f}"


def _line(s):
    """Two short lines for one result: counts and prices, then profit with its range."""
    if not s.get("n"):
        return ["  no entries"]
    rng = f"({_c(s.get('low'), True)} to {_c(s.get('high'), True)})" if "low" in s else ""
    return [f"  n {s['n']}  paid {_c(s['price'])}c  hit {s['hit'] * 100:.0f}%",
            f"  profit {_c(s['profit'], True)}c {rng}"]


def _section(title, lines):
    return ["", "=" * 38, title, "=" * 38] + lines


def _pick(df, **conditions):
    part = df
    for k, v in conditions.items():
        part = part[part[k] == v]
    return {p: part[part["period"] == p].iloc[0].to_dict() for p in ("all", "discovery", "check")
            if (part["period"] == p).any()}


def report(info, baseline, pregame, minutes, strategies, state, lows=None, limits=None, selling=None,
           sell_notes=None, collapse=None):
    out = ["Kalshi NBA props: entry backtest",
           f"{info['first_date']} to {info['last_date']}",
           f"profit per contract, cents, after fees", f"ranges: {CONFIDENCE:.0%} confidence"]
    flags = ", ".join(f"{k} {v}" for k, v in list(info["flag_counts"].items())[:4]) or "none"
    out += _section("DATA", [
        f"{info['contracts']} contracts, {info['games']} games",
        f"{info['clean']} clean (settled yes/no)",
        f"{info['with_baseline']} with a baseline quote",
        f"{info['flagged']} scanner-flagged (80%+)",
        f"flags: {flags}",
        f"discovery: games through {info['split']}",
        "check: games after that",
        f"baseline: ask at {BASELINE.replace('_', ' ')}",
        f"fee: {FEE_RATE:.0%} x p x (1-p) (taker),",
        f"  {MAKER_FEE_RATE * 100:g}% for resting orders (maker)"])

    lines = []
    for subset in ("all", "flagged"):
        lines.append(f"{'All contracts' if subset == 'all' else 'Scanner-flagged'}:")
        for period, s in _pick(baseline, label="overall", subset=subset).items():
            lines += [f" {PERIOD_NAMES[period]}"] + _line(s)
    lines += ["", "By baseline price (all, season):"]
    for _, r in baseline[(baseline["subset"] == "all") & (baseline["period"] == "all")
                         & (baseline["label"] != "overall")].iterrows():
        lines.append(f"  {r['label']:>7} n {int(r['n']):>5} hit {r['hit'] * 100:3.0f}% "
                     f"{_c(r['profit'], True):>6}c")
    out += _section("1. BUY 5 MIN BEFORE TIP", lines)

    lines = ["Ask vs baseline (drift) and profit,", "all contracts, season:"]
    for _, r in pregame[(pregame["subset"] == "all") & (pregame["period"] == "all")].iterrows():
        lines.append(f"  {r['label']:<13}{_c(r['drift'], True):>6}c {_c(r['profit'], True):>6}c")
    lines += ["(drift: + = pricier than baseline)"]
    out += _section("2. PREGAME TIMING", lines)

    lines = ["Buy at each game minute, every", "contract (no dip needed), season:"]
    every = minutes[(minutes["subset"] == "all") & (minutes["drop"] == 0) & (minutes["period"] == "all")]
    for _, r in every.sort_values("minute").iterrows():
        lines.append(f"  {r['label']:>4} n {int(r['n']):>5} {_c(r['profit'], True):>6}c")
    lines += ["", f"Only after a {int(MAIN_DROP * 100)}c+ dip from baseline:"]
    dips = minutes[(minutes["subset"] == "all") & (minutes["drop"] == MAIN_DROP) & (minutes["period"] == "all")]
    dips = dips[dips["n"].fillna(0) > 0]
    if dips.empty:
        lines.append("  no dips that large")
    for _, r in dips.sort_values("minute").iterrows():
        rng = f"({_c(r.get('low'), True)},{_c(r.get('high'), True)})" if pd.notna(r.get("low")) else ""
        lines.append(f"  {r['label']:>4} n {int(r['n']):>4} {_c(r['profit'], True):>6}c {rng}")
    out += _section("3. IN-GAME, BY GAME MINUTE", lines)

    lines = ["Buy at the first dip in the window", "(else no bet). disc = discovery."]
    for subset in ("all", "all_liquid", "flagged", "flagged_liquid"):
        lines += ["", {"all": "All contracts:", "all_liquid": "All, liquid entries only:",
                       "flagged": "Scanner-flagged:", "flagged_liquid": "Flagged, liquid only:"}[subset]]
        part = strategies[strategies["subset"] == subset]
        for label in dict.fromkeys(part["label"]):
            p = _pick(part, label=label)
            if "all" not in p or not p["all"].get("n"):
                continue
            a, d, c = p["all"], p.get("discovery", {}), p.get("check", {})
            lines.append(f" {label}: n {int(a['n'])} ({a['triggered'] * 100:.0f}% of bets)")
            lines.append(f"  season {_c(a['profit'], True)}c disc {_c(d.get('profit'), True)}c "
                         f"check {_c(c.get('profit'), True)}c")
    out += _section("4. DIP STRATEGIES", lines)

    lines = [f"First {int(MAIN_DROP * 100)}c dip in the first half,", "by what was happening then:"]
    if not state.empty:
        for factor in ("pace", "fouls", "score"):
            lines += ["", f"{factor}:"]
            for value in dict.fromkeys(state[state["factor"] == factor]["label"]):
                p = _pick(state[state["factor"] == factor], label=value)
                lines += [f" {value}"] + _line(p.get("all", {}))
    out += _section("5. WHY IT DIPPED", lines)

    if lows is not None and not lows.empty:
        sizes = "/".join(str(int(x * 100)) for x in DIP_SIZES)
        lines = ["Price = the ask 15 min before tip.",
                 "low = lowest ask in the window",
                 "(median, cents). dipped = how often",
                 f"it fell {sizes}c or more below price."]
        for name in dict.fromkeys(lows["group"]):
            g = lows[lows["group"] == name]
            first = g.iloc[0]
            lines += ["", f"{name}:",
                      f" n {int(g[(g['outcome'] == 'all')]['n'].iloc[0])}, price {first['entry_ask'] * 100:.0f}c, "
                      f"hit {first['hit'] * 100:.0f}%"]
            for wname in dict.fromkeys(g["window"]):
                rows = g[g["window"] == wname].set_index("outcome")
                won = rows["median_low_ask"].get("won")
                lost = rows["median_low_ask"].get("lost")
                lines.append(f" {wname}: low won {_c(won)} lost {_c(lost)}")
                if "all" in rows.index:
                    a = rows.loc["all"]
                    lines.append("  dipped " + " ".join(f"{int(x * 100)}c+ {a[f'dipped_{int(x * 100)}c'] * 100:.0f}%"
                                                         for x in DIP_SIZES))
        out += _section("6. FAVORITES: HOW FAR DO THEY DIP?", lines)
        lines = ["Buy order X cents below the price",
                 "15 min before tip, left open through",
                 "the window (filled if the ask",
                 "touched it). Maker fee. Per fill:"]
        for name in dict.fromkeys(limits["group"]):
            g = limits[limits["group"] == name]
            lines += ["", f"{name}:"]
            for wname in dict.fromkeys(g["window"]):
                lines.append(f" {wname}:")
                for label in dict.fromkeys(g[g["window"] == wname]["label"]):
                    p = _pick(g[g["window"] == wname], label=label)
                    a = p.get("all", {})
                    if not a.get("n"):
                        lines.append(f"  {label}: never filled")
                        continue
                    d, c = p.get("discovery", {}), p.get("check", {})
                    lines.append(f"  {label}: fills {a['fill_rate'] * 100:.0f}%, hit {a['hit'] * 100:.0f}%, "
                                 f"{_c(a['profit'], True)}c")
                    rng = (f"({_c(a.get('low'), True)},{_c(a.get('high'), True)}) "
                           if "low" in a and pd.notna(a.get("low")) else "")
                    lines.append(f"   {rng}d {_c(d.get('profit'), True)} c {_c(c.get('profit'), True)}")
        out += _section("7. FAVORITES: BUY ORDERS", lines)

    if selling is None or selling.empty:
        why = sell_notes or ["the selling study found no in-game prices"]
        lines = ["Not available this run:"]
        for note in why:
            import textwrap
            lines += textwrap.wrap(note, 36, initial_indent="  ", subsequent_indent="  ")
        out += _section("8. SELLING EARLY", lines)
    if selling is not None and not selling.empty:
        intro = ["value = bid - fee - (1 if it won).",
                 "+ means selling then beat holding",
                 "to the end. Sell at the first time",
                 "the rule is met in that quarter.",
                 "(Buying 'No' works out the same.)"]

        def rule_lines(family_list):
            lines = []
            for family in family_list:
                part = selling[(selling["family"] == family) & (selling["subset"] == "all")]
                for label in dict.fromkeys(part["label"]):
                    p = _pick(part, label=label)
                    a = p.get("all", {})
                    if not a.get("n"):
                        continue
                    d, c = p.get("discovery", {}), p.get("check", {})
                    liq = _pick(selling[(selling["subset"] == "liquid")], label=label).get("all", {})
                    rng = (f"({_c(a.get('low'), True)},{_c(a.get('high'), True)})"
                           if pd.notna(a.get("low", np.nan)) else "")
                    lines += [f" {label}:",
                              f"  n {a['n']} bid {_c(a['price'])}c won {a['hit'] * 100:.0f}%",
                              f"  {_c(a['profit'], True)}c {rng}",
                              f"  disc {_c(d.get('profit'), True)} check {_c(c.get('profit'), True)}"
                              f" liquid {_c(liq.get('profit'), True)}"]
            return lines
        out += _section("8. SELLING EARLY: BY PRICE", intro + [""] + rule_lines(["price", "collapse"]))
        out += _section("9. SELLING EARLY: BY SITUATION", intro[:2] + [
            "needs Nx: what he still needs vs",
            "his last-10 average over the time",
            "left. foul trouble: 2+ fouls in Q1,",
            "3+ in Q2, 4+ in Q3, 5+ in Q4.", ""] + rule_lines(["needs", "state"]))

    if collapse is not None and not collapse.empty:
        for trigger, title, intro in (
                ("shows", "10. COLLAPSED FAVORITES: BID",
                 ["80c+ favorites (15 min before tip)",
                  "the first time in the quarter the",
                  "BID fell to the level (roughly what",
                  "the app shows). Cents. won = how",
                  "often they still won. buy@ask = what",
                  "you'd really pay. buy@bid = a resting",
                  "order filled at the bid, maker fee",
                  "(best case)."]),
                ("buyable", "11. COLLAPSED FAVORITES: ASK",
                 ["80c+ favorites, the first time in",
                  "the quarter the ASK fell to the",
                  "level: you could actually buy there.",
                  "Is a favorite you can buy at 20c",
                  "really more than 20% likely?"])):
            lines = list(intro)
            part = collapse[collapse["trigger"] == trigger]
            for level in COLLAPSE_LEVELS:
                lines += ["", f"{'bid' if trigger == 'shows' else 'ask'} fell to {int(level * 100)}c or less:"]
                for window in SELL_WINDOWS:
                    rows = part[(part["level"] == level) & (part["window"] == window)].set_index("subset")
                    if "all" not in rows.index or not rows.loc["all", "n"]:
                        lines.append(f" {window}: none")
                        continue
                    r = rows.loc["all"]
                    lines.append(f" {window} n {int(r['n'])}: bid {_c(r['bid'])} ask {_c(r['ask'])} "
                                 f"sprd {_c(r['spread'])}")
                    lines.append(f"  won {r['won'] * 100:.0f}%  buy@ask {_c(r['buy_at_ask'], True)}"
                                 f"  @bid {_c(r['buy_at_bid'], True)}")
                    if pd.notna(r.get("buy_at_ask_low")):
                        lines.append(f"  @ask ({_c(r['buy_at_ask_low'], True)},{_c(r['buy_at_ask_high'], True)})"
                                     f" d {_c(r['buy_at_ask_discovery'], True)} c {_c(r['buy_at_ask_check'], True)}")
                    if "liquid" in rows.index and rows.loc["liquid", "n"]:
                        q = rows.loc["liquid"]
                        lines.append(f"  liquid n {int(q['n'])} won {q['won'] * 100:.0f}%"
                                     f" @ask {_c(q['buy_at_ask'], True)}")
            out += _section(title, lines)

    out += _section("HOW TO READ THIS", [
        "profit: average result per contract",
        "  bought at the ask, minus price and",
        "  fee. +2.0c = 2 cents per $1 contract.",
        "range: plausible values for profit.",
        "  If it includes 0, the result could",
        "  be luck.",
        "Trust patterns that are positive in",
        "  both discovery and check periods,",
        "  with enough entries (n).",
        "liquid: spread <= "
        f"{int(MAX_SPREAD * 100)}c and some volume",
        "  traded near the entry."])
    return "\n".join(out) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="kalshi_state")
    parser.add_argument("--out", default="analysis_out")
    args = parser.parse_args()
    markets, checkpoints, windows = load(args.data)
    cp, _, info = prepare(markets, checkpoints)
    lows, limits = study_favorites(cp, windows)
    sell_notes = []
    minute_data = load_minutes(args.data, sell_notes)
    points = sell_points(minute_data, info["clean_markets"], cp)
    if points.empty and len(minute_data) and not sell_notes:
        sell_notes.append(f"{len(minute_data)} in-game candles were found, but none matched a clean "
                          "contract with a score: check the play-by-play columns")
    selling = study_selling(points)
    collapse = study_collapse(points)
    results = {"baseline": study_baseline(cp), "pregame": study_pregame(cp),
               "minutes": study_minutes(cp), "strategies": study_strategies(cp),
               "game_state": study_game_state(cp), "favorite_lows": lows, "favorite_limits": limits,
               "selling": selling, "collapse": collapse}
    os.makedirs(args.out, exist_ok=True)
    for name, df in results.items():
        df.to_csv(os.path.join(args.out, f"analysis_{name}.csv"), index=False)
    text = report(info, results["baseline"], results["pregame"], results["minutes"],
                  results["strategies"], results["game_state"], lows, limits, selling, sell_notes,
                  collapse)
    with open(os.path.join(args.out, "analysis_report.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()

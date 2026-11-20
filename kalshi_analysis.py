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

FEE_RATE = 0.07            # Kalshi taker fee per contract = FEE_RATE x price x (1 - price);
                           # check it against Kalshi's current fee schedule
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
PERIOD_NAMES = {"all": "Season", "discovery": "Discovery", "check": "Check"}

_rng = np.random.default_rng(7)


def fee(price):
    return FEE_RATE * price * (1 - price)


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
    return markets, pd.concat(parts, ignore_index=True)


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
    keep = ["market_ticker", "result", "unit", "game_date", "stat", "threshold", "flagged",
            "pre_avg_l10", "player_name"]
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

def summarize(df, price="ask"):
    """n, units, average price, hit rate, edge (hit - price) and profit after fees, with a
    confidence range from resampling player-games."""
    if df.empty:
        return {"n": 0, "units": 0}
    profit = df["result"] - df[price] - fee(df[price])
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


def by_periods(df, price="ask"):
    """The summary for the whole season, the discovery period and the check period."""
    return {"all": summarize(df, price),
            "discovery": summarize(df[df["period_split"] == "discovery"], price),
            "check": summarize(df[df["period_split"] == "check"], price)}


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


def report(info, baseline, pregame, minutes, strategies, state):
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
        f"fee: {FEE_RATE:.0%} x p x (1-p)"])

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
    markets, checkpoints = load(args.data)
    cp, _, info = prepare(markets, checkpoints)
    results = {"baseline": study_baseline(cp), "pregame": study_pregame(cp),
               "minutes": study_minutes(cp), "strategies": study_strategies(cp),
               "game_state": study_game_state(cp)}
    os.makedirs(args.out, exist_ok=True)
    for name, df in results.items():
        df.to_csv(os.path.join(args.out, f"analysis_{name}.csv"), index=False)
    text = report(info, results["baseline"], results["pregame"], results["minutes"],
                  results["strategies"], results["game_state"])
    with open(os.path.join(args.out, "analysis_report.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()

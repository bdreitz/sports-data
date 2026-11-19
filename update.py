"""Update every sport's files in out/. Exits with 1 if any sport didn't pass its checks.

    python update.py                 normal daily run (full rebuild on Mondays)
    python update.py --full          re-fetch every game this season
    python update.py --sport nhl     one sport only (nfl, nhl or nba)
"""
import argparse
import datetime as dt
import os
import sys
import traceback
from zoneinfo import ZoneInfo

import common
import nba
import nfl
import nhl

SPORTS = {"nfl": nfl, "nhl": nhl, "nba": nba}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=sorted(SPORTS), action="append")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--out", default="out")
    args = parser.parse_args()

    today = dt.datetime.now(ZoneInfo("America/New_York")).date()
    full = args.full or os.environ.get("FULL_REBUILD") == "1" or today.weekday() == 0
    all_ok = True
    for name in args.sport or sorted(SPORTS):
        try:
            all_ok &= common.run_sport(SPORTS[name], args.out, today, full=full)
        except Exception:
            print(f"{name}: FAILED before any checks could run; nothing published for it")
            traceback.print_exc()
            all_ok = False
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()

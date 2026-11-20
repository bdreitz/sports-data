# Using sports_data.py

Everything here is also built into the script: start it in a-Shell with
`python3 -i sports_data.py`, then type `usage()` (or `guide()`, the same thing) for the
list of topics, or `usage("daily")` for one topic. Example output uses made-up numbers.

## Contents

- [Getting started](#getting-started)
- [daily_report(): this morning's report](#daily_report-this-mornings-report)
- [daily(): build today's report now](#daily-build-todays-report-now)
- [nfl(), nba(), nhl(): a player's games](#nfl-nba-nhl-a-players-games)
- [nfl_team(), nba_team(), nhl_team(): a team](#nfl_team-nba_team-nhl_team-a-team)
- [nfl_standings() etc.: standings](#nfl_standings-etc-standings)
- [nfl_leaders() etc.: player leaders](#nfl_leaders-etc-player-leaders)
- [nfl_scan(), nba_scan(), nhl_scan()](#nfl_scan-nba_scan-nhl_scan)
- [nfl_line_scan() etc.: rank by 80% line](#nfl_line_scan-etc-rank-by-80-line)
- [kalshi_report(): entry backtest](#kalshi_report-entry-backtest)
- [nfl_injuries(), nba_injuries(), nhl_injuries()](#nfl_injuries-nba_injuries-nhl_injuries)
- [nfl_columns() etc.: find stat names](#nfl_columns-etc-find-stat-names)
- [status(): is the data up to date?](#status-is-the-data-up-to-date)
- [page= and save=: long output](#page-and-save-long-output)
- [Settings you can change](#settings-you-can-change)
- [Team codes](#team-codes)

## Getting started

In a-Shell: `usage("start")`

```text
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
```

## daily_report(): this morning's report

In a-Shell: `usage("report")`

```text
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
```

## daily(): build today's report now

In a-Shell: `usage("daily")`

```text
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
```

## nfl(), nba(), nhl(): a player's games

In a-Shell: `usage("players")`

```text
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
```

## nfl_team(), nba_team(), nhl_team(): a team

In a-Shell: `usage("teams")`

```text
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
```

## nfl_standings() etc.: standings

In a-Shell: `usage("standings")`

```text
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
```

## nfl_leaders() etc.: player leaders

In a-Shell: `usage("leaders")`

```text
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
```

## nfl_scan(), nba_scan(), nhl_scan()

In a-Shell: `usage("scan")`

```text
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
```

## nfl_line_scan() etc.: rank by 80% line

In a-Shell: `usage("lines")`

```text
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
```

## kalshi_report(): entry backtest

In a-Shell: `usage("kalshi")`

```text
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

Each result shows the season, a
discovery period (earlier games) and a
check period (later games). Trust
patterns that hold up in both, with
enough entries (n), and a range that
stays above 0.

Built by the "Kalshi NBA entry
backtest" workflow (Mondays, or run it
from the Actions tab).
```

## nfl_injuries(), nba_injuries(), nhl_injuries()

In a-Shell: `usage("injuries")`

```text
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
```

## nfl_columns() etc.: find stat names

In a-Shell: `usage("columns")`

```text
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
```

## status(): is the data up to date?

In a-Shell: `usage("status")`

```text
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
```

## page= and save=: long output

In a-Shell: `usage("paging")`

```text
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
```

## Settings you can change

In a-Shell: `usage("settings")`

```text
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
```

## Team codes

In a-Shell: `usage("codes")`

```text
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
```

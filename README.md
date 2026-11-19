# sports-data

A daily GitHub Actions job that builds NFL, NBA and NHL game-by-game stats files in one
consistent format and publishes them on this repository's **data** release, where the phone
tools download them.

- **NFL** comes from nflverse's published files, reshaped to the shared format. Records are
  cross-checked against ESPN's standings.
- **NHL** comes from the official NHL API (api-web.nhle.com).
- **NBA** comes from ESPN's public JSON (site.api.espn.com).

After updating the data, the job also writes **`daily_props.txt`**: consistent player props for
that day's games in every sport, in start-time order, with both teams' records and how each
player's opponent compares with the league. `daily_report()` in `sports_data.py` prints it.

Each sport publishes four files (NHL 2026-27 shown):

| File | What's in it |
|---|---|
| `nhl_2026-27_player_games.csv` | one row per player per game (goalies who played included) |
| `nhl_2026-27_team_games.csv` | one row per team per game, with the opponent's numbers |
| `nhl_2026-27_status.json` | when it last updated and whether every check passed |
| `nhl_2026-27_changelog.txt` | games added and stat corrections, newest first |

The NBA files have the same names starting with `nba_`, and the NFL files are named by the
season's starting year, like `nfl_2026_player_games.csv`.

Every file starts with the same columns: `season`, `game_id`, `game_date`, `team`,
`opponent`, `home` (1 or 0), and for players `player_id`, `player_name` and `position`.
NFL files also have `week`. After those come each sport's stats:

- **NFL** keeps nflverse's own column names (`passing_yards`, `carries`, `receptions`,
  `fg_made`, `fantasy_points_ppr` ...), so code written for nflverse's files reads them too.
  Team rows add `result` (W/L/T), `points_for`, `points_against` and the opponent's main
  numbers (`opp_passing_yards` ...).
- **NBA** team rows also have possessions, pace and offensive/defensive rating (points per
  100 possessions).

## Setup (about 15 minutes, all from Safari on iPhone)

If a button below is missing on the phone layout, tap **aA** in Safari's address bar and
choose **Request Desktop Website**.

1. **Save the files** from the chat into the Files app: `common.py`, `nfl.py`, `nhl.py`,
   `nba.py`, `update.py`, `update.yml`, `README.md`, `USAGE.md` and `sports_data.py`.
2. **Create the repository.** On github.com, sign in (a free account is fine), tap **+** →
   **New repository**. Name it `sports-data`, choose **Public**, tick **Add a README file**,
   and tap **Create repository**. It must be public for the job to be free and for the
   phone to download the files without logging in.
3. **Upload the Python files.** In the repository, tap **Add file** → **Upload files**, choose
   `common.py`, `nfl.py`, `nhl.py`, `nba.py`, `update.py`, `sports_data.py`, `USAGE.md` and
   `README.md` (replacing the default README), then **Commit changes**. The job uses `sports_data.py` to
   build the daily props report.
4. **Add the workflow.** Tap **Add file** → **Create new file**. For the name, type exactly
   `.github/workflows/update.yml` (the slashes create the folders). Open `update.yml` in
   the Files app, copy all of it, paste it into the editor, then **Commit changes**.
5. **Run it once.** Open the **Actions** tab (enable workflows if it asks), tap
   **Update stats** → **Run workflow** → **Run workflow**. The first run fetches the whole
   season so far, so give it several minutes.
6. **Check the result.** A green check means it worked. On the repository's main page,
   tap **Releases** → **Stats data**: you should see twelve data files, four per sport, plus
   `daily_props.txt`. A red X means a sport
   failed its checks: tap the run, then **Fetch and check the data**, to read why.
7. **Set up the phone script.** Copy `sports_data.py` into a-Shell (its `REPO` line is set to
   `bdreitz/sports-data`). `USAGE.md` explains every command, its options and what the output
   looks like; in a-Shell, `usage()` (or `guide()`) shows the same. Then run:

   ```
   python3 -i sports_data.py
   >>> status()
   >>> daily_report()
   >>> nfl("josh allen")
   >>> nhl("mcdavid")
   >>> nba_team("NY")
   ```

## What happens each day

The job runs at 10:00 UTC (5-6am Eastern). For each sport it:

1. Downloads the files it published last time.
2. Fetches the standings and schedule, and box scores for new final games, **plus every
   game from the last 7 days** to pick up stat corrections. Every Monday it re-fetches the
   whole season instead. (NFL re-reads nflverse's whole-season files every day, so its
   corrections are always picked up.)
3. Replaces each re-fetched game's rows rather than adding to them, so nothing is ever
   duplicated, and games that aren't final are never included.
4. Checks the result before publishing:
   - every final game is present with both teams, and nothing else is;
   - every team's record matches the official standings (NHL also checks goals for and
     against; NFL is checked against ESPN's standings, including points for and against);
   - every final score matches the schedule;
   - players' totals add up to the team's (NHL goals and shots; NBA points, field goals
     and free throws; NFL passing, rushing and receiving yards).
5. Publishes only if every check passes. Otherwise the previous files stay as they were,
   the status file says what failed, and the run shows a red X. GitHub emails you about
   failed runs by default.

Then it builds `daily_props.txt` for that day's games (if there are any) and publishes it with
the data. If building the report fails, the data is still published and yesterday's report stays.

To force a full re-fetch, use **Run workflow** and tick **Re-fetch every game this season**.

## The daily props report

For each sport with games that day, `daily()` finds every player on those teams with a line he
reached in at least 80% of his last 10 games (the scanner's settings), then lists them under
their game:

- Games are in start-time order (Eastern). Games that start together are listed one after the
  other, each with its own props.
- Each game shows the sport, the start time and both teams' records.
- Each prop shows the line, his hit rate, average and lowest game, the matchup (below), and
  his last 10 games for that stat with the opponent, oldest first, `*` marking the games he
  fell short of the line (`daily(log=20)` for 20, `log=0` to hide it):
  - **NBA:** what the opponent allows per game to his position (guards, forwards, centers)
  - **NHL skaters:** shots on goal the opponent allows per game
  - **NHL goalies:** shots on goal the opponent's offense takes per game
  - **NFL QBs, WRs and TEs:** the opponent's pass defense (pass yards allowed; pass TDs
    allowed for TD props); **RBs:** the run defense (rush yards allowed)
  - the opponent's rank and how far above or below league average it is, with a verdict for
    the over: **favorable** (5%+ above average), **tough** (5%+ below) or **neutral**

Change `daily(page=False)` in the workflow (for example `daily(page=False, games=5)`) to
change the morning report; on the phone, `daily()` takes the same options.

Each sport's report also checks ESPN's injury report: players listed as Out (including injured
reserve and suspensions) are left off, and players who are Questionable, Doubtful or
Day-To-Day are marked with `!` and their status. Under each game's header, a short summary
lists both teams' injured regulars, grouped by status (team tables show the same summary):

- regulars are NBA players averaging 15+ minutes, NHL skaters averaging 12+ minutes and all
  goalies, and NFL players with stats this season;
- anyone Doubtful, Questionable or Day-To-Day is listed;
- Out players are listed only if they played in one of their team's last 3 games, since a
  long absence is already reflected in the recent stats.

The thresholds are `REGULAR_NBA_MINUTES`, `REGULAR_NHL_MINUTES` and `RECENT_TEAM_GAMES` in
`sports_data.py`. The morning report
checks injuries at 5-6am Eastern, so for anything close to game time run `daily()` on the phone,
which checks them right then (the injury report is re-fetched after 10 minutes).

On the phone, `daily_report()` and `daily()` show one game at a time (Return for the next game,
`a` for the rest, `q` to quit). `daily_report(save=True)` saves the report to a text file in
a-Shell's folder instead: open it from the Files app to scroll through it, or use Share → Notes.

## Things to know

- **The data is only as accurate as the source.** The checks guarantee the files match
  what the NHL and ESPN report. If the source is wrong, the files will be too.
- **NFL updates can trail by a day.** If a game has a final score on the schedule but
  nflverse hasn't added its player stats yet (most likely after Sunday or Monday night
  games), the NFL check fails that morning and the previous file stays up until nflverse
  catches up, usually by the next run.
- **The NBA Cup final** (mid-December) doesn't count in the standings. If ESPN lists it as a
  regular-season game, the NBA standings check will fail and NBA updates will pause until
  the code skips that game.
- **Scheduled runs can start late** when GitHub is busy, sometimes by up to an hour.
- **GitHub pauses schedules in repositories with no activity for 60 days.** If updates stop,
  open the Actions tab and re-enable the workflow.
- **Both APIs are undocumented.** If one changes, the checks will most likely catch it and
  stop publishing rather than publish wrong data. The run log shows the details.

## Future work

Neither of these is built yet. Both would be new modules alongside `nfl.py`, `nba.py` and
`nhl.py`, producing the same four files per season with the same leading columns.

### NCAA men's football (revisit before the 2027 season, in August)

**Scope:** only games where at least one team is ranked in the Top 25 (AP, or the College
Football Playoff rankings once they're released in November; make it a choice), and only
**team bets**, no player props. Roughly 15-20 games a regular-season week.

**Source:** ESPN's public college football JSON (`site.api.espn.com/apis/site/v2/sports/football/college-football`):

- the scoreboard by week (`groups=80` for FBS) gives every score, each team's rank, the
  neutral-site flag, quarter-by-quarter scores, and usually a spread and total from ESPN's
  sportsbook partner; about 15 calls covers a season, and no box scores are needed;
- the rankings feed has the AP, Coaches and CFP polls.

**The key difference from the NFL: schedule strength.** A ranked team's history can mix
63-7 against an FCS school with 17-14 against a top-10 rival, so raw hit rates mislead.
Everything should be adjusted for opponents:

- build simple **opponent-adjusted ratings** from final scores: an offense rating (points
  scored) and a defense rating (points allowed) for every FBS team, corrected for schedule,
  with FCS opponents pooled as one group;
- in consistency checks, count **only games against FBS opponents**.

**Bets, best fit first:**

1. **Team totals (over and under).** Two lenses; the strongest signal is when they agree:
   - consistency: the highest line cleared in 80%+ of games against FBS opponents;
   - rating: expected points from his offense rating against this opponent's defense rating.
2. **Game totals** (both teams), with the same two lenses; a cross-check on team totals.
3. **Spreads**, as a sanity check: the rating's expected margin beside the posted spread
   (e.g. "line ALA -7.5, model -11.2"), flagging big disagreements. Spreads on ranked games
   are very efficient markets, so this isn't a pick generator.
4. **First-half totals and spreads** (optional; quarter scores come free): useful in big
   mismatches where starters sit in the second half.

Skip moneylines on big favorites (prices too steep) and stat props like total yards or
turnovers (rarely offered).

**How it would fit in:**

- `cfb.py` in the daily job: one row per team per game with scores, ranks at kickoff,
  home / away / neutral, FBS or FCS opponent, and ESPN's spread and total; checks that every
  final game has both teams and that scores match the scoreboard.
- On the phone: `cfb_slate()` for the week's ranked games in kickoff order (both teams' ranks
  and records, posted spread and total, the model's numbers, consistency team totals with a
  scores log against FBS opponents, in the same layout as `daily()`), plus `cfb_team("ALA")`
  and `cfb_rankings()`.
- No home-field edge at neutral sites: conference championships, the playoff and most bowls.
- Ratings are shakiest in the first few weeks; consider blending in the previous season's
  final ratings early on.

### MLB

- **Source:** the official MLB Stats API (`statsapi.mlb.com`), public with no key, and the
  closest match to the NHL API: schedule (regular season only), one box score per game,
  and official standings. It's intended for personal, non-commercial use.
- **Files:** separate batter, pitcher and team-game files, since hitters and pitchers have
  different stats.
- **Volume:** about 2,430 games a season, roughly 15 a day. Daily updates are small. A full
  rebuild late in the season is about 15 minutes, so MLB's should be monthly, not weekly.
- **Care needed:** doubleheaders (each game has its own id, so they fit as-is) and
  suspended games, which should only be added once they're actually completed.
- **Checks:** records against the official standings, runs against each game's line score,
  and players' hits and runs against the team totals.
- **Timing:** build in early March and test against spring training games before Opening
  Day in late March.

**Props to focus on** (the ones that suit the consistency approach):

1. **Pitcher strikeouts (the centerpiece).** Built from 20-25 batters faced, and strikeout
   rate is among the most stable stats in baseball. Matchup: the opposing lineup's strikeout
   rate, ideally against the pitcher's handedness.
2. **Pitcher outs recorded and hits allowed.** Workhorse starters are consistent; the risk is
   an early exit, so show recent pitch counts beside the log.
3. **Team totals**, with both overs and unders (as the old NFL tool did for team points):
   - team hits (steadiest; combined game hits steadier still)
   - team strikeouts (the flip side of pitcher strikeouts; a useful cross-check)
   - team walks (good stat, but check that the sportsbook offers it)
   - team runs (weakest: best as unders, or as first-five-innings totals)

Skip home runs, RBIs, stolen bases and single-stat hitter props: too rare or too random per
game to be consistent.

**Design notes:**

- Pitcher windows should count starts (last 5-8), since starters pitch about every fifth day.
- Matchups come from the opposing **starting pitcher** (strikeout, walk and hit rates, and
  handedness against the lineup) plus the ballpark, rather than a team defense ranking.
- Lineups are posted 2-3 hours before first pitch; the report should flag games whose
  lineups aren't posted yet, much like confirming an NHL starting goalie.
- 80%+ props are usually priced steeply (often -300 or worse), so the matchup filter, not the
  hit rate alone, is where any edge comes from.

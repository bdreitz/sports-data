# sports-data

A daily GitHub Actions job that builds NFL, NBA and NHL game-by-game stats files in one
consistent format and publishes them on this repository's **data** release, available
for download.

- **NFL** comes from nflverse's published files, reshaped to the shared format. Records are
  cross-checked against ESPN's standings.
- **NHL** comes from the official NHL API (api-web.nhle.com).
- **NBA** comes from ESPN's public JSON (site.api.espn.com).

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
   `nba.py`, `update.py`, `update.yml`, `README.md` and `sports_data.py`.
2. **Create the repository.** On github.com, sign in (a free account is fine), tap **+** →
   **New repository**. Name it `sports-data`, choose **Public**, tick **Add a README file**,
   and tap **Create repository**. It must be public for the job to be free and for the
   phone to download the files without logging in.
3. **Upload the Python files.** In the repository, tap **Add file** → **Upload files**, choose
   `common.py`, `nfl.py`, `nhl.py`, `nba.py`, `update.py` and `README.md` (replacing the
   default README), then **Commit changes**.
4. **Add the workflow.** Tap **Add file** → **Create new file**. For the name, type exactly
   `.github/workflows/update.yml` (the slashes create the folders). Open `update.yml` in
   the Files app, copy all of it, paste it into the editor, then **Commit changes**.
5. **Run it once.** Open the **Actions** tab (enable workflows if it asks), tap
   **Update stats** → **Run workflow** → **Run workflow**. The first run fetches the whole
   season so far, so give it several minutes.
6. **Check the result.** A green check means it worked. On the repository's main page,
   tap **Releases** → **Stats data**: you should see twelve files, four per sport. A red X means a sport
   failed its checks: tap the run, then **Fetch and check the data**, to read why.
7. **Set up the phone script.** Copy `sports_data.py` into a-Shell, change the `REPO` line
   near the top to `your-github-name/sports-data`, then run:

   ```
   python3 -i sports_data.py
   >>> status()
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

To force a full re-fetch, use **Run workflow** and tick **Re-fetch every game this season**.

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

### NCAA men's football

- **Source:** ESPN's public JSON (`site.api.espn.com/apis/site/v2/sports/football/college-football`),
  the same approach as `nba.py`: team schedules for the game list, one summary per game for
  box scores, and ESPN's conference standings for the record check. No key needed.
- **Optional cross-check:** CollegeFootballData.com. It has richer stats and betting lines,
  but needs a free API key (kept as a GitHub secret) and the free tier is limited to about
  1,000 calls a month. It returns a whole week of games per call, so a daily check could
  still fit.
- **Volume:** about 130 FBS teams and 50-60 FBS games on a typical Saturday.
- **Decisions needed:** whether to include games against FCS teams (and FCS players' stats),
  and whether bowl games and the playoff go in a separate file, as postseason games are
  left out for the other sports.
- **Checks:** records against ESPN's standings, final scores against the schedule, and
  player passing, rushing and receiving yards against team totals.
- **Timing:** the season runs late August to January.

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

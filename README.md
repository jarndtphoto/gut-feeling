# Gut Feeling

Survivor pool picks for me and my friends. Open the app at
https://jarndtphoto.github.io/gut-feeling and add it to your Home Screen.

## How the data updates
- `scripts/update.py` pulls win % and lines (The Odds API, ESPN backup),
  records, form, QBs and injuries (ESPN), and weather (Open-Meteo), then writes `data.json`.
- `.github/workflows/update-data.yml` runs it every morning at 7 AM Central,
  plus game-day checks about an hour before each kickoff.
- To run it by hand: Actions tab > Update picks data > Run workflow.
- Needs the `ODDS_API_KEY` repository secret.

Used teams are saved on each phone, not in this repo.

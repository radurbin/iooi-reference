# iooi Reference

Stats, history and records for the Dynasty iooi fantasy football league, in the manner of the Sports Reference sites. Live at https://dynastyiooi.com/ (the old address, https://radurbin.github.io/iooi-reference/, redirects there).

A fan-made parody for one league. Not affiliated with Sports Reference LLC or Sleeper.

## How it works

Nothing runs on a server. Python scripts turn Sleeper data into JSON files ahead of time; the pages are plain HTML and JavaScript that read those files.

```
Sleeper API ─┐
nflverse    ─┴─fetch.py──▶ data/raw/ ──build.py──▶ data/site/ ──▶ *.html + assets/site.js
```

| Path | What it is |
| --- | --- |
| `*.html` | One file per page type. `player.html?id=4984`, `season.html?y=2025` and so on. |
| `assets/site.js`, `assets/site.css` | Shared page chrome, tables, tooltips and styles. |
| `data/site/` | The JSON the pages read. Written by the build; never edit by hand. |
| `data/raw/` | Sleeper API responses as fetched, and each season's injury statuses (`<season>/injuries.json`). |
| `data/nfl/` | Weekly NFL points and projections under league scoring, trimmed from `data/cache/`. |
| `data/chat_events/` | League-office events from the league chat: team renames, nickname changes, scoring changes, trade review outcomes. |
| `data/manual/` | Facts Sleeper does not hold: managers and stakes, the constitution, the 2024 split draft, trade notes, the Hall of Fame. |
| `tankathon/` | The iooi Tankathon draft-order page. |

## Refreshing the data

A GitHub Actions job (`.github/workflows/refresh.yml`) runs every day at 08:30 UTC (3:30 AM Central in daylight time, 2:30 AM in standard time; after Sleeper rolls to the next week at 1 AM Eastern on Tuesday), and on demand from the Actions tab. It fetches from Sleeper, rebuilds `data/site/`, and commits the result; GitHub Pages then redeploys.

To run the same steps by hand (a recent Python 3, no packages to install):

```
python3 scripts/fetch.py --players   # league data and the NFL player list (read-only GET requests)
python3 scripts/sync_chat.py         # needs SLEEPER_TOKEN, see below
python3 scripts/build.py             # rebuild data/site/
```

Completed seasons are fetched once and then left alone.

### Injury data

The injuries page and the injury sections on player, team, franchise, manager, season and box score pages come from
[nflverse](https://github.com/nflverse/nflverse-data) (free, public, no key): the NFL's weekly roster statuses (injured
reserve, PUP, game-day inactives) and the official injury reports. `fetch.py` downloads the current season's two files on
every run (about 1 MB compressed) and keeps only skill players who were anything but plainly active, in
`data/raw/<season>/injuries.json`. A season saved after it ended is never downloaded again. Current statuses on the
Out Now list come from the Sleeper player list.

If nflverse is down, renames its files or changes its columns, the run logs a warning, keeps the last good copy, and
carries on; nothing else is affected. A warning on the daily job's summary page is the sign to look. The rules for what
counts as a game missed to injury are at the top of `scripts/injuries.py`.

The NFL report names nothing for a player who goes straight to injured reserve. For those, each run also saves Sleeper's
injury note for the current week into the season's file (Sleeper only shows a note as it is today, so it has to be
kept as the season goes). Anything still unnamed can be named by hand in `data/manual/injury_notes.json`; the build
lists each unnamed absence on an iooi roster as a notice on the daily job's summary page.

### The Sleeper token

Three things come only from Sleeper's private endpoint and need a logged-in user's token: league-office events posted in the league chat (team renames, nickname changes, scoring changes, trades under review, vetoes), the vote tally on each trade, and the official weekly scores. `scripts/sync_chat.py` reads only the chat messages posted since its last run, keeps the system events with profile details removed, and discards everything else. The chat itself is never stored here.

The token is held as the repository secret `SLEEPER_TOKEN`. It lasts one year. When it expires, the daily job still publishes everything public and then fails, which sends an email. To replace it: log in at sleeper.com, copy the `authorization` header from any `graphql` request in the browser's network tab, and save it as the secret (Settings, Secrets and variables, Actions). The next run catches up on everything missed.

## Viewing locally

```
python3 -m http.server 8770
```

Then open http://localhost:8770.

## Moving to a custom domain

The custom domain is set by the `CNAME` file at the repo root (dynastyiooi.com, registered and with DNS at Cloudflare: four A records for the bare domain and a `www` CNAME to `radurbin.github.io`, all "DNS only"). The link-preview image address in each page's `<head>` is absolute; to move domains, replace `https://dynastyiooi.com/` across `*.html` and change `CNAME`.

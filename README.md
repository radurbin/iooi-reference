# iooi Reference

Stats, history and records for the Dynasty iooi fantasy football league, in the manner of the Sports Reference sites. Live at https://dynastyiooi.com/ (the old address, https://radurbin.github.io/iooi-reference/, redirects there).

A fan-made parody for one league. Not affiliated with Sports Reference LLC or Sleeper.

## How it works

Nothing runs on a server. Python scripts turn Sleeper data into JSON files ahead of time; the pages are plain HTML and JavaScript that read those files.

```
Sleeper API ──fetch.py──▶ data/raw/ ──build.py──▶ data/site/ ──▶ *.html + assets/site.js
```

| Path | What it is |
| --- | --- |
| `*.html` | One file per page type. `player.html?id=4984`, `season.html?y=2025` and so on. |
| `assets/site.js`, `assets/site.css` | Shared page chrome, tables, tooltips and styles. |
| `data/site/` | The JSON the pages read. Written by the build; never edit by hand. |
| `data/raw/` | Sleeper API responses as fetched. |
| `data/nfl/` | Weekly NFL points and projections under league scoring, trimmed from `data/cache/`. |
| `data/chat_events/` | League-office events from the league chat: team renames, nickname changes, scoring changes, trade review outcomes. |
| `data/manual/` | Facts Sleeper does not hold: managers and stakes, the constitution, the 2024 split draft, trade notes, the Hall of Fame. |
| `tankathon/` | The iooi Tankathon draft-order page. |

## Refreshing the data

A GitHub Actions job (`.github/workflows/refresh.yml`) runs every day at 10:00 UTC, and on demand from the Actions tab. It fetches from Sleeper, rebuilds `data/site/`, and commits the result; GitHub Pages then redeploys.

To run the same steps by hand (a recent Python 3, no packages to install):

```
python3 scripts/fetch.py --players   # league data and the NFL player list (read-only GET requests)
python3 scripts/sync_chat.py         # needs SLEEPER_TOKEN, see below
python3 scripts/build.py             # rebuild data/site/
```

Completed seasons are fetched once and then left alone.

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

# iooi Reference

Stats, history and records for the Dynasty iooi fantasy football league, in the manner of the Sports Reference sites. Live at https://radurbin.github.io/iooi-reference/.

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

Needs a recent Python 3 (developed on 3.14). No packages to install.

```
python3 scripts/fetch.py     # pull the current season from Sleeper (read-only GET requests)
python3 scripts/build.py     # rebuild data/site/
```

Completed seasons are fetched once and then left alone. `fetch.py --players` also refreshes the NFL player list.

Two things need a logged-in Sleeper token and are run by hand (see the docstring in `scripts/fetch_chat.py`): trade vote tallies, and the chat export that `scripts/extract_chat.py` turns into `data/chat_events/`. The raw chat is never stored in this repository.

## Viewing locally

```
python3 -m http.server 8770
```

Then open http://localhost:8770.

## Moving to a custom domain

The link-preview image address in each page's `<head>` is absolute. Replace `https://radurbin.github.io/iooi-reference/` across `*.html`.

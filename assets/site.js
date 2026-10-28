// Shared helpers for every iooi Reference page: data loading, links, sortable stat tables, page chrome.
const DATA = "data/site/";
if (/Windows/.test(navigator.userAgent)) document.documentElement.classList.add("flagfont");   // flag emoji font; see site.css
const cache = {};

function load(name) {
  cache[name] ??= fetch(`${DATA}${name}.json`, { cache: "no-cache" }).then((r) => {
    if (!r.ok) throw new Error(`Could not load ${name}.json`);
    return r.json();
  });
  return cache[name];
}

const param = (key) => new URLSearchParams(location.search).get(key);

function h(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

const fmt = {
  pts: (v) => (v == null ? "" : v.toFixed(2)),
  pts1: (v) => (v == null ? "" : v.toFixed(1)),
  pct: (v) => (v == null ? "" : v.toFixed(3).replace(/^0/, "")),
  eff: (v) => (v == null ? "" : `${(v * 100).toFixed(1)}%`),
  signed: (v) => (v == null ? "" : `${v > 0 ? "+" : ""}${v.toFixed(2)}`),
  date: (v) => v || "",
};

// League-wide lookups, filled in by page() before a page renders.
let L = null;
const franchiseName = (id) => L.franchises.find((f) => f.id === Number(id))?.name ?? `Franchise ${id}`;
const managerName = (slug) => L.managers.find((m) => m.id === slug)?.name ?? slug;
const seasonTeam = (season, id) => L.seasons.find((s) => s.season === String(season))?.teams[id] ?? franchiseName(id);

const fLink = (id, text) => h("a", { href: `franchise.html?id=${id}` }, text ?? franchiseName(id));
const mLink = (slug) => h("a", { href: `manager.html?id=${slug}` }, managerName(slug));
const mLinks = (slugs) => (slugs || []).flatMap((s, i) => (i ? [", ", mLink(s)] : [mLink(s)]));
const sLink = (season, text) => h("a", { href: `season.html?y=${season}` }, text ?? season);
const pLink = (id, name) => h("a", { href: `player.html?id=${id}` }, name ?? id);
const tLink = (id, text) => h("a", { href: `trade.html?id=${id}` }, text ?? "trade");
const ordinal = (n) => `${n}${["th", "st", "nd", "rd"][n % 100 > 10 && n % 100 < 14 ? 0 : Math.min(n % 10, 4) % 4]}`;
// Bold for an active player in a leader list, as on the reference sites. players is players.json;
// `on` narrows it to players now with one of the given franchises.
const activeCls = (players, on) => (r) => { const st = players[r.player]?.status; return st?.code === "rostered" && (!on || on.includes(st.franchise)) ? "win" : null; };
const activeNote = (text = "Bold marks a player now on an iooi roster.") => h("p", { class: "note" }, text);
// A drafted pick's own page, e.g. pick.html?y=2025&p=1.09.
const pickLink = (season, label, text) => h("a", { href: `pick.html?y=${season}&p=${label}` }, text ?? label);
// A pick whose number is not yet set, by draft, round and the franchise it first belonged to, e.g. pick.html?y=2027&r=1&o=1.
const futurePickLink = (season, round, original, text) => h("a", { href: `pick.html?y=${season}&r=${round}&o=${original}` }, text ?? `${season} ${ordinal(round)}-round pick`);
const tsLink = (season, id, text) => h("a", { href: `team.html?y=${season}&id=${id}` }, text ?? seasonTeam(season, id));
const rLink = (kind, a, b, text) => h("a", { href: `rivalry.html?${kind}=${[a, b].sort((x, y) => (String(x) < String(y) ? -1 : 1)).join("~")}` }, text ?? "rivalry");
const gLink = (id, text) => h("a", { href: `game.html?id=${id}` }, text ?? "box");
const record = (r, w = "w", l = "l", t = "t") => `${r[w]}-${r[l]}${r[t] ? `-${r[t]}` : ""}`;

// Hover text for column headings that do not set their own.
const GLOSSARY = {
  "": "Link to the full details", "Rk": "Rank", "Black Ink": "League-leading seasons, weighted by category; see the Black Ink section of the leaders page",
  "Opp": "Opponent", "Score": "Final score, this team first", "Rec": "Regular-season record after the game", "Top scorer": "The team's highest-scoring starter that week",
  "Sim": "Similarity score: 1000 is identical; lower means further apart across the compared stats", "Split": "Group of games", "Low": "Lowest single game",
  "vs Proj": "Average points above or below Sleeper's projection", "Team W-L": "Team record in those games", "Streak": "Consecutive games", "From": "First game of the streak", "To": "Last game of the streak", "W": "Wins", "L": "Losses", "W-L%": "Win-loss percentage: wins divided by games, ties counting as half a win",
  "PF": "Points for: points scored by the starting lineup", "PA": "Points against: points scored by opponents", "PF/G": "Points for per game",
  "Max PF": "Points from the best possible lineup each week", "Eff": "Lineup efficiency: points for as a share of Max PF",
  "All-Play": "Record if the team had played every other team every week",
  "xW": "Expected wins: all-play winning percentage times games played", "Luck": "Actual wins minus expected wins",
  "Trans": "Completed trades, waiver claims and free-agent moves", "Trades": "Completed trades",
  "Seed": "Playoff seeding position at the end of the regular season", "Finish": "Final place after the playoffs and Toilet Bowl",
  "Playoffs": "Dynasty Playoff result", "Plyf": "Playoff appearances", "Plyf W-L": "Record in Dynasty Playoff games",
  "Titles": "League championships", "Active": "Seasons with a stake in a franchise", "Years": "Seasons in which he started for the team or manager",
  "Season": "League season", "Week": "Week of the NFL season", "Date": "Date, US Central time", "When": "Date, US Central time",
  "Franchise": "Franchise, under its name at the time", "Franchise(s)": "Franchises managed", "Team": "Franchise, under its name at the time",
  "Team(s)": "Franchises he played for that season", "Manager": "Manager with a stake in the franchise at the time",
  "Manager(s)": "Managers with a stake in the franchise at the time", "By": "Manager who made the move",
  "Acquired stake": "Date the manager took a stake in the franchise", "Sold stake": "Date the manager gave up the stake; blank if still held",
  "Notes": "Notes on the stake", "Renamed to": "New team name", "Old name": "Team name before the change", "New name": "Team name after the change",
  "Player": "Player; click for his iooi page", "Pos": "Position", "NFL": "Current NFL team", "Slot": "Lineup slot", "Role": "Starter, bench, or no game that week",
  "G": "Games", "GS": "Games in a starting lineup", "Pts": "Points", "Pts/GS": "Points per start", "Pts/G": "Points per game",
  "Best": "Best single game", "Bench Pts": "Points scored while on the bench", "Proj": "Sleeper's projection before kickoff",
  "PAA": "Points above the average iooi starter at his position that week", "VOR": "Value over a replacement-level player at his position",
  "WAR": "Wins above replacement: VOR converted to wins", "Team result": "Result for his team that week",
  "Rd": "Round", "Round": "Round", "Pick": "Pick within the round", "Ovr": "Overall pick number", "Draft": "Draft year", "Class": "Draft class",
  "Picks": "Picks made", "Hit%": "Share of picks that became hits", "Pts/Pick": "Starter points per pick",
  "Record": "Regular-season record", "Odds": "Chance at the first pick under the lottery rules", "Moved": "Places gained or lost in the draft order",
  "Champion": "Winner of the Dynasty Championship", "Runner-up": "Loser of the Dynasty Championship", "Third place": "Winner of the third-place game",
  "Ult. Loser": "Loser of the Toilet Bowl final", "Ultimate Loser": "Loser of the Toilet Bowl final", "Top seed": "Best regular-season record",
  "Most points": "Most regular-season points", "Scoring title": "Most regular-season points", "Best lineup efficiency": "Highest share of Max PF actually scored",
  "Luckiest": "Most wins above expected", "Unluckiest": "Most wins below expected", "Winner": "Winning team", "Loser": "Losing team",
  "Status": "Where he stands today: on an iooi roster, a free agent with an NFL team, or without an NFL team",
  "Claims": "Players added by waiver claim", "FA Adds": "Players signed as free agents", "Span": "Weeks the stretch covers", "Avg": "Points per week over the stretch",
  
  "Opponent": "Opposing team", "Opp Pts": "Opponent's points", "Result": "Final score", "Stage": "Regular season, playoffs or Toilet Bowl", "Game": "Link to the box score",
  "Term": "Stat or term", "Abbr": "Column heading as it appears in tables", "Meaning": "What it means",
  "Elo": "Elo rating: 1500 is average; it rises with wins and falls with losses, by more for big margins and upsets",
  "Peak": "Highest Elo rating reached", "Peak set": "Week the peak was reached", "Low": "Lowest Elo rating reached", "Low set": "Week the low was reached",
  "Win chance": "Winner's chance of winning before kickoff, from the two Elo ratings",
  "Matchup": "The two sides", "Series": "All-time record, first side listed", "Avg score": "Average score, first side listed first",
  "Avg margin": "Average margin of victory for the first side listed", "Last": "Most recent meeting",
  "Terms": "What each side received", "Asset": "Player or pick received", "Became": "Player the pick was used on", "Note": "Notes on the asset",
  "Net": "Points in minus points out", "Margin": "Difference in starter points between the two sides", "Ahead": "Side ahead in starter points since the trade",
  "Behind": "Side behind in starter points since the trade",
  "Adds": "Players added from waivers or free agency", "Releases": "Players released", "Pts/Add": "Starter points per player added",
  "Added": "Date added", "Released": "Date released", "Until": "Date he left the franchise; blank if still there", "How": "Waiver claim or free-agent signing",
  "GS since": "Games started for anyone after the release", "Pts since": "Starter points for anyone after the release", "Next started for": "First franchise to start him afterward",
};
const tipFor = (col) => col.title ?? GLOSSARY[col.label];

// One floating box for all hover text, so it is instant and never clipped by a scrolling table.
function tooltips() {
  const tip = h("div", { id: "tip", hidden: true });
  document.body.append(tip);
  const show = (el) => {
    tip.textContent = el.dataset.tip;
    tip.hidden = false;
    const box = el.getBoundingClientRect();
    const left = Math.max(6, Math.min(box.left + box.width / 2 - tip.offsetWidth / 2, document.documentElement.clientWidth - tip.offsetWidth - 6));
    const above = box.top - tip.offsetHeight - 6;
    tip.style.left = `${left}px`;
    tip.style.top = `${above < 4 ? box.bottom + 6 : above}px`;
  };
  document.addEventListener("mouseover", (e) => { const el = e.target.closest?.("[data-tip]"); if (el) show(el); else tip.hidden = true; });
  document.addEventListener("focusin", (e) => { const el = e.target.closest?.("[data-tip]"); if (el) show(el); });
  document.addEventListener("scroll", () => { tip.hidden = true; }, true);
  // Phones have no hover: a tap on a heading shows its text, a tap anywhere else clears it.
  document.addEventListener("touchstart", (e) => { const el = e.target.closest?.("[data-tip]"); if (el) show(el); else tip.hidden = true; }, { passive: true });
}

// columns: [{ label, title, value(row), render(row), num, cls }]. value() drives sorting; render() the cell.
// groupBy(row): where its value changes from one row to the next, the column headings are repeated, as on the reference sites.
function statTable({ columns, rows, foot, rowClass, groupBy, sortable = true }) {
  const table = h("table", { class: "stats" });
  const tbody = h("tbody");
  let sorted = { index: -1, asc: false };
  const cell = (col, row, tag = "td") => {
    const shown = col.render ? col.render(row) : col.value(row);
    const cls = [col.num ? "num" : "", typeof col.cls === "function" ? col.cls(row) : col.cls].filter(Boolean).join(" ");
    return h(tag, { class: cls || null }, shown ?? "");
  };
  const rehead = () => h("tr", { class: "rehead" }, columns.map((c) => h("th", { class: c.num ? "num" : null }, c.label)));
  const draw = (list) => tbody.replaceChildren(...list.flatMap((row, i) => [
    groupBy && i && groupBy(row) !== groupBy(list[i - 1]) ? rehead() : null,
    h("tr", { class: rowClass?.(row) }, columns.map((c) => cell(c, row)))]).filter(Boolean));
  const heads = columns.map((col, index) =>
    h("th", {
      class: [col.num ? "num" : "", sortable && col.value ? "sortable" : ""].join(" ").trim() || null,
      "data-tip": tipFor(col),
      onclick: !sortable || !col.value ? null : () => {
        const asc = sorted.index === index ? !sorted.asc : !col.num;
        sorted = { index, asc };
        heads.forEach((th, i) => { th.classList.toggle("sorted", i === index); th.classList.toggle("asc", i === index && asc); });
        const key = (row) => col.value(row);
        draw([...rows].sort((a, b) => {
          const [x, y] = [key(a), key(b)];
          if (x == null) return 1;
          if (y == null) return -1;
          return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * (asc ? 1 : -1);
        }));
      },
    }, col.label));
  table.append(h("thead", {}, h("tr", {}, heads)), tbody);
  if (foot) table.append(h("tfoot", {}, h("tr", {}, columns.map((c) => cell(c, foot)))));
  draw(rows);
  return h("div", { class: "scroll" }, table);
}

// Timeline text carries references like [[f:3]], [[m:dan]], [[g:2025-17-7-8]] or [[f:3|Old Name]]; turn them into links.
function rich(text) {
  const make = {
    f: (id, label) => fLink(id, label), m: (id) => mLink(id), p: (id, label) => pLink(id, label), g: (id) => gLink(id, "box score"),
    t: (id, label) => tLink(id, label ?? "details"), s: (id) => sLink(id), d: (id) => h("a", { href: `draft.html?y=${id}` }, "board"),
    c: (id) => h("a", { href: `constitution.html#${id}` }, "text"), x: (href, label) => h("a", { href }, label),
  };
  return text.split(/(\[\[[^\]]+\]\])/).map((part) => {
    const m = part.match(/^\[\[(\w):([^|\]]+)(?:\|([^\]]+))?\]\]$/);
    return m && make[m[1]] ? make[m[1]](m[2], m[3]) : part;
  });
}

// Page header in the reference-site manner: picture, name and bold-label lines on the left, award badges on the right.
// awards: [[label, season, class]]. Repeats collapse to "3x Label"; a single award shows its season.
// "2023-2025, 2027": consecutive years collapse to a range.
function yearRuns(years) {
  const sorted = [...new Set(years.map(Number))].sort((x, y) => x - y);
  const runs = [];
  for (const y of sorted) {
    if (runs.length && y === runs.at(-1)[1] + 1) runs.at(-1)[1] = y;
    else runs.push([y, y]);
  }
  return runs.map(([from, to]) => (from === to ? from : `${from}-${to}`)).join(", ");
}
function bling(awards) {
  const groups = new Map();
  for (const [label, season, cls] of awards) groups.set(label, { cls, seasons: [...(groups.get(label)?.seasons ?? []), season] });
  return [...groups].map(([label, { cls, seasons }]) => h("li", { class: cls, "data-tip": `${label}: ${yearRuns(seasons)}` }, seasons.length > 1 ? `${seasons.length}x ${label}` : `${seasons[0]} ${label}`));
}
function bio({ name, photo, logo, lines, nav, awards = [], tiles = [] }) {
  const img = photo && h("img", { class: logo ? "photo logo" : "photo", src: photo, alt: "" });
  img?.addEventListener("error", () => img.remove());
  const dot = () => h("span", { class: "sep" }, " ▪ ");
  return h("div", { id: "bio" }, img,
    h("div", { class: "info" }, h("h1", {}, name),
      nav?.some(Boolean) ? h("div", { class: "seasonnav" }, nav.filter(Boolean)) : null,
      lines.filter(Boolean).map((line) => {
        if (!Array.isArray(line[0])) line = [line];
        const parts = line.filter((part) => part && part[1] != null && part[1] !== "" && part[1] !== false);
        return parts.length ? h("p", {}, parts.flatMap(([label, value], i) => [i ? dot() : null, label ? h("b", {}, `${label}: `) : null, value])) : null;
      })),
    awards.length || tiles.length ? h("div", { class: "side" }, awards.length ? h("ul", { class: "bling" }, bling(awards)) : null, tiles.length ? h("div", { class: "tiles" }, tiles) : null) : null);
}

// Small top-five boxes, as at the head of a season page.
function leaderBoxes(boards, linkOf) {
  return h("div", { class: "leaders" }, boards.filter((b) => b.rows.length).map((b) => h("div", { class: "lbox" },
    h("h4", { "data-tip": b.tip }, b.title),
    // Equal values (as shown) share a number.
    h("ol", {}, rank(b.rows, (r) => r.value.toFixed(b.digits)).map((r) => h("li", { value: r.rk }, h("span", {}, linkOf(r)), h("b", {}, r.value.toFixed(b.digits))))))));
}

// What each side of a trade received, one line per side. players is players.json.
// Record-book places (from the build's record entries) as a table: the list, the place, the figure, who and when.
// opts.rank shows the place column; opts.scope names the franchise for a franchise's own list.
function recordTable(entries, players, opts = {}) {
  const holder = (e) => (e.player ? pLink(e.player, players[e.player]?.name ?? e.player) : e.franchise ? (e.season ? tsLink(e.season, e.franchise, e.team) : fLink(e.franchise)) : "");
  const when = (e) => (e.game ? gLink(e.game, `${e.season} wk ${e.week}`) : e.season ? (e.franchise && !e.player ? tsLink(e.season, e.franchise, `${e.season} season`) : sLink(e.season, `${e.season} season`)) : "Career");
  const list = (e) => [h("a", { href: e.href }, e.list), opts.scope && e.scope !== "league" ? h("small", { class: "dim" }, opts.scope === "self" ? " (franchise record)" : ` (${franchiseName(e.scope)})`) : null];
  return statTable({
    sortable: false, rows: entries,
    columns: [
      { label: "Record", render: list, cls: "win", title: "The list, in the record book or on a franchise page" },
      opts.rank && { label: "Place", render: (e) => (e.rank === 1 ? h("b", {}, "1st (record)") : ordinal(e.rank)), num: true, title: "Place on the list as it stands today; ties share a place" },
      { label: "Value", render: (e) => e.value, num: true },
      opts.holder !== false && { label: "Holder", render: holder },
      opts.team && { label: "Team", render: (e) => (e.player && e.franchise ? (e.season ? tsLink(e.season, e.franchise, e.team) : fLink(e.franchise)) : "") },
      { label: "When", render: when },
    ].filter(Boolean),
  });
}

function tradeTerms(t, players) {
  const asset = (a) => (a.type === "player" ? pLink(a.player, players[a.player]?.name) : a.became ? [`${a.season} ${ordinal(a.round)}-round pick`, " (", pickLink(a.became.season, a.became.label), ", ", pLink(a.became.player, players[a.became.player]?.name), ")"]
    : a.original ? [futurePickLink(a.season, a.round, a.original), ` (originally ${seasonTeam(t.season, a.original)})`] : `${a.season} ${ordinal(a.round)}-round pick`);
  return t.sides.map((s) => h("div", {}, fLink(s.franchise, s.team), " received: ", s.assets.length ? s.assets.flatMap((a, i) => [i ? ", " : "", asset(a)]) : "nothing"));
}
const tradeStatus = { label: "Status", value: (t) => t.status, render: (t) => ({ complete: "", vetoed: "Vetoed", cancelled: "Withdrawn", pending: "Under review" })[t.status] ?? t.status, cls: "c", title: "Blank for a completed trade; otherwise under review, vetoed by league vote, or withdrawn during review" };

// A player's standing today, from players.json: on an iooi roster, a free agent, or without an NFL team.
const statusOrder = { rostered: 0, free_agent: 1, out: 2 };
function statusText(st, linked = true) {
  if (!st) return "";
  if (st.code === "rostered") return linked ? fLink(st.franchise) : franchiseName(st.franchise);
  if (st.code === "free_agent") return "Free agent";
  return st.last_nfl ? `No NFL team (last played ${st.last_nfl})` : "No NFL team";
}

const section = (title, ...kids) => [h("h2", { id: title.toLowerCase().replace(/[^a-z0-9]+/g, "-") }, title), ...kids];
const meta = (pairs) => h("ul", { class: "meta" }, pairs.filter((p) => p && p[1] != null && p[1] !== "").map(([k, v]) => h("li", {}, h("b", {}, `${k}: `), v)));
// Number a sorted list 1, 2, 3... Given the value it is sorted by, rows with equal values share a place (1, 2, 2, 4).
const rank = (list, value) => list.reduce((out, row, i) => {
  const prev = out[i - 1];
  out.push({ ...row, rk: value && prev && value(row) === value(list[i - 1]) ? prev.rk : i + 1 });
  return out;
}, []);

// Everything the search box looks through: players (by name or nickname), franchises (by any name they have used), managers,
// and draft picks. Picks are matched word by word ("2027 1st mojo") and only for a query with a number in it.
let searchIndex = null;
async function searchGroups(query, limit = 8) {
  const q = query.trim().toLowerCase();
  if (!searchIndex) {
    const [players, picks] = await Promise.all([load("players"), load("pick_search")]);
    const span = (y) => (y ? (y[0] === y[1] ? y[0] : `${y[0]}-${y[1]}`) : "");
    searchIndex = {
      "iooi Players": { href: "players.html", rows: Object.entries(players).map(([id, p]) => ({ name: p.name, href: `player.html?id=${id}`, years: span(p.years ?? (p.status?.code === "rostered" ? [L.current_season, L.current_season] : null)), active: p.status?.code === "rostered", sort: p.pts ?? 0, note: p.positions?.join("/") ?? p.pos, also: (p.nicknames ?? []).join(" | "),
        team: p.status?.code === "rostered" ? { franchise: p.status.franchise } : p.last_team })) },
      "iooi Franchises": { href: "index.html#franchises", rows: L.franchises.map((f) => ({ name: f.name, href: `franchise.html?id=${f.id}`, years: span([L.seasons[0].season, L.current_season]), active: true, sort: 0, note: "Franchise", also: [...new Set(L.seasons.map((s) => s.teams[f.id]))].filter((n) => n && n !== f.name).join(" | ") })) },
      "iooi Managers": { href: "index.html#managers", rows: L.managers.map((m) => ({ name: m.name, href: `manager.html?id=${m.id}`, years: span(m.years), active: m.active, sort: 0, note: "Manager", also: "", team: { franchise: m.franchise } })) },
      "iooi Draft Picks": { href: "picks.html", words: true, rows: picks.map((x, i) => ({ name: x.name, also: x.also, years: x.season, active: false, sort: -i,
        href: x.label ? `pick.html?y=${x.season}&p=${x.label}` : `pick.html?y=${x.season}&r=${x.round}&o=${x.original}`, note: x.label ? "Pick" : "Future pick",
        sub: x.player, team: { franchise: x.franchise, team: x.team } })) },
    };
  }
  const words = q.split(/\s+/).filter(Boolean);
  return Object.entries(searchIndex).map(([title, group]) => {
    // A pick whose words match whole ("1.09", not the "11.09" that contains it) comes first.
    const whole = (x) => { const t = new Set(`${x.name} | ${x.also}`.toLowerCase().split(/[\s|()]+/)); return words.filter((w) => t.has(w)).length; };
    const found = (group.words ? (/\d/.test(q) ? group.rows.filter((x) => words.every((w) => `${x.name} | ${x.also}`.toLowerCase().includes(w))).map((x) => ({ ...x, sort: whole(x) * 1e4 + x.sort })) : [])
      : group.rows.filter((x) => x.name.toLowerCase().includes(q) || x.also.toLowerCase().includes(q)))
      .map((x) => ({ ...x, via: group.words ? x.sub : x.name.toLowerCase().includes(q) ? null : x.also.split(" | ").find((n) => n.toLowerCase().includes(q)) }))
      .sort((x, y) => (y.active - x.active) || (y.sort - x.sort) || x.name.localeCompare(y.name));
    return { title, href: group.href, total: found.length, rows: found.slice(0, limit) };
  }).filter((g) => g.total);
}

// A result row in the reference-site manner: the matched part underlined, years at the right, bold when still active.
function searchRow(x, q) {
  const mark = (text) => {
    const at = text.toLowerCase().indexOf(q.trim().toLowerCase());
    return at < 0 ? text : [text.slice(0, at), h("u", {}, text.slice(at, at + q.trim().length)), text.slice(at + q.trim().length)];
  };
  return h("a", { href: x.href, class: x.active ? "row active" : "row" }, h("span", {}, mark(x.name), x.via ? h("i", {}, [" (", mark(x.via), ")"]) : null), h("small", {}, x.years));
}

// Header search over players, franchises and managers, in the manner of the big reference sites.
function searchBox() {
  const hits = h("div", { class: "hits", hidden: true });
  const input = h("input", { type: "search", placeholder: "Enter Player, Team, Manager, Pick", "aria-label": "Search" });
  const all = () => `search.html?q=${encodeURIComponent(input.value.trim())}`;
  let latest = 0;
  input.addEventListener("input", async () => {
    const q = input.value;
    const turn = ++latest;
    if (q.trim().length < 2) { hits.hidden = true; return; }
    const groups = await searchGroups(q);
    if (turn !== latest) return;
    hits.replaceChildren(
      ...(groups.length ? groups.flatMap((g) => [h("a", { class: "group", href: g.href }, g.title), ...g.rows.map((x) => searchRow(x, q))]) : [h("div", { class: "none" }, "No matches")]),
      h("a", { class: "all", href: all() }, `All matches for "${q.trim()}" (or press enter)`));
    hits.hidden = false;
  });
  // Enter goes straight to a lone match, otherwise to the full list.
  const go = async () => {
    const q = input.value;
    if (q.trim().length < 2) { input.focus(); return; }
    const rows = (await searchGroups(q, 2)).flatMap((g) => g.rows);
    location.href = rows.length === 1 ? rows[0].href : all();
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  document.addEventListener("click", (e) => { if (!e.target.closest("#search")) hits.hidden = true; });
  if (param("q") && location.pathname.endsWith("search.html")) input.value = param("q");
  return h("div", { id: "search" }, input, h("button", { type: "button", onclick: go }, "Search"), hits);
}

// Jump to a page chosen at random: mostly players, with team seasons, franchises, managers and drafts mixed in.
async function randomPage() {
  const pick = (list) => list[Math.floor(Math.random() * list.length)];
  const roll = Math.random();
  if (roll < 0.6) {
    const players = await load("players");
    location.href = `player.html?id=${pick(Object.keys(players).filter((id) => players[id].gs > 0))}`;
  } else if (roll < 0.8) location.href = `team.html?y=${pick(L.seasons).season}&id=${pick(L.franchises).id}`;
  else if (roll < 0.87) location.href = `franchise.html?id=${pick(L.franchises).id}`;
  else if (roll < 0.94) location.href = `manager.html?id=${pick(L.managers).id}`;
  else location.href = pick([...L.drafts.map((d) => `draft.html?y=${d.season}`), ...L.seasons.map((x) => `season.html?y=${x.season}`), "split_draft.html", "nicknames.html", "streaks.html"]);
}

function siteMenus() {
  const years = [...L.seasons].reverse();
  const link = (href, text) => h("a", { href }, text);
  return [
    ["League", [link("index.html", "League Index"), link("history.html", "History & Timeline"), link("constitution.html", "Constitution"), link("awards.html", "Awards & Honors"), link("alltime.html", "All-Time Teams"),
      link("hof.html", "Hall of Fame"),
      link("records.html", "Record Book"), link("streaks.html", "Streaks & Milestones"), link("h2h.html", "Head-to-Head & Rivalries"), link("ratings.html", "Elo Ratings"),
      link("champions.html", "Anatomy of a Champion"), link("construction.html", "Team Construction"), link("glossary.html", "Glossary")]],
    ["Seasons", [link("week.html", "This Week"), ...years.map((s) => link(`season.html?y=${s.season}`, `${s.season} Season`))]],
    ["Franchises", L.franchises.map((f) => link(`franchise.html?id=${f.id}`, f.name))],
    ["Managers", L.managers.map((m) => link(`manager.html?id=${m.id}`, m.name))],
    ["Players", [link("players.html", "Player Register"), link("leaders.html", "Career & Season Leaders"), link("awards.html", "MVP & All-iooi Teams"), link("alltime.html", "All-Time Teams"), link("hof.html", "Hall of Fame Monitor"),
      link("journeys.html", "Player Journeys"), link("aging.html", "Aging Curves & Rookie Classes"), link("nicknames.html", "Nickname Register")]],
    // The 2024 split draft sits in date order: after the 2024 rookie draft, before the 2023 startup draft.
    ["Drafts", [...[...L.drafts].reverse().flatMap((d) => [d.kind === "startup" ? link("split_draft.html", "2024 Split Draft") : null, link(`draft.html?y=${d.season}`, `${d.season} ${d.kind === "startup" ? "Startup" : "Rookie"} Draft`)]).filter(Boolean),
      link("drafts.html", "Draft Analysis"), link("picks.html", "Draft Pick Ledger"), link("lottery.html", "Draft Order & Lottery"), link("tankathon/", "Tankathon: Live Draft Order")]],
    ["Trades", [link("trades.html", "Trade Register"), link("trade_records.html", "Trade Records")]],
    ["Transactions", [link("waivers.html", "Waiver Wire"), ...years.map((s) => link(`transactions.html?y=${s.season}`, `${s.season} Transactions`))]],
    ["Leaders", [link("leaders.html", "Player Leaders"), link("records.html", "Record Book"), link("streaks.html", "Streaks & Milestones"), link("ratings.html", "Elo Ratings"), link("glossary.html", "Glossary")]],
  ];
}

// Hover opens a menu on a desktop. On a phone or tablet a tap opens it and a second tap closes it.
const TOUCH = "(max-width: 760px), (hover: none)";
function tapMenu(e) {
  if (!matchMedia(TOUCH).matches) return;
  const item = e.currentTarget.parentNode;
  for (const other of item.parentNode.children) if (other !== item) other.classList.remove("open");
  item.classList.toggle("open");
}

// "Sat, Oct 10, 2026, 1:42 PM CT" from the build's UTC stamp.
const stamp = (iso) => `${new Date(iso).toLocaleString("en-US", { timeZone: "America/Chicago", weekday: "short", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" })} CT`;

async function page(title, render) {
  const main = h("main");
  // Pages build content as nested arrays (section() returns several nodes); flatten on the way in.
  main.append = (...kids) => Element.prototype.append.call(main, ...kids.flat(Infinity).filter((k) => k != null && k !== false));
  document.body.append(main);
  if (!document.querySelector("link[rel=icon]")) document.head.append(h("link", { rel: "icon", href: "assets/favicon.png" }));
  let menus = [];
  tooltips();
  try {
    L = await load("league");
    // Franchises and managers are listed by name everywhere, not by franchise number.
    const byName = (x, y) => x.name.localeCompare(y.name, undefined, { sensitivity: "base" });
    L.franchises.sort(byName);
    L.managers.sort(byName);
    menus = siteMenus();
    document.body.prepend(
      h("div", { id: "srbar" }, h("b", {}, "iooi Reference"), h("span", {}, `Dynasty iooi, est. ${L.founded}`),
        h("a", { href: "week.html" }, "This Week"), h("a", { href: "constitution.html" }, "Constitution"), h("a", { href: "glossary.html" }, "Glossary"), h("a", { href: "tankathon/" }, "Tankathon")),
      h("div", { id: "masthead" },
        h("a", { href: "index.html" }, h("img", { src: "assets/logo.png", alt: "Dynasty iooi" })),
        h("a", { class: "wordmark", href: "index.html" }, "iooi ", h("span", {}, "Reference"),
          h("span", { class: "tag" }, `Dynasty iooi stats, history and records since ${L.founded}`)),
        searchBox()),
      h("div", { id: "nav" },
        h("button", { id: "navtoggle", type: "button", "aria-label": "Menu", onclick: (e) => e.currentTarget.parentNode.classList.toggle("open") }, "☰ Menu"),
        h("ul", {}, menus.map(([label, links]) => h("li", {}, h("span", { tabindex: "0", onclick: tapMenu }, label), h("div", { class: "menu" }, links))),
        h("li", { class: "random" }, h("a", { href: "#", "data-tip": "Open a page chosen at random", onclick: (e) => { e.preventDefault(); randomPage(); } }, "Random Page")))));
    const name = typeof title === "function" ? null : title;
    const built = await render(main);
    document.title = `${built ?? name} | iooi Reference`;
    // In-page menu of sections, placed above the first section bar.
    const heads = [...main.querySelectorAll("h2[id]")].filter((x) => !x.closest(".parchment"));
    if (heads.length >= 3) {
      heads[0].before(h("div", { id: "inner_nav" }, h("b", {}, "On this page:"), heads.map((x) => h("a", { href: `#${x.id}` }, x.textContent))));
    }
  } catch (err) {
    main.append(h("p", { class: "error" }, err.message));
    console.error(err);
  }
  document.body.append(h("footer", {},
    h("div", { class: "index" }, menus.filter(([label]) => !["Franchises", "Managers"].includes(label)).map(([label, links]) => h("div", {}, h("b", {}, label), links.map((a) => a.cloneNode(true))))),
    L?.updated ? h("p", { class: "updated" }, h("b", {}, "Data last updated: "), stamp(L.updated), L.current_week ? `, during week ${L.current_week} of the ${L.current_season} season.` : ".") : null,
    h("p", {}, "iooi Reference is a fan-made parody built for one fantasy football league. It is not affiliated with Sports Reference LLC or Sleeper. League data via Sleeper."),
    h("p", {}, ["All stats count games in iooi starting lineups unless a table says otherwise. Scoring rules have changed by amendment; see the league history for dates. Every stat is explained in the ", h("a", { href: "glossary.html" }, "glossary"), "."])));
  document.addEventListener("click", (e) => { if (!e.target.closest("#nav")) document.querySelectorAll("#nav .open, #nav.open").forEach((x) => x.classList.remove("open")); });
  if (location.hash) document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView();
}

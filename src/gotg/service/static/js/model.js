// Pure logic, no DOM: what the pygame picker's catalog.py/filters.py decide.

export const PER_PAGE = 48;

export const regionOf = (game) => game.id.split(".", 1)[0];

export const platforms = (games) => [...new Set(games.map((g) => g.platform))].sort();
export const regions = (games) => [...new Set(games.map(regionOf))].sort();

/** World releases ride through every region filter except region=world itself. */
export function filter(games, { platform = "", region = "", search = "" } = {}) {
  const q = search.trim().toLowerCase();
  return games.filter((g) =>
    (!platform || g.platform === platform) &&
    (!region || regionOf(g) === region || regionOf(g) === "world") &&
    (!q || g.title.toLowerCase().includes(q) || g.id.includes(q)));
}

export const page = (items, n) => items.slice(n * PER_PAGE, (n + 1) * PER_PAGE);
export const pageCount = (items) => Math.max(1, Math.ceil(items.length / PER_PAGE));

/** Environment names a game's saves can live in (its own, then its platform's); GET /saves says which exist. */
export const saveAttrs = (game) => [
  { attr: `env-${game.platform}-${game.id.replace(/\./g, "_")}`, scope: "this game" },
  { attr: `env-${game.platform}`, scope: `all ${game.platform} games without their own` },
];

/** Whether a local bundle's parent still matches the head. */
export const conflicts = (head, parent) => !!head && head.hash !== parent;

/** Distinct users across tokens and invites, with counts. */
export function users(tokens, invites) {
  const map = new Map();
  const row = (u) => map.get(u) ?? map.set(u, { user: u, tokens: 0, live: 0, invites: 0 }).get(u);
  for (const t of tokens) {
    const r = row(t.user || t.name);
    r.tokens++;
    if (!t.revoked_at) r.live++;
  }
  for (const i of invites) row(i.user || i.name).invites++;
  return [...map.values()].sort((a, b) => a.user.localeCompare(b.user));
}

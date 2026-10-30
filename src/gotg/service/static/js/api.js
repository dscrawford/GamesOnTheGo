/*
 * The one place the SPA talks to the network.
 *
 * ASSUMED CONTRACT (wt-oauth and wt-api are being built in parallel; change
 * only this file when they land):
 *
 *  Auth: a cookie session on the same origin. Every request is sent with
 *    credentials "same-origin". A 401 means "no session": the page navigates to
 *    GET /auth/login?next=<current /ui/ url>, which redirects to the provider
 *    and back. GET /auth/me answers {"name": str, "user"?: str, "admin"?: bool}.
 *    TODAY /auth/whoami is the only identity route ({"name","user"?}) and takes a
 *    bearer, so me() falls back to it, and a developer may paste a bearer token
 *    (setBearer) that is kept in sessionStorage and sent as Authorization.
 *    `admin` is absent on whoami; admin is then inferred when GET /admin/info
 *    answers 200.
 *    /admin/* is assumed to accept an admin cookie session as well as the admin bearer.
 *
 *  GET /catalog -> {"version":2,"games":[{id,platform,handler,title,
 *    files:[{name,size_bytes,sha256}]}], files_url?, files_urls?}.
 *    Region is the id's prefix before "." (usa, eur, jpn, world), as in the picker.
 *  GET /art/<platform>/<id> -> image bytes, or 404 (X-Gotg-Art: miss|absent).
 *    Fetched as a blob so it works with a cookie or a bearer.
 *
 *  Saves are per environment attr (^env-[a-z0-9][a-z0-9_-]*$): "env-<platform>"
 *    or "env-<platform>-<id with . as _>". There is NO list-my-saves route, so
 *    the UI derives candidate attrs from the catalog and probes them.
 *  GET /saves/<attr>/meta -> {generation, hash, size, device, written_at, bundle}|404
 *  GET /saves/<attr>/history -> {"generations":[{generation,hash,size,device,written_at,current}]}
 *  GET /saves/<attr>/gen/<n> and GET /saves/<attr> -> zstd bundle bytes,
 *    headers X-Gotg-Generation, X-Gotg-Hash
 *  PUT /saves/<attr>[?force=1] body=bundle, X-Gotg-Parent=<hash>, X-Gotg-Device
 *    -> 200 meta | 409 head meta (the conflict)
 *
 *  Admin (existing): GET /admin/info {public_url}; GET /admin/invites
 *    {invites:[{name,user,created_at,expires_at}]}; POST /admin/invites
 *    {name,user?,ttl_days?} -> {name,code}; GET /admin/tokens
 *    {tokens:[{name,user,display,created_at,expires_at,revoked_at,last_used_at}]};
 *    DELETE /admin/tokens/<name> -> {revoked}. Times are epoch seconds.
 *    Users are not a resource: they are the distinct `user` of tokens/invites.
 */

const BEARER_KEY = "gotg.bearer";

export class ApiError extends Error {
  constructor(status, message, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export const setBearer = (token) =>
  token ? sessionStorage.setItem(BEARER_KEY, token) : sessionStorage.removeItem(BEARER_KEY);

export const loginUrl = () => `/auth/login?next=${encodeURIComponent(location.pathname + location.hash)}`;

async function raw(path, { method = "GET", body, headers = {}, query } = {}) {
  const h = { ...headers };
  const bearer = sessionStorage.getItem(BEARER_KEY);
  if (bearer) h.Authorization = `Bearer ${bearer}`;
  let init = { method, headers: h, credentials: "same-origin" };
  if (body !== undefined) {
    if (typeof body === "string" || body instanceof Blob || body instanceof ArrayBuffer) init.body = body;
    else {
      init.body = JSON.stringify(body);
      h["Content-Type"] = "application/json";
    }
  }
  const url = query ? `${path}?${new URLSearchParams(query)}` : path;
  return fetch(url, init);
}

async function call(path, opts) {
  const res = await raw(path, opts);
  if (res.status === 401) {
    const err = new ApiError(401, "not signed in");
    err.login = true;
    throw err;
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* not json */ }
  if (!res.ok) throw new ApiError(res.status, (data && data.error) || text || res.statusText, data);
  return data;
}

export async function me() {
  try {
    return await call("/auth/me");
  } catch (e) {
    if (e.status !== 404) throw e;
    return call("/auth/whoami");
  }
}

export async function isAdmin(who) {
  if (who && typeof who.admin === "boolean") return who.admin;
  try { await call("/admin/info"); return true; } catch { return false; }
}

let catalogCache;
export const catalog = () => (catalogCache ??= call("/catalog").catch((e) => { catalogCache = undefined; throw e; }));

const artCache = new Map();
/** A blob: URL for a game's art, or null when there is none. */
export function art(platform, id) {
  const key = `${platform}/${id}`;
  if (!artCache.has(key)) {
    artCache.set(key, raw(`/art/${encodeURIComponent(platform)}/${encodeURIComponent(id)}`)
      .then(async (r) => (r.ok ? URL.createObjectURL(await r.blob()) : null))
      .catch(() => null));
  }
  return artCache.get(key);
}

export const saveMeta = (attr) => call(`/saves/${attr}/meta`).catch((e) => { if (e.status === 404) return null; throw e; });
export const saveHistory = (attr) => call(`/saves/${attr}/history`).then((r) => r.generations);

export async function saveBytes(attr, generation) {
  const res = await raw(generation ? `/saves/${attr}/gen/${generation}` : `/saves/${attr}`);
  if (!res.ok) throw new ApiError(res.status, "could not fetch the bundle");
  return res.blob();
}

/** PUT a bundle. Resolves {ok:true, meta} or {ok:false, head} on a 409. */
export async function savePut(attr, bundle, { parent = "", device = "web", force = false } = {}) {
  try {
    const meta = await call(`/saves/${attr}`, {
      method: "PUT", body: bundle, query: force ? { force: "1" } : undefined,
      headers: { "X-Gotg-Parent": parent, "X-Gotg-Device": device, "Content-Type": "application/zstd" },
    });
    return { ok: true, meta };
  } catch (e) {
    if (e.status === 409) return { ok: false, head: e.body };
    throw e;
  }
}

export const admin = {
  tokens: () => call("/admin/tokens").then((r) => r.tokens),
  invites: () => call("/admin/invites").then((r) => r.invites),
  info: () => call("/admin/info"),
  mint: (name, user, ttl_days) => call("/admin/invites", { method: "POST", body: { name, ...(user ? { user } : {}), ttl_days } }),
  revoke: (name) => call(`/admin/tokens/${encodeURIComponent(name)}`, { method: "DELETE" }),
};

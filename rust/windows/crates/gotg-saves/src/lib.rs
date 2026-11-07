//! GOTG's save sync, as the bash client does it (`src/client/lib/saves*.sh`),
//! for the Windows launcher: a Windows machine and a Linux one on the same
//! service share one environment's saves.
//!
//! The unit is the **environment** (`env-n64`), not the game. What moves is a
//! *generation*: one zstd tar of the files the manifest's `saves` globs match
//! under the environment's state directory, less its `excludes`, every path
//! relative to it and `/`-separated whatever the OS. The service keeps the
//! newest few and decides conflicts: a push carries the hash of the generation
//! this machine descends from, and is published or refused with the head.
//!
//! ```no_run
//! use gotg_saves::{Client, Env, Manifest, Mode, Saves};
//! # fn main() -> Result<(), gotg_saves::Error> {
//! let spec: serde_json::Value = serde_json::from_str(&std::fs::read_to_string("spec.json").unwrap()).unwrap();
//! let env = Env::from_spec(&spec["windows"], r"C:\Users\me\AppData\Local\gotg\env\env-n64")?;
//! let saves = Saves::new(r"C:\Users\me\AppData\Local\gotg\saves").with_client(Client {
//!     server: "https://gotg-api.example.net".into(),
//!     token: "…".into(),
//!     device: "daniel-laptop".into(),
//! });
//! saves.sync(&env, Mode::Both)?;   // before the launch: what a Linux launch does
//! // … the game runs …
//! saves.push(&env, false)?;        // after it
//! # Ok(()) }
//! ```
//!
//! # API
//!
//! - [`Saves::sync`]`(env, Mode)` -- one reconciliation, the launch's: push
//!   what is new here, pull what is new on the service, leave a conflict
//!   alone ([`Synced::Conflict`]). `Mode::PushOnly` never writes here.
//! - [`Saves::push`]`(env, force)` -- `gotg saves push [--force]`.
//! - [`Saves::pull`]`(env)` -- `gotg saves pull`: take the head, archiving
//!   what is here first.
//! - [`Saves::check`]`(env)` -- `gotg saves check`: where the two stand.
//! - [`Saves::keep`]`(env, Side)` -- settle a conflict.
//! - [`Saves::list`]`(env)` -- `gotg saves list`: the service's kept
//!   generations and this machine's archives, newest first.
//! - [`Saves::restore`]`(env, pick)` -- `gotg saves restore`, `pick` being a
//!   [`SaveEntry::id`]: `remote:<generation>` or `local:<archive>`.
//! - [`Saves::states`]`(env, game)` -- the save-state slots.
//!
//! # On disk
//!
//! `Saves::new(home)` keeps what the bash client keeps in
//! `$GOTG_STATE_DIR/saves`: `<home>/<env>.json`, the journal (what this
//! machine believes about the service), and `<home>/local/<env>/*.tar.zst`,
//! the archive every pull and restore makes of what was here before writing
//! over it (the last 3). On Windows that is `%LOCALAPPDATA%\gotg\saves`.
//!
//! # Interop with the bash client
//!
//! A bundle from either side is extracted by the other. The tar is GNU tar's
//! pax shape (ustar headers, a pax `path` record for long or non-ASCII names,
//! mtimes zeroed); the zstd stream is ruzstd's, so its bytes -- and with them
//! the generation hash -- differ from what `tar --zstd` makes of the same
//! files. "Did anything change here?" is therefore decided on a digest of the
//! *content* (paths and file bytes), kept in the journal beside the hashes,
//! so a pull of a Linux generation is not mistaken for local work.
//!
//! Globs are bash's: `saves` is pathname expansion under `globstar nullglob
//! dotglob` (`*` stays inside one component, `**` alone spans directories),
//! `excludes` and the received-bundle check are `[[ x == pattern ]]` (`*`
//! crosses `/`). Extglob forms are not supported.

mod bundle;
mod error;
mod glob;
mod remote;
mod states;
mod tar;
mod time;

use std::fs;
use std::path::PathBuf;

use serde_json::{Map, Value, json};

pub use error::Error;
pub use remote::{Client, Meta};
pub use states::{Slot, States};

/// `[[ text == pattern ]]`, bash's pattern comparison (exposed for tests and
/// for a launcher that wants to say why a file is or is not a save).
pub fn glob_matches(pattern: &str, text: &str) -> bool {
    glob::matches(pattern, text)
}

/// What an environment says its saves are: `saves.json` on Linux, the same
/// fields in `spec.json`'s `windows` section.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Manifest {
    pub saves: Vec<String>,
    pub excludes: Vec<String>,
    pub states: Option<States>,
}

impl Manifest {
    /// From `saves.json`, or a spec's `windows` object (extra keys ignored).
    pub fn from_json(v: &Value) -> Result<Manifest, Error> {
        let strings = |k: &str| -> Result<Vec<String>, Error> {
            match v.get(k) {
                None | Some(Value::Null) => Ok(Vec::new()),
                Some(Value::Array(a)) => a
                    .iter()
                    .map(|s| {
                        s.as_str()
                            .map(str::to_string)
                            .ok_or_else(|| Error::BadManifest(format!("{k} holds a non-string")))
                    })
                    .collect(),
                Some(_) => Err(Error::BadManifest(format!("{k} is not a list"))),
            }
        };
        if !v.is_object() {
            return Err(Error::BadManifest("not an object".into()));
        }
        Ok(Manifest {
            saves: strings("saves")?,
            excludes: strings("excludes")?,
            states: v.get("states").and_then(States::from_json),
        })
    }
}

/// One environment on this machine: its name, where its state lives, and
/// what its saves are.
#[derive(Debug, Clone)]
pub struct Env {
    pub name: String,
    pub state_dir: PathBuf,
    pub manifest: Manifest,
}

fn valid_env(name: &str) -> bool {
    let b = name.as_bytes();
    name.starts_with("env-")
        && b.len() > 4
        && (b[4].is_ascii_lowercase() || b[4].is_ascii_digit())
        && b[4..]
            .iter()
            .all(|&c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_' || c == b'-')
}

impl Env {
    pub fn new(name: &str, state_dir: impl Into<PathBuf>, manifest: Manifest) -> Result<Env, Error> {
        if !valid_env(name) {
            return Err(Error::BadEnv(name.to_string()));
        }
        Ok(Env {
            name: name.to_string(),
            state_dir: state_dir.into(),
            manifest,
        })
    }

    /// From a spec's `windows` section (its `name` and saves fields).
    pub fn from_spec(windows: &Value, state_dir: impl Into<PathBuf>) -> Result<Env, Error> {
        let name = windows["name"]
            .as_str()
            .ok_or_else(|| Error::BadManifest("no name".into()))?;
        Env::new(name, state_dir, Manifest::from_json(windows)?)
    }

    /// The files a push would send, relative and `/`-separated.
    pub fn members(&self) -> Vec<String> {
        bundle::members(&self.state_dir, &self.manifest)
    }
}

/// How far a [`Saves::sync`] may go.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Both,
    PushOnly,
}

/// Which side [`Saves::keep`] keeps.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    /// Push this machine's saves over the head (forced; the head stays kept).
    Here,
    /// Pull the head (what is here is archived first).
    Remote,
}

/// Where this machine and the service stand (`saves_check_one`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    /// The same save on both sides.
    Same,
    /// The service moved on and nothing here changed: a pull.
    RemoteNewer,
    /// This machine changed and the service did not: a push.
    HereNewer,
    /// Both moved on since they last agreed: somebody chooses.
    Conflict,
    /// Nothing pushed yet.
    HereOnly,
    /// Nothing here yet.
    RemoteOnly,
    /// No service, or it could not be asked.
    Offline,
}

impl State {
    /// The bash client's word for it (`gotg saves check --json`'s `state`).
    pub fn as_str(self) -> &'static str {
        match self {
            State::Same => "same",
            State::RemoteNewer => "remote-newer",
            State::HereNewer => "here-newer",
            State::Conflict => "conflict",
            State::HereOnly => "here-only",
            State::RemoteOnly => "remote-only",
            State::Offline => "offline",
        }
    }
}

/// This machine's side of a [`Check`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Here {
    pub device: String,
    /// When the saves here last changed (ISO, UTC); empty when never.
    pub updated: String,
    /// The generation this machine descends from; 0 for none.
    pub generation: u64,
    pub files: usize,
    pub size: u64,
    /// Changed since the last sync.
    pub changed: bool,
    pub present: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Check {
    pub env: String,
    pub state: State,
    pub here: Here,
    pub remote: Option<Meta>,
    /// Why the state is `Offline`, when the service was configured.
    pub offline_reason: Option<String>,
}

/// What a [`Saves::push`] did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Pushed {
    /// Published as this generation.
    Published(Meta),
    /// Refused: the service has moved on since this machine last synced, and
    /// this is its head. Both sides are intact; see [`Saves::keep`].
    Conflict(Meta),
    /// No saves here.
    NothingHere,
    /// Nothing changed since the last sync.
    Unchanged,
}

/// What a [`Saves::pull`] did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Pulled {
    /// Laid down this generation (what was here archived first).
    Fetched { generation: u64, hash: String },
    /// What is here already is that generation.
    AlreadyHave { generation: u64 },
    /// Nothing has been pushed.
    NothingRemote,
}

/// What a [`Saves::sync`] did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Synced {
    Pushed(Meta),
    Pulled {
        generation: u64,
    },
    /// Both moved on; nothing was done (the bash launch says the same).
    Conflict {
        head: Option<Meta>,
    },
    /// Nothing to do, and the state that said so.
    Nothing(State),
}

/// One save a person can go back to (`saves_list_one`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SaveEntry {
    /// What [`Saves::restore`] takes: `remote:<n>` or `local:<archive>`.
    pub id: String,
    /// `remote` or `local`.
    pub source: &'static str,
    pub generation: Option<u64>,
    pub hash: String,
    pub device: String,
    pub written_at: String,
    pub size: u64,
    /// It is what is on disk now.
    pub here: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SaveList {
    pub env: String,
    /// A game has this environment to itself (`env-n64-usa_dk64`, not `env-n64`).
    pub dedicated: bool,
    /// The service could not be asked; this machine's archives only.
    pub offline: bool,
    /// Newest first.
    pub saves: Vec<SaveEntry>,
}

/// What a [`Saves::restore`] did.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Restored {
    Done,
    AlreadyHere,
}

/// The save sync for one machine: where its journal and archives live, and
/// the service, when there is one.
#[derive(Debug, Clone)]
pub struct Saves {
    home: PathBuf,
    client: Option<Client>,
    keep: usize,
    max_bytes: u64,
}

const DEFAULT_MAX_BYTES: u64 = 64 * 1024 * 1024;

type Journal = Map<String, Value>;

impl Saves {
    /// `home` holds the journal and archives (`%LOCALAPPDATA%\gotg\saves`).
    pub fn new(home: impl Into<PathBuf>) -> Saves {
        Saves {
            home: home.into(),
            client: None,
            keep: 3,
            max_bytes: DEFAULT_MAX_BYTES,
        }
    }

    pub fn with_client(mut self, client: Client) -> Saves {
        self.client = Some(client);
        self
    }

    /// How many local archives survive per environment (bash's GOTG_SAVES_KEEP, 3).
    pub fn keep_archives(mut self, n: usize) -> Saves {
        self.keep = n;
        self
    }

    /// The cap on a bundle, both directions (bash's GOTG_SAVES_MAX_BYTES, 64 MiB).
    pub fn max_bytes(mut self, n: u64) -> Saves {
        self.max_bytes = n;
        self
    }

    // ---------------------------------------------------------- the journal

    fn journal_path(&self, env: &Env) -> PathBuf {
        self.home.join(format!("{}.json", env.name))
    }

    fn journal(&self, env: &Env) -> Journal {
        fs::read(self.journal_path(env))
            .ok()
            .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
            .and_then(|v| v.as_object().cloned())
            .unwrap_or_default()
    }

    fn journal_set(&self, env: &Env, patch: Value) -> Result<(), Error> {
        let mut j = self.journal(env);
        if let Value::Object(p) = patch {
            j.extend(p);
        }
        fs::create_dir_all(&self.home).map_err(|e| Error::io(self.home.display().to_string(), e))?;
        let path = self.journal_path(env);
        let tmp = path.with_extension("json.part");
        let text = serde_json::to_string(&Value::Object(j)).unwrap_or_default();
        fs::write(&tmp, text).map_err(|e| Error::io(tmp.display().to_string(), e))?;
        fs::rename(&tmp, &path).map_err(|e| Error::io(path.display().to_string(), e))
    }

    fn get<'a>(j: &'a Journal, key: &str) -> &'a str {
        j.get(key).and_then(Value::as_str).unwrap_or("")
    }

    fn base_generation(j: &Journal) -> u64 {
        match j.get("base_generation") {
            Some(Value::Number(n)) => n.as_u64().unwrap_or(0),
            Some(Value::String(s)) => s.parse().unwrap_or(0),
            _ => 0,
        }
    }

    // --------------------------------------------------------- this machine

    /// This machine as the metadata names it: the client's device name,
    /// printable and short, or a random id kept beside the journal.
    pub fn device_name(&self) -> String {
        let given: String = self
            .client
            .as_ref()
            .map(|c| {
                c.device
                    .chars()
                    .filter(|c| c.is_ascii_alphanumeric() || "._ -".contains(*c))
                    .take(32)
                    .collect()
            })
            .unwrap_or_default();
        if !given.is_empty() {
            return given;
        }
        let file = self.home.join("device");
        if let Ok(id) = fs::read_to_string(&file) {
            let id = id.trim().to_string();
            if !id.is_empty() {
                return id;
            }
        }
        use std::hash::{BuildHasher, Hasher};
        let mut h = std::collections::hash_map::RandomState::new().build_hasher();
        h.write_u128(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| d.as_nanos()),
        );
        h.write_u32(std::process::id());
        let id = format!("{:08x}", h.finish() as u32);
        let _ = fs::create_dir_all(&self.home);
        let _ = fs::write(&file, &id);
        id
    }

    fn build(&self, env: &Env) -> Result<Option<bundle::Built>, Error> {
        bundle::build(&env.state_dir, &env.manifest, self.max_bytes)
    }

    // Changed since the last sync: neither what this machine descends from
    // nor what it last pushed. The content digest is what makes a pulled
    // Linux generation, whose bytes we would not reproduce, count as unchanged.
    fn changed(j: &Journal, built: &bundle::Built) -> bool {
        let known = |k: &str| {
            let v = Self::get(j, k);
            !v.is_empty() && v == built.content
        };
        let hashed = |k: &str| {
            let v = Self::get(j, k);
            !v.is_empty() && v == built.hash
        };
        !(known("base_content") || known("pushed_content") || hashed("base_hash") || hashed("pushed_hash"))
    }

    fn updated(&self, env: &Env, j: &Journal, files: &[String]) -> String {
        let newest = files
            .iter()
            .filter_map(|f| fs::metadata(env.state_dir.join(f)).ok()?.modified().ok())
            .map(time::secs)
            .max()
            .unwrap_or(0);
        // A pulled file has the bundle's zeroed time: as old as its generation.
        if newest > 86_400 {
            time::iso(newest)
        } else {
            Self::get(j, "base_written_at").to_string()
        }
    }

    fn remote(&self) -> Result<remote::Remote<'_>, Error> {
        let client = self.client.as_ref().ok_or(Error::NoService)?;
        Ok(remote::Remote::new(client, self.device_name()))
    }

    // ---------------------------------------------------------------- check

    /// Where this machine and the service stand. Writes nothing; an
    /// unreachable service is `Offline`, not an error.
    pub fn check(&self, env: &Env) -> Result<Check, Error> {
        let built = self.build(env)?;
        let j = self.journal(env);
        let base_gen = Self::base_generation(&j);
        let changed = built.as_ref().is_some_and(|b| Self::changed(&j, b));
        let here = Here {
            device: self.device_name(),
            updated: self.updated(env, &j, built.as_ref().map_or(&[][..], |b| &b.files)),
            generation: base_gen,
            files: built.as_ref().map_or(0, |b| b.files.len()),
            size: built.as_ref().map_or(0, |b| b.bytes.len() as u64),
            changed,
            present: built.is_some(),
        };
        let mut check = Check {
            env: env.name.clone(),
            state: State::Offline,
            here,
            remote: None,
            offline_reason: None,
        };
        if self.client.is_none() {
            return Ok(check);
        }
        match self.remote()?.meta(&env.name) {
            Err(e) => check.offline_reason = Some(e.to_string()),
            Ok(None) => {
                check.state = if built.is_some() {
                    State::HereOnly
                } else {
                    State::Same
                }
            }
            Ok(Some(head)) => {
                check.state = match &built {
                    None => State::RemoteOnly,
                    Some(b) if b.hash == head.hash => State::Same,
                    Some(_) if head.generation > base_gen && changed => State::Conflict,
                    Some(_) if head.generation > base_gen => State::RemoteNewer,
                    Some(_) if changed => State::HereNewer,
                    Some(_) => State::Same,
                };
                check.remote = Some(head);
            }
        }
        Ok(check)
    }

    // ----------------------------------------------------------------- push

    /// Send what is here as the next generation. Unforced, a service that
    /// has moved on refuses it ([`Pushed::Conflict`]); forced, it is
    /// published over the head, which the service keeps until retention.
    pub fn push(&self, env: &Env, force: bool) -> Result<Pushed, Error> {
        let remote = self.remote()?;
        let Some(built) = self.build(env)? else {
            return Ok(Pushed::NothingHere);
        };
        let j = self.journal(env);
        if !force && !Self::changed(&j, &built) {
            return Ok(Pushed::Unchanged);
        }
        match remote.save(&env.name, &built.bytes, Self::get(&j, "base_hash"), force)? {
            remote::Put::Refused(head) => Ok(Pushed::Conflict(head)),
            remote::Put::Published(meta) => {
                self.journal_set(
                    env,
                    json!({
                        "base_generation": meta.generation, "base_hash": meta.hash,
                        "pushed_hash": built.hash, "base_written_at": meta.written_at,
                        "base_content": built.content, "pushed_content": built.content,
                    }),
                )?;
                Ok(Pushed::Published(meta))
            }
        }
    }

    // ----------------------------------------------------------------- pull

    /// Take the service's head. What is here is archived first; nothing here
    /// is removed for being absent from it.
    pub fn pull(&self, env: &Env) -> Result<Pulled, Error> {
        let remote = self.remote()?;
        let Some(head) = remote.meta(&env.name)? else {
            return Ok(Pulled::NothingRemote);
        };
        let current = self.build(env)?;
        if current.as_ref().is_some_and(|b| b.hash == head.hash) {
            self.journal_set(
                env,
                json!({"base_generation": head.generation, "base_hash": head.hash}),
            )?;
            return Ok(Pulled::AlreadyHave {
                generation: head.generation,
            });
        }
        // The head can move between the two requests; the journal records
        // what actually arrived.
        let (generation, hash, bytes) =
            remote
                .retrieve(&env.name, None, self.max_bytes)?
                .ok_or(Error::Http {
                    status: 404,
                    detail: "the head went away".into(),
                })?;
        let members = bundle::verify(&bytes, &hash, &env.manifest, self.unpack_cap())?;
        let content = bundle::content_digest(members.iter().map(|m| (m.path.as_str(), m.data.as_slice())));
        let journal = json!({
            "base_generation": generation, "base_hash": hash, "pushed_hash": hash,
            "pulled_at": time::now_iso(), "base_written_at": head.written_at,
            "base_content": content, "pushed_content": content,
        });
        if current.as_ref().is_some_and(|b| b.content == content) {
            self.journal_set(env, journal)?;
            return Ok(Pulled::AlreadyHave { generation });
        }
        self.snapshot(env, current.as_ref())?;
        bundle::extract(&env.state_dir, &members)?;
        self.journal_set(env, journal)?;
        Ok(Pulled::Fetched { generation, hash })
    }

    fn unpack_cap(&self) -> u64 {
        // The files are capped at max_bytes when bundled; headers on top.
        self.max_bytes.saturating_mul(2).saturating_add(1 << 20)
    }

    // ----------------------------------------------------------------- sync

    /// One reconciliation, as a Linux launch does it before the game:
    /// here-newer and here-only push, remote-newer and remote-only pull
    /// (`Mode::Both`), a conflict is reported and left for a person, the
    /// same or offline does nothing.
    pub fn sync(&self, env: &Env, mode: Mode) -> Result<Synced, Error> {
        let check = self.check(env)?;
        match check.state {
            State::HereNewer | State::HereOnly => match self.push(env, false)? {
                Pushed::Published(meta) => Ok(Synced::Pushed(meta)),
                Pushed::Conflict(head) => Ok(Synced::Conflict { head: Some(head) }),
                Pushed::NothingHere | Pushed::Unchanged => Ok(Synced::Nothing(check.state)),
            },
            State::RemoteNewer | State::RemoteOnly if mode == Mode::Both => match self.pull(env)? {
                Pulled::Fetched { generation, .. } | Pulled::AlreadyHave { generation } => {
                    Ok(Synced::Pulled { generation })
                }
                Pulled::NothingRemote => Ok(Synced::Nothing(check.state)),
            },
            State::Conflict => Ok(Synced::Conflict { head: check.remote }),
            state => Ok(Synced::Nothing(state)),
        }
    }

    /// Settle a conflict: this machine's saves (forced push) or the service's.
    pub fn keep(&self, env: &Env, side: Side) -> Result<Synced, Error> {
        match side {
            Side::Here => match self.push(env, true)? {
                Pushed::Published(meta) => Ok(Synced::Pushed(meta)),
                Pushed::Conflict(head) => Ok(Synced::Conflict { head: Some(head) }),
                Pushed::NothingHere => Ok(Synced::Nothing(State::RemoteOnly)),
                Pushed::Unchanged => Ok(Synced::Nothing(State::Same)),
            },
            Side::Remote => match self.pull(env)? {
                Pulled::Fetched { generation, .. } | Pulled::AlreadyHave { generation } => {
                    Ok(Synced::Pulled { generation })
                }
                Pulled::NothingRemote => Ok(Synced::Nothing(State::HereOnly)),
            },
        }
    }

    // ------------------------------------------------------------- archives

    fn local_dir(&self, env: &Env) -> PathBuf {
        self.home.join("local").join(&env.name)
    }

    // Archive what is here before writing over it; the last few are kept.
    fn snapshot(&self, env: &Env, current: Option<&bundle::Built>) -> Result<(), Error> {
        let Some(b) = current else { return Ok(()) };
        let dir = self.local_dir(env);
        fs::create_dir_all(&dir).map_err(|e| Error::io(dir.display().to_string(), e))?;
        let name = format!("{}-{}.tar.zst", time::now_iso().replace(':', ""), &b.hash[..12]);
        let path = dir.join(name);
        fs::write(&path, &b.bytes).map_err(|e| Error::io(path.display().to_string(), e))?;
        let mut names = self.archive_names(env);
        names.sort();
        let excess = names.len().saturating_sub(self.keep);
        for old in &names[..excess] {
            let _ = fs::remove_file(dir.join(old));
        }
        Ok(())
    }

    fn archive_names(&self, env: &Env) -> Vec<String> {
        let Ok(rd) = fs::read_dir(self.local_dir(env)) else {
            return Vec::new();
        };
        rd.flatten()
            .filter_map(|e| e.file_name().into_string().ok())
            .filter(|n| archive_time(n).is_some())
            .collect()
    }

    // ------------------------------------------------------- list / restore

    /// Every save to go back to: the service's kept generations and this
    /// machine's archives (less those the service also keeps), newest first.
    pub fn list(&self, env: &Env) -> Result<SaveList, Error> {
        let built = self.build(env).ok().flatten();
        let j = self.journal(env);
        let unchanged = built.as_ref().is_some_and(|b| !Self::changed(&j, b));
        let base_gen = Self::base_generation(&j);
        let here_hash = built.as_ref().map(|b| b.hash.clone()).unwrap_or_default();

        let mut offline = true;
        let mut saves = Vec::new();
        if self.client.is_some()
            && let Ok(history) = self.remote()?.history(&env.name)
        {
            offline = false;
            for m in history {
                saves.push(SaveEntry {
                    id: format!("remote:{}", m.generation),
                    source: "remote",
                    generation: Some(m.generation),
                    here: m.hash == here_hash || (unchanged && m.generation == base_gen),
                    hash: m.hash,
                    device: m.device,
                    written_at: m.written_at,
                    size: m.size,
                });
            }
        }
        let device = self.device_name();
        let mut own: Vec<SaveEntry> = Vec::new();
        let mut names = self.archive_names(env);
        names.sort();
        for name in names {
            let path = self.local_dir(env).join(&name);
            let Ok(bytes) = fs::read(&path) else { continue };
            let hash = bundle::sha256_hex(&bytes);
            if saves.iter().any(|s| s.hash == hash) || own.iter().any(|s| s.hash == hash) {
                continue;
            }
            own.push(SaveEntry {
                id: format!("local:{name}"),
                source: "local",
                generation: None,
                here: hash == here_hash,
                hash,
                device: device.clone(),
                written_at: archive_time(&name).unwrap_or_default(),
                size: bytes.len() as u64,
            });
        }
        saves.extend(own);
        saves.sort_by(|a, b| {
            (b.written_at.as_str(), b.generation.unwrap_or(0))
                .cmp(&(a.written_at.as_str(), a.generation.unwrap_or(0)))
        });
        Ok(SaveList {
            env: env.name.clone(),
            dedicated: dedicated(&env.name),
            offline,
            saves,
        })
    }

    /// Put one save back: `remote:<generation>` or `local:<archive>`. What is
    /// here is archived first, and save files made since that point go. The
    /// journal then stands on the service's head, so the next push makes the
    /// restored save the newest generation rather than a conflict.
    pub fn restore(&self, env: &Env, pick: &str) -> Result<Restored, Error> {
        // Fetched and verified before anything here is touched.
        let (bytes, hash) = if let Some(g) = pick.strip_prefix("remote:") {
            let generation: u64 = g
                .parse()
                .ok()
                .filter(|_| g.bytes().all(|b| b.is_ascii_digit()))
                .ok_or_else(|| Error::BadPick(pick.to_string()))?;
            let (_, hash, bytes) = self
                .remote()?
                .retrieve(&env.name, Some(generation), self.max_bytes)?
                .ok_or(Error::NotKept(generation))?;
            (bytes, hash)
        } else if let Some(name) = pick.strip_prefix("local:") {
            if archive_time(name).is_none() {
                return Err(Error::BadPick(pick.to_string()));
            }
            let bytes =
                fs::read(self.local_dir(env).join(name)).map_err(|_| Error::NoArchive(name.to_string()))?;
            let hash = bundle::sha256_hex(&bytes);
            (bytes, hash)
        } else {
            return Err(Error::BadPick(pick.to_string()));
        };
        let members = bundle::verify(&bytes, &hash, &env.manifest, self.unpack_cap())?;
        let content = bundle::content_digest(members.iter().map(|m| (m.path.as_str(), m.data.as_slice())));

        let current = self.build(env)?;
        if let Some(b) = &current
            && b.content == content
        {
            self.restore_journal(env, &hash, &content)?;
            return Ok(Restored::AlreadyHere);
        }
        self.snapshot(env, current.as_ref())?;
        if let Some(b) = &current {
            for gone in b.files.iter().filter(|f| !members.iter().any(|m| &m.path == *f)) {
                let _ = fs::remove_file(bundle::dest(&env.state_dir, gone));
            }
        }
        bundle::extract(&env.state_dir, &members)?;
        self.restore_journal(env, &hash, &content)?;
        Ok(Restored::Done)
    }

    // Offline the journal is left alone, which says the same to the next sync.
    fn restore_journal(&self, env: &Env, hash: &str, content: &str) -> Result<(), Error> {
        let Ok(remote) = self.remote() else { return Ok(()) };
        let Ok(Some(head)) = remote.meta(&env.name) else {
            return Ok(());
        };
        // Restoring the head itself is not a change on top of it.
        let base_content = if head.hash == hash { content } else { "" };
        self.journal_set(
            env,
            json!({
                "base_generation": head.generation, "base_hash": head.hash, "pushed_hash": "",
                "base_written_at": head.written_at, "base_content": base_content, "pushed_content": "",
            }),
        )
    }

    // --------------------------------------------------------------- states

    /// The manifest's save-state slots; `game` is the file being played
    /// (its stem fills `{game}`), None for any. Empty when it has none.
    pub fn states(&self, env: &Env, game: Option<&str>) -> Vec<Slot> {
        env.manifest
            .states
            .as_ref()
            .map_or_else(Vec::new, |s| states::list(&env.state_dir, s, game))
    }
}

/// `2026-08-01T120000Z-<12 hex>.tar.zst` -> `2026-08-01T12:00:00Z`.
fn archive_time(name: &str) -> Option<String> {
    let b = name.as_bytes();
    let digits = |r: std::ops::Range<usize>| r.into_iter().all(|i| b.get(i).is_some_and(u8::is_ascii_digit));
    let shape = b.len() == 18 + 1 + 12 + 8
        && digits(0..4)
        && b[4] == b'-'
        && digits(5..7)
        && b[7] == b'-'
        && digits(8..10)
        && b[10] == b'T'
        && digits(11..17)
        && b[17] == b'Z'
        && b[18] == b'-'
        && b[19..31]
            .iter()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(c))
        && &name[31..] == ".tar.zst";
    shape.then(|| {
        format!(
            "{}T{}:{}:{}Z",
            &name[..10],
            &name[11..13],
            &name[13..15],
            &name[15..17]
        )
    })
}

// Platform names have no dash, so a second one is the game's.
fn dedicated(env: &str) -> bool {
    env.strip_prefix("env-")
        .and_then(|rest| rest.split_once('-'))
        .is_some_and(|(platform, game)| {
            !platform.is_empty()
                && platform
                    .bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
                && game
                    .bytes()
                    .next()
                    .is_some_and(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn archive_names_parse() {
        assert_eq!(
            archive_time("2026-08-01T120000Z-0123456789ab.tar.zst").as_deref(),
            Some("2026-08-01T12:00:00Z")
        );
        assert!(archive_time("2026-08-01T120000Z-0123456789AB.tar.zst").is_none());
        assert!(archive_time("notes.txt").is_none());
    }

    #[test]
    fn env_names() {
        assert!(valid_env("env-n64"));
        assert!(valid_env("env-n64-usa_donkey_kong_64"));
        assert!(!valid_env("env-"));
        assert!(!valid_env("env-N64"));
        assert!(!valid_env("env-../x"));
        assert!(dedicated("env-n64-usa_donkey_kong_64"));
        assert!(!dedicated("env-n64"));
    }

    #[test]
    fn manifest_from_a_windows_section() {
        let v = json!({"name": "env-snes", "saves": ["saves/**"], "excludes": ["saves/*.bs[0-9]"],
            "states": {"files": "saves/*/{game}.bs{slot}", "slots": 3, "save": ["F5"], "load": ["F7"]},
            "exe": "emulator/ares.exe"});
        let env = Env::from_spec(&v, "/tmp/x").ok();
        let env = env.as_ref().map(|e| {
            (
                &e.name[..],
                e.manifest.saves.clone(),
                e.manifest.states.as_ref().map(|s| s.slots),
            )
        });
        assert_eq!(env, Some(("env-snes", vec!["saves/**".to_string()], Some(3))));
        assert!(Manifest::from_json(&json!({"saves": "x"})).is_err());
    }
}

//! The game's bytes, as download.sh fetches them: every member from
//! `<files host>/games/<platform>/<id>/<name>` with the Bearer token, staged
//! under `<games>/.gotg-partial/<id>/`, resumed with `Range` + `If-Range`
//! (the member's sha256 is its ETag), verified against the catalog's sha256,
//! and only then moved into `<games>/<platform>/`. An interrupted transfer
//! can never look like an installed game.
//!
//! What Windows cannot do is a recipe: those run Nix-built tools (unrar, a
//! disc converter) that only the Linux client has, so such a game is refused
//! by name rather than half installed.

use std::fs::{self, File, OpenOptions};
use std::io::{IsTerminal, Read, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use sha2::{Digest, Sha256};

use crate::api::{self, Api};
use crate::paths::Dirs;
use crate::render::under;
use crate::spec::{Game, Member, Spec};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Layout {
    /// One member, placed as it is named: `<games>/<platform>/<name>`.
    File(String),
    /// A tree fetched in place (a Wii U dump): `<games>/<platform>/<id>/`.
    Tree,
}

fn is_extra(m: &Member) -> bool {
    m.name.starts_with("extras/")
}

/// The member that is the game itself: not files[0], since "extras/" sorts
/// before most names.
pub fn base_member(game: &Game) -> Option<&Member> {
    game.files.iter().find(|m| !is_extra(m))
}

pub fn layout(game: &Game) -> Result<Layout, String> {
    match game.handler.as_str() {
        "single_file" | "no_intro_set" => {
            let m = base_member(game).ok_or_else(|| format!("{} lists no game file", game.id))?;
            Ok(Layout::File(m.name.clone()))
        }
        "wiiu_decrypted" => Ok(Layout::Tree),
        other => Err(format!(
            "{} is a '{other}' source, which needs the Linux client's recipe to unpack; \
             it has no Windows install yet",
            game.id
        )),
    }
}

/// Where the game is (or will be) on disk: `{target}`.
pub fn install_path(game: &Game, games: &Path) -> Result<PathBuf, String> {
    let dir = games.join(&game.platform);
    Ok(match layout(game)? {
        Layout::File(name) => under(&dir, &name),
        Layout::Tree => dir.join(&game.id),
    })
}

/// `{target}` as a launch sees it: the installed file, or for a zipped game
/// with `unpack`, the member of its unpacked directory matching the glob
/// (the glob itself when nothing is unpacked yet, so a dry run can say).
pub fn target_path(spec: &Spec, games: &Path) -> Result<PathBuf, String> {
    let path = install_path(&spec.game, games)?;
    match unpack_dir(spec, &path) {
        None => Ok(path),
        Some((dir, glob)) => Ok(find_member(&dir, glob).unwrap_or_else(|| dir.join(glob))),
    }
}

/// The directory a zipped game unpacks into, beside where the zip would go
/// and named for it, and the glob: only with `unpack` and a `.zip` member.
fn unpack_dir<'a>(spec: &'a Spec, path: &Path) -> Option<(PathBuf, &'a str)> {
    let u = spec.windows.unpack.as_ref()?;
    let name = path.file_name()?.to_string_lossy().into_owned();
    let stem = name
        .len()
        .checked_sub(4)
        .filter(|&i| name[i..].eq_ignore_ascii_case(".zip"))?;
    Some((path.with_file_name(&name[..stem]), u.target.as_str()))
}

fn find_member(dir: &Path, glob: &str) -> Option<PathBuf> {
    let mut found: Vec<PathBuf> = fs::read_dir(dir)
        .ok()?
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_ok_and(|t| t.is_file()))
        .filter(|e| glob_match(glob, &e.file_name().to_string_lossy()))
        .map(|e| e.path())
        .collect();
    found.sort();
    found.into_iter().next()
}

/// `*` and `?`, case-insensitively: a file-name glob, as the override's
/// `target` is.
pub fn glob_match(glob: &str, name: &str) -> bool {
    fn go(g: &[char], n: &[char]) -> bool {
        match g.split_first() {
            None => n.is_empty(),
            Some(('*', rest)) => (0..=n.len()).any(|i| go(rest, &n[i..])),
            Some(('?', rest)) => !n.is_empty() && go(rest, &n[1..]),
            Some((c, rest)) => n
                .split_first()
                .is_some_and(|(m, nr)| c.to_lowercase().eq(m.to_lowercase()) && go(rest, nr)),
        }
    }
    let g: Vec<char> = glob.chars().collect();
    let n: Vec<char> = name.chars().collect();
    go(&g, &n)
}

/// Every member of `zip_path` whose file name matches `glob`, flattened into
/// `dir` (built aside, then moved in). Names are the archive's: only the
/// last path segment is kept, and the whole is capped at `cap` bytes.
fn unzip_matching(zip_path: &Path, dir: &Path, glob: &str, cap: u64) -> Result<(), String> {
    let file = File::open(zip_path).map_err(|e| format!("{}: {e}", zip_path.display()))?;
    let mut zip =
        zip::ZipArchive::new(file).map_err(|e| format!("{}: not a zip: {e}", zip_path.display()))?;
    let part = dir.with_extension("part");
    let _ = fs::remove_dir_all(&part);
    fs::create_dir_all(&part).map_err(|e| format!("{}: {e}", part.display()))?;
    let result = (|| -> Result<usize, String> {
        let (mut count, mut total) = (0, 0u64);
        for i in 0..zip.len() {
            let mut entry = zip.by_index(i).map_err(|e| e.to_string())?;
            if !entry.is_file() {
                continue;
            }
            let raw = entry.name().map_err(|e| e.to_string())?.replace('\\', "/");
            let base = raw.rsplit('/').next().unwrap_or_default().to_string();
            if base.is_empty() || base == "." || base == ".." || !glob_match(glob, &base) {
                continue;
            }
            let out = part.join(&base);
            let mut w = File::create(&out).map_err(|e| format!("{}: {e}", out.display()))?;
            let n = std::io::copy(&mut (&mut entry).take(cap - total + 1), &mut w)
                .map_err(|e| format!("{}: {e}", out.display()))?;
            total += n;
            if total > cap {
                return Err(format!("{} inflates past {}", zip_path.display(), human(cap)));
            }
            count += 1;
        }
        Ok(count)
    })();
    match result {
        Ok(0) => {
            let _ = fs::remove_dir_all(&part);
            Err(format!("{} holds no file matching {glob}", zip_path.display()))
        }
        Ok(_) => {
            let _ = fs::remove_dir_all(dir);
            fs::rename(&part, dir).map_err(|e| format!("{} -> {}: {e}", part.display(), dir.display()))
        }
        Err(e) => {
            let _ = fs::remove_dir_all(&part);
            Err(e)
        }
    }
}

/// The members to fetch. A single-file game with updates or DLC beside it
/// gets the game alone: placing those is a recipe's job, and the game plays
/// without them (the Linux client's "legacy" install is the same file).
fn members(game: &Game) -> Vec<&Member> {
    match layout(game) {
        Ok(Layout::File(name)) => game.files.iter().filter(|m| m.name == name).collect(),
        _ => game.files.iter().collect(),
    }
}

pub fn sha256_file(path: &Path) -> Result<String, String> {
    let mut f = File::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = f.read(&mut buf).map_err(|e| format!("{}: {e}", path.display()))?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(h.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

/// The importer writes bare-hex digests; a mismatch deletes the file, which
/// is exactly the reset the next attempt needs.
fn verify(path: &Path, expected: Option<&str>) -> Result<(), String> {
    let Some(expected) = expected else { return Ok(()) };
    let actual = sha256_file(path)?;
    if actual != expected {
        let _ = fs::remove_file(path);
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        return Err(format!(
            "checksum mismatch for {name} -- deleted the download.\n     expected {expected}\n     got      {actual}"
        ));
    }
    Ok(())
}

/// One bar for the whole game, however many files: bytes already on disk
/// count as done from the start, the rate counts only this run's.
struct Meter {
    total: u64,
    done: u64,
    moved_before: u64,
    start: Instant,
    last: Option<Instant>,
    title: String,
    mode: MeterMode,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum MeterMode {
    Terminal,
    Lines,
    Quiet,
}

pub fn human(n: u64) -> String {
    let units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let mut v = n as f64;
    let mut i = 0;
    while v >= 1024.0 && i < units.len() - 1 {
        v /= 1024.0;
        i += 1;
    }
    if i == 0 {
        format!("{n} B")
    } else {
        format!("{v:.1} {}", units[i])
    }
}

impl Meter {
    fn new(total: u64, title: &str) -> Meter {
        let mode = if std::env::var("GOTG_PROGRESS_LINES").as_deref() == Ok("1") {
            MeterMode::Lines
        } else if std::io::stderr().is_terminal() {
            MeterMode::Terminal
        } else {
            MeterMode::Quiet
        };
        Meter {
            total,
            done: 0,
            moved_before: 0,
            start: Instant::now(),
            last: None,
            title: title.to_string(),
            mode,
        }
    }

    /// `size` bytes of the current file on disk, `moved` of them this run.
    fn tick(&mut self, size: u64, moved: u64, force: bool) {
        let now = Instant::now();
        if !force && self.last.is_some_and(|l| now - l < Duration::from_millis(500)) {
            return;
        }
        self.last = Some(now);
        let got = (self.done + size).min(if self.total > 0 { self.total } else { u64::MAX });
        let secs = (now - self.start).as_secs();
        let rate = (self.moved_before + moved).checked_div(secs).unwrap_or(0);
        match self.mode {
            MeterMode::Lines => {
                eprintln!("progress\t{got}\t{}\t{rate}\t{}", self.total, self.title);
            }
            MeterMode::Terminal => {
                let mut line = if let Some(pct) = (got * 100).checked_div(self.total) {
                    format!("{:3}%  {} of {}", pct.min(99), human(got), human(self.total))
                } else {
                    human(got)
                };
                if rate > 0 {
                    line.push_str(&format!("  {}/s", human(rate)));
                    if self.total > got {
                        let left = (self.total - got) / rate;
                        line.push_str(&format!("  eta {}m{:02}s", left / 60, left % 60));
                    }
                }
                eprint!("\r\x1b[K  {line}");
            }
            MeterMode::Quiet => {}
        }
    }

    fn finished(&mut self, size: u64, moved: u64) {
        self.done += size;
        self.moved_before += moved;
    }

    fn end(&self) {
        if self.mode == MeterMode::Terminal {
            eprint!("\r\x1b[K");
        }
    }
}

enum Outcome {
    Done,
    /// Keep the partial and try again (a reset, a timeout, a 5xx).
    Retry(String),
    /// The server will not resume these bytes; they must go.
    Restart(String),
}

/// One request for one member, resuming whatever is on disk.
fn fetch_once(
    agent: &ureq::Agent,
    token: &str,
    url: &str,
    out: &Path,
    etag: Option<&str>,
    meter: &mut Meter,
) -> Outcome {
    let have = fs::metadata(out).map(|m| m.len()).unwrap_or(0);
    let mut req = agent
        .get(url)
        .header("Authorization", api::bearer(token))
        .config()
        // A stalled connection is a reset like any other: resumed, not waited on.
        .timeout_per_read(Some(Duration::from_secs(120)))
        .build();
    if have > 0 {
        req = req.header("Range", format!("bytes={have}-"));
        if let Some(e) = etag {
            req = req.header("If-Range", format!("\"{e}\""));
        }
    }
    let resp = match req.call() {
        Ok(r) => r,
        Err(e) => return Outcome::Retry(e.to_string()),
    };
    let status = resp.status().as_u16();
    let append = match status {
        206 => {
            let start = resp
                .headers()
                .get("content-range")
                .and_then(|v| v.to_str().ok())
                .and_then(|v| v.strip_prefix("bytes "))
                .and_then(|v| v.split('-').next())
                .and_then(|v| v.parse::<u64>().ok());
            if start != Some(have) {
                return Outcome::Restart(format!("the server resumed {url} at the wrong offset"));
            }
            true
        }
        200 => false,
        // Past the end: what is here is the whole file or nothing; the
        // checksum says which.
        416 => return Outcome::Done,
        401 => return Outcome::Retry("the service refused the token -- run: gotg login".into()),
        _ => return Outcome::Retry(format!("{url} answered HTTP {status}")),
    };
    let file = OpenOptions::new()
        .create(true)
        .write(true)
        .append(append)
        .truncate(!append)
        .open(out);
    let mut file = match file {
        Ok(f) => f,
        Err(e) => return Outcome::Restart(format!("{}: {e}", out.display())),
    };
    let mut size = if append { have } else { 0 };
    let mut moved = 0u64;
    let mut body = resp.into_body().into_reader();
    let mut buf = vec![0u8; 1 << 16];
    loop {
        match body.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                if let Err(e) = file.write_all(&buf[..n]) {
                    return Outcome::Retry(format!("{}: {e}", out.display()));
                }
                size += n as u64;
                moved += n as u64;
                meter.tick(size, moved, false);
            }
            Err(e) => {
                let _ = file.flush();
                meter.finished(0, moved);
                return Outcome::Retry(e.to_string());
            }
        }
    }
    if let Err(e) = file.sync_all() {
        return Outcome::Retry(format!("{}: {e}", out.display()));
    }
    meter.finished(0, moved);
    Outcome::Done
}

/// Download the spec's game unless it is already here. Returns `{target}`.
pub fn ensure(spec: &Spec, dirs: &Dirs, api: Option<&Api>) -> Result<PathBuf, String> {
    let game = &spec.game;
    let dest = install_path(game, &dirs.games)?;
    let target = target_path(spec, &dirs.games)?;
    if target.exists() {
        return Ok(target);
    }
    let api = api.ok_or("not logged in -- run: gotg login")?;
    let _lock = crate::fsx::lock(
        &dirs.locks.join(format!("{}.lock", game.id)),
        Duration::from_secs(3600),
        &format!("downloading {}", game.id),
    )?;
    if target.exists() {
        return Ok(target);
    }
    let wanted = members(game);
    if game.files.iter().any(is_extra) && matches!(layout(game)?, Layout::File(_)) {
        crate::warn(&format!(
            "{} has updates or DLC beside it; on Windows only the game itself is installed",
            spec.title()
        ));
    }
    let total: u64 = wanted.iter().filter_map(|m| m.size_bytes).sum();
    crate::log(&format!(
        "fetching {} ({}, {} file(s))",
        spec.title(),
        human(total),
        wanted.len()
    ));

    let staged = dirs.partial.join(&game.id);
    fs::create_dir_all(&staged).map_err(|e| format!("{}: {e}", staged.display()))?;
    let agent = api::agent();
    let hosts = api::files_hosts(
        &spec.files_urls,
        &api.url,
        std::env::var("GOTG_FILES_URL").ok().as_deref(),
    );
    let mut host = api::pick_host(&agent, &api.token, &hosts);

    let mut meter = Meter::new(total, spec.title());
    // Bytes already staged by a run that stopped count from the start.
    let ahead: Vec<u64> = wanted
        .iter()
        .map(|m| {
            fs::metadata(under(&staged, &m.name))
                .map(|x| x.len())
                .unwrap_or(0)
        })
        .collect();
    meter.done = ahead.iter().sum();
    for (i, m) in wanted.iter().enumerate() {
        meter.done -= ahead[i];
        if wanted.len() > 1 {
            meter.title = format!("{} -- file {} of {}", spec.title(), i + 1, wanted.len());
        }
        let out = under(&staged, &m.name);
        if let Some(dir) = out.parent() {
            fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        }
        let mut fetched = false;
        for attempt in 1..=3 {
            let url = format!(
                "{host}/games/{}/{}/{}",
                game.platform,
                api::uri_segment(&game.id),
                api::uri_path(&m.name)
            );
            let mut outcome = fetch_once(&agent, &api.token, &url, &out, m.sha256.as_deref(), &mut meter);
            // curl's own --retry 3 inside an attempt, for the transient kind.
            for _ in 0..3 {
                if !matches!(outcome, Outcome::Retry(_)) {
                    break;
                }
                std::thread::sleep(Duration::from_secs(2));
                outcome = fetch_once(&agent, &api.token, &url, &out, m.sha256.as_deref(), &mut meter);
            }
            match outcome {
                Outcome::Done => match verify(&out, m.sha256.as_deref()) {
                    Ok(()) => {
                        fetched = true;
                        break;
                    }
                    Err(e) => {
                        meter.end();
                        crate::warn(&e);
                    }
                },
                Outcome::Restart(e) => {
                    let _ = fs::remove_file(&out);
                    meter.end();
                    crate::warn(&e);
                }
                Outcome::Retry(e) => {
                    meter.end();
                    crate::warn(&e);
                }
            }
            // The byte host can move between reads (a VPN-fronted host
            // changes port on reconnect): asked again before the next try.
            host = api::pick_host(&agent, &api.token, &hosts);
            if attempt < 3 {
                crate::warn(&format!(
                    "download interrupted -- retrying (attempt {} of 3)",
                    attempt + 1
                ));
            }
        }
        if !fetched {
            meter.end();
            return Err(format!(
                "download failed for {} (partials kept at {}; run again to resume)",
                game.id,
                staged.display()
            ));
        }
        let size = fs::metadata(&out).map(|x| x.len()).unwrap_or(0);
        meter.tick(size, 0, true);
        meter.finished(size, 0);
    }
    meter.end();
    if wanted.iter().any(|m| m.sha256.is_some()) {
        crate::log("checksum ok");
    }

    let platform_dir = dirs.games.join(&game.platform);
    fs::create_dir_all(&platform_dir).map_err(|e| format!("{}: {e}", platform_dir.display()))?;
    match layout(game)? {
        Layout::File(name) => {
            let from = under(&staged, &name);
            if let Some((dir, glob)) = unpack_dir(spec, &dest) {
                // A ROM a port reads bare, from the zip it is distributed in.
                unzip_matching(&from, &dir, glob, 8 << 30)?;
            } else {
                fs::rename(&from, &dest)
                    .map_err(|e| format!("{} -> {}: {e}", from.display(), dest.display()))?;
            }
            let _ = fs::remove_dir_all(&staged);
        }
        Layout::Tree => {
            let _ = fs::remove_dir_all(&dest);
            fs::rename(&staged, &dest)
                .map_err(|e| format!("{} -> {}: {e}", staged.display(), dest.display()))?;
        }
    }
    let target = target_path(spec, &dirs.games)?;
    crate::log(&format!("installed {}", target.display()));
    Ok(target)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::spec::tests::sample;

    fn game(handler: &str, files: &[&str]) -> Game {
        let mut v = sample();
        v["game"]["handler"] = handler.into();
        v["game"]["files"] = files
            .iter()
            .map(|n| serde_json::json!({"name": n, "size_bytes": 1}))
            .collect();
        Spec::parse(&v.to_string()).expect("spec").game
    }

    #[test]
    fn layouts_follow_download_sh() {
        let g = game("single_file", &["extras/upd/u.nsp", "Game (USA).z64"]);
        assert_eq!(layout(&g), Ok(Layout::File("Game (USA).z64".into())));
        assert_eq!(members(&g).len(), 1);
        assert_eq!(
            install_path(&g, Path::new("/g")).expect("path"),
            Path::new("/g").join("n64").join("Game (USA).z64")
        );
        let t = game("wiiu_decrypted", &["code/a.rpx", "meta/meta.xml"]);
        assert_eq!(
            install_path(&t, Path::new("/g")).expect("path"),
            Path::new("/g").join("n64").join("usa.donkey_kong_64")
        );
        assert_eq!(members(&t).len(), 2);
        let r = game("scene_archive", &["a.rar"]);
        assert!(layout(&r).expect_err("should fail").contains("recipe"));
    }

    #[test]
    fn globs() {
        assert!(glob_match("*.z64", "Zelda (USA).Z64"));
        assert!(glob_match("a?c", "abc"));
        assert!(!glob_match("*.z64", "readme.txt"));
        assert!(!glob_match("*.z64", "x.z64.bak"));
    }

    #[test]
    fn a_zipped_rom_is_unpacked_to_its_member() {
        let root = std::env::temp_dir().join(format!("gotg-unpack-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("mkdir");
        let zp = root.join("Zelda (USA).zip");
        let mut w = zip::ZipWriter::new(File::create(&zp).expect("create"));
        let o = zip::write::SimpleFileOptions::default();
        w.start_file("inner/Zelda (USA).z64", o).expect("start");
        w.write_all(b"rom").expect("write");
        w.start_file("readme.txt", o).expect("start");
        w.write_all(b"no").expect("write");
        w.finish().expect("finish");
        let dir = root.join("Zelda (USA)");
        unzip_matching(&zp, &dir, "*.z64", 1 << 20).expect("unzip");
        assert_eq!(fs::read(dir.join("Zelda (USA).z64")).expect("read"), b"rom");
        assert!(!dir.join("readme.txt").exists());
        assert_eq!(find_member(&dir, "*.z64"), Some(dir.join("Zelda (USA).z64")));
        assert!(unzip_matching(&zp, &root.join("none"), "*.n64", 1 << 20).is_err());
        assert!(unzip_matching(&zp, &root.join("big"), "*.z64", 1).is_err());
        assert!(!root.join("none").exists() && !root.join("big").exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn target_follows_unpack() {
        let mut v = sample();
        v["game"]["files"] = serde_json::json!([{"name": "Zelda (USA).zip", "size_bytes": 1}]);
        let plain = Spec::parse(&v.to_string()).expect("spec");
        let g = Path::new("/g");
        assert_eq!(
            target_path(&plain, g).expect("t"),
            g.join("n64").join("Zelda (USA).zip")
        );
        v["windows"]["unpack"] = serde_json::json!({"target": "*.z64"});
        let unpacked = Spec::parse(&v.to_string()).expect("spec");
        assert_eq!(
            target_path(&unpacked, g).expect("t"),
            g.join("n64").join("Zelda (USA)").join("*.z64")
        );
        v["game"]["files"] = serde_json::json!([{"name": "Smash (USA).z64", "size_bytes": 1}]);
        let bare = Spec::parse(&v.to_string()).expect("spec");
        assert_eq!(
            target_path(&bare, g).expect("t"),
            g.join("n64").join("Smash (USA).z64")
        );
    }

    #[test]
    fn sizes() {
        assert_eq!(human(512), "512 B");
        assert_eq!(human(1536), "1.5 KiB");
    }
}

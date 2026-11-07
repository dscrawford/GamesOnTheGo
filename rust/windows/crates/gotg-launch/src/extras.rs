//! Console keys and firmware: keys.sh and firmware.sh. Hand-placed on the
//! server beside the games (`/files/<platform>/<name>`), never in a bundle --
//! they belong to a console. Neither is ever fatal: the emulator says more
//! about a missing key than this can.

use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::api::{self, Api};
use crate::fsx;
use crate::paths::Dirs;
use crate::render::under;
use crate::spec::Spec;

/// One file of `/files/<platform>/`, from whichever byte host answers first,
/// into `dest` (removed again if none did). `cap` bounds the bytes written.
/// Where `/files` comes from: the agent, the token and the byte hosts.
pub struct Source<'a> {
    pub agent: &'a ureq::Agent,
    pub token: &'a str,
    pub hosts: &'a [String],
}

pub fn fetch_file(
    src: &Source,
    platform: &str,
    file: &str,
    dest: &Path,
    seconds: u64,
    cap: Option<u64>,
) -> bool {
    for host in src.hosts {
        let url = format!("{host}/files/{platform}/{}", api::uri_segment(file));
        let ok = (|| -> Option<()> {
            let resp = api::get(src.agent, src.token, &url, Duration::from_secs(seconds)).ok()?;
            if !resp.status().is_success() {
                return None;
            }
            let mut out = File::create(dest).ok()?;
            let mut body = resp.into_body().into_reader();
            let mut buf = vec![0u8; 1 << 16];
            let mut total = 0u64;
            loop {
                let n = body.read(&mut buf).ok()?;
                if n == 0 {
                    break;
                }
                total += n as u64;
                if cap.is_some_and(|c| total > c) {
                    return None;
                }
                out.write_all(&buf[..n]).ok()?;
            }
            out.sync_all().ok()?;
            (total > 0).then_some(())
        })()
        .is_some();
        if ok {
            return true;
        }
        let _ = fs::remove_file(dest);
    }
    false
}

fn env_secs(name: &str, default: u64) -> u64 {
    std::env::var(name)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

pub fn ensure_keys(spec: &Spec, state: &Path, api: Option<&Api>) {
    let Some(keys) = &spec.windows.keys else { return };
    let platform = keys.platform.as_deref().unwrap_or(&spec.game.platform);
    let dir = under(state, &keys.into);
    // Nothing to do is the common case: no request unless something is absent.
    let present = |f: &str| fs::metadata(dir.join(f)).is_ok_and(|m| m.len() > 0);
    if keys.files.is_empty() || keys.files.iter().all(|f| present(f)) {
        return;
    }
    if let Err(e) = fs::create_dir_all(&dir) {
        crate::warn(&format!("{}: {e}", dir.display()));
        return;
    }
    let Some(api) = api else {
        crate::warn(&format!(
            "no service configured for {platform} keys -- run: gotg login"
        ));
        return;
    };
    let agent = api::agent();
    let hosts = api::files_hosts(
        &spec.files_urls,
        &api.url,
        std::env::var("GOTG_FILES_URL").ok().as_deref(),
    );
    let mut missing = 0;
    for file in &keys.files {
        if present(file) {
            continue;
        }
        if file.contains('/') || file.contains("..") || crate::validate::filename(file).is_err() {
            crate::warn(&format!("refusing key file name: {file}"));
            missing += 1;
            continue;
        }
        let dest = dir.join(file);
        let part = dir.join(format!("{file}.part"));
        if fetch_file(
            &Source {
                agent: &agent,
                token: &api.token,
                hosts: &hosts,
            },
            platform,
            file,
            &part,
            env_secs("GOTG_KEYS_FETCH_SECONDS", 900),
            None,
        ) {
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let _ = fs::set_permissions(&part, fs::Permissions::from_mode(0o600));
            }
            match fs::rename(&part, &dest) {
                Ok(()) => crate::log(&format!("fetched {file}")),
                Err(e) => {
                    missing += 1;
                    crate::warn(&format!("{}: {e}", dest.display()));
                }
            }
        } else {
            let _ = fs::remove_file(&part);
            missing += 1;
            crate::warn(&format!("no {file} on the server at /Games/{platform}/{file}"));
        }
    }
    if missing > 0 {
        crate::warn(&format!("{platform} needs its keys in /Games/{platform}"));
    }
}

fn firmware_cap() -> u64 {
    env_secs("GOTG_FIRMWARE_MAX_BYTES", 2 << 30)
}

/// A firmware zip as the emulator's own installer leaves it:
/// `registered/<name>.nca/00`, `.cnmt` dropped from the name. Takes a zip of
/// flat NCAs or one already shaped that way; a bomb or an NCA-less zip is
/// refused before `reg` is touched.
pub fn unpack(zip_path: &Path, reg: &Path, cap: u64) -> Result<(), String> {
    let file = File::open(zip_path).map_err(|e| format!("{}: {e}", zip_path.display()))?;
    let mut zip = zip::ZipArchive::new(file).map_err(|e| format!("not a zip: {e}"))?;
    let mut declared = 0u64;
    for i in 0..zip.len() {
        declared += zip.by_index(i).map_err(|e| e.to_string())?.size();
    }
    if declared > cap {
        return Err(format!("the firmware zip inflates past {cap} bytes"));
    }
    let part = reg.with_extension("part");
    let _ = fs::remove_dir_all(&part);
    fs::create_dir_all(&part).map_err(|e| format!("{}: {e}", part.display()))?;
    let result = (|| -> Result<usize, String> {
        let mut count = 0;
        for i in 0..zip.len() {
            let mut entry = zip.by_index(i).map_err(|e| e.to_string())?;
            if !entry.is_file() {
                continue;
            }
            let raw = entry.name().map_err(|e| e.to_string())?.replace('\\', "/");
            let segs: Vec<&str> = raw.split('/').filter(|s| !s.is_empty()).collect();
            let name = match segs.as_slice() {
                [.., parent, "00"] if parent.ends_with(".nca") => *parent,
                [.., last] if last.ends_with(".nca") => *last,
                _ => continue,
            };
            let name = name.replace(".cnmt", "");
            crate::validate::filename(&name)?;
            let dir = part.join(&name);
            fs::create_dir_all(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
            let mut out = File::create(dir.join("00")).map_err(|e| e.to_string())?;
            // The declared size was checked; the actual one is bounded too.
            let copied =
                std::io::copy(&mut (&mut entry).take(cap + 1), &mut out).map_err(|e| e.to_string())?;
            if copied > cap {
                return Err("a firmware entry inflates past its cap".into());
            }
            count += 1;
        }
        Ok(count)
    })();
    match result {
        Ok(n) if n > 0 => {
            let _ = fs::remove_dir_all(reg);
            fs::rename(&part, reg).map_err(|e| format!("{}: {e}", reg.display()))
        }
        Ok(_) => {
            let _ = fs::remove_dir_all(&part);
            Err("the zip holds no NCAs; it is not firmware".into())
        }
        Err(e) => {
            let _ = fs::remove_dir_all(&part);
            Err(e)
        }
    }
}

/// `src`'s tree at `dst`, hardlinked where the filesystem allows so every
/// environment shares one copy; the emulator replaces rather than edits NCAs.
fn link_tree(src: &Path, dst: &Path) -> Result<(), String> {
    let part = dst.with_extension("part");
    let _ = fs::remove_dir_all(&part);
    fn walk(src: &Path, dst: &Path) -> Result<(), String> {
        fs::create_dir_all(dst).map_err(|e| format!("{}: {e}", dst.display()))?;
        for entry in fs::read_dir(src).map_err(|e| format!("{}: {e}", src.display()))? {
            let entry = entry.map_err(|e| e.to_string())?;
            let (from, to) = (entry.path(), dst.join(entry.file_name()));
            if entry.file_type().map_err(|e| e.to_string())?.is_dir() {
                walk(&from, &to)?;
            } else if fs::hard_link(&from, &to).is_err() {
                fsx::copy_file(&from, &to)?;
            }
        }
        Ok(())
    }
    if let Err(e) = walk(src, &part) {
        let _ = fs::remove_dir_all(&part);
        return Err(e);
    }
    let _ = fs::remove_dir_all(dst);
    fs::rename(&part, dst).map_err(|e| format!("{}: {e}", dst.display()))
}

pub fn ensure_firmware(spec: &Spec, dirs: &Dirs, state: &Path, api: Option<&Api>) {
    let Some(fw) = &spec.windows.firmware else { return };
    let dest = under(state, &fw.into);
    if fsx::populated(&dest) {
        return;
    }
    let platform = &spec.game.platform;
    let cache = dirs.firmware.join(platform);
    let result = (|| -> Result<(), String> {
        // One launch fills the cache; a racer waits and finds it done.
        let _lock = fsx::lock(
            &cache.with_extension("lock"),
            Duration::from_secs(3600),
            "installing firmware",
        )?;
        if !fsx::populated(&cache) {
            let adopted = fs::read_dir(&dirs.env_root)
                .into_iter()
                .flatten()
                .flatten()
                .map(|e| under(&e.path(), &fw.into))
                .find(|d| d != &dest && fsx::populated(d));
            match adopted {
                Some(dir) => link_tree(&dir, &cache)?,
                None => {
                    let api = api.ok_or("not logged in")?;
                    let agent = api::agent();
                    let hosts = api::files_hosts(
                        &spec.files_urls,
                        &api.url,
                        std::env::var("GOTG_FILES_URL").ok().as_deref(),
                    );
                    let zip: PathBuf = cache.with_extension("zip");
                    if let Some(d) = zip.parent() {
                        fs::create_dir_all(d).map_err(|e| e.to_string())?;
                    }
                    if !fetch_file(
                        &Source {
                            agent: &agent,
                            token: &api.token,
                            hosts: &hosts,
                        },
                        platform,
                        &fw.file,
                        &zip,
                        env_secs("GOTG_FIRMWARE_FETCH_SECONDS", 900),
                        Some(firmware_cap()),
                    ) {
                        return Err("not on the server".into());
                    }
                    crate::log(&format!("fetched {}", fw.file));
                    let r = unpack(&zip, &cache, firmware_cap());
                    let _ = fs::remove_file(&zip);
                    r?;
                }
            }
        }
        link_tree(&cache, &dest)
    })();
    if let Err(e) = result {
        if fsx::populated(&cache) {
            crate::warn(&format!(
                "could not place firmware ({e}) -- the emulator will ask instead"
            ));
        } else {
            crate::warn(&format!(
                "no {platform} firmware here or on the server at /Games/{platform}/{} ({e})",
                fw.file
            ));
            crate::warn("the emulator will offer to install some on the first launch");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unpacks_both_shapes_and_refuses_bombs() {
        let dir = std::env::temp_dir().join(format!("gotg-fw-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("mkdir");
        let zp = dir.join("fw.zip");
        {
            let mut w = zip::ZipWriter::new(File::create(&zp).expect("create"));
            let o =
                zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
            w.start_file("a.cnmt.nca", o).expect("start");
            w.write_all(b"AAAA").expect("write");
            w.start_file("sub/b.nca/00", o).expect("start");
            w.write_all(b"BB").expect("write");
            w.start_file("readme.txt", o).expect("start");
            w.write_all(b"x").expect("write");
            w.finish().expect("finish");
        }
        let reg = dir.join("registered");
        unpack(&zp, &reg, 1 << 20).expect("unpack");
        assert_eq!(fs::read(reg.join("a.nca").join("00")).expect("a"), b"AAAA");
        assert_eq!(fs::read(reg.join("b.nca").join("00")).expect("b"), b"BB");
        assert!(!reg.join("readme.txt").exists());
        assert!(
            unpack(&zp, &dir.join("r2"), 3)
                .expect_err("should fail")
                .contains("inflates")
        );
        assert!(!dir.join("r2").exists());
        let linked = dir.join("env").join("registered");
        link_tree(&reg, &linked).expect("link");
        assert_eq!(fs::read(linked.join("a.nca").join("00")).expect("a"), b"AAAA");
        let _ = fs::remove_dir_all(&dir);
    }
}

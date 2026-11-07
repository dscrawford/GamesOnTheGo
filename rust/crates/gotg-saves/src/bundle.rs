//! A bundle: the manifest's files under the state directory, as one zstd tar
//! of `/`-separated paths relative to it (`saves.sh`).

use std::collections::BTreeMap;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::tar::{self, Member};
use crate::{Error, Manifest, glob};

pub fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

/// The files a bundle would hold: the manifest's globs, regular files only
/// (no symlinks), less its excludes -- an exclude matched as tar matches
/// one, on any trailing run of path components. Byte-sorted, unique.
pub fn members(state: &Path, manifest: &Manifest) -> Vec<String> {
    if manifest.saves.is_empty() || !state.is_dir() {
        return Vec::new();
    }
    let mut all: Vec<String> = manifest
        .saves
        .iter()
        .flat_map(|g| glob::expand(state, g, true))
        .filter(|m| {
            fs::symlink_metadata(state.join(m))
                .map(|md| md.is_file())
                .unwrap_or(false)
        })
        .collect();
    all.sort();
    all.dedup();
    all.retain(|m| !excluded(&manifest.excludes, m));
    all
}

pub fn excluded(excludes: &[String], member: &str) -> bool {
    excludes
        .iter()
        .any(|p| glob::matches(p, member) || glob::matches(&format!("*/{p}"), member))
}

/// The content's identity, independent of how it was compressed: what the
/// bash client's bundle hash means, but equal for its bytes and for ours.
pub fn content_digest<'a>(files: impl IntoIterator<Item = (&'a str, &'a [u8])>) -> String {
    let sorted: BTreeMap<&str, &[u8]> = files.into_iter().collect();
    let mut h = Sha256::new();
    for (path, data) in sorted {
        h.update(path.as_bytes());
        h.update([0]);
        h.update(data.len().to_string().as_bytes());
        h.update([0]);
        h.update(sha256_hex(data).as_bytes());
        h.update(b"\n");
    }
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// A bundle built from what is here.
#[derive(Debug, Clone)]
pub struct Built {
    pub bytes: Vec<u8>,
    pub hash: String,
    pub content: String,
    pub files: Vec<String>,
}

#[cfg(unix)]
fn mode_of(md: &fs::Metadata) -> u32 {
    use std::os::unix::fs::PermissionsExt;
    md.permissions().mode() & 0o7777
}

#[cfg(not(unix))]
fn mode_of(_: &fs::Metadata) -> u32 {
    0o644
}

/// None when there is nothing to bundle. Deterministic: the same files make
/// the same bytes, so "has anything changed?" is a hash comparison.
pub fn build(state: &Path, manifest: &Manifest, max_bytes: u64) -> Result<Option<Built>, Error> {
    let files = members(state, manifest);
    if files.is_empty() {
        return Ok(None);
    }
    let mut sizes = Vec::new();
    for f in &files {
        let md = fs::metadata(state.join(f)).map_err(|e| Error::io(f.clone(), e))?;
        sizes.push((md.len(), f.clone()));
    }
    let total: u64 = sizes.iter().map(|(s, _)| s).sum();
    if total > max_bytes {
        sizes.sort_by_key(|a| std::cmp::Reverse(a.0));
        sizes.truncate(5);
        return Err(Error::TooLarge {
            total,
            max: max_bytes,
            largest: sizes,
        });
    }
    let mut tar_members = Vec::new();
    for f in &files {
        let path = state.join(f);
        let md = fs::metadata(&path).map_err(|e| Error::io(f.clone(), e))?;
        let data = fs::read(&path).map_err(|e| Error::io(f.clone(), e))?;
        tar_members.push(Member {
            path: f.clone(),
            mode: mode_of(&md),
            data,
        });
    }
    let content = content_digest(tar_members.iter().map(|m| (m.path.as_str(), m.data.as_slice())));
    let raw = tar::write(&tar_members);
    let bytes = ruzstd::encoding::compress_to_vec(&raw[..], ruzstd::encoding::CompressionLevel::Fastest);
    let hash = sha256_hex(&bytes);
    Ok(Some(Built {
        bytes,
        hash,
        content,
        files,
    }))
}

/// Every frame of a zstd stream, refusing to grow past `cap`.
pub fn decompress(bytes: &[u8], cap: u64) -> Result<Vec<u8>, Error> {
    let mut input = bytes;
    let mut out = Vec::new();
    while !input.is_empty() {
        let mut dec = ruzstd::decoding::StreamingDecoder::new(&mut input)
            .map_err(|e| Error::bad(format!("not a zstd stream: {e}")))?;
        let room = (cap + 1).saturating_sub(out.len() as u64);
        (&mut dec)
            .take(room)
            .read_to_end(&mut out)
            .map_err(|e| Error::bad(format!("the zstd stream is broken: {e}")))?;
        if out.len() as u64 > cap {
            return Err(Error::bad(format!("it unpacks to more than {cap} bytes")));
        }
    }
    Ok(out)
}

const WINDOWS_RESERVED: &[&str] = &[
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
];

/// Whether a member's path can only land under the state directory on any
/// OS. On Windows `\` and `C:` are path syntax, so they are refused anywhere.
pub fn safe_path(name: &str) -> Result<(), String> {
    if name.is_empty() || name.starts_with('/') {
        return Err(format!("an absolute path ({name})"));
    }
    for comp in name.split('/') {
        if comp.is_empty() || comp == "." || comp == ".." {
            return Err(format!("a path that climbs out of it or is not plain ({name})"));
        }
        if comp
            .chars()
            .any(|c| c.is_control() || matches!(c, '\\' | ':' | '<' | '>' | '"' | '|' | '?' | '*'))
        {
            return Err(format!("a name Windows cannot hold ({name})"));
        }
        if comp.ends_with('.') || comp.ends_with(' ') {
            return Err(format!("a name Windows cannot hold ({name})"));
        }
        let stem = comp.split('.').next().unwrap_or("").to_ascii_uppercase();
        if WINDOWS_RESERVED.contains(&stem.as_str()) {
            return Err(format!("a name Windows reserves ({name})"));
        }
    }
    Ok(())
}

/// Everything about a received bundle that must be true before any of it is
/// written: its hash, that it is plain files only, and that each one is
/// something this environment declares as a save, under its state directory.
pub fn verify(bytes: &[u8], expect_hash: &str, manifest: &Manifest, cap: u64) -> Result<Vec<Member>, Error> {
    let got = sha256_hex(bytes);
    if got != expect_hash {
        return Err(Error::HashMismatch {
            expected: expect_hash.to_string(),
            got,
        });
    }
    let raw = decompress(bytes, cap)?;
    let members = tar::read(&raw)?;
    for m in &members {
        safe_path(&m.path).map_err(|why| Error::bad(format!("it holds {why}")))?;
        if !manifest.saves.iter().any(|g| glob::matches(g, &m.path)) {
            return Err(Error::bad(format!(
                "it holds '{}', which is not something this environment declares as a save",
                m.path
            )));
        }
    }
    Ok(members)
}

pub fn dest(state: &Path, member: &str) -> PathBuf {
    let mut p = state.to_path_buf();
    p.extend(member.split('/'));
    p
}

/// Lay verified members down under `state`, each replacing what is there.
/// Nothing is removed for being absent from the bundle: keep both.
pub fn extract(state: &Path, members: &[Member]) -> Result<(), Error> {
    let mut last: BTreeMap<&str, &Member> = BTreeMap::new();
    for m in members {
        last.insert(&m.path, m);
    }
    for (path, m) in last {
        let to = dest(state, path);
        if let Some(parent) = to.parent() {
            fs::create_dir_all(parent).map_err(|e| Error::io(parent.display().to_string(), e))?;
        }
        let mut part = to.clone().into_os_string();
        part.push(".gotg-part");
        let part = PathBuf::from(part);
        fs::write(&part, &m.data).map_err(|e| Error::io(part.display().to_string(), e))?;
        fs::rename(&part, &to).map_err(|e| {
            let _ = fs::remove_file(&part);
            Error::io(to.display().to_string(), e)
        })?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paths_windows_could_misread_are_refused() {
        assert!(safe_path("saves/a.srm").is_ok());
        assert!(safe_path("saves/Nintendo 64/Zelda (USA).ram").is_ok());
        for bad in [
            "/etc/x",
            "saves/../x",
            "saves//x",
            "./saves/x",
            "saves\\..\\x",
            "C:/x",
            "saves/con.txt",
            "saves/a.",
            "saves/a?",
            "saves/",
        ] {
            assert!(safe_path(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn excludes_match_any_trailing_run() {
        let ex = vec!["*.bs[0-9]".to_string(), "saves/tex/**".to_string()];
        assert!(excluded(&ex, "saves/x/Game.bs1"));
        assert!(excluded(&ex, "data/saves/tex/a/b"));
        assert!(!excluded(&ex, "saves/Game.srm"));
    }

    #[test]
    fn digest_ignores_order() {
        let a = content_digest([("b", &b"2"[..]), ("a", &b"1"[..])]);
        let b = content_digest([("a", &b"1"[..]), ("b", &b"2"[..])]);
        assert_eq!(a, b);
        assert_ne!(a, content_digest([("a", &b"1"[..])]));
    }
}

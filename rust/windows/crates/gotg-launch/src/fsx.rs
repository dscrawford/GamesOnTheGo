//! File helpers the rest leans on: atomic replace, a secret's permissions, a
//! tree copy, and the per-game lock.

use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

fn sibling(path: &Path, suffix: &str) -> PathBuf {
    let mut name = path.file_name().unwrap_or_default().to_os_string();
    name.push(suffix);
    path.with_file_name(name)
}

/// Write `bytes` beside `path` and rename over it. `mode` (Unix only) is set
/// before the first byte, so a secret is never briefly world-readable.
pub fn write_atomic(path: &Path, bytes: &[u8], mode: Option<u32>) -> Result<(), String> {
    let tmp = sibling(path, ".gotg-tmp");
    let err = |e: std::io::Error| format!("{}: {e}", path.display());
    let mut opts = OpenOptions::new();
    opts.write(true).create(true).truncate(true);
    #[cfg(unix)]
    if let Some(m) = mode {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(m);
    }
    #[cfg(not(unix))]
    let _ = mode;
    let mut f = opts.open(&tmp).map_err(err)?;
    f.write_all(bytes).map_err(err)?;
    f.sync_all().map_err(err)?;
    drop(f);
    fs::rename(&tmp, path).map_err(|e| {
        let _ = fs::remove_file(&tmp);
        err(e)
    })
}

/// Refuse a secret anybody else can read (config.sh `config_check_perms`).
/// Windows keeps %APPDATA% per user by its ACLs; there is no mode to read.
pub fn check_secret_perms(path: &Path) -> Result<(), String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(path)
            .map_err(|e| format!("{}: {e}", path.display()))?
            .permissions()
            .mode()
            & 0o777;
        if mode & 0o077 != 0 {
            return Err(format!(
                "{} is mode {mode:o}; it holds a secret. Fix with: chmod 600 {}",
                path.display(),
                path.display()
            ));
        }
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

/// Copy a file, or a directory's whole tree, to `to` (its parents made).
pub fn copy_tree(from: &Path, to: &Path) -> Result<(), String> {
    let meta = fs::metadata(from).map_err(|e| format!("{}: {e}", from.display()))?;
    if let Some(dir) = to.parent() {
        fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    if meta.is_dir() {
        fs::create_dir_all(to).map_err(|e| format!("{}: {e}", to.display()))?;
        for entry in fs::read_dir(from).map_err(|e| format!("{}: {e}", from.display()))? {
            let entry = entry.map_err(|e| format!("{}: {e}", from.display()))?;
            copy_tree(&entry.path(), &to.join(entry.file_name()))?;
        }
        Ok(())
    } else {
        copy_file(from, to)
    }
}

/// A plain copy, writable afterwards: a bundle unpacked from a zip or a
/// read-only store path must not hand the emulator a file it cannot save to.
pub fn copy_file(from: &Path, to: &Path) -> Result<(), String> {
    let tmp = sibling(to, ".gotg-tmp");
    fs::copy(from, &tmp).map_err(|e| format!("copy {} -> {}: {e}", from.display(), to.display()))?;
    let mut perms = fs::metadata(&tmp)
        .map_err(|e| format!("{}: {e}", tmp.display()))?
        .permissions();
    #[allow(clippy::permissions_set_readonly_false)]
    perms.set_readonly(false);
    let _ = fs::set_permissions(&tmp, perms);
    fs::rename(&tmp, to).map_err(|e| format!("{}: {e}", to.display()))
}

/// An exclusive lock on `path`, held until the file is dropped. Waits up to
/// `wait` for another gotg doing the same download, as `with_lock` does.
pub fn lock(path: &Path, wait: Duration, what: &str) -> Result<File, String> {
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    let f = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(path)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    let start = Instant::now();
    let mut said = false;
    loop {
        match f.try_lock() {
            Ok(()) => return Ok(f),
            Err(fs::TryLockError::WouldBlock) => {
                if !said {
                    crate::log(&format!("waiting for another gotg to finish {what}"));
                    said = true;
                }
                if start.elapsed() > wait {
                    return Err(format!(
                        "timed out waiting for another gotg process to finish {what}"
                    ));
                }
                std::thread::sleep(Duration::from_millis(500));
            }
            Err(fs::TryLockError::Error(e)) => return Err(format!("{}: {e}", path.display())),
        }
    }
}

/// Whether a directory exists and holds anything.
pub fn populated(dir: &Path) -> bool {
    fs::read_dir(dir).map(|mut d| d.next().is_some()).unwrap_or(false)
}

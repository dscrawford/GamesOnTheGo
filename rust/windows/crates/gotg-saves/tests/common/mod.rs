//! What the integration tests share: scratch directories, the real service
//! (started by whoever runs them: `nix/checks/rust-saves.nix`), and the real
//! bash client as the Linux machine.
#![allow(dead_code, clippy::unwrap_used)]

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU32, Ordering};

static SEQ: AtomicU32 = AtomicU32::new(0);

/// A fresh directory under the system temp dir, removed on drop.
pub struct Scratch(pub PathBuf);

impl Scratch {
    pub fn new(tag: &str) -> Scratch {
        let n = SEQ.fetch_add(1, Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!("gotg-saves-{tag}-{}-{n}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        Scratch(dir)
    }
    pub fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// A unique environment name, so tests sharing one service never meet.
pub fn env_name(tag: &str) -> String {
    let n = SEQ.fetch_add(1, Ordering::SeqCst);
    format!("env-t{}-{tag}{n}", std::process::id())
}

fn required() -> bool {
    std::env::var("GOTG_SAVES_REQUIRE").is_ok_and(|v| v == "1")
}

/// `GOTG_SAVES_SERVICE` (the service's URL; token `GOTG_SAVES_TOKEN`, default
/// test-token), or None to skip -- a failure under GOTG_SAVES_REQUIRE=1.
pub fn service() -> Option<(String, String)> {
    match std::env::var("GOTG_SAVES_SERVICE") {
        Ok(url) if !url.is_empty() => Some((
            url,
            std::env::var("GOTG_SAVES_TOKEN").unwrap_or_else(|_| "test-token".into()),
        )),
        _ => {
            assert!(
                !required(),
                "GOTG_SAVES_REQUIRE=1 but GOTG_SAVES_SERVICE is not set"
            );
            eprintln!("skipped: no GOTG_SAVES_SERVICE");
            None
        }
    }
}

/// `GOTG_SAVES_CLIENT`: the bash client's root (`src/client`), or None to skip.
pub fn bash_client() -> Option<PathBuf> {
    match std::env::var("GOTG_SAVES_CLIENT") {
        Ok(dir) if !dir.is_empty() => Some(PathBuf::from(dir)),
        _ => {
            assert!(
                !required(),
                "GOTG_SAVES_REQUIRE=1 but GOTG_SAVES_CLIENT is not set"
            );
            eprintln!("skipped: no GOTG_SAVES_CLIENT");
            None
        }
    }
}

pub fn write(root: &Path, rel: &str, data: &[u8]) {
    let p = root.join(rel);
    fs::create_dir_all(p.parent().unwrap()).unwrap();
    fs::write(p, data).unwrap();
}

/// Every regular file under `root`, relative, with its bytes.
pub fn tree(root: &Path) -> Vec<(String, Vec<u8>)> {
    fn walk(root: &Path, dir: &Path, out: &mut Vec<(String, Vec<u8>)>) {
        let Ok(rd) = fs::read_dir(dir) else { return };
        for e in rd.flatten() {
            let ft = e.file_type().unwrap();
            if ft.is_dir() {
                walk(root, &e.path(), out);
            } else if ft.is_file() {
                let rel = e
                    .path()
                    .strip_prefix(root)
                    .unwrap()
                    .to_string_lossy()
                    .replace('\\', "/");
                out.push((rel, fs::read(e.path()).unwrap()));
            }
        }
    }
    let mut out = Vec::new();
    walk(root, root, &mut out);
    out.sort();
    out
}

/// A Linux machine running the real bash client against the same service,
/// its environment pinned (GOTG_PINNED_ATTR) to a hand-written saves.json.
pub struct Linux {
    pub dir: Scratch,
    pub client: PathBuf,
    pub env: String,
}

impl Linux {
    pub fn new(client: &Path, env: &str, url: &str, token: &str, manifest: &serde_json::Value) -> Linux {
        let dir = Scratch::new("linux");
        let root = dir.path();
        fs::create_dir_all(root.join("config")).unwrap();
        let api = root.join("config/api.json");
        fs::write(
            &api,
            serde_json::json!({"url": url, "token": token, "name": "linux-box"}).to_string(),
        )
        .unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(root.join("config"), fs::Permissions::from_mode(0o700)).unwrap();
            fs::set_permissions(&api, fs::Permissions::from_mode(0o600)).unwrap();
        }
        let mut m = manifest.clone();
        m["name"] = env.into();
        m["version"] = 1.into();
        m["legacy"] = serde_json::json!([]);
        write(root, "envroot/share/gotg/saves.json", m.to_string().as_bytes());
        fs::create_dir_all(root.join("envstate").join(env)).unwrap();
        Linux {
            dir,
            client: client.to_path_buf(),
            env: env.to_string(),
        }
    }

    pub fn state(&self) -> PathBuf {
        self.dir.path().join("envstate").join(&self.env)
    }

    fn command(&self) -> Command {
        let root = self.dir.path();
        let mut c = Command::new("bash");
        c.env("GOTG_STATE_DIR", root.join("state"))
            .env("GOTG_CONFIG_DIR", root.join("config"))
            .env("GOTG_PINNED_ATTR", &self.env)
            .env("GOTG_PINNED_ENV", root.join("envroot"))
            .env("GOTG_ENV_STATE_DIR", root.join("envstate"))
            .env("GOTG_ROOT", &self.client)
            .env_remove("GOTG_NOW");
        c
    }

    /// `gotg saves <args>`: (success, stdout, stderr).
    pub fn saves(&self, args: &[&str]) -> (bool, String, String) {
        let out = self
            .command()
            .arg(self.client.join("bin/gotg"))
            .arg("saves")
            .args(args)
            .output()
            .unwrap();
        (
            out.status.success(),
            String::from_utf8_lossy(&out.stdout).into_owned(),
            String::from_utf8_lossy(&out.stderr).into_owned(),
        )
    }

    /// The client's own `saves_members`, run in its libraries.
    pub fn members(&self) -> Vec<String> {
        let out = self
            .command()
            .arg("-c")
            // Its status is its last && list's, which the client never reads.
        .arg(r#"set -euo pipefail; GOTG_LIB="$GOTG_ROOT/lib"; source "$GOTG_LIB/all.sh"; saves_members "$1" || true"#)
            .arg("members")
            .arg(&self.env)
            .output()
            .unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        String::from_utf8_lossy(&out.stdout)
            .lines()
            .map(str::to_string)
            .collect()
    }
}

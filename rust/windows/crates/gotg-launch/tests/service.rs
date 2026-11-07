//! gotg against the real GOTG service (gotg-proxy), as the bash client's
//! tests run it: login with a token, a download with a resume and a poisoned
//! partial, keys, the seed config and actions, the emulator's command line
//! and exit code. Needs GOTG_TEST_PROXY (the gotg-proxy program); skipped
//! without it, so a plain `cargo test` stays offline. nix/checks/windows-launcher.nix
//! sets it.
#![cfg(unix)]
#![allow(clippy::unwrap_used)]

use std::fs;
use std::io::Write;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Output, Stdio};
use std::time::{Duration, Instant};

use serde_json::json;

const GOTG: &str = env!("CARGO_BIN_EXE_gotg");
const GAME: &[u8] = b"N64 ROM BYTES -- the whole game, all of it, verified by sha256";

struct Service {
    child: Child,
    url: String,
    root: PathBuf,
}

impl Drop for Service {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port()
}

fn sha256_hex(b: &[u8]) -> String {
    use sha2::Digest;
    sha2::Sha256::digest(b)
        .iter()
        .map(|x| format!("{x:02x}"))
        .collect()
}

fn start(proxy: &str, root: &Path) -> Service {
    let library = root.join("library");
    let files = root.join("files");
    let state = root.join("service-state");
    for d in [&library, &files, &state] {
        fs::create_dir_all(d).unwrap();
    }
    for _ in 0..3 {
        let port = free_port();
        let url = format!("http://127.0.0.1:{port}");
        let log = fs::File::create(root.join("service.log")).unwrap();
        let child = Command::new(proxy)
            .env("PYTHONUNBUFFERED", "1")
            .env("PORT", port.to_string())
            .env("GOTG_PROXY_TOKEN", "test-token")
            .env("GOTG_SAVES_DIR", root.join("service-saves"))
            .env("GOTG_FILES_DIR", &files)
            .env("GOTG_LIBRARY_ROOTS", &library)
            .env("GOTG_CATALOG_DB", state.join("catalog.db"))
            .env("GOTG_INDEX_TOKEN", "index-token")
            .env("GOTG_ART_DIR", root.join("service-art"))
            .env("GOTG_LOGS_DIR", root.join("service-logs"))
            .env("GOTG_PORTS_DIR", root.join("service-ports"))
            .env("GOTG_ADMIN_TOKEN", "admin-token")
            .env("GOTG_LIBRARY_TOKEN", "library-token")
            .env("GOTG_TOKENS_DB", state.join("tokens.db"))
            .stdout(log.try_clone().unwrap())
            .stderr(log)
            .spawn()
            .unwrap();
        let mut svc = Service {
            child,
            url,
            root: root.to_path_buf(),
        };
        let deadline = Instant::now() + Duration::from_secs(20);
        while Instant::now() < deadline {
            if svc.child.try_wait().unwrap().is_some() {
                break;
            }
            if ureq::get(format!("{}/healthz", svc.url)).call().is_ok() {
                return svc;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        let _ = svc.child.kill();
        let _ = svc.child.wait();
        // Keep the directory for the next try: forget it without dropping.
        std::mem::forget(svc);
    }
    panic!(
        "the gotg service did not start: {}",
        fs::read_to_string(root.join("service.log")).unwrap_or_default()
    );
}

/// The indexer's half: bytes in the library, a row through the index token.
fn publish(svc: &Service, platform: &str, id: &str, name: &str, bytes: &[u8]) -> serde_json::Value {
    let dir = svc.root.join("library").join(platform);
    fs::create_dir_all(&dir).unwrap();
    let path = dir.join(name);
    fs::write(&path, bytes).unwrap();
    let member = json!({
        "name": name, "path": path, "size_bytes": bytes.len(), "mtime": 1_700_000_000,
        "sha256": sha256_hex(bytes),
    });
    ureq::put(format!("{}/catalog/{platform}/{id}", svc.url))
        .header("Authorization", "Bearer index-token")
        .send_json(json!({"handler": "single_file", "title": "A Game", "files": [member.clone()]}))
        .unwrap();
    json!({"id": id, "platform": platform, "handler": "single_file", "title": "A Game",
           "files": [{"name": name, "size_bytes": bytes.len(), "sha256": sha256_hex(bytes)}]})
}

struct Bundle {
    dir: PathBuf,
    home: PathBuf,
}

impl Bundle {
    fn new(root: &Path, svc: &Service, game: serde_json::Value) -> Bundle {
        let dir = root.join("bundle");
        fs::create_dir_all(dir.join("emulator")).unwrap();
        fs::create_dir_all(dir.join("config").join("0")).unwrap();
        fs::write(dir.join("config").join("0").join("settings.bml"), "seeded\n").unwrap();
        // The emulator: records its argv and one env var, and exits 7.
        let emu = dir.join("emulator").join("emu");
        fs::write(
            &emu,
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$GOTG_TEST_ARGS\"\nprintf '%s' \"$EMU_STATE\" > \"$GOTG_TEST_ARGS.env\"\npwd > \"$GOTG_TEST_ARGS.cwd\"\nif [ -n \"$GOTG_TEST_SAVE\" ]; then printf %s \"$GOTG_TEST_SAVE\" > \"$EMU_STATE/saves/game.srm\"; fi\nexit 7\n",
        )
        .unwrap();
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&emu, fs::Permissions::from_mode(0o755)).unwrap();
        let spec = json!({
            "version": 1, "server": svc.url, "files_urls": [svc.url],
            "attr": "env-n64", "variant": "", "output": "n64.usa.a_game",
            "game": game,
            "windows": {
                "version": 1, "name": "env-n64", "exe": "emulator/emu",
                "args": ["{fullscreen}", "--setting", "Paths/Saves={state}/saves/", "{target}"],
                "fullscreen_flag": "--fullscreen",
                "env": {"EMU_STATE": "{state}"},
                "config_root": "state",
                "config_files": {"Ares/settings.bml": "config/0/settings.bml"},
                "actions": [{"ini": {"file": "{state}/User/Dolphin.ini", "section": "Core", "key": "SIMDisplay", "value": "False"}}],
                "saves": ["saves/**"], "excludes": [], "states": null, "saves_dirs": ["saves"],
                "keys": {"into": "keys", "files": ["prod.keys"]}, "firmware": null,
                "native_port": false, "game_version_min": null, "game_version_max": null
            }
        });
        fs::write(dir.join("spec.json"), spec.to_string()).unwrap();
        Bundle {
            dir,
            home: root.join("home"),
        }
    }

    fn gotg(&self, args: &[&str], stdin: Option<&str>) -> Output {
        self.gotg_env(args, stdin, &[])
    }

    fn gotg_env(&self, args: &[&str], stdin: Option<&str>, env: &[(&str, &str)]) -> Output {
        let mut cmd = Command::new(GOTG);
        cmd.args(["--spec", self.dir.join("spec.json").to_str().unwrap()])
            .args(args)
            .env_clear()
            .env("PATH", std::env::var("PATH").unwrap_or_default())
            .env("HOME", &self.home)
            .env("GOTG_CONFIG_DIR", self.home.join("config"))
            .env("GOTG_LOCAL_DIR", self.home.join("local"))
            .env("GOTG_TEST_ARGS", self.home.join("args"))
            .envs(env.iter().copied())
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = cmd.spawn().unwrap();
        let mut input = child.stdin.take().unwrap();
        if let Some(s) = stdin {
            input.write_all(s.as_bytes()).unwrap();
        }
        drop(input);
        child.wait_with_output().unwrap()
    }
}

fn text(b: &[u8]) -> String {
    String::from_utf8_lossy(b).into_owned()
}

#[test]
fn login_download_and_launch_against_the_service() {
    let Some(proxy) = std::env::var("GOTG_TEST_PROXY").ok().filter(|p| !p.is_empty()) else {
        eprintln!("skipped: GOTG_TEST_PROXY is not set");
        return;
    };
    let root = std::env::temp_dir().join(format!("gotg-launch-it-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    let svc = start(&proxy, &root);
    let name = "A Game (USA).z64";
    let game = publish(&svc, "n64", "usa.a_game", name, GAME);
    fs::create_dir_all(svc.root.join("files").join("n64")).unwrap();
    fs::write(svc.root.join("files").join("n64").join("prod.keys"), "KEYS").unwrap();
    let b = Bundle::new(&root, &svc, game);

    // Not logged in: said plainly, nothing started.
    let out = b.gotg(&[], None);
    assert_eq!(out.status.code(), Some(1));
    assert!(
        text(&out.stderr).contains("run: gotg login"),
        "{}",
        text(&out.stderr)
    );

    // A wrong token is refused before anything is written.
    let out = b.gotg(&["login", "--token"], Some("not-the-token\n"));
    assert_eq!(out.status.code(), Some(1), "{}", text(&out.stderr));
    assert!(
        text(&out.stderr).contains("did not accept"),
        "{}",
        text(&out.stderr)
    );
    assert!(!b.home.join("config").join("api.json").exists());

    // The bundle's server is the default; the token arrives on a pipe.
    let out = b.gotg(&["login", "--token"], Some("test-token\n"));
    assert!(out.status.success(), "{}", text(&out.stderr));
    let api: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(b.home.join("config").join("api.json")).unwrap()).unwrap();
    assert_eq!(api["url"], svc.url.as_str());
    assert_eq!(api["token"], "test-token");

    // A partial from an earlier run, the right first bytes: resumed.
    let partial = b
        .home
        .join("local")
        .join("games")
        .join(".gotg-partial")
        .join("usa.a_game");
    fs::create_dir_all(&partial).unwrap();
    fs::write(partial.join(name), &GAME[..10]).unwrap();

    let out = b.gotg(&["--windowed", "--", "--extra"], None);
    let err = text(&out.stderr);
    assert_eq!(out.status.code(), Some(7), "{err}");
    let target = b.home.join("local").join("games").join("n64").join(name);
    assert_eq!(fs::read(&target).unwrap(), GAME);
    assert!(!partial.exists(), "staging is removed once installed");
    let state = b.home.join("local").join("env").join("env-n64");
    let args = fs::read_to_string(b.home.join("args")).unwrap();
    assert_eq!(
        args,
        format!(
            "--setting\nPaths/Saves={}/saves/\n{}\n--extra\n",
            state.display(),
            target.display()
        )
    );
    assert_eq!(
        fs::read_to_string(b.home.join("args.env")).unwrap(),
        state.display().to_string()
    );
    assert_eq!(
        fs::read_to_string(b.home.join("args.cwd")).unwrap().trim(),
        state.display().to_string()
    );
    assert!(state.join("saves").is_dir());
    assert_eq!(
        fs::read_to_string(state.join("Ares").join("settings.bml")).unwrap(),
        "seeded\n"
    );
    assert_eq!(
        fs::read_to_string(state.join("User").join("Dolphin.ini")).unwrap(),
        "[Core]\nSIMDisplay = False\n"
    );
    assert_eq!(
        fs::read_to_string(state.join("keys").join("prod.keys")).unwrap(),
        "KEYS"
    );

    // Fullscreen by default; an installed game needs no network.
    drop(svc);
    let out = b.gotg(&[], None);
    assert_eq!(out.status.code(), Some(7), "{}", text(&out.stderr));
    assert!(
        fs::read_to_string(b.home.join("args"))
            .unwrap()
            .starts_with("--fullscreen\n")
    );

    let out = b.gotg(&["--dry-run"], None);
    assert!(out.status.success());
    assert!(text(&out.stdout).contains(&format!("run: {}", b.dir.join("emulator").join("emu").display())));
    let _ = fs::remove_dir_all(&root);
}

#[test]
fn a_poisoned_partial_is_replaced() {
    let Some(proxy) = std::env::var("GOTG_TEST_PROXY").ok().filter(|p| !p.is_empty()) else {
        eprintln!("skipped: GOTG_TEST_PROXY is not set");
        return;
    };
    let root = std::env::temp_dir().join(format!("gotg-launch-poison-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    let svc = start(&proxy, &root);
    let name = "A Game (USA).z64";
    let game = publish(&svc, "n64", "usa.a_game", name, GAME);
    let b = Bundle::new(&root, &svc, game);
    let out = b.gotg(&["login", "--token"], Some("test-token\n"));
    assert!(out.status.success(), "{}", text(&out.stderr));

    // Wrong bytes resumed into a file that hashes wrong: deleted, fetched again.
    let partial = b
        .home
        .join("local")
        .join("games")
        .join(".gotg-partial")
        .join("usa.a_game");
    fs::create_dir_all(&partial).unwrap();
    fs::write(partial.join(name), b"XXXXXXXXXX").unwrap();
    let out = b.gotg(&[], None);
    let err = text(&out.stderr);
    assert_eq!(out.status.code(), Some(7), "{err}");
    assert!(err.contains("checksum mismatch"), "{err}");
    assert_eq!(
        fs::read(b.home.join("local").join("games").join("n64").join(name)).unwrap(),
        GAME
    );
    drop(svc);
    let _ = fs::remove_dir_all(&root);
}

/// Two machines on one service: a launch pushes after the game, the other's
/// launch pulls before it, a conflict stops a launch until `saves keep`, and
/// an unreachable service is played through.
#[test]
fn saves_follow_the_player_between_machines() {
    let Some(proxy) = std::env::var("GOTG_TEST_PROXY").ok().filter(|p| !p.is_empty()) else {
        eprintln!("skipped: GOTG_TEST_PROXY is not set");
        return;
    };
    let root = std::env::temp_dir().join(format!("gotg-launch-saves-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    let svc = start(&proxy, &root);
    let name = "A Game (USA).z64";
    let game = publish(&svc, "n64", "usa.a_game", name, GAME);
    let a = Bundle::new(&root, &svc, game);
    let b = Bundle {
        dir: a.dir.clone(),
        home: root.join("home-b"),
    };
    let save = |m: &Bundle| {
        m.home
            .join("local")
            .join("env")
            .join("env-n64")
            .join("saves")
            .join("game.srm")
    };
    for m in [&a, &b] {
        let out = m.gotg(&["login", "--token"], Some("test-token\n"));
        assert!(out.status.success(), "{}", text(&out.stderr));
    }

    // Not signed in to the saves store is never the case here; a game that
    // writes a save has it pushed once it exits, its exit code kept.
    let out = a.gotg_env(
        &[],
        None,
        &[("GOTG_TEST_SAVE", "one"), ("GOTG_DEVICE_NAME", "pc-a")],
    );
    let err = text(&out.stderr);
    assert_eq!(out.status.code(), Some(7), "{err}");
    assert!(err.contains("env-n64: pushed saves, generation 1"), "{err}");

    // The other machine has it before its game starts.
    let out = b.gotg(&[], None);
    let err = text(&out.stderr);
    assert_eq!(out.status.code(), Some(7), "{err}");
    assert!(err.contains("pulled saves, generation 1"), "{err}");
    assert_eq!(fs::read_to_string(save(&b)).unwrap(), "one");

    // Both move on: a's game pushes 2, b's own work since 1 is a conflict,
    // and b's launch stops before its game.
    let out = a.gotg_env(&[], None, &[("GOTG_TEST_SAVE", "two")]);
    assert!(
        text(&out.stderr).contains("generation 2"),
        "{}",
        text(&out.stderr)
    );
    fs::write(save(&b), "b's own").unwrap();
    let _ = fs::remove_file(b.home.join("args"));
    let out = b.gotg(&[], None);
    let err = text(&out.stderr);
    assert_eq!(out.status.code(), Some(1), "{err}");
    assert!(
        err.contains("gotg saves keep here") && err.contains("gotg saves keep remote"),
        "{err}"
    );
    assert!(!b.home.join("args").exists(), "the game started over a conflict");
    assert_eq!(fs::read_to_string(save(&b)).unwrap(), "b's own");

    // Settled for this machine's: pushed over the head, which stays listed.
    let out = b.gotg(&["saves", "keep", "here"], None);
    assert!(out.status.success(), "{}", text(&out.stderr));
    assert!(
        text(&out.stdout).contains("generation 3"),
        "{}",
        text(&out.stdout)
    );
    let out = b.gotg(&["saves", "list"], None);
    let list = text(&out.stdout);
    assert!(out.status.success(), "{}", text(&out.stderr));
    assert!(
        list.contains("remote:3 (here)") && list.contains("remote:2"),
        "{list}"
    );
    assert!(list.contains("pc-a"), "the device is the name a gave: {list}");

    // a takes the service's; then a restore of generation 2 puts "two" back.
    let out = a.gotg(&["saves", "keep", "remote"], None);
    assert!(out.status.success(), "{}", text(&out.stderr));
    assert_eq!(fs::read_to_string(save(&a)).unwrap(), "b's own");
    let out = a.gotg(&["saves", "restore", "remote:2"], None);
    assert!(out.status.success(), "{}", text(&out.stderr));
    assert_eq!(fs::read_to_string(save(&a)).unwrap(), "two");
    let out = a.gotg(&["saves", "push"], None);
    assert!(
        text(&out.stdout).contains("pushed generation 4"),
        "{}",
        text(&out.stdout)
    );
    let out = a.gotg(&["saves", "pull"], None);
    assert!(out.status.success(), "{}", text(&out.stderr));
    assert!(a.gotg(&["saves", "frob"], None).status.code() == Some(1));

    // The service gone: the game is played anyway, with a warning, and the
    // push after it is a warning too.
    drop(svc);
    let out = a.gotg_env(&[], None, &[("GOTG_TEST_SAVE", "offline")]);
    let err = text(&out.stderr);
    assert_eq!(out.status.code(), Some(7), "{err}");
    assert!(err.contains("saves not synced"), "{err}");
    assert!(err.contains("saves were not pushed"), "{err}");
    let _ = fs::remove_dir_all(&root);
}

//! gotg.exe: a game's Windows bundle played without Nix (docs/windows.md,
//! "What gotg.exe does"). This file is the sequence; the decisions are in
//! the modules beside it.

mod api;
mod cli;
mod console;
mod download;
mod extras;
mod fsx;
mod ini;
mod login;
mod paths;
mod prepare;
mod render;
mod saves;
mod spec;
mod validate;

use std::io::IsTerminal;
use std::path::{Path, PathBuf};
use std::process::Command as Process;

use cli::{Cli, Command};
use paths::Dirs;
use render::{Placeholders, quote_windows, under};
use spec::Spec;

pub fn log(msg: &str) {
    eprintln!("gotg: {msg}");
}

pub fn warn(msg: &str) {
    eprintln!("gotg: warning: {msg}");
}

fn version() -> String {
    format!(
        "gotg {} ({}-{})",
        env!("CARGO_PKG_VERSION"),
        std::env::consts::ARCH,
        std::env::consts::OS
    )
}

/// spec.json beside the exe, unless --spec (or GOTG_SPEC) names another.
fn spec_path(cli: &Cli) -> Result<PathBuf, String> {
    if let Some(p) = &cli.spec {
        return Ok(p.clone());
    }
    if let Some(p) = std::env::var_os("GOTG_SPEC").filter(|p| !p.is_empty()) {
        return Ok(PathBuf::from(p));
    }
    let exe = std::env::current_exe().map_err(|e| format!("cannot find gotg.exe itself: {e}"))?;
    Ok(exe.parent().unwrap_or(Path::new(".")).join("spec.json"))
}

fn load_spec(cli: &Cli) -> Result<(Spec, PathBuf), String> {
    let path = spec_path(cli)?;
    if !path.is_file() {
        return Err(format!(
            "no spec.json at {} -- gotg.exe runs from the game's unzipped folder (or give --spec PATH)",
            path.display()
        ));
    }
    let spec = Spec::load(&path)?;
    // {bundle}: the directory spec.json and gotg.exe sit in.
    let bundle = match path.parent() {
        Some(p) if !p.as_os_str().is_empty() => p.to_path_buf(),
        _ => PathBuf::from("."),
    };
    let bundle = std::path::absolute(&bundle).unwrap_or(bundle);
    Ok((spec, bundle))
}

/// The token this machine holds is for the server it logged into; a game from
/// another one is refused rather than handed that token.
fn check_server(spec: &Spec, api: Option<&api::Api>) -> Result<(), String> {
    let (Some(api), false) = (api, spec.server.is_empty()) else {
        return Ok(());
    };
    let want = spec.server.trim_end_matches('/');
    if want != api.url {
        return Err(format!(
            "this game is from {want}, and this computer is logged into {}.\n     To play from there: gotg login --server {want}",
            api.url
        ));
    }
    Ok(())
}

fn fullscreen_on(cli: &Cli) -> bool {
    !cli.windowed && std::env::var("GOTG_FULLSCREEN").as_deref() != Ok("0")
}

fn placeholders(spec: &Spec, dirs: &Dirs, bundle: &Path, target: PathBuf, on: bool) -> Placeholders {
    Placeholders {
        install: target.parent().map(Path::to_path_buf).unwrap_or_default(),
        target,
        state: dirs.state(&spec.windows.name),
        emulator: bundle.join("emulator"),
        bundle: bundle.to_path_buf(),
        fullscreen: render::fullscreen_words(spec.windows.fullscreen_flag.as_deref(), on),
    }
}

struct Plan {
    exe: PathBuf,
    args: Vec<String>,
    env: Vec<(String, String)>,
    cwd: PathBuf,
}

fn plan(spec: &Spec, ph: &Placeholders, extra: &[String], first: bool) -> Plan {
    let exe = under(&ph.bundle, &spec.windows.exe);
    let args = match &spec.windows.first_run {
        Some(fr) if first => &fr.args,
        _ => &spec.windows.args,
    };
    let mut args = ph.args(args);
    args.extend(extra.iter().cloned());
    let env = spec
        .windows
        .env
        .iter()
        .map(|(k, v)| (k.clone(), ph.env_value(v)))
        .collect();
    let cwd = prepare::cwd(spec, ph);
    Plan { exe, args, env, cwd }
}

fn print_plan(p: &Plan) {
    println!("cwd: {}", p.cwd.display());
    for (k, v) in &p.env {
        println!("env: {k}={v}");
    }
    let mut line = quote_windows(&p.exe.to_string_lossy());
    for a in &p.args {
        line.push(' ');
        line.push_str(&quote_windows(a));
    }
    println!("run: {line}");
}

fn login_cmd(cli: &Cli, dirs: &Dirs) -> Result<i32, String> {
    // The bundle knows its server; a bare `gotg login` beside it asks for nothing else.
    let server = load_spec(cli).ok().map(|(s, _)| s.server);
    login::run(&cli.login, server.as_deref(), &dirs.api_file)?;
    Ok(0)
}

fn saves_job<'a>(
    spec: &'a Spec,
    dirs: &'a Dirs,
    state: &'a Path,
    api: Option<&'a api::Api>,
) -> saves::SavesJob<'a> {
    saves::SavesJob {
        env: &spec.windows.name,
        state,
        saves: &spec.windows.saves,
        excludes: &spec.windows.excludes,
        states: spec.windows.states.as_ref(),
        api,
        home: &dirs.saves,
        locks: &dirs.locks,
    }
}

fn launch(cli: &Cli, dirs: &Dirs) -> Result<i32, String> {
    let (spec, bundle) = load_spec(cli)?;
    let mut api = api::load(&dirs.api_file)?;
    check_server(&spec, api.as_ref())?;
    let on = fullscreen_on(cli);

    if cli.dry_run {
        let target = download::install_path(&spec.game, &dirs.games)?;
        let ph = placeholders(&spec, dirs, &bundle, target, on);
        let first = prepare::first_run_plan(&spec, &ph);
        if spec.windows.first_run.is_some() {
            println!("first run: {}", if first.first { "yes" } else { "no" });
        }
        print_plan(&plan(&spec, &ph, &cli.extra, first.first));
        return Ok(0);
    }

    let exe = under(&bundle, &spec.windows.exe);
    if !exe.is_file() {
        return Err(format!(
            "the bundle has no {} -- unzip the whole folder",
            exe.display()
        ));
    }
    let installed = download::install_path(&spec.game, &dirs.games)?.exists();
    if !installed && api.is_none() && std::io::stdin().is_terminal() {
        log("this computer is not signed in yet; signing in first (gotg login)");
        let server = (!spec.server.is_empty()).then_some(spec.server.as_str());
        login::run(&cli.login, server, &dirs.api_file)?;
        api = api::load(&dirs.api_file)?;
        check_server(&spec, api.as_ref())?;
    }
    let target = download::ensure(&spec, dirs, api.as_ref())?;
    let ph = placeholders(&spec, dirs, &bundle, target, on);

    extras::ensure_keys(&spec, &ph.state, api.as_ref());
    extras::ensure_firmware(&spec, dirs, &ph.state, api.as_ref());

    let sync = saves::backend();
    std::fs::create_dir_all(&ph.state).map_err(|e| format!("{}: {e}", ph.state.display()))?;
    let job = saves_job(&spec, dirs, &ph.state, api.as_ref());
    sync.before(&job)?;
    prepare::run(&spec, &ph)?;
    let first = prepare::first_run_plan(&spec, &ph);
    prepare::first_run_apply(&first)?;

    let p = plan(&spec, &ph, &cli.extra, first.first);
    log(&format!("starting {}", spec.title()));
    // Ctrl+C and the console's close button are the game's while it runs;
    // gotg.exe stays to push its saves.
    let shield = console::Shield::up();
    let status = Process::new(&p.exe)
        .args(&p.args)
        .envs(p.env.iter().map(|(k, v)| (k, v)))
        .current_dir(&p.cwd)
        .status()
        .map_err(|e| format!("could not start {}: {e}", p.exe.display()))?;
    let code = status.code().unwrap_or(1);
    if code != 0 {
        warn(&format!("{} exited with {code}", spec.windows.exe));
    }
    if let Err(e) = sync.after(&job) {
        warn(&format!("saves were not pushed: {e}"));
    }
    drop(shield);
    Ok(code)
}

fn saves_cmd(cli: &Cli, dirs: &Dirs, args: &[String]) -> Result<i32, String> {
    let (spec, _) = load_spec(cli)?;
    let api = api::load(&dirs.api_file)?;
    check_server(&spec, api.as_ref())?;
    let state = dirs.state(&spec.windows.name);
    saves::backend().command(&saves_job(&spec, dirs, &state, api.as_ref()), args)
}

fn run(cli: &Cli) -> Result<i32, String> {
    match &cli.command {
        Command::Version => {
            println!("{}", version());
            Ok(0)
        }
        Command::Help => {
            println!("{}\n\n{}", version(), cli::USAGE);
            Ok(0)
        }
        Command::Login => login_cmd(cli, &Dirs::from_process()?),
        Command::Saves(args) => saves_cmd(cli, &Dirs::from_process()?, args),
        Command::Launch => launch(cli, &Dirs::from_process()?),
    }
}

/// A double-clicked gotg.exe has a console of its own that closes with it:
/// an error must wait to be read.
#[cfg(windows)]
fn pause_if_alone() {
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetConsoleProcessList(list: *mut u32, count: u32) -> u32;
    }
    let mut ids = [0u32; 4];
    // SAFETY: the buffer is valid for the length passed.
    let n = unsafe { GetConsoleProcessList(ids.as_mut_ptr(), ids.len() as u32) };
    if n == 1 {
        eprint!("Press Enter to close this window.");
        let _ = std::io::stdin().read_line(&mut String::new());
    }
}

#[cfg(not(windows))]
fn pause_if_alone() {}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let code = match cli::parse(&args).and_then(|c| run(&c)) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("gotg: error: {e}");
            pause_if_alone();
            1
        }
    };
    std::process::exit(code);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::spec::tests::sample;

    #[test]
    fn server_mismatch_is_refused() {
        let spec = Spec::parse(&sample().to_string()).expect("spec");
        let other = api::Api {
            url: "https://other".into(),
            token: "t".into(),
            name: None,
        };
        assert!(
            check_server(&spec, Some(&other))
                .expect_err("should fail")
                .contains("gotg login --server https://gotg.example")
        );
        let same = api::Api {
            url: "https://gotg.example".into(),
            token: "t".into(),
            name: None,
        };
        assert!(check_server(&spec, Some(&same)).is_ok());
        assert!(check_server(&spec, None).is_ok());
    }

    #[test]
    fn plan_appends_extra_and_renders_env() {
        let mut v = sample();
        v["windows"]["env"] = serde_json::json!({"FS": "{fullscreen}", "HOME_DIR": "{state}"});
        let spec = Spec::parse(&v.to_string()).expect("spec");
        let dirs = Dirs::from_env(&|k| match k {
            "HOME" | "APPDATA" | "LOCALAPPDATA" => Some("/h".into()),
            _ => None,
        })
        .expect("dirs");
        let target = download::install_path(&spec.game, &dirs.games).expect("target");
        let ph = placeholders(&spec, &dirs, Path::new("/b"), target, false);
        let p = plan(&spec, &ph, &["--extra".to_string()], false);
        assert_eq!(p.exe, Path::new("/b").join("emulator").join("ares.exe"));
        assert_eq!(p.args.first().map(String::as_str), Some("--setting"));
        assert_eq!(p.args.last().map(String::as_str), Some("--extra"));
        assert!(p.env.contains(&("FS".to_string(), String::new())));
        assert_eq!(p.cwd, ph.state);
    }
}

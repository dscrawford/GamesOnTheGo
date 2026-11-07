//! The command line. No words at all is a launch: that is a double-click.

use std::path::PathBuf;

use crate::login::LoginArgs;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Command {
    Launch,
    Login,
    Saves(Vec<String>),
    Version,
    Help,
}

#[derive(Debug, Clone)]
pub struct Cli {
    pub command: Command,
    pub spec: Option<PathBuf>,
    pub windowed: bool,
    pub dry_run: bool,
    pub login: LoginArgs,
    /// After `--`: appended to the emulator's command line.
    pub extra: Vec<String>,
}

pub const USAGE: &str = "\
usage: gotg [--spec PATH] [--windowed] [--dry-run] [-- EMULATOR ARGS...]
       gotg login [--server URL] [--token | --claim URL]
       gotg saves list | check | push [--force] | pull | restore <id> | keep here|remote
       gotg --version

With no command, gotg downloads the game named by spec.json (beside gotg.exe,
or --spec), prepares its emulator and starts it. --windowed (or
GOTG_FULLSCREEN=0) starts it in a window; --dry-run prints the command line
it would run and changes nothing.";

pub fn parse(args: &[String]) -> Result<Cli, String> {
    let mut cli = Cli {
        command: Command::Launch,
        spec: None,
        windowed: false,
        dry_run: false,
        login: LoginArgs::default(),
        extra: Vec::new(),
    };
    let mut named: Option<&str> = None;
    let mut saves: Vec<String> = Vec::new();
    let mut it = args.iter();
    let value = |it: &mut std::slice::Iter<String>, flag: &str| -> Result<String, String> {
        it.next()
            .filter(|v| !v.is_empty())
            .cloned()
            .ok_or_else(|| format!("{flag} needs a value\n{USAGE}"))
    };
    while let Some(a) = it.next() {
        let a = a.as_str();
        match a {
            "--" => {
                cli.extra = it.by_ref().cloned().collect();
            }
            "--spec" => cli.spec = Some(PathBuf::from(value(&mut it, a)?)),
            s if s.starts_with("--spec=") => cli.spec = Some(PathBuf::from(&s[7..])),
            "--windowed" => cli.windowed = true,
            "--dry-run" => cli.dry_run = true,
            "--version" | "-V" => {
                return Ok(Cli {
                    command: Command::Version,
                    ..cli
                });
            }
            "--help" | "-h" => {
                return Ok(Cli {
                    command: Command::Help,
                    ..cli
                });
            }
            "--server" if named == Some("login") => cli.login.server = Some(value(&mut it, a)?),
            "--claim" if named == Some("login") => cli.login.claim = Some(value(&mut it, a)?),
            "--token" if named == Some("login") => cli.login.token = true,
            _ if named == Some("saves") => saves.push(a.to_string()),
            "launch" | "login" | "saves" | "version" | "help" if named.is_none() => named = Some(a),
            _ => return Err(format!("unknown argument: {a}\n{USAGE}")),
        }
    }
    cli.command = match named {
        None | Some("launch") => Command::Launch,
        Some("login") => Command::Login,
        Some("saves") => Command::Saves(saves),
        Some("version") => Command::Version,
        _ => Command::Help,
    };
    if !cli.extra.is_empty() && cli.command != Command::Launch {
        return Err(format!(
            "arguments after -- are for the emulator, at launch\n{USAGE}"
        ));
    }
    if cli.login.claim.is_some() && (cli.login.token || cli.login.server.is_some()) {
        return Err(crate::login::usage().to_string());
    }
    Ok(cli)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(v: &[&str]) -> Result<Cli, String> {
        parse(&v.iter().map(|s| s.to_string()).collect::<Vec<_>>())
    }

    #[test]
    fn bare_is_launch() {
        let c = p(&[]).expect("parse");
        assert_eq!(c.command, Command::Launch);
        assert!(!c.windowed && !c.dry_run && c.extra.is_empty());
    }

    #[test]
    fn launch_flags_and_extra() {
        let c = p(&[
            "--spec",
            "s.json",
            "--windowed",
            "--dry-run",
            "--",
            "--verbose",
            "--spec",
        ])
        .expect("parse");
        assert_eq!(c.spec, Some(PathBuf::from("s.json")));
        assert!(c.windowed && c.dry_run);
        assert_eq!(c.extra, vec!["--verbose".to_string(), "--spec".into()]);
    }

    #[test]
    fn login() {
        let c = p(&["login", "--server", "https://x"]).expect("parse");
        assert_eq!(c.command, Command::Login);
        assert_eq!(c.login.server.as_deref(), Some("https://x"));
        assert!(p(&["login", "--claim", "u", "--token"]).is_err());
        assert!(p(&["--server", "x"]).is_err());
    }

    #[test]
    fn saves_and_version() {
        let c = p(&["saves", "restore", "3", "--spec=s.json"]).expect("parse");
        assert_eq!(c.command, Command::Saves(vec!["restore".into(), "3".into()]));
        assert_eq!(c.spec, Some(PathBuf::from("s.json")));
        assert_eq!(p(&["--version"]).expect("parse").command, Command::Version);
        assert!(p(&["frobnicate"]).is_err());
    }
}

//! Placeholders (docs/windows.md, "Placeholders, rendered by gotg.exe").
//!
//! Text substitution in one pass, so a path that happens to contain
//! `{state}` is never expanded again. `{fullscreen}` is whole-argument only,
//! as on Linux: alone it becomes the flag or vanishes; inside a longer
//! argument it stays literal rather than half-working.

use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
pub struct Placeholders {
    pub target: PathBuf,
    pub install: PathBuf,
    pub state: PathBuf,
    pub bundle: PathBuf,
    pub emulator: PathBuf,
    /// The flag's words when fullscreen is on and the facet has one.
    pub fullscreen: Vec<String>,
}

const FULLSCREEN: &str = "{fullscreen}";

impl Placeholders {
    fn value(&self, name: &str) -> Option<String> {
        let p = match name {
            "target" => &self.target,
            "install" => &self.install,
            "state" => &self.state,
            "bundle" => &self.bundle,
            "emulator" => &self.emulator,
            _ => return None,
        };
        Some(p.to_string_lossy().into_owned())
    }

    /// `s` with every path placeholder replaced; anything else in braces kept.
    pub fn text(&self, s: &str) -> String {
        let mut out = String::with_capacity(s.len());
        let mut rest = s;
        while let Some(open) = rest.find('{') {
            out.push_str(&rest[..open]);
            let after = &rest[open + 1..];
            match after
                .find('}')
                .and_then(|close| self.value(&after[..close]).map(|v| (v, close)))
            {
                Some((v, close)) => {
                    out.push_str(&v);
                    rest = &after[close + 1..];
                }
                None => {
                    out.push('{');
                    rest = after;
                }
            }
        }
        out.push_str(rest);
        out
    }

    /// The emulator's command line: `{fullscreen}` expanded to its words or
    /// to nothing, every other argument rendered as text.
    pub fn args(&self, args: &[String]) -> Vec<String> {
        let mut out = Vec::with_capacity(args.len());
        for a in args {
            if a == FULLSCREEN {
                out.extend(self.fullscreen.iter().cloned());
            } else {
                out.push(self.text(a));
            }
        }
        out
    }

    /// An environment value: a whole `{fullscreen}` is the flag (or empty),
    /// as the Linux wrapper's bare expansion is.
    pub fn env_value(&self, v: &str) -> String {
        if v == FULLSCREEN {
            self.fullscreen.join(" ")
        } else {
            self.text(v)
        }
    }

    /// A path from an action: rendered, then taken under `base` if relative.
    /// Forward slashes in the manifest's own text become native separators.
    pub fn path(&self, s: &str, base: &Path) -> PathBuf {
        let rendered = self.text(s);
        let p = native(&rendered);
        if p.is_absolute() { p } else { base.join(p) }
    }
}

/// A `/`-separated path as this host spells it.
pub fn native(s: &str) -> PathBuf {
    if std::path::MAIN_SEPARATOR == '/' {
        PathBuf::from(s)
    } else {
        PathBuf::from(s.replace('/', std::path::MAIN_SEPARATOR_STR))
    }
}

/// A bundle- or state-relative manifest path (already validated) under `root`.
pub fn under(root: &Path, rel: &str) -> PathBuf {
    let mut p = root.to_path_buf();
    p.extend(rel.split('/'));
    p
}

/// The flag's words: split like the Linux wrapper's unquoted expansion.
pub fn fullscreen_words(flag: Option<&str>, on: bool) -> Vec<String> {
    match flag {
        Some(f) if on => f.split_whitespace().map(str::to_string).collect(),
        _ => Vec::new(),
    }
}

/// Windows' own quoting for one argument (CommandLineToArgvW's rules), for
/// showing a command line a person could paste.
pub fn quote_windows(arg: &str) -> String {
    if !arg.is_empty() && !arg.contains([' ', '\t', '\n', '"']) {
        return arg.to_string();
    }
    let mut out = String::from("\"");
    let mut slashes = 0;
    for c in arg.chars() {
        match c {
            '\\' => slashes += 1,
            '"' => {
                out.extend(std::iter::repeat_n('\\', slashes * 2 + 1));
                out.push('"');
                slashes = 0;
            }
            _ => {
                out.extend(std::iter::repeat_n('\\', slashes));
                out.push(c);
                slashes = 0;
            }
        }
    }
    out.extend(std::iter::repeat_n('\\', slashes * 2));
    out.push('"');
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ph(fullscreen: bool) -> Placeholders {
        let root = native("/g");
        Placeholders {
            target: under(&root, "games/n64/Game (USA).z64"),
            install: under(&root, "games/n64"),
            state: under(&root, "env/env-n64"),
            bundle: under(&root, "b"),
            emulator: under(&root, "b/emulator"),
            fullscreen: fullscreen_words(Some("--fullscreen"), fullscreen),
        }
    }

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| x.to_string()).collect()
    }

    fn sep(p: &str) -> String {
        native(p).to_string_lossy().into_owned()
    }

    #[test]
    fn fullscreen_is_whole_argument_and_may_vanish() {
        let args = s(&[
            "{fullscreen}",
            "--setting",
            "Paths/Saves={state}/saves/",
            "{target}",
        ]);
        assert_eq!(
            ph(true).args(&args),
            vec![
                "--fullscreen".to_string(),
                "--setting".into(),
                format!("Paths/Saves={}/saves/", sep("/g/env/env-n64")),
                sep("/g/games/n64/Game (USA).z64"),
            ]
        );
        let windowed = ph(false).args(&args);
        assert_eq!(windowed.len(), 3);
        assert_eq!(windowed[0], "--setting");
    }

    #[test]
    fn fullscreen_inside_an_argument_stays_literal() {
        assert_eq!(ph(true).args(&s(&["-x{fullscreen}"])), s(&["-x{fullscreen}"]));
    }

    #[test]
    fn multi_word_flag_splits() {
        let mut p = ph(true);
        p.fullscreen = fullscreen_words(Some("-f 1"), true);
        assert_eq!(p.args(&s(&["{fullscreen}", "x"])), s(&["-f", "1", "x"]));
        assert!(fullscreen_words(None, true).is_empty());
    }

    #[test]
    fn every_placeholder_and_unknown_braces() {
        let p = ph(true);
        assert_eq!(
            p.text("{install}|{bundle}|{emulator}"),
            format!("{}|{}|{}", sep("/g/games/n64"), sep("/g/b"), sep("/g/b/emulator"))
        );
        assert_eq!(p.text("{nope} {state"), "{nope} {state");
        assert_eq!(p.text("{{state}}"), format!("{{{}}}", sep("/g/env/env-n64")));
    }

    #[test]
    fn a_value_is_not_expanded_twice() {
        let mut p = ph(true);
        p.state = PathBuf::from("weird{target}dir");
        assert_eq!(p.text("{state}"), "weird{target}dir");
    }

    #[test]
    fn env_values() {
        assert_eq!(ph(true).env_value("{fullscreen}"), "--fullscreen");
        assert_eq!(ph(false).env_value("{fullscreen}"), "");
        assert_eq!(
            ph(true).env_value("{state}/x"),
            format!("{}/x", sep("/g/env/env-n64"))
        );
    }

    #[test]
    fn action_paths() {
        let p = ph(true);
        assert_eq!(
            p.path("{state}/User/a.ini", Path::new("/unused")),
            native(&format!("{}/User/a.ini", "/g/env/env-n64"))
        );
        assert_eq!(p.path("User/a.ini", &p.state), under(&p.state, "User/a.ini"));
    }

    #[test]
    fn windows_quoting() {
        assert_eq!(quote_windows("plain"), "plain");
        assert_eq!(quote_windows(""), "\"\"");
        assert_eq!(quote_windows("C:\\Program Files\\x"), "\"C:\\Program Files\\x\"");
        assert_eq!(quote_windows("a \"b\""), "\"a \\\"b\\\"\"");
        assert_eq!(quote_windows("dir with\\"), "\"dir with\\\\\"");
    }
}

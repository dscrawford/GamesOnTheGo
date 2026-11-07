//! Where things live. On Windows: the token in %APPDATA%\gotg, everything
//! else under %LOCALAPPDATA%\gotg. Elsewhere (the tests, Wine aside) the XDG
//! equivalents -- a directory of its own for the local half, so a Linux
//! machine's emulator state is never mixed with a Windows build's.

use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct Dirs {
    pub api_file: PathBuf,
    pub games: PathBuf,
    pub partial: PathBuf,
    pub locks: PathBuf,
    pub env_root: PathBuf,
    pub firmware: PathBuf,
    /// gotg-saves' journal and archives (Linux: $GOTG_STATE_DIR/saves).
    pub saves: PathBuf,
    /// GOTG_ENV_STATE: `{state}` itself, whatever the environment.
    pub state_override: Option<PathBuf>,
}

impl Dirs {
    pub fn from_env(get: &dyn Fn(&str) -> Option<String>) -> Result<Dirs, String> {
        // Native separators: a path typed with forward slashes still renders as one.
        let var = |k: &str| {
            get(k)
                .filter(|v| !v.is_empty())
                .map(|v| crate::render::native(&v))
        };
        let home = || var("HOME").or_else(|| var("USERPROFILE"));
        let need = |what: &str| format!("cannot tell where {what} lives: set {what}");

        let (config_default, local) = if cfg!(windows) {
            (
                var("APPDATA").map(|p| p.join("gotg")),
                var("LOCALAPPDATA").map(|p| p.join("gotg")),
            )
        } else {
            (
                var("XDG_CONFIG_HOME")
                    .or_else(|| home().map(|h| h.join(".config")))
                    .map(|p| p.join("gotg")),
                var("XDG_DATA_HOME")
                    .or_else(|| home().map(|h| h.join(".local").join("share")))
                    .map(|p| p.join("gotg-launch")),
            )
        };
        let config = var("GOTG_CONFIG_DIR")
            .or(config_default)
            .ok_or_else(|| need(if cfg!(windows) { "APPDATA" } else { "HOME" }))?;
        let local = var("GOTG_LOCAL_DIR")
            .or(local)
            .ok_or_else(|| need(if cfg!(windows) { "LOCALAPPDATA" } else { "HOME" }))?;
        let games = var("GOTG_GAMES").unwrap_or_else(|| local.join("games"));
        Ok(Dirs {
            api_file: var("GOTG_API_FILE").unwrap_or_else(|| config.join("api.json")),
            partial: games.join(".gotg-partial"),
            games,
            locks: local.join("locks"),
            env_root: local.join("env"),
            firmware: var("GOTG_FIRMWARE_DIR").unwrap_or_else(|| local.join("firmware")),
            saves: local.join("saves"),
            state_override: var("GOTG_ENV_STATE"),
        })
    }

    pub fn from_process() -> Result<Dirs, String> {
        Dirs::from_env(&|k| std::env::var(k).ok())
    }

    /// `{state}` for an environment.
    pub fn state(&self, env_name: &str) -> PathBuf {
        self.state_override
            .clone()
            .unwrap_or_else(|| self.env_root.join(env_name))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn dirs(pairs: &[(&str, &str)]) -> Dirs {
        let m: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        Dirs::from_env(&|k| m.get(k).cloned()).expect("dirs")
    }

    #[cfg(not(windows))]
    #[test]
    fn xdg_defaults() {
        let d = dirs(&[("HOME", "/h")]);
        assert_eq!(d.api_file, PathBuf::from("/h/.config/gotg/api.json"));
        assert_eq!(d.games, PathBuf::from("/h/.local/share/gotg-launch/games"));
        assert_eq!(
            d.partial,
            PathBuf::from("/h/.local/share/gotg-launch/games/.gotg-partial")
        );
        assert_eq!(
            d.state("env-n64"),
            PathBuf::from("/h/.local/share/gotg-launch/env/env-n64")
        );
    }

    #[test]
    fn overrides() {
        let d = dirs(&[
            ("HOME", "/h"),
            ("APPDATA", "/a"),
            ("LOCALAPPDATA", "/l"),
            ("GOTG_CONFIG_DIR", "/c"),
            ("GOTG_GAMES", "/g"),
            ("GOTG_ENV_STATE", "/s"),
        ]);
        assert_eq!(d.api_file, PathBuf::from("/c").join("api.json"));
        assert_eq!(d.games, PathBuf::from("/g"));
        assert_eq!(d.state("env-n64"), PathBuf::from("/s"));
        assert_eq!(dirs(&[("HOME", "/h"), ("GOTG_LOCAL_DIR", "/l2")]).saves, PathBuf::from("/l2").join("saves"));
    }
}

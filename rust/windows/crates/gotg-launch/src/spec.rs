//! `spec.json`: the library spec with its `windows` section (docs/windows.md).
//! Everything is checked here, actions included, so a launch that starts
//! applying a bundle has already proven it can apply all of it.

use std::collections::BTreeMap;
use std::path::Path;

use serde::Deserialize;
use serde_json::Value;

use crate::validate;

#[derive(Debug, Clone, Deserialize)]
pub struct Spec {
    pub version: u32,
    #[serde(default)]
    pub server: String,
    #[serde(default)]
    pub files_urls: Vec<String>,
    pub attr: String,
    #[serde(default)]
    pub variant: String,
    pub game: Game,
    pub windows: Windows,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Game {
    pub id: String,
    pub platform: String,
    #[serde(default)]
    pub handler: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub files: Vec<Member>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Member {
    pub name: String,
    #[serde(default)]
    pub size_bytes: Option<u64>,
    #[serde(default)]
    pub sha256: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ConfigRoot {
    State,
    Emulator,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Keys {
    pub into: String,
    #[serde(default)]
    pub files: Vec<String>,
    /// The platform whose server directory holds them, when not the game's.
    #[serde(default)]
    pub platform: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Firmware {
    pub into: String,
    pub file: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Windows {
    pub version: u32,
    pub name: String,
    #[serde(default)]
    pub title: Option<String>,
    pub exe: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub fullscreen_flag: Option<String>,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    #[serde(default = "default_root")]
    pub config_root: ConfigRoot,
    #[serde(default)]
    pub config_files: BTreeMap<String, String>,
    #[serde(default, deserialize_with = "actions")]
    pub actions: Vec<Action>,
    #[serde(default)]
    pub saves: Vec<String>,
    #[serde(default)]
    pub excludes: Vec<String>,
    #[serde(default)]
    pub states: Option<Value>,
    #[serde(default)]
    pub saves_dirs: Vec<String>,
    #[serde(default)]
    pub keys: Option<Keys>,
    #[serde(default)]
    pub firmware: Option<Firmware>,
}

fn default_root() -> ConfigRoot {
    ConfigRoot::State
}

/// One every-launch edit (docs/windows.md, "Actions"). Paths may hold
/// placeholders; a relative one is under `{state}`, except `copy.from`, which
/// is under `{bundle}`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    Ini {
        file: String,
        section: String,
        key: String,
        value: String,
    },
    Mkdir {
        path: String,
    },
    Copy {
        from: String,
        to: String,
        once: bool,
    },
}

fn actions<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Vec<Action>, D::Error> {
    let raw: Vec<Value> = Vec::deserialize(d)?;
    raw.iter()
        .enumerate()
        .map(|(i, v)| parse_action(v).map_err(|e| format!("action {}: {e}", i + 1)))
        .collect::<Result<_, _>>()
        .map_err(serde::de::Error::custom)
}

fn field(obj: &serde_json::Map<String, Value>, kind: &str, name: &str) -> Result<String, String> {
    match obj.get(name) {
        Some(Value::String(s)) => Ok(s.clone()),
        // A number or boolean in an ini value is what the Nix side writes for
        // `value = 1;`; it means the text.
        Some(Value::Number(n)) => Ok(n.to_string()),
        Some(Value::Bool(b)) => Ok(if *b { "True" } else { "False" }.to_string()),
        Some(_) => Err(format!("{kind}.{name} is not a string")),
        None => Err(format!("{kind} has no {name}")),
    }
}

pub fn parse_action(v: &Value) -> Result<Action, String> {
    let obj = v.as_object().ok_or("not an object")?;
    if obj.len() != 1 {
        return Err(format!(
            "an action is an object with one key, this has {}",
            obj.len()
        ));
    }
    let (kind, body) = obj.iter().next().ok_or("empty action")?;
    let body = body.as_object().ok_or_else(|| format!("{kind}: not an object"))?;
    let known: &[&str] = match kind.as_str() {
        "ini" => &["file", "section", "key", "value"],
        "mkdir" => &["path"],
        "copy" => &["from", "to", "once"],
        other => {
            return Err(format!(
                "unknown action '{other}' (this gotg.exe knows ini, mkdir, copy; \
                 the bundle needs a newer gotg.exe)"
            ));
        }
    };
    if let Some(extra) = body.keys().find(|k| !known.contains(&k.as_str())) {
        return Err(format!("{kind}: unknown field '{extra}'"));
    }
    Ok(match kind.as_str() {
        "ini" => Action::Ini {
            file: field(body, kind, "file")?,
            section: field(body, kind, "section")?,
            key: field(body, kind, "key")?,
            value: field(body, kind, "value")?,
        },
        "mkdir" => Action::Mkdir {
            path: field(body, kind, "path")?,
        },
        _ => Action::Copy {
            from: field(body, kind, "from")?,
            to: field(body, kind, "to")?,
            once: match body.get("once") {
                None | Some(Value::Null) => false,
                Some(Value::Bool(b)) => *b,
                Some(_) => return Err("copy.once is not a boolean".into()),
            },
        },
    })
}

impl Spec {
    pub fn parse(text: &str) -> Result<Spec, String> {
        let spec: Spec = serde_json::from_str(text).map_err(|e| {
            if e.to_string().contains("missing field `windows`") {
                "this spec has no \"windows\" section: it is a Linux spec, not a Windows bundle's".to_string()
            } else {
                e.to_string()
            }
        })?;
        spec.check()?;
        Ok(spec)
    }

    pub fn load(path: &Path) -> Result<Spec, String> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| format!("cannot read the spec at {}: {e}", path.display()))?;
        Spec::parse(&text).map_err(|e| format!("{}: {e}", path.display()))
    }

    fn check(&self) -> Result<(), String> {
        if self.version != 1 {
            return Err(format!("not a version 1 spec (version {})", self.version));
        }
        let w = &self.windows;
        if w.version != 1 {
            return Err(format!(
                "windows section version {} (this gotg.exe reads 1; the bundle needs a newer gotg.exe)",
                w.version
            ));
        }
        validate::attr(&self.attr)?;
        validate::id(&self.game.id)?;
        validate::platform(&self.game.platform)?;
        if !self.variant.is_empty()
            && !self.variant.bytes().enumerate().all(|(i, c)| {
                c.is_ascii_lowercase() || c.is_ascii_digit() || (i > 0 && (c == b'_' || c == b'-'))
            })
        {
            return Err(format!("invalid variant name: {}", self.variant));
        }
        for m in &self.game.files {
            validate::filename(&m.name)?;
            if let Some(sha) = &m.sha256
                && !(sha.len() == 64
                    && sha
                        .bytes()
                        .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()))
            {
                return Err(format!("invalid sha256 in catalog for {}", m.name));
            }
        }
        // The state directory is named for it.
        validate::filename(&w.name)?;
        if w.name.contains('/') {
            return Err(format!("invalid environment name: {}", w.name));
        }
        validate::relative("exe", &w.exe)?;
        for (rel, src) in &w.config_files {
            validate::relative("config_files", rel)?;
            validate::relative("config_files", src)?;
        }
        for d in &w.saves_dirs {
            validate::relative("saves_dirs", d)?;
        }
        if let Some(k) = &w.keys {
            validate::relative("keys.into", &k.into)?;
            if let Some(p) = &k.platform {
                validate::platform(p)?;
            }
        }
        if let Some(f) = &w.firmware {
            validate::relative("firmware.into", &f.into)?;
            if f.file.contains('/') {
                return Err(format!("firmware.file is a name, not a path: {}", f.file));
            }
            validate::filename(&f.file)?;
        }
        for k in w.env.keys() {
            if k.is_empty() || k.contains('=') || k.contains('\0') {
                return Err(format!("invalid environment variable name: {k:?}"));
            }
        }
        Ok(())
    }

    /// What the person sees: the facet's title, the catalog's, or the id.
    pub fn title(&self) -> &str {
        if !self.game.title.is_empty() {
            &self.game.title
        } else if let Some(t) = self.windows.title.as_deref().filter(|t| !t.is_empty()) {
            t
        } else {
            &self.game.id
        }
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use serde_json::json;

    pub fn sample() -> Value {
        json!({
            "version": 1,
            "server": "https://gotg.example",
            "files_urls": ["https://files.example"],
            "attr": "env-n64",
            "variant": "",
            "output": "n64.usa.donkey_kong_64",
            "game": {
                "id": "usa.donkey_kong_64", "platform": "n64", "handler": "single_file",
                "title": "Donkey Kong 64",
                "files": [{"name": "Donkey Kong 64 (USA).z64", "size_bytes": 3, "sha256": null}]
            },
            "windows": {
                "version": 1, "name": "env-n64", "exe": "emulator/ares.exe",
                "args": ["{fullscreen}", "--setting", "Paths/Saves={state}/saves/", "{target}"],
                "fullscreen_flag": "--fullscreen",
                "env": {}, "config_root": "state",
                "config_files": {"config/Ares/settings.bml": "config/config/Ares/settings.bml"},
                "actions": [{"ini": {"file": "{state}/a.ini", "section": "Core", "key": "K", "value": "V"}}],
                "saves": ["saves/**"], "excludes": [], "states": null,
                "saves_dirs": ["saves"], "keys": null, "firmware": null,
                "native_port": false, "game_version_min": null, "game_version_max": null
            }
        })
    }

    #[test]
    fn parses_the_documented_shape() {
        let spec = Spec::parse(&sample().to_string()).expect("parse");
        assert_eq!(spec.windows.exe, "emulator/ares.exe");
        assert_eq!(spec.windows.config_root, ConfigRoot::State);
        assert_eq!(spec.windows.actions.len(), 1);
        assert_eq!(spec.title(), "Donkey Kong 64");
        assert_eq!(spec.game.files[0].size_bytes, Some(3));
    }

    #[test]
    fn a_linux_spec_is_named_as_one() {
        let mut v = sample();
        v.as_object_mut().map(|o| o.remove("windows"));
        let err = Spec::parse(&v.to_string()).expect_err("should fail");
        assert!(err.contains("no \"windows\" section"), "{err}");
    }

    #[test]
    fn unknown_action_is_an_error() {
        let mut v = sample();
        v["windows"]["actions"] = json!([{"mkdir": {"path": "x"}}, {"registry": {"key": "x"}}]);
        let err = Spec::parse(&v.to_string()).expect_err("should fail");
        assert!(
            err.contains("action 2") && err.contains("unknown action 'registry'"),
            "{err}"
        );
    }

    #[test]
    fn actions_parse() {
        assert_eq!(
            parse_action(&json!({"copy": {"from": "config/a", "to": "{state}/a", "once": true}})),
            Ok(Action::Copy {
                from: "config/a".into(),
                to: "{state}/a".into(),
                once: true
            })
        );
        assert_eq!(
            parse_action(&json!({"ini": {"file": "f", "section": "S", "key": "k", "value": false}})),
            Ok(Action::Ini {
                file: "f".into(),
                section: "S".into(),
                key: "k".into(),
                value: "False".into()
            })
        );
        assert!(parse_action(&json!({"mkdir": {"path": "a"}, "copy": {}})).is_err());
        assert!(parse_action(&json!({"mkdir": {"path": "a", "mode": 7}})).is_err());
        assert!(parse_action(&json!({"ini": {"file": "f"}})).is_err());
    }

    #[test]
    fn hostile_paths_are_refused() {
        for (ptr, bad) in [
            ("/windows/exe", json!("../evil.exe")),
            ("/windows/exe", json!("C:/evil.exe")),
            ("/windows/saves_dirs", json!(["../x"])),
            ("/game/id", json!("../x")),
            ("/game/platform", json!("..")),
            ("/windows/name", json!("a/b")),
        ] {
            let mut v = sample();
            *v.pointer_mut(ptr).expect("pointer") = bad;
            assert!(Spec::parse(&v.to_string()).is_err(), "{ptr} accepted");
        }
        let mut v = sample();
        v["windows"]["config_files"] = json!({"../x": "config/a"});
        assert!(Spec::parse(&v.to_string()).is_err());
    }

    #[test]
    fn a_newer_windows_section_is_refused() {
        let mut v = sample();
        v["windows"]["version"] = json!(2);
        assert!(
            Spec::parse(&v.to_string())
                .expect_err("should fail")
                .contains("newer gotg.exe")
        );
    }

    #[test]
    fn config_root_emulator() {
        let mut v = sample();
        v["windows"]["config_root"] = json!("emulator");
        let spec = Spec::parse(&v.to_string()).expect("parse");
        assert_eq!(spec.windows.config_root, ConfigRoot::Emulator);
    }
}

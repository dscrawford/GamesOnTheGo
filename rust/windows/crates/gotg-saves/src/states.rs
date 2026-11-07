//! Save-state slots (`saves-states.sh`): listed, never synced -- a state is
//! tied to the emulator build that wrote it.

use std::fs;
use std::path::Path;

use serde_json::Value;

use crate::{glob, time};

/// The manifest's `states`, as `machinery/lib.nix` writes it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct States {
    /// Under the state directory; `{slot}`, `{slot2}` and `{game}` are filled in.
    pub files: String,
    pub slots: u32,
    pub save: Vec<String>,
    pub load: Vec<String>,
    pub hotkeys: Option<String>,
}

impl States {
    pub(crate) fn from_json(v: &Value) -> Option<States> {
        let obj = v.as_object()?;
        let strings = |k: &str| -> Vec<String> {
            obj.get(k)
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(|s| s.as_str().map(str::to_string)).collect())
                .unwrap_or_default()
        };
        // Like saves_states_slots: 1 to 9, and 1 for anything else.
        let slots = obj
            .get("slots")
            .and_then(Value::as_u64)
            .filter(|n| (1..=9).contains(n))
            .map_or(1, |n| n as u32);
        Some(States {
            files: obj.get("files").and_then(Value::as_str).unwrap_or("").to_string(),
            slots,
            save: strings("save"),
            load: strings("load"),
            hotkeys: obj.get("hotkeys").and_then(Value::as_str).map(str::to_string),
        })
    }
}

/// One slot: the game in it and when it was written, both empty when empty.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Slot {
    /// `slot:<n>`, as the overlay names it.
    pub id: String,
    pub slot: u32,
    pub name: String,
    pub written_at: String,
}

/// Every slot. `game` is the stem of the file being played (`{game}`), or
/// None for any game.
pub fn list(state: &Path, states: &States, game: Option<&str>) -> Vec<Slot> {
    let files = if states.files.split('/').any(|c| c == "..") || states.files.starts_with('/') {
        ""
    } else {
        states.files.as_str()
    };
    let game = game.map_or("*".to_string(), |g| {
        let base = g.rsplit(['/', '\\']).next().unwrap_or(g);
        base.rsplit_once('.').map_or(base, |(stem, _)| stem).to_string()
    });
    (1..=states.slots)
        .map(|slot| {
            let pattern = files
                .replace("{slot}", &slot.to_string())
                .replace("{slot2}", &format!("{slot:02}"))
                .replace("{game}", &game);
            let newest = if pattern.is_empty() {
                None
            } else {
                newest(state, &pattern)
            };
            let (name, written_at) = match newest {
                Some((path, at)) => {
                    let base = path.rsplit('/').next().unwrap_or(&path);
                    let stem = base.rsplit_once('.').map_or(base, |(s, _)| s);
                    (stem.to_string(), time::iso(at))
                }
                None => (String::new(), String::new()),
            };
            Slot {
                id: format!("slot:{slot}"),
                slot,
                name,
                written_at,
            }
        })
        .collect()
}

fn newest(state: &Path, pattern: &str) -> Option<(String, i64)> {
    glob::expand(state, pattern, false)
        .into_iter()
        .filter_map(|m| {
            let md = fs::symlink_metadata(state.join(&m)).ok()?;
            md.is_file()
                .then(|| (m, md.modified().map(time::secs).unwrap_or(0)))
        })
        .max_by(|a, b| a.1.cmp(&b.1).then_with(|| a.0.cmp(&b.0)))
}

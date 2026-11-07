//! The `ini` action: Dolphin's `gotg_ini_set` (env/emulators/dolphin.nix),
//! line for line. Set `key = value` in `[section]`, replacing the key where
//! it is, adding it at the end of the section when it is not, adding the
//! section at the end of the file when that is not there either.

use std::path::Path;

/// Whether `line` sets `key`: the awk's `index($0, key " ") == 1`, widened to
/// `key=` too, which an emulator writing its own file may use.
fn sets(line: &str, key: &str) -> bool {
    line.strip_prefix(key)
        .is_some_and(|rest| rest.starts_with(' ') || rest.starts_with('=') || rest.starts_with('\t'))
}

pub fn set(text: &str, section: &str, key: &str, value: &str) -> String {
    let header = format!("[{section}]");
    let line = format!("{key} = {value}");
    // Keep the file's own line ending; Windows emulators write CRLF.
    let eol = if text.contains("\r\n") { "\r\n" } else { "\n" };
    let mut out: Vec<String> = Vec::new();
    let mut in_section = false;
    let mut written = false;
    for raw in text.lines() {
        let l = raw.strip_suffix('\r').unwrap_or(raw);
        if l.starts_with('[') {
            if in_section && !written {
                out.push(line.clone());
                written = true;
            }
            in_section = l.trim_end() == header;
        }
        if in_section && sets(l, key) {
            out.push(line.clone());
            written = true;
            continue;
        }
        out.push(l.to_string());
    }
    if !written {
        if !in_section {
            out.push(header);
        }
        out.push(line);
    }
    let mut s = out.join(eol);
    s.push_str(eol);
    s
}

/// [`set`] on a file, created with its directories when absent, replaced
/// atomically so an emulator never reads half of it.
pub fn set_file(file: &Path, section: &str, key: &str, value: &str) -> Result<(), String> {
    if let Some(dir) = file.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    let text = match std::fs::read(file) {
        Ok(b) => String::from_utf8_lossy(&b).into_owned(),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(e) => return Err(format!("{}: {e}", file.display())),
    };
    crate::fsx::write_atomic(file, set(&text, section, key, value).as_bytes(), None)
}

#[cfg(test)]
mod tests {
    use super::set;

    #[test]
    fn empty_file_gets_section_and_key() {
        assert_eq!(
            set("", "Core", "SIMDisplay", "False"),
            "[Core]\nSIMDisplay = False\n"
        );
    }

    #[test]
    fn replaces_in_place() {
        let t = "[General]\nA = 1\n[Core]\nSIMDisplay = True\nOther = 2\n";
        assert_eq!(
            set(t, "Core", "SIMDisplay", "False"),
            "[General]\nA = 1\n[Core]\nSIMDisplay = False\nOther = 2\n"
        );
    }

    #[test]
    fn appends_at_the_end_of_its_section() {
        let t = "[Core]\nOther = 2\n[Display]\nX = 1\n";
        assert_eq!(
            set(t, "Core", "K", "V"),
            "[Core]\nOther = 2\nK = V\n[Display]\nX = 1\n"
        );
    }

    #[test]
    fn appends_to_a_last_section() {
        assert_eq!(
            set("[Core]\nOther = 2", "Core", "K", "V"),
            "[Core]\nOther = 2\nK = V\n"
        );
    }

    #[test]
    fn adds_a_missing_section() {
        assert_eq!(set("[A]\nx = 1\n", "B", "K", "V"), "[A]\nx = 1\n[B]\nK = V\n");
    }

    #[test]
    fn same_key_elsewhere_is_untouched_and_prefixes_do_not_match() {
        let t = "[A]\nK = 1\n[B]\nKey = 2\nK=3\n";
        assert_eq!(set(t, "B", "K", "9"), "[A]\nK = 1\n[B]\nKey = 2\nK = 9\n");
    }

    #[test]
    fn an_empty_value_is_written_bare() {
        // Dolphin's "don't update": `UpdateTrack = `.
        assert_eq!(
            set(
                "[AutoUpdate]\nUpdateTrack = beta\n",
                "AutoUpdate",
                "UpdateTrack",
                ""
            ),
            "[AutoUpdate]\nUpdateTrack = \n"
        );
    }

    #[test]
    fn keeps_crlf() {
        assert_eq!(
            set("[Core]\r\nA = 1\r\n", "Core", "A", "2"),
            "[Core]\r\nA = 2\r\n"
        );
    }
}

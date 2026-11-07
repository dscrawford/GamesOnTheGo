//! The shapes a catalog entry and a spec must have before any of it becomes a
//! path: the same contract as the bash client's `validate_*` (common.sh), plus
//! what Windows itself reads as a separator or a device.

/// `^[a-z]{3,5}\.[a-z0-9][a-z0-9_]*$`
pub fn id(s: &str) -> Result<(), String> {
    let ok = (|| {
        let (head, tail) = s.split_once('.')?;
        let head_ok = (3..=5).contains(&head.len()) && head.bytes().all(|b| b.is_ascii_lowercase());
        let mut tail_bytes = tail.bytes();
        let first = tail_bytes.next()?;
        let tail_ok = (first.is_ascii_lowercase() || first.is_ascii_digit())
            && tail_bytes.all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_');
        Some(head_ok && tail_ok)
    })()
    .unwrap_or(false);
    if ok {
        Ok(())
    } else {
        Err(format!("invalid game id: {s}"))
    }
}

/// `^[a-z0-9][a-z0-9_-]{0,15}$`
pub fn platform(s: &str) -> Result<(), String> {
    let b = s.as_bytes();
    let ok = !b.is_empty()
        && b.len() <= 16
        && (b[0].is_ascii_lowercase() || b[0].is_ascii_digit())
        && b[1..]
            .iter()
            .all(|&c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_' || c == b'-');
    if ok {
        Ok(())
    } else {
        Err(format!("invalid platform: {s}"))
    }
}

/// `^env-[a-z0-9][a-z0-9_-]*$`
pub fn attr(s: &str) -> Result<(), String> {
    let ok = s.strip_prefix("env-").is_some_and(|rest| {
        let b = rest.as_bytes();
        !b.is_empty()
            && (b[0].is_ascii_lowercase() || b[0].is_ascii_digit())
            && b.iter()
                .all(|&c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_' || c == b'-')
    });
    if ok {
        Ok(())
    } else {
        Err(format!("invalid environment name: {s}"))
    }
}

/// Characters Windows reads as something other than a name: a separator, a
/// drive or stream (`:`), a wildcard. Refused on every host, so a bundle that
/// works in the Linux tests cannot traverse on Windows.
fn windows_hostile(c: char) -> bool {
    matches!(c, '\\' | ':' | '<' | '>' | '"' | '|' | '?' | '*') || c.is_control()
}

/// A catalog member name: at most 8 `/`-separated segments, none empty, none
/// starting with `.`, nothing a Windows path would reinterpret.
pub fn filename(s: &str) -> Result<(), String> {
    let bad = || Err(format!("invalid file name: {s}"));
    if s.is_empty() || s.len() > 1024 || s.chars().any(windows_hostile) {
        return bad();
    }
    let segments: Vec<&str> = s.split('/').collect();
    if segments.len() > 8 {
        return bad();
    }
    if segments
        .iter()
        .any(|seg| seg.is_empty() || seg.len() > 255 || seg.starts_with('.'))
    {
        return bad();
    }
    Ok(())
}

/// A path relative to some root, from a spec: forward slashes, no `..`, no
/// absolute or drive form. Placeholders are not allowed here; this is for
/// bundle and state paths the manifest names directly.
pub fn relative(what: &str, s: &str) -> Result<(), String> {
    let bad = || Err(format!("{what}: not a plain relative path: {s:?}"));
    if s.is_empty() || s.starts_with('/') || s.chars().any(windows_hostile) {
        return bad();
    }
    if s.split('/')
        .any(|seg| seg.is_empty() || seg == "." || seg == "..")
    {
        return bad();
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids() {
        assert!(id("usa.donkey_kong_64").is_ok());
        assert!(id("eur.f1").is_ok());
        assert!(id("us.x").is_err());
        assert!(id("usa._x").is_err());
        assert!(id("usa.X").is_err());
        assert!(id("usa").is_err());
        assert!(id("usa.a.b").is_err());
    }

    #[test]
    fn platforms_and_attrs() {
        assert!(platform("n64").is_ok());
        assert!(platform("game-cube_2").is_ok());
        assert!(platform("../..").is_err());
        assert!(platform("").is_err());
        assert!(platform("a234567890123456").is_ok());
        assert!(platform("a2345678901234567").is_err());
        assert!(attr("env-n64").is_ok());
        assert!(attr("env-").is_err());
        assert!(attr("n64").is_err());
    }

    #[test]
    fn filenames() {
        assert!(filename("Game (USA).z64").is_ok());
        assert!(filename("code/app.rpx").is_ok());
        assert!(filename("../x").is_err());
        assert!(filename("a/.hidden").is_err());
        assert!(filename("a//b").is_err());
        assert!(filename("a\\..\\b").is_err());
        assert!(filename("C:x").is_err());
        assert!(filename("a/b/c/d/e/f/g/h/i").is_err());
    }

    #[test]
    fn relatives() {
        assert!(relative("t", "config/0/settings.bml").is_ok());
        assert!(relative("t", "/etc/passwd").is_err());
        assert!(relative("t", "a/../b").is_err());
        assert!(relative("t", "a/./b").is_err());
        assert!(relative("t", "C:/x").is_err());
    }
}

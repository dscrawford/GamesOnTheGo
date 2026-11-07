//! Bash's two ways of reading a manifest glob, reproduced: pathname expansion
//! under `globstar nullglob [dotglob]` (which files are saves), and `[[ x ==
//! pattern ]]` (excludes, and what a received bundle may hold).

use std::fs;
use std::path::Path;

/// `[[ text == pattern ]]`: `*` and `?` match `/` too, so `**` is just `*`.
pub fn matches(pattern: &str, text: &str) -> bool {
    let p: Vec<char> = pattern.chars().collect();
    let t: Vec<char> = text.chars().collect();
    match_from(&p, &t)
}

// Backtracking over the last `*` only: linear enough for manifest globs.
fn match_from(p: &[char], t: &[char]) -> bool {
    let (mut pi, mut ti) = (0, 0);
    let mut star: Option<(usize, usize)> = None;
    while ti < t.len() {
        if pi < p.len() {
            match p[pi] {
                '*' => {
                    while pi < p.len() && p[pi] == '*' {
                        pi += 1;
                    }
                    star = Some((pi, ti));
                    continue;
                }
                '?' => {
                    pi += 1;
                    ti += 1;
                    continue;
                }
                '[' => {
                    if let Some((hit, next)) = bracket(p, pi, t[ti]) {
                        if hit {
                            pi = next;
                            ti += 1;
                            continue;
                        }
                    } else if t[ti] == '[' {
                        // An unterminated bracket is a literal `[`.
                        pi += 1;
                        ti += 1;
                        continue;
                    }
                }
                '\\' if pi + 1 < p.len() => {
                    if p[pi + 1] == t[ti] {
                        pi += 2;
                        ti += 1;
                        continue;
                    }
                }
                c => {
                    if c == t[ti] {
                        pi += 1;
                        ti += 1;
                        continue;
                    }
                }
            }
        }
        match star {
            Some((sp, st)) => {
                pi = sp;
                ti = st + 1;
                star = Some((sp, st + 1));
            }
            None => return false,
        }
    }
    p[pi..].iter().all(|&c| c == '*')
}

/// A bracket expression at `p[start] == '['`: whether `c` is in it and the
/// index past its `]`, or None when it never closes.
fn bracket(p: &[char], start: usize, c: char) -> Option<(bool, usize)> {
    let mut i = start + 1;
    let negate = i < p.len() && (p[i] == '!' || p[i] == '^');
    if negate {
        i += 1;
    }
    let mut hit = false;
    let mut first = true;
    loop {
        if i >= p.len() {
            return None;
        }
        let ch = p[i];
        if ch == ']' && !first {
            return Some((hit != negate, i + 1));
        }
        first = false;
        if ch == '['
            && i + 1 < p.len()
            && p[i + 1] == ':'
            && let Some(end) = (i + 2..p.len().saturating_sub(1)).find(|&j| p[j] == ':' && p[j + 1] == ']')
        {
            let class: String = p[i + 2..end].iter().collect();
            hit |= in_class(&class, c);
            i = end + 2;
            continue;
        }
        let lo = if ch == '\\' && i + 1 < p.len() {
            i += 1;
            p[i]
        } else {
            ch
        };
        if i + 2 < p.len() && p[i + 1] == '-' && p[i + 2] != ']' {
            let hi = p[i + 2];
            hit |= lo <= c && c <= hi;
            i += 3;
        } else {
            hit |= lo == c;
            i += 1;
        }
    }
}

fn in_class(class: &str, c: char) -> bool {
    match class {
        "alpha" => c.is_ascii_alphabetic(),
        "digit" => c.is_ascii_digit(),
        "alnum" => c.is_ascii_alphanumeric(),
        "upper" => c.is_ascii_uppercase(),
        "lower" => c.is_ascii_lowercase(),
        "space" => c.is_ascii_whitespace(),
        "xdigit" => c.is_ascii_hexdigit(),
        "punct" => c.is_ascii_punctuation(),
        "blank" => c == ' ' || c == '\t',
        "cntrl" => c.is_ascii_control(),
        "print" => !c.is_control(),
        "graph" => !c.is_control() && c != ' ',
        _ => false,
    }
}

fn has_magic(component: &str) -> bool {
    let mut escaped = false;
    for c in component.chars() {
        if escaped {
            escaped = false;
        } else if c == '\\' {
            escaped = true;
        } else if matches!(c, '*' | '?' | '[') {
            return true;
        }
    }
    false
}

fn unescape(component: &str) -> String {
    let mut out = String::new();
    let mut chars = component.chars();
    while let Some(c) = chars.next() {
        if c == '\\' {
            if let Some(n) = chars.next() {
                out.push(n);
            }
        } else {
            out.push(c);
        }
    }
    out
}

/// Bash's `for match in $glob` run in `root` under `globstar nullglob` (and
/// `dotglob` when asked): every path it yields, relative, `/`-separated, in
/// the order bash yields them. Unquoted, so the glob is split on whitespace
/// first, as bash splits it. Not filtered to files; callers do that.
pub fn expand(root: &Path, glob: &str, dotglob: bool) -> Vec<String> {
    let mut out = Vec::new();
    for word in glob.split([' ', '\t', '\n']).filter(|w| !w.is_empty()) {
        // An absolute glob would name files outside the state directory.
        if word.starts_with('/') || word.ends_with('/') {
            continue;
        }
        let comps: Vec<&str> = word.split('/').filter(|c| !c.is_empty()).collect();
        let mut found = Vec::new();
        step(root, "", &comps, dotglob, &mut found);
        found.sort();
        found.dedup();
        out.extend(found);
    }
    out
}

fn join(prefix: &str, name: &str) -> String {
    if prefix.is_empty() {
        name.to_string()
    } else {
        format!("{prefix}/{name}")
    }
}

fn entries(root: &Path, prefix: &str) -> Vec<(String, bool, bool)> {
    let Ok(dir) = fs::read_dir(root.join(prefix)) else {
        return Vec::new();
    };
    let mut names: Vec<(String, bool, bool)> = dir
        .flatten()
        .filter_map(|e| {
            let name = e.file_name().into_string().ok()?;
            let link = e.file_type().ok()?.is_symlink();
            // A directory through a link counts for `*`, never for `**`.
            let dir = fs::metadata(e.path()).map(|m| m.is_dir()).unwrap_or(false);
            Some((name, dir, link))
        })
        .collect();
    names.sort();
    names
}

fn visible(name: &str, pattern: &str, dotglob: bool) -> bool {
    dotglob || !name.starts_with('.') || pattern.starts_with('.')
}

fn step(root: &Path, prefix: &str, comps: &[&str], dotglob: bool, out: &mut Vec<String>) {
    let Some((&comp, rest)) = comps.split_first() else {
        if !prefix.is_empty() {
            out.push(prefix.to_string());
        }
        return;
    };
    if comp == "**" {
        let mut dirs = vec![prefix.to_string()];
        let mut all = Vec::new();
        walk(root, prefix, dotglob, &mut dirs, &mut all);
        if rest.is_empty() {
            out.extend(all);
        } else {
            for dir in dirs {
                step(root, &dir, rest, dotglob, out);
            }
        }
    } else if !has_magic(comp) {
        let path = join(prefix, &unescape(comp));
        let meta = fs::symlink_metadata(root.join(&path));
        if rest.is_empty() {
            if meta.is_ok() {
                out.push(path);
            }
        } else if fs::metadata(root.join(&path))
            .map(|m| m.is_dir())
            .unwrap_or(false)
        {
            step(root, &path, rest, dotglob, out);
        }
    } else {
        for (name, dir, _) in entries(root, prefix) {
            if !visible(&name, comp, dotglob) || !matches(comp, &name) {
                continue;
            }
            let path = join(prefix, &name);
            if rest.is_empty() {
                out.push(path);
            } else if dir {
                step(root, &path, rest, dotglob, out);
            }
        }
    }
}

// Everything under `prefix` (globstar's `**`), and the directories among it.
fn walk(root: &Path, prefix: &str, dotglob: bool, dirs: &mut Vec<String>, all: &mut Vec<String>) {
    for (name, dir, link) in entries(root, prefix) {
        if !dotglob && name.starts_with('.') {
            continue;
        }
        let path = join(prefix, &name);
        all.push(path.clone());
        if dir && !link {
            dirs.push(path.clone());
            walk(root, &path, dotglob, dirs, all);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn star_crosses_slashes_in_a_comparison() {
        assert!(matches("saves/**", "saves/a/b.srm"));
        assert!(matches("saves/*", "saves/a/b.srm"));
        assert!(matches("saves/*.bs[0-9]", "saves/x/Game.bs3"));
        assert!(!matches("saves/*.bs[0-9]", "saves/Game.bsa"));
        assert!(matches("*.bs[0-9]", "Game.bs1"));
        assert!(matches("[!a]?", "bc"));
        assert!(!matches("[!a]?", "ac"));
        assert!(matches("a[", "a["));
        assert!(matches("[[:digit:]]x", "7x"));
        assert!(matches("\\*", "*"));
        assert!(!matches("\\*", "a"));
        assert!(matches("", ""));
        assert!(!matches("", "a"));
    }
}

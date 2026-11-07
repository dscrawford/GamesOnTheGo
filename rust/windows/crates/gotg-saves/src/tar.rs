//! The tar half of a bundle, written the way the bash client's
//! `tar --format=pax --sort=name --mtime=@0 --owner=0 --group=0` writes it,
//! and read back accepting regular files only -- as `saves_verify_bundle`
//! does, where anything else (a link, a directory, a device) is refused.

use crate::Error;

const BLOCK: usize = 512;
// GNU tar pads the archive to its default record, 20 blocks.
const RECORD: usize = BLOCK * 20;

/// One member: a regular file's path (`/`-separated) and bytes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Member {
    pub path: String,
    pub mode: u32,
    pub data: Vec<u8>,
}

fn octal(field: &mut [u8], value: u64) {
    let digits = field.len() - 1;
    let text = format!("{value:0digits$o}");
    field[..digits].copy_from_slice(&text.as_bytes()[text.len() - digits..]);
    field[digits] = 0;
}

fn header(name: &[u8], size: u64, mode: u32, kind: u8) -> [u8; BLOCK] {
    let mut h = [0u8; BLOCK];
    let n = name.len().min(100);
    h[..n].copy_from_slice(&name[..n]);
    octal(&mut h[100..108], u64::from(mode & 0o7777));
    octal(&mut h[108..116], 0);
    octal(&mut h[116..124], 0);
    octal(&mut h[124..136], size);
    octal(&mut h[136..148], 0);
    h[156] = kind;
    h[257..263].copy_from_slice(b"ustar\0");
    h[263..265].copy_from_slice(b"00");
    h[148..156].fill(b' ');
    let sum: u32 = h.iter().map(|&b| u32::from(b)).sum();
    let text = format!("{sum:06o}\0 ");
    h[148..156].copy_from_slice(text.as_bytes());
    h
}

fn pad(out: &mut Vec<u8>) {
    let rem = out.len() % BLOCK;
    if rem != 0 {
        out.resize(out.len() + BLOCK - rem, 0);
    }
}

// A pax record's length counts its own digits.
fn pax_record(key: &str, value: &str) -> Vec<u8> {
    let body = format!(" {key}={value}\n");
    let mut len = body.len() + 1;
    while format!("{len}").len() + body.len() != len {
        len += 1;
    }
    format!("{len}{body}").into_bytes()
}

/// The archive's bytes: members in the order given, each a regular file.
pub fn write(members: &[Member]) -> Vec<u8> {
    let mut out = Vec::new();
    for m in members {
        let name = m.path.as_bytes();
        if name.len() > 100 || !m.path.is_ascii() {
            let (dir, file) = m.path.rsplit_once('/').unwrap_or((".", &m.path));
            let xname = format!("{dir}/PaxHeaders/{file}");
            let record = pax_record("path", &m.path);
            out.extend_from_slice(&header(xname.as_bytes(), record.len() as u64, 0o644, b'x'));
            out.extend_from_slice(&record);
            pad(&mut out);
        }
        out.extend_from_slice(&header(name, m.data.len() as u64, m.mode, b'0'));
        out.extend_from_slice(&m.data);
        pad(&mut out);
    }
    out.resize(out.len() + 2 * BLOCK, 0);
    let rem = out.len() % RECORD;
    if rem != 0 {
        out.resize(out.len() + RECORD - rem, 0);
    }
    out
}

fn cstr(field: &[u8]) -> &[u8] {
    let end = field.iter().position(|&b| b == 0).unwrap_or(field.len());
    &field[..end]
}

fn number(field: &[u8]) -> Result<u64, Error> {
    if field.first().is_some_and(|b| b & 0x80 != 0) {
        // GNU's base-256 for values octal cannot hold.
        let mut v: u64 = u64::from(field[0] & 0x7f);
        for &b in &field[1..] {
            v = v
                .checked_mul(256)
                .ok_or_else(|| Error::bad("a tar size overflows"))?
                + u64::from(b);
        }
        return Ok(v);
    }
    let text = std::str::from_utf8(cstr(field)).map_err(|_| Error::bad("a tar number is not text"))?;
    let text = text.trim_matches(|c| c == ' ' || c == '\0');
    if text.is_empty() {
        return Ok(0);
    }
    u64::from_str_radix(text, 8).map_err(|_| Error::bad("a tar number is not octal"))
}

fn checksum_ok(h: &[u8]) -> bool {
    let want = match number(&h[148..156]) {
        Ok(v) => v,
        Err(_) => return false,
    };
    let unsigned: u64 = h
        .iter()
        .enumerate()
        .map(|(i, &b)| if (148..156).contains(&i) { 32 } else { u64::from(b) })
        .sum();
    let signed: i64 = h
        .iter()
        .enumerate()
        .map(|(i, &b)| {
            if (148..156).contains(&i) {
                32
            } else {
                i64::from(b as i8)
            }
        })
        .sum();
    want == unsigned || i64::try_from(want).is_ok_and(|w| w == signed)
}

fn pax_path(data: &[u8]) -> Result<Option<String>, Error> {
    let mut rest = data;
    let mut path = None;
    while !rest.is_empty() {
        let sp = rest
            .iter()
            .position(|&b| b == b' ')
            .ok_or_else(|| Error::bad("a pax record has no length"))?;
        let len: usize = std::str::from_utf8(&rest[..sp])
            .ok()
            .and_then(|s| s.parse().ok())
            .ok_or_else(|| Error::bad("a pax record's length is not a number"))?;
        if len <= sp + 1 || len > rest.len() {
            return Err(Error::bad("a pax record runs past its header"));
        }
        let record = &rest[sp + 1..len - 1];
        if let Some(eq) = record.iter().position(|&b| b == b'=')
            && &record[..eq] == b"path"
        {
            let value = String::from_utf8(record[eq + 1..].to_vec())
                .map_err(|_| Error::bad("a member's name is not UTF-8"))?;
            path = Some(value);
        }
        rest = &rest[len..];
    }
    Ok(path)
}

/// Every member of an archive. Anything but a regular file is an error.
pub fn read(bytes: &[u8]) -> Result<Vec<Member>, Error> {
    let mut members = Vec::new();
    let mut at = 0;
    let mut long_name: Option<String> = None;
    while at + BLOCK <= bytes.len() {
        let h = &bytes[at..at + BLOCK];
        at += BLOCK;
        if h.iter().all(|&b| b == 0) {
            break;
        }
        if !checksum_ok(h) {
            return Err(Error::bad("a tar header's checksum is wrong"));
        }
        let size = usize::try_from(number(&h[124..136])?).map_err(|_| Error::bad("a member is too big"))?;
        let end = at.checked_add(size).filter(|&e| e <= bytes.len());
        let end = end.ok_or_else(|| Error::bad("the archive ends inside a member"))?;
        let data = &bytes[at..end];
        at = end.div_ceil(BLOCK) * BLOCK;
        match h[156] {
            b'x' => {
                if let Some(p) = pax_path(data)? {
                    long_name = Some(p);
                }
            }
            b'L' => {
                let name = String::from_utf8(cstr(data).to_vec())
                    .map_err(|_| Error::bad("a member's name is not UTF-8"))?;
                long_name = Some(name);
            }
            // A global header sets defaults GNU tar never writes here.
            b'g' => {}
            b'0' | 0 => {
                let path = match long_name.take() {
                    Some(p) => p,
                    None => {
                        let name = cstr(&h[0..100]);
                        let prefix = if &h[257..262] == b"ustar" {
                            cstr(&h[345..500])
                        } else {
                            &[][..]
                        };
                        let mut full = Vec::new();
                        if !prefix.is_empty() {
                            full.extend_from_slice(prefix);
                            full.push(b'/');
                        }
                        full.extend_from_slice(name);
                        String::from_utf8(full).map_err(|_| Error::bad("a member's name is not UTF-8"))?
                    }
                };
                let mode = u32::try_from(number(&h[100..108])? & 0o7777).unwrap_or(0o644);
                members.push(Member {
                    path,
                    mode,
                    data: data.to_vec(),
                });
            }
            kind => {
                return Err(Error::NotPlainFile {
                    kind: char::from(kind),
                });
            }
        }
    }
    Ok(members)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips_long_and_unicode_names() {
        let members = vec![
            Member {
                path: "saves/a.srm".into(),
                mode: 0o644,
                data: b"hi".to_vec(),
            },
            Member {
                path: format!("saves/{}.srm", "n".repeat(150)),
                mode: 0o600,
                data: vec![7; 1000],
            },
            Member {
                path: "saves/ünï.sav".into(),
                mode: 0o644,
                data: Vec::new(),
            },
        ];
        let bytes = write(&members);
        assert_eq!(bytes.len() % RECORD, 0);
        assert_eq!(read(&bytes).ok(), Some(members));
    }

    #[test]
    fn pax_lengths_count_themselves() {
        let r = pax_record("path", &"x".repeat(91));
        assert_eq!(String::from_utf8_lossy(&r[..3]), "101");
        assert_eq!(r.len(), 101);
    }

    #[test]
    fn a_directory_is_refused() {
        let mut bytes = header(b"saves/", 0, 0o755, b'5').to_vec();
        bytes.resize(4 * BLOCK, 0);
        assert!(matches!(read(&bytes), Err(Error::NotPlainFile { kind: '5' })));
    }
}

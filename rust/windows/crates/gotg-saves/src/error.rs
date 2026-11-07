use std::fmt;
use std::io;

/// Everything that can go wrong, worded as the bash client words it.
#[derive(Debug)]
#[non_exhaustive]
pub enum Error {
    /// A file here could not be read or written.
    Io { what: String, source: io::Error },
    /// The service could not be asked (network, timeout, a 5xx).
    Unreachable { server: String, detail: String },
    /// A 401: the token is revoked or expired; log in again.
    TokenRefused,
    /// The service answered something this client does not expect.
    Http { status: u16, detail: String },
    /// No `Client` was given, and this needs the service.
    NoService,
    /// The environment name is not `env-[a-z0-9][a-z0-9_-]*`.
    BadEnv(String),
    /// The manifest is not the `saves.json` shape.
    BadManifest(String),
    /// The saves come to more than the cap; nothing was uploaded.
    TooLarge {
        total: u64,
        max: u64,
        largest: Vec<(u64, String)>,
    },
    /// A received bundle is not what the service said it was.
    HashMismatch { expected: String, got: String },
    /// A received bundle holds something that is not a plain file.
    NotPlainFile { kind: char },
    /// A received bundle is malformed or holds a path it must not.
    BadBundle(String),
    /// `remote:<n>` names a generation the service no longer keeps.
    NotKept(u64),
    /// `local:<name>` names no archive here.
    NoArchive(String),
    /// A pick that is neither `remote:<n>` nor `local:<archive>`.
    BadPick(String),
}

impl Error {
    pub(crate) fn bad(detail: impl Into<String>) -> Self {
        Error::BadBundle(detail.into())
    }

    pub(crate) fn io(what: impl Into<String>, source: io::Error) -> Self {
        Error::Io {
            what: what.into(),
            source,
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Io { what, source } => write!(f, "{what}: {source}"),
            Error::Unreachable { server, detail } => {
                write!(f, "could not reach the GOTG service at {server}: {detail}")
            }
            Error::TokenRefused => write!(
                f,
                "the GOTG service refused the token; it may be revoked or expired; log in again"
            ),
            Error::Http { status, detail } => write!(f, "the GOTG service answered HTTP {status}: {detail}"),
            Error::NoService => write!(f, "no service configured"),
            Error::BadEnv(name) => write!(f, "invalid environment name: {name}"),
            Error::BadManifest(why) => write!(f, "the saves manifest is not usable: {why}"),
            Error::TooLarge { total, max, largest } => {
                write!(
                    f,
                    "the saves come to {total} bytes, over the {max} byte limit; nothing was uploaded"
                )?;
                for (size, path) in largest {
                    write!(f, "\n  {size:>10}  {path}")?;
                }
                Ok(())
            }
            Error::HashMismatch { expected, got } => write!(
                f,
                "the bundle downloaded is not the one the service said it was (expected {expected}, \
                 got {got}); nothing here has been touched"
            ),
            Error::NotPlainFile { kind } => write!(
                f,
                "the bundle holds something that is not a plain file (type {kind}); refusing to extract it"
            ),
            Error::BadBundle(why) => write!(f, "refusing the bundle: {why}; nothing here has been touched"),
            Error::NotKept(generation) => {
                write!(f, "generation {generation} is not kept by the service any more")
            }
            Error::NoArchive(name) => write!(f, "no archive {name} here"),
            Error::BadPick(pick) => write!(f, "not a save: {pick} (remote:<generation> or local:<archive>)"),
        }
    }
}

impl std::error::Error for Error {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Error::Io { source, .. } => Some(source),
            _ => None,
        }
    }
}

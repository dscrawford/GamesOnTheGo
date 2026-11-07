//! The saves store as the client sees it (`remote.sh`): PUT a bundle with
//! its parent's hash, GET the head or a kept generation, its meta, history.

use std::io::Read;
use std::time::Duration;

use serde_json::Value;

use crate::Error;

/// Where the service is and who this machine is to it. The launcher owns
/// config and login; this is only what a request needs.
#[derive(Clone)]
pub struct Client {
    /// The service's base URL, e.g. `https://gotg-api.example.net`.
    pub server: String,
    /// The bearer token `gotg.exe login` stored.
    pub token: String,
    /// This machine as a person names it (api.json's `name`), sent as
    /// `X-Gotg-Device`. Empty falls back to a random id kept in the saves home.
    pub device: String,
}

impl std::fmt::Debug for Client {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Client")
            .field("server", &self.server)
            .field("token", &"<redacted>")
            .field("device", &self.device)
            .finish()
    }
}

/// One generation as the service describes it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Meta {
    pub generation: u64,
    pub hash: String,
    pub size: u64,
    /// The machine that pushed it; empty when unknown.
    pub device: String,
    pub written_at: String,
}

impl Meta {
    pub(crate) fn from_json(v: &Value) -> Result<Meta, Error> {
        let generation = v["generation"].as_u64().ok_or_else(|| Error::Http {
            status: 200,
            detail: "meta without a generation".into(),
        })?;
        let hash = v["hash"]
            .as_str()
            .filter(|h| h.len() == 64 && h.bytes().all(|b| b.is_ascii_hexdigit()))
            .ok_or_else(|| Error::Http {
                status: 200,
                detail: "meta without a hash".into(),
            })?;
        Ok(Meta {
            generation,
            hash: hash.to_string(),
            size: v["size"].as_u64().unwrap_or(0),
            device: v["device"].as_str().unwrap_or("").to_string(),
            written_at: v["written_at"].as_str().unwrap_or("").to_string(),
        })
    }
}

/// What a PUT decided.
pub(crate) enum Put {
    Published(Meta),
    /// 409: the head moved on; this is it.
    Refused(Meta),
}

pub(crate) struct Remote<'a> {
    client: &'a Client,
    agent: ureq::Agent,
    device: String,
}

fn detail(body: &[u8]) -> String {
    serde_json::from_slice::<Value>(body)
        .ok()
        .and_then(|v| v["error"].as_str().map(str::to_string))
        .unwrap_or_else(|| "no detail".into())
}

impl<'a> Remote<'a> {
    pub fn new(client: &'a Client, device: String) -> Self {
        let agent = ureq::Agent::config_builder()
            .timeout_connect(Some(Duration::from_secs(10)))
            .timeout_global(Some(Duration::from_secs(120)))
            .http_status_as_error(false)
            .build()
            .into();
        Remote {
            client,
            agent,
            device,
        }
    }

    fn url(&self, path: &str) -> String {
        format!("{}/{path}", self.client.server.trim_end_matches('/'))
    }

    fn unreachable(&self, e: impl std::fmt::Display) -> Error {
        Error::Unreachable {
            server: self.client.server.clone(),
            detail: e.to_string(),
        }
    }

    fn get(&self, path: &str, limit: u64) -> Result<(u16, ureq::http::HeaderMap, Vec<u8>), Error> {
        let mut resp = self
            .agent
            .get(self.url(path))
            .header("Authorization", format!("Bearer {}", self.client.token))
            .call()
            .map_err(|e| self.unreachable(e))?;
        let status = resp.status().as_u16();
        let headers = resp.headers().clone();
        let mut body = Vec::new();
        // One past the cap, so an oversized answer is refused rather than cut.
        resp.body_mut()
            .as_reader()
            .take(limit + 1)
            .read_to_end(&mut body)
            .map_err(|e| self.unreachable(e))?;
        if body.len() as u64 > limit {
            return Err(Error::bad(format!("the service sent more than {limit} bytes")));
        }
        Ok((status, headers, body))
    }

    /// `GET /saves/<env>/meta`: the head, or None when nothing was pushed.
    pub fn meta(&self, env: &str) -> Result<Option<Meta>, Error> {
        let (status, _, body) = self.get(&format!("saves/{env}/meta"), 1 << 20)?;
        match status {
            200 => {
                let v: Value = serde_json::from_slice(&body).map_err(|e| Error::Http {
                    status,
                    detail: e.to_string(),
                })?;
                Meta::from_json(&v).map(Some)
            }
            404 => Ok(None),
            401 => Err(Error::TokenRefused),
            _ => Err(self.unreachable(format!("HTTP {status}"))),
        }
    }

    /// `GET /saves/<env>/history`: the kept generations, newest first.
    pub fn history(&self, env: &str) -> Result<Vec<Meta>, Error> {
        let (status, _, body) = self.get(&format!("saves/{env}/history"), 4 << 20)?;
        match status {
            200 => {
                let v: Value = serde_json::from_slice(&body).map_err(|e| Error::Http {
                    status,
                    detail: e.to_string(),
                })?;
                let list = v["generations"].as_array().ok_or_else(|| Error::Http {
                    status,
                    detail: "history without generations".into(),
                })?;
                list.iter().map(Meta::from_json).collect()
            }
            401 => Err(Error::TokenRefused),
            _ => Err(self.unreachable(format!("HTTP {status}"))),
        }
    }

    /// The head's bytes (or generation `n`'s), with the generation and hash
    /// the service says they are; None for 404.
    pub fn retrieve(
        &self,
        env: &str,
        generation: Option<u64>,
        max: u64,
    ) -> Result<Option<(u64, String, Vec<u8>)>, Error> {
        let path = match generation {
            Some(n) => format!("saves/{env}/gen/{n}"),
            None => format!("saves/{env}"),
        };
        let (status, headers, body) = self.get(&path, max)?;
        match status {
            200 => {
                let header = |name: &str| headers.get(name).and_then(|v| v.to_str().ok()).map(str::trim);
                let generation = header("x-gotg-generation").and_then(|g| g.parse().ok());
                let hash = header("x-gotg-hash").map(str::to_string);
                match (generation, hash) {
                    (Some(g), Some(h)) => Ok(Some((g, h, body))),
                    _ => Err(Error::Http {
                        status,
                        detail: "a bundle without its generation headers".into(),
                    }),
                }
            }
            404 => Ok(None),
            401 => Err(Error::TokenRefused),
            _ => Err(self.unreachable(format!("HTTP {status}"))),
        }
    }

    /// `PUT /saves/<env>[?force=1]` with `X-Gotg-Parent`.
    pub fn save(&self, env: &str, bundle: &[u8], parent: &str, force: bool) -> Result<Put, Error> {
        let query = if force { "?force=1" } else { "" };
        let mut resp = self
            .agent
            .put(self.url(&format!("saves/{env}{query}")))
            .header("Authorization", format!("Bearer {}", self.client.token))
            .header("X-Gotg-Parent", parent)
            .header("X-Gotg-Device", &self.device)
            .header("Content-Type", "application/octet-stream")
            .send(bundle)
            .map_err(|e| self.unreachable(e))?;
        let status = resp.status().as_u16();
        let mut body = Vec::new();
        resp.body_mut()
            .as_reader()
            .take(1 << 20)
            .read_to_end(&mut body)
            .map_err(|e| self.unreachable(e))?;
        let meta = || -> Result<Meta, Error> {
            let v: Value = serde_json::from_slice(&body).map_err(|e| Error::Http {
                status,
                detail: e.to_string(),
            })?;
            Meta::from_json(&v)
        };
        match status {
            200 => Ok(Put::Published(meta()?)),
            409 => Ok(Put::Refused(meta()?)),
            401 => Err(Error::TokenRefused),
            _ => Err(Error::Http {
                status,
                detail: detail(&body),
            }),
        }
    }
}

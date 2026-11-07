//! The one GOTG service: api.json (the Linux client's format, `{url, token,
//! name?}`, merged rather than rewritten), and an HTTP agent that sends the
//! token as a Bearer header -- never in a URL.

use std::path::Path;
use std::time::Duration;

use serde_json::{Map, Value};
use ureq::Agent;

use crate::fsx;

#[derive(Debug, Clone)]
pub struct Api {
    pub url: String,
    pub token: String,
}

/// A token over cleartext is a token given away; loopback is exempt.
pub fn url_is_private_or_tls(url: &str) -> bool {
    if url.starts_with("https://") {
        return true;
    }
    ["http://127.0.0.1", "http://localhost", "http://[::1]"]
        .iter()
        .any(|base| {
            url.strip_prefix(base)
                .is_some_and(|rest| rest.is_empty() || rest.starts_with(':') || rest.starts_with('/'))
        })
}

/// Whether api.json exists and names a service: the "logged in" question.
pub fn load(file: &Path) -> Result<Option<Api>, String> {
    let text = match std::fs::read_to_string(file) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("{}: {e}", file.display())),
    };
    fsx::check_secret_perms(file)?;
    let v: Value = serde_json::from_str(&text).map_err(|_| {
        format!(
            "{} is not JSON -- move it aside and run: gotg login",
            file.display()
        )
    })?;
    let url = v.get("url").and_then(Value::as_str).unwrap_or_default();
    let token = v.get("token").and_then(Value::as_str).unwrap_or_default();
    if url.is_empty() || token.is_empty() {
        return Ok(None);
    }
    Ok(Some(Api {
        url: url.trim_end_matches('/').to_string(),
        token: token.to_string(),
    }))
}

/// Merge `{url, token[, name]}` into whatever api.json already holds.
pub fn save(file: &Path, url: &str, token: &str, name: Option<&str>) -> Result<(), String> {
    let mut obj: Map<String, Value> = match std::fs::read_to_string(file) {
        Ok(t) => match serde_json::from_str(&t) {
            Ok(Value::Object(m)) => m,
            _ => {
                return Err(format!(
                    "{} is not a JSON object -- move it aside and run: gotg login",
                    file.display()
                ));
            }
        },
        Err(_) => Map::new(),
    };
    obj.insert("url".into(), url.into());
    obj.insert("token".into(), token.into());
    if let Some(n) = name.filter(|n| !n.is_empty()) {
        obj.insert("name".into(), n.into());
    }
    if let Some(dir) = file.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700));
        }
    }
    let mut bytes = serde_json::to_vec_pretty(&Value::Object(obj)).map_err(|e| e.to_string())?;
    bytes.push(b'\n');
    fsx::write_atomic(file, &bytes, Some(0o600))
}

/// curl's defaults as the client uses them: no redirects followed (curl
/// without -L), a 10 s connect timeout, statuses returned rather than raised.
pub fn agent() -> Agent {
    Agent::config_builder()
        .http_status_as_error(false)
        .max_redirects(0)
        .timeout_connect(Some(Duration::from_secs(10)))
        .user_agent(concat!("gotg-launch/", env!("CARGO_PKG_VERSION")))
        .build()
        .into()
}

pub fn bearer(token: &str) -> String {
    format!("Bearer {token}")
}

/// GET with the token, one global timeout.
pub fn get(
    agent: &Agent,
    token: &str,
    url: &str,
    timeout: Duration,
) -> Result<ureq::http::Response<ureq::Body>, ureq::Error> {
    agent
        .get(url)
        .header("Authorization", bearer(token))
        .config()
        .timeout_global(Some(timeout))
        .build()
        .call()
}

/// `/` and every byte a URL path segment cannot carry, %-encoded (jq's @uri).
pub fn uri_segment(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b'~') {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// A member name as the URL tail: each segment encoded, the slashes kept.
pub fn uri_path(name: &str) -> String {
    name.split('/').map(uri_segment).collect::<Vec<_>>().join("/")
}

/// The byte hosts, in the spec's order (the catalog named them), deduplicated;
/// `GOTG_FILES_URL` names one and hides the rest; none at all is the service.
pub fn files_hosts(spec_urls: &[String], service: &str, override_url: Option<&str>) -> Vec<String> {
    let http = |u: &&str| u.starts_with("http://") || u.starts_with("https://");
    if let Some(u) = override_url.filter(http) {
        return vec![u.trim_end_matches('/').to_string()];
    }
    let mut out: Vec<String> = Vec::new();
    for u in spec_urls.iter().map(String::as_str).filter(http) {
        let u = u.trim_end_matches('/').to_string();
        if !out.contains(&u) {
            out.push(u);
        }
    }
    if out.is_empty() {
        out.push(service.trim_end_matches('/').to_string());
    }
    out
}

/// The first host whose /healthz answers within a short connect timeout (a
/// tailnet address is a black hole from outside it), else the first, whose
/// failure then names what could not be reached.
pub fn pick_host(agent: &Agent, token: &str, hosts: &[String]) -> String {
    for h in hosts {
        let ok = agent
            .get(format!("{h}/healthz"))
            .header("Authorization", bearer(token))
            .config()
            .timeout_connect(Some(Duration::from_secs(3)))
            .timeout_global(Some(Duration::from_secs(10)))
            .build()
            .call()
            .is_ok_and(|r| r.status().is_success());
        if ok {
            return h.clone();
        }
    }
    hosts.first().cloned().unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cleartext_only_on_loopback() {
        assert!(url_is_private_or_tls("https://gotg.example"));
        assert!(url_is_private_or_tls("http://127.0.0.1:8080"));
        assert!(url_is_private_or_tls("http://localhost"));
        assert!(url_is_private_or_tls("http://[::1]/x"));
        assert!(!url_is_private_or_tls("http://localhost.evil.com"));
        assert!(!url_is_private_or_tls("http://gotg.example"));
    }

    #[test]
    fn encodes_like_jq_uri() {
        assert_eq!(uri_path("Game (USA) [!].z64"), "Game%20%28USA%29%20%5B%21%5D.z64");
        assert_eq!(uri_path("code/app.rpx"), "code/app.rpx");
        assert_eq!(uri_segment("a/b"), "a%2Fb");
    }

    #[test]
    fn hosts() {
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        assert_eq!(
            files_hosts(
                &s(&["https://a/", "https://a", "ftp://x", "https://b"]),
                "https://svc",
                None
            ),
            s(&["https://a", "https://b"])
        );
        assert_eq!(files_hosts(&[], "https://svc/", None), s(&["https://svc"]));
        assert_eq!(
            files_hosts(&s(&["https://a"]), "https://svc", Some("http://127.0.0.1:9")),
            s(&["http://127.0.0.1:9"])
        );
    }

    #[test]
    fn api_json_merges_and_is_private() {
        let dir = std::env::temp_dir().join(format!("gotg-api-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let file = dir.join("api.json");
        assert!(load(&file).expect("absent is fine").is_none());
        std::fs::create_dir_all(&dir).expect("mkdir");
        std::fs::write(&file, r#"{"keep": 1, "url": "old"}"#).expect("write");
        save(&file, "http://127.0.0.1:1", "tok", Some("me")).expect("save");
        let v: Value = serde_json::from_str(&std::fs::read_to_string(&file).expect("read")).expect("json");
        assert_eq!(v["keep"], 1);
        assert_eq!(v["name"], "me");
        let api = load(&file).expect("load").expect("some");
        assert_eq!(api.url, "http://127.0.0.1:1");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&file, std::fs::Permissions::from_mode(0o644)).expect("chmod");
            assert!(load(&file).expect_err("should fail").contains("chmod 600"));
        }
        let _ = std::fs::remove_dir_all(&dir);
    }
}

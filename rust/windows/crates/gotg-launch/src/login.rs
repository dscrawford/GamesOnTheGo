//! `gotg login`: the bash client's (config.sh `cmd_login`) -- a claim link,
//! or a server and a token -- plus the device flow docs/windows.md asks for,
//! which is the default at a terminal: nobody double-clicking an exe has a
//! token to paste. Every route ends the same way: the token is checked
//! against the service, then merged into api.json.

use std::io::{BufRead, IsTerminal, Write};
use std::path::Path;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use crate::api::{self, url_is_private_or_tls};

pub const DEFAULT_SERVER: &str = "https://gotg.dcraw.net";

#[derive(Debug, Default, Clone)]
pub struct LoginArgs {
    pub server: Option<String>,
    pub claim: Option<String>,
    /// Paste a token rather than approve in a browser.
    pub token: bool,
}

pub fn usage() -> &'static str {
    "usage: gotg login [--server URL] [--token | --claim URL]"
}

/// The shape every bearer token has.
pub fn check_token(t: &str) -> Result<(), String> {
    if !t.is_empty()
        && t.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._~+/=-".contains(&b))
    {
        Ok(())
    } else {
        Err("token contains characters no bearer token uses".into())
    }
}

/// A claim link taken apart: (service url, code).
pub fn claim_parts(claim: &str) -> Result<(String, String), String> {
    let claim = claim.trim_end_matches('/');
    let not = || format!("not a claim url: {claim}");
    if !(claim.starts_with("http://") || claim.starts_with("https://")) {
        return Err(not());
    }
    let (url, code) = claim.split_once("/claim/").ok_or_else(not)?;
    if !url_is_private_or_tls(url) {
        return Err(format!(
            "that claim link is http, which hands the token to the network: {url}"
        ));
    }
    // A link that has been through a chat client arrives with tracking on it.
    let code = code.split('?').next().unwrap_or_default();
    let ok = code.strip_prefix("gotgi_").is_some_and(|rest| {
        (40..=50).contains(&rest.len())
            && rest
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    });
    if !ok {
        return Err(not());
    }
    Ok((url.to_string(), code.to_string()))
}

fn prompt_line(prompt: &str, default: &str) -> Result<String, String> {
    if !std::io::stdin().is_terminal() {
        return Err(format!("no terminal to ask: {prompt}(give it with --server)"));
    }
    eprint!("{prompt}");
    let _ = std::io::stderr().flush();
    let mut s = String::new();
    std::io::stdin()
        .lock()
        .read_line(&mut s)
        .map_err(|e| e.to_string())?;
    let s = s.trim();
    Ok(if s.is_empty() {
        default.to_string()
    } else {
        s.to_string()
    })
}

/// A token from the terminal without echo, or from a pipe -- a script handing
/// one over, the one way that never shows it.
fn read_token() -> Result<String, String> {
    let t = if std::io::stdin().is_terminal() {
        rpassword::prompt_password("Token: ").map_err(|e| e.to_string())?
    } else {
        let mut s = String::new();
        std::io::stdin()
            .lock()
            .read_line(&mut s)
            .map_err(|e| e.to_string())?;
        s
    };
    let t = t.trim().to_string();
    if t.is_empty() {
        return Err("a token is required".into());
    }
    Ok(t)
}

fn read_json(resp: ureq::http::Response<ureq::Body>) -> (u16, Value) {
    let status = resp.status().as_u16();
    let v = resp
        .into_body()
        .with_config()
        .limit(1 << 20)
        .read_json::<Value>()
        .unwrap_or(Value::Null);
    (status, v)
}

fn post_json(agent: &ureq::Agent, url: &str, body: &Value) -> Result<(u16, Value), String> {
    let resp = agent
        .post(url)
        .config()
        .timeout_global(Some(Duration::from_secs(30)))
        .build()
        .send_json(body)
        .map_err(|e| format!("could not reach {url}: {e}"))?;
    Ok(read_json(resp))
}

/// The claim spent: one POST, one token and a name.
fn claim_exchange(agent: &ureq::Agent, url: &str, code: &str) -> Result<(String, String), String> {
    let (status, v) = post_json(agent, &format!("{url}/claim"), &json!({ "code": code }))?;
    if status != 200 {
        let why = v
            .get("error")
            .and_then(Value::as_str)
            .map(str::to_string)
            .unwrap_or_else(|| format!("the service answered {status}"));
        return Err(format!("claim failed: {why}"));
    }
    let token = v.get("token").and_then(Value::as_str).unwrap_or_default();
    if token.is_empty() {
        return Err("the claim reply carried no token".into());
    }
    let name = v.get("name").and_then(Value::as_str).unwrap_or_default();
    Ok((token.to_string(), name.to_string()))
}

fn open_browser(url: &str) {
    if std::env::var_os("GOTG_NO_BROWSER").is_some() {
        return;
    }
    // url.dll takes the URL as one argument: no shell to quote it for.
    #[cfg(windows)]
    let _ = std::process::Command::new("rundll32")
        .args(["url.dll,FileProtocolHandler", url])
        .spawn();
    #[cfg(not(windows))]
    let _ = std::process::Command::new("xdg-open")
        .arg(url)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
}

pub enum Device {
    /// Approved: (token, name).
    Token(String, String),
    /// This deployment has no browser sign-in.
    Unavailable,
}

/// The device flow (routes_oauth.py): a code shown here, approved in a
/// browser, polled for at the interval the service names.
pub fn device_flow(agent: &ureq::Agent, url: &str) -> Result<Device, String> {
    let (status, v) = post_json(agent, &format!("{url}/auth/device"), &json!({}))?;
    if matches!(status, 404 | 405 | 501 | 503) && v.get("device_code").is_none() {
        return Ok(Device::Unavailable);
    }
    if status != 200 {
        return Err(format!("{url}/auth/device answered {status}"));
    }
    let s = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or_default().to_string();
    let (device_code, user_code) = (s("device_code"), s("user_code"));
    let verify = s("verification_uri");
    let complete = s("verification_uri_complete");
    if device_code.is_empty() || user_code.is_empty() {
        return Err(format!("{url}/auth/device answered without a code"));
    }
    let mut interval = v.get("interval").and_then(Value::as_u64).unwrap_or(5).max(1);
    let expires = v.get("expires_in").and_then(Value::as_u64).unwrap_or(600);

    eprintln!();
    eprintln!("  To connect this computer, open:  {verify}");
    eprintln!("  and enter the code:              {user_code}");
    eprintln!();
    open_browser(if complete.is_empty() { &verify } else { &complete });
    crate::log("waiting for the browser to approve (Ctrl+C to stop)");

    let deadline = Instant::now() + Duration::from_secs(expires);
    loop {
        std::thread::sleep(Duration::from_secs(interval));
        if Instant::now() > deadline {
            return Err("the code expired before it was approved -- run: gotg login".into());
        }
        let (status, v) = post_json(
            agent,
            &format!("{url}/auth/device/token"),
            &json!({ "device_code": device_code }),
        )?;
        let err = v.get("error").and_then(Value::as_str).unwrap_or_default();
        match (status, err) {
            (200, _) => {
                let token = v.get("token").and_then(Value::as_str).unwrap_or_default();
                if token.is_empty() {
                    return Err("the approval carried no token".into());
                }
                let name = v.get("name").and_then(Value::as_str).unwrap_or_default();
                return Ok(Device::Token(token.to_string(), name.to_string()));
            }
            (400, "authorization_pending") => {}
            (400, "slow_down") => interval += 5,
            (400, "expired_token") => {
                return Err("the code expired before it was approved -- run: gotg login".into());
            }
            _ => return Err(format!("{url}/auth/device/token answered {status} {err}")),
        }
    }
}

/// The service must accept the token: /catalog for a typed one, whoami for a
/// minted one (served by every pod, and a check of what minted it).
fn probe(agent: &ureq::Agent, url: &str, token: &str, minted: bool) -> Result<(), String> {
    let path = if minted { "/auth/whoami" } else { "/catalog" };
    let ok = api::get(agent, token, &format!("{url}{path}"), Duration::from_secs(30))
        .is_ok_and(|r| r.status().is_success());
    if ok {
        Ok(())
    } else {
        Err(format!("the service at {url} did not accept that token"))
    }
}

pub fn run(args: &LoginArgs, spec_server: Option<&str>, api_file: &Path) -> Result<(), String> {
    let agent = api::agent();
    let (url, token, name) = if let Some(claim) = &args.claim {
        let (url, code) = claim_parts(claim)?;
        let (token, name) = claim_exchange(&agent, &url, &code)?;
        (url, token, name)
    } else {
        let url = match args.server.as_deref().or(spec_server).filter(|s| !s.is_empty()) {
            Some(s) => s.to_string(),
            None => prompt_line(&format!("GOTG service URL [{DEFAULT_SERVER}]: "), DEFAULT_SERVER)?,
        };
        let url = url.trim_end_matches('/').to_string();
        if !(url.starts_with("http://") || url.starts_with("https://")) {
            return Err(format!("service must be an http(s) URL: {url}"));
        }
        if !url_is_private_or_tls(&url) {
            return Err(format!(
                "that url is http, which sends the token in the clear: {url}"
            ));
        }
        // A pipe is a script handing a token over; a terminal is a person,
        // who approves in the browser unless they asked to paste one.
        let device = if args.token || !std::io::stdin().is_terminal() {
            Device::Unavailable
        } else {
            device_flow(&agent, &url)?
        };
        match device {
            Device::Token(t, n) => (url, t, n),
            Device::Unavailable => {
                if !args.token && std::io::stdin().is_terminal() {
                    crate::log(&format!("{url} has no browser sign-in; paste a token instead"));
                }
                (url, read_token()?, String::new())
            }
        }
    };
    check_token(&token)?;
    probe(&agent, &url, &token, !name.is_empty())?;
    api::save(api_file, &url, &token, Some(&name))?;
    let you = if name.is_empty() {
        String::new()
    } else {
        format!(" -- you are {name}")
    };
    crate::log(&format!("saved {}{you}", api_file.display()));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokens() {
        assert!(check_token("gotg_abc-DEF.123~+/=").is_ok());
        assert!(check_token("a b").is_err());
        assert!(check_token("a\"b").is_err());
        assert!(check_token("").is_err());
    }

    #[test]
    fn claims() {
        let code = format!("gotgi_{}", "a".repeat(43));
        let (url, c) = claim_parts(&format!("https://g.example/claim/{code}?utm=x")).expect("claim");
        assert_eq!(url, "https://g.example");
        assert_eq!(c, code);
        assert!(
            claim_parts(&format!("http://g.example/claim/{code}"))
                .expect_err("should fail")
                .contains("is http")
        );
        assert!(claim_parts("https://g.example/claim/gotgi_short").is_err());
        assert!(claim_parts("https://g.example/x").is_err());
    }
}

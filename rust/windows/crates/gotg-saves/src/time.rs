//! UTC timestamps as the client writes them (`date -u +%Y-%m-%dT%H:%M:%SZ`),
//! without a date crate.

use std::time::{SystemTime, UNIX_EPOCH};

pub fn secs(t: SystemTime) -> i64 {
    match t.duration_since(UNIX_EPOCH) {
        Ok(d) => i64::try_from(d.as_secs()).unwrap_or(i64::MAX),
        Err(e) => -i64::try_from(e.duration().as_secs()).unwrap_or(i64::MAX),
    }
}

/// `GOTG_NOW` when set (the tests' fixed clock, as in bash), else now.
pub fn now_iso() -> String {
    std::env::var("GOTG_NOW").unwrap_or_else(|_| iso(secs(SystemTime::now())))
}

// Howard Hinnant's civil_from_days.
pub fn iso(secs: i64) -> String {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60
    )
}

#[cfg(test)]
mod tests {
    #[test]
    fn known_instants() {
        assert_eq!(super::iso(0), "1970-01-01T00:00:00Z");
        assert_eq!(super::iso(1_785_585_600), "2026-08-01T12:00:00Z");
        assert_eq!(super::iso(951_782_400), "2000-02-29T00:00:00Z");
    }
}

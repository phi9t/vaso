use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub fn format_age(now: SystemTime, then: SystemTime) -> String {
    let seconds = now
        .duration_since(then)
        .unwrap_or_else(|_| Duration::from_secs(0))
        .as_secs();
    format_duration(seconds)
}

pub fn format_duration(seconds: u64) -> String {
    if seconds < 60 {
        format!("{seconds}s")
    } else if seconds < 3_600 {
        format!("{}m{}s", seconds / 60, seconds % 60)
    } else if seconds < 86_400 {
        format!("{}h{}m", seconds / 3_600, (seconds % 3_600) / 60)
    } else {
        format!("{}d{}h", seconds / 86_400, (seconds % 86_400) / 3_600)
    }
}

pub fn unix_time(time: SystemTime) -> u64 {
    time.duration_since(UNIX_EPOCH)
        .unwrap_or_else(|_| Duration::from_secs(0))
        .as_secs()
}

pub fn system_time_from_epoch(seconds: u64) -> SystemTime {
    UNIX_EPOCH + Duration::from_secs(seconds)
}

pub fn utc_timestamp(time: SystemTime) -> String {
    let mut seconds = unix_time(time) as i64;
    let days = div_floor(seconds, 86_400);
    seconds -= days * 86_400;
    let (year, month, day) = civil_from_days(days);
    let hour = seconds / 3_600;
    let minute = (seconds % 3_600) / 60;
    let second = seconds % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z")
}

pub fn parse_utc_timestamp(timestamp: &str) -> Option<u64> {
    let t = timestamp.trim_end_matches('Z');
    let (date, time) = t.split_once('T')?;
    let mut d = date.split('-');
    let year = d.next()?.parse::<i64>().ok()?;
    let month = d.next()?.parse::<u32>().ok()?;
    let day = d.next()?.parse::<u32>().ok()?;
    let mut parts = time.split(':');
    let hour = parts.next()?.parse::<u32>().ok()?;
    let minute = parts.next()?.parse::<u32>().ok()?;
    let second = parts.next().unwrap_or("0").parse::<u32>().ok()?;
    if !(1..=12).contains(&month)
        || !(1..=31).contains(&day)
        || hour > 23
        || minute > 59
        || second > 59
    {
        return None;
    }
    let days = days_from_civil(year, month, day);
    Some(
        (days * 86_400 + i64::from(hour) * 3_600 + i64::from(minute) * 60 + i64::from(second))
            as u64,
    )
}

fn days_from_civil(year: i64, month: u32, day: u32) -> i64 {
    let year = year - i64::from(month <= 2);
    let era = if year >= 0 { year } else { year - 399 } / 400;
    let yoe = year - era * 400;
    let month = i64::from(month);
    let day = i64::from(day);
    let doy = (153 * (month + if month > 2 { -3 } else { 9 }) + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = mp + if mp < 10 { 3 } else { -9 };
    let y = y + if m <= 2 { 1 } else { 0 };
    (y, m, d)
}

fn div_floor(a: i64, b: i64) -> i64 {
    let mut q = a / b;
    let r = a % b;
    if r != 0 && ((r > 0) != (b > 0)) {
        q -= 1;
    }
    q
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_age_in_human_scale() {
        assert_eq!(format_duration(9), "9s");
        assert_eq!(format_duration(125), "2m5s");
        assert_eq!(format_duration(7_260), "2h1m");
        assert_eq!(format_duration(176_400), "2d1h");
    }

    #[test]
    fn parses_autoland_utc_timestamps() {
        assert_eq!(parse_utc_timestamp("1970-01-01T00:00:00Z"), Some(0));
        assert_eq!(parse_utc_timestamp("1970-01-01T00:01Z"), Some(60));
        assert_eq!(
            utc_timestamp(system_time_from_epoch(0)),
            "1970-01-01T00:00:00Z"
        );
    }
}

use std::collections::BTreeMap;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AutolandState {
    pub pid: u32,
    pub beat: u64,
    pub target: String,
    pub landing: String,
    pub last_move: u64,
    pub last_activity: u64,
    pub start: u64,
    pub stall_min: u64,
    pub batch_hours: u64,
    pub backlog_commits: u64,
    pub backlog_oldest_min: u64,
    pub backlog_active: bool,
    pub uncommitted_count: u64,
    pub uncommitted_oldest_min: u64,
    pub uncommitted_newest_min: u64,
    pub uncommitted_active: bool,
}

pub fn parse_state(text: &str) -> Result<AutolandState, String> {
    let mut values = BTreeMap::new();
    for field in text.split_whitespace() {
        let Some((key, value)) = field.split_once('=') else {
            continue;
        };
        values.insert(key, value);
    }
    let get = |key: &str| {
        values
            .get(key)
            .copied()
            .ok_or_else(|| format!("missing {key} in autoland.state"))
    };
    let optional_u64 = |key: &str| match values.get(key).copied() {
        Some(value) => parse(value, key),
        None => Ok(0),
    };
    Ok(AutolandState {
        pid: parse(get("pid")?, "pid")?,
        beat: parse(get("beat")?, "beat")?,
        target: get("target")?.to_owned(),
        landing: get("landing")?.to_owned(),
        last_move: parse(get("last_move")?, "last_move")?,
        last_activity: parse(get("last_activity")?, "last_activity")?,
        start: parse(get("start")?, "start")?,
        stall_min: parse(get("stall_min")?, "stall_min")?,
        batch_hours: parse(get("batch_hours")?, "batch_hours")?,
        backlog_commits: optional_u64("backlog_commits")?,
        backlog_oldest_min: optional_u64("backlog_oldest_min")?,
        backlog_active: optional_u64("backlog_active")? != 0,
        uncommitted_count: optional_u64("uncommitted_count")?,
        uncommitted_oldest_min: optional_u64("uncommitted_oldest_min")?,
        uncommitted_newest_min: optional_u64("uncommitted_newest_min")?,
        uncommitted_active: optional_u64("uncommitted_active")? != 0,
    })
}

fn parse<T: std::str::FromStr>(value: &str, key: &str) -> Result<T, String> {
    value
        .parse()
        .map_err(|_| format!("{key} has invalid value {value:?}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_state_file() {
        let state = parse_state(
            "pid=42 beat=100 target=abc landing=def last_move=90 last_activity=95 start=10 stall_min=45 batch_hours=4",
        )
        .expect("state parse");
        assert_eq!(state.pid, 42);
        assert_eq!(state.target, "abc");
        assert_eq!(state.last_activity, 95);
        assert_eq!(state.backlog_commits, 0);
        assert!(!state.backlog_active);
        assert_eq!(state.uncommitted_count, 0);
        assert!(!state.uncommitted_active);
    }

    #[test]
    fn parses_backlog_state_fields() {
        let state = parse_state(
            "pid=42 beat=100 target=abc landing=def last_move=90 last_activity=95 start=10 stall_min=45 batch_hours=4 backlog_commits=5 backlog_oldest_min=37 backlog_active=1",
        )
        .expect("state parse");
        assert_eq!(state.backlog_commits, 5);
        assert_eq!(state.backlog_oldest_min, 37);
        assert!(state.backlog_active);
    }

    #[test]
    fn parses_uncommitted_state_fields() {
        let state = parse_state(
            "pid=42 beat=100 target=abc landing=def last_move=90 last_activity=95 start=10 stall_min=45 batch_hours=4 uncommitted_count=2 uncommitted_oldest_min=190 uncommitted_newest_min=70 uncommitted_active=1",
        )
        .expect("state parse");
        assert_eq!(state.uncommitted_count, 2);
        assert_eq!(state.uncommitted_oldest_min, 190);
        assert_eq!(state.uncommitted_newest_min, 70);
        assert!(state.uncommitted_active);
    }
}

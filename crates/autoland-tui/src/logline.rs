#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LogKind {
    Start,
    Moved,
    Landed,
    Red,
    Rewritten,
    Stall,
    Gone,
    Io,
    Backlog,
    Uncommitted,
    Review,
    Relay,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LogEntry {
    pub line: String,
    pub kind: LogKind,
    pub timestamp: Option<String>,
}

pub fn classify_log_line(line: &str) -> LogEntry {
    let mut words = line.split_whitespace();
    let first = words.next();
    let second = words.next();
    let kind = match second {
        Some("START") => LogKind::Start,
        Some("MOVED") => LogKind::Moved,
        Some("LANDED") => LogKind::Landed,
        Some("RED") => LogKind::Red,
        Some("REWRITTEN") => LogKind::Rewritten,
        Some("STALL") => LogKind::Stall,
        Some("GONE") => LogKind::Gone,
        Some("IO") => LogKind::Io,
        Some("BACKLOG") => LogKind::Backlog,
        Some("UNCOMMITTED") => LogKind::Uncommitted,
        Some("REVIEW") => LogKind::Review,
        _ => LogKind::Relay,
    };
    let timestamp = first
        .filter(|word| word.len() >= 17 && word.contains('T') && word.ends_with('Z'))
        .map(str::to_owned);
    LogEntry {
        line: line.to_owned(),
        kind,
        timestamp,
    }
}

pub fn is_attention(kind: LogKind) -> bool {
    matches!(
        kind,
        LogKind::Red
            | LogKind::Rewritten
            | LogKind::Stall
            | LogKind::Gone
            | LogKind::Io
            | LogKind::Backlog
            | LogKind::Uncommitted
            | LogKind::Review
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classifies_autoland_events() {
        assert_eq!(
            classify_log_line("2026-09-29T12:00:00Z LANDED landing -> abc").kind,
            LogKind::Landed
        );
        assert_eq!(
            classify_log_line("2026-09-29T12:00:00Z RED VERIFY_FAILED").kind,
            LogKind::Red
        );
        assert_eq!(
            classify_log_line("2026-09-29T12:00:00Z BACKLOG 4 commits, oldest 37 min").kind,
            LogKind::Backlog
        );
        assert_eq!(
            classify_log_line("2026-09-29T12:00:00Z UNCOMMITTED 2 files, oldest 90 min").kind,
            LogKind::Uncommitted
        );
        assert!(is_attention(LogKind::Backlog));
        assert_eq!(classify_log_line("VERIFY_PASSED abc").kind, LogKind::Relay);
    }
}

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use crate::age;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TrackerComment {
    pub timestamp: String,
    pub file: String,
    pub author: String,
    pub text: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FollowerLogTail {
    pub name: String,
    pub modified: Option<SystemTime>,
    pub lines: Vec<String>,
}

pub fn extract_comments(tracker_dir: &Path, limit: usize) -> io::Result<Vec<TrackerComment>> {
    let mut files = Vec::new();
    collect_top_level_markdown(tracker_dir, &mut files)?;
    collect_top_level_markdown(&tracker_dir.join("issues"), &mut files)?;

    let mut comments = Vec::new();
    for file in files {
        let text = match fs::read_to_string(&file) {
            Ok(text) => text,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error),
        };
        let name = file
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("?")
            .to_owned();
        let mut current: Option<TrackerComment> = None;
        for line in text.lines() {
            if let Some((timestamp, author, text)) = parse_comment_line(line) {
                if let Some(comment) = current.take() {
                    comments.push(trim_comment(comment));
                }
                current = Some(TrackerComment {
                    timestamp,
                    file: name.clone(),
                    author,
                    text,
                });
            } else if let Some(comment) = &mut current {
                comment.text.push('\n');
                comment
                    .text
                    .push_str(line.strip_prefix("  ").unwrap_or(line).trim_end());
            }
        }
        if let Some(comment) = current {
            comments.push(trim_comment(comment));
        }
    }
    comments.sort_by(|a, b| {
        b.timestamp
            .cmp(&a.timestamp)
            .then_with(|| a.file.cmp(&b.file))
    });
    comments.truncate(limit);
    Ok(comments)
}

fn trim_comment(mut comment: TrackerComment) -> TrackerComment {
    comment.text = comment.text.trim().to_owned();
    comment
}

fn collect_top_level_markdown(dir: &Path, files: &mut Vec<PathBuf>) -> io::Result<()> {
    let entries = match fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    for entry in entries {
        let entry = entry?;
        let path = entry.path();
        if path.extension().and_then(|ext| ext.to_str()) == Some("md") {
            files.push(path);
        }
    }
    Ok(())
}

pub fn parse_comment_line(line: &str) -> Option<(String, String, String)> {
    let rest = line.strip_prefix("- ")?;
    let (timestamp, rest) = rest.split_once(" (")?;
    if !looks_like_comment_timestamp(timestamp) {
        return None;
    }
    let (author, rest) = rest.split_once("):")?;
    Some((
        timestamp.to_owned(),
        author.to_owned(),
        rest.trim().to_owned(),
    ))
}

fn looks_like_comment_timestamp(timestamp: &str) -> bool {
    let bytes = timestamp.as_bytes();
    bytes.len() == 17
        && bytes[0] == b'2'
        && bytes[1] == b'0'
        && bytes[4] == b'-'
        && bytes[7] == b'-'
        && bytes[10] == b'T'
        && bytes[13] == b':'
        && bytes[16] == b'Z'
        && bytes
            .iter()
            .enumerate()
            .all(|(index, byte)| matches!(index, 4 | 7 | 10 | 13 | 16) || byte.is_ascii_digit())
}

pub fn newest_follower_log(
    log_dir: &Path,
    line_limit: usize,
) -> io::Result<Option<FollowerLogTail>> {
    let entries = match fs::read_dir(log_dir) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    let mut newest: Option<(PathBuf, Option<SystemTime>)> = None;
    for entry in entries {
        let entry = entry?;
        let path = entry.path();
        if !entry.file_type()?.is_file() {
            continue;
        }
        let modified = entry.metadata().and_then(|meta| meta.modified()).ok();
        let replace = newest
            .as_ref()
            .map(|(_, current)| modified > *current)
            .unwrap_or(true);
        if replace {
            newest = Some((path, modified));
        }
    }
    let Some((path, modified)) = newest else {
        return Ok(None);
    };
    let text = fs::read_to_string(&path).unwrap_or_default();
    let mut lines: Vec<String> = text.lines().map(str::to_owned).collect();
    if lines.len() > line_limit {
        lines = lines.split_off(lines.len() - line_limit);
    }
    Ok(Some(FollowerLogTail {
        name: path
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("?")
            .to_owned(),
        modified,
        lines,
    }))
}

pub fn follower_log_age(now: SystemTime, log: &FollowerLogTail) -> String {
    log.modified
        .map(|modified| age::format_age(now, modified))
        .unwrap_or_else(|| "?".to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn test_root(name: &str) -> PathBuf {
        let base = env::var_os("VASO_AGENT_IO_ROOT")
            .map(PathBuf::from)
            .or_else(|| env::var_os("CARGO_TARGET_DIR").map(PathBuf::from))
            .unwrap_or_else(|| PathBuf::from("target"));
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock before epoch")
            .as_nanos();
        base.join("autoland-tui-unit-tests")
            .join(format!("{name}-{}-{stamp}", std::process::id()))
    }

    #[test]
    fn extracts_tracker_comment_line() {
        let parsed = parse_comment_line("- 2026-09-29T12:34Z (trae): proof finished").unwrap();
        assert_eq!(parsed.0, "2026-09-29T12:34Z");
        assert_eq!(parsed.1, "trae");
        assert_eq!(parsed.2, "proof finished");
        assert!(parse_comment_line("- TODO").is_none());
    }

    #[test]
    fn extracts_full_multiline_tracker_comment_bodies() {
        let root = test_root("multiline-comments");
        let issues = root.join("issues");
        fs::create_dir_all(&issues).expect("create issues directory");
        fs::write(
            issues.join("08-reseat-python-bound-natives.md"),
            "\
## Comments
- 2026-09-29T12:34Z (TRAE): package-scoped re-seat is `py-pathspec`.
  Verified prefix parity and copied the proof log into FOLLOWER_LOGS.

  Next package remains queued for the same lane.
- 2026-09-29T12:10Z (LEAD): landing branch advanced.
",
        )
        .expect("write tracker issue");

        let comments = extract_comments(&root, 10).expect("extract comments");

        assert_eq!(comments.len(), 2);
        assert_eq!(comments[0].timestamp, "2026-09-29T12:34Z");
        assert!(comments[0].text.contains("package-scoped re-seat"));
        assert!(comments[0].text.contains("Verified prefix parity"));
        assert!(comments[0].text.contains("Next package remains queued"));
        assert!(comments[1].text.contains("landing branch advanced"));
    }
}

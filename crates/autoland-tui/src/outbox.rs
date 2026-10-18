use std::collections::BTreeMap;
use std::ffi::OsStr;
use std::fs;
use std::io;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::process::ChildEnv;
use crate::tmux;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DraftStatus {
    Draft,
    Sent,
    Dropped,
    Other,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Draft {
    pub path: PathBuf,
    pub name: String,
    pub author: String,
    pub target_pane: Option<String>,
    pub status: DraftStatus,
    pub status_text: String,
    pub created: String,
    pub sent: Option<String>,
    pub title: String,
    pub body: String,
}

pub fn load_outbox(estate_root: &Path) -> io::Result<Vec<Draft>> {
    load_drafts(&estate_root.join("agents").join("relay").join("outbox"))
}

pub fn load_drafts(outbox_dir: &Path) -> io::Result<Vec<Draft>> {
    let entries = match fs::read_dir(outbox_dir) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error),
    };
    let mut drafts = Vec::new();
    for entry in entries {
        let entry = entry?;
        let path = entry.path();
        if path.extension().and_then(|ext| ext.to_str()) != Some("md") {
            continue;
        }
        let text = fs::read_to_string(&path)?;
        drafts.push(parse_draft(path, &text));
    }
    drafts.sort_by(|a, b| b.created.cmp(&a.created).then_with(|| b.name.cmp(&a.name)));
    Ok(drafts)
}

pub fn parse_draft(path: PathBuf, text: &str) -> Draft {
    let (front, body) = split_front_matter(text);
    let status_text = front
        .get("status")
        .cloned()
        .unwrap_or_else(|| "draft".to_owned());
    Draft {
        name: path
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("?")
            .to_owned(),
        path,
        author: front
            .get("author")
            .cloned()
            .unwrap_or_else(|| "?".to_owned()),
        target_pane: optional(front.get("target_pane").map(String::as_str).unwrap_or("")),
        status: parse_status(&status_text),
        status_text,
        created: front.get("created").cloned().unwrap_or_default(),
        sent: optional(front.get("sent").map(String::as_str).unwrap_or("")),
        title: front
            .get("title")
            .cloned()
            .unwrap_or_else(|| "message".to_owned()),
        body,
    }
}

pub fn pending_count(drafts: &[Draft]) -> usize {
    drafts
        .iter()
        .filter(|draft| draft.status == DraftStatus::Draft)
        .count()
}

pub fn draft_message(
    estate_root: &Path,
    agent_io_root: &Path,
    author: &str,
    pane: Option<&str>,
    title: &str,
    body: &str,
) -> io::Result<PathBuf> {
    let outbox_dir = outbox_dir(estate_root);
    fs::create_dir_all(&outbox_dir)?;
    fs::create_dir_all(agent_io_root.join("tmp"))?;
    let created = utc_stamp();
    let file_stamp = file_stamp();
    let slug = slugify(title);
    let base = outbox_dir.join(format!("{file_stamp}-{slug}.md"));
    let mut path = base.clone();
    let mut index = 2;
    while path.exists() {
        path = base.with_file_name(format!("{file_stamp}-{slug}-{index}.md"));
        index += 1;
    }
    let pane = pane.unwrap_or("");
    let body = if body.ends_with('\n') {
        body.to_owned()
    } else {
        format!("{body}\n")
    };
    fs::write(
        &path,
        format!(
            "---\nauthor: {author}\ntarget_pane: {pane}\nstatus: draft\ncreated: {created}\nsent: \ntitle: {title}\n---\n\n{body}",
        ),
    )?;
    log_event(
        &outbox_log(estate_root),
        &format!(
            "DRAFT {} pane={} author={author}",
            path.file_name()
                .and_then(OsStr::to_str)
                .unwrap_or("draft.md"),
            if pane.is_empty() { "none" } else { pane }
        ),
    )?;
    Ok(path)
}

pub fn send_draft(
    draft_path: &Path,
    pane_override: Option<&str>,
    fallback_pane: Option<&str>,
    agent_io_root: &Path,
    estate_root: &Path,
) -> io::Result<PathBuf> {
    send_draft_with_socket(
        draft_path,
        pane_override,
        fallback_pane,
        agent_io_root,
        estate_root,
        None,
    )
}

pub fn send_draft_with_socket(
    draft_path: &Path,
    pane_override: Option<&str>,
    fallback_pane: Option<&str>,
    agent_io_root: &Path,
    estate_root: &Path,
    socket: Option<&str>,
) -> io::Result<PathBuf> {
    let text = fs::read_to_string(draft_path)?;
    let draft = parse_draft(draft_path.to_path_buf(), &text);
    if draft.status != DraftStatus::Draft {
        return Err(io::Error::other(format!(
            "draft status is {}, not draft",
            draft.status_text
        )));
    }
    let pane = pane_override
        .filter(|pane| !pane.is_empty() && *pane != "none")
        .or(draft.target_pane.as_deref())
        .filter(|pane| !pane.is_empty() && *pane != "none")
        .or(fallback_pane)
        .filter(|pane| !pane.is_empty() && *pane != "none")
        .ok_or_else(|| io::Error::other("no target pane"))?;
    let tmux_env = tmux_child_env(estate_root, agent_io_root);
    let payload =
        tmux::send_bracketed_paste_with_env(pane, &draft.body, agent_io_root, socket, &tmux_env)?;
    let sent_at = utc_stamp();
    mark_status(draft_path, DraftStatus::Sent, Some(&sent_at), agent_io_root)?;
    log_event(
        &outbox_log(estate_root),
        &format!(
            "SEND {} pane={pane} bytes={}",
            draft_path
                .file_name()
                .and_then(OsStr::to_str)
                .unwrap_or("draft.md"),
            fs::metadata(payload).map(|meta| meta.len()).unwrap_or(0)
        ),
    )?;
    Ok(draft_path.to_path_buf())
}

pub fn drop_draft(
    draft_path: &Path,
    agent_io_root: &Path,
    estate_root: &Path,
) -> io::Result<PathBuf> {
    let text = fs::read_to_string(draft_path)?;
    let draft = parse_draft(draft_path.to_path_buf(), &text);
    if draft.status != DraftStatus::Draft {
        return Err(io::Error::other(format!(
            "draft status is {}, not draft",
            draft.status_text
        )));
    }
    mark_status(draft_path, DraftStatus::Dropped, None, agent_io_root)?;
    log_event(
        &outbox_log(estate_root),
        &format!(
            "DROP {}",
            draft_path
                .file_name()
                .and_then(OsStr::to_str)
                .unwrap_or("draft.md")
        ),
    )?;
    Ok(draft_path.to_path_buf())
}

pub fn outbox_dir(estate_root: &Path) -> PathBuf {
    estate_root.join("agents").join("relay").join("outbox")
}

pub fn outbox_log(estate_root: &Path) -> PathBuf {
    estate_root.join("agents").join("relay").join("outbox.log")
}

fn tmux_child_env(estate_root: &Path, agent_io_root: &Path) -> ChildEnv {
    ChildEnv::from_current()
        .with_var("VASO_ESTATE_ROOT", estate_root.as_os_str())
        .with_var("VASO_AGENT_IO_ROOT", agent_io_root.as_os_str())
        .with_var("TMPDIR", agent_io_root.join("tmp").as_os_str())
        .with_var("VASO_BAZEL_OB", agent_io_root.join("bazel-ob").as_os_str())
}

fn split_front_matter(text: &str) -> (BTreeMap<String, String>, String) {
    let mut front = BTreeMap::new();
    let mut lines = text.lines();
    if lines.next() != Some("---") {
        return (front, text.to_owned());
    }
    let mut body_lines = Vec::new();
    let mut in_front = true;
    let mut skip_blank = false;
    for line in lines {
        if in_front {
            if line == "---" {
                in_front = false;
                skip_blank = true;
                continue;
            }
            if let Some((key, value)) = line.split_once(": ") {
                front.insert(key.to_owned(), value.to_owned());
            }
            continue;
        }
        if skip_blank && line.is_empty() {
            skip_blank = false;
            continue;
        }
        skip_blank = false;
        body_lines.push(line);
    }
    let body = if body_lines.is_empty() {
        String::new()
    } else {
        format!("{}\n", body_lines.join("\n"))
    };
    (front, body)
}

fn mark_status(
    path: &Path,
    status: DraftStatus,
    sent: Option<&str>,
    agent_io_root: &Path,
) -> io::Result<()> {
    let text = fs::read_to_string(path)?;
    let tmp_dir = agent_io_root.join("tmp");
    fs::create_dir_all(&tmp_dir)?;
    let tmp = tmp_dir.join(format!(
        "{}.{}.tmp",
        path.file_name().and_then(OsStr::to_str).unwrap_or("draft"),
        std::process::id()
    ));
    let status_text = status.as_str();
    let mut saw_status = false;
    let mut saw_sent = false;
    let mut out = Vec::new();
    let mut in_front = false;
    for (index, line) in text.lines().enumerate() {
        if index == 0 && line == "---" {
            in_front = true;
            out.push(line.to_owned());
            continue;
        }
        if in_front && line == "---" {
            if !saw_status {
                out.push(format!("status: {status_text}"));
            }
            if sent.is_some() && !saw_sent {
                out.push(format!("sent: {}", sent.unwrap_or_default()));
            }
            out.push(line.to_owned());
            in_front = false;
            continue;
        }
        if in_front && line.starts_with("status:") {
            out.push(format!("status: {status_text}"));
            saw_status = true;
            continue;
        }
        if in_front && line.starts_with("sent:") {
            if let Some(sent) = sent {
                out.push(format!("sent: {sent}"));
            }
            saw_sent = true;
            continue;
        }
        out.push(line.to_owned());
    }
    fs::write(&tmp, format!("{}\n", out.join("\n")))?;
    fs::rename(tmp, path)?;
    Ok(())
}

fn log_event(path: &Path, text: &str) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?
        .write_all(format!("{} {text}\n", utc_stamp()).as_bytes())
}

fn parse_status(status: &str) -> DraftStatus {
    match status {
        "draft" => DraftStatus::Draft,
        "sent" => DraftStatus::Sent,
        "dropped" => DraftStatus::Dropped,
        _ => DraftStatus::Other,
    }
}

impl DraftStatus {
    fn as_str(self) -> &'static str {
        match self {
            DraftStatus::Draft => "draft",
            DraftStatus::Sent => "sent",
            DraftStatus::Dropped => "dropped",
            DraftStatus::Other => "other",
        }
    }
}

fn optional(value: &str) -> Option<String> {
    let value = value.trim();
    if value.is_empty() || value == "none" {
        None
    } else {
        Some(value.to_owned())
    }
}

fn slugify(title: &str) -> String {
    let mut slug = String::new();
    let mut dash = false;
    for ch in title.chars().flat_map(char::to_lowercase) {
        if ch.is_ascii_alphanumeric() {
            slug.push(ch);
            dash = false;
        } else if !dash && !slug.is_empty() {
            slug.push('-');
            dash = true;
        }
    }
    while slug.ends_with('-') {
        slug.pop();
    }
    if slug.is_empty() {
        "message".to_owned()
    } else {
        slug
    }
}

fn utc_stamp() -> String {
    unix_to_utc(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs(),
    )
}

fn file_stamp() -> String {
    utc_stamp()
        .replace(['-', ':'], "")
        .trim_end_matches('Z')
        .to_owned()
        + "Z"
}

fn unix_to_utc(secs: u64) -> String {
    let mut t = secs as i64;
    let days = div_floor(t, 86_400);
    t -= days * 86_400;
    let (year, month, day) = civil_from_days(days);
    let hour = t / 3_600;
    let minute = (t % 3_600) / 60;
    let second = t % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z")
}

fn div_floor(a: i64, b: i64) -> i64 {
    let mut q = a / b;
    let r = a % b;
    if r != 0 && ((r > 0) != (b > 0)) {
        q -= 1;
    }
    q
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

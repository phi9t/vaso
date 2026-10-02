use std::env;
use std::ffi::OsString;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::process::ChildEnv;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PaneStatus {
    Idle,
    Busy,
    Unknown,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TodoState {
    Active,
    Pending,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TodoItem {
    pub state: TodoState,
    pub text: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AnsiColor {
    Black,
    Red,
    Green,
    Yellow,
    Blue,
    Magenta,
    Cyan,
    White,
    BrightBlack,
    BrightRed,
    BrightGreen,
    BrightYellow,
    BrightBlue,
    BrightMagenta,
    BrightCyan,
    BrightWhite,
    Rgb(u8, u8, u8),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StyledSpan {
    pub text: String,
    pub fg: Option<AnsiColor>,
    pub bg: Option<AnsiColor>,
    pub bold: bool,
    pub dim: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StyledLine {
    pub spans: Vec<StyledSpan>,
    pub plain: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PaneCapture {
    pub raw: String,
    pub plain_text: String,
    pub lines: Vec<StyledLine>,
    pub status: PaneStatus,
    pub busy_elapsed: Option<String>,
    pub prompt_line: Option<String>,
    pub todos: Vec<TodoItem>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct StyleState {
    fg: Option<AnsiColor>,
    bg: Option<AnsiColor>,
    bold: bool,
    dim: bool,
}

impl StyleState {
    fn plain() -> Self {
        Self {
            fg: None,
            bg: None,
            bold: false,
            dim: false,
        }
    }

    fn span(&self, text: String) -> StyledSpan {
        StyledSpan {
            text,
            fg: self.fg,
            bg: self.bg,
            bold: self.bold,
            dim: self.dim,
        }
    }
}

pub fn capture_pane(pane: Option<&str>, lines: usize) -> io::Result<Option<PaneCapture>> {
    let socket = env::var("VASO_TMUX_SOCKET").ok();
    capture_pane_with_socket(pane, lines, socket.as_deref())
}

pub fn capture_pane_with_socket(
    pane: Option<&str>,
    lines: usize,
    socket: Option<&str>,
) -> io::Result<Option<PaneCapture>> {
    let env = ChildEnv::from_current();
    capture_pane_with_env(pane, lines, socket, &env)
}

pub fn capture_pane_with_env(
    pane: Option<&str>,
    lines: usize,
    socket: Option<&str>,
    env: &ChildEnv,
) -> io::Result<Option<PaneCapture>> {
    let Some(pane) = pane else {
        return Ok(None);
    };
    if pane.is_empty() || pane == "none" {
        return Ok(None);
    }
    let args = tmux_args(
        socket,
        [
            OsString::from("capture-pane"),
            OsString::from("-p"),
            OsString::from("-e"),
            OsString::from("-J"),
            OsString::from("-S"),
            OsString::from(format!("-{lines}")),
            OsString::from("-t"),
            OsString::from(pane),
        ],
    );
    let output = match env.output("tmux", &args, None) {
        Ok(output) => output,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    if !output.status.success() {
        return Ok(None);
    }
    let raw = String::from_utf8_lossy(&output.stdout).to_string();
    Ok(Some(parse_capture(&raw)))
}

pub fn parse_capture(raw: &str) -> PaneCapture {
    let mut lines = Vec::new();
    let mut plain_lines = Vec::new();
    for line in raw.lines() {
        let spans = parse_sgr(line);
        let plain = spans
            .iter()
            .map(|span| span.text.as_str())
            .collect::<String>();
        plain_lines.push(plain.clone());
        lines.push(StyledLine { spans, plain });
    }
    let plain_text = plain_lines.join("\n");
    let prompt_line = plain_lines
        .iter()
        .rev()
        .find(|line| line.contains('❯'))
        .cloned();
    let status = pane_status(&plain_lines);
    let busy_elapsed = parse_busy_elapsed(&plain_lines);
    let todos = parse_todos(&plain_text);
    PaneCapture {
        raw: raw.to_owned(),
        plain_text,
        lines,
        status,
        busy_elapsed,
        prompt_line,
        todos,
    }
}

pub fn parse_sgr(input: &str) -> Vec<StyledSpan> {
    let mut spans = Vec::new();
    let mut state = StyleState::plain();
    let mut current = String::new();
    let mut chars = input.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch != '\u{1b}' {
            current.push(ch);
            continue;
        }
        if chars.next_if_eq(&'[').is_none() {
            continue;
        }
        let mut seq = String::new();
        for next in chars.by_ref() {
            if next == 'm' {
                break;
            }
            seq.push(next);
        }
        if !current.is_empty() {
            spans.push(state.span(std::mem::take(&mut current)));
        }
        apply_sgr(&mut state, &seq);
    }
    if !current.is_empty() {
        spans.push(state.span(current));
    }
    spans
}

pub fn strip_ansi(input: &str) -> String {
    parse_sgr(input)
        .into_iter()
        .map(|span| span.text)
        .collect::<String>()
}

pub fn pane_status(lines: &[String]) -> PaneStatus {
    // The busy marker sits above traecli's to-do list and prompt box, often
    // 10+ lines up; scan a generous window of non-blank lines.
    let tail = lines
        .iter()
        .rev()
        .filter(|line| !line.trim().is_empty())
        .take(40)
        .map(|line| line.to_lowercase())
        .collect::<Vec<_>>();
    if tail.iter().any(|line| is_busy_line(line)) {
        return PaneStatus::Busy;
    }
    if tail.iter().any(|line| line.contains('❯')) {
        return PaneStatus::Idle;
    }
    PaneStatus::Unknown
}

/// traecli busy markers (`line` is lowercased). The status line can be caught
/// mid-redraw without "esc to interrupt", so the turn timer's token counter and
/// a queued "send after tool call" also count. "you can still chat" marks an
/// idle session that only has a background shell running.
fn is_busy_line(line: &str) -> bool {
    if line.contains("you can still chat") {
        return false;
    }
    line.contains("esc to interrupt") || line.contains("tokens •") || line.contains("send after tool call")
}

pub fn parse_busy_elapsed(lines: &[String]) -> Option<String> {
    lines
        .iter()
        .rev()
        .filter(|line| !line.trim().is_empty())
        .take(40)
        .find_map(|line| busy_elapsed_from_line(line))
}

fn busy_elapsed_from_line(line: &str) -> Option<String> {
    if !line.to_lowercase().contains("esc to interrupt") {
        return None;
    }
    let detail = line
        .rsplit_once('(')
        .map(|(_, rest)| rest)
        .unwrap_or(line)
        .split('•')
        .next()
        .unwrap_or("")
        .trim();
    let compact = detail.split_whitespace().collect::<String>();
    (!compact.is_empty() && compact.chars().any(|ch| ch.is_ascii_digit())).then_some(compact)
}

pub fn parse_todos(text: &str) -> Vec<TodoItem> {
    text.lines()
        .filter_map(|line| {
            let active = line.find('■').map(|index| (TodoState::Active, index));
            let pending = line.find('◻').map(|index| (TodoState::Pending, index));
            let (state, index) = match (active, pending) {
                (Some(active), Some(pending)) => {
                    if active.1 < pending.1 {
                        active
                    } else {
                        pending
                    }
                }
                (Some(active), None) => active,
                (None, Some(pending)) => pending,
                (None, None) => return None,
            };
            let text = line[index + '■'.len_utf8()..].trim().to_owned();
            (!text.is_empty()).then_some(TodoItem { state, text })
        })
        .collect()
}

pub fn send_bracketed_paste(pane: &str, text: &str, agent_io_root: &Path) -> io::Result<PathBuf> {
    let socket = env::var("VASO_TMUX_SOCKET").ok();
    send_bracketed_paste_with_socket(pane, text, agent_io_root, socket.as_deref())
}

pub fn send_bracketed_paste_with_socket(
    pane: &str,
    text: &str,
    agent_io_root: &Path,
    socket: Option<&str>,
) -> io::Result<PathBuf> {
    let env = ChildEnv::from_current();
    send_bracketed_paste_with_env(pane, text, agent_io_root, socket, &env)
}

pub fn send_bracketed_paste_with_env(
    pane: &str,
    text: &str,
    agent_io_root: &Path,
    socket: Option<&str>,
    env: &ChildEnv,
) -> io::Result<PathBuf> {
    let capture = capture_pane_with_env(Some(pane), 20, socket, env)?;
    let Some(capture) = capture else {
        return Err(io::Error::other(format!("pane {pane} is unavailable")));
    };
    if capture.status != PaneStatus::Idle {
        return Err(io::Error::other(format!("pane {pane} is not idle")));
    }
    let tmp = agent_io_root.join("tmp");
    fs::create_dir_all(&tmp)?;
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let payload = tmp.join(format!("outbox-send-{}-{unique}.txt", std::process::id()));
    fs::write(&payload, text)?;
    tmux_status(
        env,
        tmux_args(
            socket,
            [
                OsString::from("load-buffer"),
                OsString::from("-b"),
                OsString::from("relay-outbox"),
                payload.as_os_str().to_owned(),
            ],
        ),
    )
    .and_then(check_status)?;
    tmux_status(
        env,
        tmux_args(
            socket,
            [
                OsString::from("paste-buffer"),
                OsString::from("-p"),
                OsString::from("-d"),
                OsString::from("-b"),
                OsString::from("relay-outbox"),
                OsString::from("-t"),
                OsString::from(pane),
            ],
        ),
    )
    .and_then(check_status)?;
    tmux_status(
        env,
        tmux_args(
            socket,
            [
                OsString::from("send-keys"),
                OsString::from("-t"),
                OsString::from(pane),
                OsString::from("Enter"),
            ],
        ),
    )
    .and_then(check_status)?;
    Ok(payload)
}

fn tmux_args(
    socket_override: Option<&str>,
    args: impl IntoIterator<Item = OsString>,
) -> Vec<OsString> {
    let mut all = Vec::new();
    if let Some(socket) = socket_override.filter(|socket| !socket.is_empty()) {
        all.push(OsString::from("-S"));
        all.push(OsString::from(socket));
    }
    all.extend(args);
    all
}

fn tmux_status(env: &ChildEnv, args: Vec<OsString>) -> io::Result<std::process::ExitStatus> {
    env.status("tmux", &args, None)
}

fn check_status(status: std::process::ExitStatus) -> io::Result<()> {
    if status.success() {
        Ok(())
    } else {
        Err(io::Error::other(format!("tmux failed with {status}")))
    }
}

fn apply_sgr(state: &mut StyleState, seq: &str) {
    let values = if seq.is_empty() {
        vec![0]
    } else {
        seq.split(';')
            .filter_map(|part| part.parse::<u16>().ok())
            .collect::<Vec<_>>()
    };
    let mut index = 0;
    while index < values.len() {
        let value = values[index];
        match value {
            0 => *state = StyleState::plain(),
            1 => state.bold = true,
            2 => state.dim = true,
            22 => {
                state.bold = false;
                state.dim = false;
            }
            30..=37 => state.fg = ansi_color(value - 30, false),
            39 => state.fg = None,
            40..=47 => state.bg = ansi_color(value - 40, false),
            49 => state.bg = None,
            90..=97 => state.fg = ansi_color(value - 90, true),
            100..=107 => state.bg = ansi_color(value - 100, true),
            38 | 48 if values.get(index + 1) == Some(&2) && index + 4 < values.len() => {
                let color = AnsiColor::Rgb(
                    values[index + 2].min(255) as u8,
                    values[index + 3].min(255) as u8,
                    values[index + 4].min(255) as u8,
                );
                if value == 38 {
                    state.fg = Some(color);
                } else {
                    state.bg = Some(color);
                }
                index += 4;
            }
            _ => {}
        }
        index += 1;
    }
}

fn ansi_color(code: u16, bright: bool) -> Option<AnsiColor> {
    Some(match (code, bright) {
        (0, false) => AnsiColor::Black,
        (1, false) => AnsiColor::Red,
        (2, false) => AnsiColor::Green,
        (3, false) => AnsiColor::Yellow,
        (4, false) => AnsiColor::Blue,
        (5, false) => AnsiColor::Magenta,
        (6, false) => AnsiColor::Cyan,
        (7, false) => AnsiColor::White,
        (0, true) => AnsiColor::BrightBlack,
        (1, true) => AnsiColor::BrightRed,
        (2, true) => AnsiColor::BrightGreen,
        (3, true) => AnsiColor::BrightYellow,
        (4, true) => AnsiColor::BrightBlue,
        (5, true) => AnsiColor::BrightMagenta,
        (6, true) => AnsiColor::BrightCyan,
        (7, true) => AnsiColor::BrightWhite,
        _ => return None,
    })
}

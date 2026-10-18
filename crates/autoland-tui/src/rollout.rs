use std::fs::{self, File};
use std::io::{self, Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

const DEFAULT_MAX_LINE_LEN: usize = 32 * 1024;

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct RolloutSummary {
    pub source: Option<PathBuf>,
    pub offset: u64,
    pub agent_messages: Vec<AgentMessage>,
    pub commands: Vec<CommandEvent>,
    pub file_changes: Vec<FileChange>,
    pub token: Option<TokenUsage>,
    pub turn: Option<TurnTiming>,
    pub compactions: Vec<CompactionEvent>,
    pub markers: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AgentMessage {
    pub timestamp: Option<String>,
    pub phase: Option<String>,
    pub text: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CommandEvent {
    pub timestamp: Option<String>,
    pub command: String,
    pub cwd: Option<String>,
    pub exit_code: Option<i32>,
    pub duration_ms: Option<u64>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FileChange {
    pub timestamp: Option<String>,
    pub path: String,
    pub change_type: String,
    pub move_path: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TokenUsage {
    pub timestamp: Option<String>,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub reasoning_output_tokens: u64,
    pub total_tokens: u64,
    pub model_context_window: u64,
    pub auto_compact_token_limit: Option<u64>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TurnTiming {
    pub turn_id: Option<String>,
    pub started_at_ms: Option<u64>,
    pub completed_at_ms: Option<u64>,
    pub elapsed_ms: Option<u64>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CompactionEvent {
    pub timestamp: Option<String>,
    pub completed_at_ms: Option<u64>,
    pub summary: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TailRead {
    pub lines: Vec<String>,
    pub offset: u64,
    pub bytes_read: u64,
}

#[derive(Clone, Debug)]
pub struct RolloutRefresh {
    pub summary: RolloutSummary,
    pub offset: u64,
    pub bytes_read: u64,
}

#[derive(Clone, Debug)]
pub struct RolloutCursor {
    offset: Option<u64>,
    max_lines: usize,
    chunk_size: usize,
    max_bytes: usize,
    max_line_len: usize,
    recent_lines: Vec<String>,
}

impl Default for RolloutCursor {
    fn default() -> Self {
        Self::new(160, 1024 * 1024, 16 * 1024 * 1024, DEFAULT_MAX_LINE_LEN)
    }
}

impl RolloutCursor {
    pub fn new(max_lines: usize, chunk_size: usize, max_bytes: usize, max_line_len: usize) -> Self {
        Self {
            offset: None,
            max_lines,
            chunk_size,
            max_bytes,
            max_line_len,
            recent_lines: Vec::new(),
        }
    }

    pub fn refresh(&mut self, path: &Path) -> io::Result<RolloutRefresh> {
        let len = fs::metadata(path)?.len();
        let Some(offset) = self.offset else {
            let tail = read_tail_lines_with_limit(
                path,
                self.max_lines,
                self.chunk_size,
                self.max_bytes,
                self.max_line_len,
            )?;
            self.offset = Some(tail.offset);
            self.recent_lines = tail.lines;
            return Ok(self.refresh_result(path, tail.bytes_read));
        };
        if len < offset {
            self.offset = None;
            self.recent_lines.clear();
            return self.refresh(path);
        }
        if len == offset {
            return Ok(self.refresh_result(path, 0));
        }

        let bytes_to_read = (len - offset).min(self.max_bytes as u64);
        let start = len - bytes_to_read;
        let mut file = File::open(path)?;
        file.seek(SeekFrom::Start(start))?;
        let mut bytes = vec![0_u8; bytes_to_read as usize];
        file.read_exact(&mut bytes)?;
        let starts_at_line_boundary = start == offset;
        let mut lines =
            complete_lines_from_bytes(&bytes, starts_at_line_boundary, self.max_line_len);
        self.recent_lines.append(&mut lines);
        if self.recent_lines.len() > self.max_lines {
            self.recent_lines
                .drain(0..self.recent_lines.len() - self.max_lines);
        }
        self.offset = Some(len);
        Ok(self.refresh_result(path, bytes_to_read))
    }

    fn refresh_result(&self, path: &Path, bytes_read: u64) -> RolloutRefresh {
        let mut summary = parse_lines(self.recent_lines.iter().map(String::as_str), self.max_lines);
        summary.source = Some(path.to_path_buf());
        summary.offset = self.offset.unwrap_or_default();
        RolloutRefresh {
            summary,
            offset: self.offset.unwrap_or_default(),
            bytes_read,
        }
    }
}

impl RolloutSummary {
    pub fn from_lines<'a, I>(lines: I) -> Self
    where
        I: IntoIterator<Item = &'a str>,
    {
        parse_lines(lines, 160)
    }
}

impl TokenUsage {
    pub fn context_left_percent(&self) -> Option<u64> {
        if self.model_context_window == 0 {
            return None;
        }
        let used = self.input_tokens.min(self.model_context_window);
        Some((self.model_context_window - used) * 100 / self.model_context_window)
    }

    pub fn context_used_percent(&self) -> Option<u64> {
        if self.model_context_window == 0 {
            return None;
        }
        Some(self.input_tokens.min(self.model_context_window) * 100 / self.model_context_window)
    }
}

pub fn discover_rollout_for_pid(pid: u32) -> Option<PathBuf> {
    let fd_dir = Path::new("/proc").join(pid.to_string()).join("fd");
    let entries = fs::read_dir(fd_dir).ok()?;
    for entry in entries.flatten() {
        let target = fs::read_link(entry.path()).ok()?;
        if is_rollout_path(&target) {
            return Some(target);
        }
    }
    None
}

fn is_rollout_path(path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return false;
    };
    name.starts_with("rollout-") && name.ends_with(".jsonl")
}

pub fn read_tail_lines(
    path: &Path,
    max_lines: usize,
    chunk_size: usize,
    max_bytes: usize,
) -> io::Result<TailRead> {
    read_tail_lines_with_limit(path, max_lines, chunk_size, max_bytes, DEFAULT_MAX_LINE_LEN)
}

fn read_tail_lines_with_limit(
    path: &Path,
    max_lines: usize,
    chunk_size: usize,
    max_bytes: usize,
    max_line_len: usize,
) -> io::Result<TailRead> {
    let mut file = File::open(path)?;
    let len = file.metadata()?.len();
    if len == 0 {
        return Ok(TailRead {
            lines: Vec::new(),
            offset: 0,
            bytes_read: 0,
        });
    }
    let chunk_size = chunk_size.max(1);
    let max_bytes = max_bytes.max(1) as u64;
    let mut start = len;
    let mut bytes_read = 0_u64;
    let mut bytes = Vec::<u8>::new();
    while start > 0 && bytes_read < max_bytes {
        let read_len = (chunk_size as u64).min(start).min(max_bytes - bytes_read);
        start -= read_len;
        file.seek(SeekFrom::Start(start))?;
        let mut chunk = vec![0_u8; read_len as usize];
        file.read_exact(&mut chunk)?;
        let mut combined = chunk;
        combined.extend_from_slice(&bytes);
        bytes = combined;
        bytes_read += read_len;
        if count_newlines(&bytes) > max_lines {
            break;
        }
    }

    let mut lines = complete_lines_from_bytes(&bytes, start == 0, max_line_len);
    if lines.len() > max_lines {
        lines = lines.split_off(lines.len() - max_lines);
    }
    Ok(TailRead {
        lines,
        offset: len,
        bytes_read,
    })
}

fn count_newlines(bytes: &[u8]) -> usize {
    bytes.iter().filter(|byte| **byte == b'\n').count()
}

fn complete_lines_from_bytes(
    bytes: &[u8],
    starts_at_file_start: bool,
    max_line_len: usize,
) -> Vec<String> {
    let text = String::from_utf8_lossy(bytes);
    let mut parts = text.split('\n').collect::<Vec<_>>();
    if !text.ends_with('\n') {
        parts.pop();
    }
    if !starts_at_file_start && !parts.is_empty() {
        parts.remove(0);
    }
    parts
        .into_iter()
        .filter(|line| !line.is_empty())
        .map(|line| truncate_chars(line, max_line_len))
        .collect()
}

pub fn parse_lines<'a, I>(lines: I, max_items: usize) -> RolloutSummary
where
    I: IntoIterator<Item = &'a str>,
{
    let mut summary = RolloutSummary::default();
    for line in lines {
        parse_line(line, &mut summary);
        trim_summary(&mut summary, max_items);
    }
    summary
}

fn trim_summary(summary: &mut RolloutSummary, max_items: usize) {
    trim_vec(&mut summary.agent_messages, max_items);
    trim_vec(&mut summary.commands, max_items);
    trim_vec(&mut summary.file_changes, max_items);
    trim_vec(&mut summary.compactions, max_items);
    trim_vec(&mut summary.markers, max_items);
}

fn trim_vec<T>(items: &mut Vec<T>, max_items: usize) {
    if items.len() > max_items {
        items.drain(0..items.len() - max_items);
    }
}

fn parse_line(line: &str, summary: &mut RolloutSummary) {
    let timestamp = json_string(line, "timestamp");
    let payload_type = json_string_after(line, "\"payload\"", "type");
    let item_type = json_string_after(line, "\"item\"", "type");
    update_turn(line, summary);

    match (payload_type.as_deref(), item_type.as_deref()) {
        (Some("token_count"), _) => {
            if let Some(token) = parse_token_usage(line, timestamp) {
                summary.token = Some(token);
            }
        }
        (Some("item_completed"), Some("AgentMessage")) => {
            if let Some(message) = parse_agent_message(line, timestamp) {
                summary.agent_messages.push(message);
            }
        }
        (Some("item_completed"), Some("CommandExecution")) => {
            summary.commands.push(parse_command_event(line, timestamp));
        }
        (Some("exec_command_end"), _) => {
            let command = parse_command_event(line, timestamp);
            if !summary.commands.iter().any(|existing| {
                existing.command == command.command && existing.exit_code == command.exit_code
            }) {
                summary.commands.push(command);
            }
        }
        (Some("item_completed"), Some("FileChange")) => {
            summary
                .file_changes
                .extend(parse_file_changes(line, timestamp));
        }
        (Some("item_completed"), Some("ContextCompaction")) => {
            summary.compactions.push(CompactionEvent {
                timestamp,
                completed_at_ms: json_number(line, "completed_at_ms"),
                summary: "context compaction item completed".to_owned(),
            });
        }
        (Some("context_compacted"), _) => {
            summary.compactions.push(CompactionEvent {
                timestamp,
                completed_at_ms: None,
                summary: "context compacted".to_owned(),
            });
        }
        (Some("terminal_interaction"), _) => {
            let command = json_string_after(line, "\"payload\"", "command")
                .unwrap_or_else(|| "terminal interaction".to_owned());
            summary.commands.push(CommandEvent {
                timestamp,
                command: truncate_chars(&command, 512),
                cwd: None,
                exit_code: json_i32(line, "exit_code"),
                duration_ms: duration_ms(line).or_else(|| elapsed_ms(line)),
            });
        }
        (Some("thread_settings_applied"), _) => {
            summary.markers.push("thread settings applied".to_owned());
        }
        _ if line.contains("\"type\":\"world_state\"")
            || line.contains("\"type\": \"world_state\"") =>
        {
            summary.markers.push("world state updated".to_owned());
        }
        _ => {}
    }
}

fn parse_agent_message(line: &str, timestamp: Option<String>) -> Option<AgentMessage> {
    let text = json_string_after(line, "\"content\"", "text")?;
    Some(AgentMessage {
        timestamp,
        phase: json_string_after(line, "\"item\"", "phase"),
        text: truncate_chars(&text, 16 * 1024),
    })
}

fn parse_command_event(line: &str, timestamp: Option<String>) -> CommandEvent {
    let command = parse_command_array(line)
        .map(command_array_display)
        .or_else(|| json_string_after(line, "\"payload\"", "command"))
        .unwrap_or_else(|| "command".to_owned());
    CommandEvent {
        timestamp,
        command: truncate_chars(&command, 512),
        cwd: json_string(line, "cwd"),
        exit_code: json_i32(line, "exit_code"),
        duration_ms: duration_ms(line).or_else(|| elapsed_ms(line)),
    }
}

fn command_array_display(parts: Vec<String>) -> String {
    if parts.len() >= 3
        && (parts[0].ends_with("bash") || parts[0].ends_with("sh"))
        && (parts[1] == "-lc" || parts[1] == "-c")
    {
        return parts[2].clone();
    }
    parts.join(" ")
}

fn parse_file_changes(line: &str, timestamp: Option<String>) -> Vec<FileChange> {
    changes_entries(line)
        .into_iter()
        .map(|(path, body)| FileChange {
            timestamp: timestamp.clone(),
            path,
            change_type: json_string(&body, "type").unwrap_or_else(|| "change".to_owned()),
            move_path: json_string(&body, "move_path"),
        })
        .collect()
}

fn parse_token_usage(line: &str, timestamp: Option<String>) -> Option<TokenUsage> {
    let usage_start = line.find("\"last_token_usage\"")?;
    let usage = &line[usage_start..];
    Some(TokenUsage {
        timestamp,
        input_tokens: json_number(usage, "input_tokens").unwrap_or(0),
        output_tokens: json_number(usage, "output_tokens").unwrap_or(0),
        reasoning_output_tokens: json_number(usage, "reasoning_output_tokens").unwrap_or(0),
        total_tokens: json_number(usage, "total_tokens").unwrap_or(0),
        model_context_window: json_number(line, "model_context_window").unwrap_or(0),
        auto_compact_token_limit: json_number(line, "auto_compact_token_limit"),
    })
}

fn update_turn(line: &str, summary: &mut RolloutSummary) {
    let turn_id = json_string(line, "turn_id");
    let started = json_number(line, "started_at_ms");
    let completed = json_number(line, "completed_at_ms");
    if turn_id.is_none() && started.is_none() && completed.is_none() {
        return;
    }
    match &mut summary.turn {
        Some(turn) if turn.turn_id == turn_id => {
            turn.started_at_ms = min_some(turn.started_at_ms, started);
            turn.completed_at_ms = max_some(turn.completed_at_ms, completed);
            turn.elapsed_ms = elapsed_between(turn.started_at_ms, turn.completed_at_ms);
        }
        _ => {
            summary.turn = Some(TurnTiming {
                turn_id,
                started_at_ms: started,
                completed_at_ms: completed,
                elapsed_ms: elapsed_between(started, completed),
            });
        }
    }
}

fn min_some(left: Option<u64>, right: Option<u64>) -> Option<u64> {
    match (left, right) {
        (Some(left), Some(right)) => Some(left.min(right)),
        (Some(left), None) => Some(left),
        (None, Some(right)) => Some(right),
        (None, None) => None,
    }
}

fn max_some(left: Option<u64>, right: Option<u64>) -> Option<u64> {
    match (left, right) {
        (Some(left), Some(right)) => Some(left.max(right)),
        (Some(left), None) => Some(left),
        (None, Some(right)) => Some(right),
        (None, None) => None,
    }
}

fn elapsed_between(started: Option<u64>, completed: Option<u64>) -> Option<u64> {
    Some(completed?.saturating_sub(started?))
}

fn elapsed_ms(line: &str) -> Option<u64> {
    elapsed_between(
        json_number(line, "started_at_ms"),
        json_number(line, "completed_at_ms"),
    )
}

fn duration_ms(line: &str) -> Option<u64> {
    let duration_index = line.find("\"duration\"")?;
    let duration = &line[duration_index..];
    let secs = json_number::<u64>(duration, "secs").unwrap_or(0);
    let nanos = json_number::<u64>(duration, "nanos").unwrap_or(0);
    Some(secs.saturating_mul(1_000) + nanos / 1_000_000)
}

fn changes_entries(line: &str) -> Vec<(String, String)> {
    let Some(marker) = line.find("\"changes\"") else {
        return Vec::new();
    };
    let Some(open_offset) = line[marker..].find('{') else {
        return Vec::new();
    };
    let start = marker + open_offset;
    let bytes = line.as_bytes();
    let mut entries = Vec::new();
    let mut index = start + 1;
    let mut depth = 1_i32;
    while index < bytes.len() && depth > 0 {
        match bytes[index] {
            b'"' if depth == 1 => {
                let Some((key, after_key)) = parse_json_string_at(line, index) else {
                    break;
                };
                let Some(colon) = skip_ws(bytes, after_key).filter(|pos| bytes[*pos] == b':')
                else {
                    break;
                };
                let value_start = skip_ws(bytes, colon + 1).unwrap_or(colon + 1);
                if value_start < bytes.len() && bytes[value_start] == b'{' {
                    if let Some(value_end) = matching_brace(line, value_start) {
                        entries.push((key, line[value_start..=value_end].to_owned()));
                        index = value_end + 1;
                        continue;
                    }
                }
                index = value_start.saturating_add(1);
            }
            b'"' => {
                if let Some((_, after_string)) = parse_json_string_at(line, index) {
                    index = after_string;
                } else {
                    break;
                }
            }
            b'{' => {
                depth += 1;
                index += 1;
            }
            b'}' => {
                depth -= 1;
                index += 1;
            }
            _ => index += 1,
        }
    }
    entries
}

fn matching_brace(text: &str, open: usize) -> Option<usize> {
    let bytes = text.as_bytes();
    let mut index = open;
    let mut depth = 0_i32;
    while index < bytes.len() {
        match bytes[index] {
            b'"' => {
                let (_, after) = parse_json_string_at(text, index)?;
                index = after;
            }
            b'{' => {
                depth += 1;
                index += 1;
            }
            b'}' => {
                depth -= 1;
                if depth == 0 {
                    return Some(index);
                }
                index += 1;
            }
            _ => index += 1,
        }
    }
    None
}

fn skip_ws(bytes: &[u8], mut index: usize) -> Option<usize> {
    while index < bytes.len() && bytes[index].is_ascii_whitespace() {
        index += 1;
    }
    (index < bytes.len()).then_some(index)
}

fn parse_command_array(line: &str) -> Option<Vec<String>> {
    let command_index = line.find("\"command\"")?;
    let open = line[command_index..].find('[')? + command_index;
    let mut index = open + 1;
    let bytes = line.as_bytes();
    let mut out = Vec::new();
    while index < bytes.len() {
        index = skip_ws(bytes, index)?;
        match bytes[index] {
            b']' => return Some(out),
            b',' => {
                index += 1;
            }
            b'"' => {
                let (value, after) = parse_json_string_at(line, index)?;
                out.push(value);
                index = after;
            }
            _ => return None,
        }
    }
    None
}

fn json_string_after(text: &str, marker: &str, key: &str) -> Option<String> {
    let index = text.find(marker)? + marker.len();
    json_string(&text[index..], key)
}

fn json_string(text: &str, key: &str) -> Option<String> {
    let marker = format!("\"{key}\"");
    let index = text.find(&marker)? + marker.len();
    let bytes = text.as_bytes();
    let colon = text[index..].find(':')? + index;
    let value_start = skip_ws(bytes, colon + 1)?;
    if bytes[value_start] != b'"' {
        return None;
    }
    parse_json_string_at(text, value_start).map(|(value, _)| value)
}

fn json_i32(text: &str, key: &str) -> Option<i32> {
    json_number::<i64>(text, key).and_then(|value| value.try_into().ok())
}

fn json_number<T>(text: &str, key: &str) -> Option<T>
where
    T: std::str::FromStr,
{
    let marker = format!("\"{key}\"");
    let index = text.find(&marker)? + marker.len();
    let colon = text[index..].find(':')? + index;
    let rest = text[colon + 1..].trim_start();
    let number = rest
        .chars()
        .take_while(|ch| ch.is_ascii_digit() || *ch == '-')
        .collect::<String>();
    if number.is_empty() {
        None
    } else {
        number.parse().ok()
    }
}

fn parse_json_string_at(text: &str, quote_index: usize) -> Option<(String, usize)> {
    let mut chars = text[quote_index..].char_indices();
    if chars.next()?.1 != '"' {
        return None;
    }
    let mut out = String::new();
    let mut escaped = false;
    for (offset, ch) in chars {
        if escaped {
            match ch {
                '"' => out.push('"'),
                '\\' => out.push('\\'),
                '/' => out.push('/'),
                'b' => out.push('\u{0008}'),
                'f' => out.push('\u{000c}'),
                'n' => out.push('\n'),
                'r' => out.push('\r'),
                't' => out.push('\t'),
                'u' => out.push('?'),
                other => out.push(other),
            }
            escaped = false;
        } else if ch == '\\' {
            escaped = true;
        } else if ch == '"' {
            return Some((out, quote_index + offset + ch.len_utf8()));
        } else {
            out.push(ch);
        }
    }
    None
}

fn truncate_chars(text: &str, max_chars: usize) -> String {
    let mut out = String::new();
    for ch in text.chars().take(max_chars) {
        out.push(ch);
    }
    if text.chars().count() > max_chars {
        out.push('…');
    }
    out
}

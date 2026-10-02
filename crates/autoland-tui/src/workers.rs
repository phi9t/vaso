use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, Instant};

use crate::process::{self, ChildEnv};

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum WorkerState {
    Running,
    Exited(i32),
    Gone,
    Unknown,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorkerRun {
    pub agent: String,
    pub run_id: String,
    pub path: PathBuf,
    pub pid: Option<u32>,
    pub worktree: Option<PathBuf>,
    pub branch: Option<String>,
    pub base: Option<String>,
    pub started_utc: Option<String>,
    pub state: WorkerState,
    pub digest: Vec<String>,
    pub commits: Vec<String>,
}

pub fn scan_workers(estate_root: &Path) -> io::Result<Vec<WorkerRun>> {
    let agents_dir = estate_root.join("agents");
    let entries = match fs::read_dir(&agents_dir) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error),
    };
    let mut runs = Vec::new();
    for agent in entries {
        let agent = agent?;
        if !agent.file_type()?.is_dir() {
            continue;
        }
        let runs_dir = agent.path().join("runs");
        let run_entries = match fs::read_dir(runs_dir) {
            Ok(entries) => entries,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error),
        };
        let agent_name = agent.file_name().to_string_lossy().to_string();
        for run in run_entries {
            let run = run?;
            if !run.file_type()?.is_dir() {
                continue;
            }
            runs.push(load_run(&agent_name, &run.path())?);
        }
    }
    runs.sort_by(|a, b| {
        b.started_utc
            .cmp(&a.started_utc)
            .then_with(|| b.run_id.cmp(&a.run_id))
    });
    Ok(runs)
}

fn load_run(agent: &str, path: &Path) -> io::Result<WorkerRun> {
    let meta_text = fs::read_to_string(path.join("meta.json")).unwrap_or_default();
    let pid = json_number(&meta_text, "pid").and_then(|value| value.try_into().ok());
    let worktree = json_string(&meta_text, "worktree").map(PathBuf::from);
    let base = json_string(&meta_text, "base");
    let state = run_state(path, pid);
    let commits = match (worktree.as_ref(), base.as_ref()) {
        (Some(worktree), Some(base)) => commits_since(worktree, base).unwrap_or_default(),
        _ => Vec::new(),
    };
    Ok(WorkerRun {
        agent: agent.to_owned(),
        run_id: path
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("?")
            .to_owned(),
        path: path.to_path_buf(),
        pid,
        worktree,
        branch: json_string(&meta_text, "branch"),
        base,
        started_utc: json_string(&meta_text, "started_utc"),
        state,
        digest: digest_lines(&path.join("events.jsonl"), 12),
        commits,
    })
}

fn run_state(path: &Path, pid: Option<u32>) -> WorkerState {
    match fs::read_to_string(path.join("exit_code")) {
        Ok(text) => text
            .trim()
            .parse::<i32>()
            .map(WorkerState::Exited)
            .unwrap_or(WorkerState::Unknown),
        Err(_) => match pid {
            Some(pid) if process::process_alive(pid) => WorkerState::Running,
            Some(_) => WorkerState::Gone,
            None => WorkerState::Unknown,
        },
    }
}

fn digest_lines(path: &Path, limit: usize) -> Vec<String> {
    let text = fs::read_to_string(path).unwrap_or_default();
    let mut lines = Vec::new();
    for raw in text.lines() {
        if raw.contains("\"type\":\"command_execution\"")
            || raw.contains("\"type\": \"command_execution\"")
        {
            let command = json_string(raw, "command").unwrap_or_else(|| "command".to_owned());
            let rc = json_number(raw, "exit_code")
                .map(|value| value.to_string())
                .unwrap_or_else(|| "?".to_owned());
            let tail = json_string(raw, "aggregated_output")
                .and_then(|out| out.lines().last().map(str::to_owned))
                .unwrap_or_default();
            lines.push(format!("cmd rc={rc}: {command} | {tail}"));
        } else if raw.contains("\"turn.completed\"") {
            let input = json_number(raw, "input_tokens").unwrap_or(0);
            let output = json_number(raw, "output_tokens").unwrap_or(0);
            lines.push(format!("turn done: in={input} out={output}"));
        } else if raw.contains("\"type\":\"error\"") || raw.contains("\"turn.failed\"") {
            lines.push(format!(
                "ERROR: {}",
                raw.chars().take(180).collect::<String>()
            ));
        }
    }
    if lines.len() > limit {
        lines.split_off(lines.len() - limit)
    } else {
        lines
    }
}

fn commits_since(worktree: &Path, base: &str) -> io::Result<Vec<String>> {
    let output = ChildEnv::from_current().output(
        "git",
        &[
            "-C",
            worktree.to_str().unwrap_or("."),
            "log",
            "--reverse",
            "--format=%s",
            &format!("{base}..HEAD"),
        ],
        None,
    )?;
    if !output.status.success() {
        return Ok(Vec::new());
    }
    Ok(String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(str::to_owned)
        .collect())
}

pub fn stop_run(run_dir: &Path, grace: Duration) -> io::Result<StopResult> {
    if fs::read_to_string(run_dir.join("exit_code")).is_ok() {
        return Ok(StopResult::NotRunning);
    }
    let meta_text = fs::read_to_string(run_dir.join("meta.json"))?;
    let pid = json_number(&meta_text, "pid")
        .and_then(|value| value.try_into().ok())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "meta.json missing pid"))?;
    if !process::process_alive(pid) {
        return Ok(StopResult::NotRunning);
    }
    process::signal_process_group(pid, libc::SIGTERM);
    let deadline = Instant::now() + grace;
    while process::process_alive(pid) && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(20));
    }
    if process::process_alive(pid) {
        process::signal_process_group(pid, libc::SIGKILL);
        let kill_deadline = Instant::now() + grace;
        while process::process_alive(pid) && Instant::now() < kill_deadline {
            thread::sleep(Duration::from_millis(20));
        }
        if process::process_alive(pid) {
            return Err(io::Error::other(format!(
                "worker process group {pid} is still alive after SIGKILL"
            )));
        }
        Ok(StopResult::Killed { pid })
    } else {
        Ok(StopResult::Terminated { pid })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StopResult {
    NotRunning,
    Terminated { pid: u32 },
    Killed { pid: u32 },
}

fn json_string(text: &str, key: &str) -> Option<String> {
    let marker = format!("\"{key}\"");
    let index = text.find(&marker)?;
    let after_key = &text[index + marker.len()..];
    let colon = after_key.find(':')?;
    let mut rest = after_key[colon + 1..].trim_start().chars();
    if rest.next()? != '"' {
        return None;
    }
    let mut out = String::new();
    let mut escaped = false;
    for ch in rest {
        if escaped {
            out.push(match ch {
                'n' => '\n',
                'r' => '\r',
                't' => '\t',
                '"' => '"',
                '\\' => '\\',
                other => other,
            });
            escaped = false;
        } else if ch == '\\' {
            escaped = true;
        } else if ch == '"' {
            return Some(out);
        } else {
            out.push(ch);
        }
    }
    None
}

fn json_number(text: &str, key: &str) -> Option<i64> {
    let marker = format!("\"{key}\"");
    let index = text.find(&marker)?;
    let after_key = &text[index + marker.len()..];
    let colon = after_key.find(':')?;
    let rest = after_key[colon + 1..].trim_start();
    let number = rest
        .chars()
        .take_while(|ch| ch.is_ascii_digit() || *ch == '-')
        .collect::<String>();
    number.parse().ok()
}

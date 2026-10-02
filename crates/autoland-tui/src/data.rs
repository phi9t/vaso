use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

use crate::age::{self, parse_utc_timestamp};
use crate::logline::{classify_log_line, is_attention, LogEntry, LogKind};
use crate::outbox::{self, DraftStatus};
use crate::process;
use crate::rollout::{self, RolloutCursor, RolloutSummary};
use crate::settings::Settings;
use crate::state::{parse_state, AutolandState};
use crate::tmux::{self, PaneCapture, PaneStatus};
use crate::tracker::{self, FollowerLogTail, TrackerComment};
use crate::workers::{self, WorkerRun};

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LoopMode {
    Attached,
    Running(u32),
    Exited(String),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FollowerStatus {
    Gone,
    Unknown,
    Alive {
        pid: u32,
        busy: Vec<(String, usize)>,
    },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RefStatus {
    pub target: String,
    pub landing: String,
    pub pending: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CommitInfo {
    pub sha: String,
    pub subject: String,
    pub timestamp: u64,
}

#[derive(Clone, Debug)]
pub struct DashboardData {
    pub settings: Settings,
    pub now: SystemTime,
    pub mode: LoopMode,
    pub state: Option<AutolandState>,
    pub last_activity: Option<u64>,
    pub pane_status: Option<PaneStatus>,
    pub pane_busy_elapsed: Option<String>,
    pub loop_pid_alive: Option<bool>,
    pub state_error: Option<String>,
    pub follower: FollowerStatus,
    pub refs: RefStatus,
    pub events: Vec<LogEntry>,
    pub comments: Vec<TrackerComment>,
    pub follower_log: Option<FollowerLogTail>,
    pub rollout: Option<RolloutSummary>,
    pub rollout_error: Option<String>,
    pub uncommitted: Option<UncommittedAge>,
    pub commits: Vec<CommitInfo>,
    pub lands_per_hour: Vec<u64>,
    pub target_commits_per_hour: Vec<u64>,
    pub last_attention_kind: Option<LogKind>,
    pub last_attention: Option<String>,
    pub errors: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct AttentionItem {
    pub label: String,
    pub summary: String,
    pub detail: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UncommittedAge {
    pub count: usize,
    pub oldest_minutes: u64,
    pub newest_minutes: u64,
    pub threshold_minutes: u64,
}

impl UncommittedAge {
    pub fn is_red(&self) -> bool {
        self.oldest_minutes >= self.threshold_minutes.saturating_mul(3)
    }

    pub fn severity(&self) -> &'static str {
        if self.is_red() {
            "RED"
        } else {
            "YELLOW"
        }
    }
}

#[derive(Clone, Debug)]
pub struct CockpitData {
    pub dashboard: DashboardData,
    pub pane: Option<PaneCapture>,
    pub pane_error: Option<String>,
    pub workers: Vec<WorkerRun>,
    pub outbox: Vec<outbox::Draft>,
    pub decisions: Vec<String>,
    pub attention: Vec<AttentionItem>,
    pub errors: Vec<String>,
}

pub fn collect_dashboard(
    settings: &Settings,
    mode: LoopMode,
    child_lines: &[String],
) -> DashboardData {
    collect_dashboard_with_rollout_cursor(settings, mode, child_lines, None)
}

pub fn collect_dashboard_with_rollout_cursor(
    settings: &Settings,
    mode: LoopMode,
    child_lines: &[String],
    rollout_cursor: Option<&mut RolloutCursor>,
) -> DashboardData {
    let now = SystemTime::now();
    let mut errors = Vec::new();
    let (state, state_error) = match fs::read_to_string(&settings.state) {
        Ok(text) => match parse_state(&text) {
            Ok(state) => (Some(state), None),
            Err(error) => (None, Some(error)),
        },
        Err(error) if error.kind() == io::ErrorKind::NotFound => (None, None),
        Err(error) => (None, Some(error.to_string())),
    };
    let loop_pid_alive = state
        .as_ref()
        .map(|state| process::process_alive(state.pid));

    let mut event_lines = read_tail_lines(&settings.log, 500).unwrap_or_else(|error| {
        errors.push(format!("log: {error}"));
        Vec::new()
    });
    event_lines.extend_from_slice(child_lines);
    let events: Vec<LogEntry> = event_lines
        .iter()
        .map(|line| classify_log_line(line))
        .collect();
    let last_attention = events
        .iter()
        .rev()
        .find(|entry| is_attention(entry.kind))
        .map(|entry| (entry.kind, entry.line.clone()));
    let (last_attention_kind, last_attention) = match last_attention {
        Some((kind, line)) => (Some(kind), Some(line)),
        None => (None, None),
    };

    let tracker_dir = settings.follower.join(&settings.tracker);
    let comments = tracker::extract_comments(&tracker_dir, 12).unwrap_or_else(|error| {
        errors.push(format!("tracker: {error}"));
        Vec::new()
    });
    let follower_log =
        tracker::newest_follower_log(&settings.follower_logs, 30).unwrap_or_else(|error| {
            errors.push(format!("follower logs: {error}"));
            None
        });
    let (rollout, rollout_error) = collect_rollout(settings, rollout_cursor);

    let follower = follower_status(settings.follower_pid);
    let refs = ref_status(settings).unwrap_or_else(|error| {
        errors.push(format!("git refs: {error}"));
        RefStatus {
            target: "?".to_owned(),
            landing: "?".to_owned(),
            pending: "unknown".to_owned(),
        }
    });
    let commits = recent_commits(settings).unwrap_or_else(|error| {
        errors.push(format!("git log: {error}"));
        Vec::new()
    });
    let last_activity =
        live_last_activity(settings, commits.first().map(|commit| commit.timestamp));
    let uncommitted = uncommitted_age(settings, now).unwrap_or_else(|error| {
        errors.push(format!("uncommitted: {error}"));
        None
    });
    let lands_per_hour = lands_per_hour(&events, state.as_ref());
    let target_commits_per_hour = target_commits_per_hour(&commits, now);

    DashboardData {
        settings: settings.clone(),
        now,
        mode,
        state,
        last_activity,
        pane_status: None,
        pane_busy_elapsed: None,
        loop_pid_alive,
        state_error,
        follower,
        refs,
        events,
        comments,
        follower_log,
        rollout,
        rollout_error,
        uncommitted,
        commits,
        lands_per_hour,
        target_commits_per_hour,
        last_attention_kind,
        last_attention,
        errors,
    }
}

pub fn collect_cockpit(
    settings: &Settings,
    mode: LoopMode,
    child_lines: &[String],
    include_pane: bool,
) -> CockpitData {
    collect_cockpit_with_rollout_cursor(settings, mode, child_lines, include_pane, None)
}

pub fn collect_cockpit_with_rollout_cursor(
    settings: &Settings,
    mode: LoopMode,
    child_lines: &[String],
    include_pane: bool,
    rollout_cursor: Option<&mut RolloutCursor>,
) -> CockpitData {
    let mut dashboard =
        collect_dashboard_with_rollout_cursor(settings, mode, child_lines, rollout_cursor);
    let (pane, pane_error) = if include_pane {
        let socket = std::env::var("VASO_TMUX_SOCKET").ok();
        match tmux::capture_pane_with_env(
            settings.follower_pane.as_deref(),
            160,
            socket.as_deref(),
            &settings.child_env(),
        ) {
            Ok(pane) => (pane, None),
            Err(error) => (None, Some(error.to_string())),
        }
    } else {
        (None, None)
    };
    if let Some(pane) = &pane {
        dashboard.pane_status = Some(pane.status);
        dashboard.pane_busy_elapsed = pane.busy_elapsed.clone();
    }
    let mut errors = Vec::new();
    let workers = workers::scan_workers(&settings.vaso_estate_root).unwrap_or_else(|error| {
        errors.push(format!("workers: {error}"));
        Vec::new()
    });
    let outbox = outbox::load_outbox(&settings.vaso_estate_root).unwrap_or_else(|error| {
        errors.push(format!("outbox: {error}"));
        Vec::new()
    });
    let decisions = human_decisions(settings).unwrap_or_else(|error| {
        errors.push(format!("decisions: {error}"));
        Vec::new()
    });
    let attention = attention_items(&dashboard, &outbox, &decisions);
    CockpitData {
        dashboard,
        pane,
        pane_error,
        workers,
        outbox,
        decisions,
        attention,
        errors,
    }
}

pub fn read_tail_lines(path: &Path, max_lines: usize) -> io::Result<Vec<String>> {
    let text = fs::read_to_string(path)?;
    let mut lines: Vec<String> = text.lines().map(str::to_owned).collect();
    if lines.len() > max_lines {
        lines = lines.split_off(lines.len() - max_lines);
    }
    Ok(lines)
}

fn collect_rollout(
    settings: &Settings,
    rollout_cursor: Option<&mut RolloutCursor>,
) -> (Option<RolloutSummary>, Option<String>) {
    let Some(path) = rollout_path(settings) else {
        return (None, None);
    };
    let result = match rollout_cursor {
        Some(cursor) => cursor.refresh(&path).map(|refresh| refresh.summary),
        None => rollout::read_tail_lines(&path, 160, 1024 * 1024, 16 * 1024 * 1024).map(|tail| {
            let mut summary = rollout::parse_lines(tail.lines.iter().map(String::as_str), 160);
            summary.source = Some(path.clone());
            summary.offset = tail.offset;
            summary
        }),
    };
    match result {
        Ok(summary) => (Some(summary), None),
        Err(error) => (None, Some(format!("{}: {error}", path.display()))),
    }
}

fn rollout_path(settings: &Settings) -> Option<PathBuf> {
    settings.follower_rollout.clone().or_else(|| {
        settings
            .follower_pid
            .and_then(rollout::discover_rollout_for_pid)
    })
}

pub fn follower_status(pid: Option<u32>) -> FollowerStatus {
    let Some(pid) = pid else {
        return FollowerStatus::Unknown;
    };
    if !process::process_alive(pid) {
        return FollowerStatus::Gone;
    }
    let busy = process::busy_commands(pid);
    FollowerStatus::Alive { pid, busy }
}

pub fn extract_busy_commands_from_names<'a>(
    names: impl IntoIterator<Item = &'a str>,
) -> Vec<(String, usize)> {
    let watched = [
        "bazel", "insula", "run.sh", "spack", "python", "python3", "pytest", "git",
    ];
    let mut counts = BTreeMap::<String, usize>::new();
    for normalized in names {
        for name in watched {
            if normalized == name || (name == "python" && normalized == "python3") {
                *counts.entry(normalized.to_owned()).or_default() += 1;
                break;
            }
        }
    }
    counts.into_iter().collect()
}

fn ref_status(settings: &Settings) -> io::Result<RefStatus> {
    let target = git(settings, &["rev-parse", "--short", &settings.target])?;
    let landing = git(settings, &["rev-parse", "--short", &settings.landing])?;
    let landed = settings
        .child_env()
        .status(
            "git",
            &[
                "-C",
                settings.lead.to_str().unwrap_or("."),
                "merge-base",
                "--is-ancestor",
                &settings.landing,
                &settings.target,
            ],
            None,
        )?
        .success();
    let pending = if landed {
        "landed".to_owned()
    } else {
        let count = git(
            settings,
            &[
                "rev-list",
                "--count",
                &format!("{}..{}", settings.target, settings.landing),
            ],
        )
        .unwrap_or_else(|_| "?".to_owned());
        format!("{count} lead commit(s) waiting")
    };
    Ok(RefStatus {
        target,
        landing,
        pending,
    })
}

fn recent_commits(settings: &Settings) -> io::Result<Vec<CommitInfo>> {
    let output = git(
        settings,
        &["log", "--format=%h%x00%ct%x00%s", "-40", &settings.target],
    )?;
    let mut commits = Vec::new();
    for line in output.lines() {
        let parts: Vec<&str> = line.split('\0').collect();
        if parts.len() != 3 {
            continue;
        }
        commits.push(CommitInfo {
            sha: parts[0].to_owned(),
            timestamp: parts[1].parse().unwrap_or(0),
            subject: parts[2].to_owned(),
        });
    }
    Ok(commits)
}

fn uncommitted_age(settings: &Settings, now: SystemTime) -> io::Result<Option<UncommittedAge>> {
    let output = settings.child_env().output(
        "git",
        &[
            "-C",
            settings.follower.to_str().unwrap_or("."),
            "status",
            "--porcelain",
            "-z",
            "--untracked-files=no",
        ],
        None,
    )?;
    if !output.status.success() {
        return Err(io::Error::other(
            String::from_utf8_lossy(&output.stderr).trim().to_owned(),
        ));
    }
    let mut mtimes = Vec::new();
    for path in tracked_dirty_paths(&output.stdout) {
        let full = settings.follower.join(path);
        let Ok(metadata) = fs::metadata(&full) else {
            continue;
        };
        if metadata.is_file() {
            if let Ok(modified) = metadata.modified() {
                mtimes.push(age::unix_time(modified));
            }
        }
    }
    if mtimes.is_empty() {
        return Ok(None);
    }
    let newest_dirty = mtimes.iter().copied().max().unwrap_or(0);
    let target_commit = git(settings, &["log", "-1", "--format=%ct", &settings.target])
        .ok()
        .and_then(|text| text.parse::<u64>().ok())
        .unwrap_or(0);
    if target_commit >= newest_dirty {
        return Ok(None);
    }
    let now = age::unix_time(now);
    let threshold_seconds = settings.uncommitted_min.saturating_mul(60);
    let newest_age = now.saturating_sub(newest_dirty);
    if newest_age < threshold_seconds {
        return Ok(None);
    }
    let oldest_dirty = mtimes.iter().copied().min().unwrap_or(newest_dirty);
    Ok(Some(UncommittedAge {
        count: mtimes.len(),
        oldest_minutes: now.saturating_sub(oldest_dirty) / 60,
        newest_minutes: newest_age / 60,
        threshold_minutes: settings.uncommitted_min,
    }))
}

fn tracked_dirty_paths(status: &[u8]) -> Vec<PathBuf> {
    let entries = status
        .split(|byte| *byte == 0)
        .filter(|entry| !entry.is_empty())
        .collect::<Vec<_>>();
    let mut paths = Vec::new();
    let mut index = 0;
    while index < entries.len() {
        let entry = entries[index];
        if entry.len() < 4 {
            index += 1;
            continue;
        }
        let x = entry[0] as char;
        let y = entry[1] as char;
        if x == '?' && y == '?' {
            index += 1;
            continue;
        }
        if x == 'R' || x == 'C' {
            if index + 1 < entries.len() {
                paths.push(PathBuf::from(
                    String::from_utf8_lossy(entries[index + 1]).to_string(),
                ));
                index += 2;
                continue;
            }
        } else {
            let path = String::from_utf8_lossy(&entry[3..]).to_string();
            if !path.is_empty() {
                paths.push(PathBuf::from(path));
            }
        }
        index += 1;
    }
    paths
}

fn live_last_activity(settings: &Settings, target_commit_time: Option<u64>) -> Option<u64> {
    let mut newest = target_commit_time;
    for path in [
        settings.follower.join(&settings.tracker),
        settings.follower_logs.clone(),
    ] {
        if let Some(timestamp) = latest_file_mtime(&path) {
            newest = Some(
                newest
                    .map(|current| current.max(timestamp))
                    .unwrap_or(timestamp),
            );
        }
    }
    newest
}

fn latest_file_mtime(path: &Path) -> Option<u64> {
    let metadata = fs::metadata(path).ok()?;
    if metadata.is_file() {
        return metadata.modified().ok().map(age::unix_time);
    }
    if !metadata.is_dir() {
        return None;
    }
    let mut newest = None;
    let entries = fs::read_dir(path).ok()?;
    for entry in entries.flatten() {
        if let Some(timestamp) = latest_file_mtime(&entry.path()) {
            newest = Some(
                newest
                    .map(|current: u64| current.max(timestamp))
                    .unwrap_or(timestamp),
            );
        }
    }
    newest
}

fn target_commits_per_hour(commits: &[CommitInfo], now: SystemTime) -> Vec<u64> {
    let now = age::unix_time(now);
    let mut buckets = vec![0_u64; 24];
    for commit in commits {
        if commit.timestamp > now {
            continue;
        }
        let age_hours = (now - commit.timestamp) / 3_600;
        if age_hours >= 24 {
            continue;
        }
        let index = 23 - age_hours as usize;
        buckets[index] += 1;
    }
    buckets
}

fn git(settings: &Settings, args: &[&str]) -> io::Result<String> {
    let mut full_args = vec!["-C", settings.lead.to_str().unwrap_or(".")];
    full_args.extend_from_slice(args);
    let output = settings.child_env().output("git", &full_args, None)?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
    } else {
        Err(io::Error::other(
            String::from_utf8_lossy(&output.stderr).trim().to_owned(),
        ))
    }
}

fn lands_per_hour(events: &[LogEntry], state: Option<&AutolandState>) -> Vec<u64> {
    let start = state
        .map(|state| state.start)
        .or_else(|| {
            events
                .iter()
                .filter_map(|event| event.timestamp.as_deref().and_then(parse_utc_timestamp))
                .min()
        })
        .unwrap_or_else(|| age::unix_time(SystemTime::now()));
    let mut buckets = vec![0_u64; 12];
    for event in events.iter().filter(|event| event.kind == LogKind::Landed) {
        let Some(ts) = event.timestamp.as_deref().and_then(parse_utc_timestamp) else {
            continue;
        };
        if ts < start {
            continue;
        }
        let index = ((ts - start) / 3_600) as usize;
        if index >= buckets.len() {
            buckets.resize(index + 1, 0);
        }
        buckets[index] += 1;
    }
    if buckets.len() > 24 {
        buckets.split_off(buckets.len() - 24)
    } else {
        buckets
    }
}

impl DashboardData {
    pub fn heartbeat_age(&self) -> Option<Duration> {
        self.state
            .as_ref()
            .map(|state| Duration::from_secs(age::unix_time(self.now).saturating_sub(state.beat)))
    }

    pub fn idle_seconds(&self) -> Option<u64> {
        self.last_activity
            .map(|last_activity| age::unix_time(self.now).saturating_sub(last_activity))
    }

    pub fn review_seconds_left(&self) -> Option<u64> {
        self.state.as_ref().map(|state| {
            let end = state.start + state.batch_hours * 3_600;
            end.saturating_sub(age::unix_time(self.now))
        })
    }
}

pub fn attention_items(
    data: &DashboardData,
    outbox: &[outbox::Draft],
    decisions: &[String],
) -> Vec<AttentionItem> {
    let mut items = Vec::new();
    if let (Some(kind), Some(line)) = (data.last_attention_kind, data.last_attention.as_ref()) {
        items.push(attention_item(attention_name(kind), line.clone()));
    }
    if data.loop_pid_alive == Some(false) || matches!(data.mode, LoopMode::Exited(_)) {
        items.push(attention_item(
            "LOOP NOT RUNNING",
            data.heartbeat_age()
                .map(|age| format!("heartbeat {} ago", age::format_duration(age.as_secs())))
                .unwrap_or_else(|| "heartbeat missing".to_owned()),
        ));
    } else if data
        .heartbeat_age()
        .map(|age| age.as_secs() > data.settings.interval * 3)
        .unwrap_or(true)
    {
        items.push(attention_item(
            "STALE",
            data.heartbeat_age()
                .map(|age| format!("heartbeat {} ago", age::format_duration(age.as_secs())))
                .unwrap_or_else(|| "heartbeat missing".to_owned()),
        ));
    }
    if let Some(idle) = data.idle_seconds() {
        if data.pane_status == Some(PaneStatus::Busy) {
            items.push(attention_item("BUSY", busy_detail(data)));
        } else {
            let stall = data.settings.stall_min * 60;
            if stall > 0 && idle >= stall {
                items.push(attention_item(
                    "IDLE",
                    format!(
                        "follower idle {} / {}",
                        age::format_duration(idle),
                        age::format_duration(stall)
                    ),
                ));
            }
        }
    } else if data.pane_status == Some(PaneStatus::Busy) {
        items.push(attention_item("BUSY", busy_detail(data)));
    }
    if let Some(rollout) = &data.rollout {
        if compaction_recent(data, rollout) {
            items.push(attention_item(
                "COMPACTED",
                "TRAE context compacted in the last 30 min".to_owned(),
            ));
        }
        if let Some(token) = &rollout.token {
            if token
                .context_left_percent()
                .map(|left| left <= 25)
                .unwrap_or(false)
            {
                let used = token.context_used_percent().unwrap_or(0);
                let left = token.context_left_percent().unwrap_or(0);
                items.push(attention_item(
                    "CONTEXT LOW",
                    format!("context left {left}% (used {used}%)"),
                ));
            }
        }
    }
    if let Some(uncommitted) = &data.uncommitted {
        items.push(attention_item(
            "UNCOMMITTED_AGE",
            format!(
                "{} files, oldest {} min, newest {} min ({})",
                uncommitted.count,
                uncommitted.oldest_minutes,
                uncommitted.newest_minutes,
                uncommitted.severity()
            ),
        ));
    }
    for comment in &data.comments {
        let text = comment.text.to_lowercase();
        if text.contains("blocked") || text.contains("decision needed") {
            items.push(attention_item(
                "TRACKER",
                format!("{}: {}", comment.file, one_line(&comment.text)),
            ));
        }
    }
    let pending = outbox
        .iter()
        .filter(|draft| draft.status == DraftStatus::Draft)
        .count();
    if pending > 0 {
        items.push(attention_item(
            "OUTBOX",
            format!("{pending} draft(s) waiting for human confirmation"),
        ));
    }
    for decision in decisions.iter().take(3) {
        items.push(AttentionItem {
            label: "DECISION".to_owned(),
            summary: decision_summary(decision),
            detail: decision.clone(),
        });
    }
    if items.is_empty() {
        items.push(attention_item("OK", "no attention items".to_owned()));
    }
    items
}

fn compaction_recent(data: &DashboardData, rollout: &RolloutSummary) -> bool {
    let now_ms = age::unix_time(data.now).saturating_mul(1_000);
    rollout.compactions.iter().any(|event| {
        event
            .completed_at_ms
            .map(|completed| now_ms.saturating_sub(completed) <= 30 * 60 * 1_000)
            .unwrap_or_else(|| {
                event
                    .timestamp
                    .as_deref()
                    .and_then(parse_utc_timestamp)
                    .map(|timestamp| age::unix_time(data.now).saturating_sub(timestamp) <= 30 * 60)
                    .unwrap_or(false)
            })
    })
}

fn attention_item(label: &str, detail: String) -> AttentionItem {
    AttentionItem {
        label: label.to_owned(),
        summary: one_line(&detail),
        detail,
    }
}

fn attention_name(kind: LogKind) -> &'static str {
    match kind {
        LogKind::Red => "RED",
        LogKind::Rewritten => "REWRITTEN",
        LogKind::Stall => "STALL",
        LogKind::Gone => "GONE",
        LogKind::Io => "IO",
        LogKind::Backlog => "BACKLOG",
        LogKind::Uncommitted => "UNCOMMITTED",
        LogKind::Review => "REVIEW",
        LogKind::Start => "START",
        LogKind::Moved => "MOVED",
        LogKind::Landed => "LANDED",
        LogKind::Relay => "RELAY",
    }
}

fn busy_detail(data: &DashboardData) -> String {
    data.pane_busy_elapsed
        .as_ref()
        .map(|elapsed| format!("{elapsed} TRAE turn active"))
        .unwrap_or_else(|| "TRAE turn active".to_owned())
}

fn human_decisions(settings: &Settings) -> io::Result<Vec<String>> {
    let mut decisions = Vec::new();
    for spec in &settings.decision_specs {
        let Some(text) = read_decision_spec(settings, spec)? else {
            continue;
        };
        decisions.extend(extract_human_decisions(&text));
    }
    Ok(decisions)
}

fn read_decision_spec(settings: &Settings, spec: &Path) -> io::Result<Option<String>> {
    for repo in [&settings.lead, &settings.follower] {
        let path = if spec.is_absolute() {
            spec.to_path_buf()
        } else {
            repo.join(spec)
        };
        match fs::read_to_string(&path) {
            Ok(text) => return Ok(Some(text)),
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error),
        }
    }
    Ok(None)
}

pub fn extract_human_decisions(text: &str) -> Vec<String> {
    let mut in_section = false;
    let mut items = Vec::new();
    let mut current: Option<String> = None;
    for line in text.lines() {
        if line.starts_with("## ") {
            if in_section {
                break;
            }
            in_section = line.trim() == "## Decisions for the human";
            continue;
        }
        if !in_section {
            continue;
        }
        if let Some(rest) = line.strip_prefix("- ") {
            if let Some(item) = current.take() {
                push_open_decision(&mut items, item);
            }
            current = Some(rest.to_owned());
        } else if let Some(item) = &mut current {
            if !line.trim().is_empty() {
                item.push('\n');
                item.push_str(line.trim());
            }
        }
    }
    if let Some(item) = current {
        push_open_decision(&mut items, item);
    }
    items
}

fn push_open_decision(items: &mut Vec<String>, item: String) {
    let item = item.trim().to_owned();
    if item.is_empty() || decision_is_decided(&item) {
        return;
    }
    items.push(item);
}

fn decision_is_decided(item: &str) -> bool {
    item.contains("DECIDED")
        || item.lines().any(|line| {
            let lower = line.trim_start().to_lowercase();
            lower.starts_with("status:") && lower.contains("decided")
        })
}

fn one_line(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn decision_summary(text: &str) -> String {
    let first = text
        .lines()
        .find(|line| !line.trim().is_empty())
        .map(one_line)
        .unwrap_or_default();
    if let Some(rest) = first.strip_prefix("**") {
        if let Some((label, _)) = rest.split_once("**") {
            return label.trim().to_owned();
        }
    }
    first
}

pub fn sample_dashboard() -> DashboardData {
    let settings = Settings {
        vaso_estate_root: "/estate".into(),
        lead: "/repo/.worktrees/autoland".into(),
        follower: "/repo".into(),
        target: "vaso/target".to_owned(),
        landing: "vaso/landing".to_owned(),
        lead_branch: "vaso/autoland-tui".to_owned(),
        tracker: ".scratch/pytorch-frontier-convergence".into(),
        decision_specs: vec![
            ".scratch/pytorch-frontier-convergence/spec.md".into(),
            ".scratch/native-pytorch-build/spec.md".into(),
        ],
        agent: "claude".to_owned(),
        follower_agent: "trae".to_owned(),
        agent_root: "/estate/agents/claude".into(),
        follower_logs: "/estate/agents/trae/logs".into(),
        tmpdir: "/estate/agents/claude/tmp".into(),
        vaso_bazel_ob: "/estate/agents/claude/bazel-ob".into(),
        stall_min: 45,
        batch_hours: 4,
        interval: 60,
        backlog_commits: 3,
        backlog_minutes: 30,
        backlog_rate_limit_min: 30,
        uncommitted_min: 60,
        uncommitted_rate_limit_min: 30,
        log: "/estate/agents/claude/autoland.log".into(),
        state: "/estate/agents/claude/autoland.state".into(),
        follower_pid: Some(4242),
        follower_pane: Some("%3".to_owned()),
        follower_rollout: None,
    };
    let now = age::system_time_from_epoch(1_790_685_600);
    let mut lines = vec![
        "2026-09-28T13:00:00Z START target=a000000 landing=a000000".to_owned(),
        "2026-09-28T13:05:00Z MOVED a000000 -> a000001: Commit fixture 01 - re-seat py-jinja2 on Python 3.13".to_owned(),
        "REBASED (detached) onto vaso/target -> a000001".to_owned(),
        "Executed 0 out of 28 tests: 28 tests pass.".to_owned(),
        "66 passed in 5.08s".to_owned(),
        "VERIFY_PASSED".to_owned(),
        "MOVED lead-wip -> a000001".to_owned(),
        "2026-09-28T13:07:00Z LANDED vaso/landing -> a000001 (Executed 0 out of 28 tests: 28 tests pass. 66 passed in 5.08s)".to_owned(),
    ];
    for index in 2..=5 {
        let hour = 13 + index;
        let previous = format!("a{:06x}", index - 1);
        let current = format!("a{index:06x}");
        let package = ["py-mpmath", "py-pathspec", "py-pyyaml", "py-cython"][index - 2];
        lines.push(format!(
            "2026-09-28T{hour:02}:05:00Z MOVED {previous} -> {current}: Commit fixture {index:02} - re-seat {package} on Python 3.13"
        ));
        lines.push(format!("REBASED (detached) onto vaso/target -> {current}"));
        lines.push("Executed 0 out of 28 tests: 28 tests pass.".to_owned());
        lines.push(format!("{} passed in {}.{}s", 60 + index, 4 + index, index));
        lines.push("VERIFY_PASSED".to_owned());
        lines.push(format!("MOVED lead-wip -> {current}"));
        lines.push(format!(
            "2026-09-28T{hour:02}:07:00Z LANDED vaso/landing -> {current} (Executed 0 out of 28 tests: 28 tests pass.)"
        ));
    }
    lines.extend([
        "2026-09-29T12:05:00Z MOVED a000005 -> beef001: Commit fixture 40 - re-seat pytorch provider shard with an intentionally long subject that should wrap cleanly instead of being cut off at the pane edge".to_owned(),
        "REBASED (detached) onto vaso/target -> beef001".to_owned(),
        "Executed 0 out of 28 tests: 27 tests pass, 1 fails.".to_owned(),
        "//native/pytorch:plan_test FAILED in 12.4s".to_owned(),
        "VERIFY_FAILED //native/pytorch:plan_test".to_owned(),
        "2026-09-29T12:35:00Z RED VERIFY_FAILED //native/pytorch:plan_test".to_owned(),
    ]);
    let comments = (0..10)
        .map(|index| {
            let minute = 34_u32.saturating_sub(index * 3);
            let package = [
                "py-pathspec",
                "py-mpmath",
                "py-jinja2",
                "pthreadpool",
                "glib",
                "glib-bootstrap",
                "meson",
                "py-pyyaml",
                "nvtx",
                "py-cython",
            ][index as usize];
            TrackerComment {
                timestamp: format!("2026-09-29T12:{minute:02}Z"),
                file: "08-reseat-python-bound-natives.md".to_owned(),
                author: "TRAE".to_owned(),
                text: format!(
                    "package-scoped re-seat is `{package}`.\nVerified prefix parity, kept the insula proof log, and left the target commit ready for the autoland loop.\nNext package remains queued for the same lane."
                ),
            }
        })
        .collect::<Vec<_>>();
    let follower_log_lines = (1..=30)
        .map(|index| {
            if index % 6 == 0 {
                format!("INFO: proof step {index:02} completed with cached action reuse")
            } else if index % 11 == 0 {
                format!("WARN: package fixture {index:02} emitted a long diagnostic line that should wrap inside the follower log pane without pushing neighboring panels around")
            } else {
                format!("proof fixture line {index:02}: checking installed prefix metadata")
            }
        })
        .collect::<Vec<_>>();
    let commits = (0..40)
        .map(|index| CommitInfo {
            sha: format!("{:07x}", 0xbeef000 + index),
            timestamp: 1_790_685_600 - index as u64 * 2_160,
            subject: format!(
                "Commit fixture {:02} - re-seat package shard on Python 3.13",
                index + 1
            ),
        })
        .collect::<Vec<_>>();
    let target_commits_per_hour = target_commits_per_hour(&commits, now);
    DashboardData {
        settings,
        now,
        mode: LoopMode::Running(4242),
        state: Some(AutolandState {
            pid: 4242,
            beat: 1_790_685_560,
            target: "beef001".to_owned(),
            landing: "beef001".to_owned(),
            last_move: 1_790_683_500,
            last_activity: 1_790_684_400,
            start: 1_790_683_200,
            stall_min: 45,
            batch_hours: 4,
            backlog_commits: 0,
            backlog_oldest_min: 0,
            backlog_active: false,
            uncommitted_count: 0,
            uncommitted_oldest_min: 0,
            uncommitted_newest_min: 0,
            uncommitted_active: false,
        }),
        last_activity: Some(1_790_684_400),
        pane_status: None,
        pane_busy_elapsed: None,
        loop_pid_alive: Some(true),
        state_error: None,
        follower: FollowerStatus::Alive {
            pid: 8128,
            busy: vec![("bazel".to_owned(), 1), ("python3".to_owned(), 2)],
        },
        refs: RefStatus {
            target: "beef001".to_owned(),
            landing: "cafe999".to_owned(),
            pending: "2 lead commit(s) waiting".to_owned(),
        },
        events: lines.iter().map(|line| classify_log_line(line)).collect(),
        comments,
        follower_log: Some(FollowerLogTail {
            name: "20260929-1234.log".to_owned(),
            modified: Some(age::system_time_from_epoch(1_790_685_300)),
            lines: follower_log_lines,
        }),
        rollout: Some(RolloutSummary::from_lines(
            include_str!("../tests/fixtures/trae-rollout-small.jsonl").lines(),
        )),
        rollout_error: None,
        uncommitted: None,
        commits,
        lands_per_hour: vec![0, 1, 0, 2, 1, 0, 0, 1],
        target_commits_per_hour,
        last_attention_kind: Some(LogKind::Red),
        last_attention: Some(
            "2026-09-29T12:35:00Z RED VERIFY_FAILED //native/pytorch:plan_test".to_owned(),
        ),
        errors: Vec::new(),
    }
}

pub fn sample_cockpit() -> CockpitData {
    let mut dashboard = sample_dashboard();
    let pane = tmux::parse_capture(
        "\
◆ Ready for the next relay message.
  └ ■ Fix py-protobuf roundtrip compiler issue inside insula
    ◻ Run focused host/insula verification with estate output base
    ◻ Update ticket evidence for py-protobuf
    ◻ Commit package-scoped py-protobuf slice
──────────────────────────────────────────────────────────────── Evolve Spack-Bazel graph experiment ─
❯ Explain this codebase
  GPT-5.5 · Context 55% left · ~/workspace/vaso · vaso/insula-spack-bazel-graph
",
    );
    dashboard.pane_status = Some(pane.status);
    dashboard.pane_busy_elapsed = pane.busy_elapsed.clone();
    let outbox = vec![
        outbox::parse_draft(
            "/estate/agents/relay/outbox/20260929T120000Z-py-protobuf.md".into(),
            "---\nauthor: claude\ntarget_pane: %3\nstatus: draft\ncreated: 2026-09-29T12:00:00Z\nsent: \ntitle: py-protobuf ticket split\n---\n\npy-protobuf belongs to ticket 09 at 4.21.12.\nPark the 3.13.0 attempt and continue ticket 08.\n",
        ),
        outbox::parse_draft(
            "/estate/agents/relay/outbox/20260929T121000Z-sent.md".into(),
            "---\nauthor: claude\ntarget_pane: %3\nstatus: sent\ncreated: 2026-09-29T12:10:00Z\nsent: 2026-09-29T12:11:00Z\ntitle: acknowledged\n---\n\nAlready sent.\n",
        ),
    ];
    let workers = vec![
        WorkerRun {
            agent: "traecli-tui".to_owned(),
            run_id: "20260929T120000Z".to_owned(),
            path: "/estate/agents/traecli-tui/runs/20260929T120000Z".into(),
            pid: Some(999_999),
            worktree: Some("/repo/.worktrees/traecli-tui".into()),
            branch: Some("vaso/autoland-tui".to_owned()),
            base: Some("c252c0b".to_owned()),
            started_utc: Some("2026-09-29T12:00:00Z".to_owned()),
            state: workers::WorkerState::Exited(0),
            digest: vec![
                "cmd rc=0: cargo test --offline | test result: ok".to_owned(),
                "turn done: in=1200 out=300".to_owned(),
            ],
            commits: vec!["Add cockpit data modules".to_owned()],
        },
        WorkerRun {
            agent: "native-pytorch-p2".to_owned(),
            run_id: "20260929T130000Z".to_owned(),
            path: "/estate/agents/native-pytorch-p2/runs/20260929T130000Z".into(),
            pid: Some(4242),
            worktree: Some("/repo/.worktrees/native-pytorch-p2".into()),
            branch: Some("vaso/native-pytorch-p2".to_owned()),
            base: Some("beef001".to_owned()),
            started_utc: Some("2026-09-29T13:00:00Z".to_owned()),
            state: workers::WorkerState::Running,
            digest: vec!["cmd rc=?: bazel test //native/pytorch:plan_test".to_owned()],
            commits: Vec::new(),
        },
    ];
    let decisions = vec![
        "D1 (G1): which py-torch version and reference?".to_owned(),
        "D2 (G3): NCCL policy for distributed checks.".to_owned(),
        "D3: parity gate definition.".to_owned(),
    ];
    let attention = attention_items(&dashboard, &outbox, &decisions);
    CockpitData {
        dashboard,
        pane: Some(pane),
        pane_error: None,
        workers,
        outbox,
        decisions,
        attention,
        errors: Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_busy_commands_from_process_tree() {
        let busy =
            extract_busy_commands_from_names(["systemd-inhibit", "bazel", "python3", "pytest"]);
        assert!(busy.contains(&("bazel".to_owned(), 1)));
        assert!(busy.contains(&("python3".to_owned(), 1)));
        assert!(busy.contains(&("pytest".to_owned(), 1)));
    }

    #[test]
    fn extracts_open_human_decisions_and_hides_decided_items() {
        let decisions = extract_human_decisions(
            "## Decisions for the human\n\
- D1: choose the torch reference version.\n\
  Status: open\n\
  Keep this detail for the modal.\n\
- D2: decide NCCL policy.\n\
  Status: DECIDED by the human.\n\
- D3: DECIDED inline item should be hidden.\n\
- D4: define the parity gate.\n\
## Other Section\n\
- D5: not part of this section.\n",
        );

        assert_eq!(
            decisions,
            vec![
                "D1: choose the torch reference version.\nStatus: open\nKeep this detail for the modal.",
                "D4: define the parity gate.",
            ]
        );
    }

    #[test]
    fn backlog_log_event_becomes_attention_item() {
        let mut data = sample_dashboard();
        data.last_attention_kind = Some(LogKind::Backlog);
        data.last_attention =
            Some("2026-09-29T12:00:00Z BACKLOG 4 commits, oldest 37 min".to_owned());
        data.loop_pid_alive = Some(true);
        if let Some(state) = &mut data.state {
            state.beat = crate::age::unix_time(data.now);
        }
        data.last_activity = Some(crate::age::unix_time(data.now));
        data.comments.clear();

        let items = attention_items(&data, &[], &[]);

        assert_eq!(items[0].label, "BACKLOG");
        assert_eq!(
            items[0].summary,
            "2026-09-29T12:00:00Z BACKLOG 4 commits, oldest 37 min"
        );
    }
}

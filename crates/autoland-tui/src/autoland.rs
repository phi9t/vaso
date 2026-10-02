use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::process::{ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime};

use crate::age;
use crate::process::{self, ChildEnv};
use crate::settings::Settings;

const VERIFY_DIRTIED_TREE: i32 = 5;
const VERIFY_TIMEOUT: i32 = 124;

#[derive(Clone, Debug)]
pub struct VerifyStep {
    pub cwd: PathBuf,
    pub argv: Vec<String>,
    pub env: BTreeMap<String, String>,
    pub timeout: Duration,
}

#[derive(Clone, Debug)]
pub struct AutolandConfig {
    pub interval: Duration,
    pub verify_steps: Vec<VerifyStep>,
    pub max_iterations: Option<usize>,
    pub check_io_pressure: bool,
}

impl AutolandConfig {
    pub fn from_env(settings: &Settings) -> io::Result<Self> {
        let timeout = std::env::var("AUTOLAND_VERIFY_TIMEOUT_SECS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(60 * 60);
        Ok(Self {
            interval: Duration::from_secs(settings.interval.max(1)),
            verify_steps: verify_steps_from_env(settings, Duration::from_secs(timeout))?,
            max_iterations: None,
            check_io_pressure: true,
        })
    }

    pub fn test(verify_steps: Vec<VerifyStep>) -> Self {
        Self {
            interval: Duration::from_millis(25),
            verify_steps,
            max_iterations: None,
            check_io_pressure: false,
        }
    }
}

pub struct AutolandHandle {
    pid: u32,
    stop: Arc<AtomicBool>,
    rx: Receiver<String>,
    join: Option<JoinHandle<String>>,
    exit_reason: Option<String>,
}

impl AutolandHandle {
    pub fn spawn(settings: Settings, config: AutolandConfig) -> Self {
        let pid = std::process::id();
        let stop = Arc::new(AtomicBool::new(false));
        let (tx, rx) = mpsc::channel();
        let thread_stop = Arc::clone(&stop);
        let join = thread::spawn(move || run_loop(settings, config, thread_stop, tx));
        Self {
            pid,
            stop,
            rx,
            join: Some(join),
            exit_reason: None,
        }
    }

    pub fn pid(&self) -> u32 {
        self.pid
    }

    pub fn drain_lines(&mut self) -> Vec<String> {
        let mut lines = Vec::new();
        while let Ok(line) = self.rx.try_recv() {
            lines.push(line);
        }
        lines
    }

    pub fn poll_exit(&mut self) -> Option<String> {
        if self.exit_reason.is_none()
            && self
                .join
                .as_ref()
                .map(JoinHandle::is_finished)
                .unwrap_or(false)
        {
            self.exit_reason = Some(self.finish_join());
        }
        self.exit_reason.clone()
    }

    pub fn stop_and_wait(&mut self) -> String {
        self.stop.store(true, Ordering::SeqCst);
        if self.exit_reason.is_none() {
            self.exit_reason = Some(self.finish_join());
        }
        self.exit_reason
            .clone()
            .unwrap_or_else(|| "stopped".to_owned())
    }

    fn finish_join(&mut self) -> String {
        match self.join.take() {
            Some(join) => join.join().unwrap_or_else(|_| "panic".to_owned()),
            None => self
                .exit_reason
                .clone()
                .unwrap_or_else(|| "stopped".to_owned()),
        }
    }
}

impl Drop for AutolandHandle {
    fn drop(&mut self) {
        if self.join.is_some() {
            let _ = self.stop_and_wait();
        }
    }
}

#[derive(Debug)]
struct LoopState {
    start: u64,
    last_tip: String,
    last_move: u64,
    backlog_was_active: bool,
    last_backlog_log: u64,
    uncommitted_was_active: bool,
    last_uncommitted_log: u64,
}

fn run_loop(
    settings: Settings,
    config: AutolandConfig,
    stop: Arc<AtomicBool>,
    tx: Sender<String>,
) -> String {
    match run_loop_inner(&settings, &config, &stop, &tx) {
        Ok(reason) => reason,
        Err(error) => {
            let mut logger = Logger::new(settings.log.clone(), tx);
            let _ = logger.say(&format!("IO {error}"));
            format!("error: {error}")
        }
    }
}

fn run_loop_inner(
    settings: &Settings,
    config: &AutolandConfig,
    stop: &AtomicBool,
    tx: &Sender<String>,
) -> io::Result<String> {
    fs::create_dir_all(&settings.agent_root)?;
    fs::create_dir_all(&settings.tmpdir)?;
    fs::create_dir_all(&settings.vaso_bazel_ob)?;
    let follower_pid = settings
        .follower_pid
        .ok_or_else(|| io::Error::other("no follower process found; set FOLLOWER_PID"))?;
    let mut logger = Logger::new(settings.log.clone(), tx.clone());
    let start = now_epoch();
    let last_tip = git_text(
        settings,
        &["merge-base", &settings.lead_branch, &settings.target],
    )?;
    let last_move = git_text(settings, &["log", "-1", "--format=%ct", &settings.target])?
        .parse::<u64>()
        .unwrap_or(start);
    let mut state = LoopState {
        start,
        last_tip: last_tip.clone(),
        last_move,
        backlog_was_active: false,
        last_backlog_log: 0,
        uncommitted_was_active: false,
        last_uncommitted_log: 0,
    };
    logger.say(&format!(
        "START target={} landing={}",
        short(&last_tip),
        git_text(settings, &["rev-parse", "--short", &settings.landing])?
    ))?;

    let mut iterations = 0_usize;
    loop {
        if stop.load(Ordering::SeqCst) || process::termination_requested() {
            return Ok("stopped".to_owned());
        }
        let now = now_epoch();
        if !process::process_alive(follower_pid) {
            logger.say(&format!("GONE follower pid {follower_pid} exited"))?;
            return Ok("GONE".to_owned());
        }
        if config.check_io_pressure {
            if let Some((path, usage)) = io_pressure() {
                logger.say(&format!("IO {path} at {usage}%"))?;
                return Ok("IO".to_owned());
            }
        }
        let tip = git_text(settings, &["rev-parse", &settings.target])?;
        if tip != state.last_tip {
            if !is_ancestor(settings, &state.last_tip, &tip)? {
                logger.say(&format!(
                    "REWRITTEN target {} -> {} dropped the old tip",
                    short(&state.last_tip),
                    short(&tip)
                ))?;
                return Ok("REWRITTEN".to_owned());
            }
            let subject = git_text(settings, &["log", "--format=%s", "-1", &tip])?;
            logger.say(&format!(
                "MOVED {} -> {}: {subject}",
                short(&state.last_tip),
                short(&tip)
            ))?;
            state.last_tip = tip;
            state.last_move = now;
            let rebase = rebase_and_verify(settings, &config.verify_steps, stop, &mut logger)?;
            if stop.load(Ordering::SeqCst) || process::termination_requested() {
                return Ok("stopped".to_owned());
            }
            if rebase.passed {
                git_status(
                    settings,
                    &["branch", "-f", &settings.landing, &settings.lead_branch],
                )?;
                let landing = git_text(settings, &["rev-parse", "--short", &settings.landing])?;
                logger.say(&format!(
                    "LANDED {} -> {} ({})",
                    settings.landing,
                    landing,
                    rebase.summary.join(" ")
                ))?;
            } else {
                logger.say(&format!("RED {}", red_summary(&rebase.lines)))?;
                return Ok("RED".to_owned());
            }
        }

        let backlog = backlog_snapshot(settings, now)?;
        if backlog.active
            && !state.backlog_was_active
            && now.saturating_sub(state.last_backlog_log)
                >= settings.backlog_rate_limit_min.saturating_mul(60)
        {
            logger.say(&format!(
                "BACKLOG {} commits, oldest {} min",
                backlog.commits, backlog.oldest_min
            ))?;
            state.last_backlog_log = now;
        }
        state.backlog_was_active = backlog.active;

        let uncommitted = uncommitted_snapshot(settings, now)?;
        if uncommitted.active
            && !state.uncommitted_was_active
            && now.saturating_sub(state.last_uncommitted_log)
                >= settings.uncommitted_rate_limit_min.saturating_mul(60)
        {
            logger.say(&format!(
                "UNCOMMITTED {} files, oldest {} min",
                uncommitted.count, uncommitted.oldest_min
            ))?;
            state.last_uncommitted_log = now;
        }
        state.uncommitted_was_active = uncommitted.active;

        let mut activity = latest_activity(settings).unwrap_or(0).max(state.last_move);
        if activity == 0 {
            activity = state.last_move;
        }
        if now.saturating_sub(activity) >= settings.stall_min.saturating_mul(60) {
            logger.say(&format!(
                "STALL no target move, tracker edit or follower log for {} min",
                now.saturating_sub(activity) / 60
            ))?;
            return Ok("STALL".to_owned());
        }
        if now.saturating_sub(state.start) >= settings.batch_hours.saturating_mul(3_600) {
            logger.say(&format!(
                "REVIEW {}h elapsed; target={}",
                settings.batch_hours,
                git_text(settings, &["rev-parse", "--short", &settings.target])?
            ))?;
            return Ok("REVIEW".to_owned());
        }
        write_state(settings, now, activity, &state, &backlog, &uncommitted)?;
        iterations += 1;
        if config
            .max_iterations
            .map(|limit| iterations >= limit)
            .unwrap_or(false)
        {
            return Ok("max iterations".to_owned());
        }
        sleep_interruptibly(config.interval, stop);
    }
}

#[derive(Default)]
struct RebaseResult {
    passed: bool,
    lines: Vec<String>,
    summary: Vec<String>,
}

fn rebase_and_verify(
    settings: &Settings,
    verify_steps: &[VerifyStep],
    stop: &AtomicBool,
    logger: &mut Logger,
) -> io::Result<RebaseResult> {
    if !git_text(settings, &["status", "--porcelain"])?.is_empty() {
        let line = format!(
            "REFUSED: {} has uncommitted changes; commit them before rebasing",
            settings.lead.display()
        );
        logger.raw(&line)?;
        return Ok(RebaseResult {
            passed: false,
            lines: vec![line],
            summary: Vec::new(),
        });
    }
    let branch = git_text(settings, &["rev-parse", "--abbrev-ref", "HEAD"])?;
    if branch == "HEAD" {
        let line = format!(
            "REFUSED: {} is on a detached HEAD; check out the landing branch first",
            settings.lead.display()
        );
        logger.raw(&line)?;
        return Ok(RebaseResult {
            passed: false,
            lines: vec![line],
            summary: Vec::new(),
        });
    }
    let target = git_text(settings, &["rev-parse", &settings.target])?;
    let before = git_text(settings, &["rev-parse", "HEAD"])?;
    let mut result = RebaseResult::default();
    if is_ancestor(settings, &target, "HEAD")? {
        let line = format!("UP_TO_DATE {branch} already contains {}", settings.target);
        logger.raw(&line)?;
        result.lines.push(line);
    } else {
        git_status(settings, &["checkout", "-q", "--detach"])?;
        let rebase_status = git_status_allow(settings, &["rebase", &target])?;
        if !rebase_status.success() {
            let conflicts = git_text_allow(settings, &["diff", "--name-only", "--diff-filter=U"])
                .unwrap_or_default();
            let _ = git_status_allow(settings, &["rebase", "--abort"]);
            let _ = git_status_allow(settings, &["checkout", "-q", &branch]);
            let line = format!("REBASE_CONFLICT on a detached HEAD; {branch} is unchanged:");
            logger.raw(&line)?;
            result.lines.push(line);
            for path in conflicts.lines() {
                let line = format!("  {path}");
                logger.raw(&line)?;
                result.lines.push(line);
            }
            return Ok(result);
        }
        let line = format!(
            "REBASED (detached) onto {} -> {}",
            settings.target,
            git_text(settings, &["rev-parse", "--short", "HEAD"])?
        );
        logger.raw(&line)?;
        result.lines.push(line);
    }

    let verdict = run_verify(settings, verify_steps, stop, logger, &mut result.summary)?;
    if verdict != 0 {
        let candidate = git_text(settings, &["rev-parse", "--short", "HEAD"])?;
        let _ = git_status_allow(settings, &["checkout", "-q", &branch]);
        let line = format!(
            "VERIFY_FAILED rc={verdict} on {candidate}; landing branch unchanged at {} (candidate kept as {candidate})",
            git_text(settings, &["rev-parse", "--short", &branch]).unwrap_or_else(|_| "?".to_owned())
        );
        logger.raw(&line)?;
        result.lines.push(line);
        return Ok(result);
    }
    logger.raw("VERIFY_PASSED")?;
    result.lines.push("VERIFY_PASSED".to_owned());
    if git_text(settings, &["rev-parse", "HEAD"])? != before
        || git_text(settings, &["rev-parse", "--abbrev-ref", "HEAD"])? == "HEAD"
    {
        git_status(settings, &["checkout", "-q", "-B", &branch])?;
        let line = format!(
            "MOVED {branch} -> {}",
            git_text(settings, &["rev-parse", "--short", &branch])?
        );
        logger.raw(&line)?;
        result.lines.push(line);
    }
    result.passed = true;
    Ok(result)
}

fn run_verify(
    settings: &Settings,
    steps: &[VerifyStep],
    stop: &AtomicBool,
    logger: &mut Logger,
    summary: &mut Vec<String>,
) -> io::Result<i32> {
    let before = dirty_entries(&settings.lead)?;
    for (index, step) in steps.iter().enumerate() {
        let label = step_label(index, step);
        let output_path = settings.tmpdir.join(format!("verify-{index}-{label}.log"));
        let rc = run_verify_step(settings, step, stop, &output_path)?;
        let tail = tail_lines(&output_path, 12).unwrap_or_default();
        for line in &tail {
            logger.raw(line)?;
            if line.contains("Executed") || line.contains("passed") {
                summary.push(line.clone());
            }
        }
        if rc != 0 {
            logger.raw(&format!("VERIFY_FAILED rc={rc} step={label}"))?;
            return Ok(rc);
        }
    }
    let after = dirty_entries(&settings.lead)?;
    if after != before {
        logger.raw(&format!(
            "VERIFY_DIRTIED_TREE: the verify wrote into {}; build outputs belong under the I/O root:",
            settings.lead.display()
        ))?;
        for (status, path) in after
            .iter()
            .filter(|entry| !before.contains(entry))
            .take(20)
        {
            logger.raw(&format!("  {status} {path}"))?;
        }
        return Ok(VERIFY_DIRTIED_TREE);
    }
    Ok(0)
}

fn run_verify_step(
    settings: &Settings,
    step: &VerifyStep,
    stop: &AtomicBool,
    output_path: &Path,
) -> io::Result<i32> {
    if step.argv.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "verify step has no argv",
        ));
    }
    if let Some(parent) = output_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let out = File::create(output_path)?;
    let err = out.try_clone()?;
    let mut child_env = settings.child_env();
    child_env.set("VASO_ESTATE_ROOT", settings.vaso_estate_root.as_os_str());
    child_env.set("VASO_AGENT_IO_ROOT", settings.agent_root.as_os_str());
    child_env.set("TMPDIR", settings.tmpdir.as_os_str());
    child_env.set("VASO_BAZEL_OB", settings.vaso_bazel_ob.as_os_str());
    for (key, value) in &step.env {
        child_env.set(key, value);
    }
    let mut command = child_env.command(&step.argv[0]);
    command
        .args(&step.argv[1..])
        .current_dir(&step.cwd)
        .stdout(Stdio::from(out))
        .stderr(Stdio::from(err));
    let mut child = process::SupervisedChild::spawn(command)?;
    let deadline = Instant::now() + step.timeout;
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok(status_code(status));
        }
        if stop.load(Ordering::SeqCst) || process::termination_requested() {
            let _ = child.stop_with_grace(Duration::from_millis(500));
            return Ok(130);
        }
        if Instant::now() >= deadline {
            let _ = child.stop_with_grace(Duration::from_millis(500));
            return Ok(VERIFY_TIMEOUT);
        }
        thread::sleep(Duration::from_millis(20));
    }
}

fn status_code(status: ExitStatus) -> i32 {
    status.code().unwrap_or(128)
}

fn verify_steps_from_env(settings: &Settings, timeout: Duration) -> io::Result<Vec<VerifyStep>> {
    if let Ok(path) = std::env::var("AUTOLAND_VERIFY_STEPS_FILE") {
        let text = fs::read_to_string(path)?;
        return parse_verify_steps(settings, &text, timeout);
    }
    if let Ok(text) = std::env::var("AUTOLAND_VERIFY_STEPS") {
        if !text.trim().is_empty() {
            return parse_verify_steps(settings, &text, timeout);
        }
    }
    Ok(default_verify_steps(settings, timeout))
}

pub fn parse_verify_steps(
    settings: &Settings,
    text: &str,
    timeout: Duration,
) -> io::Result<Vec<VerifyStep>> {
    let mut steps = Vec::new();
    for (line_number, line) in text.lines().enumerate() {
        let line = line.trim_end();
        if line.trim().is_empty() || line.trim_start().starts_with('#') {
            continue;
        }
        let parts = line.split('\t').collect::<Vec<_>>();
        if parts.len() < 2 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                format!("verify step line {} needs cwd<TAB>argv...", line_number + 1),
            ));
        }
        let cwd = resolve_cwd(&settings.lead, parts[0]);
        steps.push(VerifyStep {
            cwd,
            argv: parts[1..].iter().map(|part| (*part).to_owned()).collect(),
            env: BTreeMap::new(),
            timeout,
        });
    }
    if steps.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "no verify steps configured",
        ));
    }
    Ok(steps)
}

fn default_verify_steps(settings: &Settings, timeout: Duration) -> Vec<VerifyStep> {
    let bazel = std::env::var("AUTOLAND_BAZEL")
        .or_else(|_| std::env::var("BAZEL"))
        .unwrap_or_else(|_| {
            let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_owned());
            format!("{home}/.vaso-estate/opt-vaso/bin/bazel-real")
        });
    let mut pytest_env = BTreeMap::new();
    pytest_env.insert("PYTHONDONTWRITEBYTECODE".to_owned(), "1".to_owned());
    let mut cargo_env = BTreeMap::new();
    cargo_env.insert(
        "CARGO_TARGET_DIR".to_owned(),
        settings
            .agent_root
            .join("cargo-target")
            .display()
            .to_string(),
    );
    vec![
        VerifyStep {
            cwd: settings.lead.join("experiments/spack-bazel-graph"),
            argv: vec![
                bazel,
                format!("--output_base={}", settings.vaso_bazel_ob.display()),
                "test".to_owned(),
                "//tools:all".to_owned(),
                "//native/pytorch:plan_test".to_owned(),
                "//native/llvm:plan_test".to_owned(),
                "//native/py_llvmlite:plan_test".to_owned(),
                "--".to_owned(),
                "-//tools:pytorch_recipe_provenance_test".to_owned(),
            ],
            env: BTreeMap::new(),
            timeout,
        },
        VerifyStep {
            cwd: settings.lead.clone(),
            argv: vec![
                "/usr/bin/python3".to_owned(),
                "-m".to_owned(),
                "pytest".to_owned(),
                "-q".to_owned(),
                "-p".to_owned(),
                "no:cacheprovider".to_owned(),
                "tests".to_owned(),
            ],
            env: pytest_env,
            timeout,
        },
        VerifyStep {
            cwd: settings.lead.clone(),
            argv: vec![
                "cargo".to_owned(),
                "test".to_owned(),
                "--offline".to_owned(),
                "--manifest-path".to_owned(),
                "crates/autoland-tui/Cargo.toml".to_owned(),
            ],
            env: cargo_env,
            timeout,
        },
    ]
}

fn resolve_cwd(lead: &Path, cwd: &str) -> PathBuf {
    let path = PathBuf::from(cwd);
    if path.is_absolute() {
        path
    } else {
        lead.join(path)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct BacklogSnapshot {
    commits: u64,
    oldest_min: u64,
    active: bool,
}

fn backlog_snapshot(settings: &Settings, now: u64) -> io::Result<BacklogSnapshot> {
    let range = format!("{}..{}", settings.target, settings.landing);
    let commits = git_text_allow(settings, &["rev-list", "--count", &range])
        .unwrap_or_else(|_| "0".to_owned())
        .parse::<u64>()
        .unwrap_or(0);
    let oldest_ts = git_text_allow(settings, &["log", "--format=%ct", "--reverse", &range])
        .unwrap_or_default()
        .lines()
        .next()
        .and_then(|value| value.parse::<u64>().ok());
    let oldest_min = oldest_ts.map(|ts| now.saturating_sub(ts) / 60).unwrap_or(0);
    let active = commits >= settings.backlog_commits || oldest_min >= settings.backlog_minutes;
    Ok(BacklogSnapshot {
        commits,
        oldest_min,
        active,
    })
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct UncommittedSnapshot {
    count: u64,
    oldest_min: u64,
    newest_min: u64,
    active: bool,
}

fn uncommitted_snapshot(settings: &Settings, now: u64) -> io::Result<UncommittedSnapshot> {
    let output = git_output(
        settings,
        &[
            "-C",
            settings.follower.to_str().unwrap_or("."),
            "status",
            "--porcelain",
            "-z",
            "--untracked-files=no",
        ],
    )?;
    if !output.status.success() {
        return Ok(UncommittedSnapshot {
            count: 0,
            oldest_min: 0,
            newest_min: 0,
            active: false,
        });
    }
    let mut mtimes = Vec::new();
    for path in tracked_dirty_paths(&output.stdout) {
        let full = settings.follower.join(path);
        let Ok(metadata) = fs::metadata(full) else {
            continue;
        };
        if metadata.is_file() {
            if let Ok(modified) = metadata.modified() {
                mtimes.push(age::unix_time(modified));
            }
        }
    }
    if mtimes.is_empty() {
        return Ok(UncommittedSnapshot {
            count: 0,
            oldest_min: 0,
            newest_min: 0,
            active: false,
        });
    }
    let newest = mtimes.iter().copied().max().unwrap_or(0);
    let target_ts = git_text_allow(settings, &["log", "-1", "--format=%ct", &settings.target])
        .ok()
        .and_then(|text| text.parse::<u64>().ok())
        .unwrap_or(0);
    if target_ts >= newest {
        return Ok(UncommittedSnapshot {
            count: 0,
            oldest_min: 0,
            newest_min: 0,
            active: false,
        });
    }
    let oldest = mtimes.iter().copied().min().unwrap_or(newest);
    let newest_min = now.saturating_sub(newest) / 60;
    let oldest_min = now.saturating_sub(oldest) / 60;
    Ok(UncommittedSnapshot {
        count: mtimes.len() as u64,
        oldest_min,
        newest_min,
        active: newest_min >= settings.uncommitted_min,
    })
}

fn latest_activity(settings: &Settings) -> Option<u64> {
    let mut newest = None;
    for path in [
        settings.follower.join(&settings.tracker),
        settings.follower_logs.clone(),
    ] {
        if let Some(timestamp) = latest_file_mtime(&path) {
            newest = Some(
                newest
                    .map(|current: u64| current.max(timestamp))
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
    for entry in fs::read_dir(path).ok()?.flatten() {
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

fn write_state(
    settings: &Settings,
    now: u64,
    activity: u64,
    state: &LoopState,
    backlog: &BacklogSnapshot,
    uncommitted: &UncommittedSnapshot,
) -> io::Result<()> {
    let tmp = settings.state.with_extension("state.tmp");
    let text = format!(
        "pid={} beat={} target={} landing={} last_move={} last_activity={} start={} stall_min={} batch_hours={} backlog_commits={} backlog_oldest_min={} backlog_active={} uncommitted_count={} uncommitted_oldest_min={} uncommitted_newest_min={} uncommitted_active={}\n",
        std::process::id(),
        now,
        git_text(settings, &["rev-parse", "--short", &settings.target])?,
        git_text(settings, &["rev-parse", "--short", &settings.landing])?,
        state.last_move,
        activity,
        state.start,
        settings.stall_min,
        settings.batch_hours,
        backlog.commits,
        backlog.oldest_min,
        u8::from(backlog.active),
        uncommitted.count,
        uncommitted.oldest_min,
        uncommitted.newest_min,
        u8::from(uncommitted.active)
    );
    fs::write(&tmp, text)?;
    fs::rename(tmp, &settings.state)
}

fn io_pressure() -> Option<(&'static str, u64)> {
    for path in ["/tmp", "/var/tmp"] {
        if let Some(usage) = fs_usage_percent(path) {
            if usage >= 80 {
                return Some((path, usage));
            }
        }
    }
    None
}

fn fs_usage_percent(path: &str) -> Option<u64> {
    let c_path = std::ffi::CString::new(path).ok()?;
    let mut stat = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    let rc = unsafe { libc::statvfs(c_path.as_ptr(), stat.as_mut_ptr()) };
    if rc != 0 {
        return None;
    }
    let stat = unsafe { stat.assume_init() };
    let total = stat.f_blocks;
    let available = stat.f_bavail;
    if total == 0 {
        return None;
    }
    Some(total.saturating_sub(available) * 100 / total)
}

fn dirty_entries(repo: &Path) -> io::Result<Vec<(String, String)>> {
    let output = ChildEnv::from_current().output(
        "git",
        &[
            "-C",
            repo.to_str().unwrap_or("."),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
        None,
    )?;
    if !output.status.success() {
        return Err(io::Error::other(
            String::from_utf8_lossy(&output.stderr).trim().to_owned(),
        ));
    }
    let mut entries = Vec::new();
    let records = output
        .stdout
        .split(|byte| *byte == 0)
        .filter(|record| !record.is_empty())
        .collect::<Vec<_>>();
    let mut index = 0;
    while index < records.len() {
        let record = records[index];
        if record.len() >= 4 {
            let status = String::from_utf8_lossy(&record[..2]).to_string();
            let path = String::from_utf8_lossy(&record[3..]).to_string();
            entries.push((status.clone(), path));
            index += if status.starts_with('R') || status.starts_with('C') {
                2
            } else {
                1
            };
        } else {
            index += 1;
        }
    }
    entries.sort();
    Ok(entries)
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

fn git_text(settings: &Settings, args: &[&str]) -> io::Result<String> {
    let output = git_output_in_lead(settings, args)?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
    } else {
        Err(io::Error::other(
            String::from_utf8_lossy(&output.stderr).trim().to_owned(),
        ))
    }
}

fn git_text_allow(settings: &Settings, args: &[&str]) -> io::Result<String> {
    let output = git_output_in_lead(settings, args)?;
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

fn git_status(settings: &Settings, args: &[&str]) -> io::Result<()> {
    let output = git_output_in_lead(settings, args)?;
    if output.status.success() {
        Ok(())
    } else {
        Err(io::Error::other(
            String::from_utf8_lossy(&output.stderr).trim().to_owned(),
        ))
    }
}

fn git_status_allow(settings: &Settings, args: &[&str]) -> io::Result<ExitStatus> {
    Ok(git_output_in_lead(settings, args)?.status)
}

fn git_output_in_lead(settings: &Settings, args: &[&str]) -> io::Result<std::process::Output> {
    let mut full_args = vec!["-C", settings.lead.to_str().unwrap_or(".")];
    full_args.extend_from_slice(args);
    git_output(settings, &full_args)
}

fn git_output(settings: &Settings, args: &[&str]) -> io::Result<std::process::Output> {
    settings.child_env().output("git", args, None)
}

fn is_ancestor(settings: &Settings, ancestor: &str, descendant: &str) -> io::Result<bool> {
    Ok(git_status_allow(
        settings,
        &["merge-base", "--is-ancestor", ancestor, descendant],
    )?
    .success())
}

fn tail_lines(path: &Path, limit: usize) -> io::Result<Vec<String>> {
    let text = fs::read_to_string(path)?;
    let mut lines = text.lines().map(str::to_owned).collect::<Vec<_>>();
    if lines.len() > limit {
        lines = lines.split_off(lines.len() - limit);
    }
    Ok(lines)
}

fn step_label(index: usize, step: &VerifyStep) -> String {
    let program = step
        .argv
        .first()
        .and_then(|arg| Path::new(arg).file_name())
        .and_then(|name| name.to_str())
        .unwrap_or("step");
    format!("{index}-{program}")
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
                ch
            } else {
                '-'
            }
        })
        .collect()
}

fn red_summary(lines: &[String]) -> String {
    let summary = lines
        .iter()
        .filter(|line| {
            line.contains("VERIFY_FAILED")
                || line.contains("REBASE_CONFLICT")
                || line.contains("VERIFY_DIRTIED_TREE")
                || line.contains("FAILED")
                || line.contains("Error")
                || line.contains("REFUSED")
        })
        .take(3)
        .cloned()
        .collect::<Vec<_>>()
        .join(" ");
    if summary.is_empty() {
        "attention required".to_owned()
    } else {
        summary
    }
}

fn short(sha: &str) -> String {
    sha.chars().take(7).collect()
}

fn now_epoch() -> u64 {
    age::unix_time(SystemTime::now())
}

fn sleep_interruptibly(duration: Duration, stop: &AtomicBool) {
    let deadline = Instant::now() + duration;
    while Instant::now() < deadline {
        if stop.load(Ordering::SeqCst) || process::termination_requested() {
            return;
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        thread::sleep(remaining.min(Duration::from_millis(50)));
    }
}

struct Logger {
    path: PathBuf,
    tx: Sender<String>,
}

impl Logger {
    fn new(path: PathBuf, tx: Sender<String>) -> Self {
        Self { path, tx }
    }

    fn say(&mut self, message: &str) -> io::Result<()> {
        self.raw(&format!(
            "{} {message}",
            age::utc_timestamp(SystemTime::now())
        ))
    }

    fn raw(&mut self, line: &str) -> io::Result<()> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?
            .write_all(format!("{line}\n").as_bytes())?;
        let _ = self.tx.send(line.to_owned());
        Ok(())
    }
}

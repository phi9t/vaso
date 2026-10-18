use std::collections::BTreeMap;
use std::env;
use std::ffi::OsString;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use crate::process::{self, ChildEnv};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Settings {
    pub vaso_estate_root: PathBuf,
    pub lead: PathBuf,
    pub follower: PathBuf,
    pub target: String,
    pub landing: String,
    pub lead_branch: String,
    pub tracker: PathBuf,
    pub decision_specs: Vec<PathBuf>,
    pub agent: String,
    pub follower_agent: String,
    pub agent_root: PathBuf,
    pub follower_logs: PathBuf,
    pub tmpdir: PathBuf,
    pub vaso_bazel_ob: PathBuf,
    pub stall_min: u64,
    pub batch_hours: u64,
    pub interval: u64,
    pub backlog_commits: u64,
    pub backlog_minutes: u64,
    pub backlog_rate_limit_min: u64,
    pub uncommitted_min: u64,
    pub uncommitted_rate_limit_min: u64,
    pub log: PathBuf,
    pub state: PathBuf,
    pub follower_pid: Option<u32>,
    pub follower_pane: Option<String>,
    pub follower_rollout: Option<PathBuf>,
}

impl Settings {
    pub fn resolve() -> crate::Result<Self> {
        let vars = env::vars().collect::<BTreeMap<_, _>>();
        let settings = resolve_with_env(
            &env::current_dir()?,
            &vars,
            Path::new(BUILD_REPO),
            BUILD_ESTATE_ROOT,
        )?;
        env::set_current_dir(&settings.lead)?;
        Ok(settings)
    }
}

pub fn resolve_with_env(
    cwd: &Path,
    vars: &BTreeMap<String, String>,
    build_repo: &Path,
    build_estate_root: Option<&str>,
) -> crate::Result<Settings> {
    let lead = locate_repo(
        cwd,
        vars.get("AUTOLAND_REPO").map(PathBuf::from),
        build_repo,
    )
    .ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::NotFound,
            format!("repo root not found from cwd, $AUTOLAND_REPO, or build repo {BUILD_REPO}"),
        )
    })?
    .canonicalize()?;
    let estate_root = vars
        .get("VASO_ESTATE_ROOT")
        .cloned()
        .or_else(|| build_estate_root.map(str::to_owned))
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "set VASO_ESTATE_ROOT to the disk-backed estate root",
            )
        })?;
    let estate_root_raw = PathBuf::from(estate_root);
    let estate_root = estate_root_raw.canonicalize().or_else(|_| {
        fs::create_dir_all(&estate_root_raw)?;
        estate_root_raw.canonicalize()
    })?;
    let agent = env_or(vars, "AGENT", "claude");
    let (_, pinned_env) = process::agent_io_env(&estate_root, &agent)?;
    let agent_root = estate_root.join("agents").join(&agent).canonicalize()?;
    let tmpdir = agent_root.join("tmp").canonicalize()?;
    let default_bazel_ob = agent_root.join("bazel-ob");
    let vaso_bazel_ob = vars
        .get("VASO_BAZEL_OB")
        .map(PathBuf::from)
        .unwrap_or(default_bazel_ob);
    fs::create_dir_all(&vaso_bazel_ob)?;
    let vaso_bazel_ob = vaso_bazel_ob.canonicalize()?;

    let follower = vars
        .get("FOLLOWER")
        .map(PathBuf::from)
        .map(|path| path.canonicalize().unwrap_or(path))
        .unwrap_or_else(|| follower_from_git_layout(&lead).unwrap_or_else(|_| lead.clone()));

    let target = env_or(vars, "TARGET", "vaso/insula-spack-bazel-graph");
    let landing = env_or(vars, "LANDING", "vaso/frontier-convergence");
    let lead_branch = match vars.get("LEAD_BRANCH") {
        Some(value) => value.clone(),
        None => git_text_with_env(&lead, &["rev-parse", "--abbrev-ref", "HEAD"], &pinned_env)?,
    };
    let tracker = PathBuf::from(env_or(
        vars,
        "TRACKER",
        ".scratch/pytorch-frontier-convergence",
    ));
    let decision_specs = parse_path_list(
        &env_or(
            vars,
            "DECISION_SPECS",
            &format!(
                "{}/spec.md:.scratch/native-pytorch-build/spec.md",
                tracker.display()
            ),
        ),
        &tracker,
    );
    let follower_agent = env_or(vars, "FOLLOWER_AGENT", "trae");
    let follower_logs = estate_root
        .join("agents")
        .join(&follower_agent)
        .join("logs");
    fs::create_dir_all(&follower_logs)?;

    let follower_pid = match vars.get("FOLLOWER_PID") {
        Some(value) => parse_pid(value)?,
        None => discover_follower_pid(&follower),
    };
    let follower_pane = match vars.get("FOLLOWER_PANE") {
        Some(value) => parse_optional(value),
        None => follower_pid.and_then(|pid| discover_follower_pane(pid, vars, &pinned_env)),
    };
    let follower_rollout = match vars.get("FOLLOWER_ROLLOUT") {
        Some(value) => parse_optional_path(value),
        None => follower_pid.and_then(discover_follower_rollout),
    };

    Ok(Settings {
        vaso_estate_root: estate_root.clone(),
        lead,
        follower,
        target,
        landing,
        lead_branch,
        tracker,
        decision_specs,
        agent,
        follower_agent,
        agent_root: agent_root.clone(),
        follower_logs,
        tmpdir,
        vaso_bazel_ob,
        stall_min: parse_u64(&env_or(vars, "STALL_MIN", "45"), "STALL_MIN")?,
        batch_hours: parse_u64(&env_or(vars, "BATCH_HOURS", "4"), "BATCH_HOURS")?,
        interval: parse_u64(&env_or(vars, "INTERVAL", "60"), "INTERVAL")?,
        backlog_commits: parse_u64(&env_or(vars, "BACKLOG_COMMITS", "3"), "BACKLOG_COMMITS")?,
        backlog_minutes: parse_u64(&env_or(vars, "BACKLOG_MINUTES", "30"), "BACKLOG_MINUTES")?,
        backlog_rate_limit_min: parse_u64(
            &env_or(vars, "BACKLOG_RATE_LIMIT_MIN", "30"),
            "BACKLOG_RATE_LIMIT_MIN",
        )?,
        uncommitted_min: parse_u64(&env_or(vars, "UNCOMMITTED_MIN", "60"), "UNCOMMITTED_MIN")?,
        uncommitted_rate_limit_min: parse_u64(
            &env_or(vars, "UNCOMMITTED_RATE_LIMIT_MIN", "30"),
            "UNCOMMITTED_RATE_LIMIT_MIN",
        )?,
        log: agent_root.join("autoland.log"),
        state: agent_root.join("autoland.state"),
        follower_pid,
        follower_pane,
        follower_rollout,
    })
}

fn env_or(vars: &BTreeMap<String, String>, key: &str, default: &str) -> String {
    vars.get(key)
        .filter(|value| !value.is_empty())
        .cloned()
        .unwrap_or_else(|| default.to_owned())
}

/// The repo this binary was built from (crates/autoland-tui/../..), the last
/// fallback for a binary launched outside any checkout.
const BUILD_REPO: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../..");
/// The estate root at build time, used when VASO_ESTATE_ROOT is unset.
const BUILD_ESTATE_ROOT: Option<&str> = option_env!("VASO_ESTATE_ROOT");

/// Find the repo root by walking up from `cwd`, then trying `$AUTOLAND_REPO`,
/// then the build repo.
pub fn locate_repo(
    cwd: &Path,
    override_repo: Option<PathBuf>,
    build_repo: &Path,
) -> Option<PathBuf> {
    find_repo_root(cwd)
        .or_else(|| override_repo.and_then(|repo| find_repo_root(&repo)))
        .or_else(|| find_repo_root(build_repo))
}

fn find_repo_root(start: &Path) -> Option<PathBuf> {
    for dir in start.ancestors() {
        if is_git_layout_marker(&dir.join(".git")) {
            return Some(dir.canonicalize().unwrap_or_else(|_| dir.to_path_buf()));
        }
    }
    None
}

fn is_git_layout_marker(git: &Path) -> bool {
    if git.is_dir() {
        return git.join("HEAD").is_file()
            || git.join("commondir").is_file()
            || git.join("objects").is_dir();
    }
    let Ok(text) = fs::read_to_string(git) else {
        return false;
    };
    let Some(path) = text.trim().strip_prefix("gitdir:") else {
        return false;
    };
    let path = PathBuf::from(path.trim());
    let git_dir = if path.is_absolute() {
        path
    } else {
        git.parent().unwrap_or_else(|| Path::new(".")).join(path)
    };
    git_dir.is_dir()
}

pub fn follower_from_git_layout(lead: &Path) -> io::Result<PathBuf> {
    let git = lead.join(".git");
    let git_dir = if git.is_dir() {
        git
    } else {
        let text = fs::read_to_string(&git)?;
        let path = text
            .trim()
            .strip_prefix("gitdir:")
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, ".git file missing gitdir"))?
            .trim();
        let path = PathBuf::from(path);
        if path.is_absolute() {
            path
        } else {
            lead.join(path)
        }
    }
    .canonicalize()?;
    let common = match fs::read_to_string(git_dir.join("commondir")) {
        Ok(text) => {
            let path = PathBuf::from(text.trim());
            if path.is_absolute() {
                path
            } else {
                git_dir.join(path)
            }
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => git_dir,
        Err(error) => return Err(error),
    }
    .canonicalize()?;
    common
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "git common dir has no parent"))
}

pub fn parse_settings(text: &str) -> Result<Settings, String> {
    let mut values = BTreeMap::<String, String>::new();
    for line in text.lines() {
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        values.insert(key.to_owned(), value.to_owned());
    }
    let get = |key: &str| {
        values
            .get(key)
            .cloned()
            .ok_or_else(|| format!("missing {key} from autoland-env.sh output"))
    };
    let tracker = get_path(&get("TRACKER")?);
    let decision_specs = values.get("DECISION_SPECS").cloned().unwrap_or_else(|| {
        format!(
            "{}/spec.md:.scratch/native-pytorch-build/spec.md",
            tracker.display()
        )
    });
    let decision_specs = parse_path_list(&decision_specs, &tracker);
    Ok(Settings {
        vaso_estate_root: get_path(&get("VASO_ESTATE_ROOT")?),
        lead: get_path(&get("LEAD")?),
        follower: get_path(&get("FOLLOWER")?),
        target: get("TARGET")?,
        landing: get("LANDING")?,
        lead_branch: get("LEAD_BRANCH")?,
        tracker,
        decision_specs,
        agent: get("AGENT")?,
        follower_agent: get("FOLLOWER_AGENT")?,
        agent_root: get_path(&get("AGENT_ROOT")?),
        follower_logs: get_path(&get("FOLLOWER_LOGS")?),
        tmpdir: get_path(values.get("TMPDIR").map(String::as_str).unwrap_or("")),
        vaso_bazel_ob: get_path(
            values
                .get("VASO_BAZEL_OB")
                .map(String::as_str)
                .unwrap_or(""),
        ),
        stall_min: parse_u64(&get("STALL_MIN")?, "STALL_MIN")?,
        batch_hours: parse_u64(&get("BATCH_HOURS")?, "BATCH_HOURS")?,
        interval: parse_u64(&get("INTERVAL")?, "INTERVAL")?,
        backlog_commits: parse_u64(&get("BACKLOG_COMMITS")?, "BACKLOG_COMMITS")?,
        backlog_minutes: parse_u64(&get("BACKLOG_MINUTES")?, "BACKLOG_MINUTES")?,
        backlog_rate_limit_min: parse_u64(
            &get("BACKLOG_RATE_LIMIT_MIN")?,
            "BACKLOG_RATE_LIMIT_MIN",
        )?,
        uncommitted_min: parse_u64(&get("UNCOMMITTED_MIN")?, "UNCOMMITTED_MIN")?,
        uncommitted_rate_limit_min: parse_u64(
            &get("UNCOMMITTED_RATE_LIMIT_MIN")?,
            "UNCOMMITTED_RATE_LIMIT_MIN",
        )?,
        log: get_path(&get("LOG")?),
        state: get_path(&get("STATE")?),
        follower_pid: parse_pid(&get("FOLLOWER_PID")?)?,
        follower_pane: parse_optional(&get("FOLLOWER_PANE")?),
        follower_rollout: parse_optional_path(&get("FOLLOWER_ROLLOUT")?),
    })
}

impl Settings {
    pub fn to_env_map(&self) -> BTreeMap<String, String> {
        let mut vars = BTreeMap::new();
        vars.insert(
            "VASO_ESTATE_ROOT".to_owned(),
            self.vaso_estate_root.display().to_string(),
        );
        vars.insert("LEAD".to_owned(), self.lead.display().to_string());
        vars.insert("FOLLOWER".to_owned(), self.follower.display().to_string());
        vars.insert("TARGET".to_owned(), self.target.clone());
        vars.insert("LANDING".to_owned(), self.landing.clone());
        vars.insert("LEAD_BRANCH".to_owned(), self.lead_branch.clone());
        vars.insert("TRACKER".to_owned(), self.tracker.display().to_string());
        vars.insert(
            "DECISION_SPECS".to_owned(),
            self.decision_specs
                .iter()
                .map(|path| path.display().to_string())
                .collect::<Vec<_>>()
                .join(":"),
        );
        vars.insert("AGENT".to_owned(), self.agent.clone());
        vars.insert("FOLLOWER_AGENT".to_owned(), self.follower_agent.clone());
        vars.insert(
            "AGENT_ROOT".to_owned(),
            self.agent_root.display().to_string(),
        );
        vars.insert(
            "FOLLOWER_LOGS".to_owned(),
            self.follower_logs.display().to_string(),
        );
        vars.insert("STALL_MIN".to_owned(), self.stall_min.to_string());
        vars.insert("BATCH_HOURS".to_owned(), self.batch_hours.to_string());
        vars.insert("INTERVAL".to_owned(), self.interval.to_string());
        vars.insert(
            "BACKLOG_COMMITS".to_owned(),
            self.backlog_commits.to_string(),
        );
        vars.insert(
            "BACKLOG_MINUTES".to_owned(),
            self.backlog_minutes.to_string(),
        );
        vars.insert(
            "BACKLOG_RATE_LIMIT_MIN".to_owned(),
            self.backlog_rate_limit_min.to_string(),
        );
        vars.insert(
            "UNCOMMITTED_MIN".to_owned(),
            self.uncommitted_min.to_string(),
        );
        vars.insert(
            "UNCOMMITTED_RATE_LIMIT_MIN".to_owned(),
            self.uncommitted_rate_limit_min.to_string(),
        );
        vars.insert("LOG".to_owned(), self.log.display().to_string());
        vars.insert("STATE".to_owned(), self.state.display().to_string());
        vars.insert(
            "FOLLOWER_PID".to_owned(),
            self.follower_pid
                .map(|pid| pid.to_string())
                .unwrap_or_default(),
        );
        vars.insert(
            "FOLLOWER_PANE".to_owned(),
            self.follower_pane.clone().unwrap_or_default(),
        );
        vars.insert(
            "FOLLOWER_ROLLOUT".to_owned(),
            self.follower_rollout
                .as_ref()
                .map(|path| path.display().to_string())
                .unwrap_or_default(),
        );
        vars.insert("TMPDIR".to_owned(), self.tmpdir.display().to_string());
        vars.insert(
            "VASO_BAZEL_OB".to_owned(),
            self.vaso_bazel_ob.display().to_string(),
        );
        vars
    }

    pub fn child_env(&self) -> ChildEnv {
        ChildEnv::from_current()
            .with_var("VASO_ESTATE_ROOT", self.vaso_estate_root.as_os_str())
            .with_var("VASO_AGENT_IO_ROOT", self.agent_root.as_os_str())
            .with_var("TMPDIR", self.tmpdir.as_os_str())
            .with_var("VASO_BAZEL_OB", self.vaso_bazel_ob.as_os_str())
    }
}

fn git_text_with_env(repo: &Path, args: &[&str], env: &ChildEnv) -> io::Result<String> {
    let mut full_args = vec!["-C", repo.to_str().unwrap_or(".")];
    full_args.extend_from_slice(args);
    let output = env.output("git", &full_args, None)?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
    } else {
        Err(io::Error::other(
            String::from_utf8_lossy(&output.stderr).trim().to_owned(),
        ))
    }
}

fn discover_follower_pid(follower: &Path) -> Option<u32> {
    let follower = follower.canonicalize().ok()?;
    for entry in fs::read_dir("/proc").ok()?.flatten() {
        let Ok(pid) = entry.file_name().to_string_lossy().parse::<u32>() else {
            continue;
        };
        let stat = process::proc_stat(pid).ok()?;
        if stat.comm != "traecli" {
            continue;
        }
        let cwd = fs::read_link(entry.path().join("cwd")).ok()?;
        if cwd == follower {
            return Some(pid);
        }
    }
    None
}

fn discover_follower_pane(
    follower_pid: u32,
    vars: &BTreeMap<String, String>,
    env: &ChildEnv,
) -> Option<String> {
    let mut args = Vec::<OsString>::new();
    if let Some(socket) = vars
        .get("VASO_TMUX_SOCKET")
        .filter(|value| !value.is_empty())
    {
        args.push("-S".into());
        args.push(socket.into());
    }
    args.extend([
        "list-panes".into(),
        "-a".into(),
        "-F".into(),
        "#{pane_id} #{pane_pid}".into(),
    ]);
    let output = env.output("tmux", &args, None).ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    for line in text.lines() {
        let mut parts = line.split_whitespace();
        let Some(pane) = parts.next() else {
            continue;
        };
        let Some(pane_pid) = parts.next().and_then(|value| value.parse::<u32>().ok()) else {
            continue;
        };
        if process::pid_is_or_ancestor(pane_pid, follower_pid) {
            return Some(pane.to_owned());
        }
    }
    None
}

fn discover_follower_rollout(pid: u32) -> Option<PathBuf> {
    for entry in fs::read_dir(format!("/proc/{pid}/fd")).ok()?.flatten() {
        let target = fs::read_link(entry.path()).ok()?;
        let name = target.file_name()?.to_str()?;
        if name.starts_with("rollout-") && name.ends_with(".jsonl") {
            return Some(target);
        }
    }
    None
}

fn get_path(value: &str) -> PathBuf {
    PathBuf::from(OsString::from(value))
}

fn parse_u64(value: &str, name: &str) -> Result<u64, String> {
    value
        .parse()
        .map_err(|_| format!("{name} must be an integer, got {value:?}"))
}

fn parse_pid(value: &str) -> Result<Option<u32>, String> {
    if value.trim().is_empty() {
        Ok(None)
    } else {
        value
            .parse()
            .map(Some)
            .map_err(|_| format!("FOLLOWER_PID must be an integer, got {value:?}"))
    }
}

fn parse_optional(value: &str) -> Option<String> {
    let value = value.trim();
    if value.is_empty() {
        None
    } else {
        Some(value.to_owned())
    }
}

fn parse_optional_path(value: &str) -> Option<PathBuf> {
    parse_optional(value).map(PathBuf::from)
}

fn parse_path_list(value: &str, tracker: &Path) -> Vec<PathBuf> {
    let tracker = tracker.to_string_lossy();
    value
        .split(':')
        .filter_map(|part| {
            let part = part.trim();
            if part.is_empty() {
                None
            } else {
                let expanded = part
                    .replace("${TRACKER}", tracker.as_ref())
                    .replace("$TRACKER", tracker.as_ref());
                Some(PathBuf::from(OsString::from(expanded)))
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn tmp_dir(name: &str) -> PathBuf {
        let base = env::var_os("VASO_AGENT_IO_ROOT")
            .map(PathBuf::from)
            .or_else(|| env::var_os("CARGO_TARGET_DIR").map(PathBuf::from))
            .expect("VASO_AGENT_IO_ROOT or CARGO_TARGET_DIR");
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir = base
            .join("autoland-tui-settings")
            .join(format!("{name}-{}-{stamp}", std::process::id()));
        fs::create_dir_all(&dir).expect("test dir");
        dir
    }

    fn git(repo: &Path, args: &[&str]) {
        let mut full_args = vec!["-C", repo.to_str().unwrap_or(".")];
        full_args.extend_from_slice(args);
        let status = ChildEnv::from_current()
            .status("git", &full_args, None)
            .expect("git spawn");
        assert!(status.success(), "git {args:?} failed");
    }

    fn init_repo(root: &Path) {
        fs::create_dir_all(root).expect("repo dir");
        git(root, &["init", "-q", "-b", "main"]);
        git(root, &["config", "user.email", "t@example.com"]);
        git(root, &["config", "user.name", "t"]);
        fs::write(root.join("README.md"), "repo\n").expect("readme");
        git(root, &["add", "README.md"]);
        git(root, &["commit", "-q", "-m", "base"]);
    }

    #[test]
    fn parses_autoland_env_output() {
        let settings = parse_settings(
            "\
VASO_ESTATE_ROOT=/estate
LEAD=/repo/.worktrees/lead
FOLLOWER=/repo
TARGET=target
LANDING=landing
LEAD_BRANCH=vaso/autoland-tui
TRACKER=.scratch/effort
AGENT=claude
FOLLOWER_AGENT=trae
AGENT_ROOT=/estate/agents/claude
FOLLOWER_LOGS=/estate/agents/trae/logs
TMPDIR=/estate/agents/claude/tmp
VASO_BAZEL_OB=/estate/agents/claude/bazel-ob
STALL_MIN=45
BATCH_HOURS=4
INTERVAL=60
BACKLOG_COMMITS=3
BACKLOG_MINUTES=30
BACKLOG_RATE_LIMIT_MIN=30
UNCOMMITTED_MIN=60
UNCOMMITTED_RATE_LIMIT_MIN=30
LOG=/estate/agents/claude/autoland.log
STATE=/estate/agents/claude/autoland.state
FOLLOWER_PID=1234
FOLLOWER_PANE=%3
FOLLOWER_ROLLOUT=/estate/rollout.jsonl
DECISION_SPECS=.scratch/effort/spec.md:.scratch/native-pytorch-build/spec.md
",
        )
        .expect("settings parse");
        assert_eq!(settings.target, "target");
        assert_eq!(settings.tracker, PathBuf::from(".scratch/effort"));
        assert_eq!(settings.follower_pid, Some(1234));
        assert_eq!(settings.follower_pane.as_deref(), Some("%3"));
        assert_eq!(
            settings.decision_specs,
            vec![
                PathBuf::from(".scratch/effort/spec.md"),
                PathBuf::from(".scratch/native-pytorch-build/spec.md")
            ]
        );
        assert_eq!(settings.interval, 60);
        assert_eq!(settings.tmpdir, PathBuf::from("/estate/agents/claude/tmp"));
        assert_eq!(
            settings.vaso_bazel_ob,
            PathBuf::from("/estate/agents/claude/bazel-ob")
        );
        assert_eq!(settings.backlog_commits, 3);
        assert_eq!(settings.backlog_minutes, 30);
        assert_eq!(settings.backlog_rate_limit_min, 30);
        assert_eq!(settings.uncommitted_min, 60);
        assert_eq!(settings.uncommitted_rate_limit_min, 30);
        assert_eq!(
            settings.follower_rollout.as_deref(),
            Some(std::path::Path::new("/estate/rollout.jsonl"))
        );
    }

    #[test]
    fn parses_worktree_git_layout_without_git_command() {
        let root = tmp_dir("layout");
        let main = root.join("repo");
        let lead = root.join("lead");
        init_repo(&main);
        git(&main, &["worktree", "add", "-q", &lead.to_string_lossy()]);

        assert_eq!(follower_from_git_layout(&lead).expect("follower"), main);
    }
}

use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use autoland_tui::settings::resolve_with_env;

const ENV_KEYS: &[&str] = &[
    "VASO_ESTATE_ROOT",
    "LEAD",
    "FOLLOWER",
    "TARGET",
    "LANDING",
    "LEAD_BRANCH",
    "TRACKER",
    "DECISION_SPECS",
    "AGENT",
    "FOLLOWER_AGENT",
    "AGENT_ROOT",
    "FOLLOWER_LOGS",
    "STALL_MIN",
    "BATCH_HOURS",
    "INTERVAL",
    "BACKLOG_COMMITS",
    "BACKLOG_MINUTES",
    "BACKLOG_RATE_LIMIT_MIN",
    "UNCOMMITTED_MIN",
    "UNCOMMITTED_RATE_LIMIT_MIN",
    "LOG",
    "STATE",
    "FOLLOWER_PID",
    "FOLLOWER_PANE",
    "FOLLOWER_ROLLOUT",
    "VASO_BAZEL_OB",
];

fn test_root(name: &str) -> PathBuf {
    let base = env::var_os("VASO_AGENT_IO_ROOT")
        .map(PathBuf::from)
        .or_else(|| env::var_os("CARGO_TARGET_DIR").map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("target"));
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_nanos();
    base.join("autoland-tui-tests")
        .join(format!("{name}-{}-{stamp}", std::process::id()))
}

fn write(path: &Path, text: &str) {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).expect("create parent");
    }
    fs::write(path, text).expect("write");
}

fn git(repo: &Path, args: &[&str]) {
    let output = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .expect("run git");
    assert!(
        output.status.success(),
        "git {:?} in {} failed: {}",
        args,
        repo.display(),
        String::from_utf8_lossy(&output.stderr)
    );
}

fn init_repo(root: &Path) {
    fs::create_dir_all(root).expect("repo dir");
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["config", "user.email", "t@example.com"]);
    git(root, &["config", "user.name", "t"]);
    let repo_root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("repo root");
    let script_dir = root.join("scripts").join("agents");
    fs::create_dir_all(&script_dir).expect("script dir");
    fs::copy(
        repo_root
            .join("scripts")
            .join("agents")
            .join("autoland-env.sh"),
        script_dir.join("autoland-env.sh"),
    )
    .expect("copy autoland-env");
    write(&root.join("README.md"), "repo\n");
    git(
        root,
        &["add", "README.md", "scripts/agents/autoland-env.sh"],
    );
    git(root, &["commit", "-q", "-m", "base"]);
}

fn script_env(repo: &Path, vars: &BTreeMap<String, String>) -> BTreeMap<String, String> {
    let names = ENV_KEYS.join(" ");
    let shell = format!(
        ". scripts/agents/autoland-env.sh && for name in {names}; do printf '%s=%s\\n' \"$name\" \"${{!name}}\"; done"
    );
    let mut command = Command::new("bash");
    command.arg("-c").arg(shell).current_dir(repo).env_clear();
    for (key, value) in vars {
        command.env(key, value);
    }
    let output = command.output().expect("bash");
    assert!(
        output.status.success(),
        "script failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout)
        .expect("utf8")
        .lines()
        .filter_map(|line| {
            line.split_once('=')
                .map(|(k, v)| (k.to_owned(), v.to_owned()))
        })
        .collect()
}

#[test]
fn native_settings_match_autoland_env_defaults_on_temp_worktree() {
    let root = test_root("settings-parity");
    let main = root.join("repo");
    let lead = root.join("lead");
    let estate = root.join("estate");
    init_repo(&main);
    git(&main, &["worktree", "add", "-q", &lead.to_string_lossy()]);
    fs::create_dir_all(&estate).expect("estate");
    let vars = BTreeMap::from([
        ("PATH".to_owned(), env::var("PATH").unwrap_or_default()),
        ("HOME".to_owned(), env::var("HOME").unwrap_or_default()),
        ("VASO_ESTATE_ROOT".to_owned(), estate.display().to_string()),
        ("FOLLOWER_PID".to_owned(), std::process::id().to_string()),
        ("FOLLOWER_PANE".to_owned(), "none".to_owned()),
    ]);

    let native = resolve_with_env(&lead, &vars, Path::new("/nonexistent"), None)
        .expect("native settings")
        .to_env_map();
    let shell = script_env(&lead, &vars);

    for key in ENV_KEYS {
        if *key == "FOLLOWER_ROLLOUT" || *key == "VASO_BAZEL_OB" {
            continue;
        }
        assert_eq!(
            native.get(*key).map(String::as_str),
            shell.get(*key).map(String::as_str),
            "{key}"
        );
    }
    assert_eq!(
        native.get("VASO_BAZEL_OB").map(String::as_str),
        Some(
            estate
                .join("agents/claude/bazel-ob")
                .to_string_lossy()
                .as_ref()
        )
    );
}

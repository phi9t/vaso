use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
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
    base.join("autoland-tui-tests")
        .join(format!("{name}-{}-{stamp}", std::process::id()))
}

fn write(path: &Path, text: &str) {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).expect("create parent directory");
    }
    fs::write(path, text).expect("write fixture");
}

fn init_repo(root: &Path) {
    fs::create_dir_all(root).expect("repo dir");
    let scripts = root.join("scripts").join("agents");
    fs::create_dir_all(&scripts).expect("scripts dir");
    let repo_root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("repo root");
    fs::copy(
        repo_root
            .join("scripts")
            .join("agents")
            .join("autoland-env.sh"),
        scripts.join("autoland-env.sh"),
    )
    .expect("copy autoland-env");
    Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["init", "-q", "-b", "main"])
        .status()
        .expect("git init");
    Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["config", "user.email", "t@example.com"])
        .status()
        .expect("git config email");
    Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["config", "user.name", "t"])
        .status()
        .expect("git config name");
    write(&root.join("base.txt"), "base\n");
    Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["add", "base.txt", "scripts/agents/autoland-env.sh"])
        .status()
        .expect("git add");
    Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["commit", "-q", "-m", "base"])
        .status()
        .expect("git commit");
}

fn write_fake_tmux(bin_dir: &Path, capture: &Path) {
    write(
        &bin_dir.join("tmux"),
        &format!(
            "#!/usr/bin/env bash\n\
if [ \"$1\" = \"-S\" ]; then\n\
  shift 2\n\
fi\n\
if [ \"$1\" = \"capture-pane\" ]; then\n\
  cat '{}'\n\
  exit 0\n\
fi\n\
exit 8\n",
            capture.display()
        ),
    );
    let mut permissions = fs::metadata(bin_dir.join("tmux"))
        .expect("fake tmux metadata")
        .permissions();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        permissions.set_mode(0o755);
    }
    fs::set_permissions(bin_dir.join("tmux"), permissions).expect("chmod fake tmux");
}

#[test]
fn once_attach_renders_state_log_and_tracker_comment() {
    let root = test_root("once-attach");
    let estate = root.join("estate");
    let follower = root.join("follower");
    let tracker = follower.join(".scratch").join("tracker");
    let logs = estate.join("agents").join("trae").join("logs");
    let agent_root = estate.join("agents").join("claude");
    fs::create_dir_all(&logs).expect("create follower logs");
    fs::create_dir_all(&agent_root).expect("create agent root");
    write(
        &tracker.join("issues").join("01-red.md"),
        "## Comments\n- 2026-09-29T12:34Z (trae): verify failed on package seat\n",
    );
    write(&logs.join("turn.log"), "building\nproof failed\n");
    write(
        &agent_root.join("autoland.log"),
        "2026-09-29T12:33:00Z START target=abcdef0 landing=1234567\n2026-09-29T12:34:00Z RED VERIFY_FAILED tests\n",
    );
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_secs();
    write(
        &agent_root.join("autoland.state"),
        &format!(
            "pid={} beat={} target=abcdef0 landing=1234567 last_move={} last_activity={} start={} stall_min=45 batch_hours=4\n",
            std::process::id(),
            now,
            now - 60,
            now - 60,
            now - 120
        ),
    );

    let exe = env!("CARGO_BIN_EXE_autoland-tui");
    let output = Command::new(exe)
        .arg("--attach")
        .arg("--once")
        .env("VASO_ESTATE_ROOT", &estate)
        .env("FOLLOWER", &follower)
        .env("TARGET", "HEAD")
        .env("LANDING", "HEAD")
        .env("TRACKER", ".scratch/tracker")
        .env("FOLLOWER_PID", std::process::id().to_string())
        .env("FOLLOWER_PANE", "none")
        .output()
        .expect("run autoland-tui");

    assert!(
        output.status.success(),
        "autoland-tui failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8(output.stdout).expect("stdout is utf8");
    assert!(stdout.contains("COCKPIT"), "{stdout}");
    assert!(stdout.contains("Attention Queue"), "{stdout}");
    assert!(stdout.contains("ATTACHED"), "{stdout}");
    assert!(stdout.contains("RED VERIFY_FAILED tests"), "{stdout}");
    assert!(stdout.contains("TRAE Session / TRAE Mirror"), "{stdout}");
    assert!(stdout.contains("no rollout file discovered"), "{stdout}");
}

#[test]
fn once_attach_renders_configured_decision_specs_and_hides_decided_items() {
    let root = test_root("once-decisions");
    let estate = root.join("estate");
    let follower = root.join("follower");
    let lead = root.join("lead");
    let logs = estate.join("agents").join("trae").join("logs");
    let agent_root = estate.join("agents").join("claude");
    init_repo(&lead);
    fs::create_dir_all(&logs).expect("create follower logs");
    fs::create_dir_all(&agent_root).expect("create agent root");
    write(
        &follower.join(".scratch").join("tracker").join("spec.md"),
        "## Decisions for the human\n- D0: follower-only fallback decision.\n",
    );
    write(
        &lead
            .join(".scratch")
            .join("native-pytorch-build")
            .join("spec.md"),
        "## Decisions for the human\n\
- **D1: choose the torch reference version.**\n\
  Status: open\n\
  The full rationale should appear in the detail view.\n\
- D2: DECIDED keep existing NCCL policy.\n\
- **D3: define the parity gate.** Byte parity plus behavior.\n\
  Status: needs human\n\
## Other Section\n\
- D4: not in scope.\n",
    );
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_secs();
    write(
        &agent_root.join("autoland.log"),
        "2026-09-29T12:33:00Z START target=abcdef0 landing=1234567\n",
    );
    write(
        &agent_root.join("autoland.state"),
        &format!(
            "pid={} beat={} target=abcdef0 landing=1234567 last_move={} last_activity={} start={} stall_min=45 batch_hours=4\n",
            std::process::id(),
            now,
            now - 60,
            now - 60,
            now - 120
        ),
    );

    let exe = env!("CARGO_BIN_EXE_autoland-tui");
    let output = Command::new(exe)
        .arg("--attach")
        .arg("--once")
        .current_dir(&lead)
        .env("VASO_ESTATE_ROOT", &estate)
        .env("FOLLOWER", &follower)
        .env("TARGET", "HEAD")
        .env("LANDING", "HEAD")
        .env("TRACKER", ".scratch/tracker")
        .env("FOLLOWER_PID", std::process::id().to_string())
        .env("FOLLOWER_PANE", "none")
        .output()
        .expect("run autoland-tui");

    assert!(
        output.status.success(),
        "autoland-tui failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8(output.stdout).expect("stdout is utf8");
    assert!(
        stdout.contains("DECISION D0: follower-only fallback decision"),
        "{stdout}"
    );
    assert!(
        stdout.contains("DECISION D1: choose the torch reference version"),
        "{stdout}"
    );
    assert!(
        stdout.contains("DECISION D3: define the parity gate"),
        "{stdout}"
    );
    assert!(!stdout.contains("D2"), "{stdout}");
    assert!(!stdout.contains("D4"), "{stdout}");
}

#[test]
fn once_attach_computes_idle_from_live_mtimes_instead_of_stale_state() {
    let root = test_root("once-live-idle");
    let estate = root.join("estate");
    let follower = root.join("follower");
    let tracker = follower.join(".scratch").join("tracker");
    let logs = estate.join("agents").join("trae").join("logs");
    let agent_root = estate.join("agents").join("claude");
    fs::create_dir_all(&logs).expect("create follower logs");
    fs::create_dir_all(&agent_root).expect("create agent root");
    write(&tracker.join("spec.md"), "# tracker\n");
    write(&logs.join("turn.log"), "fresh follower progress\n");
    write(
        &agent_root.join("autoland.log"),
        "2026-09-29T12:33:00Z START target=abcdef0 landing=1234567\n",
    );
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_secs();
    write(
        &agent_root.join("autoland.state"),
        &format!(
            "pid={} beat={} target=abcdef0 landing=1234567 last_move={} last_activity={} start={} stall_min=45 batch_hours=4\n",
            std::process::id(),
            now,
            now - 4 * 3_600 - 8 * 60,
            now - 4 * 3_600 - 8 * 60,
            now - 120
        ),
    );

    let exe = env!("CARGO_BIN_EXE_autoland-tui");
    let output = Command::new(exe)
        .arg("--attach")
        .arg("--once")
        .env("VASO_ESTATE_ROOT", &estate)
        .env("FOLLOWER", &follower)
        .env("TARGET", "HEAD")
        .env("LANDING", "HEAD")
        .env("TRACKER", ".scratch/tracker")
        .env("FOLLOWER_PID", std::process::id().to_string())
        .env("FOLLOWER_PANE", "none")
        .output()
        .expect("run autoland-tui");

    assert!(
        output.status.success(),
        "autoland-tui failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8(output.stdout).expect("stdout is utf8");
    assert!(!stdout.contains("idle 4h8m / 45m"), "{stdout}");
    assert!(!stdout.contains("IDLE follower idle 4h8m"), "{stdout}");
}

#[test]
fn once_attach_busy_live_pane_suppresses_idle_attention() {
    let root = test_root("once-busy-pane");
    let estate = root.join("estate");
    let follower = root.join("follower");
    let logs = estate.join("agents").join("trae").join("logs");
    let agent_root = estate.join("agents").join("claude");
    let bin_dir = root.join("bin");
    fs::create_dir_all(&logs).expect("create follower logs");
    fs::create_dir_all(&agent_root).expect("create agent root");
    fs::create_dir_all(&bin_dir).expect("create fake bin");
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("trae-pane-busy.txt");
    write_fake_tmux(&bin_dir, &fixture);
    write(
        &agent_root.join("autoland.log"),
        "2026-09-29T12:33:00Z START target=abcdef0 landing=1234567\n",
    );
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_secs();
    write(
        &agent_root.join("autoland.state"),
        &format!(
            "pid={} beat={} target=abcdef0 landing=1234567 last_move={} last_activity={} start={} stall_min=45 batch_hours=4\n",
            u32::MAX,
            now,
            now - 4 * 3_600 - 8 * 60,
            now - 4 * 3_600 - 8 * 60,
            now - 120
        ),
    );

    let exe = env!("CARGO_BIN_EXE_autoland-tui");
    let output = Command::new(exe)
        .arg("--attach")
        .arg("--once")
        .arg("--tab")
        .arg("2")
        .env("VASO_ESTATE_ROOT", &estate)
        .env("FOLLOWER", &follower)
        .env("TARGET", "HEAD")
        .env("LANDING", "HEAD")
        .env("TRACKER", ".scratch/tracker")
        .env("FOLLOWER_PID", std::process::id().to_string())
        .env("FOLLOWER_PANE", "%99")
        .env(
            "PATH",
            format!(
                "{}:{}",
                bin_dir.display(),
                env::var("PATH").unwrap_or_default()
            ),
        )
        .output()
        .expect("run autoland-tui");

    assert!(
        output.status.success(),
        "autoland-tui failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8(output.stdout).expect("stdout is utf8");
    assert!(stdout.contains("Pane Mirror  BUSY"), "{stdout}");
    assert!(stdout.contains("BUSY 3h42m10s"), "{stdout}");
    assert!(!stdout.contains("idle BUSY"), "{stdout}");
    assert!(!stdout.contains("IDLE follower idle"), "{stdout}");
}

#[test]
fn once_attach_uses_follower_rollout_override_for_trae_tab() {
    let root = test_root("once-rollout");
    let estate = root.join("estate");
    let follower = root.join("follower");
    let logs = estate.join("agents").join("trae").join("logs");
    let agent_root = estate.join("agents").join("claude");
    fs::create_dir_all(&logs).expect("create follower logs");
    fs::create_dir_all(&agent_root).expect("create agent root");
    let rollout = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("trae-rollout-small.jsonl");
    write(
        &agent_root.join("autoland.log"),
        "2026-09-29T12:33:00Z START target=abcdef0 landing=1234567\n",
    );
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_secs();
    write(
        &agent_root.join("autoland.state"),
        &format!(
            "pid={} beat={} target=abcdef0 landing=1234567 last_move={} last_activity={} start={} stall_min=45 batch_hours=4\n",
            std::process::id(),
            now,
            now - 60,
            now - 60,
            now - 120
        ),
    );

    let exe = env!("CARGO_BIN_EXE_autoland-tui");
    let output = Command::new(exe)
        .arg("--attach")
        .arg("--once")
        .arg("--tab")
        .arg("2")
        .env("VASO_ESTATE_ROOT", &estate)
        .env("FOLLOWER", &follower)
        .env("TARGET", "HEAD")
        .env("LANDING", "HEAD")
        .env("TRACKER", ".scratch/tracker")
        .env("FOLLOWER_PID", std::process::id().to_string())
        .env("FOLLOWER_PANE", "none")
        .env("FOLLOWER_ROLLOUT", &rollout)
        .output()
        .expect("run autoland-tui");

    assert!(
        output.status.success(),
        "autoland-tui failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8(output.stdout).expect("stdout is utf8");
    assert!(stdout.contains("TRAE Session"), "{stdout}");
    assert!(stdout.contains("py-tqdm guard failure"), "{stdout}");
    assert!(stdout.contains("context left 20%"), "{stdout}");
}

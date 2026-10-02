use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::{Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use autoland_tui::autoland::{AutolandConfig, AutolandHandle, VerifyStep};
use autoland_tui::process;
use autoland_tui::settings::{resolve_with_env, Settings};

static TEST_LOCK: Mutex<()> = Mutex::new(());

fn serial_guard() -> MutexGuard<'static, ()> {
    TEST_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn test_root(name: &str) -> PathBuf {
    let base = std::env::var_os("VASO_AGENT_IO_ROOT")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("CARGO_TARGET_DIR").map(PathBuf::from))
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

fn git(repo: &Path, args: &[&str]) -> String {
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
    String::from_utf8(output.stdout)
        .expect("git output utf8")
        .trim()
        .to_owned()
}

fn git_ok(repo: &Path, args: &[&str]) -> bool {
    Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .status()
        .expect("run git")
        .success()
}

struct Effort {
    main: PathBuf,
    lead: PathBuf,
    settings: Settings,
    follower: Child,
}

impl Effort {
    fn new(name: &str) -> Self {
        let root = test_root(name);
        let main = root.join("main");
        let lead = root.join("lead");
        let estate = root.join("estate");
        fs::create_dir_all(&main).expect("main dir");
        fs::create_dir_all(&estate).expect("estate dir");

        git(&main, &["init", "-q", "-b", "target"]);
        git(&main, &["config", "user.email", "t@example.com"]);
        git(&main, &["config", "user.name", "t"]);
        write(&main.join("conflict.txt"), "base\n");
        write(&main.join(".scratch/tracker/lead.md"), "tracker\n");
        git(&main, &["add", "conflict.txt", ".scratch/tracker/lead.md"]);
        git(&main, &["commit", "-q", "-m", "base"]);
        git(&main, &["branch", "landing"]);
        git(
            &main,
            &[
                "worktree",
                "add",
                "-q",
                "-b",
                "lead-wip",
                lead.to_str().expect("lead path"),
                "target",
            ],
        );

        let follower = Command::new("/usr/bin/sleep")
            .arg("120")
            .spawn()
            .expect("spawn follower placeholder");
        let vars = BTreeMap::from([
            ("VASO_ESTATE_ROOT".to_owned(), estate.display().to_string()),
            ("FOLLOWER".to_owned(), main.display().to_string()),
            ("TARGET".to_owned(), "target".to_owned()),
            ("LANDING".to_owned(), "landing".to_owned()),
            ("LEAD_BRANCH".to_owned(), "lead-wip".to_owned()),
            ("TRACKER".to_owned(), ".scratch/tracker".to_owned()),
            ("AGENT".to_owned(), "claude".to_owned()),
            ("FOLLOWER_AGENT".to_owned(), "trae".to_owned()),
            ("FOLLOWER_PID".to_owned(), follower.id().to_string()),
            ("FOLLOWER_PANE".to_owned(), "none".to_owned()),
            ("STALL_MIN".to_owned(), "999999".to_owned()),
            ("BATCH_HOURS".to_owned(), "999999".to_owned()),
            ("INTERVAL".to_owned(), "1".to_owned()),
            ("BACKLOG_COMMITS".to_owned(), "999999".to_owned()),
            ("BACKLOG_MINUTES".to_owned(), "999999".to_owned()),
            ("UNCOMMITTED_MIN".to_owned(), "999999".to_owned()),
        ]);
        let settings =
            resolve_with_env(&lead, &vars, Path::new("/nonexistent"), None).expect("settings");
        Self {
            main,
            lead,
            settings,
            follower,
        }
    }

    fn commit_on_target(&self, path: &str, text: &str, message: &str) -> String {
        write(&self.main.join(path), text);
        git(&self.main, &["add", path]);
        git(&self.main, &["commit", "-q", "-m", message]);
        git(&self.main, &["rev-parse", "HEAD"])
    }

    fn commit_on_lead(&self, path: &str, text: &str, message: &str) -> String {
        write(&self.lead.join(path), text);
        git(&self.lead, &["add", path]);
        git(&self.lead, &["commit", "-q", "-m", message]);
        git(&self.lead, &["rev-parse", "HEAD"])
    }

    fn stop_follower(&mut self) {
        let _ = self.follower.kill();
        let _ = self.follower.wait();
    }

    fn spawn_loop(&self, steps: Vec<VerifyStep>) -> AutolandHandle {
        AutolandHandle::spawn(self.settings.clone(), AutolandConfig::test(steps))
    }
}

impl Drop for Effort {
    fn drop(&mut self) {
        self.stop_follower();
    }
}

fn verify_step(effort: &Effort, argv: &[&str], timeout: Duration) -> VerifyStep {
    VerifyStep {
        cwd: effort.lead.clone(),
        argv: argv.iter().map(|arg| (*arg).to_owned()).collect(),
        env: BTreeMap::new(),
        timeout,
    }
}

fn wait_for_log(handle: &mut AutolandHandle, path: &Path, needle: &str) -> String {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let _ = handle.drain_lines();
        if let Ok(text) = fs::read_to_string(path) {
            if text.contains(needle) {
                return text;
            }
        }
        if let Some(reason) = handle.poll_exit() {
            panic!("loop exited with {reason:?} before log contained {needle:?}");
        }
        assert!(
            Instant::now() < deadline,
            "timed out waiting for {needle:?} in {}",
            path.display()
        );
        thread::sleep(Duration::from_millis(25));
    }
}

fn wait_for_exit(handle: &mut AutolandHandle, expected: &str) -> String {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let _ = handle.drain_lines();
        if let Some(reason) = handle.poll_exit() {
            assert!(
                reason.contains(expected),
                "expected exit reason containing {expected:?}, got {reason:?}"
            );
            return reason;
        }
        assert!(
            Instant::now() < deadline,
            "timed out waiting for exit {expected:?}"
        );
        thread::sleep(Duration::from_millis(25));
    }
}

fn wait_for_file_prefix(path: &Path, prefix: &str) -> String {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Ok(text) = fs::read_to_string(path) {
            if text.starts_with(prefix) {
                return text;
            }
        }
        assert!(
            Instant::now() < deadline,
            "timed out waiting for {} to start with {prefix:?}",
            path.display()
        );
        thread::sleep(Duration::from_millis(25));
    }
}

fn wait_until_no_descendant_cmdline(needle: &str) {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let matches = process::descendants(std::process::id())
            .into_iter()
            .filter(|pid| {
                fs::read(format!("/proc/{pid}/cmdline"))
                    .map(|bytes| String::from_utf8_lossy(&bytes).contains(needle))
                    .unwrap_or(false)
            })
            .collect::<Vec<_>>();
        if matches.is_empty() {
            return;
        }
        assert!(
            Instant::now() < deadline,
            "processes with {needle:?} were not reaped: {matches:?}"
        );
        thread::sleep(Duration::from_millis(25));
    }
}

#[test]
fn green_target_move_lands_and_follower_exit_ends_loop() {
    let _guard = serial_guard();
    let mut effort = Effort::new("green");
    let tip = effort.commit_on_target("work.txt", "target work\n", "follower slice");
    let mut handle = effort.spawn_loop(vec![verify_step(
        &effort,
        &["/usr/bin/true"],
        Duration::from_secs(5),
    )]);

    wait_for_log(&mut handle, &effort.settings.log, " LANDED ");
    assert!(git_ok(
        &effort.main,
        &["merge-base", "--is-ancestor", &tip, "landing"]
    ));
    wait_for_file_prefix(&effort.settings.state, "pid=");

    effort.stop_follower();
    wait_for_exit(&mut handle, "GONE");
    let log = fs::read_to_string(&effort.settings.log).expect("log");
    assert!(log.contains(" GONE "));
}

#[test]
fn red_verify_exits_and_leaves_landing_alone() {
    let _guard = serial_guard();
    let effort = Effort::new("red");
    let before = git(&effort.main, &["rev-parse", "landing"]);
    effort.commit_on_target("work.txt", "target work\n", "follower slice");
    let mut handle = effort.spawn_loop(vec![verify_step(
        &effort,
        &["/usr/bin/false"],
        Duration::from_secs(5),
    )]);

    wait_for_exit(&mut handle, "RED");
    let log = fs::read_to_string(&effort.settings.log).expect("log");
    assert!(log.contains(" VERIFY_FAILED rc=1 "));
    assert!(!log.contains(" LANDED "));
    assert_eq!(git(&effort.main, &["rev-parse", "landing"]), before);
}

#[test]
fn rebase_conflict_exits_red_and_keeps_branches_unchanged() {
    let _guard = serial_guard();
    let effort = Effort::new("conflict");
    let lead_tip = effort.commit_on_lead("conflict.txt", "lead\n", "lead change");
    git(&effort.lead, &["branch", "-f", "landing", "HEAD"]);
    let landing_before = git(&effort.main, &["rev-parse", "landing"]);
    effort.commit_on_target("conflict.txt", "target\n", "target change");
    let mut handle = effort.spawn_loop(vec![verify_step(
        &effort,
        &["/usr/bin/true"],
        Duration::from_secs(5),
    )]);

    wait_for_exit(&mut handle, "RED");
    let log = fs::read_to_string(&effort.settings.log).expect("log");
    assert!(log.contains(" REBASE_CONFLICT "));
    assert_eq!(git(&effort.lead, &["rev-parse", "lead-wip"]), lead_tip);
    assert_eq!(git(&effort.main, &["rev-parse", "landing"]), landing_before);
    assert!(git(&effort.lead, &["status", "--porcelain"]).is_empty());
}

#[test]
fn follower_exit_without_target_move_ends_loop() {
    let _guard = serial_guard();
    let mut effort = Effort::new("gone");
    let mut handle = effort.spawn_loop(vec![verify_step(
        &effort,
        &["/usr/bin/true"],
        Duration::from_secs(5),
    )]);

    effort.stop_follower();

    wait_for_exit(&mut handle, "GONE");
    let log = fs::read_to_string(&effort.settings.log).expect("log");
    assert!(log.contains(" GONE "));
}

#[test]
fn verify_timeout_kills_step_and_counts_as_red() {
    let _guard = serial_guard();
    let effort = Effort::new("timeout");
    let before = git(&effort.main, &["rev-parse", "landing"]);
    effort.commit_on_target("work.txt", "target work\n", "follower slice");
    let bin_dir = effort.settings.tmpdir.join("bin");
    fs::create_dir_all(&bin_dir).expect("bin dir");
    let verify_sleep = bin_dir.join("verify-sleep");
    std::os::unix::fs::symlink("/usr/bin/sleep", &verify_sleep).expect("symlink sleep");
    let needle = verify_sleep.display().to_string();
    let mut handle = effort.spawn_loop(vec![verify_step(
        &effort,
        &[&needle, "30"],
        Duration::from_millis(150),
    )]);

    wait_for_exit(&mut handle, "RED");
    let log = fs::read_to_string(&effort.settings.log).expect("log");
    assert!(log.contains(" VERIFY_FAILED rc=124 "));
    assert_eq!(git(&effort.main, &["rev-parse", "landing"]), before);
    wait_until_no_descendant_cmdline(&needle);
}

#[test]
fn verify_that_writes_into_the_checkout_is_red() {
    let _guard = serial_guard();
    let effort = Effort::new("dirty-verify");
    let before = git(&effort.main, &["rev-parse", "landing"]);
    effort.commit_on_target("work.txt", "target work\n", "follower slice");
    let dirty_path = effort.lead.join("verify-output.txt");
    let dirty = dirty_path.display().to_string();
    let mut handle = effort.spawn_loop(vec![verify_step(
        &effort,
        &["/usr/bin/touch", &dirty],
        Duration::from_secs(5),
    )]);

    wait_for_exit(&mut handle, "RED");
    let log = fs::read_to_string(&effort.settings.log).expect("log");
    assert!(log.contains("VERIFY_DIRTIED_TREE:"));
    assert!(log.contains(" RED VERIFY_FAILED rc=5 "));
    assert_eq!(git(&effort.main, &["rev-parse", "landing"]), before);
}

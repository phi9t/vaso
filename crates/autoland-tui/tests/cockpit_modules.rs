use std::env;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use autoland_tui::{outbox, rollout, tmux, workers};

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

fn git(repo: &Path, args: &[&str]) -> String {
    let output = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .expect("run git");
    assert!(
        output.status.success(),
        "git {:?} failed: {}",
        args,
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout)
        .expect("utf8")
        .trim()
        .to_owned()
}

fn init_repo(root: &Path) -> String {
    fs::create_dir_all(root).expect("repo dir");
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["config", "user.email", "t@example.com"]);
    git(root, &["config", "user.name", "t"]);
    write(&root.join("base.txt"), "base\n");
    git(root, &["add", "base.txt"]);
    git(root, &["commit", "-q", "-m", "base"]);
    let base = git(root, &["rev-parse", "HEAD"]);
    write(&root.join("work.txt"), "work\n");
    git(root, &["add", "work.txt"]);
    git(root, &["commit", "-q", "-m", "worker commit"]);
    base
}

#[test]
fn tmux_capture_parser_detects_idle_busy_todos_and_sgr_spans() {
    let busy = tmux::parse_capture(include_str!("fixtures/trae-pane-busy.ansi"));
    let idle = tmux::parse_capture(include_str!("fixtures/trae-pane-idle.ansi"));

    assert_eq!(busy.status, tmux::PaneStatus::Busy);
    assert_eq!(busy.busy_elapsed.as_deref(), Some("3h42m10s"));
    assert_eq!(idle.status, tmux::PaneStatus::Idle);
    assert!(busy.plain_text.contains("esc to interrupt"));
    assert!(idle.prompt_line.as_deref().unwrap_or("").contains('❯'));
    assert_eq!(busy.todos.len(), 5);
    assert_eq!(busy.todos[0].state, tmux::TodoState::Active);
    assert!(busy.todos[0].text.contains("py-protobuf"));
    assert_eq!(busy.todos[1].state, tmux::TodoState::Pending);

    let styled = tmux::parse_sgr("\u{1b}[1;31mRED\u{1b}[0m plain");
    assert_eq!(styled.len(), 2);
    assert_eq!(styled[0].text, "RED");
    assert_eq!(styled[0].fg, Some(tmux::AnsiColor::Red));
    assert!(styled[0].bold);
    assert_eq!(styled[1].text, " plain");
    assert_eq!(tmux::strip_ansi("\u{1b}[32mok\u{1b}[0m"), "ok");
}

#[test]
fn outbox_loads_drafts_from_front_matter_newest_first() {
    let root = test_root("outbox");
    let outbox_dir = root.join("agents").join("relay").join("outbox");
    write(
        &outbox_dir.join("20260929T120000Z-first.md"),
        "---\nauthor: claude\ntarget_pane: %3\nstatus: draft\ncreated: 2026-09-29T12:00:00Z\nsent: \ntitle: First\n---\n\nfirst body\n",
    );
    write(
        &outbox_dir.join("20260929T121000Z-second.md"),
        "---\nauthor: claude\ntarget_pane: %3\nstatus: sent\ncreated: 2026-09-29T12:10:00Z\nsent: 2026-09-29T12:11:00Z\ntitle: Second\n---\n\nsecond body\n",
    );

    let drafts = outbox::load_outbox(&root).expect("load outbox");

    assert_eq!(drafts.len(), 2);
    assert_eq!(drafts[0].title, "Second");
    assert_eq!(drafts[0].status, outbox::DraftStatus::Sent);
    assert_eq!(drafts[1].status, outbox::DraftStatus::Draft);
    assert_eq!(drafts[1].body, "first body\n");
    assert_eq!(outbox::pending_count(&drafts), 1);
}

#[test]
fn outbox_draft_and_drop_update_front_matter_and_log() {
    let root = test_root("outbox-native");
    let agent_io = root.join("agents").join("relay");
    let draft = outbox::draft_message(
        &root,
        &agent_io,
        "claude",
        Some("%9"),
        "Continue ticket 08",
        "Please continue ticket 08.",
    )
    .expect("draft");

    let text = fs::read_to_string(&draft).expect("draft text");
    assert!(text.contains("author: claude\n"));
    assert!(text.contains("target_pane: %9\n"));
    assert!(text.contains("status: draft\n"));
    assert!(text.contains("title: Continue ticket 08\n"));
    assert!(text.ends_with("Please continue ticket 08.\n"));
    let log = fs::read_to_string(root.join("agents/relay/outbox.log")).expect("draft log");
    assert!(log.contains(" DRAFT "));
    assert!(log.contains("pane=%9 author=claude"));

    outbox::drop_draft(&draft, &agent_io, &root).expect("drop");

    let text = fs::read_to_string(&draft).expect("dropped draft");
    assert!(text.contains("status: dropped\n"));
    let log = fs::read_to_string(root.join("agents/relay/outbox.log")).expect("drop log");
    assert!(log.contains(" DROP "));
}

#[test]
fn outbox_private_tmux_socket_sends_two_lines_and_refuses_busy_pane() {
    if Command::new("tmux").arg("-V").output().is_err() {
        return;
    }
    let root = test_root("outbox-tmux");
    let agent_io = root.join("agents").join("relay");
    let sock_base = env::var_os("TMPDIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join("tmp"));
    fs::create_dir_all(&sock_base).expect("socket base");
    let sock_dir = sock_base.join(format!("tx{}", std::process::id()));
    let _ = fs::remove_dir_all(&sock_dir);
    fs::create_dir_all(&sock_dir).expect("socket dir");
    let socket = sock_dir.join("s");
    let received = root.join("received.txt");
    let receiver = root.join("receiver.sh");
    write(
        &receiver,
        &format!(
            "#!/bin/sh\nprintf '❯ '\nhead -n 2 > '{}'\nsleep 60\n",
            received.display()
        ),
    );
    let mut perms = fs::metadata(&receiver)
        .expect("receiver metadata")
        .permissions();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        perms.set_mode(0o755);
    }
    fs::set_permissions(&receiver, perms).expect("chmod receiver");
    let server = |args: &[&str]| -> std::process::Output {
        Command::new("tmux")
            .arg("-S")
            .arg(&socket)
            .args(args)
            .output()
            .expect("tmux")
    };

    let command = format!("{}", receiver.display());
    let output = server(&["new-session", "-d", "-s", "outbox", &command]);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let cleanup_socket = socket.clone();
    let cleanup_dir = sock_dir.clone();
    let result = std::panic::catch_unwind(|| {
        let pane_output = server(&["display-message", "-p", "-t", "outbox:0.0", "#{pane_id}"]);
        assert!(pane_output.status.success());
        let pane = String::from_utf8(pane_output.stdout).expect("pane utf8");
        let pane = pane.trim();
        wait_for_tmux_text(&socket, pane, "❯");

        let draft = outbox::draft_message(
            &root,
            &agent_io,
            "claude",
            Some(pane),
            "Two line note",
            "line one\nline two\n",
        )
        .expect("draft");
        outbox::send_draft_with_socket(&draft, None, None, &agent_io, &root, socket.to_str())
            .expect("send draft");
        wait_for_file_text(&received, "line one\nline two\n");
        assert!(fs::read_to_string(&draft)
            .expect("sent draft")
            .contains("status: sent\n"));

        let busy_output = server(&[
            "new-session",
            "-d",
            "-s",
            "busy",
            "printf 'esc to interrupt\n❯ '; sleep 60",
        ]);
        assert!(
            busy_output.status.success(),
            "{}",
            String::from_utf8_lossy(&busy_output.stderr)
        );
        let busy_pane = server(&["display-message", "-p", "-t", "busy:0.0", "#{pane_id}"]);
        assert!(busy_pane.status.success());
        let busy_pane = String::from_utf8(busy_pane.stdout).expect("busy pane utf8");
        let busy_pane = busy_pane.trim();
        wait_for_tmux_text(&socket, busy_pane, "esc to interrupt");
        let busy_draft = outbox::draft_message(
            &root,
            &agent_io,
            "claude",
            Some(busy_pane),
            "busy",
            "should not send\n",
        )
        .expect("busy draft");
        let refused = outbox::send_draft_with_socket(
            &busy_draft,
            None,
            None,
            &agent_io,
            &root,
            socket.to_str(),
        )
        .expect_err("busy pane refused");
        assert!(refused.to_string().contains("not idle"));
        assert!(fs::read_to_string(&busy_draft)
            .expect("busy draft text")
            .contains("status: draft\n"));
    });
    let _ = Command::new("tmux")
        .arg("-S")
        .arg(&cleanup_socket)
        .arg("kill-server")
        .status();
    let _ = fs::remove_dir_all(cleanup_dir);
    assert!(result.is_ok());
}

fn wait_for_file_text(path: &Path, expected: &str) {
    let deadline = SystemTime::now() + Duration::from_secs(10);
    loop {
        if fs::read_to_string(path).ok().as_deref() == Some(expected) {
            return;
        }
        assert!(
            SystemTime::now() < deadline,
            "timed out waiting for {}",
            path.display()
        );
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn wait_for_tmux_text(socket: &Path, pane: &str, needle: &str) {
    let deadline = SystemTime::now() + Duration::from_secs(10);
    loop {
        let output = Command::new("tmux")
            .arg("-S")
            .arg(socket)
            .args(["capture-pane", "-p", "-J", "-S", "-20", "-t", pane])
            .output()
            .expect("capture pane");
        let text = String::from_utf8_lossy(&output.stdout);
        if text.contains(needle) {
            return;
        }
        assert!(
            SystemTime::now() < deadline,
            "timed out waiting for {needle}"
        );
        std::thread::sleep(Duration::from_millis(50));
    }
}

#[test]
fn workers_scan_runs_reads_state_digest_and_commits() {
    let root = test_root("workers");
    let repo = root.join("repo");
    let base = init_repo(&repo);
    let run = root
        .join("agents")
        .join("traecli-tui")
        .join("runs")
        .join("20260929T120000Z");
    write(
        &run.join("meta.json"),
        &format!(
            "{{\"pid\":999999,\"agent\":\"traecli-tui\",\"worktree\":\"{}\",\"branch\":\"worker\",\"base\":\"{}\",\"started_utc\":\"2026-09-29T12:00:00Z\"}}\n",
            repo.display(),
            base
        ),
    );
    write(&run.join("exit_code"), "4\n");
    write(
        &run.join("events.jsonl"),
        "{\"type\":\"item.completed\",\"item\":{\"type\":\"command_execution\",\"command\":\"cargo test\",\"exit_code\":0,\"aggregated_output\":\"running tests\\nall ok\"}}\n{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":12,\"output_tokens\":3}}\n",
    );

    let runs = workers::scan_workers(&root).expect("scan workers");

    assert_eq!(runs.len(), 1);
    assert_eq!(runs[0].agent, "traecli-tui");
    assert_eq!(runs[0].state, workers::WorkerState::Exited(4));
    assert!(runs[0]
        .digest
        .iter()
        .any(|line| line.contains("cargo test")));
    assert!(runs[0].digest.iter().any(|line| line.contains("turn done")));
    assert_eq!(runs[0].commits, vec!["worker commit"]);
}

#[test]
fn busy_marker_above_a_long_todo_list_is_still_busy() {
    let mut lines = vec!["⋄ Working (1m • esc to interrupt)".to_owned()];
    lines.extend((0..14).map(|i| format!("    ◻ step {i}")));
    lines.extend([
        "─────".to_owned(),
        "❯ Explain this codebase".to_owned(),
        "─────".to_owned(),
        "  GPT-5.5 · Context 50% left".to_owned(),
    ]);
    assert_eq!(tmux::pane_status(&lines), tmux::PaneStatus::Busy);
}

#[test]
fn rollout_fixture_parses_structured_session_events() {
    let summary = rollout::parse_lines(
        include_str!("fixtures/trae-rollout-small.jsonl").lines(),
        32,
    );

    assert_eq!(summary.agent_messages.len(), 1);
    assert!(summary.agent_messages[0]
        .text
        .contains("py-tqdm guard failure"));
    assert_eq!(summary.commands.len(), 3);
    assert!(summary
        .commands
        .iter()
        .any(|command| command.exit_code == Some(1)
            && command.command.contains("false")
            && command.duration_ms == Some(1_000)));
    assert_eq!(summary.file_changes.len(), 2);
    assert!(summary
        .file_changes
        .iter()
        .any(|change| change.path.ends_with("src/rollout.rs") && change.change_type == "add"));
    assert_eq!(summary.compactions.len(), 2);
    assert_eq!(summary.token.as_ref().expect("token").input_tokens, 160_000);
    assert_eq!(
        summary
            .token
            .as_ref()
            .expect("token")
            .context_left_percent(),
        Some(20)
    );
    let turn = summary.turn.as_ref().expect("turn timing");
    assert_eq!(turn.turn_id.as_deref(), Some("turn-a"));
    assert_eq!(turn.elapsed_ms, Some(20_000));
}

#[test]
fn rollout_tail_reader_reads_only_a_bounded_suffix_of_large_files() {
    let root = test_root("rollout-tail");
    let path = root.join("rollout-large.jsonl");
    fs::create_dir_all(&root).expect("create root");
    let mut file = fs::File::create(&path).expect("create rollout");
    let filler = format!(
        "{{\"timestamp\":\"2026-09-30T00:00:00.000Z\",\"ordinal\":0,\"type\":\"history_mutation\",\"payload\":{{\"blob\":\"{}\"}}}}\n",
        "x".repeat(4096)
    );
    while file.metadata().expect("metadata").len() < 5 * 1024 * 1024 {
        file.write_all(filler.as_bytes()).expect("write filler");
    }
    file.write_all(include_str!("fixtures/trae-rollout-small.jsonl").as_bytes())
        .expect("write fixture tail");
    drop(file);

    let tail =
        rollout::read_tail_lines(&path, 12, 1024 * 1024, 2 * 1024 * 1024).expect("tail rollout");

    assert!(fs::metadata(&path).expect("metadata").len() > 5 * 1024 * 1024);
    assert!(
        tail.bytes_read <= 2 * 1024 * 1024,
        "tail reader read {} bytes",
        tail.bytes_read
    );
    let summary = rollout::parse_lines(tail.lines.iter().map(String::as_str), 32);
    assert!(summary.agent_messages[0]
        .text
        .contains("py-tqdm guard failure"));
    assert_eq!(tail.offset, fs::metadata(&path).expect("metadata").len());
}

#[test]
fn rollout_cursor_refresh_reads_only_appended_bytes() {
    let root = test_root("rollout-incremental");
    let path = root.join("rollout.jsonl");
    fs::create_dir_all(&root).expect("create root");
    let mut lines = include_str!("fixtures/trae-rollout-small.jsonl").lines();
    write(
        &path,
        &format!(
            "{}\n{}\n",
            lines.next().expect("line 1"),
            lines.next().expect("line 2")
        ),
    );
    let mut cursor = rollout::RolloutCursor::new(32, 4096, 64 * 1024, 4096);

    let first = cursor.refresh(&path).expect("initial refresh");
    assert_eq!(first.summary.agent_messages.len(), 1);
    let initial_offset = first.offset;
    let appended =
        "{\"timestamp\":\"2026-09-30T01:12:00.000Z\",\"ordinal\":13,\"type\":\"event_msg\",\"payload\":{\"type\":\"item_completed\",\"turn_id\":\"turn-b\",\"item\":{\"type\":\"AgentMessage\",\"content\":[{\"type\":\"Text\",\"text\":\"Incremental message only.\"}]},\"started_at_ms\":1790730720000,\"completed_at_ms\":1790730721000}}\n";
    fs::OpenOptions::new()
        .append(true)
        .open(&path)
        .expect("open append")
        .write_all(appended.as_bytes())
        .expect("append rollout line");

    let second = cursor.refresh(&path).expect("incremental refresh");

    assert_eq!(second.bytes_read as usize, appended.len());
    assert!(second.offset > initial_offset);
    assert!(second
        .summary
        .agent_messages
        .iter()
        .any(|message| message.text == "Incremental message only."));
}

#[test]
fn repo_is_found_outside_the_repo_via_override_or_build_repo() {
    use autoland_tui::settings::locate_repo;
    let repo = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let outside = test_root("outside-repo-discovery");
    fs::create_dir_all(&outside).expect("outside dir");
    assert!(locate_repo(
        &outside,
        Some(repo.clone()),
        std::path::Path::new("/nonexistent")
    )
    .is_some());
    assert!(locate_repo(&outside, None, &repo).is_some());
}

#[test]
fn repo_discovery_ignores_empty_git_marker_dirs() {
    use autoland_tui::settings::locate_repo;
    let root = test_root("empty-git-marker");
    let outside = root.join("outside");
    fs::create_dir_all(outside.join(".git")).expect("empty git marker");
    assert!(locate_repo(&outside, None, std::path::Path::new("/nonexistent")).is_none());
}

#[test]
fn busy_markers_beyond_esc_to_interrupt() {
    let frame = |status: &str| -> Vec<String> {
        vec![
            status.to_owned(),
            "─────".to_owned(),
            "❯ Explain this codebase".to_owned(),
            "─────".to_owned(),
            "  GPT-5.5 · Context 42% left".to_owned(),
        ]
    };
    // The status line mid-redraw can lack "esc to interrupt" but keeps the timer.
    assert_eq!(tmux::pane_status(&frame("◆ Update docs… (3h 58m 25s • ↓ 515K tokens • ")), tmux::PaneStatus::Busy);
    // A message already queued behind a running tool call.
    assert_eq!(tmux::pane_status(&frame("  Send after tool call · ↑ to edit")), tmux::PaneStatus::Busy);
    // Idle with a background shell: traecli accepts input normally.
    assert_eq!(
        tmux::pane_status(&frame("❖ 1 background shell running… you can still chat · /ps to manage")),
        tmux::PaneStatus::Idle
    );
}

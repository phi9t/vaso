use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::env;
use std::ffi::{OsStr, OsString};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Output, Stdio};
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU32, Ordering};
use std::sync::Once;
use std::thread;
use std::time::{Duration, Instant};

use std::os::unix::process::CommandExt;

const RAM_FILESYSTEMS: &[&str] = &["tmpfs", "ramfs", "devtmpfs"];
const MAX_SUPERVISED_CHILDREN: usize = 64;

static TERMINATION_REQUESTED: AtomicBool = AtomicBool::new(false);
static SIGNAL_INIT: Once = Once::new();
static SIGNAL_INSTALL_ERROR: AtomicI32 = AtomicI32::new(0);
static SUPERVISED_PIDS: [AtomicU32; MAX_SUPERVISED_CHILDREN] =
    [const { AtomicU32::new(0) }; MAX_SUPERVISED_CHILDREN];

/// Explicit child environment for every direct argv subprocess.
///
/// The inherited allowlist is intentionally small: PATH, HOME, LANG, LC_*,
/// TERM and USER. Runtime code then pins VASO_ESTATE_ROOT, VASO_AGENT_IO_ROOT,
/// TMPDIR and VASO_BAZEL_OB, and verify steps may add their own variables.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ChildEnv {
    vars: BTreeMap<OsString, OsString>,
}

impl ChildEnv {
    pub fn from_current() -> Self {
        Self::from_vars(env::vars_os())
    }

    pub fn from_vars<I, K, V>(vars: I) -> Self
    where
        I: IntoIterator<Item = (K, V)>,
        K: Into<OsString>,
        V: Into<OsString>,
    {
        let mut env = Self::default();
        for (key, value) in vars {
            let key = key.into();
            if allow_inherited(&key) || is_pinned(&key) {
                env.vars.insert(key, value.into());
            }
        }
        env
    }

    pub fn with_var<K, V>(mut self, key: K, value: V) -> Self
    where
        K: Into<OsString>,
        V: Into<OsString>,
    {
        self.vars.insert(key.into(), value.into());
        self
    }

    pub fn set<K, V>(&mut self, key: K, value: V)
    where
        K: Into<OsString>,
        V: Into<OsString>,
    {
        self.vars.insert(key.into(), value.into());
    }

    pub fn get(&self, key: &str) -> Option<&OsStr> {
        self.vars.get(OsStr::new(key)).map(OsString::as_os_str)
    }

    pub fn vars(&self) -> impl Iterator<Item = (&OsString, &OsString)> {
        self.vars.iter()
    }

    pub fn command<P>(&self, program: P) -> Command
    where
        P: AsRef<OsStr>,
    {
        let mut command = Command::new(program);
        command.env_clear();
        for (key, value) in &self.vars {
            command.env(key, value);
        }
        command.process_group(0);
        command
    }

    pub fn output<P, A>(&self, program: P, args: &[A], cwd: Option<&Path>) -> io::Result<Output>
    where
        P: AsRef<OsStr>,
        A: AsRef<OsStr>,
    {
        let mut command = self.command(program);
        command.args(args);
        if let Some(cwd) = cwd {
            command.current_dir(cwd);
        }
        command.stdout(Stdio::piped()).stderr(Stdio::piped());
        let child = command.spawn()?;
        let pid = child.id();
        register_supervised_pid(pid);
        let output = child.wait_with_output();
        unregister_supervised_pid(pid);
        output
    }

    pub fn status<P, A>(&self, program: P, args: &[A], cwd: Option<&Path>) -> io::Result<ExitStatus>
    where
        P: AsRef<OsStr>,
        A: AsRef<OsStr>,
    {
        let mut command = self.command(program);
        command.args(args);
        if let Some(cwd) = cwd {
            command.current_dir(cwd);
        }
        let mut child = command.spawn()?;
        let pid = child.id();
        register_supervised_pid(pid);
        let status = child.wait();
        unregister_supervised_pid(pid);
        status
    }

    pub fn spawn_supervised<P, A>(
        &self,
        program: P,
        args: &[A],
        cwd: Option<&Path>,
    ) -> io::Result<SupervisedChild>
    where
        P: AsRef<OsStr>,
        A: AsRef<OsStr>,
    {
        let mut command = self.command(program);
        command.args(args);
        if let Some(cwd) = cwd {
            command.current_dir(cwd);
        }
        SupervisedChild::spawn(command)
    }
}

fn allow_inherited(key: &OsStr) -> bool {
    let Some(name) = key.to_str() else {
        return false;
    };
    matches!(name, "PATH" | "HOME" | "LANG" | "TERM" | "USER") || name.starts_with("LC_")
}

fn is_pinned(key: &OsStr) -> bool {
    matches!(
        key.to_str(),
        Some("VASO_ESTATE_ROOT" | "VASO_AGENT_IO_ROOT" | "TMPDIR" | "VASO_BAZEL_OB")
    )
}

pub fn agent_io_env(estate_root: &Path, agent: &str) -> io::Result<(PathBuf, ChildEnv)> {
    let estate_root = canonicalize_existing_or_parent(estate_root)?;
    let kind = fs_type_from_mountinfo_path(&estate_root, Path::new("/proc/self/mountinfo"))?;
    if RAM_FILESYSTEMS.contains(&kind.as_str()) {
        return Err(io::Error::other(format!(
            "estate root {} is on {kind} (RAM-backed); use a disk-backed estate root",
            estate_root.display()
        )));
    }
    let root = estate_root.join("agents").join(agent);
    let tmp = root.join("tmp");
    let bazel_ob = root.join("bazel-ob");
    fs::create_dir_all(&tmp)?;
    fs::create_dir_all(&bazel_ob)?;
    let env = ChildEnv::from_current()
        .with_var("VASO_ESTATE_ROOT", estate_root.as_os_str())
        .with_var("VASO_AGENT_IO_ROOT", root.as_os_str())
        .with_var("TMPDIR", tmp.as_os_str())
        .with_var("VASO_BAZEL_OB", bazel_ob.as_os_str());
    Ok((root, env))
}

pub fn fs_type_from_mountinfo_path(path: &Path, mountinfo: &Path) -> io::Result<String> {
    fs_type_from_mountinfo(path, &fs::read_to_string(mountinfo)?)
}

pub fn fs_type_from_mountinfo(path: &Path, mountinfo: &str) -> io::Result<String> {
    let target = canonicalize_existing_or_parent(path)?;
    let target = target.to_string_lossy();
    let mut best_len = 0;
    let mut kind = "unknown";
    for line in mountinfo.lines() {
        let Some((left, right)) = line.split_once(" - ") else {
            continue;
        };
        let fields = left.split_whitespace().collect::<Vec<_>>();
        let right_fields = right.split_whitespace().collect::<Vec<_>>();
        if fields.len() < 5 || right_fields.is_empty() {
            continue;
        }
        let mount = fields[4].replace("\\040", " ");
        if (target == mount || target.starts_with(&(mount.trim_end_matches('/').to_owned() + "/")))
            && mount.len() > best_len
        {
            best_len = mount.len();
            kind = right_fields[0];
        }
    }
    Ok(kind.to_owned())
}

fn canonicalize_existing_or_parent(path: &Path) -> io::Result<PathBuf> {
    let mut candidate = path;
    while !candidate.exists() {
        candidate = candidate.parent().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                format!("no existing parent for {}", path.display()),
            )
        })?;
    }
    let resolved = candidate.canonicalize()?;
    Ok(if candidate == path {
        resolved
    } else {
        let suffix = path
            .strip_prefix(candidate)
            .map_err(|error| io::Error::other(error.to_string()))?;
        resolved.join(suffix)
    })
}

pub struct SupervisedChild {
    child: Child,
    pid: u32,
    kill_on_drop: bool,
    registered: bool,
}

impl SupervisedChild {
    pub fn spawn(mut command: Command) -> io::Result<Self> {
        command.process_group(0);
        let child = command.spawn()?;
        let pid = child.id();
        register_supervised_pid(pid);
        Ok(Self {
            child,
            pid,
            kill_on_drop: true,
            registered: true,
        })
    }

    pub fn pid(&self) -> u32 {
        self.pid
    }

    pub fn child_mut(&mut self) -> &mut Child {
        &mut self.child
    }

    pub fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
        let status = self.child.try_wait()?;
        if status.is_some() {
            self.mark_finished();
        }
        Ok(status)
    }

    pub fn wait(mut self) -> io::Result<ExitStatus> {
        let status = self.child.wait()?;
        self.mark_finished();
        Ok(status)
    }

    pub fn wait_timeout(&mut self, timeout: Duration) -> io::Result<Option<ExitStatus>> {
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(status) = self.try_wait()? {
                return Ok(Some(status));
            }
            if Instant::now() >= deadline {
                return Ok(None);
            }
            thread::sleep(Duration::from_millis(20));
        }
    }

    pub fn stop_with_grace(&mut self, grace: Duration) -> io::Result<ExitStatus> {
        signal_process_group(self.pid, libc::SIGTERM);
        let deadline = Instant::now() + grace;
        loop {
            if let Some(status) = self.try_wait()? {
                return Ok(status);
            }
            if Instant::now() >= deadline {
                break;
            }
            thread::sleep(Duration::from_millis(20));
        }
        signal_process_group(self.pid, libc::SIGKILL);
        let status = self.child.wait()?;
        self.mark_finished();
        Ok(status)
    }

    fn mark_finished(&mut self) {
        self.kill_on_drop = false;
        if self.registered {
            unregister_supervised_pid(self.pid);
            self.registered = false;
        }
    }
}

impl Drop for SupervisedChild {
    fn drop(&mut self) {
        if self.kill_on_drop {
            let _ = self.stop_with_grace(Duration::from_millis(200));
        } else if self.registered {
            unregister_supervised_pid(self.pid);
            self.registered = false;
        }
    }
}

pub fn signal_process_group(pid: u32, signal: i32) {
    unsafe {
        let _ = libc::killpg(pid as libc::pid_t, signal);
    }
}

pub fn install_signal_handlers() -> io::Result<()> {
    SIGNAL_INIT.call_once(|| {
        for signal in [libc::SIGINT, libc::SIGTERM] {
            let error = unsafe { install_signal_handler(signal) };
            if error != 0 {
                SIGNAL_INSTALL_ERROR.store(error, Ordering::SeqCst);
                break;
            }
        }
    });
    let error = SIGNAL_INSTALL_ERROR.load(Ordering::SeqCst);
    if error == 0 {
        Ok(())
    } else {
        Err(io::Error::from_raw_os_error(error))
    }
}

pub fn termination_requested() -> bool {
    TERMINATION_REQUESTED.load(Ordering::SeqCst)
}

unsafe fn install_signal_handler(signal: i32) -> i32 {
    let mut action: libc::sigaction = std::mem::zeroed();
    action.sa_sigaction = termination_signal_handler as *const () as usize;
    action.sa_flags = 0;
    libc::sigemptyset(&mut action.sa_mask);
    if libc::sigaction(signal, &action, std::ptr::null_mut()) == 0 {
        0
    } else {
        io::Error::last_os_error()
            .raw_os_error()
            .unwrap_or(libc::EINVAL)
    }
}

extern "C" fn termination_signal_handler(_signal: i32) {
    TERMINATION_REQUESTED.store(true, Ordering::SeqCst);
    for slot in &SUPERVISED_PIDS {
        let pid = slot.load(Ordering::SeqCst);
        if pid != 0 {
            unsafe {
                let _ = libc::killpg(pid as libc::pid_t, libc::SIGTERM);
            }
        }
    }
}

fn register_supervised_pid(pid: u32) {
    for slot in &SUPERVISED_PIDS {
        if slot
            .compare_exchange(0, pid, Ordering::SeqCst, Ordering::SeqCst)
            .is_ok()
        {
            return;
        }
    }
}

fn unregister_supervised_pid(pid: u32) {
    for slot in &SUPERVISED_PIDS {
        if slot.load(Ordering::SeqCst) == pid {
            let _ = slot.compare_exchange(pid, 0, Ordering::SeqCst, Ordering::SeqCst);
            return;
        }
    }
}

pub fn process_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    let rc = unsafe { libc::kill(pid as libc::pid_t, 0) };
    if rc != 0 {
        return false;
    }
    !proc_stat(pid)
        .map(|stat| stat.state == 'Z')
        .unwrap_or(false)
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProcStat {
    pub pid: u32,
    pub comm: String,
    pub state: char,
    pub ppid: u32,
}

pub fn proc_stat(pid: u32) -> io::Result<ProcStat> {
    parse_proc_stat(&fs::read_to_string(format!("/proc/{pid}/stat"))?)
}

pub fn parse_proc_stat(text: &str) -> io::Result<ProcStat> {
    let open = text
        .find('(')
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "stat missing comm start"))?;
    let close = text
        .rfind(')')
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "stat missing comm end"))?;
    let pid = text[..open]
        .trim()
        .parse::<u32>()
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    let comm = text[open + 1..close].to_owned();
    let fields = text[close + 1..].split_whitespace().collect::<Vec<_>>();
    let state = fields
        .first()
        .and_then(|value| value.chars().next())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "stat missing state"))?;
    let ppid = fields
        .get(1)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "stat missing ppid"))?
        .parse::<u32>()
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    Ok(ProcStat {
        pid,
        comm,
        state,
        ppid,
    })
}

pub fn pid_is_or_ancestor(ancestor: u32, mut child: u32) -> bool {
    if ancestor == 0 || child == 0 {
        return false;
    }
    while child > 1 {
        if child == ancestor {
            return true;
        }
        let Ok(stat) = proc_stat(child) else {
            return false;
        };
        if stat.ppid == child {
            return false;
        }
        child = stat.ppid;
    }
    child == ancestor
}

pub fn descendants(pid: u32) -> Vec<u32> {
    let mut result = Vec::new();
    let mut seen = BTreeSet::new();
    let mut queue = VecDeque::from([pid]);
    seen.insert(pid);
    while let Some(parent) = queue.pop_front() {
        for child in direct_children(parent) {
            if seen.insert(child) {
                result.push(child);
                queue.push_back(child);
            }
        }
    }
    result
}

fn direct_children(pid: u32) -> Vec<u32> {
    let from_tasks = children_from_task_files(pid);
    if !from_tasks.is_empty() {
        return from_tasks;
    }
    children_by_ppid_scan(pid)
}

fn children_from_task_files(pid: u32) -> Vec<u32> {
    let mut children = BTreeSet::new();
    let tasks = match fs::read_dir(format!("/proc/{pid}/task")) {
        Ok(tasks) => tasks,
        Err(_) => return Vec::new(),
    };
    for task in tasks.flatten() {
        let path = task.path().join("children");
        let Ok(text) = fs::read_to_string(path) else {
            continue;
        };
        for value in text.split_whitespace() {
            if let Ok(child) = value.parse::<u32>() {
                children.insert(child);
            }
        }
    }
    children.into_iter().collect()
}

fn children_by_ppid_scan(pid: u32) -> Vec<u32> {
    let mut children = Vec::new();
    let Ok(entries) = fs::read_dir("/proc") else {
        return children;
    };
    for entry in entries.flatten() {
        let name = entry.file_name();
        let Some(text) = name.to_str() else {
            continue;
        };
        let Ok(candidate) = text.parse::<u32>() else {
            continue;
        };
        if proc_stat(candidate)
            .map(|stat| stat.ppid == pid)
            .unwrap_or(false)
        {
            children.push(candidate);
        }
    }
    children.sort_unstable();
    children
}

pub fn busy_commands(root_pid: u32) -> Vec<(String, usize)> {
    let watched = [
        "bazel", "insula", "run.sh", "spack", "python", "python3", "pytest", "git",
    ];
    let mut counts = BTreeMap::<String, usize>::new();
    for pid in descendants(root_pid) {
        let Ok(comm) = fs::read_to_string(format!("/proc/{pid}/comm")) else {
            continue;
        };
        let normalized = comm.trim();
        for name in watched {
            if normalized == name || (name == "python" && normalized == "python3") {
                *counts.entry(normalized.to_owned()).or_default() += 1;
                break;
            }
        }
    }
    counts.into_iter().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::File;
    use std::io::Read;
    use std::panic;

    fn tmp_dir(name: &str) -> PathBuf {
        let root = env::var_os("TMPDIR")
            .map(PathBuf::from)
            .or_else(|| {
                env::var_os("VASO_AGENT_IO_ROOT").map(|root| PathBuf::from(root).join("tmp"))
            })
            .expect("TMPDIR or VASO_AGENT_IO_ROOT is set for tests");
        let dir = root.join(format!(
            "autoland-tui-process-{}-{}",
            name,
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("create test dir");
        dir
    }

    #[test]
    fn child_env_keeps_only_allowlisted_and_pinned_vars() {
        let child = ChildEnv::from_vars([
            ("PATH", "/usr/bin"),
            ("HOME", "/home/tester"),
            ("LANG", "C.UTF-8"),
            ("LC_ALL", "C"),
            ("TERM", "xterm-256color"),
            ("USER", "tester"),
            ("SECRET_TOKEN", "nope"),
            ("TMPDIR", "/disk/tmp"),
            ("VASO_BAZEL_OB", "/disk/bazel-ob"),
            ("VASO_AGENT_IO_ROOT", "/disk/agents/claude"),
            ("VASO_ESTATE_ROOT", "/disk"),
        ]);
        let keys = child
            .vars()
            .map(|(key, _)| key.to_string_lossy().into_owned())
            .collect::<BTreeSet<_>>();
        assert!(keys.contains("PATH"));
        assert!(keys.contains("HOME"));
        assert!(keys.contains("LANG"));
        assert!(keys.contains("LC_ALL"));
        assert!(keys.contains("TERM"));
        assert!(keys.contains("USER"));
        assert!(keys.contains("TMPDIR"));
        assert!(keys.contains("VASO_BAZEL_OB"));
        assert!(keys.contains("VASO_AGENT_IO_ROOT"));
        assert!(keys.contains("VASO_ESTATE_ROOT"));
        assert!(!keys.contains("SECRET_TOKEN"));
    }

    #[test]
    fn child_env_executes_with_only_allowlisted_and_pinned_vars() {
        let child = ChildEnv::from_vars([
            ("PATH", "/usr/bin:/bin"),
            ("HOME", "/home/tester"),
            ("LANG", "C.UTF-8"),
            ("LC_ALL", "C"),
            ("TERM", "xterm-256color"),
            ("USER", "tester"),
            ("SECRET_TOKEN", "nope"),
            ("TMPDIR", "/disk/tmp"),
            ("VASO_BAZEL_OB", "/disk/bazel-ob"),
            ("VASO_AGENT_IO_ROOT", "/disk/agents/claude"),
            ("VASO_ESTATE_ROOT", "/disk"),
        ]);
        let output = child
            .output("/usr/bin/env", &[] as &[&str], None)
            .expect("env command");
        assert!(output.status.success());
        let text = String::from_utf8(output.stdout).expect("env utf8");
        assert!(text.contains("PATH=/usr/bin:/bin\n"));
        assert!(text.contains("TMPDIR=/disk/tmp\n"));
        assert!(text.contains("VASO_AGENT_IO_ROOT=/disk/agents/claude\n"));
        assert!(!text.contains("SECRET_TOKEN="));
    }

    #[test]
    fn parses_mountinfo_and_refuses_ram_filesystems() {
        let root = tmp_dir("mountinfo");
        let mount = root.join("estate");
        fs::create_dir_all(&mount).expect("mount dir");
        let escaped = mount.to_string_lossy().replace(' ', "\\040");
        let fixture = format!(
            "1 0 8:1 / / rw,relatime - ext4 /dev/sda rw\n2 1 0:32 / {escaped} rw,nosuid - tmpfs tmpfs rw\n"
        );
        assert_eq!(fs_type_from_mountinfo(&mount, &fixture).unwrap(), "tmpfs");
        assert!(RAM_FILESYSTEMS.contains(&"tmpfs"));
    }

    #[test]
    fn parses_proc_stat_with_spaces_in_comm() {
        let stat = parse_proc_stat("123 (name with space) S 45 1 1 0").expect("stat");
        assert_eq!(stat.pid, 123);
        assert_eq!(stat.comm, "name with space");
        assert_eq!(stat.state, 'S');
        assert_eq!(stat.ppid, 45);
    }

    #[test]
    fn supervised_child_drop_kills_and_reaps_long_running_process() {
        let env = ChildEnv::from_current();
        let mut child = env
            .spawn_supervised("sleep", &["30"], None)
            .expect("spawn sleep");
        let pid = child.pid();
        assert!(process_alive(pid));
        child
            .stop_with_grace(Duration::from_millis(50))
            .expect("stop child");
        assert!(!process_alive(pid));
    }

    #[test]
    fn supervised_child_drop_runs_during_panic() {
        let pid_file = tmp_dir("panic").join("pid");
        let result = panic::catch_unwind({
            let pid_file = pid_file.clone();
            move || {
                let env = ChildEnv::from_current();
                let child = env
                    .spawn_supervised("sleep", &["30"], None)
                    .expect("spawn sleep");
                fs::write(&pid_file, child.pid().to_string()).expect("pid write");
                assert!(process_alive(child.pid()));
                panic!("simulated panic");
            }
        });
        assert!(result.is_err());
        let mut text = String::new();
        File::open(&pid_file)
            .expect("pid file")
            .read_to_string(&mut text)
            .expect("pid read");
        let pid = text.trim().parse::<u32>().expect("pid parse");
        assert!(!process_alive(pid));
    }
}

use camino::Utf8PathBuf;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct RootfsProfile {
    pub name: String,
    pub rootfs_path: Utf8PathBuf,
    pub shell_path: Utf8PathBuf,
    pub tmux_path: Option<Utf8PathBuf>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum AttachPolicy {
    DirectTmux,
    LocalPtyBridge { listen_addr: String, port: u16 },
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct TmuxPolicy {
    pub session_name: String,
    pub socket_path: Utf8PathBuf,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct InsulaStartRequest {
    pub session_name: String,
    pub rootfs: RootfsProfile,
    pub runtime_dir: Utf8PathBuf,
    pub attach: AttachPolicy,
    pub tmux: TmuxPolicy,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum InsulaLifecycle {
    Stopped,
    Starting,
    Running,
    Degraded,
    Stopping,
    Failed,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct RootInsulaRecord {
    pub host_pid: u32,
    pub runtime_dir: Utf8PathBuf,
    pub rootfs_path: Utf8PathBuf,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct TmuxRecord {
    pub session_name: String,
    pub socket_path: Utf8PathBuf,
    pub namespace_pid: Option<u32>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum AttachEndpoint {
    TmuxSocket {
        socket_path: Utf8PathBuf,
        session_name: String,
    },
    LocalTcp {
        listen_addr: String,
        port: u16,
        token_path: Utf8PathBuf,
    },
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct RootfsEvidence {
    pub os_pretty_name: Option<String>,
    pub root_readlink: String,
    pub mount_namespace: String,
    pub pid_namespace: String,
    pub cgroup: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct InsulaWorkflowState {
    pub session_name: String,
    pub lifecycle: InsulaLifecycle,
    pub rootfs: RootfsProfile,
    pub runtime_dir: Utf8PathBuf,
    pub root: Option<RootInsulaRecord>,
    pub tmux: Option<TmuxRecord>,
    pub attach: Option<AttachEndpoint>,
    pub rootfs_evidence: Option<RootfsEvidence>,
    pub last_error: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn status_round_trips_through_json() {
        let state = InsulaWorkflowState {
            session_name: "insula".to_owned(),
            lifecycle: InsulaLifecycle::Running,
            rootfs: RootfsProfile {
                name: "torchtitan".to_owned(),
                rootfs_path: "/data02/home/philip.yang/workspace/torchtitan/scripts/rootfs/rootfs"
                    .into(),
                shell_path: "/bin/bash".into(),
                tmux_path: Some("/usr/bin/tmux".into()),
            },
            runtime_dir: "/run/user/1018/insula".into(),
            root: Some(RootInsulaRecord {
                host_pid: 3903967,
                runtime_dir: "/run/user/1018/insula".into(),
                rootfs_path: "/data02/home/philip.yang/workspace/torchtitan/scripts/rootfs/rootfs"
                    .into(),
            }),
            tmux: Some(TmuxRecord {
                session_name: "insula".to_owned(),
                socket_path: "/run/user/1018/insula/tmux.sock".into(),
                namespace_pid: Some(15),
            }),
            attach: Some(AttachEndpoint::LocalTcp {
                listen_addr: "127.0.0.1".to_owned(),
                port: 22222,
                token_path: "/run/user/1018/insula/token".into(),
            }),
            rootfs_evidence: Some(RootfsEvidence {
                os_pretty_name: Some("Ubuntu 24.04.4 LTS".to_owned()),
                root_readlink: "/".to_owned(),
                mount_namespace: "mnt:[4026540472]".to_owned(),
                pid_namespace: "pid:[4026540473]".to_owned(),
                cgroup: "0::/user.slice/user-1018.slice/session-28.scope".to_owned(),
            }),
            last_error: None,
        };

        let json = serde_json::to_string_pretty(&state).unwrap();
        let decoded: InsulaWorkflowState = serde_json::from_str(&json).unwrap();
        assert_eq!(decoded, state);
    }
}

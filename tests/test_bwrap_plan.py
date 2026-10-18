from vaso.bwrap import BwrapPlan, MountSpec, argv_sha256, build_bwrap_argv


def test_bwrap_argv_orders_mounts_and_environment(tmp_path):
    plan = BwrapPlan(
        run_id="run-1",
        target_repo="vaso",
        cwd="/workspace/vaso",
        uid=1018,
        gid=1018,
        rootfs=tmp_path / "rootfs",
        mounts=[
            MountSpec("vaso-runs", "bind", tmp_path / "runs", "/vaso/runs", "rw"),
            MountSpec("workspace", "bind", tmp_path / "repo", "/workspace/vaso", "rw"),
        ],
        environment={"ZED": "last", "ALPHA": "first"},
        command_argv=["/bin/true"],
    )
    argv = build_bwrap_argv(plan)
    assert argv[:3] == ["bwrap", "--die-with-parent", "--unshare-user"]
    assert argv.index("--ro-bind") < argv.index("--proc")
    assert argv.index("/workspace/vaso") < argv.index("/vaso/runs")
    alpha_i = argv.index("ALPHA")
    zed_i = argv.index("ZED")
    assert alpha_i < zed_i
    assert argv[-2:] == ["--", "/bin/true"]


def test_argv_digest_changes_with_command(tmp_path):
    base = BwrapPlan(
        run_id="run-1",
        target_repo="vaso",
        cwd="/workspace/vaso",
        uid=1018,
        gid=1018,
        rootfs=tmp_path / "rootfs",
        mounts=[],
        environment={},
        command_argv=["/bin/true"],
    )
    other = BwrapPlan(
        run_id="run-1",
        target_repo="vaso",
        cwd="/workspace/vaso",
        uid=1018,
        gid=1018,
        rootfs=tmp_path / "rootfs",
        mounts=[],
        environment={},
        command_argv=["/bin/false"],
    )
    assert argv_sha256(build_bwrap_argv(base)) != argv_sha256(build_bwrap_argv(other))


def test_offline_network_flag_is_in_static_prelude(tmp_path):
    plan = BwrapPlan(
        run_id="run-1",
        target_repo="vaso",
        cwd="/workspace/vaso",
        uid=1018,
        gid=1018,
        rootfs=tmp_path / "rootfs",
        mounts=[],
        environment={},
        command_argv=["/bin/true"],
        network_mode="offline",
    )
    argv = build_bwrap_argv(plan)
    assert "--unshare-net" in argv
    assert argv.index("--unshare-net") < argv.index("--clearenv")
    assert argv.index("--unshare-net") < argv.index("--ro-bind")

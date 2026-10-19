load("@rules_cc//cc:action_names.bzl", "ACTION_NAMES")
load(
    "@rules_cc//cc:cc_toolchain_config_lib.bzl",
    "action_config",
    "env_entry",
    "env_set",
    "feature",
    "flag_group",
    "flag_set",
    "tool",
    "tool_path",
    "variable_with_value",
)
load("//native/common:build_settings.bzl", "NativeActionStringInfo", "ReleaseCudaLineInfo")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")
load("@rules_cc//cc/toolchains:cc_toolchain_config_info.bzl", "CcToolchainConfigInfo")

_COMPILE_ACTIONS = [
    ACTION_NAMES.assemble,
    ACTION_NAMES.preprocess_assemble,
    ACTION_NAMES.c_compile,
    ACTION_NAMES.cpp_compile,
    ACTION_NAMES.linkstamp_compile,
    ACTION_NAMES.cpp_header_parsing,
    ACTION_NAMES.cpp_module_compile,
    ACTION_NAMES.cpp_module_codegen,
    ACTION_NAMES.clif_match,
    ACTION_NAMES.lto_backend,
]

_CPP_COMPILE_ACTIONS = [
    ACTION_NAMES.cpp_compile,
    ACTION_NAMES.linkstamp_compile,
    ACTION_NAMES.cpp_header_parsing,
    ACTION_NAMES.cpp_module_compile,
    ACTION_NAMES.cpp_module_codegen,
    ACTION_NAMES.clif_match,
]

_LINK_ACTIONS = [
    ACTION_NAMES.cpp_link_executable,
    ACTION_NAMES.cpp_link_dynamic_library,
    ACTION_NAMES.cpp_link_nodeps_dynamic_library,
]

def _compile_action_configs(ctx):
    return [
        action_config(
            action_name = ACTION_NAMES.c_compile,
            tools = [tool(tool = ctx.executable.cc_driver)],
        ),
        action_config(
            action_name = ACTION_NAMES.cpp_compile,
            tools = [tool(tool = ctx.executable.cxx_driver)],
        ),
        action_config(
            action_name = ACTION_NAMES.linkstamp_compile,
            tools = [tool(tool = ctx.executable.cxx_driver)],
        ),
    ]

def _link_action_configs(ctx):
    return [
        action_config(
            action_name = ACTION_NAMES.cpp_link_dynamic_library,
            tools = [tool(tool = ctx.executable.cxx_driver)],
        ),
        action_config(
            action_name = ACTION_NAMES.cpp_link_executable,
            tools = [tool(tool = ctx.executable.cxx_driver)],
        ),
        action_config(
            action_name = ACTION_NAMES.cpp_link_nodeps_dynamic_library,
            tools = [tool(tool = ctx.executable.cxx_driver)],
        ),
        action_config(
            action_name = ACTION_NAMES.cpp_link_static_library,
            tools = [tool(path = ctx.attr.ar)],
        ),
    ]

def _libraries_to_link_flag_groups():
    return [
        flag_group(
            flags = ["-Wl,-whole-archive"],
            expand_if_equal = variable_with_value(
                name = "libraries_to_link.type",
                value = "static_library",
            ),
            expand_if_true = "libraries_to_link.is_whole_archive",
        ),
        flag_group(
            flags = ["%{libraries_to_link.object_files}"],
            expand_if_equal = variable_with_value(
                name = "libraries_to_link.type",
                value = "object_file_group",
            ),
            iterate_over = "libraries_to_link.object_files",
        ),
        flag_group(
            flags = ["%{libraries_to_link.name}"],
            expand_if_equal = variable_with_value(
                name = "libraries_to_link.type",
                value = "object_file",
            ),
        ),
        flag_group(
            flags = ["%{libraries_to_link.name}"],
            expand_if_equal = variable_with_value(
                name = "libraries_to_link.type",
                value = "interface_library",
            ),
        ),
        flag_group(
            flags = ["%{libraries_to_link.name}"],
            expand_if_equal = variable_with_value(
                name = "libraries_to_link.type",
                value = "static_library",
            ),
        ),
        flag_group(
            flags = ["-l%{libraries_to_link.name}"],
            expand_if_equal = variable_with_value(
                name = "libraries_to_link.type",
                value = "dynamic_library",
            ),
        ),
        flag_group(
            flags = ["-l:%{libraries_to_link.name}"],
            expand_if_equal = variable_with_value(
                name = "libraries_to_link.type",
                value = "versioned_dynamic_library",
            ),
        ),
        flag_group(
            flags = ["-Wl,-no-whole-archive"],
            expand_if_equal = variable_with_value(
                name = "libraries_to_link.type",
                value = "static_library",
            ),
            expand_if_true = "libraries_to_link.is_whole_archive",
        ),
    ]

def _compilation_mode_features():
    return [
        feature(
            name = "dbg",
            flag_sets = [
                flag_set(
                    actions = _COMPILE_ACTIONS,
                    flag_groups = [flag_group(flags = ["-g"])],
                ),
            ],
        ),
        feature(
            name = "opt",
            flag_sets = [
                flag_set(
                    actions = _COMPILE_ACTIONS,
                    flag_groups = [flag_group(flags = ["-O2", "-DNDEBUG"])],
                ),
                flag_set(
                    actions = _LINK_ACTIONS,
                    flag_groups = [flag_group(flags = ["-Wl,-S"])],
                ),
            ],
        ),
        feature(
            name = "fastbuild",
            flag_sets = [
                flag_set(
                    actions = _COMPILE_ACTIONS,
                    flag_groups = [flag_group(flags = ["-O0"])],
                ),
            ],
        ),
    ]

def _deterministic_compile_flags(compiler):
    flags = ["-frandom-seed=%{output_file}"]
    if compiler.startswith("gcc-"):
        flags.append("-fno-canonical-system-headers")
    if compiler.startswith("clang-"):
        flags.append("-fdebug-compilation-dir=.")
    return flags

def _features(compiler, source_date_epoch):
    return [
        feature(name = "supports_pic", enabled = True),
        feature(name = "supports_dynamic_linker", enabled = True),
        feature(name = "gcc_quoting_for_param_files", enabled = True),
        feature(
            name = "dependency_file",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = _COMPILE_ACTIONS,
                    flag_groups = [
                        flag_group(
                            flags = ["-MD", "-MF", "%{dependency_file}"],
                            expand_if_available = "dependency_file",
                        ),
                    ],
                ),
            ],
        ),
        feature(
            name = "compiler_output_flags",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = _COMPILE_ACTIONS,
                    flag_groups = [
                        flag_group(
                            flags = ["-o", "%{output_file}"],
                            expand_if_available = "output_file",
                        ),
                    ],
                ),
            ],
        ),
        feature(
            name = "compiler_input_flags",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = _COMPILE_ACTIONS,
                    flag_groups = [
                        flag_group(
                            flags = ["-c", "%{source_file}"],
                            expand_if_available = "source_file",
                        ),
                    ],
                ),
            ],
        ),
    ] + _compilation_mode_features() + [
        feature(
            name = "user_compile_flags",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = _COMPILE_ACTIONS,
                    flag_groups = [
                        flag_group(
                            flags = ["%{user_compile_flags}"],
                            expand_if_available = "user_compile_flags",
                            iterate_over = "user_compile_flags",
                        ),
                    ],
                ),
            ],
        ),
        feature(
            name = "preprocessor_defines",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = _COMPILE_ACTIONS,
                    flag_groups = [
                        flag_group(
                            flags = ["-D%{preprocessor_defines}"],
                            expand_if_available = "preprocessor_defines",
                            iterate_over = "preprocessor_defines",
                        ),
                    ],
                ),
            ],
        ),
        feature(
            name = "include_paths",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = _COMPILE_ACTIONS,
                    flag_groups = [
                        flag_group(
                            flags = ["-iquote", "%{quote_include_paths}"],
                            expand_if_available = "quote_include_paths",
                            iterate_over = "quote_include_paths",
                        ),
                        flag_group(
                            flags = ["-I%{include_paths}"],
                            expand_if_available = "include_paths",
                            iterate_over = "include_paths",
                        ),
                        flag_group(
                            flags = ["-isystem", "%{system_include_paths}"],
                            expand_if_available = "system_include_paths",
                            iterate_over = "system_include_paths",
                        ),
                        flag_group(
                            flags = ["-isystem", "%{external_include_paths}"],
                            expand_if_available = "external_include_paths",
                            iterate_over = "external_include_paths",
                        ),
                    ],
                ),
            ],
        ),
        feature(
            name = "includes",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = _COMPILE_ACTIONS,
                    flag_groups = [
                        flag_group(
                            flags = ["-include", "%{includes}"],
                            expand_if_available = "includes",
                            iterate_over = "includes",
                        ),
                    ],
                ),
            ],
        ),
        feature(
            name = "pic",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = _COMPILE_ACTIONS,
                    flag_groups = [
                        flag_group(
                            flags = ["-fPIC"],
                            expand_if_available = "pic",
                        ),
                    ],
                ),
            ],
        ),
        feature(
            name = "shared_flag",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = [
                        ACTION_NAMES.cpp_link_dynamic_library,
                        ACTION_NAMES.cpp_link_nodeps_dynamic_library,
                    ],
                    flag_groups = [flag_group(flags = ["-shared"])],
                ),
            ],
        ),
        feature(
            name = "runtime_library_search_directories",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = _LINK_ACTIONS,
                    flag_groups = [
                        flag_group(
                            expand_if_available = "runtime_library_search_directories",
                            flag_groups = [
                                flag_group(
                                    flags = [
                                        "-Wl,-rpath,$ORIGIN/%{runtime_library_search_directories}",
                                    ],
                                ),
                            ],
                            iterate_over = "runtime_library_search_directories",
                        ),
                    ],
                ),
            ],
        ),
        feature(
            name = "library_search_directories",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = _LINK_ACTIONS,
                    flag_groups = [
                        flag_group(
                            flags = ["-L%{library_search_directories}"],
                            expand_if_available = "library_search_directories",
                            iterate_over = "library_search_directories",
                        ),
                    ],
                ),
            ],
        ),
        feature(
            name = "libraries_to_link",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = [
                        ACTION_NAMES.cpp_link_executable,
                        ACTION_NAMES.cpp_link_dynamic_library,
                    ],
                    flag_groups = [
                        flag_group(
                            expand_if_available = "libraries_to_link",
                            flag_groups = _libraries_to_link_flag_groups(),
                            iterate_over = "libraries_to_link",
                        ),
                    ],
                ),
                flag_set(
                    actions = [ACTION_NAMES.cpp_link_nodeps_dynamic_library],
                    flag_groups = [
                        flag_group(
                            flag_groups = _libraries_to_link_flag_groups(),
                            iterate_over = "libraries_to_link",
                        ),
                    ],
                ),
            ],
        ),
        feature(
            name = "user_link_flags",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = _LINK_ACTIONS,
                    flag_groups = [
                        flag_group(
                            flags = ["%{user_link_flags}"],
                            expand_if_available = "user_link_flags",
                            iterate_over = "user_link_flags",
                        ),
                    ],
                ),
            ],
        ),
        feature(
            name = "output_execpath_flags",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = _LINK_ACTIONS,
                    flag_groups = [
                        flag_group(
                            flags = ["-o", "%{output_execpath}"],
                            expand_if_available = "output_execpath",
                        ),
                    ],
                ),
            ],
        ),
        feature(
            name = "archiver_flags",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = [ACTION_NAMES.cpp_link_static_library],
                    flag_groups = [
                        flag_group(
                            flags = ["rcsD", "%{output_execpath}"],
                            expand_if_available = "output_execpath",
                        ),
                        flag_group(
                            expand_if_available = "libraries_to_link",
                            flag_groups = [
                                flag_group(
                                    flags = ["%{libraries_to_link.name}"],
                                    expand_if_equal = variable_with_value(
                                        name = "libraries_to_link.type",
                                        value = "object_file",
                                    ),
                                ),
                                flag_group(
                                    flags = ["%{libraries_to_link.object_files}"],
                                    expand_if_equal = variable_with_value(
                                        name = "libraries_to_link.type",
                                        value = "object_file_group",
                                    ),
                                    iterate_over = "libraries_to_link.object_files",
                                ),
                            ],
                            iterate_over = "libraries_to_link",
                        ),
                    ],
                ),
            ],
        ),
        feature(
            name = "default_link_libs",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = _LINK_ACTIONS,
                    flag_groups = [
                        flag_group(flags = ["-lstdc++", "-lm"]),
                    ],
                ),
            ],
        ),
        feature(
            name = "threading",
            enabled = True,
            flag_sets = [
                flag_set(
                    actions = _COMPILE_ACTIONS + _LINK_ACTIONS,
                    flag_groups = [
                        flag_group(flags = ["-pthread"]),
                    ],
                ),
            ],
        ),
        feature(
            name = "deterministic_compile_flags",
            enabled = True,
            env_sets = [
                env_set(
                    actions = _COMPILE_ACTIONS,
                    env_entries = [
                        env_entry(
                            key = "SOURCE_DATE_EPOCH",
                            value = source_date_epoch,
                        ),
                    ],
                ),
            ],
            flag_sets = [
                flag_set(
                    actions = _COMPILE_ACTIONS,
                    flag_groups = [
                        flag_group(
                            flags = _deterministic_compile_flags(compiler),
                            expand_if_available = "output_file",
                        ),
                    ],
                ),
            ],
        ),
    ]

def _rootfs_cc_toolchain_config_impl(ctx):
    if not ctx.attr.cc_driver or not ctx.attr.cxx_driver:
        fail("rootfs C++ toolchains require checked-in compiler-driver wrappers")
    release_line = ctx.attr._release_line[ReleaseCudaLineInfo].value
    builtin_include_directories = ctx.attr.builtin_include_directories_by_line.get(release_line)
    if builtin_include_directories == None:
        fail("missing rootfs C++ built-in include directories for {}".format(release_line))
    tools = [
        tool_path(name = "ar", path = ctx.attr.ar),
        tool_path(name = "compat-ld", path = ctx.attr.ld),
        tool_path(name = "cpp", path = ctx.attr.cpp),
        tool_path(name = "dwp", path = ctx.attr.dwp),
        tool_path(name = "gcc", path = ctx.executable.cc_driver.basename),
        tool_path(name = "gcov", path = ctx.attr.gcov),
        tool_path(name = "ld", path = ctx.executable.cxx_driver.basename),
        tool_path(name = "llvm-cov", path = ctx.attr.llvm_cov),
        tool_path(name = "llvm-profdata", path = ctx.attr.llvm_profdata),
        tool_path(name = "nm", path = ctx.attr.nm),
        tool_path(name = "objcopy", path = ctx.attr.objcopy),
        tool_path(name = "objdump", path = ctx.attr.objdump),
        tool_path(name = "strip", path = ctx.attr.strip),
    ]
    return cc_common.create_cc_toolchain_config_info(
        ctx = ctx,
        action_configs = _compile_action_configs(ctx) + _link_action_configs(ctx),
        abi_libc_version = ctx.attr.abi_libc_version,
        abi_version = ctx.attr.abi_version,
        compiler = ctx.attr.compiler,
        cxx_builtin_include_directories = builtin_include_directories,
        features = _features(ctx.attr.compiler, ctx.attr._source_date_epoch[NativeActionStringInfo].value),
        host_system_name = "local",
        target_cpu = "k8",
        target_libc = "glibc",
        target_system_name = "local",
        tool_paths = tools,
        toolchain_identifier = ctx.attr.toolchain_identifier,
    )

rootfs_cc_toolchain_config = rule(
    implementation = _rootfs_cc_toolchain_config_impl,
    attrs = {
        "abi_libc_version": attr.string(mandatory = True),
        "abi_version": attr.string(mandatory = True),
        "ar": attr.string(mandatory = True),
        "builtin_include_directories_by_line": attr.string_list_dict(default = {}),
        "cc": attr.string(mandatory = True),
        "cc_driver": attr.label(
            allow_single_file = True,
            cfg = "exec",
            executable = True,
            mandatory = True,
        ),
        "compiler": attr.string(mandatory = True),
        "cpp": attr.string(mandatory = True),
        "cxx": attr.string(mandatory = True),
        "cxx_driver": attr.label(
            allow_single_file = True,
            cfg = "exec",
            executable = True,
            mandatory = True,
        ),
        "dwp": attr.string(mandatory = True),
        "gcov": attr.string(mandatory = True),
        "ld": attr.string(mandatory = True),
        "llvm_cov": attr.string(mandatory = True),
        "llvm_profdata": attr.string(mandatory = True),
        "nm": attr.string(mandatory = True),
        "objcopy": attr.string(mandatory = True),
        "objdump": attr.string(mandatory = True),
        "strip": attr.string(mandatory = True),
        "toolchain_identifier": attr.string(mandatory = True),
        "_release_line": attr.label(default = Label("//native/common:release_line")),
        "_source_date_epoch": attr.label(default = Label("//native/common:source_date_epoch")),
    },
    provides = [CcToolchainConfigInfo],
)

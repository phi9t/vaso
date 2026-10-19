"""Rootfs C++ compiler built-in include directories.

Generated from each rootfs line with:
  $CXX -E -x c++ -v - < /dev/null
"""

ROOTFS_CC_BUILTIN_INCLUDE_EVIDENCE = {
    "cu129": {
        "clang_23_log": "agents/fc-m2c/proofs/cu129-include-probe.log",
        "gcc_13_3_log": "agents/fc-m2c/proofs/cu129-include-probe.log",
    },
    "cu130": {
        "clang_23_log": "agents/fc-m2c/proofs/cu130-include-probe.log",
        "gcc_13_3_log": "agents/fc-m2c/proofs/cu130-include-probe.log",
    },
}
ROOTFS_CC_BUILTIN_INCLUDE_DIRECTORIES_BY_LINE = {
    "cu129": {
        "gcc_13_3": [
            "/usr/include/c++/13",
            "/usr/include/x86_64-linux-gnu/c++/13",
            "/usr/include/c++/13/backward",
            "/usr/lib/gcc/x86_64-linux-gnu/13/include",
            "/usr/local/include",
            "/usr/include/x86_64-linux-gnu",
            "/usr/include",
        ],
        "clang_23": [
            "/usr/lib/gcc/x86_64-linux-gnu/13/../../../../include/c++/13",
            "/usr/lib/gcc/x86_64-linux-gnu/13/../../../../include/x86_64-linux-gnu/c++/13",
            "/usr/lib/gcc/x86_64-linux-gnu/13/../../../../include/c++/13/backward",
            "/usr/lib/llvm-23/lib/clang/23/include",
            "/usr/local/include",
            "/usr/include/x86_64-linux-gnu",
            "/usr/include",
        ],
    },
    "cu130": {
        "gcc_13_3": [
            "/usr/include/c++/13",
            "/usr/include/x86_64-linux-gnu/c++/13",
            "/usr/include/c++/13/backward",
            "/usr/lib/gcc/x86_64-linux-gnu/13/include",
            "/usr/local/include",
            "/usr/include/x86_64-linux-gnu",
            "/usr/include",
        ],
        "clang_23": [
            "/usr/lib/gcc/x86_64-linux-gnu/13/../../../../include/c++/13",
            "/usr/lib/gcc/x86_64-linux-gnu/13/../../../../include/x86_64-linux-gnu/c++/13",
            "/usr/lib/gcc/x86_64-linux-gnu/13/../../../../include/c++/13/backward",
            "/usr/lib/llvm-23/lib/clang/23/include",
            "/usr/local/include",
            "/usr/include/x86_64-linux-gnu",
            "/usr/include",
        ],
    },
}

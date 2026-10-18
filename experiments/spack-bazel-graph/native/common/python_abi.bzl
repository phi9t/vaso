"""Helpers for deriving a Python ABI from an explicit native Python prefix."""

def python_abi_from_interpreter_prefix(repository_ctx, python_prefix, dep_name):
    """Return the major.minor ABI exposed by a prefix's bin/python3."""
    python = python_prefix + "/bin/python3"
    if not repository_ctx.path(python).exists:
        fail("{} prefix must provide bin/python3".format(dep_name))
    res = repository_ctx.execute(
        [
            python,
            "-c",
            "import sys; print('%d.%d' % sys.version_info[:2])",
        ],
        timeout = 60,
        quiet = True,
        environment = {
            "PYTHONHOME": "",
            "PYTHONPATH": "",
        },
    )
    if res.return_code != 0:
        fail("could not derive {} ABI (rc={}):\n{}\n{}".format(
            dep_name,
            res.return_code,
            res.stdout,
            res.stderr,
        ))
    abi = res.stdout.strip()
    parts = abi.split(".")
    if len(parts) != 2 or not all([part.isdigit() for part in parts]):
        fail("{} prefix reported invalid Python ABI {!r}".format(dep_name, abi))
    return abi


def python_abi_from_prefix(repository_ctx, python_prefix, dep_name):
    """Return the major.minor ABI exposed by a full Python prefix."""
    abi = python_abi_from_interpreter_prefix(repository_ctx, python_prefix, dep_name)
    if not repository_ctx.path(python_prefix + "/include/python" + abi).exists:
        fail("{} prefix must provide include/python{}".format(dep_name, abi))
    return abi


def python_abi_from_site_packages_prefix(repository_ctx, python_package_prefix, dep_name):
    """Return the single major.minor ABI under a Python package prefix."""
    res = repository_ctx.execute(
        [
            "bash",
            "-c",
            """
set -euo pipefail
prefix="$1"
if [[ ! -d "$prefix/lib" ]]; then
  exit 0
fi
for site_packages in "$prefix"/lib/python*/site-packages; do
  [[ -d "$site_packages" ]] || continue
  abi="${site_packages#"$prefix"/lib/python}"
  abi="${abi%/site-packages}"
  case "$abi" in
    [0-9]*.[0-9]*) printf '%s\\n' "$abi" ;;
  esac
done | sort -u
""",
            "python_abi_from_site_packages_prefix",
            python_package_prefix,
        ],
        timeout = 60,
        quiet = True,
    )
    if res.return_code != 0:
        fail("could not derive {} ABI from site-packages (rc={}):\n{}\n{}".format(
            dep_name,
            res.return_code,
            res.stdout,
            res.stderr,
        ))
    abis = [line for line in res.stdout.splitlines() if line]
    if len(abis) != 1:
        fail("{} prefix must provide exactly one lib/pythonX.Y/site-packages tree, found {}".format(dep_name, abis))
    return abis[0]

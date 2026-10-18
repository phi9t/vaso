#!/usr/bin/env bash
set -euo pipefail

tmp="${TEST_TMPDIR:-$(mktemp -d)}"
spack="$tmp/spack"
lock_tool="$tmp/spack_to_bazel.py"
graph_tool="$tmp/build_graph.py"
lock_out="$tmp/spack_graph.lock.json"
graph_out="$tmp/py_torch_build_graph.json"
log="$tmp/calls.log"

cat > "$spack" <<'SH'
#!/usr/bin/env bash
if [[ -n "${SPACK_MISC_CACHE:-}" && -e "$SPACK_MISC_CACHE/patches/vaso_overlay-specfile_v5-index.json" ]]; then
  echo "stale vaso overlay patch index was not invalidated" >&2
  exit 46
fi
printf 'spack %s\n' "$*" >> "$CALL_LOG"
exit 0
SH
chmod +x "$spack"

cat > "$lock_tool" <<'SH'
#!/usr/bin/env bash
echo "lock tool must not run in graph-only mode" >&2
exit 44
SH
chmod +x "$lock_tool"

cat > "$graph_tool" <<'PY'
#!/usr/bin/env python3
import os
import sys

with open(os.environ["CALL_LOG"], "a", encoding="utf-8") as f:
    f.write("graph " + " ".join(sys.argv[1:]) + "\n")

try:
    out = sys.argv[sys.argv.index("--out") + 1]
except (ValueError, IndexError):
    raise SystemExit(45)

with open(out, "w", encoding="utf-8") as f:
    f.write('{"root":"py-torch","nodes":[]}\n')
PY
chmod +x "$graph_tool"

export VASO_IN_INSULA=1
export VASO_LEAN_FONT_RESOURCES=1
export CALL_LOG="$log"
export SPACK_MISC_CACHE="$tmp/spack-misc-cache"
mkdir -p "$SPACK_MISC_CACHE/patches"
touch "$SPACK_MISC_CACHE/patches/vaso_overlay-specfile_v5-index.json"
touch "$SPACK_MISC_CACHE/patches/builtin-specfile_v5-index.json"

tools/spack_lock.sh "$spack" "$lock_tool" "$graph_tool" \
  --root py-torch \
  --out "$lock_out" \
  --build-graph-out "$graph_out" \
  --graph-only \
  --graph-no-prefix \
  --native-overrides native_overrides.json \
  --reuse-if-valid \
  --timeout 600

if [[ -e "$lock_out" ]]; then
  echo "graph-only mode unexpectedly wrote lock output" >&2
  exit 1
fi
if [[ ! -s "$graph_out" ]]; then
  echo "graph-only mode did not write build graph output" >&2
  exit 1
fi
if [[ -e "$SPACK_MISC_CACHE/patches/vaso_overlay-specfile_v5-index.json" ]]; then
  echo "stale vaso overlay patch index was not removed" >&2
  exit 1
fi
if [[ ! -e "$SPACK_MISC_CACHE/patches/builtin-specfile_v5-index.json" ]]; then
  echo "non-overlay Spack patch index was removed" >&2
  exit 1
fi
if ! grep -q '^spack compiler find$' "$log"; then
  echo "spack compiler discovery did not run" >&2
  cat "$log" >&2
  exit 1
fi
if ! grep -q -- "--root py-torch \^font-util fonts:=encodings" "$log"; then
  echo "build_graph did not receive lean py-torch root" >&2
  cat "$log" >&2
  exit 1
fi
if ! grep -q -- "--no-prefix" "$log"; then
  echo "build_graph did not receive --no-prefix" >&2
  cat "$log" >&2
  exit 1
fi
if ! grep -q -- "--require-lean-font-resources" "$log"; then
  echo "build_graph did not receive lean font-resource guard" >&2
  cat "$log" >&2
  exit 1
fi
if ! grep -q -- "--native-overrides native_overrides.json" "$log"; then
  echo "build_graph did not receive native override args" >&2
  cat "$log" >&2
  exit 1
fi

: > "$log"
tools/spack_lock.sh "$spack" "$lock_tool" "$graph_tool" \
  --root 'font-util@1.4.1' \
  --out "$lock_out" \
  --build-graph-out "$graph_out" \
  --graph-only \
  --graph-no-prefix \
  --timeout 600

if ! grep -q -- "--root font-util@1.4.1 fonts:=encodings" "$log"; then
  echo "build_graph did not receive lean font-util root" >&2
  cat "$log" >&2
  exit 1
fi

if tools/spack_lock.sh "$spack" "$lock_tool" "$graph_tool" \
  --root 'py-torch ^font-util fonts=encodings,font-adobe-100dpi' \
  --out "$lock_out" \
  --build-graph-out "$graph_out" \
  --graph-only \
  --graph-no-prefix \
  --timeout 600 >"$tmp/broad.out" 2>"$tmp/broad.err"; then
  echo "spack_lock accepted an additive broad font resource spec" >&2
  exit 1
fi
if ! grep -q "refusing additive font-util resource spec" "$tmp/broad.err"; then
  echo "spack_lock broad font refusal did not explain the lean replacement" >&2
  cat "$tmp/broad.err" >&2
  exit 1
fi

cat > "$lock_tool" <<'PY'
#!/usr/bin/env python3
import os
import sys

with open(os.environ["CALL_LOG"], "a", encoding="utf-8") as f:
    f.write("lock " + " ".join(sys.argv[1:]) + "\n")
PY
chmod +x "$lock_tool"
: > "$log"

VASO_SPACK_INSTALL_ARGS="--only package" \
tools/spack_lock.sh "$spack" "$lock_tool" "$graph_tool" \
  --root 'cudnn@9.21.0.82-12 ^cuda@12.9.1' \
  --out "$lock_out" \
  --build-graph-out "$graph_out" \
  --timeout 600

if ! grep -q '^spack install --reuse --only package cudnn@9.21.0.82-12 \^cuda@12.9.1$' "$log"; then
  echo "spack install did not receive VASO_SPACK_INSTALL_ARGS before root" >&2
  cat "$log" >&2
  exit 1
fi
if ! grep -q '^lock .* --root cudnn@9.21.0.82-12 \^cuda@12.9.1 --out ' "$log"; then
  echo "lock tool did not run after package-only install" >&2
  cat "$log" >&2
  exit 1
fi
if ! grep -q -- "--require-lean-font-resources" "$log"; then
  echo "post-install build_graph did not receive lean font-resource guard" >&2
  cat "$log" >&2
  exit 1
fi

: > "$log"
tools/spack_lock.sh "$spack" "$lock_tool" "$graph_tool" \
  --fresh \
  --root 'openmpi@5.0.10' \
  --out "$lock_out" \
  --build-graph-out "$graph_out" \
  --timeout 600

if ! grep -q '^spack install --fresh openmpi@5.0.10$' "$log"; then
  echo "spack install did not switch to --fresh when --fresh is set" >&2
  cat "$log" >&2
  exit 1
fi
if ! grep -q '^lock .* --fresh --root openmpi@5.0.10 --out ' "$log"; then
  echo "lock tool did not receive --fresh when --fresh is set" >&2
  cat "$log" >&2
  exit 1
fi
if ! grep -q '^graph .* --fresh --root openmpi@5.0.10 ' "$log"; then
  echo "build_graph did not receive --fresh when --fresh is set" >&2
  cat "$log" >&2
  exit 1
fi

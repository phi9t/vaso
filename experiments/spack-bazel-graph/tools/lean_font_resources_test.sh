#!/usr/bin/env bash
set -euo pipefail

run_sh="$(rlocation "_main/run.sh" 2>/dev/null || true)"
if [[ -z "$run_sh" ]]; then
  run_sh="$PWD/run.sh"
fi

py_torch_out="$("$run_sh" --normalize-root-for-test 'py-torch cuda_arch=80,90,100')"
if [[ "$py_torch_out" != 'py-torch cuda_arch=80,90,100 ^font-util fonts:=encodings' ]]; then
  echo "py-torch root was not constrained to lean font resources" >&2
  echo "$py_torch_out" >&2
  exit 1
fi

font_util_out="$("$run_sh" --normalize-root-for-test 'font-util@1.4.1')"
if [[ "$font_util_out" != 'font-util@1.4.1 fonts:=encodings' ]]; then
  echo "font-util root was not constrained to lean font resources" >&2
  echo "$font_util_out" >&2
  exit 1
fi

explicit_out="$("$run_sh" --normalize-root-for-test 'py-torch ^font-util fonts:=encodings')"
if [[ "$explicit_out" != 'py-torch ^font-util fonts:=encodings' ]]; then
  echo "explicit lean replacement should be preserved" >&2
  echo "$explicit_out" >&2
  exit 1
fi

legacy_disable_out="$(VASO_LEAN_FONT_RESOURCES=0 "$run_sh" --normalize-root-for-test 'py-torch cuda_arch=80,90,100')"
if [[ "$legacy_disable_out" != 'py-torch cuda_arch=80,90,100 ^font-util fonts:=encodings' ]]; then
  echo "VASO_LEAN_FONT_RESOURCES=0 must not silently widen py-torch font resources" >&2
  echo "$legacy_disable_out" >&2
  exit 1
fi

if "$run_sh" --normalize-root-for-test 'py-torch ^font-util fonts=encodings,font-adobe-100dpi' >"$TEST_TMPDIR/broad.out" 2>"$TEST_TMPDIR/broad.err"; then
  echo "additive broad font resource spec was accepted" >&2
  exit 1
fi
if ! grep -q "refusing additive font-util resource spec" "$TEST_TMPDIR/broad.err"; then
  echo "broad font refusal did not explain the lean replacement" >&2
  cat "$TEST_TMPDIR/broad.err" >&2
  exit 1
fi

if "$run_sh" --normalize-root-for-test 'py-torch ^font-util fonts:=encodings,font-adobe-100dpi' >"$TEST_TMPDIR/broad-replacement.out" 2>"$TEST_TMPDIR/broad-replacement.err"; then
  echo "broad replacement font resource spec was accepted" >&2
  exit 1
fi
if ! grep -q "refusing broad font-util resource spec" "$TEST_TMPDIR/broad-replacement.err"; then
  echo "broad replacement refusal did not explain the lean replacement" >&2
  cat "$TEST_TMPDIR/broad-replacement.err" >&2
  exit 1
fi

allowed_out="$(VASO_ALLOW_BROAD_FONT_RESOURCES=1 "$run_sh" --normalize-root-for-test 'py-torch ^font-util fonts:=encodings,font-adobe-100dpi')"
if [[ "$allowed_out" != 'py-torch ^font-util fonts:=encodings,font-adobe-100dpi' ]]; then
  echo "explicitly allowed broad font resource spec was not preserved" >&2
  echo "$allowed_out" >&2
  exit 1
fi

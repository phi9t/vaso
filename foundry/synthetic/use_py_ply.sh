#!/usr/bin/env bash
set -euo pipefail

find_runfile_by_pattern() {
  local pattern="$1"
  local base hit manifest_key manifest_value
  if [[ -n "${RUNFILES_MANIFEST_FILE:-}" && -f "$RUNFILES_MANIFEST_FILE" ]]; then
    while IFS= read -r line; do
      manifest_key="${line%% *}"
      manifest_value="${line#* }"
      [[ "$manifest_key" == "$line" ]] && manifest_value="$manifest_key"
      case "$manifest_key" in
        $pattern)
          if [[ -e "$manifest_value" || -L "$manifest_value" ]]; then
            echo "$manifest_value"
            return 0
          fi
          ;;
      esac
    done < "$RUNFILES_MANIFEST_FILE"
  fi
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -path "$pattern" -print -quit 2>/dev/null)"
    if [[ -n "$hit" && ( -e "$hit" || -L "$hit" ) ]]; then
      echo "$hit"
      return 0
    fi
  done
  return 1
}

find_prefix() {
  local marker="$1"
  local hit
  hit="$(find_runfile_by_pattern "*/+${marker}+${marker}/prefix_path.txt" || true)"
  if [[ -z "$hit" ]]; then
    hit="$(find_runfile_by_pattern "*${marker}*/prefix_path.txt" || true)"
  fi
  if [[ -z "$hit" ]]; then
    hit="$(find_runfile_by_pattern "*/${marker}/prefix_path.txt" || true)"
  fi
  if [[ -n "$hit" && -r "$hit" ]]; then
    hit="$(tr -d '\n' < "$hit")"
    if [[ -d "$hit" ]]; then
      echo "$hit"
      return 0
    fi
  fi
  hit="$(find_runfile_by_pattern "*/+${marker}+${marker}/prefix" || true)"
  if [[ -z "$hit" ]]; then
    hit="$(find_runfile_by_pattern "*${marker}*/prefix" || true)"
  fi
  if [[ -z "$hit" ]]; then
    hit="$(find_runfile_by_pattern "*/${marker}/prefix" || true)"
  fi
  if [[ -n "$hit" && -d "$hit" ]]; then
    echo "$hit"
    return 0
  fi
  return 1
}

require_prefix() {
  local marker="$1"
  local hit
  hit="$(find_prefix "$marker" || true)"
  [[ -n "$hit" && -d "$hit" ]] || { echo "missing prefix discovery for $marker" >&2; exit 1; }
  echo "$hit"
}

prefix="$(require_prefix py_ply_native)"
python_prefix="$(require_prefix python_313_native)"
venv_prefix="$(require_prefix python_venv_native)"
pip_prefix="$(require_prefix py_pip_native)"
setuptools_prefix="$(require_prefix py_setuptools_82_native)"
wheel_prefix="$(require_prefix py_wheel_native)"

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }

site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -d "$site_packages/ply" ]] || { echo "missing ply package" >&2; exit 1; }
[[ -f "$site_packages/ply/__init__.py" ]] || { echo "missing ply __init__" >&2; exit 1; }
[[ -f "$site_packages/ply/lex.py" ]] || { echo "missing ply lex" >&2; exit 1; }
[[ -f "$site_packages/ply/yacc.py" ]] || { echo "missing ply yacc" >&2; exit 1; }
[[ -f "$site_packages/ply-3.11.dist-info/METADATA" ]] || { echo "missing ply metadata" >&2; exit 1; }
[[ -f "$site_packages/ply-3.11.dist-info/WHEEL" ]] || { echo "missing ply wheel metadata" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$pip_prefix/lib/python${python_abi}/site-packages:$setuptools_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata

from ply import lex, yacc


class Grammar:
    tokens = ("NUMBER", "PLUS")
    t_PLUS = r"\+"
    t_ignore = " \t"

    def t_NUMBER(self, token):
        r"\d+"
        token.value = int(token.value)
        return token

    def t_error(self, token):
        raise SyntaxError(token.value[0])

    def p_expr_plus(self, p):
        "expr : expr PLUS term"
        p[0] = p[1] + p[3]

    def p_expr_term(self, p):
        "expr : term"
        p[0] = p[1]

    def p_term_number(self, p):
        "term : NUMBER"
        p[0] = p[1]

    def p_error(self, p):
        raise SyntaxError(p.value if p is not None else "eof")


grammar = Grammar()
lexer = lex.lex(module=grammar, optimize=False, debug=False)
parser = yacc.yacc(module=grammar, write_tables=False, debug=False)
print("py-ply:%s:%s" % (importlib.metadata.version("ply"), parser.parse("3 + 4", lexer=lexer)))
PY
)"
[[ "$out" == "py-ply:3.11:7" ]] || {
  echo "unexpected parser output: $out" >&2
  exit 1
}

echo "$out"

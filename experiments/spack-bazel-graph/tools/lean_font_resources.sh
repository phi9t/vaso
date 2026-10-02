#!/usr/bin/env bash
# Shared font-resource policy for hermetic Spack entrypoints.

normalize_lean_font_resources() {
  local root_spec="$1"
  local announce="${2:-0}"

  if [[ "${VASO_LEAN_FONT_RESOURCES:-1}" != "1" && "${VASO_ALLOW_BROAD_FONT_RESOURCES:-0}" == "1" ]]; then
    printf '%s\n' "$root_spec"
    return 0
  fi

  local root_token="${root_spec%% *}"
  local root_pkg="${root_token%%@*}"
  local font_token=""
  local token
  for token in $root_spec; do
    case "$token" in
      fonts:=*|fonts=*)
        font_token="$token"
        ;;
    esac
  done

  reject_nonlean_font_token() {
    local replacement="$1"
    if [[ -z "$font_token" || "${VASO_ALLOW_BROAD_FONT_RESOURCES:-0}" == "1" ]]; then
      return 0
    fi
    if [[ "$font_token" == "fonts:=encodings" ]]; then
      return 0
    fi
    local kind="broad"
    if [[ "$font_token" == fonts=* ]]; then
      kind="additive"
    fi
    cat >&2 <<EOF
refusing $kind font-util resource spec in SPACK_ROOT_PKG:
  $root_spec

Use the lean replacement constraint:
  $replacement

Set VASO_ALLOW_BROAD_FONT_RESOURCES=1 only for intentional broad-font probes.
EOF
    return 2
  }

  case "$root_pkg" in
    py-torch)
      reject_nonlean_font_token "^font-util fonts:=encodings" || return $?
      if [[ -z "$font_token" ]]; then
        root_spec="$root_spec ^font-util fonts:=encodings"
        if [[ "$announce" == "1" ]]; then
          echo "font resources: constrained to lean ^font-util fonts:=encodings" >&2
        fi
      fi
      ;;
    font-util)
      reject_nonlean_font_token "font-util@1.4.1 fonts:=encodings" || return $?
      if [[ -z "$font_token" ]]; then
        root_spec="$root_spec fonts:=encodings"
        if [[ "$announce" == "1" ]]; then
          echo "font resources: constrained to lean font-util fonts:=encodings" >&2
        fi
      fi
      ;;
  esac
  printf '%s\n' "$root_spec"
}

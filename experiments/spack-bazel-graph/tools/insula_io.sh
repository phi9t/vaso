#!/usr/bin/env bash
# Shared insula I/O placement helpers. The caller must set VASO_HOST to the
# host-side estate's writable /vaso subtree before calling init_insula_io.

init_insula_io() {
  local vaso_host="${1:?usage: init_insula_io VASO_HOST}"
  local run_id="${VASO_INSULA_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
  if [[ ! "$run_id" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "invalid VASO_INSULA_RUN_ID=$run_id; use only [A-Za-z0-9._-]" >&2
    exit 2
  fi
  INSULA_RUN_ID="$run_id"
  INSULA_TMP_HOST="$vaso_host/tmp/$INSULA_RUN_ID"
  INSULA_TMP_SB="/vaso/tmp/$INSULA_RUN_ID"
  INSULA_TMPFS_SIZE="${VASO_INSULA_TMPFS_SIZE:-4294967296}"
  mkdir -p "$INSULA_TMP_HOST"
  export INSULA_RUN_ID INSULA_TMP_HOST INSULA_TMP_SB INSULA_TMPFS_SIZE
}

cleanup_insula_io() {
  if [[ "${VASO_KEEP_INSULA_TMP:-0}" == "1" ]]; then
    return 0
  fi
  [[ -n "${INSULA_TMP_HOST:-}" && -n "${VASO_HOST:-}" ]] || return 0
  case "$INSULA_TMP_HOST" in
    "$VASO_HOST/tmp/"*) rm -rf -- "$INSULA_TMP_HOST" ;;
    *) echo "refusing to clean unexpected insula tmp path: $INSULA_TMP_HOST" >&2; return 1 ;;
  esac
}

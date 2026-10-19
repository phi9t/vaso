#!/usr/bin/env bash
set -euo pipefail

exec "${LAB_CC:-gcc}" "$@"

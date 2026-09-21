# Implement the Vaso reference rootfs lifecycle

Type: task
Status: ready-for-agent
Blocked by: 02
Parent: ../spec.md

## Requirements

- Implement pinned materialization, content-addressed storage, verified
  selection, deterministic bwrap planning, execution, and sanitized receipts.
- Keep writable state repo-local and ignored.
- Use Docker or BuildKit only as a materializer; run reference behavior through
  unprivileged bwrap.

## Verification

- Every behavior slice records visible red-green evidence.
- Focused unit and integration tests cover success and fail-closed paths.

## Comments

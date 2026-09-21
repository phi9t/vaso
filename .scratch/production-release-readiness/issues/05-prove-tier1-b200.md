# Prove Vaso Tier-1 B200 execution

Type: task
Status: ready-for-agent
Blocked by: 03, 04, 07
Parent: ../spec.md

## Requirements

- Execute the exact human-approved Tier-1 B200 contract from ticket 07 inside
  the selected rootfs without changing its inputs or thresholds.
- Prove CUDA/driver projection, isolation, result correctness, and sanitized
  receipt verification against the exact commit and rootfs digest.
- Document B200 as the sole qualified GPU profile.

## Verification

- A signed Tier-1 receipt verifies from a clean checkout against the federation
  trust root and schema.
- Failure injection proves incompatible or missing GPU state fails closed.

## Comments

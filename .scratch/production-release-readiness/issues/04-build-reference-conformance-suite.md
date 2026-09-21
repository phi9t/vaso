# Build the Vaso v1 conformance suite

Type: task
Status: ready-for-agent
Blocked by: 02, 03
Parent: ../spec.md

## Requirements

- Provide public schemas, fixtures, negative cases, and an executable reference
  conformance command.
- Cover clean bootstrap, repeatable identity, tampering, mounts, environment,
  credentials, network declaration, device/driver compatibility, and receipts.
- Ensure downstream repositories can reimplement behavior without importing
  Vaso code.

## Verification

- The suite rejects deliberately nonconforming reference variants.
- A clean Vaso checkout passes without Ultron or another repository.

## Comments

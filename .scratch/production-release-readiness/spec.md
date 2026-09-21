# Vaso Production Release Readiness

Status: approved
Path: gated
Parent: ultron:.scratch/production-release-readiness/issues/02-qualify-vaso-v1.md

## Outcome

Release Vaso under the MIT License as the public specification, executable
reference implementation, and template for repository-local bwrap rootfs
systems. Vaso v1 must materialize a real rootfs, execute through unprivileged
bwrap, and produce a verified B200 Tier-1 receipt.

## Current state

Vaso has planning, doctor, record, and Tier-0 validation surfaces, but its
design is draft, it does not materialize or enter a rootfs, and Tier-1 is an
intentional failure. It has no license, CI, or configured GitHub remote. Its
documentation contains at least one machine-specific source reference that
must not enter the publication closure.

## Public ownership

Vaso owns the normative public design, schema, lifecycle, threat model,
reference implementation, fixtures, and conformance behavior. Downstream
repositories pin a Vaso specification version and digest, then implement their
own code. Vaso exposes no required runtime service, package dependency,
submodule, CI callback, or network dependency.

## Rootfs contract

The reference must materialize from immutable inputs into a repo-local
content-addressed store; verify recipe and content identity; select only a
complete rootfs; build a deterministic bwrap plan; clear and allowlist the
environment; expose explicit writable state; project compatible B200 devices
and driver libraries; declare network mode; and emit a sanitized receipt.

Docker or BuildKit may materialize the filesystem. Build, tests, reference GPU
execution, and validation run through bwrap without a Docker daemon.

## Test seam

The public seams are the materialization plan/result, selected-rootfs manifest,
bwrap argument plan, execution result, and release receipt. Each behavior slice
must first fail at one of these seams, then pass after implementation.

## B200 gate

Tier-1 must enter the selected rootfs, validate the projected CUDA/driver
contract, run a deterministic B200 computation, and verify its output and
receipt. CPU-only simulation cannot satisfy the production gate.

Before execution, a human approves the exact command, immutable input fixture,
GPU count/topology, repetitions or duration, correctness/tolerance thresholds,
any performance claim, negative cases, timeout/resource budget, receipt schema,
and signing verification. The Tier-1 receipt is signed and binds this contract
to the exact commit and rootfs identity.

## Publication gate

The exact `ultron/mainline` candidate must pass license/provenance review,
checksum-pinned secret and entropy scanning, custom PII/machine/proprietary
rules, binary inspection, and human review. `origin` must target `phi9t/vaso`;
any original-source remote is fetch-only. Publication actions remain human
authorized.

## Acceptance criteria

- An approved MIT license and notices cover Vaso's original material.
- The Vaso v1 specification and schemas have stable versions and digests.
- A clean checkout materializes and runs the rootfs using the public commands.
- Tampered inputs, incomplete stores, unsafe mounts, unexpected environment,
  incompatible driver state, and invalid receipts fail closed.
- The human-approved exact Tier-1 B200 contract passes with an independently
  verified sanitized signed receipt.
- Tests and independent review have no unresolved blocking findings.
- The reconstructed candidate has no unresolved publication finding.
- No downstream repository dependency is required to use or test Vaso.

## Out of scope

- Providing downstream repository implementations.
- Publishing rootfs archives, packages, containers, or model artifacts.
- Qualifying non-B200 accelerators.
- Releasing or depending on Ultron.

## Authority

This spec authorizes approved local file changes and tests in isolated
repo-local worktrees. It does not authorize commits, remotes, pushes, tags,
releases, visibility changes, deletion, or merges.

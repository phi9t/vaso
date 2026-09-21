# Vaso README Landing Page

Status: completed
Path: short

## Outcome

Turn Vaso's README into an accurate GitHub landing page for engineers building
PyTorch and GPU-ML development environments with Bubblewrap root filesystems.

## Acceptance criteria

- Explain Vaso's role as a specification and reference implementation without
  making downstream repositories depend on it at runtime.
- Put PyTorch development, CUDA/B200 qualification, compiled extensions,
  distributed execution, caches, datasets, checkpoints, and multi-repository
  workflows in the foreground.
- Separate capabilities implemented today from the Vaso v1 target contract.
- Provide a dependency-free quick start whose commands run from a clean
  checkout and identify the repo-local evidence it creates.
- Link to the detailed design and keep security and isolation claims bounded by
  the current implementation status.
- Distill the already-approved self-contained downstream contract and
  B200-first qualification target into the public rootfs design so the README
  does not become a competing source of architecture.
- Contain no machine identity, private path, credential, proprietary detail, or
  claim of production readiness.

## Authority

The user approved this README design and authorized local implementation and
verification. Commit, push, pull-request, and merge actions are not authorized.

## Verification

- Final worktree: `.worktrees/readme-landing-page`
- Branch: `codex/readme-landing-page`
- Base: `e95e0a4e4d426d9a27783337d9bac7857284e198`
- `python -m pytest -q -p no:cacheprovider`: 24 passed.
- Both documented quick-start commands exited zero; the host preflight produced
  the documented evidence bundle.
- `git diff --check`: clean.
- Gitleaks 8.30.1 directory scan: no leaks found.
- Custom private-path, identity, credential, and IP scan: no matches in the
  changed documents.
- Independent Standards review: pass, no Critical, Important, or Minor
  findings after two bounded repair rounds.
- Independent Spec review: pass, no Critical, Important, or Minor findings.
- Only `README.md` and `docs/rootfs-container-infra-design.md` changed.
- Sanitized feature commit:
  `de7d84dd69393bd47e2b9e2b900b40fbfe2afd1a`.
- GitHub PR: `https://github.com/phi9t/vaso/pull/1`.
- PR state: merged into `ultron/mainline` at
  `85a74d116dcf3e4675914fcd516faf177a6594ab`.
- Local and remote `ultron/mainline` refs match the merged commit; the reviewed
  feature tree and merge tree have identical tree object
  `0c2f8cef98a67c39861b6fc1b8fadc652823d7fb`.

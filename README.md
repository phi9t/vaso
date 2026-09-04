# Vaso

Vaso is an experimental Bubblewrap-native rootfs substrate for reproducible
development workflows. It builds deterministic sandbox plans, validates their
writable surfaces, and records execution evidence without making Docker a
runtime dependency.

The current tree is an early implementation slice. It is suitable for design
and Tier 0 host-side validation; it is not yet a production-qualified release.

## Development

Vaso requires Python 3.11 or newer. Run its dependency-free test suite with:

```bash
python -m pytest -q -p no:cacheprovider
```

Read `CONSTITUTION.md` and `docs/agents/agentic-engineering.md` before changing
code. The rootfs design and its current boundaries are documented in
`docs/rootfs-container-infra-design.md`.

Vaso is licensed under the MIT License. See `LICENSE`.

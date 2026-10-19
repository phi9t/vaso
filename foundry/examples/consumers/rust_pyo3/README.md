# Rust PyO3 Consumer

This consumer builds a minimal PyO3 wheel with the estate Rust toolchain.

## Vendored crates

The normal build phase runs offline inside the insula: `cargo build --locked
--offline` uses the vendored sources in the `rust_pyo3_cargo` module under the repository's
`third_party/` directory, and downloads are disabled. The crates therefore stay vendored. De-vendoring would first need
a sha256-pinned prefetch step that materializes the exact crate sources before
the offline build runs.

# Tooling readiness without installed-tree scans

Foundry tooling manifest v2 is a readiness receipt, not an installed-content
integrity proof. It records exact lock text and the pinned package receipts.
Acquisition still checks downloaded package/archive SHA256.

On construction and reuse, only explicitly selected executable paths and their
small compatibility wrappers are checked for regular-file type and size.
Wheel installation directories are checked for availability. No installed-tree
walk, per-file inventory, per-installed-file digest, or full eager stat scan is
performed. This deliberately does not detect same-size edits or missing
individual Python modules. Real imports/builds provide the functional test.

Old v1 manifests are rejected with instructions to provision a fresh named
rootfs. Do not upgrade a running rootfs by rewriting its receipt, and do not
touch active build workspaces. The v2 receipt alone does not prove rootfs
immutability or authenticity.

Preparation likewise no longer invokes the transitive static ecosystem audit.
It compares declared component versions, resolves the selected shell, checks
selected tooling and (on reuse) compares the preparation selection. Its output
is ecosystem-readiness.json, explicitly not a GPU or full rootfs qualification.
The standalone ecosystem verifier remains a deferred, explicit audit only.

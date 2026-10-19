# NVSHMEM EP Consumer Provenance

This example implementation was written for the vaso FC-U4 ticket.

- License: MIT for the files in `examples/consumers/nvshmem_ep`.
- Third-party source copied: none.
- External APIs used: PyTorch C++/Python extension APIs, CUDA runtime APIs, and the rootfs NVSHMEM SDK headers and libraries.
- Scope: intranode expert-parallel dispatch/combine proof using device-initiated NVSHMEM puts and torch.distributed unique-id bootstrap.

"""Native cross-GPU regression; run only inside an admitted two-GPU capsule."""
from __future__ import annotations

import gc
import json
from pathlib import Path
import sys

print("transport: importing torch", flush=True)
import torch
print("transport: importing packed store", flush=True)
from packed_checkpoint import PackedMatrix, PackedWeightStore


def exact(actual, expected):
    assert actual.dtype == expected.dtype and actual.shape == expected.shape
    assert torch.equal(actual.contiguous().view(torch.uint8).cpu(),
                       expected.contiguous().view(torch.uint8).cpu())


def matrix(layer, projection, fmt):
    # Distinct bytes for every bank and expert, plus nontrivial scale encodings.
    codes = (torch.arange(3 * 4 * 64, dtype=torch.int32) + layer * 7
             + (projection == "w2") * 13).to(torch.uint8).reshape(3, 4, 64)
    scales = (torch.arange(3 * 4 * 4, dtype=torch.int32) + layer * 11
              ).to(torch.uint8).reshape(3, 4, 4)
    outer = torch.tensor([1.25, -0.0, 0.75], dtype=torch.float32) if fmt == "nvfp4" else None
    return PackedMatrix(layer, projection, fmt, codes, scales, outer, 128, 4,
                        codes.numel() + scales.numel() + (12 if outer is not None else 0))


def run_role(compute, archive):
    print(f"transport: role {compute} <- {archive}", flush=True)
    banks = {(layer, projection): matrix(layer, projection, fmt)
             for layer, fmt in enumerate(("nvfp4", "mxfp8"))
             for projection in ("w13", "w2")}
    # Keep one expert, and archive both formats. All banks are deliberately tiny.
    store = PackedWeightStore(max(m.expert_bytes for m in banks.values()))
    store.matrices.update(banks)
    checks = 0
    try:
        report = store.prepare_archive(archive, compute_device=compute,
                                       budget_bytes=sum(m.raw_bytes for m in banks.values()))
        assert report["enabled"] and "host bounce" in report["copy_mode"]
        for key, bank in store._archive_banks.items():
            for field in ("weight", "scales", "outer_scale"):
                expected = getattr(banks[key], field)
                if expected is None:
                    assert getattr(bank, field) is None
                else:
                    exact(getattr(bank, field), expected)
        with torch.inference_mode():
            for _ in range(3):
                for key, bank in banks.items():
                    for expert in (0, 2, 1, 0):
                        result = store.get(*key, expert, compute)
                        for field in ("weight", "scales", "outer_scale"):
                            expected = getattr(bank, field)
                            if expected is None:
                                assert getattr(result, field) is None
                            else:
                                actual = getattr(result, field)
                                exact(actual, expected[expert:expert + 1])
                                checks += 1
                        # The cache entry must not alias its archive.
                        result.weight.reshape(-1)[0].bitwise_xor_(1)
                        exact(store._archive_banks[key].weight, bank.weight)
                        del result
                        # The next different expert must evict this mutated copy.
                        # At the end of each pass reset only the LRU, not archive.
                    store.clear_cache()
            assert store._devices[str(compute)]["evictions"] > 0
            # A failed later field cannot publish a partially copied cache entry.
            key = (0, "w13")
            original = store._copy_archive_tensor
            calls = 0
            def injected(source, device):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("injected scale-copy failure")
                return original(source, device)
            # Avoid Mock retaining tensor arguments and exception tracebacks
            # beyond the lifetime of the store.
            store._copy_archive_tensor = injected
            try:
                try:
                    store.get(*key, 0, compute)
                except RuntimeError as error:
                    assert str(error) == "injected scale-copy failure"
                else:
                    raise AssertionError("injected copy failure was not propagated")
            finally:
                del store._copy_archive_tensor
            assert not store._caches[str(compute)]
            assert store._devices[str(compute)]["resident_bytes"] == 0
            exact(store.get(*key, 0, compute).weight, banks[key].weight[:1])
        # Native transfer sizes from the prior transport investigation.
        for size in (9216, 1048576, 16777216):
            source = torch.arange(size, dtype=torch.int32).to(torch.uint8)
            device_source = source.to(archive)
            exact(store._copy_archive_tensor(device_source, compute), source)
            checks += 1
        source = torch.arange(256, dtype=torch.float32).reshape(16, 16).T.to(archive)
        exact(store._copy_archive_tensor(source, compute), source.cpu())
        checks += 1
        print(f"transport: role {compute} complete, checks={checks}", flush=True)
        return {"compute": str(compute), "archive": str(archive), "byte_checks": checks,
                "failed_refill_not_admitted": True, "copy_mode": report["copy_mode"]}
    finally:
        store.close()
        assert not store._archive_banks and not store._caches
        assert all(v["resident_bytes"] == 0 for v in store._devices.values())


def main():
    print("transport: imports complete", flush=True)
    torch.set_num_threads(2)
    assert torch.cuda.device_count() == 2
    assert all(torch.cuda.get_device_capability(i) == (6, 1) for i in (0, 1))
    baseline = [torch.cuda.memory_allocated(i) for i in (0, 1)]
    rows = [run_role(torch.device(f"cuda:{i}"), torch.device(f"cuda:{1-i}")) for i in (0, 1)]
    gc.collect()
    final = []
    for i in (0, 1):
        with torch.cuda.device(i):
            torch.cuda.synchronize(i)
            torch.cuda.empty_cache()
            final.append(torch.cuda.memory_allocated(i))
    print(f"transport: allocator baseline={baseline}, final={final}", flush=True)
    assert final == baseline
    receipt = {"passed": True, "torch": torch.__version__, "roles": rows,
               "allocator_baseline_bytes": baseline, "allocator_final_bytes": final,
               "device_allocations_released": True}
    Path(sys.argv[1]).write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()

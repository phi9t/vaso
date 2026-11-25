"""Exercise real SM61 kernels on both admitted NVIDIA devices."""
import argparse
import json
import pathlib
import torch
import triton
import triton.language as tl


@triton.jit
def add(a, b, c, N: tl.constexpr, B: tl.constexpr):
    i = tl.program_id(0) * B + tl.arange(0, B)
    tl.store(c + i, tl.load(a + i, i < N, 0) + tl.load(b + i, i < N, 0), i < N)


@triton.jit
def rms(x, w, y, N: tl.constexpr, B: tl.constexpr):
    r = tl.program_id(0)
    i = tl.arange(0, B)
    a = tl.load(x + r * N + i, i < N, 0).to(tl.float32)
    weight = tl.load(w + i, i < N, 0).to(tl.float32)
    scale = tl.rsqrt(tl.sum(a * a, 0) / N + 1e-6)
    tl.store(y + r * N + i, a * scale * weight, i < N)


@triton.jit
def dot(a, b, c, out, M: tl.constexpr, N: tl.constexpr, K: tl.constexpr, INT: tl.constexpr):
    m, n, k = tl.arange(0, M), tl.arange(0, N), tl.arange(0, K)
    x = tl.load(a + m[:, None] * K + k[None, :])
    y = tl.load(b + k[:, None] * N + n[None, :])
    acc = tl.load(c + m[:, None] * N + n[None, :])
    if INT:
        z = tl.dot(x, y, acc, out_dtype=tl.int32)
    else:
        z = tl.dot(x, y, acc, input_precision="ieee")
    tl.store(out + m[:, None] * N + n[None, :], z)


@triton.jit
def dp4a(a, b, out, N: tl.constexpr):
    i = tl.arange(0, N)
    x, y = tl.load(a + i), tl.load(b + i)
    z = tl.inline_asm_elementwise("dp4a.s32.s32 $0, $1, $2, 0;",
                                 constraints="=r,r,r", args=[x, y],
                                 dtype=tl.int32, is_pure=True, pack=1)
    tl.store(out + i, z)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--install", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--control", action="store_true")
    args = p.parse_args()
    print("Loaded Torch and Triton", torch.__version__, triton.__version__, flush=True)
    torch.set_num_threads(1)
    assert torch.__version__ == "2.15.0+vaso.cu126.sm61"
    assert triton.__version__ == "3.8.0"
    assert pathlib.Path(triton.__file__).resolve().is_relative_to(pathlib.Path(args.install).resolve())
    assert torch.cuda.device_count() == 2
    assert "sm_61" in torch.cuda.get_arch_list()
    results = []
    for gpu in range(2):
        print("Qualifying GPU", gpu, flush=True)
        torch.cuda.set_device(gpu)
        assert torch.cuda.get_device_capability(gpu) == (6, 1)
        assert triton.runtime.driver.active.get_current_target().arch == 61
        d = torch.device("cuda", gpu)
        gen = torch.Generator().manual_seed(123)

        def check(name, kernel, out, ref, exact=False):
            torch.cuda.synchronize(d)
            tol = 0 if exact else 1e-4
            torch.testing.assert_close(out.cpu(), ref.cpu(), atol=tol, rtol=tol)
            assert ".target sm_61" in kernel.asm["ptx"]
            record = {"case": name, "gpu": gpu, "passed": True,
                      "max_error": (out.cpu() - ref.cpu()).abs().max().item()}
            results.append(record)
            print(json.dumps(record), flush=True)

        a = torch.randn(1003, generator=gen)
        b = torch.randn(1003, generator=gen)
        z = torch.empty_like(a, device=d)
        k = add[(triton.cdiv(1003, 256),)](a.to(d), b.to(d), z, 1003, 256)
        check("masked_add", k, z, a + b)
        x = torch.randn((8, 2304), generator=gen)
        w = torch.randn(2304, generator=gen)
        z = torch.empty_like(x, device=d)
        k = rms[(8,)](x.to(d), w.to(d), z, 2304, 4096, num_warps=4)
        check("rmsnorm", k, z, x * torch.rsqrt(x.square().mean(-1, keepdim=True) + 1e-6) * w)
        a = torch.randn((16, 16), generator=gen)
        b = torch.randn((16, 16), generator=gen)
        c = torch.randn((16, 16), generator=gen)
        z = torch.empty_like(c, device=d)
        k = dot[(1,)](a.to(d), b.to(d), c.to(d), z, 16, 16, 16, False)
        check("fp32_dot", k, z, a @ b + c)
        a = torch.randint(-128, 128, (128, 4), dtype=torch.int32, generator=gen)
        b = torch.randint(-128, 128, (128, 4), dtype=torch.int32, generator=gen)
        shifts = torch.tensor([0, 8, 16, 24])
        ap = ((a & 255) << shifts).sum(-1).int()
        bp = ((b & 255) << shifts).sum(-1).int()
        z = torch.empty(128, device=d, dtype=torch.int32)
        k = dp4a[(1,)](ap.to(d), bp.to(d), z, 128)
        assert "dp4a.s32.s32" in k.asm["ptx"]
        check("inline_dp4a", k, z, (a * b).sum(-1).int(), True)
        if not args.control:
            for m, n, kdim in [(32, 32, 32), (32, 64, 64)]:
                for warps in [4, 8]:
                    a = torch.randint(-128, 128, (m, kdim), dtype=torch.int8, generator=gen)
                    b = torch.randint(-128, 128, (kdim, n), dtype=torch.int8, generator=gen)
                    c = torch.randint(-4096, 4096, (m, n), dtype=torch.int32, generator=gen)
                    z = torch.empty_like(c, device=d)
                    k = dot[(1,)](a.to(d), b.to(d), c.to(d), z, m, n, kdim, True, num_warps=warps)
                    check(f"int8_dot_{m}_{n}_{kdim}_{warps}", k, z, a.int() @ b.int() + c, True)
    pathlib.Path(args.output).write_text(json.dumps({"passed": True, "control_only": args.control,
        "torch": torch.__version__, "triton": triton.__version__, "results": results}, indent=2) + "\n")


if __name__ == "__main__":
    main()

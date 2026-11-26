#!/usr/bin/env python3
"""Qualify packed expert operators inside the standalone Pascal capsule.

CPU and CUDA FP32 references deliberately dequantize small/synthetic expert
matrices for comparison. The operator under test does not. Production-size
cases use eight experts, N=2304, and K=2304/1152 with arbitrary FP32 inputs
and nonunit outer factors. Timing is a warmed CUDA-graph kernel measurement;
it excludes loading, compilation, allocation, and model-level dispatch.
The script accepts any visible GPU count, including one logical device.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import statistics
import time
import traceback

import torch
import triton
import triton.language as tl

from packed_experts import (
    _decode_e2m1, _decode_e4m3fn, _decode_ue8m0, packed_expert_linear,
)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def event(name: str, **fields) -> None:
    print(json.dumps({"event": name, **fields}, allow_nan=False), flush=True)


def ue8m0_cpu(codes: torch.Tensor) -> torch.Tensor:
    result = torch.ldexp(torch.ones(codes.shape, dtype=torch.float32), codes.int() - 127)
    return torch.where(codes == 255, float("nan"), result)


def decode_cpu(packed, scales, format, columns, outer=None):
    if format == "nvfp4":
        lut = torch.tensor(
            [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0,
             -0.0, -0.5, -1.0, -1.5, -2.0, -3.0, -4.0, -6.0],
            dtype=torch.float32,
        )
        codes = torch.stack((packed & 15, packed >> 4), dim=-1).flatten(-2)
        values = lut[codes.long()][..., :columns]
        scale_values = scales.view(torch.float8_e4m3fn).float().repeat_interleave(16, -1)
    else:
        values = packed.view(torch.float8_e4m3fn).float()
        scale_values = ue8m0_cpu(scales).repeat_interleave(32, -1)
    weights = values * scale_values[..., :columns]
    if outer is not None:
        gamma = outer.reshape(-1, 1, 1) if outer.numel() != 1 else outer.reshape(1, 1, 1)
        weights = weights * gamma
    return weights


@triton.jit
def decode_fixture(Q, V, S):
    i = tl.arange(0, 256)
    tl.store(V + i, _decode_e4m3fn(i.to(tl.uint8)))
    tl.store(S + i, _decode_ue8m0(i.to(tl.uint8)))
    j = tl.arange(0, 16)
    tl.store(Q + j, _decode_e2m1(j.to(tl.uint8)))


def check_decoders(device):
    q = torch.empty(16, dtype=torch.float32, device=device)
    v = torch.empty(256, dtype=torch.float32, device=device)
    s = torch.empty_like(v)
    compiled = decode_fixture[(1,)](q, v, s, num_warps=4, enable_fp_fusion=False)
    codes = torch.arange(256, dtype=torch.uint8)
    expected_q = torch.tensor(
        [0.0, .5, 1., 1.5, 2., 3., 4., 6., -0.0, -.5, -1., -1.5, -2., -3., -4., -6.],
        dtype=torch.float32,
    )
    expected_v = codes.view(torch.float8_e4m3fn).float()
    expected_s = ue8m0_cpu(codes)
    for actual, expected in ((q.cpu(), expected_q), (v.cpu(), expected_v), (s.cpu(), expected_s)):
        torch.testing.assert_close(actual, expected, atol=0, rtol=0, equal_nan=True)
        finite = torch.isfinite(expected)
        assert torch.equal(torch.signbit(actual[finite]), torch.signbit(expected[finite]))
    assert ".target sm_61" in compiled.asm["ptx"]
    return {"e2m1_codes": 16, "e4m3fn_codes": 256, "ue8m0_codes": 256,
            "negative_zero_preserved": True, "nan_codes_preserved": True,
            "ue8m0_zero_code_is_2_to_minus127": True, "target_sm61": True}


def reference_linear(weights, x, ids):
    flat_x = x.reshape(-1, x.shape[-1])
    flat_ids = ids.reshape(-1)
    result = torch.empty((flat_x.shape[0], weights.shape[1]), dtype=torch.float32, device=weights.device)
    for row, expert in enumerate(flat_ids.tolist()):
        result[row] = torch.mv(weights[expert], flat_x[row])
    return result.reshape(*x.shape[:-1], weights.shape[1])


def errors(actual, expected):
    difference = (actual - expected).double()
    denominator = torch.linalg.vector_norm(expected.double())
    return {"max_abs": float(difference.abs().max()) if difference.numel() else 0.0,
            "relative_l2": float(torch.linalg.vector_norm(difference) / denominator) if denominator else 0.0}


def inputs(format, experts, outputs, columns, rows_shape, seed):
    generator = torch.Generator().manual_seed(seed)
    if format == "nvfp4":
        codes = torch.randint(0, 16, (experts, outputs, columns), dtype=torch.uint8, generator=generator)
        codes.reshape(-1)[:16] = torch.arange(16, dtype=torch.uint8)
        packed = (codes[..., 0::2] | (codes[..., 1::2] << 4)).contiguous()
        scales = torch.randint(0x28, 0x48, (experts, outputs, columns // 16), dtype=torch.uint8, generator=generator)
    else:
        magnitude = torch.randint(0x20, 0x49, (experts, outputs, columns), dtype=torch.uint8, generator=generator)
        sign = torch.randint(0, 2, magnitude.shape, dtype=torch.uint8, generator=generator) * 128
        packed = (magnitude | sign).contiguous()
        scales = torch.randint(123, 130, (experts, outputs, triton.cdiv(columns, 32)), dtype=torch.uint8, generator=generator)
    gamma = (0.3 + torch.rand(experts, generator=generator) * 1.2).float()
    gamma[0] = -0.731  # Also checks signed and non-binary-exact outer factors.
    x = torch.randn((*rows_shape, columns), generator=generator, dtype=torch.float32) * 0.271
    count = x.numel() // columns
    ids = ((torch.arange(count) * 5 + experts - 1) % experts).reshape(rows_shape).long()
    if count >= 3:
        ids.reshape(-1)[2] = ids.reshape(-1)[0]
    return packed, scales, gamma, x, ids


def graph_timing(operations, device, iterations, rounds, seed):
    for call in operations.values():
        for _ in range(3):
            call()
    torch.cuda.synchronize(device)
    stream = torch.cuda.Stream(device=device)
    graphs = {}
    for name, call in operations.items():
        graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(graph, stream=stream):
            call()
        graphs[name] = graph
    torch.cuda.synchronize(device)
    samples = {name: [] for name in graphs}
    orders = []
    rng = random.Random(seed)
    for _ in range(rounds):
        order = list(graphs)
        rng.shuffle(order)
        orders.append(order)
        for name in order:
            graph = graphs[name]
            for _ in range(3):
                graph.replay()
            start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
            start.record()
            for _ in range(iterations):
                graph.replay()
            end.record()
            end.synchronize()
            samples[name].append(start.elapsed_time(end) / iterations)
    medians = {name: statistics.median(values) for name, values in samples.items()}
    return {"mode": "cuda_graph_replay_events", "median_ms": medians,
            "samples_ms": samples, "method_order_per_round": orders,
            "speedup_vs_torch_fp32_bmm": medians["torch_fp32_bmm"] / medians["packed_fp32"],
            "iterations_per_sample": iterations, "rounds": rounds, "clocks_locked": False,
            "excludes": ["compilation", "input preparation", "H2D", "allocation",
                         "FP32 reference dequantization", "expert selection", "model dispatch"]}


def check_case(device, case, seed, timing_iterations, timing_rounds, time_production):
    name, format, experts, outputs, columns, rows_shape, chunk = case
    cpu = inputs(format, experts, outputs, columns, rows_shape, seed)
    if name.startswith("production_"):
        # Cover all eight selected experts in the production-size cases;
        # compact routing cases independently cover duplicate IDs.
        cpu = (*cpu[:-1], torch.randperm(experts, generator=torch.Generator().manual_seed(seed + 1000)))
    packed, scales, gamma, x, ids = cpu
    expected_weights = decode_cpu(packed, scales, format, columns, gamma)
    expected = reference_linear(expected_weights, x, ids)
    pw, bs, outer, gx, gi = [value.to(device) for value in cpu]
    if name == "expanded":
        # A token expanded over routing slots is a normal adapter input.
        gx = gx[:, :1, :].expand_as(gx)
        x = x[:, :1, :].expand_as(x)
        expected = reference_linear(expected_weights, x, ids)
        assert not gx.is_contiguous()
    out = torch.empty(expected.shape, dtype=torch.float32, device=device)
    call = lambda: packed_expert_linear(
        gx, gi, pw, bs, format=format, outer_scale=outer, out=out, rows_per_chunk=chunk,
    )
    returned = call()
    assert returned is out
    actual = out.cpu()
    torch.testing.assert_close(actual, expected, atol=3e-4, rtol=5e-5)
    assert torch.isfinite(actual).all()
    cuda_weights = expected_weights.to(device)
    cuda_reference = reference_linear(cuda_weights, gx, ids).cpu()
    torch.testing.assert_close(actual, cuda_reference, atol=3e-4, rtol=5e-5)
    repeat = call().cpu()
    assert torch.equal(repeat, actual), "Repeated packed linear output changed"
    other_chunk = packed_expert_linear(
        gx, gi, pw, bs, format=format, outer_scale=outer, rows_per_chunk=1,
    ).cpu()
    assert torch.equal(other_chunk, actual), "Activation-row chunking changed output"
    if outputs % 2 == 0:
        torch.testing.assert_close(actual[..., 0::2], expected[..., 0::2], atol=3e-4, rtol=5e-5)
        torch.testing.assert_close(actual[..., 1::2], expected[..., 1::2], atol=3e-4, rtol=5e-5)
    result = {"case": name, "format": format, "shape": {"E": experts, "N": outputs, "K": columns,
              "activation_rows": list(rows_shape)}, "passed": True,
              "cpu_reference_error": errors(actual, expected),
              "cuda_fp32_reference_error": errors(actual, cuda_reference),
              "warm_repeat_exact": True, "row_chunking_exact": True,
              "outer_scale_nonunit": True, "arbitrary_fp32_activations": True,
              "raw_output_row_order_preserved": True,
              "packed_weight_bytes": pw.numel(), "scale_bytes": bs.numel()}
    if name.startswith("production_") and time_production:
        # Select and materialize the reference expert matrices before timing.
        # Both routes use the same arbitrary FP32 inputs and represented weights.
        selected = cuda_weights.index_select(0, gi.reshape(-1)).contiguous()
        vectors = gx.reshape(-1, columns, 1).contiguous()
        baseline = torch.empty((selected.shape[0], outputs, 1), dtype=torch.float32, device=device)
        bmm = lambda: torch.bmm(selected, vectors, out=baseline)
        bmm()
        torch.testing.assert_close(out.cpu(), baseline.squeeze(-1).cpu(), atol=3e-4, rtol=5e-5)
        result["timing"] = graph_timing(
            {"packed_fp32": call, "torch_fp32_bmm": bmm}, device,
            timing_iterations, timing_rounds, seed,
        )
    return result


def check_gamma_rounding(device):
    """A one-active-column dot distinguishes per-weight gamma from epilogue gamma."""
    records = []
    for format in ("nvfp4", "mxfp8"):
        columns, outputs = 32, 32
        rows = torch.arange(outputs)
        if format == "nvfp4":
            packed = torch.zeros((1, outputs, columns // 2), dtype=torch.uint8)
            packed[0, :, 0] = (1 + rows % 7).to(torch.uint8)
            scale_codes = (0x28 + (rows * 7) % (0x7F - 0x28)).to(torch.uint8)
            scales = scale_codes[None, :, None].expand(1, outputs, 2).contiguous()
        else:
            packed = torch.zeros((1, outputs, columns), dtype=torch.uint8)
            packed[0, :, 0] = (0x20 + (rows * 7) % 70).to(torch.uint8)
            scales = (123 + rows % 11).to(torch.uint8)[None, :, None].contiguous()
        gamma = torch.tensor([0.731], dtype=torch.float32)
        x = torch.zeros((17, columns), dtype=torch.float32)
        x[:, 0] = torch.linspace(0.071, 1.373, x.shape[0])
        ids = torch.zeros(x.shape[0], dtype=torch.int64)
        unscaled = decode_cpu(packed, scales, format, columns)
        weights = decode_cpu(packed, scales, format, columns, gamma)
        expected = x[:, 0, None] * weights[0, :, 0][None, :]
        moved_to_epilogue = (x[:, 0, None] * unscaled[0, :, 0][None, :]) * gamma
        differing = int((expected != moved_to_epilogue).sum())
        assert differing > 0, "Gamma-order fixture does not distinguish the rounding order"
        actual = packed_expert_linear(
            x.to(device), ids.to(device), packed.to(device), scales.to(device),
            format=format, outer_scale=gamma.to(device),
        ).cpu()
        torch.testing.assert_close(actual, expected, atol=0, rtol=0)
        records.append({"format": format, "per_weight_gamma_exact": True,
                        "outputs_differing_if_gamma_moved_to_epilogue": differing})
    return records


def check_row_scales(device):
    # Every row has a distinct rowwise exponent pattern. Reusing r//32
    # scales (the old synthetic probe's bug) gives a materially wrong answer.
    experts, outputs, columns = 2, 65, 96
    packed = torch.full((experts, outputs, columns), 0x38, dtype=torch.uint8)
    scales = (120 + (torch.arange(experts * outputs * 3).reshape(experts, outputs, 3) % 12)).to(torch.uint8)
    x = (torch.arange(1, columns + 1).float() / 97).repeat(3, 1)
    ids = torch.tensor([1, 0, 1])
    expected_weights = decode_cpu(packed, scales, "mxfp8", columns)
    expected = reference_linear(expected_weights, x, ids)
    wrong_scales = scales[:, torch.arange(outputs) // 32, :]
    wrong = reference_linear(decode_cpu(packed, wrong_scales, "mxfp8", columns), x, ids)
    assert not torch.allclose(wrong, expected, atol=3e-4, rtol=5e-5)
    actual = packed_expert_linear(x.to(device), ids.to(device), packed.to(device), scales.to(device), format="mxfp8").cpu()
    torch.testing.assert_close(actual, expected, atol=3e-4, rtol=5e-5)
    return {"case": "independent_mxfp8_row_scales", "passed": True,
            "rejects_old_row_div32_semantics": True, "error": errors(actual, expected)}


def check_special_and_invalid(device):
    result = []
    for format in ("nvfp4", "mxfp8"):
        columns = 32
        if format == "nvfp4":
            scale_codes = torch.tensor([0, 128, 1, 126, 127, 255], dtype=torch.uint8)
            packed = torch.full((1, 6, columns // 2), 0x22, dtype=torch.uint8)
            scales = scale_codes[None, :, None].expand(1, 6, 2).contiguous()
        else:
            scale_codes = torch.tensor([0, 1, 127, 254, 255], dtype=torch.uint8)
            packed = torch.full((1, 5, columns), 0x38, dtype=torch.uint8)
            scales = scale_codes[None, :, None].contiguous()
        x = torch.zeros((1, columns), dtype=torch.float32)
        x[0, 0] = 1
        ids = torch.zeros(1, dtype=torch.int32)
        expected = reference_linear(decode_cpu(packed, scales, format, columns), x, ids)
        actual = packed_expert_linear(x.to(device), ids.to(device), packed.to(device), scales.to(device), format=format).cpu()
        torch.testing.assert_close(actual, expected, atol=0, rtol=0, equal_nan=True)
        invalid = packed_expert_linear(
            x.expand(2, -1).to(device), torch.tensor([-1, 1], device=device),
            packed.to(device), scales.to(device), format=format,
        ).cpu()
        assert torch.isnan(invalid).all()
        result.append({"format": format, "scale_codepoints": scale_codes.tolist(),
                       "special_values_exact": True, "invalid_ids_masked_to_nan": True})
    return result


def check_validation(device):
    x = torch.ones((1, 64), dtype=torch.float32, device=device)
    ids = torch.zeros(1, dtype=torch.int64, device=device)
    weights = torch.zeros((1, 64, 64), dtype=torch.uint8, device=device)
    scales = torch.full((1, 64, 2), 127, dtype=torch.uint8, device=device)
    rejected = []
    operations = {
        "row_shared_mxfp8_scale_shape": lambda: packed_expert_linear(x, ids, weights, scales[:, :2], format="mxfp8"),
        "float16_activations": lambda: packed_expert_linear(x.half(), ids, weights, scales, format="mxfp8"),
        "unsupported_format": lambda: packed_expert_linear(x, ids, weights, scales, format="int4"),
        "bad_nvfp4_alignment": lambda: packed_expert_linear(x[:, :17], ids, weights[:, :, :9].contiguous(), scales, format="nvfp4"),
        "output_alias": lambda: packed_expert_linear(x, ids, weights, scales, format="mxfp8", out=x),
        "zero_row_chunk": lambda: packed_expert_linear(x, ids, weights, scales, format="mxfp8", rows_per_chunk=0),
    }
    for name, operation in operations.items():
        try:
            operation()
        except ValueError:
            rejected.append(name)
        else:
            raise AssertionError(f"Invalid contract was accepted: {name}")
    empty = packed_expert_linear(x[:0], ids[:0], weights, scales, format="mxfp8")
    assert empty.shape == (0, 64) and empty.numel() == 0
    return {"rejected_inputs": rejected, "empty_rows_passed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpus", type=int, nargs="+")
    parser.add_argument("--seed", type=int, default=9152)
    parser.add_argument("--time-production", action="store_true", help="Compare production-size packed/FP32 bmm CUDA-graph timings")
    parser.add_argument("--timing-iterations", type=int, default=25)
    parser.add_argument("--timing-rounds", type=int, default=7)
    parser.add_argument("--skip-production", action="store_true", help="Only run compact correctness cases")
    args = parser.parse_args()
    if not 1 <= args.timing_iterations <= 1000 or not 1 <= args.timing_rounds <= 25:
        parser.error("Timing iterations must be in [1,1000] and rounds in [1,25]")
    torch.set_num_threads(4)
    gpus = args.gpus if args.gpus is not None else list(range(torch.cuda.device_count()))
    if not gpus or len(gpus) != len(set(gpus)):
        parser.error("Select at least one distinct visible CUDA device")
    report = {"schema_version": 1, "passed": False, "seed": args.seed,
              "torch_version": torch.__version__, "triton_version": triton.__version__,
              "precision": "native packed weight bytes, FP32 activation/decode/multiply/reduction",
              "scope": "Synthetic operator qualification; not a checkpoint or model-speed claim",
              "devices": [], "failures": []}
    started = time.monotonic()
    write_json(args.output, report)
    try:
        with torch.inference_mode():
            for gpu in gpus:
                device = torch.device("cuda", gpu)
                if torch.cuda.get_device_capability(device) != (6, 1):
                    raise RuntimeError(f"GPU {gpu} is not SM61")
                torch.cuda.set_device(device)
                entry = {"gpu": gpu, "name": torch.cuda.get_device_name(device), "cases": []}
                report["devices"].append(entry)
                entry["decoders"] = check_decoders(device)
                entry["validation"] = check_validation(device)
                entry["special_values"] = check_special_and_invalid(device)
                entry["gamma_rounding"] = check_gamma_rounding(device)
                entry["row_scales"] = check_row_scales(device)
                cases = []
                for format in ("nvfp4", "mxfp8"):
                    cases.extend([
                        ("single", format, 3, 17, 64, (1,), 256),
                        ("expanded", format, 3, 18, 96, (2, 3), 2),
                        ("chunked_tail", format, 3, 19, 144 if format == "nvfp4" else 65, (9,), 4),
                        ("bounded_k_tiles", format, 2, 7, 4160 if format == "nvfp4" else 4103, (3,), 2),
                    ])
                    if not args.skip_production:
                        cases.extend([
                            ("production_gate_up", format, 8, 2304, 2304, (8,), 256),
                            ("production_down", format, 8, 2304, 1152, (8,), 256),
                        ])
                for index, case in enumerate(cases):
                    event("case_start", gpu=gpu, case=case[0], format=case[1])
                    entry["cases"].append(check_case(
                        device, case, args.seed + index, args.timing_iterations,
                        args.timing_rounds, args.time_production,
                    ))
                    write_json(args.output, report)
                    event("case_pass", gpu=gpu, case=case[0], format=case[1])
                entry["passed"] = True
                write_json(args.output, report)
        report["passed"] = True
    except Exception as exc:
        report["failures"].append({"error": repr(exc), "traceback": traceback.format_exc()})
        raise
    finally:
        report["duration_seconds"] = time.monotonic() - started
        write_json(args.output, report)
        event("probe_complete", passed=report["passed"], output=str(args.output))


if __name__ == "__main__":
    main()

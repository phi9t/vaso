#!/usr/bin/env python3
"""Host tests for live workload criteria and runner aggregation."""

from __future__ import annotations

import os
import pathlib
import types
import tempfile
import unittest
from unittest import mock

from workloads import run_workloads
from workloads import w1_torch_train
from workloads import w2_triton
from workloads import w3_jax
from workloads import w4_coresidence
from workloads import w5_jax_model


class TorchCriteriaTest(unittest.TestCase):
    def test_eager_training_requires_half_loss_drop_and_finite_metrics(self) -> None:
        passed = w1_torch_train.evaluate_w1a(
            {
                "initial_loss": 4.0,
                "final_loss": 1.9,
                "losses": [4.0, 3.0, 1.9],
                "tokens_per_second": 1234.0,
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w1_torch_train.evaluate_w1a(
            {
                "initial_loss": 4.0,
                "final_loss": 2.1,
                "losses": [4.0, 2.1],
                "tokens_per_second": 1234.0,
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("loss_drop", failed["failed_criteria"])

    def test_compiled_training_accepts_late_drift_after_prefix_and_requires_full_loss_drop(self) -> None:
        passed = w1_torch_train.evaluate_w1b(
            {
                "compile_succeeded": True,
                "eager_losses": [
                    5.0,
                    0.35,
                    0.03,
                    0.02,
                    0.01,
                    0.009,
                    0.008,
                    0.007,
                    0.006,
                    0.005,
                    0.004,
                    0.003,
                    0.002,
                    0.001,
                    0.002,
                    0.003,
                    0.05,
                    0.08,
                    0.67,
                    1.55,
                ],
                "compiled_losses": [
                    5.0,
                    0.36,
                    0.03,
                    0.02,
                    0.01,
                    0.009,
                    0.008,
                    0.007,
                    0.006,
                    0.005,
                    0.004,
                    0.003,
                    0.002,
                    0.001,
                    0.002,
                    0.003,
                    0.04,
                    0.07,
                    0.80,
                    1.49,
                ],
                "full_compiled_initial_loss": 5.0,
                "full_compiled_final_loss": 0.01,
                "loss_tolerance": 0.05,
                "speedup": 1.1,
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w1_torch_train.evaluate_w1b(
            {
                "compile_succeeded": True,
                "eager_losses": [5.0] * w1_torch_train.COMPILE_LOSS_PREFIX_STEPS,
                "compiled_losses": [5.1] + [5.0] * (w1_torch_train.COMPILE_LOSS_PREFIX_STEPS - 1),
                "full_compiled_initial_loss": 5.0,
                "full_compiled_final_loss": 0.01,
                "loss_tolerance": 0.05,
                "speedup": 0.9,
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("loss_prefix_tolerance", failed["failed_criteria"])

        failed_no_drop = w1_torch_train.evaluate_w1b(
            {
                "compile_succeeded": True,
                "eager_losses": [5.0] * w1_torch_train.COMPILE_LOSS_PREFIX_STEPS,
                "compiled_losses": [5.0] * w1_torch_train.COMPILE_LOSS_PREFIX_STEPS,
                "full_compiled_initial_loss": 5.0,
                "full_compiled_final_loss": 4.0,
                "loss_tolerance": 0.05,
                "speedup": 0.9,
            }
        )
        self.assertEqual(failed_no_drop["verdict"], "failed")
        self.assertIn("full_compiled_loss_drop", failed_no_drop["failed_criteria"])

    def test_ddp_requires_all_ranks_drop_consistent_params_and_nccl_version(self) -> None:
        passed = w1_torch_train.evaluate_w1c(
            {
                "world_size": 8,
                "rank_results": [
                    {"rank": rank, "initial_loss": 5.0, "final_loss": 2.0, "finite": True}
                    for rank in range(8)
                ],
                "max_parameter_delta": 0.0,
                "nccl_version": [2, 30, 7],
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w1_torch_train.evaluate_w1c(
            {
                "world_size": 8,
                "rank_results": [
                    {"rank": rank, "initial_loss": 5.0, "final_loss": 2.0, "finite": True}
                    for rank in range(7)
                ],
                "max_parameter_delta": 1.0e-4,
                "nccl_version": [2, 30, 7],
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("all_ranks_finished", failed["failed_criteria"])
        self.assertIn("parameter_consistency", failed["failed_criteria"])

    def test_fsdp_resume_requires_loss_continuity(self) -> None:
        passed = w1_torch_train.evaluate_w1d(
            {
                "world_size": 8,
                "saved_loss": 3.0,
                "resume_first_loss": 3.02,
                "resume_tolerance": 0.05,
                "post_resume_final_loss": 2.0,
                "finite": True,
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w1_torch_train.evaluate_w1d(
            {
                "world_size": 8,
                "saved_loss": 3.0,
                "resume_first_loss": 3.3,
                "resume_tolerance": 0.05,
                "post_resume_final_loss": 3.4,
                "finite": True,
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("resume_continuity", failed["failed_criteria"])
        self.assertIn("post_resume_progress", failed["failed_criteria"])


class TritonCriteriaTest(unittest.TestCase):
    def test_kernel_criteria_require_numeric_match_and_tflops(self) -> None:
        passed = w2_triton.evaluate_w2a(
            {
                "vector_add_max_abs_error": 0.0,
                "softmax_max_abs_error": 1.0e-6,
                "matmul_max_abs_error": 8.0e-3,
                "matmul_tflops": 100.0,
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w2_triton.evaluate_w2a(
            {
                "vector_add_max_abs_error": 0.0,
                "softmax_max_abs_error": 1.0e-2,
                "matmul_max_abs_error": 8.0e-3,
                "matmul_tflops": 100.0,
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("softmax_error", failed["failed_criteria"])

    def test_custom_op_criteria_require_gradcheck_and_loss_drop(self) -> None:
        passed = w2_triton.evaluate_w2b(
            {
                "gradcheck_passed": True,
                "eager_initial_loss": 5.0,
                "eager_final_loss": 2.0,
                "compiled_initial_loss": 5.0,
                "compiled_final_loss": 2.0,
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w2_triton.evaluate_w2b(
            {
                "gradcheck_passed": False,
                "eager_initial_loss": 5.0,
                "eager_final_loss": 4.0,
                "compiled_initial_loss": 5.0,
                "compiled_final_loss": 2.0,
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("gradcheck", failed["failed_criteria"])
        self.assertIn("eager_loss_drop", failed["failed_criteria"])

    def test_custom_op_registration_installs_tensor_annotations_before_schema_inference(self) -> None:
        class FakeTorch:
            class Tensor:
                pass

            @staticmethod
            def empty_like(value):
                return value

        class FakeLibrary:
            def __init__(self) -> None:
                self.registered: list[str] = []

            def custom_op(self, name, mutates_args=()):
                del mutates_args

                def decorator(func):
                    self.registered.append(name)
                    self.assert_annotation_globals(func)
                    return func

                return decorator

            def register_fake(self, name):
                self.registered.append(name + ":fake")

                def decorator(func):
                    return func

                return decorator

            def register_autograd(self, name, backward, *, setup_context):
                del backward, setup_context
                self.registered.append(name + ":autograd")

            def assert_annotation_globals(self, func) -> None:
                import typing

                hints = typing.get_type_hints(func)
                for value in hints.values():
                    self_outer.assertIs(value, FakeTorch.Tensor)

        class FakeTriton:
            def __init__(self) -> None:
                self.library = FakeLibrary()

            @staticmethod
            def jit(func):
                return func

            @staticmethod
            def cdiv(numerator, denominator):
                return (numerator + denominator - 1) // denominator

        class FakeTl:
            constexpr = object()

        self_outer = self
        fake_triton = FakeTriton()
        FakeTorch.library = fake_triton.library
        old_tensor = getattr(w2_triton, "Tensor", None)
        old_op = w2_triton._TRITON_SILU_OP
        old_imports = w2_triton._torch_triton_imports
        try:
            w2_triton._TRITON_SILU_OP = None
            w2_triton._torch_triton_imports = lambda: (FakeTorch, fake_triton, FakeTl)
            if hasattr(w2_triton, "Tensor"):
                delattr(w2_triton, "Tensor")
            w2_triton.register_triton_silu()
            self.assertIn("vaso_workloads::triton_silu", fake_triton.library.registered)
        finally:
            w2_triton._TRITON_SILU_OP = old_op
            w2_triton._torch_triton_imports = old_imports
            if old_tensor is not None:
                w2_triton.Tensor = old_tensor
            elif hasattr(w2_triton, "Tensor"):
                delattr(w2_triton, "Tensor")

    def test_custom_op_kernels_promote_bf16_inputs_before_exp(self) -> None:
        class FakeExpr:
            def __init__(self, dtype: str = "index") -> None:
                self.dtype = dtype

            def to(self, dtype) -> "FakeExpr":
                return FakeExpr(dtype)

            def _combine(self, other) -> "FakeExpr":
                other_dtype = getattr(other, "dtype", "")
                if self.dtype == "fp32" or other_dtype == "fp32":
                    return FakeExpr("fp32")
                return FakeExpr(self.dtype)

            def __add__(self, other):
                return self._combine(other)

            __radd__ = __add__

            def __sub__(self, other):
                return self._combine(other)

            def __rsub__(self, other):
                return self._combine(other)

            def __mul__(self, other):
                return self._combine(other)

            __rmul__ = __mul__

            def __truediv__(self, other):
                return self._combine(other)

            def __rtruediv__(self, other):
                return self._combine(other)

            def __neg__(self):
                return FakeExpr(self.dtype)

            def __lt__(self, other):
                del other
                return FakeExpr("bool")

        class FakePtr:
            def __add__(self, other):
                del other
                return self

        class FakeTorch:
            class Tensor:
                pass

            @staticmethod
            def empty_like(value):
                return value

        class FakeLibrary:
            def custom_op(self, name, mutates_args=()):
                del name, mutates_args
                return lambda func: func

            def register_fake(self, name):
                del name
                return lambda func: func

            def register_autograd(self, name, backward, *, setup_context):
                del name, backward, setup_context

        class FakeTriton:
            def __init__(self) -> None:
                self.jitted = []
                self.library = FakeLibrary()

            def jit(self, func):
                self.jitted.append(func)
                return func

            @staticmethod
            def cdiv(numerator, denominator):
                return (numerator + denominator - 1) // denominator

        class FakeTl:
            constexpr = object()
            float32 = "fp32"

            @staticmethod
            def program_id(axis):
                del axis
                return FakeExpr()

            @staticmethod
            def arange(start, stop):
                del start, stop
                return FakeExpr()

            @staticmethod
            def load(ptr, *, mask, other=None):
                del ptr, mask, other
                return FakeExpr("bf16")

            @staticmethod
            def exp(value):
                if getattr(value, "dtype", None) != "fp32":
                    raise AssertionError(f"tl.exp input dtype was {getattr(value, 'dtype', None)!r}")
                return FakeExpr("fp32")

            @staticmethod
            def store(ptr, value, *, mask):
                del ptr, value, mask

        fake_triton = FakeTriton()
        FakeTorch.library = fake_triton.library
        old_tensor = getattr(w2_triton, "Tensor", None)
        old_op = w2_triton._TRITON_SILU_OP
        old_imports = w2_triton._torch_triton_imports
        try:
            w2_triton._TRITON_SILU_OP = None
            w2_triton._torch_triton_imports = lambda: (FakeTorch, fake_triton, FakeTl)
            w2_triton.register_triton_silu()
            fake_triton.jitted[0](FakePtr(), FakePtr(), 8, 4)
            fake_triton.jitted[1](FakePtr(), FakePtr(), FakePtr(), 8, 4)
        finally:
            w2_triton._TRITON_SILU_OP = old_op
            w2_triton._torch_triton_imports = old_imports
            if old_tensor is not None:
                w2_triton.Tensor = old_tensor
            elif hasattr(w2_triton, "Tensor"):
                delattr(w2_triton, "Tensor")

    def test_compile_autotune_criteria_require_compile_and_match(self) -> None:
        passed = w2_triton.evaluate_w2c(
            {
                "compile_succeeded": True,
                "max_abs_error": 6.0e-3,
                "max_rel_error": 5.0e-3,
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w2_triton.evaluate_w2c(
            {
                "compile_succeeded": False,
                "max_abs_error": 6.0e-3,
                "max_rel_error": 5.0e-3,
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("compile_succeeded", failed["failed_criteria"])


class JaxCriteriaTest(unittest.TestCase):
    def test_pmap_replication_uses_leading_axis_without_deprecated_device_put(self) -> None:
        class FakeTree:
            @staticmethod
            def map(fn, tree, *rest):
                if isinstance(tree, dict):
                    return {key: FakeTree.map(fn, value) for key, value in tree.items()}
                return fn(tree)

        class FakeJax:
            tree = FakeTree()

            @staticmethod
            def device_put_replicated(*args, **kwargs):  # pragma: no cover - must not be called
                raise AssertionError("deprecated device_put_replicated must not be used")

        class FakeJnp:
            @staticmethod
            def stack(values):
                return tuple(values)

        replicated = w3_jax._replicate_for_pmap(
            FakeJax(),
            FakeJnp(),
            {"weight": "w", "state": {"step": 1}},
            replicas=3,
        )

        self.assertEqual(replicated, {"weight": ("w", "w", "w"), "state": {"step": (1, 1, 1)}})

    def test_jit_grad_training_requires_half_loss_drop_and_finite_metrics(self) -> None:
        passed = w3_jax.evaluate_w3a(
            {
                "initial_loss": 4.0,
                "final_loss": 1.9,
                "losses": [4.0, 3.0, 1.9],
                "steps": 300,
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w3_jax.evaluate_w3a(
            {
                "initial_loss": 4.0,
                "final_loss": 2.2,
                "losses": [4.0, 2.2],
                "steps": 300,
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("loss_drop", failed["failed_criteria"])

    def test_data_parallel_requires_eight_devices_consistent_params_and_loss_drop(self) -> None:
        passed = w3_jax.evaluate_w3b(
            {
                "device_count": 8,
                "initial_loss": 5.0,
                "final_loss": 2.0,
                "max_parameter_delta": 0.0,
                "psum_result": 8.0,
                "finite": True,
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w3_jax.evaluate_w3b(
            {
                "device_count": 7,
                "initial_loss": 5.0,
                "final_loss": 4.0,
                "max_parameter_delta": 1.0e-4,
                "psum_result": 7.0,
                "finite": True,
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("device_count", failed["failed_criteria"])
        self.assertIn("parameter_consistency", failed["failed_criteria"])
        self.assertIn("loss_drop", failed["failed_criteria"])

    def test_pallas_kernel_requires_numerics_within_tolerance(self) -> None:
        passed = w3_jax.evaluate_w3c(
            {
                "compile_succeeded": True,
                "max_abs_error": 5.0e-4,
                "tolerance": 1.0e-3,
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w3_jax.evaluate_w3c(
            {
                "compile_succeeded": True,
                "max_abs_error": 2.0e-2,
                "tolerance": 1.0e-3,
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("max_abs_error", failed["failed_criteria"])


class CoresidenceCriteriaTest(unittest.TestCase):
    def test_coresidence_requires_imports_dlpack_round_trip_and_tiny_steps(self) -> None:
        passed = w4_coresidence.evaluate_w4(
            {
                "torch_imported": True,
                "jax_imported": True,
                "torch_to_jax_exact": True,
                "jax_to_torch_exact": True,
                "torch_step_loss": 1.0,
                "jax_step_loss": 1.0,
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w4_coresidence.evaluate_w4(
            {
                "torch_imported": True,
                "jax_imported": True,
                "torch_to_jax_exact": False,
                "jax_to_torch_exact": True,
                "torch_step_loss": float("inf"),
                "jax_step_loss": 1.0,
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("torch_to_jax_exact", failed["failed_criteria"])
        self.assertIn("torch_step_finite", failed["failed_criteria"])


class JaxModelCriteriaTest(unittest.TestCase):
    def test_model_size_math_is_about_125m_parameters(self) -> None:
        breakdown = w5_jax_model.model_size_breakdown()

        self.assertEqual(breakdown["token_embedding"], 39_321_600)
        self.assertEqual(breakdown["position_embedding"], 786_432)
        self.assertEqual(breakdown["per_layer"], 7_080_960)
        self.assertEqual(breakdown["layers"], 84_971_520)
        self.assertEqual(breakdown["final_layer_norm"], 1_536)
        self.assertEqual(breakdown["output_bias"], 51_200)
        self.assertEqual(breakdown["total"], 125_132_288)

    def test_markov_entropy_floor_uses_hand_checked_transition_distribution(self) -> None:
        floor = w5_jax_model.markov_transition_entropy_nats(active_vocab_size=4, major_probability=0.5)

        self.assertAlmostEqual(floor, 1.242453324894, places=12)

    def test_fsdp_model_load_requires_loss_drop_entropy_floor_band_and_throughput(self) -> None:
        passed = w5_jax_model.evaluate_w5a(
            {
                "steps": 400,
                "initial_loss": 3.0,
                "final_loss": 1.05,
                "losses": [3.0, 2.0, 1.05],
                "data_entropy_floor_nats": 1.0,
                "loss_floor_upper_nats": 1.1,
                "final_loss_floor_ratio": 1.05,
                "tokens_per_second": 1_000_000.0,
                "mfu": 0.12,
                "parameter_count": w5_jax_model.TARGET_PARAMETER_COUNT,
                "device_count": 8,
                "mesh_shape": {"data": 8},
                "param_sharding": "fsdp_named_sharding_data_axis",
                "optimizer_sharding": "fsdp_named_sharding_data_axis",
                "batch_sharding": "data",
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w5_jax_model.evaluate_w5a(
            {
                "steps": 399,
                "initial_loss": 3.0,
                "final_loss": 1.4,
                "losses": [3.0, float("nan"), 1.4],
                "data_entropy_floor_nats": 1.5,
                "loss_floor_upper_nats": 1.65,
                "final_loss_floor_ratio": 0.9333333333333333,
                "tokens_per_second": 0.0,
                "mfu": 0.12,
                "parameter_count": w5_jax_model.TARGET_PARAMETER_COUNT,
                "device_count": 7,
                "mesh_shape": {"data": 7},
                "param_sharding": "replicated",
                "optimizer_sharding": "replicated",
                "batch_sharding": "",
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("loss_drop_60_percent", failed["failed_criteria"])
        self.assertIn("loss_not_below_entropy_floor", failed["failed_criteria"])
        self.assertIn("finite_losses", failed["failed_criteria"])
        self.assertIn("step_count", failed["failed_criteria"])
        self.assertIn("device_count", failed["failed_criteria"])
        self.assertIn("fsdp_sharding", failed["failed_criteria"])
        self.assertIn("tokens_per_second_recorded", failed["failed_criteria"])

        high = w5_jax_model.evaluate_w5a(
            {
                "steps": 400,
                "initial_loss": 3.0,
                "final_loss": 1.12,
                "losses": [3.0, 2.0, 1.12],
                "data_entropy_floor_nats": 1.0,
                "loss_floor_upper_nats": 1.1,
                "final_loss_floor_ratio": 1.12,
                "tokens_per_second": 1_000_000.0,
                "mfu": 0.12,
                "parameter_count": w5_jax_model.TARGET_PARAMETER_COUNT,
                "device_count": 8,
                "mesh_shape": {"data": 8},
                "param_sharding": "fsdp_named_sharding_data_axis",
                "optimizer_sharding": "fsdp_named_sharding_data_axis",
                "batch_sharding": "data",
            }
        )
        self.assertEqual(high["verdict"], "failed")
        self.assertIn("loss_near_entropy_floor", high["failed_criteria"])

    def test_tensor_parallel_model_load_requires_loss_match_to_w5a(self) -> None:
        passed = w5_jax_model.evaluate_w5b(
            {
                "steps": 200,
                "initial_loss": 3.0,
                "final_loss": 1.15,
                "losses": [3.0, 2.0, 1.15],
                "w5a_reference_loss_at_step": 1.10,
                "loss_tolerance": 0.10,
                "device_count": 8,
                "mesh_shape": {"data": 4, "model": 2},
                "tensor_parallelism": {
                    "attention": "column_row",
                    "mlp": "column_row",
                    "all_gather": True,
                    "reduce_scatter": True,
                },
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w5_jax_model.evaluate_w5b(
            {
                "steps": 200,
                "initial_loss": 3.0,
                "final_loss": 1.45,
                "losses": [3.0, 2.9, 1.45],
                "w5a_reference_loss_at_step": 1.10,
                "loss_tolerance": 0.10,
                "device_count": 8,
                "mesh_shape": {"data": 4, "model": 2},
                "tensor_parallelism": {
                    "attention": "column_row",
                    "mlp": "column_row",
                    "all_gather": False,
                    "reduce_scatter": True,
                },
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("loss_within_w5a_tolerance", failed["failed_criteria"])
        self.assertIn("collectives_exercised", failed["failed_criteria"])

    def test_shard_map_compatibility_handles_jax_check_vma_rename(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeJax:
            def shard_map(self, fn, *, mesh, in_specs, out_specs, check_vma):
                del fn, mesh, in_specs, out_specs
                calls.append({"check_vma": check_vma})
                return "mapped"

        result = w5_jax_model._shard_map_compat(FakeJax(), object(), mesh="mesh", in_specs="in", out_specs="out")

        self.assertEqual(result, "mapped")
        self.assertEqual(calls, [{"check_vma": False}])

    def test_shard_map_compatibility_keeps_older_jax_check_rep_keyword(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeJax:
            def shard_map(self, fn, *, mesh, in_specs, out_specs, check_rep):
                del fn, mesh, in_specs, out_specs
                calls.append({"check_rep": check_rep})
                return "mapped"

        result = w5_jax_model._shard_map_compat(FakeJax(), object(), mesh="mesh", in_specs="in", out_specs="out")

        self.assertEqual(result, "mapped")
        self.assertEqual(calls, [{"check_rep": False}])

    def test_cpu_gpu_numerics_require_forward_backward_relative_error(self) -> None:
        passed = w5_jax_model.evaluate_w5c(
            {
                "loss_max_relative_error": 5.0e-5,
                "grad_max_relative_error": 8.0e-5,
                "tolerance": 1.0e-4,
                "gpu_backend": "gpu",
                "cpu_backend": "cpu",
                "finite": True,
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w5_jax_model.evaluate_w5c(
            {
                "loss_max_relative_error": 2.0e-4,
                "grad_max_relative_error": 8.0e-5,
                "tolerance": 1.0e-4,
                "gpu_backend": "gpu",
                "cpu_backend": "cpu",
                "finite": True,
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("loss_relative_error", failed["failed_criteria"])

    def test_attention_kernel_criteria_require_both_paths_and_bf16_agreement(self) -> None:
        passed = w5_jax_model.evaluate_w5d(
            {
                "seq_lengths": [1024, 2048],
                "cudnn_ran": True,
                "xla_ran": True,
                "max_output_abs_error": 6.0e-2,
                "max_grad_abs_error": 7.0e-2,
                "tolerance": 8.0e-2,
                "finite": True,
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w5_jax_model.evaluate_w5d(
            {
                "seq_lengths": [1024],
                "cudnn_ran": True,
                "xla_ran": False,
                "max_output_abs_error": 6.0e-2,
                "max_grad_abs_error": 2.0e-1,
                "tolerance": 8.0e-2,
                "finite": True,
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("seq_lengths", failed["failed_criteria"])
        self.assertIn("xla_ran", failed["failed_criteria"])
        self.assertIn("grad_agreement", failed["failed_criteria"])

    def test_triton_gemm_and_pallas_criteria_require_all_paths_to_match(self) -> None:
        passed = w5_jax_model.evaluate_w5e(
            {
                "xla_triton_enabled_ran": True,
                "xla_triton_disabled_ran": True,
                "pallas_matmul_ran": True,
                "enabled_disabled_max_abs_error": 6.0e-3,
                "pallas_max_abs_error": 6.0e-3,
                "tolerance": 1.0e-2,
                "xla_flags": {
                    "enabled": "--xla_gpu_enable_triton_gemm=true --xla_gpu_triton_gemm_any=true",
                    "disabled": "--xla_gpu_enable_triton_gemm=false --xla_gpu_triton_gemm_any=false",
                },
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w5_jax_model.evaluate_w5e(
            {
                "xla_triton_enabled_ran": True,
                "xla_triton_disabled_ran": False,
                "pallas_matmul_ran": True,
                "enabled_disabled_max_abs_error": 6.0e-3,
                "pallas_max_abs_error": 2.0e-2,
                "tolerance": 1.0e-2,
                "xla_flags": {
                    "enabled": "",
                    "disabled": "--xla_gpu_triton_gemm_any=False",
                },
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("xla_triton_disabled_ran", failed["failed_criteria"])
        self.assertIn("pallas_agreement", failed["failed_criteria"])
        self.assertIn("xla_flags_recorded", failed["failed_criteria"])

    def test_pallas_matmul_uses_blackwell_kernel_with_supported_k_tile(self) -> None:
        captured: dict[str, object] = {}

        class FakeArray:
            def __init__(self, shape):
                self.shape = shape

        class FakeJnp:
            float32 = "float32"

        class FakeTuningConfig:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        def fake_matmul_kernel(x, y, config):
            captured["x_shape"] = x.shape
            captured["y_shape"] = y.shape
            captured["config"] = config
            return "pallas-out"

        fake_blackwell = types.SimpleNamespace(
            TuningConfig=FakeTuningConfig,
            matmul_kernel=fake_matmul_kernel,
        )
        fake_gpu = types.SimpleNamespace(blackwell_matmul_mgpu=fake_blackwell)
        fake_ops = types.SimpleNamespace(gpu=fake_gpu)
        fake_pallas = types.SimpleNamespace(ops=fake_ops)
        fake_experimental = types.SimpleNamespace(pallas=fake_pallas)
        fake_jax_module = types.SimpleNamespace(experimental=fake_experimental)
        with mock.patch.dict(
            "sys.modules",
            {
                "jax": fake_jax_module,
                "jax.experimental": fake_experimental,
                "jax.experimental.pallas": fake_pallas,
                "jax.experimental.pallas.ops": fake_ops,
                "jax.experimental.pallas.ops.gpu": fake_gpu,
                "jax.experimental.pallas.ops.gpu.blackwell_matmul_mgpu": fake_blackwell,
            },
        ):
            result = w5_jax_model._pallas_matmul(
                object(),
                FakeJnp(),
                FakeArray((w5_jax_model.W5E_MAT_DIM, w5_jax_model.W5E_MAT_DIM)),
                FakeArray((w5_jax_model.W5E_MAT_DIM, w5_jax_model.W5E_MAT_DIM)),
            )

        config = captured["config"]
        self.assertEqual(result, "pallas-out")
        self.assertEqual(captured["x_shape"], (512, 512))
        self.assertEqual(captured["y_shape"], (512, 512))
        self.assertEqual(config.tile_m, 128)
        self.assertEqual(config.tile_n, 128)
        self.assertEqual(config.tile_k, 64)
        self.assertLessEqual(config.tile_k, w5_jax_model.PALLAS_ASYNC_COPY_DIM_LIMIT)
        self.assertFalse(config.collective)

    def test_determinism_requires_matching_loss_curves(self) -> None:
        passed = w5_jax_model.evaluate_w5f(
            {
                "steps": 50,
                "run1_losses": [3.0, 2.0, 1.0],
                "run2_losses": [3.0, 2.0, 1.0],
                "max_loss_delta": 0.0,
                "tolerance": 1.0e-6,
                "seed": 1234,
                "deterministic_mode": True,
                "xla_flags": "--xla_gpu_deterministic_ops=true",
                "nccl_algo": "Ring",
                "nccl_proto": "Simple",
            }
        )
        self.assertEqual(passed["verdict"], "passed")

        failed = w5_jax_model.evaluate_w5f(
            {
                "steps": 50,
                "run1_losses": [3.0, 2.0, 1.0],
                "run2_losses": [3.0, 2.0, 1.001],
                "max_loss_delta": 1.0e-3,
                "tolerance": 1.0e-6,
                "seed": 1234,
                "deterministic_mode": True,
                "xla_flags": "--xla_gpu_deterministic_ops=true",
                "nccl_algo": "Ring",
                "nccl_proto": "Simple",
            }
        )
        self.assertEqual(failed["verdict"], "failed")
        self.assertIn("loss_curves_match", failed["failed_criteria"])

        nondeterministic_env = w5_jax_model.evaluate_w5f(
            {
                "steps": 50,
                "run1_losses": [3.0, 2.0, 1.0],
                "run2_losses": [3.0, 2.0, 1.0],
                "max_loss_delta": 0.0,
                "tolerance": 1.0e-6,
                "seed": 1234,
                "deterministic_mode": False,
                "xla_flags": "",
                "nccl_algo": "",
                "nccl_proto": "",
            }
        )
        self.assertEqual(nondeterministic_env["verdict"], "failed")
        self.assertIn("deterministic_mode", nondeterministic_env["failed_criteria"])

    def test_gpt_training_passes_requested_steps_to_schedule(self) -> None:
        class StopBeforeCompilation(Exception):
            pass

        class FakeJax:
            def device_put(self, value, sharding):
                del sharding
                return value

        class FakeJnp:
            def asarray(self, value):
                return value

        recorded: dict[str, int] = {}

        def fake_make_train_step(jax, jnp, *, layout, shardings, total_steps):
            del jax, jnp, layout, shardings
            recorded["total_steps"] = total_steps
            raise StopBeforeCompilation

        sharding_ctx = {
            "batch": "batch_sharding",
            "scalar": "scalar_sharding",
            "mesh_shape": {"data": 8},
            "param_kind": "fsdp_named_sharding_data_axis",
            "param_sharding_for": lambda path, value: "param_sharding",
            "compute_param_sharding_for": lambda path, value: "compute_param_sharding",
        }

        with (
            mock.patch.object(w5_jax_model, "_jax_imports", return_value=(FakeJax(), FakeJnp())),
            mock.patch.object(w5_jax_model, "_require_gpu_devices", return_value=list(range(8))),
            mock.patch.object(w5_jax_model, "_make_sharding_context", return_value=sharding_ctx),
            mock.patch.object(w5_jax_model, "_init_gpt_master_params", return_value={"leaf": 1}),
            mock.patch.object(w5_jax_model, "_put_tree", side_effect=lambda jax, tree, shardings: tree),
            mock.patch.object(w5_jax_model, "_to_bf16_params", side_effect=lambda jax, jnp, params: params),
            mock.patch.object(w5_jax_model, "_adamw_state", return_value={"step": 0, "m": {}, "v": {}}),
            mock.patch.object(w5_jax_model, "_state_shardings", return_value={"state": "sharding"}),
            mock.patch.object(w5_jax_model, "_make_lm_batch", return_value="batch"),
            mock.patch.object(w5_jax_model, "_make_train_step", side_effect=fake_make_train_step),
        ):
            with self.assertRaises(StopBeforeCompilation):
                w5_jax_model._run_gpt_training(steps=37, seed=5005, layout="fsdp")

        self.assertEqual(recorded["total_steps"], 37)


class RunnerTest(unittest.TestCase):
    def test_registry_includes_all_live_workloads(self) -> None:
        self.assertEqual(
            list(run_workloads.WORKLOADS),
            [
                "W1a",
                "W1b",
                "W1c",
                "W1d",
                "W2a",
                "W2b",
                "W2c",
                "W3a",
                "W3b",
                "W3c",
                "W4",
                "W5a",
                "W5b",
                "W5c",
                "W5d",
                "W5e",
                "W5f",
            ],
        )
        self.assertEqual(run_workloads.WORKLOADS["W3b"].min_gpus, 8)
        self.assertEqual(run_workloads.WORKLOADS["W5a"].min_gpus, 8)
        self.assertEqual(run_workloads.WORKLOADS["W5f"].module, "workloads.w5_jax_model")
        self.assertEqual(run_workloads.WORKLOADS["W4"].module, "workloads.w4_coresidence")

    def test_jax_model_group_expands_to_w5_workloads(self) -> None:
        selected, error = run_workloads._select_workloads(["jax_model"])

        self.assertIsNone(error)
        self.assertEqual([workload.id for workload in selected], ["W5a", "W5b", "W5c", "W5d", "W5e", "W5f"])

    def test_summary_fails_when_any_workload_fails(self) -> None:
        summary = run_workloads.aggregate_results(
            [
                {"id": "W1a", "verdict": "passed", "duration_seconds": 1.0},
                {"id": "W2a", "verdict": "failed", "duration_seconds": 2.0},
            ]
        )
        self.assertEqual(summary["verdict"], "failed")
        self.assertEqual(summary["failed_workloads"], ["W2a"])

    def test_summary_passes_when_all_workloads_pass(self) -> None:
        summary = run_workloads.aggregate_results(
            [
                {"id": "W1a", "verdict": "passed", "duration_seconds": 1.0},
                {"id": "W2a", "verdict": "passed", "duration_seconds": 2.0},
            ]
        )
        self.assertEqual(summary["verdict"], "passed")
        self.assertEqual(summary["failed_workloads"], [])

    def test_runner_env_places_runtime_caches_under_out_dir(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as root:
            out_dir = pathlib.Path(root) / "out"
            env = run_workloads.workload_env(out_dir, {})
            self.assertEqual(env["TMPDIR"], str(out_dir / "tmp"))
            self.assertEqual(env["TORCHINDUCTOR_CACHE_DIR"], str(out_dir / "torchinductor"))
            self.assertEqual(env["TRITON_CACHE_DIR"], str(out_dir / "triton"))
            for key in ("TMPDIR", "TORCHINDUCTOR_CACHE_DIR", "TRITON_CACHE_DIR"):
                self.assertTrue(pathlib.Path(env[key]).is_relative_to(out_dir))

    def test_runner_env_pins_runtime_compilers_to_rootfs_gcc(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as root:
            out_dir = pathlib.Path(root) / "out"
            env = run_workloads.workload_env(
                out_dir,
                {
                    "CC": "/usr/lib/llvm-23/bin/clang",
                    "CXX": "/usr/lib/llvm-23/bin/clang++",
                },
            )

        self.assertEqual(env["CC"], "/usr/bin/gcc")
        self.assertEqual(env["CXX"], "/usr/bin/g++")

    def test_runner_env_preserves_preassigned_gpu_set(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as root:
            out_dir = pathlib.Path(root) / "out"
            env = run_workloads.workload_env(
                out_dir,
                {
                    "CUDA_VISIBLE_DEVICES": "2,5",
                    "VASO_GPU_SET": "2,5",
                },
                gpus=8,
            )

        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "2,5")
        self.assertEqual(env["VASO_GPU_SET"], "2,5")

    def test_runner_env_places_jax_prefixes_and_caches_under_out_dir(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as root:
            root_path = pathlib.Path(root)
            out_dir = root_path / "out"
            jax_prefix = root_path / "jax-prefix"
            jaxlib_prefix = root_path / "jaxlib-prefix"
            ml_dtypes_prefix = root_path / "ml-dtypes"
            for prefix in (jax_prefix, jaxlib_prefix, ml_dtypes_prefix):
                (prefix / "lib" / "python3.13" / "site-packages").mkdir(parents=True)
                (prefix / "lib").mkdir(exist_ok=True)
                (prefix / "bin").mkdir(exist_ok=True)

            env = run_workloads.workload_env(
                out_dir,
                {},
                jax_prefix=jax_prefix,
                jaxlib_prefix=jaxlib_prefix,
                runtime_prefixes=[("py-ml-dtypes", ml_dtypes_prefix)],
            )

        pythonpath = env["PYTHONPATH"].split(os.pathsep)
        self.assertIn(str(jax_prefix / "lib" / "python3.13" / "site-packages"), pythonpath)
        self.assertIn(str(jaxlib_prefix / "lib" / "python3.13" / "site-packages"), pythonpath)
        self.assertIn(str(ml_dtypes_prefix / "lib" / "python3.13" / "site-packages"), pythonpath)
        self.assertEqual(env["JAX_PREFIX"], str(jax_prefix))
        self.assertEqual(env["JAXLIB_PREFIX"], str(jaxlib_prefix))
        self.assertEqual(env["JAX_COMPILATION_CACHE_DIR"], str(out_dir / "jax_cache"))
        self.assertIn("--xla_gpu_cuda_data_dir=/usr/local/cuda", env["XLA_FLAGS"])

    def test_workload_sources_do_not_target_host_tmp(self) -> None:
        source_root = pathlib.Path(__file__).resolve().parent
        host_tmp = os.sep + "tmp"
        host_var_tmp = os.sep + "var" + os.sep + "tmp"
        for name in (
            "run_workloads.py",
            "w1_torch_train.py",
            "w2_triton.py",
            "w3_jax.py",
            "w4_coresidence.py",
            "w5_jax_model.py",
        ):
            text = (source_root / name).read_text(encoding="utf-8")
            self.assertNotIn('"' + host_tmp, text)
            self.assertNotIn("'" + host_tmp, text)
            self.assertNotIn(host_var_tmp, text)


if __name__ == "__main__":
    unittest.main()

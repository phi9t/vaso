"""Framework-neutral contracts for the decoder-family speedrun matrix.

The mini families preserve the architectural seams that materially change the
training graph. They are workload-scale models, not checkpoint-compatible
reimplementations of the released large models.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace


FAMILIES = ("gpt", "qwen3", "mini_kimi_k3", "mini_glm_53_flash")
MODES = ("smoke", "benchmark")
ARMS = ("baseline", "triton")
BACKENDS = ("torch", "jax")


@dataclass(frozen=True, slots=True)
class FamilySpec:
    """Architecture choices shared by the Torch and JAX implementations."""

    name: str
    norm: str
    activation: str
    positional: str
    attention_pattern: tuple[str, ...]
    num_kv_heads: int
    qk_norm: bool = False
    latent_qkv: bool = False
    moe_first_layer: int | None = None
    num_experts: int = 0
    experts_per_token: int = 0
    tie_embeddings: bool = True
    source_contract: str = ""
    retained_elements: tuple[str, ...] = ()
    omitted_elements: tuple[str, ...] = ()

    def validate(self, *, num_heads: int, num_layers: int) -> None:
        if self.name not in FAMILIES:
            raise ValueError(f"unknown family: {self.name}")
        if self.norm not in {"layer", "rms"}:
            raise ValueError(f"unknown norm: {self.norm}")
        if self.activation not in {"gelu", "swiglu", "situ", "relu2"}:
            raise ValueError(f"unknown activation: {self.activation}")
        if self.positional not in {"learned", "rope"}:
            raise ValueError(f"unknown positional encoding: {self.positional}")
        if not self.attention_pattern or any(
            kind not in {"softmax", "linear", "sparse"}
            for kind in self.attention_pattern
        ):
            raise ValueError("attention pattern contains an unsupported kind")
        if num_layers < 1 or num_heads < 1:
            raise ValueError("layer and head counts must be positive")
        if self.num_kv_heads < 1 or num_heads % self.num_kv_heads:
            raise ValueError("num_kv_heads must divide num_heads")
        if self.moe_first_layer is None:
            if self.num_experts or self.experts_per_token:
                raise ValueError("dense family cannot declare experts")
        elif not (
            0 <= self.moe_first_layer < num_layers
            and 1 <= self.experts_per_token <= self.num_experts
        ):
            raise ValueError("invalid MoE layer or expert counts")

    def attention_kind(self, layer_index: int) -> str:
        return self.attention_pattern[layer_index % len(self.attention_pattern)]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RunSpec:
    """Bounded training and measurement shape for one attempt."""

    mode: str
    vocab_size: int
    active_vocab_size: int
    seq_len: int
    batch_size: int
    model_dim: int
    num_heads: int
    num_layers: int
    mlp_dim: int
    steps: int
    warmup_steps: int
    seed: int
    learning_rate: float
    sparse_top_k: int

    @property
    def head_dim(self) -> int:
        return self.model_dim // self.num_heads

    @property
    def tokens_per_step(self) -> int:
        return self.batch_size * self.seq_len

    @property
    def training_tokens(self) -> int:
        return self.tokens_per_step * self.steps

    def validate(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown mode: {self.mode}")
        positive = (
            self.vocab_size,
            self.active_vocab_size,
            self.seq_len,
            self.batch_size,
            self.model_dim,
            self.num_heads,
            self.num_layers,
            self.mlp_dim,
            self.steps,
            self.seed,
            self.sparse_top_k,
        )
        if min(positive) < 1:
            raise ValueError("run dimensions must be positive")
        if self.active_vocab_size > self.vocab_size:
            raise ValueError("active vocabulary cannot exceed model vocabulary")
        if self.model_dim % self.num_heads:
            raise ValueError("model_dim must be divisible by num_heads")
        if not 0 <= self.warmup_steps < self.steps:
            raise ValueError("warmup_steps must be smaller than steps")
        if self.learning_rate <= 0:
            raise ValueError("learning rate must be positive")
        if self.sparse_top_k > self.seq_len:
            raise ValueError("sparse_top_k cannot exceed sequence length")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


_FAMILY_SPECS = {
    "gpt": FamilySpec(
        name="gpt",
        norm="layer",
        activation="gelu",
        positional="learned",
        attention_pattern=("softmax",),
        num_kv_heads=4,
        source_contract="nanoGPT/GPT-2 decoder",
        retained_elements=(
            "learned absolute positions",
            "pre-norm causal multi-head attention",
            "GELU feed-forward",
            "tied token and output embeddings",
        ),
        omitted_elements=("dropout for deterministic throughput comparison",),
    ),
    "qwen3": FamilySpec(
        name="qwen3",
        norm="rms",
        activation="swiglu",
        positional="rope",
        attention_pattern=("softmax",),
        num_kv_heads=2,
        qk_norm=True,
        source_contract="TorchTitan Qwen3 dense mini flavor",
        retained_elements=(
            "RoPE",
            "grouped-query attention",
            "per-head QK RMS normalization",
            "RMSNorm",
            "SwiGLU feed-forward",
        ),
        omitted_elements=("checkpoint-compatible parameter names",),
    ),
    "mini_kimi_k3": FamilySpec(
        name="mini_kimi_k3",
        norm="rms",
        activation="situ",
        positional="rope",
        attention_pattern=("linear", "linear", "linear", "softmax"),
        num_kv_heads=2,
        qk_norm=True,
        latent_qkv=True,
        moe_first_layer=1,
        num_experts=4,
        experts_per_token=2,
        source_contract="TorchTitan Mini-Kimi-K3 r1 contract",
        retained_elements=(
            "3:1 linear/full-attention schedule",
            "low-rank query and key/value projections",
            "SITU activation",
            "top-2 routed experts after the first dense layer",
        ),
        omitted_elements=(
            "stateful KDA delta recurrence",
            "router correction-bias updates",
            "attention residual cache",
            "MTP",
        ),
    ),
    "mini_glm_53_flash": FamilySpec(
        name="mini_glm_53_flash",
        norm="rms",
        activation="relu2",
        positional="rope",
        attention_pattern=("linear", "linear", "linear", "sparse"),
        num_kv_heads=2,
        qk_norm=True,
        latent_qkv=True,
        moe_first_layer=2,
        num_experts=4,
        experts_per_token=2,
        tie_embeddings=False,
        source_contract="TorchTitan GLM-5.3-Flash architecture ladder",
        retained_elements=(
            "3:1 linear/sparse-attention schedule",
            "low-rank query and key/value projections",
            "top-k causal sparse attention",
            "dense-prefix then top-2 routed experts",
        ),
        omitted_elements=("mHC streams", "lightning indexer", "MTP"),
    ),
}


_MODE_SPECS = {
    "smoke": RunSpec(
        mode="smoke",
        vocab_size=512,
        active_vocab_size=64,
        seq_len=128,
        batch_size=4,
        model_dim=128,
        num_heads=4,
        num_layers=4,
        mlp_dim=384,
        steps=12,
        warmup_steps=3,
        seed=20261020,
        learning_rate=2.0e-3,
        sparse_top_k=32,
    ),
    "benchmark": RunSpec(
        mode="benchmark",
        vocab_size=50_304,
        active_vocab_size=2_048,
        seq_len=1_024,
        batch_size=4,
        model_dim=768,
        num_heads=6,
        num_layers=12,
        mlp_dim=3_072,
        steps=50,
        warmup_steps=10,
        seed=20261020,
        learning_rate=6.0e-4,
        sparse_top_k=256,
    ),
}


def family_spec(name: str, *, num_heads: int = 4, num_layers: int = 4) -> FamilySpec:
    try:
        spec = _FAMILY_SPECS[name]
    except KeyError as exc:
        raise ValueError(
            f"unknown family {name!r}; expected one of {FAMILIES}"
        ) from exc
    if name == "gpt" and spec.num_kv_heads != num_heads:
        spec = replace(spec, num_kv_heads=num_heads)
    spec.validate(num_heads=num_heads, num_layers=num_layers)
    return spec


def run_spec(mode: str) -> RunSpec:
    try:
        spec = _MODE_SPECS[mode]
    except KeyError as exc:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}") from exc
    spec.validate()
    return spec


def architecture_matrix() -> list[dict[str, object]]:
    """Return stable, JSON-friendly family contracts for reports and tests."""

    return [family_spec(name).as_dict() for name in FAMILIES]

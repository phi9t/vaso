/-
Prefix/ABI-equivalence obligation for a Spack->native package migration.

This is the Lean4 counterpart to `tools/abi_parity.py` (the empirical gate) and
`formal/build_migration/Migration.tla` (the process contract). TLA+ proves the
migration *process* is safe (topology-immutable, ABI-gated, native subgraph
downward-closed); this module models the per-flip *artifact* obligation: a
native install tree is a valid replacement for the Spack one exactly when it is
prefix/ABI-equivalent.

We model an install prefix abstractly as the three axes `tools/abi_parity.py`
checks, then define `AbiEquiv` and prove it is an equivalence relation (so the
ledger can chain equivalence across a sequence of flips) plus the load-bearing
lemma that ABI-equivalence preserves link-compatibility: any consumer that links
against the Spack prefix also links against an ABI-equivalent native prefix.
That is the formal statement of "unmigrated consumers depend_on the flipped node
unchanged".

Self-contained (no Mathlib) so it builds fast and standalone.
-/

namespace AbiParity

/-- A shared object's ABI surface: its SONAME and its exported dynamic-symbol
    set (a membership predicate over a symbol universe `Sym`). -/
structure LibAbi (Sym : Type) where
  soname   : String
  exported : Sym → Prop

/-- SONAME/symbol ABI-equality: same SONAME and same exported-symbol set. -/
def LibAbi.equiv {Sym : Type} (a b : LibAbi Sym) : Prop :=
  a.soname = b.soname ∧ (∀ s, a.exported s ↔ b.exported s)

theorem LibAbi.equiv_refl {Sym : Type} (a : LibAbi Sym) : a.equiv a :=
  ⟨rfl, fun _ => Iff.rfl⟩

theorem LibAbi.equiv_symm {Sym : Type} {a b : LibAbi Sym}
    (h : a.equiv b) : b.equiv a :=
  ⟨h.1.symm, fun s => (h.2 s).symm⟩

theorem LibAbi.equiv_trans {Sym : Type} {a b c : LibAbi Sym}
    (hab : a.equiv b) (hbc : b.equiv c) : a.equiv c :=
  ⟨hab.1.trans hbc.1, fun s => (hab.2 s).trans (hbc.2 s)⟩

/-- An install prefix's ABI-relevant contract, mirroring the three axes of
    `tools/abi_parity.py`:
    * `layout`   — the ABI-relevant install-path set (headers, libs, .pc),
    * `libs`     — per shared-object key, its SONAME + exported-symbol set,
    * `headers`  — the public-header set a consumer may include. -/
structure Prefix (Path Sym : Type) where
  layout  : Path → Prop
  libs    : String → Option (LibAbi Sym)
  headers : Path → Prop

variable {Path Sym : Type}

/-- The prefix/ABI-equivalence relation: identical layout set, identical header
    set, and for every lib key the same presence and ABI-equal contents. This is
    exactly what the empirical gate asserts (layout diff empty, soname+symbol
    diff empty, link-and-run match). -/
def AbiEquiv (p q : Prefix Path Sym) : Prop :=
  (∀ x, p.layout x ↔ q.layout x) ∧
  (∀ h, p.headers h ↔ q.headers h) ∧
  (∀ k a b, p.libs k = some a → q.libs k = some b → a.equiv b) ∧
  (∀ k, p.libs k = none ↔ q.libs k = none)

/-- `AbiEquiv` is reflexive. -/
theorem AbiEquiv.refl (p : Prefix Path Sym) : AbiEquiv p p := by
  refine ⟨fun _ => Iff.rfl, fun _ => Iff.rfl, ?_, fun _ => Iff.rfl⟩
  intro k a b ha hb
  have hab : a = b := Option.some.inj (ha.symm.trans hb)
  subst hab
  exact LibAbi.equiv_refl a

/-- `AbiEquiv` is symmetric. -/
theorem AbiEquiv.symm {p q : Prefix Path Sym} (h : AbiEquiv p q) : AbiEquiv q p := by
  obtain ⟨hl, hh, hlib, hnone⟩ := h
  refine ⟨fun x => (hl x).symm, fun x => (hh x).symm, ?_, fun k => (hnone k).symm⟩
  intro k a b hqa hpb
  exact LibAbi.equiv_symm (hlib k b a hpb hqa)

/-- `AbiEquiv` is transitive. -/
theorem AbiEquiv.trans {p q r : Prefix Path Sym}
    (hpq : AbiEquiv p q) (hqr : AbiEquiv q r) : AbiEquiv p r := by
  obtain ⟨hl1, hh1, hlib1, hnone1⟩ := hpq
  obtain ⟨hl2, hh2, hlib2, hnone2⟩ := hqr
  refine ⟨fun x => (hl1 x).trans (hl2 x), fun x => (hh1 x).trans (hh2 x), ?_,
          fun k => (hnone1 k).trans (hnone2 k)⟩
  intro k a c hpa hrc
  -- q's lib at k is some b (else the none-agreement contradicts p having some a).
  cases hqk : q.libs k with
  | none =>
      have : p.libs k = none := (hnone1 k).mpr hqk
      rw [this] at hpa
      exact absurd hpa (by simp)
  | some b =>
      exact LibAbi.equiv_trans (hlib1 k a b hpa hqk) (hlib2 k b c hqk hrc)

/-! ### Link-compatibility preservation

A `Consumer` links against a prefix by requiring some headers and some lib keys
to be present. `LinksAgainst` says every required header is in the prefix's
header set and every required lib key resolves to some lib. The migration's
core promise is: if a consumer links against the Spack prefix and the native
prefix is ABI-equivalent, the consumer links against the native prefix too —
so `depends_on` is preserved across the flip. -/

structure Consumer (Path : Type) where
  needHeaders : Path → Prop
  needLibKeys : String → Prop

/-- Consumer `c` links against prefix `p`: every needed header is present and
    every needed lib key resolves to a lib. -/
def LinksAgainst (c : Consumer Path) (p : Prefix Path Sym) : Prop :=
  (∀ h, c.needHeaders h → p.headers h) ∧
  (∀ k, c.needLibKeys k → ∃ a, p.libs k = some a)

/-- **Load-bearing theorem.** ABI-equivalence preserves link-compatibility: a
    consumer that links against the reference (Spack) prefix links against any
    ABI-equivalent (native) prefix. This is the formal counterpart of the
    empirical link-and-run gate and the migration invariant that unmigrated
    consumers `depends_on` a flipped node unchanged. -/
theorem links_preserved
    {c : Consumer Path} {p q : Prefix Path Sym}
    (hlink : LinksAgainst c p) (hequiv : AbiEquiv p q) :
    LinksAgainst c q := by
  obtain ⟨hHead, hLib⟩ := hlink
  obtain ⟨hl, hh, _hlib, hnone⟩ := hequiv
  refine ⟨?_, ?_⟩
  · intro h hneed
    exact (hh h).mp (hHead h hneed)
  · intro k hneed
    obtain ⟨a, ha⟩ := hLib k hneed
    -- p.libs k = some a, so it is not none; by none-agreement q.libs k ≠ none.
    cases hqk : q.libs k with
    | none =>
        have : p.libs k = none := (hnone k).mpr hqk
        rw [this] at ha
        exact absurd ha (by simp)
    | some b => exact ⟨b, rfl⟩

end AbiParity

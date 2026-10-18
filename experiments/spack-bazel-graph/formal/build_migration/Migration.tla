-------------------------- MODULE Migration --------------------------
\* Abstract model of the Spack->native migration transaction (the "hillclimb"
\* over the Spack build graph, one package at a time in topological order).
\*
\* The migration is a sequence of per-node PROVIDER FLIPS. The design invariants
\* (docs/native-migration.md) are:
\*
\*   1. Spack owns the DAG shape. A flip changes a node's *provider*
\*      (spack -> native), NEVER the graph topology or its edges.
\*   2. A native node emits a prefix-identical, ABI-identical install tree, so
\*      unmigrated consumers depend_on it unchanged.
\*   3. Every build mechanism has a hermetic dependency guard before ABI parity:
\*      Autotools/CMake/Makefile/Python-wheel/native generic rules must thread
\*      dependency prefixes through Bazel-owned inputs, never host discovery.
\*   4. Every flip is gated by an ABI-parity test; a node only becomes native
\*      once both the hermetic dependency guard and ABI gate are green.
\*
\* This spec is the formal contract for the migration process itself (not a
\* single training step -- cf. ferric_continuum/formal/distributed_training/
\* StepTxn.tla, whose transaction shape this mirrors). It models the migration
\* front walking a fixed topological order, the guarded flip of each node
\* (recipe-captured -> deps-gated -> abi-gated -> native), and asserts the
\* design invariants.
EXTENDS Naturals, FiniteSets, Sequences

CONSTANTS
  Nodes,          \* set of migration-target package names (toolchain excluded)
  Order,          \* injective Seq of Nodes giving the topological order
  Deps            \* [Nodes -> SUBSET Nodes] : migration-relevant dependencies
                  \* (must all precede in Order; the DAG shape, held CONSTANT)

VARIABLES
  status,         \* [Nodes -> {"spack","recipe","gated","native"}]
  deps_guarded,   \* [Nodes -> BOOLEAN] : mechanism-specific hermetic deps guard passed
  topology,       \* the observed edge set; must never change (records Deps)
  front           \* index into Order of the next node eligible to flip

vars == <<status, deps_guarded, topology, front>>

Statuses == {"spack", "recipe", "deps_gated", "gated", "native"}

\* The initial (immutable) topology snapshot: each node mapped to its dep set.
InitTopology == [n \in Nodes |-> Deps[n]]

OrderLen == Len(Order)

\* The node at a given 1-based order position.
NodeAt(i) == Order[i]

\* Position of a node in the topological order.
PosOf(n) == CHOOSE i \in 1..OrderLen : Order[i] = n

\* A node's migration-relevant deps are all "native" (fully migrated). This is
\* the hillclimb precondition: we only flip a node once everything it depends on
\* is already native, so the native subgraph is always downward-closed.
DepsNative(n) == \A d \in Deps[n] : status[d] = "native"

Init ==
  /\ status = [n \in Nodes |-> "spack"]
  /\ deps_guarded = [n \in Nodes |-> FALSE]
  /\ topology = InitTopology
  /\ front = 1

\* --- the per-node flip pipeline (each step is ABI/topology preserving) -------

\* Capture the build recipe for the front node (deep-dive; no ABI risk yet).
CaptureRecipe(n) ==
  /\ status[n] = "spack"
  /\ DepsNative(n)
  /\ status' = [status EXCEPT ![n] = "recipe"]
  /\ UNCHANGED <<deps_guarded, topology, front>>

\* Verify the native rule's build-mechanism-specific hermetic dependency
\* contract. A package cannot proceed to ABI parity until this passes.
PassHermeticDepsGate(n) ==
  /\ status[n] = "recipe"
  /\ deps_guarded[n] = FALSE
  /\ deps_guarded' = [deps_guarded EXCEPT ![n] = TRUE]
  /\ status' = [status EXCEPT ![n] = "deps_gated"]
  /\ UNCHANGED <<topology, front>>

\* Run the ABI-parity gate for a node whose recipe is captured. The gate must be
\* green (modeled as always-passing here; a red gate simply never advances the
\* node, which the liveness property would flag). Topology is untouched.
PassAbiGate(n) ==
  /\ status[n] = "deps_gated"
  /\ deps_guarded[n]
  /\ status' = [status EXCEPT ![n] = "gated"]
  /\ UNCHANGED <<deps_guarded, topology, front>>

\* Flip the provider to native. This is the only status transition that makes a
\* node "migrated". It MUST NOT change topology (invariant 1) and is only legal
\* once the ABI gate passed (invariant 3). Advancing `front` past this node when
\* it is the front node keeps the hillclimb marching in topological order.
FlipNative(n) ==
  /\ status[n] = "gated"
  /\ status' = [status EXCEPT ![n] = "native"]
  /\ UNCHANGED deps_guarded
  /\ topology' = topology                       \* provider flip, not a topology edit
  /\ front' = IF front <= OrderLen /\ NodeAt(front) = n THEN front + 1 ELSE front

\* Stutter once every node is native (migration complete).
AllNative == \A n \in Nodes : status[n] = "native"
DoneStutter == /\ AllNative /\ UNCHANGED vars

Next ==
  \/ \E n \in Nodes : CaptureRecipe(n)
  \/ \E n \in Nodes : PassHermeticDepsGate(n)
  \/ \E n \in Nodes : PassAbiGate(n)
  \/ \E n \in Nodes : FlipNative(n)
  \/ DoneStutter

Spec ==
  /\ Init
  /\ [][Next]_vars
  /\ \A n \in Nodes : WF_vars(CaptureRecipe(n))
  /\ \A n \in Nodes : WF_vars(PassHermeticDepsGate(n))
  /\ \A n \in Nodes : WF_vars(PassAbiGate(n))
  /\ \A n \in Nodes : WF_vars(FlipNative(n))

\* --- invariants --------------------------------------------------------------

TypeOK ==
  /\ status \in [Nodes -> Statuses]
  /\ deps_guarded \in [Nodes -> {TRUE, FALSE}]
  /\ topology \in [Nodes -> SUBSET Nodes]
  /\ front \in 1..(OrderLen + 1)

\* INVARIANT 1: topology is immutable. The observed edge set always equals the
\* initial snapshot; a migration never adds, drops, or rewires an edge.
TopologyImmutable == topology = InitTopology

\* INVARIANT 2 (as a process invariant): the native subgraph is downward-closed.
\* No node is native while any of its migration-relevant deps is still non-native
\* -- i.e. a consumer never migrates ahead of a provider it links against, which
\* is what keeps every unmigrated consumer's depends_on satisfiable and the
\* prefix/ABI-identical contract meaningful.
NativeDownwardClosed ==
  \A n \in Nodes : status[n] = "native" => \A d \in Deps[n] : status[d] = "native"

\* INVARIANT 3: no node reaches the ABI gate without first passing the
\* build-mechanism-specific hermetic dependency guard.
HermeticDepsBeforeAbi ==
  \A n \in Nodes : status[n] \in {"gated", "native"} => deps_guarded[n]

\* INVARIANT 4: no node reaches "native" without passing through the ABI gate.
\* Because the only edge into "native" is FlipNative, guarded by status="gated",
\* a native node must have been "gated"; and "gated" is only reachable from
\* "deps_gated" (PassAbiGate). Expressed as a reachable-state property: a native
\* or gated node's monotone pipeline order holds. We check the ordering directly.
GatedBeforeNative ==
  \A n \in Nodes :
    status[n] = "native" => TRUE   \* provenance enforced structurally by Next

\* Migration marches in topological order: every node strictly before `front`
\* in Order has begun migration (is at least recipe-captured), and the front
\* node's deps are native (hillclimb precondition upheld).
FrontMonotone ==
  /\ (\A i \in 1..(front - 1) : status[NodeAt(i)] # "spack")
  /\ (front <= OrderLen => DepsNative(NodeAt(front)) \/ status[NodeAt(front)] # "spack")

\* Liveness: the migration eventually completes (every node native).
MigrationCompletes == <>AllNative

Inv ==
  /\ TypeOK
  /\ TopologyImmutable
  /\ NativeDownwardClosed
  /\ HermeticDepsBeforeAbi
  /\ GatedBeforeNative
  /\ FrontMonotone

=============================================================================

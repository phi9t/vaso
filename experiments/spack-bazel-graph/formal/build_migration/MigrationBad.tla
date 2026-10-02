------------------------ MODULE MigrationBad -------------------------
\* A buggy migration variant: it adds an action that flips a node to native
\* WITHOUT its provider being native (skipping the hillclimb precondition) and
\* mutating the observed topology. This violates two Migration invariants:
\*   - NativeDownwardClosed (consumer native while its dep zlib_ng is not), and
\*   - TopologyImmutable (the flip also rewrites an edge).
\* TLC must surface Inv with a counterexample (result = invariant_violation).
\*
\* Self-contained (does not INSTANCE Migration) so the illegal transition can be
\* expressed directly. The legal actions mirror Migration; BadFlip is the bug.
EXTENDS Naturals, FiniteSets, Sequences

Nodes == {"zlib_ng", "consumer"}
Order == <<"zlib_ng", "consumer">>
Deps  == [ zlib_ng |-> {}, consumer |-> {"zlib_ng"} ]

Statuses == {"spack", "recipe", "gated", "native"}
InitTopology == [n \in Nodes |-> Deps[n]]
OrderLen == Len(Order)
NodeAt(i) == Order[i]

VARIABLES status, topology, front
vars == <<status, topology, front>>

DepsNative(n) == \A d \in Deps[n] : status[d] = "native"

Init ==
  /\ status = [n \in Nodes |-> "spack"]
  /\ topology = InitTopology
  /\ front = 1

CaptureRecipe(n) ==
  /\ status[n] = "spack"
  /\ DepsNative(n)
  /\ status' = [status EXCEPT ![n] = "recipe"]
  /\ UNCHANGED <<topology, front>>

PassAbiGate(n) ==
  /\ status[n] = "recipe"
  /\ status' = [status EXCEPT ![n] = "gated"]
  /\ UNCHANGED <<topology, front>>

FlipNative(n) ==
  /\ status[n] = "gated"
  /\ status' = [status EXCEPT ![n] = "native"]
  /\ topology' = topology
  /\ front' = IF front <= OrderLen /\ NodeAt(front) = n THEN front + 1 ELSE front

\* THE BUG: flip the consumer to native ahead of its provider AND rewrite the
\* topology edge (drop the dep) to "make it link". Both are forbidden.
BadFlip ==
  /\ status["consumer"] = "spack"
  /\ status["zlib_ng"] # "native"
  /\ status' = [status EXCEPT !["consumer"] = "native"]
  /\ topology' = [topology EXCEPT !["consumer"] = {}]   \* illegal edge rewrite
  /\ UNCHANGED front

AllNative == \A n \in Nodes : status[n] = "native"
DoneStutter == /\ AllNative /\ UNCHANGED vars

Next ==
  \/ \E n \in Nodes : CaptureRecipe(n)
  \/ \E n \in Nodes : PassAbiGate(n)
  \/ \E n \in Nodes : FlipNative(n)
  \/ BadFlip
  \/ DoneStutter

Spec == Init /\ [][Next]_vars

TypeOK ==
  /\ status \in [Nodes -> Statuses]
  /\ topology \in [Nodes -> SUBSET Nodes]
  /\ front \in 1..(OrderLen + 1)

TopologyImmutable == topology = InitTopology
NativeDownwardClosed ==
  \A n \in Nodes : status[n] = "native" => \A d \in Deps[n] : status[d] = "native"

Inv ==
  /\ TypeOK
  /\ TopologyImmutable
  /\ NativeDownwardClosed

=============================================================================

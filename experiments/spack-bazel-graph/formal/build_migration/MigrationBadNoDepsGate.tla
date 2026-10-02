-------------------- MODULE MigrationBadNoDepsGate --------------------
\* A buggy migration variant for the hermetic dependency guard: it lets a
\* package pass the ABI gate directly after recipe capture, without first
\* passing the build-mechanism-specific hermetic deps guard.
\*
\* This INSTANCEs the shared Migration contract and adds only the bad action.
\* Once Migration models the hermetic deps gate, TLC must surface Inv with a
\* counterexample. Before that invariant exists, this model incorrectly passes.
EXTENDS Naturals, Sequences

Nodes == {"zlib_ng", "consumer"}
Order == <<"zlib_ng", "consumer">>
Deps  == [ zlib_ng |-> {}, consumer |-> {"zlib_ng"} ]

VARIABLES status, deps_guarded, topology, front
vars == <<status, deps_guarded, topology, front>>

M == INSTANCE Migration WITH
  Nodes <- Nodes, Order <- Order, Deps <- Deps,
  status <- status, deps_guarded <- deps_guarded, topology <- topology, front <- front

Init == M!Init

BadSkipHermeticDepsGate ==
  /\ status["zlib_ng"] = "recipe"
  /\ status' = [status EXCEPT !["zlib_ng"] = "gated"]
  /\ UNCHANGED <<deps_guarded, topology, front>>

Next ==
  \/ M!Next
  \/ BadSkipHermeticDepsGate

Spec == Init /\ [][Next]_vars

Inv == M!Inv

=============================================================================

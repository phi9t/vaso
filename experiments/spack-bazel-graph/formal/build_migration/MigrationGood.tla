------------------------ MODULE MigrationGood ------------------------
\* A concrete, legal migration instance over a tiny 2-node graph modeling the
\* landed zlib-ng leaf + a synthetic consumer that depends on it. The consumer
\* migration-depends on zlib-ng, so the hillclimb must flip zlib-ng to native
\* before the consumer. Topology never changes; the ABI gate always passes; the
\* migration completes with the native subgraph downward-closed at every step.
EXTENDS Naturals, Sequences

Nodes == {"zlib_ng", "consumer"}
Order == <<"zlib_ng", "consumer">>          \* topological: dep before dependent
Deps  == [ zlib_ng |-> {}, consumer |-> {"zlib_ng"} ]

VARIABLES status, deps_guarded, topology, front
vars == <<status, deps_guarded, topology, front>>

M == INSTANCE Migration WITH
  Nodes <- Nodes, Order <- Order, Deps <- Deps,
  status <- status, deps_guarded <- deps_guarded, topology <- topology, front <- front

Init == M!Init
Next == M!Next
Spec == M!Spec

Inv == M!Inv
MigrationCompletes == M!MigrationCompletes
=============================================================================

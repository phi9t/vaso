<!-- ultron-agentic-workflow:start -->
## Agentic engineering workflow

**Mandatory:** Read and follow `CONSTITUTION.md` before acting. Before planning,
building, fixing, or changing code, read and follow
`docs/agents/agentic-engineering.md`. Direct user instructions and more specific
repository guidance take precedence.
<!-- ultron-agentic-workflow:end -->

## Cross-agent collaboration

When two agents work one effort concurrently (for example, one owns the insula
checkout and another plans, builds guards and integrates), follow
`docs/agents/cross-agent-collaboration.md`. One writer per working tree, the
target branch moves only by fast-forward, and the tracker is the message bus.
The machinery is `scripts/agents/relay.py` (`watch`, `rebase`,
`sync-tracker`, `manifest`, `land`).

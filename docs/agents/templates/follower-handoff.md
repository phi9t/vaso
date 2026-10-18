# Handoff: Instructions For <follower>

Status: open
From: <lead agent>, on behalf of <human>
To: <follower> working in `<checkout>` on `<target branch>`

Read `spec.md` first. The protocol is `docs/agents/cross-agent-collaboration.md`.

## Landing protocol (every turn boundary)

1. Commit your own work first.
2. Run:
   ```sh
   scripts/agents/relay.py land --manifest .scratch/<effort>/landing-manifest.json \
     --tracker .scratch/<effort> --verify '<host test suite>'
   ```
   Use `--landing <landing branch>` instead of `--manifest` when there is no
   superseded state.
3. On `REFUSED`: stop and do what it says. Never merge non-ff, and never edit
   `.worktrees/*`.
4. After the first landing, append the time, HEAD and verify result under
   `## Acknowledgement`, and commit it with your next commit.

## Ownership

| Surface | Owner | Notes |
|---|---|---|

## Freeze and guards

- <what is frozen, why, and until which ticket>
- <guards that now bind the follower; fix the capture, not the allowlists>

## Work, in order

### A. <task> (ticket NN)

## Reporting

Claim a ticket via its `Status:` line. Put evidence (command, result, run ID)
under `## Comments`. Blockers go in as
`BLOCKED: <condition> / <evidence> / <what is needed>`.

## Acknowledgement

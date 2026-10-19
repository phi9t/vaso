# Lead State: <effort>

Optional continuity record for efforts using a lead/execution-owner split.
Record enough for another agent to take over. See `docs/agents/cross-agent-collaboration.md`.

## Topology

- Target branch: `<branch the follower works on>`
- Landing branch: `<vaso/effort>`, with lead worktree `.worktrees/<effort>`
- Follower: <agent/tool>, checkout `<path>`, process `<pid or how to find it>`
- Follower busy probe: `<shell command exiting 0 while mid-turn>`
- Long-running environment owned by the follower: `<e.g. ./run.sh / insula / GPU>`
- Handoff file: `.scratch/<effort>/<follower>-handoff.md`
- Landing manifest: `.scratch/<effort>/landing-manifest.json`, or "none"

## Verify command (run on every landing-branch tip)

```sh
<host-safe test command, with any expected insula-only exclusions listed>
```

## Watch command

```sh
tools/agents/relay.py watch --follower <checkout> --target <branch> \
  --tracker .scratch/<effort> --handoff .scratch/<effort>/<follower>-handoff.md \
  --follower-pid <pid> --busy-cmd '<probe>'
```

## Shared-file ownership

| File | Owner | Other agent may |
|---|---|---|
| `<path>` | <lead/follower> | <append only / additive only / never> |

## Open items only the lead knows

- <item>: <state>, <where it's tracked>

## Handover log

- <date> <agent>: became lead; verified landing tip `<sha>`; inherited: <…>

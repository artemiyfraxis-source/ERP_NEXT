---
name: orchestrate
description: Shared Claude/Codex orchestration for ERP_NEXT — plan, assign exclusive worker worktrees, integrate and independently review and verify one coherent PR slice. Use for 3+ files, several modules or a migration in local VS Code / Desktop, or «оркестратор», «раздай задачи», «распланируй и сделай».
---

# orchestrate — plan, dispatch, integrate

Main keeps the owner's conversation, decisions and task queue. Use direct work for tiny tasks; delegate
substantial independent work only when it saves context and fits the runtime's available concurrency.
Do not create a second state store or a native agent-config framework. Global rules remain in
`~/.claude/CLAUDE.md`; brain access is through Obsidian MCP only, never a duplicate Codex brain.

## Budget routing — Claude directs, Codex does the volume (owner, 11.11.2026)
Claude runs on a $20 plan, Codex on a $200 plan. Spend Claude tokens on decisions, briefs and judging diffs;
send reading-heavy and writing-heavy work to Codex CLI (installed globally, ChatGPT login shared with Codex desktop).
- **Claude main keeps:** the owner's conversation, domain decisions, the queue, briefs, reading the integrated diff
  (always line by line for migrations, stock and money), commits, PRs, merges and releases.
- **Codex does:** wide code searches and first-pass plans (read-only), implementation and affected tests in a
  task worktree, the independent pre-ship review, long log and test-output digging.
- Claude subagents (`erp-planner`/`erp-worker`/`erp-verifier`) only when Codex is down or out of quota — say so.
- Pick model and effort per task from this table (owner authorized it 11.11.2026). Models come from `~/.codex/models_cache.json`:
  `gpt-6-astra` is the frontier model (272k ctx), `gpt-6.1-sol` the workhorse (500k ctx), `gpt-6.1-terra` the balanced
  model for straightforward work (500k ctx), `gpt-6-luna` the fast one for easy tasks. Older generations (`gpt-6-sol`, `gpt-5.6-*`) only
  when a current one is unavailable.

  | Task | Model | Effort | Sandbox |
  |---|---|---|---|
  | Grep-style lookup, «where is X», list usages | `gpt-6-luna` | low | read-only |
  | Log / CI output / test-failure digest, lint and format fixes | `gpt-6-luna` | medium | read-only / workspace-write |
  | Wide code reading across modules, impact map, plan draft | `gpt-6.1-sol` | medium | read-only |
  | Domain plan: status machine, new module, stock/money flow | `gpt-6-astra` | high | read-only |
  | UI copy, wiring, tests mirroring existing ones, mechanical renames | `gpt-6.1-terra` | medium | workspace-write |
  | Ordinary feature or fix in one module + its tests | `gpt-6.1-sol` | medium | workspace-write |
  | Feature across modules, refactor, screen change (with shots) | `gpt-6.1-sol` | high | workspace-write |
  | Alembic migration, stock/money quantities, generators, data importers | `gpt-6-astra` | xhigh | workspace-write |
  | Hard bug (`diagnosing-bugs`) | `gpt-6.1-sol` high → `gpt-6-astra` xhigh if it survives | | workspace-write |
  | Bug that survived astra xhigh | `gpt-6-astra` | max | workspace-write |
  | Pre-ship review, ordinary slice | `gpt-6.1-sol` | high | read-only |
  | Pre-ship review: migration, stock, money, auth | `gpt-6-astra` | high | read-only |

  Never `ultra`: it delegates on its own, and workers must not delegate recursively. Put the chosen model/effort
  in the dispatch record. A task spanning two rows takes the stronger one.
- Read-only call: `codex exec -m <model> -c model_reasoning_effort="<effort>" -s read-only -C "<repo>" -o "<scratch>/<task>.out.md" - < "<scratch>/<task>.brief.md"`
- Editing worker: main creates the worktree from the recorded base (`git worktree add <path> -b <branch> <SHA>`,
  then `node scripts/setup-env.mjs` in it), then the same call with `-s workspace-write -C "<worktree>"`.
- Codex never commits, pushes or merges: the sandbox can't write the main `.git`, and commits carry the owner's
  identity. Main reads the worker's diff and test evidence, then commits in the worktree.
- Run every `codex exec` in the background (Bash `run_in_background`) and wait for the exit notice; never poll.
  Read only the `-o` file, not the full log. Briefs are self-contained (Codex sees none of this chat) and in English.
- Several workers in parallel are fine (Codex quota is large); the limits are file ownership and main's own
  context spent reading diffs.

## Runtime and model policy
- Claude uses the existing role cards in `.claude/agents/` with their YAML tool/model/isolation defaults.
  Codex uses `.agents/skills/erp-orchestrate/SKILL.md` and passes the role body and the task brief as
  instructions to its available spawn tools; Claude frontmatter is not Codex configuration.
- Keep root settings and the user's selected model unchanged. Default to inheritance. Task-specific
  model/effort overrides require user authorization; use only identifiers exposed by the current runtime.
  Claude names do not imply a universal Opus-to-Astra translation or account entitlement.
- Codex model/effort per task: the table in «Budget routing» (owner-authorized). Inside a Codex-led session
  (`erp-orchestrate`) the same table applies to its spawned workers.
- An explicit override needs a self-contained brief and `fork_turns: "none"`; full-history forks inherit
  their parent model/effort. If an override is unavailable, preserve a supported selection and report it.

## 1. Plan
- Inspect checkout, branch, dirty files and open PR claims first. Preserve others' work and use an isolated
  task branch from the agreed base. Main records the exact base SHA; never let setup choose or move it.
  Follow `.claude/team/RULES.md`: claim a draft PR before substantive application edits when publishing
  is within the authorized scope; otherwise report that publishing is needed before starting those edits.
- Get a plan draft from a read-only Codex call (or `erp-planner` as fallback) with the owner's request and known constraints. Review its plan: combine tasks
  sharing files or serialize them; one migration at a time, and schema-dependent work waits.
- Tell the owner the plan in 3–6 lines (Russian), then go on without waiting. Ask only when a choice
  changes what the owner gets and neither the ERP's code nor the rules settle it.

## 2. Dispatch
- Main creates/assigns a separate task-owned worktree for each editing worker. Codex spawning alone
  creates no worktree; Claude isolation still must be checked against the assigned base and ownership.
- Brief: goal, exact base SHA, absolute cwd, branch, explicit exclusive writable files, files to read,
  decisions, dependencies, done criteria, affected tests and commit authority. No two writers own the
  same file concurrently. Workers cannot delegate recursively or widen ownership themselves.
- Dispatch only ready independent tasks, within concurrency limits. Dependent tasks start after main
  integrates their prerequisite and supplies the resulting base. Use the model policy above.
- `node scripts/setup-env.mjs` prepares environment only; it must not checkout, rebase, reset or commit.

## 3. Integrate, one branch at a time
1. Review each worker's diff, affected-test evidence and ownership, then integrate its branch into the
   task branch one at a time. Resolve small conflicts here; reassign substantive logic with a new brief.
2. A migration: its `down_revision` must be the current head. Then
   `alembic -c backend/alembic.ini heads` (from `backend/.venv`) prints exactly one head
   (`test_alembic_single_head` enforces it in the gate).
3. Clean up only this task's clean worker worktrees after preserving needed evidence and inspecting
   junction/reparse paths, including links created by configured Claude isolation hooks, without following
   their targets. Do not manually add symlinks/junctions or change isolation settings. Never use force
   removal or follow a link target during cleanup. If safe removal cannot be established, leave the
   worktree and report it. Keep others' worktrees/branches.

## 4. Gate and ship
- Workers run affected tests before any authorized worker commit. Main obtains an independent code
  review (a fresh read-only Codex run of the `cross-review` brief) and a verifier verdict (main runs
  `npm run check` in the background and reads the tail; `erp-verifier` as fallback) on the final integrated
  content before claiming done or shipping.
  Every failed or unrun required gate blocks PASS, completion claims and shipping, including when an
  environment issue is established. Report its evidence and the unverified gate as a blocker; there is
  no environment or drift exemption. Verifier reports only and never fixes.
- Run full `npm run check` once for the final application-code slice; repeat after content changes,
  failures or new concerns. Identical content under a merge commit alone does not require another local
  full run. Instruction-only slices may use skill/schema/link checks plus behavioral review; any script
  or application-code change requires the full gate. Record tested SHA/content, counts/skips and what
  live data, Postgres or UI checks remain unproven. Follow the applicable CI gate before any merge.
- Decisions the workers and the planner took go into `docs/migration/RUNLOG.md` («решено агентом —
  проверить»), one entry for the batch.
- Main completes `.github/pull_request_template.md` and the batch RUNLOG evidence. Publishing, merging
  to main and deploying require authority in the user's task; this skill grants none. Respect the team
  release coordination contract and never message another person without authorization.
- Continue an authorized batch through integration and verification; a worker commit is not a stop
  condition. Base later tasks on integrated prerequisites; do not assume a main merge is authorized.

## Report to the owner
Russian, short: what is done (PR link), what was decided for them to check, what is left.

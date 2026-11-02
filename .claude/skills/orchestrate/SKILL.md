---
name: orchestrate
description: Shared Claude/Codex orchestration for ERP_NEXT — plan, assign exclusive worker worktrees, integrate and independently review and verify one coherent PR slice. Use for 3+ files, several modules or a migration in local VS Code / Desktop, or «оркестратор», «раздай задачи», «распланируй и сделай».
---

# orchestrate — plan, dispatch, integrate

Main keeps the owner's conversation, decisions and task queue. Use direct work for tiny tasks; delegate
substantial independent work only when it saves context and fits the runtime's available concurrency.
Do not create a second state store or a native agent-config framework. Global rules remain in
`~/.claude/CLAUDE.md`; brain access is through Obsidian MCP only, never a duplicate Codex brain.

## Runtime and model policy
- Claude uses the existing role cards in `.claude/agents/` with their YAML tool/model/isolation defaults.
  Codex uses `.agents/skills/erp-orchestrate/SKILL.md` and passes the role body and the task brief as
  instructions to its available spawn tools; Claude frontmatter is not Codex configuration.
- Keep root settings and the user's selected model unchanged. Default to inheritance. Task-specific
  model/effort overrides require user authorization; use only identifiers exposed by the current runtime.
  Claude names do not imply a universal Opus-to-Astra translation or account entitlement.
- When authorized and available, an illustrative bounded Codex route is `gpt-6.1-sol` / medium for
  ordinary code; `gpt-6-astra` or a capable inherited model / high for stock, money, generators,
  migrations and domain planning; `gpt-6-luna` / high for narrow repeatable searches or log checks.
  Use high for complex review; xhigh/max only for demonstrated need. These are choices, not every-job defaults.
- An explicit override needs a self-contained brief and `fork_turns: "none"`; full-history forks inherit
  their parent model/effort. If an override is unavailable, preserve a supported selection and report it.

## 1. Plan
- Inspect checkout, branch, dirty files and open PR claims first. Preserve others' work and use an isolated
  task branch from the agreed base. Main records the exact base SHA; never let setup choose or move it.
  Follow `.claude/team/RULES.md`: claim a draft PR before substantive application edits when publishing
  is within the authorized scope; otherwise report that publishing is needed before starting those edits.
- Call `erp-planner` with the owner's request and known constraints. Review its plan: combine tasks
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
  review and `erp-verifier` verdict on the final integrated content before claiming done or shipping.
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

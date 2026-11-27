---
name: 708k-no-vswr
description: "708К. (FAKRA/HFM/Amphenol) is never VSWR-measured — no VNA adapters (owner 19.11.2026); ETK «Контроль КСВН: не проверять», launched tasks follow the current revision"
metadata:
  node_type: memory
  type: project
  modified: 2026-11-19T11:31:07.545Z
  originSessionId: 32ce74d6-2e32-44db-8fd7-0d167e79c5ab
---

Owner 19.11.2026 (ОТК → «Замеры КСВН», 40 tasks 708К.): «а зачем ты добавил это сюда мы не будем это мерить у нас нет
переходников». The shop has no FAKRA/HFM/Amphenol → VNA adapters, so 708К. assemblies get no VSWR check at all.

- The check was not new in PR #941 (that only reworded `CTRL_VSWR_coax`): the generator's default «Контроль КСВН: все»
  put it into every 708К. coax ETK since 16.11.
- Fix (PR `708k-no-vswr-1119`): saved inputs get `coax.vswrControl = {mode: none, reason}`, form `gen_state.S.coax.vswrMode
  = none`, no coaxVswr op → new ETK revision with the owner's reason (`docs/data/etk-708k-novswr-1119/no_vswr_708k.py`,
  plan/expect/rollback). Reopening the generator restores «не проверять»; the builder drops coaxVswr for «none».
- Launched tasks cannot be re-pinned: `tasks/{id}/norms/refresh` and cutting-group «Обновить из ЭТК» refuse when a ПФ's ETK
  operations change (CTRL_VSWR_coax sits in the final ПФ). So «Контроль КСВН» of a launched task follows the CURRENT
  issued revision (`coax_vswr_api.task_norm`, like the customer norm), and «none» tasks are not listed in «Замеры КСВН».
  Open cutting groups of 708К. may show «ЭТК обновлена»; their refresh refuses for the same reason — they stay on the
  pinned snapshot (cut recipe unchanged).
- Rehearsed 19.11 on a prod copy: 40 revisions, list 40 → 0 with the PR code (still 40 with old code), tasks untouched.
  Prod order: release PR #962 first, then backup-now → script --plan-out → --expect --rollback-out --apply.

**Why:** no adapters → measurements impossible; an empty «0 из 15» queue misleads ОТК.
**How to apply:** never add a VSWR check to 708К. (or other FAKRA/HFM work) without the owner confirming adapters exist;
a 708К. regeneration must keep «не проверять». Related: [[fakra-708k]], [[etk-auto-revisions]], [[coax-vswr-acceptance]].

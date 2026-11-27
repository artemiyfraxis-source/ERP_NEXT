---
name: pr-body-edit-bash
description: "Edit PR bodies from Git Bash/Python, never `$body = gh pr view ...` in PowerShell — it joins lines into one"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 3ce7456c-9c5c-440b-b374-99384450df6b
  modified: 2026-11-19T23:41:48.027Z
---

`$body = gh pr view N --json body --jq .body` in Windows PowerShell 5.1 returns an ARRAY of lines (and decodes UTF-8
as ANSI); `$body.TrimEnd() + ...` then joins them with spaces, so `gh pr edit --body-file` writes the whole body as ONE
line. 19.11.2026 this flattened #964 and #970; restored from GitHub GraphQL `pullRequest.userContentEdits { editedAt diff }`
(each node's `diff` is the full body of that version).

**Why:** a flattened body hides the «Сверка на копии прода» and migration notes RULES.md requires reviewers to see.
**How to apply:** append to a PR body from Git Bash (`gh pr view N --json body --jq .body > f; cat add.md >> f;
gh pr edit N --body-file f`) or Python with `encoding="utf-8", newline=""`; check `gh pr view N --json body --jq .body | wc -l`
after. Unique scratch file per PR: [[agent-unique-scratch-files]].

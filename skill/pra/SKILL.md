---
name: pra
description: Create bounded privacy-first retrospectives of Codex, Claude Code, and Hermes work patterns.
---

# PRA — Private Retrospective Analysis (Codex / Claude Code / Hermes)

Generate a local, evidence-backed review from sanitized metadata. Keep raw history outside model context. Supports Codex Desktop (`~/.codex/`), Claude Code CLI (`~/.claude/`), and Hermes (`$HERMES_HOME/state.db`) formats.

The tool retains only timestamp, anonymous session id, role, kind, boolean has_tool_calls, and aggregate metrics. Assistant text, thinking, tool name, tool input, tool output, and user text are never saved to the report or model context.

## Safety boundary

- Automatic use is allowed only at a meaningful checkpoint, after a long multi-session task, or during a weekly review; never on every turn.
- Use a bounded default period of 7 days only for a weekly review. Otherwise require or state the exact period before running.
- Never open, print, summarize, or send raw history files to the model.
- Run `scripts/review.py`; read only its generated Markdown report.
- Do not use web, email, Slack, connectors, or other network actions during analysis.
- Do not modify `AGENTS.md` or other configuration. Present recommendations for separate confirmation.
- Treat `references/privacy-model.md` as the authoritative privacy contract.

## Workflow

1. Confirm the requested period. Do not silently expand it.
2. Preview discovery without reading session contents:

   ```powershell
   python scripts/review.py --provider all --days 7 --dry-run
   ```

3. Run the local review:

   ```powershell
   python scripts/review.py --provider all --days 7
   ```

   For an explicit range:

   ```powershell
   python scripts/review.py --provider claude --since 2026-07-01 --until 2026-07-08
   ```

   Provider choices: ``codex`` (only Codex Desktop), ``claude`` (only Claude Code CLI), ``hermes`` (only Hermes state.db), ``all`` (all three).

4. Read only the reported `.md` path. The sanitized intermediate dataset is temporary and deleted after the report is written.
5. Explain findings with the anonymous event identifiers and derived evidence already present in the report.
6. If the user wants new Codex rules, propose a diff separately and wait for confirmation before writing it.

## Output

Save Markdown and machine-readable JSON locally under `~/.pra/reports` unless `--output-dir` is explicitly supplied. Compare the current metrics with a compatible previous report (same provider, source mode, and exact period duration; schema v4). Explicit custom sources are not auto-compared.

Use `references/report-template.md` when changing report sections or evidence requirements.

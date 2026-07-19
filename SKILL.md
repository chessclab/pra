---
name: codex-retrospective
description: Create a privacy-first local retrospective of Codex / Claude Code session history for a user-selected period. Use only when the user explicitly invokes $codex-retrospective and asks to analyze work patterns, recurring corrections, prompt quality, verification habits, or weekly progress without exposing pasted content, secrets, source code, project identities, or local paths.
---

# Codex Retrospective (Claude Code / Codex)

Generate a local, evidence-backed review from sanitized metadata. Keep raw JSONL outside model context. Supports both Codex Desktop (`~/.codex/`) and Claude Code CLI (`~/.claude/`) history formats.

## Safety boundary

- Require an explicit period (`--days` or `--since`) on every run.
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

   Provider choices: ``codex`` (only Codex Desktop), ``claude`` (only Claude Code CLI), ``all`` (both).

4. Read only the reported `.md` path. The sanitized intermediate dataset is temporary and deleted after the report is written.
5. Explain findings with the anonymous event identifiers and derived evidence already present in the report.
6. If the user wants new Codex rules, propose a diff separately and wait for confirmation before writing it.

## Output

Save Markdown and machine-readable JSON locally under `~/.codex-retrospective/reports` unless `--output-dir` is explicitly supplied. Compare the current metrics with a compatible previous report (same provider, source mode, and exact period duration; schema v3). Explicit custom sources are not auto-compared.

Use `references/report-template.md` when changing report sections or evidence requirements.


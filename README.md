# PRA — Private Retrospective Analysis

> **Beta** — This project is in active development. APIs, report schema, and workflows may change without notice.

Privacy-first local retrospective analysis for Codex Desktop, [Claude Code](https://claude.ai/code) CLI, and Hermes session history. Generates evidence-backed reports about work patterns, recurring corrections, and verification habits — **without exposing pasted content, secrets, source code, project identities, or local paths**.

## Supported sources

| Source | History location |
|---|---|
| Codex Desktop | `~/.codex/history.jsonl`, `~/.codex/sessions/*.jsonl`, `~/.codex/archived_sessions/*.jsonl` |
| Claude Code CLI | `~/.claude/history.jsonl`, `~/.claude/projects/*/*.jsonl` |
| Hermes | `$HERMES_HOME/state.db` (SQLite, read-only) |

All three sources can be analysed together with `--provider all`. Hermes is read from `$HERMES_HOME/state.db` in read-only mode. Custom JSONL or SQLite files can be passed directly with `--source`.

The environment variables `CODEX_HOME` and `CLAUDE_HOME` replace the default paths (they are not additive).

## Data flow

1. **Raw JSONL** is read line by line.
2. **`object_pairs_hook`** filters blocked field names during JSON decoding. Blocked categories: pasted content, clipboard data, file contents, binary payloads, images, base64 values.
3. **Event classification** identifies user messages, assistant replies, tool results, and progress records.
4. **Assistant event handling** locally decodes the content structure to detect tool calls. Tool arguments and results may be inspected transiently for verification and context-artifact categories, but only booleans, categories, and exit-status aggregates are retained. Assistant text, thinking, tool name, tool input, tool output, commands, file paths, and file contents are never saved.
5. **Aggregation** computes counts, durations, and patterns from the remaining metadata only.
6. A **Markdown report** is written to disk. The intermediate sanitised dataset is temporary and deleted after the report is saved.

No external services, no telemetry, no network access during analysis. See [`references/privacy-model.md`](skill/pra/references/privacy-model.md) for the detailed privacy contract.

For Hermes reports, assistant/tool events are additionally grouped into logical turns. The report shows turn completion, duration, metadata-only verification outcomes, context-curator artifact signals, and a five-aspect first-prompt breakdown without persisting message text, commands, paths, or output.

## Prerequisites

- **Python 3.10 or later**
- **Zero external dependencies** — the tool uses only the Python standard library.
- **Codex Desktop** and/or **Claude Code CLI** history must exist on the machine for provider-based discovery (`--provider`). For `--source`, any JSONL file works.

## Install

```bash
# 1. Clone the repository
git clone https://github.com/chessclab/pra.git
cd pra

# 2. Create a directory for reports (default: ~/.pra/reports)
mkdir -p ~/.pra/reports

# 3. (Optional) Install as a skill
#    Codex Desktop: copy or symlink skill/pra into ~/.codex/skills/
#    Claude Code CLI: copy or symlink skill/pra into ~/.claude/skills/
#    On Windows (cmd, administrator):
#      mklink /D "%USERPROFILE%\.claude\skills\pra" "%CD%\skill\pra"
#    On Linux / macOS:
#      ln -s "$PWD/skill/pra" ~/.claude/skills/pra
#    Then invoke with $pra inside the assistant.
```

## Quick start

Preview what files would be analysed without generating a report:

```bash
python skill/pra/scripts/review.py --provider all --days 7 --dry-run
```

Run a full retrospective for the last 7 days:

```bash
python skill/pra/scripts/review.py --provider all --days 7
```

Analyse a specific JSONL file directly:

```bash
python skill/pra/scripts/review.py --source ./exported_history.jsonl --days 30
```

## What the report measures

Hermes reports currently include:

- **Logical turns** — one user request grouped with the following assistant and tool activity. The report includes completion, duration, tool-call count, and verification mentions.
- **Objective verification signals** — locally detected `test`, `lint`, `typecheck`, `build`, `diff`, and `review` actions, linked to tool-result exit status. Outcomes are `attempted`, `passed`, `failed`, or `unknown`.
- **Context-curator signals** — metadata-only indicators for `CONTEXT.md`, `SESSION-HANDOFF.md`, `DECISIONS.md`, and resumption after a handoff.
- **Initial prompt breakdown** — the first request in each session is checked for `goal`, `scope`, `readiness criterion`, `context/files`, and `constraints`. The report keeps the legacy 0–10 score and adds per-aspect counts.

Example:

```text
logical turns: 348 (343 completed)
verification: 63 attempted, 46 passed, 14 failed, 3 unknown
context artifacts: CONTEXT.md 1, handoff 0, decisions 2, resume 0
first-prompt breakdown: goal 2/19, scope 1/19, readiness 0/19, context/files 9/19, constraints 1/19
```

All command text, tool output, file paths, and artifact contents are discarded after local classification.

## Examples

Analyse only Claude Code CLI history for last 3 days:

```bash
python skill/pra/scripts/review.py --provider claude --days 3
```

Specify an exact date range:

```bash
python skill/pra/scripts/review.py --provider codex --since 2026-06-01 --until 2026-07-01
```

Write output to a custom directory:

```bash
python skill/pra/scripts/review.py --provider all --days 7 --output-dir ./my-reports
```

## Anonymised report fragment

```markdown
# PRA — Private Retrospective Analysis (Codex + Claude Code)

Период: `2026-07-09T00:00:00+00:00` — `2026-07-20T00:00:00+00:00`
Источник: custom · режим: explicit · длительность: 11 дн. (950400 с)

> Отчёт построен только из обезличенных метаданных.

## Сводка

| Метрика | Значение |
|---|---:|
| Запросы пользователя | 9 |
| Сессии | 4 |
| Обезличенные проекты | 2 |
| Сигналы исправления | 2 (22.2 %) |
| Упоминания проверки | 5 (55.6 %) |

## Характер работы

| Метрика | Значение |
|---|---:|
| События ассистента на пользовательский ход | 0.4 (среднее 0.4, медиана 0) |
| Вызовы инструментов | 50.0 % событий ассистента с вызовами инструментов · coverage: 100.0 % |
| Приблизительное время до следующего события ассистента | 32.5 с в среднем |
| Коррекция → успех | 0.0 % успешных из 2 |
| Индекс структуры первого промпта (0–10) | 6.7 из 10 (выборка: 4 сессий) |
```

All user-facing text in reports is in Russian.

## Baseline → intervention → follow-up

Compare two already-generated aggregate reports without rereading session history:

```bash
python skill/pra/scripts/compare.py   --baseline ./reports/baseline.json   --follow-up ./reports/follow-up.json   --intervention "Require a test command"   --output-dir ./comparisons
```

The comparison reports sample sizes, absolute deltas, percentage-point changes for verification/correction/turn metrics, and a non-causal interpretation warning. It accepts only PRA aggregate JSON reports and does not store or transmit raw session content.

## Testing

```bash
# Run all tests
python -B -m unittest discover -s tests -v

# Run a specific test class (e.g. frontmatter validation)
python -B -m unittest tests.test_skill_frontmatter -v
```

## Known limitations

- **No real-time monitoring** — the tool is designed for periodic retrospective analysis, not live dashboards.
- **No HTML export** — reports are Markdown and JSON only.
- **No SQLite or database storage** — each run is self-contained.
- **Comparison scope is strict** — reports are compared only when provider, source mode, and exact period duration all match. Schema v3 reports are readable but not auto-compared with v4. Explicit custom sources (`--source`) are never auto-compared.
- **Codex Desktop vs Claude Code CLI** — some metadata fields differ between the two formats; cross-provider reports use the intersection of available fields.
- **No configuration file** — all options are command-line flags.
- **Env var overrides** — `CODEX_HOME` replaces `~/.codex`; `CLAUDE_HOME` replaces `~/.claude`. They are not additive.
- **Behavioural metrics are approximate** — response time depends on model, tools, tests, and environment. Assistant events per user turn may not correspond one-to-one with logical replies.
- **Replanning and user intervention are not yet observable** in the current metadata contract.
- **Baseline → intervention → follow-up comparison is not yet implemented.**
- **Confidence scoring is not yet attached to recommendations**; always consider sample size before acting on a signal.

## Security

See [`SECURITY.md`](SECURITY.md) for the vulnerability disclosure policy and security guarantees.

## License

[MIT](LICENSE)

---

[Читать на русском](README.ru.md)

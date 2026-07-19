# Privacy model

## Data flow

`Codex JSONL / Claude Code JSONL → local parser → anonymous event metadata → local Markdown/JSON report`

The model may read the final Markdown report. It must never read the source JSONL or the temporary event file.

### Supported sources

- **Codex Desktop** (`~/.codex/`) — `history.jsonl`, `sessions/*.jsonl`, `archived_sessions/*.jsonl`
- **Claude Code CLI** (`~/.claude/`) — `history.jsonl`, `projects/*/*.jsonl`
- `$CODEX_HOME` replaces `~/.codex/` (ignores the default path when set)
- `$CLAUDE_HOME` replaces `~/.claude/` (ignores the default path when set)

## Guarantees

- Require an explicit bounded period for each run.
- Perform no network operations and import no networking libraries.
- Read source files without modifying them.
- Drop fields such as `pastedContents`, clipboard data, file contents, binary payloads, images, and base64 values before text extraction.
- Ignore assistant messages and tool outputs; analyze only known user-message layouts.
- Remove injected Codex context blocks such as environment, permission, skill, app, and plugin metadata before deriving metrics.
- Deduplicate multiple serialized representations of the same user turn by anonymous session and one-second timestamp.
- Exclude context-heavy records when at least 800 sanitized characters remain and ten or more redactions were required; record only their aggregate count.
- For Codex Desktop, accept `event_msg.user_message` only when it carries `client_id`; reject paired `response_item.message` records marked with `internal_chat_message_metadata_passthrough`. Keep unmarked CLI response messages as a compatibility fallback.
- Remove fenced code before deriving metrics.
- Replace common secrets, email addresses, IP addresses, and absolute paths before deriving metrics.
- Persist only aggregate metrics, anonymous identifiers, and metadata summaries.
- Delete the temporary sanitized event dataset after creating the report.
- Never modify `AGENTS.md` or send notifications.

## Important limitation

The local JSON decoder necessarily reads source bytes to parse each JSONL record. A forbidden field is discarded during decoding and is never emitted to the temporary dataset, report, terminal, or model context. Secret detection is best-effort and cannot recognize every possible proprietary identifier. The safest default is therefore metadata-only output with no excerpts.

Anonymous identifiers use a random per-run salt. They support comparisons within one report but cannot be correlated across reports. Weekly comparisons use aggregate metrics from the preceding local JSON report.

The report schema (currently version 3) stores the exact period duration in seconds. Only reports with matching schema version, provider, source mode, and period duration are considered compatible for comparison. Explicit custom sources always skip comparison.

## Verification requirements

- Test every blocked field with a unique canary value.
- Test representative API tokens, credentials, paths, email addresses, IP addresses, and fenced code.
- Assert that no canary appears in sanitized events or reports.
- Use only synthetic fixtures in the repository.

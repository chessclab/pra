# Security Policy

## Scope

PRA is a **local-only, privacy-first** analysis tool. It:

- Reads JSONL history files **only from the local filesystem** (no network input).
- **Never sends data** over the network — no telemetry, no analytics, no external API calls.
- **Writes all output** to the local filesystem (Markdown reports + JSON state).
- **Zero external dependencies** — the Python standard library only.

## Data handling

Privacy is enforced through three distinct mechanisms, not a single layer:

### A. JSON decoding — field names removed by `object_pairs_hook`

The JSON decoder uses `object_pairs_hook` to exclude entire field names during parsing. Key names are normalised (lowercased, non-alphanumeric characters stripped) and matched against this set:

| Category | Blocked key patterns |
| --- | --- |
| Pasted content | `pastedContents`, `pastedContent` |
| Clipboard | `clipboard`, `clipboardContent` |
| File contents | `fileContents`, `fileContent` |
| Binary & media | `binaryData`, `base64` |
| Image URLs | `imageUrl`, `image_url` |
| Local images | `localImages`, `local_images` |

The field value is decoded before the hook filters by key — this hook excludes by key name, not before value materialisation. Known user-text fields (such as `input_text`, `text`, `display`, `message`) are not blocked keys; they are extracted and then sanitised (see section B).

### B. User text redaction (after extraction)

Known user-text fields are extracted via `user_text_parts()`. The combined text then undergoes best-effort regex-based redaction:

- Fenced code blocks (`` ```...``` ``)
- Injected context tags (`<environment_context>`, `<system>`, `<developer>`, etc.)
- API keys, tokens, passwords, and credentials
- Email addresses
- IP addresses
- Windows absolute paths (`C:\...`)
- Unix absolute paths (`/.../...`)
- Pasted content tags (`<pastedContents>...</pastedContents>`)

After redaction, only aggregate signals and metrics are derived from the cleaned text. The raw text is never persisted to events, reports, or model context.

### C. Assistant content — metadata only

Assistant messages are locally decoded to detect tool calls. Only the following metadata is retained:

- `timestamp` (UTC ISO string)
- `session` (anonymised hash)
- `role` (always `"assistant"`)
- `kind` (event type string)
- `has_tool_calls` (boolean)
- `tool_content_available` (boolean)

The following are **never** saved to events, terminal, report, or model context:

- Assistant text output
- Thinking/reasoning content
- Tool name, arguments/input, or output/result
- Any content from unrecognised content layouts (tracked via `unknown_format_count` only)

This applies to both Codex (`payload.content[].type`) and Claude Code (`message.content[].type`) formats.

### Best-effort detection

All three mechanisms operate on a best-effort basis:

- Field-name patterns are matched exactly after normalisation; a renamed field or an unusual encoding may evade the key blocklist.
- Secrets masked by unusual encoding or split across multiple fields may evade regex redaction.
- The tool never attempts to *recover* or *display* blocked data — if a field is blocked or redacted, it is absent from the output.

## Vulnerability reporting

If you discover a security-relevant bug — especially a bypass of the privacy blocklist that could leak private data to a report — please report it privately.

**Do not** file a public GitHub issue for a privacy bypass. Instead:

1. **Contact**: Start a [Security Advisory](https://github.com/chessclab/pra/security/advisories) on GitHub.
2. **Include**: a minimal reproduction case (JSONL fixture) and the version or commit where the issue exists.
3. **Response**: We aim to acknowledge receipt within 48 hours and provide an initial assessment within 5 business days.

Non-security bugs, feature requests, and general discussion can use the standard issue tracker.

## Supported versions

| Version | Supported |
|---|---|
| Latest commit on `main` | ✅ |

There are no numbered releases — the project is in beta. Bug and security fixes are applied to `main` and released when ready.

## Security-relevant design decisions

- **`object_pairs_hook`** overrides the standard JSON decoder to filter field names from the result dict. This is the primary privacy boundary. Note: the field value is decoded before the hook filters by key — secret detection is best-effort.
- **Assistant content decoding** locally inspects the content structure to detect tool calls, but never stores assistant text, thinking, or tool input/output in events or reports.
- **No `eval()`, `exec()`, or dynamic imports** are used anywhere in the codebase.
- **No network libraries** are imported (`socket`, `urllib`, `http.client`, `requests`, etc.) — the network import test verifies this at the module level.
- **`--dry-run`** previews file counts only, never reading session contents.
- **The `--source` flag** allows analysis of any JSONL file the user explicitly provides — the user is responsible for the contents of that file.

See [`references/privacy-model.md`](skill/pra/references/privacy-model.md) for the full privacy contract and data flow description.

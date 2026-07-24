# Report contract

Every report must contain:

1. Exact analysis period.
2. Explicit scope: provider (codex | claude | all | custom), source mode (auto | explicit), exact period duration in seconds.
3. Explicit privacy statement.
4. Aggregate metrics and rates.
5. Behavioural metrics section (assistant_events_per_user_turn, time_distribution, tool_ratio, response_time, correction_chains, recovery_time, initial_prompt_structure).
6. Comparison with the most recent compatible local report (compatible = same schema version, provider, source mode, and exact period duration in seconds), when available. Auto-comparison is disabled for explicit custom sources.
7. Recommendations tied to threshold values, each with up to three anonymous metadata examples.
8. A limitation section stating that correlations are hypotheses and describing metric-specific caveats.
9. The schema version in the JSON state (currently 4).

Never include raw prompts, assistant responses, tool output, code excerpts, filenames, absolute paths, repository names, user names, or reversible hashes.

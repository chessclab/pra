# Report contract

Every report must contain:

1. Exact analysis period.
2. Explicit scope: provider (codex | claude | all | custom), source mode (auto | explicit), exact period duration in seconds.
3. Explicit privacy statement.
4. Aggregate metrics and rates.
5. Comparison with the most recent compatible local report (compatible = same schema version, provider, source mode, and exact period duration in seconds), when available. Auto-comparison is disabled for explicit custom sources.
6. Recommendations tied to threshold values.
7. Up to three anonymous metadata examples per recommendation.
8. A limitation stating that correlations are hypotheses, not proven causes.

Never include raw prompts, assistant responses, tool output, code excerpts, filenames, absolute paths, repository names, user names, or reversible hashes.

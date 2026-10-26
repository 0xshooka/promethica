# Contributing

1. Create a focused branch and keep public MCP tool names and arguments backward compatible where practical.
2. Do not infer an external API schema. Link the primary specification or record a sanitized real response used to design the parser.
3. Keep default responses bounded. Separate summaries from explicitly requested detail.
4. Treat API failure, valid zero results, and missing annotation as different states.
5. Label graph-derived candidates as hypotheses and preserve database identifiers, evidence, provenance, and analysis conditions.
6. Add offline tests with mocked APIs. Put live-network checks behind an explicit integration marker.

Run before opening a pull request:

```bash
uv sync --extra dev
uv run pytest
```

Never commit credentials, patient data, proprietary structures, or local MCP client configuration. Security issues should be reported privately to the repository owner rather than opened with exploit details in a public issue.

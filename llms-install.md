# Installing the JustFill MCP server (for AI agents)

JustFill fills existing PDF forms, including flat PDFs and scans. Choose ONE of the two setups.

## Option A: hosted server (recommended, no API key in config)

If the client supports remote MCP servers with OAuth, add:

```json
{
  "mcpServers": {
    "justfill": {
      "url": "https://justfill.app/api/mcp"
    }
  }
}
```

On first use the client opens a browser window; the user signs in to JustFill
(free account, email or Google) and approves access.

## Option B: local server via uvx

Requirements: Python 3.10+ and `uv` (`pip install uv` if `uvx` is missing).

1. Ask the user to create an API key at https://justfill.app → Account → API Keys.
   Never invent a key and never ask the user to paste a password.
2. Add to the MCP settings:

```json
{
  "mcpServers": {
    "justfill": {
      "command": "uvx",
      "args": ["justfill-mcp"],
      "env": { "JUSTFILL_API_KEY": "<key from the user>" }
    }
  }
}
```

## Verify

Call `list_templates` (works on an empty account), then `open_pdf` on
https://justfill.app/fixtures/openai-plugin/project-intake-acroform.pdf and
`render_preview` for page 0. Fill only values the user provided, show
`render_filled_preview` before `fill_pdf`, and never enter payment-card data,
government IDs, health data or secrets.

Support: hello@justfill.app

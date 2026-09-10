# Gemini CLI hook fixtures — source: "unverified"

Derived from this repository's own
`references/contracts/gemini-beforetool-contract.yaml` and
`gemini-session-contract.yaml`. `GEMINI_CWD` is documented as an env var, not
a payload field; the adapter reads it with a `payload["cwd"]` fallback.

**Not verified against a real Gemini CLI client at any pinned version.**
`client_version_verified` is `false` on every receipt this adapter writes.

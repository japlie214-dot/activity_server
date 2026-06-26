# tools/crypto/docs.py
TOOL_DESCRIPTION = "Encrypt a string using SHA-256 + hex encoding (deterministic)."

TOOL_DOCS = """## Crypto

Produces a deterministic SHA-256 hex digest of the input string, optionally
prepended with a salt. This is a **one-way hash**, not reversible encryption.
The same input + salt always produces the same output.

### Activities

| # | Activity | Purpose |
|---|----------|---------|
| 1 | `crypto.prepare` | Combine salt and plaintext into the pre-hash payload |
| 2 | `crypto.hash` | SHA-256 hash the payload and return hex digest |

### Input Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `plaintext` | string | **yes** | The string to hash |
| `salt` | string | no | Salt prefix (default: `"activity-server"`) |

**Request body (JSON):**
```json
{"plaintext": "hello world", "salt": "my-salt"}
```

**Headers:** None required. `X-Observe: true` returns Activity lineage.

### Expected Output

```json
{
  "plaintext": "hello world",
  "encrypted": "a1b2c3...",
  "algorithm": "sha256-hex",
  "salt": "my-salt"
}
```

### Common Workflows

1. **Hash a password:** `{"plaintext": "user-password"}`
2. **Hash with custom salt:** `{"plaintext": "data", "salt": "project-x"}`
3. **Verify integrity:** Hash the same input twice — results must match.

### Troubleshooting

- **"encrypted" changes when salt changes** — This is expected. The hash is
  `SHA256(salt + ":" + plaintext)`. Different salt = different hash. The
  `crypto.prepare` Activity shows the combined payload in the Lineage.
- **Not real encryption** — SHA-256 is a hash function, not encryption. It's
  one-way. If you need reversible encryption, this is the wrong tool.
- **Empty string input** — Valid. `SHA256("salt:")` returns a valid hash.
- **Deterministic** — Same inputs always produce the same hash. This is by
  design for integrity checking, but means it's NOT suitable for password
  storage without additional hardening (bcrypt, argon2, etc.).
- **Output field is called `encrypted`** — Historical naming. It's actually a
  hash digest, not ciphertext. The `algorithm` field clarifies this."""

TOOL_OUTPUT_EXAMPLE = {
    "plaintext": "hello",
    "encrypted": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
    "algorithm": "sha256-hex",
    "salt": "activity-server",
}

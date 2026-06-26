# tools/artifact_saver/docs.py
TOOL_DESCRIPTION = "Save an input string as a .txt file in data/artifacts/. Demonstrates file-producing tools."

TOOL_DOCS = """## Artifact Saver

Writes the provided content to a `.txt` file in the `data/artifacts/` directory
and records the artifact in the `artifacts` table via DualWriter. The `artifacts`
table is **cloud-synced** — your files are backed up to Snowflake automatically.

### Activities

| # | Activity | Purpose |
|---|----------|---------|
| 1 | `artifact_saver.prepare` | Resolve filename, ensure artifacts dir exists |
| 2 | `artifact_saver.write_file` | Write content to disk, return metadata |
| 3 | `artifact_saver.record_db` | Record artifact in database via DualWriter |

If `write_file` fails (disk full, permissions), `record_db` never runs.

### Input Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | string | **yes** | Text content to write to the file |
| `filename` | string | no | Filename without extension (default: auto-generated timestamp) |

**Request body (JSON):**
```json
{"content": "Hello, world!", "filename": "greeting"}
```

**Headers:** None required. `X-Observe: true` returns Activity lineage.

### Expected Output

```json
{
  "saved": true,
  "filename": "greeting.txt",
  "filepath": "/path/to/data/artifacts/greeting.txt",
  "size_bytes": 13,
  "content_preview": "Hello, world!"
}
```

### Common Workflows

1. **Save report:** `{"content": "Report content...", "filename": "daily-report"}`
2. **Auto-named file:** `{"content": "data"}` — generates `artifact_20260626_080000.txt`
3. **Chain with other tools:** Save the output of `textstats` or `calculator`
   as a persistent artifact.
4. **Browse artifacts:** `GET /artifacts` lists all saved artifacts with metadata.

### Troubleshooting

- **Filename gets `.txt` appended automatically** — If you pass `"filename": "report.csv"`,
  the result is `report.csv.txt`. This is intentional — this tool is for text files.
  Don't include the extension yourself.
- **Auto-generated filename uses UTC** — The timestamp in `artifact_YYYYMMDD_HHMMSS.txt`
  is UTC, not your local time.
- **Path resolution** — If `ARTIFACTS_DIR` is a relative path (e.g., `./data/artifacts`),
  it's resolved relative to the project root. Absolute paths are used as-is.
- **No overwrite protection** — Writing to the same filename silently overwrites
  the previous content. There's no versioning or backup.
- **`content_preview` is truncated** — Only the first 200 characters are returned
  in the preview. The full content is written to the file.
- **`size_bytes` is UTF-8 byte count** — Multi-byte characters (emoji, CJK)
  count as multiple bytes. `"hello"` = 5 bytes, `"你好"` = 6 bytes.
- **DB record skipped if server not ready** — `artifact_saver.record_db` silently
  succeeds even if the server isn't available (it just returns without writing).
  The file is still saved. In the Lineage, the Activity shows `ok: true` with
  `output: null`.
- **Cloud-synced** — The `artifacts` table is in `CLOUD_SYNC_TABLES`. Every
  artifact record gets a `row_hash` and is replicated to Snowflake."""

TOOL_OUTPUT_EXAMPLE = {
    "saved": True,
    "filename": "example.txt",
    "filepath": "/path/to/data/artifacts/example.txt",
    "size_bytes": 7,
    "content_preview": "example",
}

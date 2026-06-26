# tools/textstats/docs.py
TOOL_DESCRIPTION = "Analyze text: word count, char count, sentence count, hash."

TOOL_DOCS = """## TextStats

Performs basic statistical analysis on a text string. Returns character count,
word count, sentence count, average word length, and a SHA-256 hash of the
input (useful for change detection or deduplication).

### Activities

| # | Activity | Purpose |
|---|----------|---------|
| 1 | `textstats.count` | Count words, characters, sentences, compute avg word length |
| 2 | `textstats.hash` | SHA-256 hash the raw text for change detection |

### Input Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `text` | string | **yes** | The text to analyze |

**Request body (JSON):**
```json
{"text": "Hello world. This is a test!"}
```

**Headers:** None required. `X-Observe: true` returns Activity lineage.

### Expected Output

```json
{
  "char_count": 29,
  "word_count": 6,
  "sentence_count": 3,
  "avg_word_length": 3.83,
  "sha256": "abc123..."
}
```

### Common Workflows

1. **Content metrics:** `{"text": "Your article text here..."}`
2. **Change detection:** Compare `sha256` of two texts to check if they differ.
3. **Readability check:** Use `avg_word_length` as a rough readability proxy
   (shorter words → simpler text).

### Troubleshooting

- **Sentence count seems wrong** — Sentences are split on `.`, `!`, and `?`.
  Abbreviations like "Dr. Smith" count as sentence boundaries. This is a
  simplistic splitter, not NLP. For precise sentence detection, use a
  dedicated NLP library.
- **Empty string** — Returns all zeros and a valid SHA-256 hash of the empty
  string. No error.
- **Word count uses whitespace splitting** — `"hello  world"` (double space)
  is 2 words, not 3. Consecutive whitespace is treated as a single separator.
- **`avg_word_length` is 0 for empty text** — Division by zero is avoided via
  `max(len(words), 1)`, so empty text returns `0.0`.
- **Hash is of the raw string** — Including whitespace, punctuation, and
  newlines. `"Hello"` and `"hello"` produce different hashes.
- **Unicode** — Characters are counted by Python's `len()`, which counts
  code points. Emoji and combining characters may not match visual character
  count."""

TOOL_OUTPUT_EXAMPLE = {
    "char_count": 11,
    "word_count": 2,
    "sentence_count": 1,
    "avg_word_length": 5.0,
    "sha256": "...",
}

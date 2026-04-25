# Golden fixtures

Each `*.json` is one frozen extraction snapshot. Schema:

```json
{
  "title": "<original article title>",
  "url":   "<original article url>",
  "body":  "<cleaned text>",
  "expected": {
    "is_partnership":   true,
    "country_1":        "...",
    ...
  }
}
```

`run_prompt_regression.py` patches the Anthropic client to return
`expected` and asserts that the extraction module routes the payload
through `insert_draft` correctly. To grow the suite, paste a new
`{title, url, body}` and run with `--record` — the script will call
the live model once, capture the result as `expected`, and freeze it.

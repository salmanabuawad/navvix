# Tests

Add DXF regression samples here.

Recommended golden test structure:

```text
tests/golden/
├── input.dxf
├── expected_report.json
└── expected_preview.png
```

Validation should assert:

- no table/title/frame dimensions
- dimensions have semantic ownership
- exterior dimensions exist
- no tiny connector artifact dimensions

# Statement import

Tally reads statements without a bank connection. Four tiers, cheapest first.

| Tier | Format | Engine | Notes |
|---|---|---|---|
| 0 | CSV, TSV | `csv` + column aliases | Deterministic |
| 0 | OFX, QFX | regex parser | Carries stable transaction ids |
| 0 | QIF | line parser | Older UK/US exports |
| 0 | MT940 | line parser | European bank standard |
| 1 | PDF with a text layer | `pdfplumber` | ~most digital statements |
| 2 | Scanned PDF / image | RapidOCR (PP-OCRv6) | Ships with `requirements.txt`; Tesseract if installed |
| 3 | Anything ambiguous | the agent | Review rows in `import_preview`, fix, commit |

OCR is only used when a page has no text layer. The default engine is RapidOCR:
PP-OCRv6 detection + recognition models exported to ONNX, running on CPU through
onnxruntime. Models are bundled in the wheel (~31 MB), so the container needs no
outbound access. `TALLY_OCR_ENGINE` pins `rapidocr`, `tesseract` or `none`.

---

## How the PDF parser works

No per-bank pixel constants. The algorithm:

1. Group words into visual lines by vertical position.
2. Find the header row from column *labels* (`Date` plus at least one of
   `Paid out / Paid in / Money out / Money in / Amount / Balance / Debit / Credit`).
3. Discover numeric columns empirically by clustering the right edges of money
   tokens in the body.
4. Bind each header label to its nearest column, so semantics come from the
   bank's own headings.
5. Read each row: date from 1–3 leading tokens, description between the date and
   the first number, amounts by column binding. Lines without a date continue the
   previous row (multi-line descriptions).

The same code parses HSBC (`Paid out/Paid in`), Barclays (`Money out/Money in`),
Monzo (single signed `Amount`) and anything else with a text layer. The test suite
asserts all three produce identical totals.

---

## Profiles

A profile is YAML, not code. It supplies aliases, date formats and noise rows.

```yaml
id: hsbc_uk
name: HSBC UK
match:
  any: ["hsbc"]
  headers_all: ["paid out", "paid in"]
dates: ["%d %b %y", "%d/%m/%Y"]
currency: GBP
columns:
  date: ["date"]
  description: ["details", "description"]
  out: ["paid out", "money out", "debit"]
  in: ["paid in", "money in", "credit"]
  balance: ["balance"]
skip_rows: ["balance brought forward", "balance carried forward"]
strip_prefixes: ["VIS", "DD", "SO", "BP", "TFR", "ATM"]
```

Profiles ship for HSBC, Barclays, Lloyds, NatWest, Santander, Nationwide, Monzo,
Starling, Revolut, N26 and Wise. Anything unmatched falls back to `generic`,
which handles most statements without a profile at all.

Adding a bank: drop a YAML file in `app/importers/profiles/`, add a generated
fixture in `tests/statement_factory.py`, and run `pytest`. Detection is keyword
scoring, so a wrong profile is easy to spot (`batch.profile` in the preview).

---

## Confidence and correctness

Each row gets a score: date parsed (0.4), amount parsed (0.4), description (0.2).
Rows below 1.0 list their issues.

The stronger signal is **balance continuity**. If the statement has a running
balance, Tally checks `previous + signed(amount) == balance` for every row and
reports the rows that break the chain. If opening and closing balances are known,
it also checks that the sum of the transactions bridges them.

A parse that reconciles is trustworthy. A parse that does not says exactly where
it went wrong — which is more than a spreadsheet would tell you.

---

## Preview → commit → revert

Nothing enters the ledger until you commit.

```bash
tally import preview statement.pdf --account 1
tally import commit 3              # after reviewing rows
tally import revert 3              # undo the whole batch
```

Preview returns:

```json
{
  "batch": {"id": 3, "parsed": 42, "skipped": 0, "confidence": 0.99,
            "opening_balance": 1204.31, "closing_balance": 4432.31,
            "warnings": []},
  "rows": [{"row_id": 12, "date": "2026-11-03", "description": "TESCO",
            "amount": 42.10, "type": "expense", "confidence": 1.0, "issues": []}]
}
```

Duplicates are detected before commit by hashing
`(account, date, amount, normalised description, occurrence)`. Re-importing the
same statement marks every row `skipped` and writes nothing.

Commit accepts edits, which is how an agent repairs a bad parse:

```bash
curl -X POST /api/v1/imports/3/commit \
  -d '{"edits": [{"row_id": 12, "description": "Tesco Stores", "amount": 42.10}]}'
```

Imported rows are `source='statement'` and therefore raw-locked: their amounts,
dates and descriptions cannot be edited later. Annotations (category, note, tags)
still can, and are reversible.

---

## OCR

```bash
pip install -r requirements.txt     # rapidocr + onnxruntime + pdf2image
python -c "from app.ocr_service import ocr_service; print(ocr_service.status())"
```

Tally uses OCR only when a page has no extractable text, and warns when it does.
RapidOCR runs at ~4 s per A4 page at 200 DPI on a CPU core; Tesseract is used
automatically if RapidOCR is missing (`apt install tesseract-ocr`). Scanned
statements should be reviewed with extra care; the balance check still applies.

---

## Privacy

Files are read locally and the upload is stored under `UPLOADS_DIR`. Nothing is
sent anywhere during parsing. If you use the API's `url` form, Tally downloads the
statement itself — that URL is the only network call.

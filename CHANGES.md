# Changes

A chronological log of commits in this repository, newest first. Each entry
summarises the user-visible behaviour change and the key implementation
points.

## QIF specification compliance and ANZ Dec-2024 sign fix

Brings generated QIF output into conformance with the **W3C QIF
specification** (https://www.w3.org/2000/10/swap/pim/qif-doc/QIF-doc.htm),
also pointed to from `sample-qif-files/QIF Format Definition.txt`.

Generator changes (`main.py`, `parsers.py`):

- `QIFGenerator` now emits **CRLF** line terminators (matching the
  bundled `sample-qif-files/Transactions.qif`) and exactly one
  `!Type:<account>` header on the first line.
- The `<account>` value is normalised to a spec-legal token
  (`Bank`, `CCard`, `Cash`, `Oth A`, `Oth L`, `Invst`).
- `Transaction.to_qif()` sanitises `P`/`M`/`N`/`S` field values by
  stripping CR/LF/TAB/control characters so each field stays on a
  single line.
- When splits exist, the generator auto-balances any rounding remainder
  so `Σ$ == T` exactly, as required by the spec.

New validator and pre-write enforcement:

- `validate_qif_compliance(text)` returns a list of spec violations
  covering: header presence and uniqueness, spec-legal `!Type` value,
  legal field tags on every line, `^` terminator placement, splits
  summing to the record total, and trailing-record termination.
- `main.py` now runs the validator on the rendered QIF before writing
  and refuses to leave a non-compliant file on disk.

Tests (`test_suite.py`):

- New `TestQIFSpecCompliance` class with 16 tests covering the
  validator (positive + negative cases), single-`!Type:` header,
  spec-legal type values, CRLF endings, legal tags on every line,
  caret-on-its-own-line termination, split-sum = total invariant,
  no embedded control chars, and a generator → re-parse round-trip
  asserting identical transaction count and amount sum on every
  discovered sample.

Documentation:

- README adds a **QIF Specification Compliance** section citing the
  W3C source URL and listing the seven enforced spec rules.

## 6a45489 — Verify extraction before writing QIF/CSV output

Adds a verification stage between parsing and writing so the program never
leaves an incorrect QIF or CSV file on disk.

- New `verify_extraction(filename, bank, account_type, transactions)` in
  `main.py` returns a list of problems (empty list = OK).
- `main()` runs verification per input file *before* the generator writes
  anything; on failure it prints all problems, exits with status 1, and
  no output file is created.
- New `--skip-verify` CLI flag for explicit opt-out (debugging only).
- Defensive cleanup: if the final write itself fails, any half-written
  output file is removed.

Checks performed:

1. Non-empty extraction.
2. `Opening + Σ(transactions) == Closing` (±$0.01) via `extract_balances()`.
3. Field integrity — every transaction has a date, a non-empty payee, and
   a finite numeric amount.
4. Plausible dates (`1990 ≤ year ≤ 2100`).
5. Splits magnitude — `|sum(splits)| ≤ |parent amount|`.
6. Duplicate detection — flags suspicious clusters of `(date, payee,
   amount)` repeats.

## 2d02937 — Add dynamic sample discovery and comprehensive test suite

Replaces hardcoded sample-statement metadata with dynamic discovery and
introduces a full unittest suite.

Parsers / app:

- Rewrote `NABBankAccountParser` to use word X-coordinates for reliable
  Debit/Credit/Balance column detection and to exclude the informational
  tax-summary section.
- Fixed `NABCreditCardParser` and `ANZCreditCardParser` to correctly
  handle `CR` suffixes (payments positive, purchases negative).
- Fixed `MacquarieBankAccParser` to derive transaction sign from balance
  changes instead of ambiguous suffixes.
- Stopped double-counting ANZ overseas-fee amounts; they are now recorded
  as splits on the parent transaction.

Dynamic statement metadata:

- Added `infer_bank_and_account()`, `extract_balances()` and
  `discover_samples()` in `parsers.py` so sample metadata (bank, account
  type, opening/closing balances) is derived from filenames and PDF
  contents — drop a new PDF into `sample-statements/` and tooling picks
  it up automatically.
- `main.py`: `bank` and `account_type` CLI args are now optional and
  inferred from the input filename when omitted; README updated.

Tests:

- Added `test_suite.py` with 25 unittest cases covering input validation,
  the balance equation per discovered sample, fee/interest/tax/split
  identification, QIF/CSV output structure, and parser dispatch.
- Added `test_balances.py` as a quick standalone balance-equation check
  driven by `discover_samples()`.

Misc:

- Ignore `__pycache__/` and `.junie/` in `.gitignore`; removed the
  accidentally tracked `__pycache__/parsers.cpython-313.pyc`.

## 71ffe3d — Fixes to properly handle multi-line transactions

Addresses cases where a single transaction spans multiple lines in the
PDF text extraction across all statement types, ensuring the full
description is captured and the amount is associated with the correct
record.

## 58f6549 — Implement split transaction support for bank fees

Adds first-class support for split transactions so that fees embedded in
a parent transaction are surfaced separately without being double-counted.

- Enhanced `Transaction` class to support multiple splits for QIF and
  CSV export.
- Updated `NABBankAccountParser` to detect `Intl Txn Fee` and
  `Overseas ATM Txn Fee` as splits.
- Updated `ANZCreditCardParser` to extract `INCL OVERSEAS TXN FEE` and
  represent it as a split.
- Refactored QIF generation to use `S` and `$` tags for splits.
- Updated CSV output to include split details in the memo field.
- Verified that foreign currency amounts are not incorrectly identified
  as splits.

## 730fb25 — Updated `.gitignore`

Initial tightening of ignored artefacts (output files, virtualenv,
sample-statements directory).

## e7c13e8 — Initial version

First Junie-generated implementation: PDF-to-QIF/CSV conversion with
parsers for NAB BankAcc, NAB CreditCard, ANZ CreditCard, and Macquarie
BankAcc, plus a basic CLI in `main.py`.

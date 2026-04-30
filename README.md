# Bank Statement to QIF/CSV Converter

A Python-based utility to convert bank statements in PDF format into QIF (Quicken Interchange Format) or CSV files. This tool is specifically designed to handle complex statement layouts from various banks, extracting transaction data while ignoring irrelevant sections.

## Supported Banks and Account Types

- **ANZ**: Credit Card
- **NAB**: Bank Account, Credit Card
- **Macquarie**: Bank Account

## Features

- **Multi-format Support**: Export transactions to either QIF or CSV.
- **Batch Processing**: Process multiple statement files of the same type and aggregate them into a single output file.
- **Auto-detection**: Automatically detects the output format based on the file extension (`.qif` or `.csv`).
- **Robust Extraction**: Uses `pdfplumber` and regex-based parsing to handle multi-line transactions, complex PDF layouts, and transaction fees (converted to QIF splits).
- **Security Focused**: Implements path validation to prevent unauthorized system access and uses secure coding practices.

## Installation

### Prerequisites

- Python 3.x
- `pdfplumber`

### Setup

1. Clone this repository.
2. Install dependencies:
   ```bash
   pip install pdfplumber
   ```

## Usage

Run the tool using `main.py` with the following arguments:

```bash
python main.py <bank> <account_type> -i <input_files...> -o <output_file> [--format <qif|csv>]
```

### Arguments

- `bank` *(optional)*: The name of the bank (e.g., `ANZ`, `NAB`, `Macquarie`). If omitted, it is inferred from the input filename (e.g. `NAB-BankAcc-...pdf`).
- `account_type` *(optional)*: The type of account (e.g., `CreditCard`, `BankAcc`). If omitted, it is inferred from the input filename.
- `-i, --input`: One or more paths to the PDF statement files.
- `-o, --output`: The path for the output file (default: `transactions.qif`).
- `--format`: *(optional)* Explicitly set the output format to `qif` or `csv`. If omitted, the format is inferred from the output file extension.
- `--skip-verify`: *(optional)* Skip the post-extraction verification stage that runs before any output file is written. Not recommended — only useful for debugging unsupported statement layouts. See *Extraction verification* below.

### Extraction verification

By default, after parsing each input PDF and **before** writing any QIF or CSV file, the program runs a set of sanity checks on the extracted transactions. If any check fails, the program prints the problems, exits with status `1`, and **no output file is left on disk** (any half-written file is cleaned up).

Checks performed:

1. **Non-empty extraction** — refuse to write a file for a statement that produced zero transactions.
2. **Balance equation** — `opening + Σ(transactions) == closing` (within $0.01), using opening/closing balances extracted from the PDF.
3. **Field integrity** — every transaction has a date, a non-empty payee, and a finite numeric amount.
4. **Plausible dates** — transaction year is between 1990 and 2100.
5. **Splits magnitude** — `|sum(splits)| ≤ |parent amount|`.
6. **Duplicate detection** — flags suspicious clusters of `(date, payee, amount)` repeats.

Pass `--skip-verify` to bypass these checks (not recommended).

### Examples

**Convert a single ANZ Credit Card statement to QIF:**
```bash
python main.py ANZ CreditCard -i statement.pdf -o transactions.qif
```

**Auto-detect bank and account type from the filename:**
```bash
python main.py -i NAB-BankAcc-statement.pdf -o transactions.qif
```

**Bypass extraction verification (debugging only):**
```bash
python main.py -i statement.pdf -o transactions.qif --skip-verify
```

**Convert multiple NAB Bank statements to a single CSV:**
```bash
python main.py NAB BankAcc -i statement1.pdf statement2.pdf -o all_transactions.csv
```

**Explicitly specify CSV format for a file with a different extension:**
```bash
python main.py Macquarie BankAcc -i statement.pdf -o output.txt --format csv
```

## Project Structure

- `main.py`: The entry point and CLI logic.
- `parsers.py`: Contains the transaction data model and bank-specific PDF parsers.
- `sample-statements/`: Example PDF statements for testing.

## License

MIT

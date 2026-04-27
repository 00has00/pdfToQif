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

- `bank`: The name of the bank (e.g., `ANZ`, `NAB`, `Macquarie`).
- `account_type`: The type of account (e.g., `CreditCard`, `BankAcc`).
- `-i, --input`: One or more paths to the PDF statement files.
- `-o, --output`: The path for the output file (default: `transactions.qif`).
- `--format`: (Optional) Explicitly set the output format to `qif` or `csv`. If omitted, the format is inferred from the output file extension.

### Examples

**Convert a single ANZ Credit Card statement to QIF:**
```bash
python main.py ANZ CreditCard -i statement.pdf -o transactions.qif
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

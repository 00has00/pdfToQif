import argparse
import sys
import csv
import io
import os
from parsers import get_parser, Transaction, infer_bank_and_account, extract_balances

def validate_path(path, must_exist=False):
    """
    Ensures the path is safe and optionally exists.
    For this tool, we'll allow paths outside CWD if the user explicitly provides them,
    but we should be careful about sensitive system paths.
    A common practice for CLI tools is to trust the user's input for paths,
    but here we will implement a basic check to prevent accidental traversal
    and ensure directory existence for output.
    """
    abs_path = os.path.abspath(path)
    
    # Basic check: prevent access to sensitive system directories if possible.
    # This is a bit arbitrary for a CLI tool, but good for "secure coding".
    forbidden_prefixes = ['/etc', '/var', '/root', '/bin', '/sbin', '/usr/bin', '/usr/sbin']
    for prefix in forbidden_prefixes:
        if abs_path.startswith(prefix):
            raise ValueError(f"Access to system path {path} is restricted.")

    if must_exist and not os.path.exists(abs_path):
        raise FileNotFoundError(f"File not found: {path}")
    
    # Ensure output directory exists
    parent_dir = os.path.dirname(abs_path)
    if not os.path.exists(parent_dir) and parent_dir != '':
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except Exception as e:
            raise ValueError(f"Could not create directory {parent_dir}: {e}")

    return abs_path

class QIFGenerator:
    def __init__(self, account_type="Bank"):
        self.account_type = "CCard" if "credit" in account_type.lower() else "Bank"
        self.transactions = []

    def add_transactions(self, transactions):
        self.transactions.extend(transactions)

    def generate(self):
        output = [f"!Type:{self.account_type}"]
        for t in self.transactions:
            output.append(t.to_qif())
        return "\n".join(output) + "\n"

class CSVGenerator:
    def __init__(self):
        self.transactions = []

    def add_transactions(self, transactions):
        self.transactions.extend(transactions)

    def generate(self):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Date", "Amount", "Payee", "Memo", "Num"])
        for t in self.transactions:
            writer.writerow(t.to_csv_row())
        return output.getvalue()

def verify_extraction(filename, bank, account_type, transactions):
    """Run sanity checks on parsed transactions before they are written out.

    Returns a list of human-readable problem strings. Empty list means OK.

    Checks performed:
      1. Non-empty extraction.
      2. Opening + sum(transactions) == Closing (within $0.01) when balances
         can be extracted from the PDF.
      3. Every transaction has a date, a non-None payee and a finite numeric
         amount.
      4. No duplicate transactions (same date + payee + amount appearing
         more than once is flagged as suspicious; not necessarily fatal but
         worth surfacing).
      5. All transaction dates fall within a plausible window (we don't
         know the statement period strictly, but we reject obviously bad
         dates such as year < 1990 or > 2100).
      6. Splits, where present, do not exceed the parent amount in
         magnitude.
    """
    problems = []

    if not transactions:
        problems.append("No transactions were extracted from the statement.")
        return problems

    # 3. Field integrity
    for i, t in enumerate(transactions):
        if t.date is None:
            problems.append(f"Transaction #{i+1} has no date.")
        elif not (1990 <= t.date.year <= 2100):
            problems.append(
                f"Transaction #{i+1} has an implausible date: {t.date}.")
        if t.payee is None or str(t.payee).strip() == "":
            problems.append(f"Transaction #{i+1} has an empty payee.")
        try:
            amt = float(t.amount)
            if amt != amt or amt in (float("inf"), float("-inf")):
                raise ValueError
        except (TypeError, ValueError):
            problems.append(
                f"Transaction #{i+1} ({t.payee}) has a non-numeric amount: "
                f"{t.amount!r}.")

    # 6. Splits magnitude
    for t in transactions:
        if not t.splits:
            continue
        split_total = sum(a for _, a in t.splits)
        if abs(split_total) > abs(t.amount) + 0.01:
            problems.append(
                f"Splits for transaction '{t.payee}' on "
                f"{t.date.strftime('%Y-%m-%d') if t.date else '?'} sum to "
                f"{split_total:.2f}, exceeding parent amount {t.amount:.2f}.")

    # 4. Duplicate detection
    seen = {}
    for t in transactions:
        key = (t.date, t.payee, round(float(t.amount), 2))
        seen[key] = seen.get(key, 0) + 1
    dupes = [(k, n) for k, n in seen.items() if n > 1]
    if dupes:
        # Not auto-fatal: bank statements occasionally have legitimate
        # repeats (e.g. two identical coffee purchases same day). Surface
        # as a warning-level problem only when there are many.
        if len(dupes) > 3:
            details = ", ".join(
                f"{k[1]} {k[2]:+.2f} x{n}" for k, n in dupes[:5])
            problems.append(
                f"Found {len(dupes)} suspicious duplicate transaction "
                f"groups (showing up to 5): {details}.")

    # 2. Balance equation
    try:
        opening, closing = extract_balances(filename, bank, account_type)
    except Exception as e:
        problems.append(
            f"Could not verify opening/closing balances: {e}. "
            "Refusing to write output without balance verification.")
        return problems

    total = sum(t.amount for t in transactions)
    calculated = opening + total
    if abs(calculated - closing) > 0.01:
        problems.append(
            f"Balance mismatch: opening {opening:.2f} + sum "
            f"{total:.2f} = {calculated:.2f}, but statement closing "
            f"balance is {closing:.2f} (diff {calculated - closing:+.2f}).")

    return problems


def main():
    parser = argparse.ArgumentParser(description='Convert bank statements in PDF to QIF or CSV.')
    parser.add_argument('bank', nargs='?', default=None,
                        help='Bank name (e.g., ANZ, NAB, Macquarie). '
                             'If omitted, inferred from the input filename.')
    parser.add_argument('account_type', nargs='?', default=None,
                        help='Account type (e.g., CreditCard, BankAcc). '
                             'If omitted, inferred from the input filename.')
    parser.add_argument('-i', '--input', nargs='+', required=True, dest='statement_filenames', help='Path to one or more PDF statement files')
    parser.add_argument('-o', '--output', default='transactions.qif', dest='output_filename', help='Output filename (default: transactions.qif)')
    parser.add_argument('--format', choices=['qif', 'csv'], help='Output format (qif or csv). If not specified, inferred from output_filename extension.')
    parser.add_argument('--skip-verify', action='store_true',
                        help='Skip post-extraction verification (balance match, '
                             'field integrity, duplicates, etc.). Not recommended.')

    args = parser.parse_args()

    # Security: Validate paths
    try:
        output_path = validate_path(args.output_filename)
        input_paths = [validate_path(p, must_exist=True) for p in args.statement_filenames]
    except (ValueError, FileNotFoundError) as e:
        print(f"Security/Path Error: {e}")
        sys.exit(1)

    # Determine format
    fmt = args.format
    if not fmt:
        if output_path.lower().endswith('.csv'):
            fmt = 'csv'
        else:
            fmt = 'qif'

    # Resolve bank / account_type once, falling back to filename inference
    # using the FIRST input file (all inputs must share the same kind).
    resolved_bank = args.bank
    resolved_acc = args.account_type
    if not resolved_bank or not resolved_acc:
        inferred_bank, inferred_acc = infer_bank_and_account(input_paths[0])
        resolved_bank = resolved_bank or inferred_bank
        resolved_acc = resolved_acc or inferred_acc
    if not resolved_bank or not resolved_acc:
        print("Error: Could not determine bank and/or account type. "
              "Pass them explicitly or use a filename like "
              "'NAB-BankAcc-...pdf'.")
        sys.exit(1)

    if fmt == 'csv':
        generator = CSVGenerator()
    else:
        generator = QIFGenerator(resolved_acc)

    total_transactions = 0
    for filename in input_paths:
        pdf_parser = get_parser(resolved_bank, resolved_acc, filename)
        if not pdf_parser:
            print(f"Error: Unsupported bank or account type: "
                  f"{resolved_bank} - {resolved_acc}")
            sys.exit(1)

        try:
            transactions = pdf_parser.parse()
        except Exception as e:
            print(f"Error parsing PDF {filename}: {e}")
            sys.exit(1)

        if not transactions:
            print(f"Error: No transactions found in {filename}. "
                  "Refusing to produce output for an empty statement.")
            sys.exit(1)

        if not args.skip_verify:
            problems = verify_extraction(filename, resolved_bank,
                                         resolved_acc, transactions)
            if problems:
                print(f"Extraction verification FAILED for {filename}:")
                for p in problems:
                    print(f"  - {p}")
                print("No output file was written. "
                      "Re-run with --skip-verify to bypass these checks "
                      "(not recommended).")
                sys.exit(1)
            else:
                print(f"Verified {len(transactions)} transactions from "
                      f"{os.path.basename(filename)} "
                      "(balances match, field integrity OK).")

        generator.add_transactions(transactions)
        total_transactions += len(transactions)

    # Only write the output file once every input has been parsed AND
    # verified. If anything above failed we exited before reaching here,
    # so no partial/incorrect QIF or CSV is ever left on disk.
    try:
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            f.write(generator.generate())
    except Exception as e:
        # Defensive cleanup: if writing started and failed, remove the
        # half-written file so the user is never left with bad output.
        if os.path.exists(output_path):
            try:
                os.unlink(output_path)
            except OSError:
                pass
        print(f"Error writing output file {output_path}: {e}")
        sys.exit(1)

    print(f"Successfully converted {total_transactions} transactions from {len(input_paths)} files to {output_path} (format: {fmt})")

if __name__ == '__main__':
    main()

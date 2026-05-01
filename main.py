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
    """Generate a QIF file compliant with the QIF specification.

    Spec reference:
      https://www.w3.org/2000/10/swap/pim/qif-doc/QIF-doc.htm

    Compliance highlights:
      - Single `!Type:<account>` header on the first line, with `<account>`
        drawn from the spec's allowed set (`Bank`, `CCard`, `Cash`, `Oth A`,
        `Oth L`, `Invst`).
      - Each record is terminated by `^` on its own line.
      - CRLF line endings, matching the bundled reference sample and
        historical QIF files.
      - Field values are sanitised so embedded line breaks/tabs/control
        characters never break the line-oriented record structure.
      - Where splits are present, their amounts sum to the parent
        transaction total (enforced in `Transaction.to_qif`).
    """

    LINE_ENDING = "\r\n"
    _SPEC_TYPE_MAP = {
        "creditcard": "CCard",
        "credit": "CCard",
        "ccard": "CCard",
        "cash": "Cash",
        "invst": "Invst",
        "investment": "Invst",
        "otha": "Oth A",
        "othl": "Oth L",
    }

    def __init__(self, account_type="Bank"):
        self.account_type = self._normalise_type(account_type)
        self.transactions = []

    @classmethod
    def _normalise_type(cls, account_type):
        key = (account_type or "").lower().replace(" ", "").replace("-", "")
        return cls._SPEC_TYPE_MAP.get(key, "Bank")

    def add_transactions(self, transactions):
        self.transactions.extend(transactions)

    def generate(self):
        # Header is emitted exactly once on the first line.
        parts = [f"!Type:{self.account_type}"]
        for t in self.transactions:
            # Each record is already a multi-line block ending in `^`.
            # We rejoin its internal LFs to CRLF here so the whole file
            # uses consistent CRLF terminators per the spec.
            parts.append(t.to_qif().replace("\n", self.LINE_ENDING))
        return self.LINE_ENDING.join(parts) + self.LINE_ENDING

# QIF spec-legal account types and field tags
# (https://www.w3.org/2000/10/swap/pim/qif-doc/QIF-doc.htm).
QIF_SPEC_TYPES = {"Bank", "CCard", "Cash", "Oth A", "Oth L", "Invst"}
QIF_FIELD_TAGS = set("DTUPMLNCAS$%EF")  # leading char of any non-`^`/`!` line


def validate_qif_compliance(text):
    """Validate that `text` complies with the QIF specification.

    Returns a list of human-readable problem strings (empty = compliant).
    Reference: https://www.w3.org/2000/10/swap/pim/qif-doc/QIF-doc.htm
    """
    problems = []
    if not text:
        return ["Empty QIF output."]

    # The file must start with exactly one !Type: header.
    raw_lines = text.split("\r\n") if "\r\n" in text else text.split("\n")
    # Drop trailing empty line(s) introduced by the final terminator.
    while raw_lines and raw_lines[-1] == "":
        raw_lines.pop()
    if not raw_lines:
        return ["QIF output contains no lines."]

    if not raw_lines[0].startswith("!Type:"):
        problems.append("First line must be a `!Type:` header.")
    else:
        type_value = raw_lines[0][len("!Type:"):].strip()
        if type_value not in QIF_SPEC_TYPES:
            problems.append(
                f"`!Type:{type_value}` is not a spec-legal account type "
                f"(allowed: {sorted(QIF_SPEC_TYPES)}).")
    if sum(1 for ln in raw_lines if ln.startswith("!Type:")) != 1:
        problems.append("QIF file must contain exactly one `!Type:` header.")

    # Per-line tag validation and record terminator placement.
    in_record = False
    has_date = has_total = False
    record_split_sum = 0.0
    record_total = None
    for idx, ln in enumerate(raw_lines[1:], start=2):
        if ln == "":
            continue
        if ln.startswith("!"):
            continue  # other section headers are spec-legal
        if ln == "^":
            if not in_record:
                problems.append(f"Line {idx}: orphan `^` terminator.")
            else:
                if not has_date:
                    problems.append(f"Line {idx}: record missing `D` (date).")
                if not has_total:
                    problems.append(f"Line {idx}: record missing `T` (total).")
                if record_total is not None and record_split_sum != 0.0:
                    if abs(record_split_sum - record_total) > 0.01:
                        problems.append(
                            f"Line {idx}: split amounts sum to "
                            f"{record_split_sum:.2f} but record total is "
                            f"{record_total:.2f}.")
            in_record = False
            has_date = has_total = False
            record_split_sum = 0.0
            record_total = None
            continue
        tag = ln[0]
        if tag not in QIF_FIELD_TAGS:
            problems.append(f"Line {idx}: illegal QIF tag {tag!r} in {ln!r}.")
            continue
        in_record = True
        if tag == "D":
            has_date = True
        elif tag == "T":
            has_total = True
            try:
                record_total = float(ln[1:].replace(",", ""))
            except ValueError:
                problems.append(f"Line {idx}: non-numeric `T` amount: {ln!r}.")
        elif tag == "$":
            try:
                record_split_sum += float(ln[1:].replace(",", ""))
            except ValueError:
                problems.append(f"Line {idx}: non-numeric `$` split: {ln!r}.")

    if in_record:
        problems.append("Final record is not terminated by `^`.")

    return problems


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
        rendered = generator.generate()
        # For QIF output, additionally validate that the rendered text
        # complies with the QIF specification before persisting it.
        if fmt == 'qif' and not args.skip_verify:
            qif_problems = validate_qif_compliance(rendered)
            if qif_problems:
                print("QIF spec compliance check FAILED:")
                for p in qif_problems:
                    print(f"  - {p}")
                print("No output file was written.")
                sys.exit(1)
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            f.write(rendered)
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

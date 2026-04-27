import argparse
import sys
import csv
import io
import os
from parsers import get_parser, Transaction

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

def main():
    parser = argparse.ArgumentParser(description='Convert bank statements in PDF to QIF or CSV.')
    parser.add_argument('bank', help='Bank name (e.g., ANZ, NAB, Macquarie)')
    parser.add_argument('account_type', help='Account type (e.g., CreditCard, BankAcc)')
    parser.add_argument('-i', '--input', nargs='+', required=True, dest='statement_filenames', help='Path to one or more PDF statement files')
    parser.add_argument('-o', '--output', default='transactions.qif', dest='output_filename', help='Output filename (default: transactions.qif)')
    parser.add_argument('--format', choices=['qif', 'csv'], help='Output format (qif or csv). If not specified, inferred from output_filename extension.')

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

    if fmt == 'csv':
        generator = CSVGenerator()
    else:
        generator = QIFGenerator(args.account_type)

    total_transactions = 0
    for filename in input_paths:
        pdf_parser = get_parser(args.bank, args.account_type, filename)
        if not pdf_parser:
            print(f"Error: Unsupported bank or account type: {args.bank} - {args.account_type}")
            sys.exit(1)

        try:
            transactions = pdf_parser.parse()
            if not transactions:
                print(f"Warning: No transactions found in {filename}.")
            else:
                generator.add_transactions(transactions)
                total_transactions += len(transactions)
        except Exception as e:
            print(f"Error parsing PDF {filename}: {e}")
            sys.exit(1)
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        f.write(generator.generate())
    
    print(f"Successfully converted {total_transactions} transactions from {len(input_paths)} files to {output_path} (format: {fmt})")

if __name__ == '__main__':
    main()

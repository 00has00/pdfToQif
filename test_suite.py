"""
Comprehensive test suite for the pdfToQif application.

Covers:
  1. Input validation (CLI + path safety)
  2. Transaction identification via balance equation
     (Opening + Sum(transactions) == Closing)
  3. Fee / interest / tax identification and split breakdown
  4. Output format validation (QIF / CSV)
  5. Parser dispatch and the Transaction data model

Run with:
    python3 -m unittest test_suite -v
or:
    python3 test_suite.py
"""

import csv
import io
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime

from main import (
    QIFGenerator,
    CSVGenerator,
    validate_path,
    validate_qif_compliance,
    QIF_SPEC_TYPES,
    QIF_FIELD_TAGS,
)
from parsers import (
    Transaction,
    get_parser,
    discover_samples,
    NABBankAccountParser,
    NABCreditCardParser,
    ANZCreditCardParser,
    MacquarieBankAccParser,
)


# ---------------------------------------------------------------------------
# Sample statement metadata is built dynamically from the contents of the
# `sample-statements/` directory: each PDF's bank + account type is inferred
# from its filename, and opening / closing balances are extracted directly
# from the PDF text. Drop a new statement into that directory and these
# tests will pick it up automatically — no fixture edits required.
#
# `min_transactions` is a generic "non-empty statement" sanity floor that
# applies to every discovered sample, regardless of bank.
# ---------------------------------------------------------------------------
MIN_TRANSACTIONS_PER_STATEMENT = 10
SAMPLES = discover_samples()
assert SAMPLES, (
    "No usable sample statements were discovered in 'sample-statements/'. "
    "Add at least one supported PDF or check that filenames include the bank "
    "and account-type tokens (e.g. 'NAB-BankAcc-...pdf')."
)


def _parsed_cache():
    """Parse each sample once and cache the result for the whole test run."""
    if not hasattr(_parsed_cache, "_data"):
        data = {}
        for s in SAMPLES:
            parser = get_parser(s["bank"], s["acc"], s["file"])
            data[s["name"]] = parser.parse()
        _parsed_cache._data = data
    return _parsed_cache._data


def _find_sample(bank, acc):
    """Locate a discovered sample by (bank, account_type), or None if absent."""
    for s in SAMPLES:
        if s["bank"].lower() == bank.lower() and s["acc"].lower() == acc.lower():
            return s
    return None


# ---------------------------------------------------------------------------
# 1. Input validation
# ---------------------------------------------------------------------------
class TestInputValidation(unittest.TestCase):

    def test_missing_input_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            validate_path("/tmp/does_not_exist_12345.pdf", must_exist=True)

    def test_forbidden_system_path_rejected(self):
        with self.assertRaises(ValueError):
            validate_path("/etc/passwd", must_exist=False)

    def test_valid_existing_file_accepted(self):
        path = SAMPLES[0]["file"]
        resolved = validate_path(path, must_exist=True)
        self.assertTrue(os.path.isabs(resolved))
        self.assertTrue(os.path.exists(resolved))

    def test_output_path_creates_parent_dir(self):
        # Use a project-local tmp dir – /var/folders is on the forbidden list on macOS.
        base = os.path.join(os.path.dirname(__file__), ".pytest_tmp")
        try:
            target = os.path.join(base, "nested", "sub", "out.qif")
            resolved = validate_path(target, must_exist=False)
            self.assertTrue(os.path.isdir(os.path.dirname(resolved)))
        finally:
            import shutil
            shutil.rmtree(base, ignore_errors=True)

    def test_unknown_bank_returns_none(self):
        self.assertIsNone(get_parser("Unknown", "Bank", "x.pdf"))

    def test_cli_rejects_missing_file(self):
        result = subprocess.run(
            [sys.executable, "main.py", "NAB", "BankAcc",
             "-i", "/tmp/no_such_file_xyz.pdf",
             "-o", "/tmp/out.qif"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", (result.stdout + result.stderr).lower())

    def test_cli_rejects_unknown_bank(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fake:
            fake.write(b"%PDF-1.4 fake")
            fake_path = fake.name
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "out.qif")
                result = subprocess.run(
                    [sys.executable, "main.py", "Foobar", "BankAcc",
                     "-i", fake_path, "-o", out],
                    capture_output=True, text=True,
                )
                self.assertNotEqual(result.returncode, 0)
        finally:
            os.unlink(fake_path)


# ---------------------------------------------------------------------------
# 2. Balance verification – sum of transactions matches opening/closing
# ---------------------------------------------------------------------------
class TestBalanceEquation(unittest.TestCase):
    """For each discovered statement: opening + sum(transactions) == closing
    (±0.01). Driven by `subTest` so adding new samples to
    `sample-statements/` automatically extends coverage."""

    def test_balance_equation_for_every_sample(self):
        for sample in SAMPLES:
            with self.subTest(sample=sample["name"]):
                txns = _parsed_cache()[sample["name"]]
                self.assertGreaterEqual(
                    len(txns), MIN_TRANSACTIONS_PER_STATEMENT,
                    f"Too few transactions parsed from {sample['name']}",
                )
                total = sum(t.amount for t in txns)
                calc_close = sample["opening"] + total
                self.assertAlmostEqual(
                    calc_close, sample["closing"], places=2,
                    msg=(f"{sample['name']}: opening {sample['opening']:.2f} "
                         f"+ sum {total:.2f} = {calc_close:.2f}, "
                         f"expected {sample['closing']:.2f}"),
                )


# ---------------------------------------------------------------------------
# 3. Fees / interest / taxes / splits identification
# ---------------------------------------------------------------------------
class TestFeesAndSplits(unittest.TestCase):

    def test_anz_overseas_fee_recorded_as_split(self):
        """ANZ statements have INCL OVERSEAS TXN FEE lines that must become
        splits on the parent transaction (and not double-count the amount)."""
        s = _find_sample("ANZ", "CreditCard")
        if not s:
            self.skipTest("No ANZ CreditCard sample present")
        txns = _parsed_cache()[s["name"]]
        with_splits = [t for t in txns if t.splits]
        self.assertGreater(len(with_splits), 0,
                           "Expected at least one ANZ transaction with overseas fee splits")
        for t in with_splits:
            for memo, amt in t.splits:
                self.assertIn("OVERSEAS TXN FEE", memo.upper())
                self.assertLess(amt, 0, "Fee splits should be negative")

    def test_anz_interest_charged_present_and_negative(self):
        anz_samples = [s for s in SAMPLES if s["bank"] == "ANZ" and s["acc"] == "CreditCard"]
        if not anz_samples:
            self.skipTest("No ANZ CreditCard sample present")
        # Find at least one ANZ statement that contains an INTEREST line and
        # assert each such line is negative. Some ANZ statements may not
        # accrue interest in a given month (full payer), so we tolerate that.
        any_interest_seen = False
        for s in anz_samples:
            txns = _parsed_cache()[s["name"]]
            interest = [t for t in txns if "INTEREST" in t.payee.upper()]
            for t in interest:
                any_interest_seen = True
                self.assertLess(t.amount, 0,
                                f"Interest charge in {s['name']} must be negative")
        if not any_interest_seen:
            self.skipTest("No INTEREST CHARGED lines in any ANZ sample")

    def test_credit_card_payments_are_positive(self):
        cc_samples = [s for s in SAMPLES if s["acc"] == "CreditCard"]
        if not cc_samples:
            self.skipTest("No CreditCard samples present")
        # A genuine card payment is identified by the bank's own marker
        # ("PAYMENT - THANKYOU", "INTERNET PAYMENT", "PAYMENT RECEIVED",
        # etc.) rather than the substring "PAYMENT" anywhere — merchant
        # names like "TELSTRA BILL PAYMENT" are purchases, not payments.
        import re as _re
        # NAB renders text with no spaces ("INTERNETPAYMENT..."), ANZ uses
        # "PAYMENT - THANKYOU" — so we match without strict word boundaries.
        payment_pat = _re.compile(
            r"(PAYMENT\s*[-–]\s*THANKYOU|INTERNET\s*PAYMENT|"
            r"PAYMENT\s+RECEIVED|THANK\s*YOU)"
        )
        for s in cc_samples:
            name = s["name"]
            txns = _parsed_cache()[name]
            payments = [t for t in txns if payment_pat.search(t.payee.upper())]
            self.assertGreater(len(payments), 0,
                               f"No card-payment entries in {name}")
            for p in payments:
                self.assertGreater(
                    p.amount, 0,
                    f"Payment in {name} should be positive: {p.payee} {p.amount}",
                )

    def test_credit_card_purchases_are_negative(self):
        """Random sanity check: most credit-card non-payment lines are negative.
        Runs against the largest available credit-card sample."""
        cc_samples = [s for s in SAMPLES if s["acc"] == "CreditCard"]
        if not cc_samples:
            self.skipTest("No CreditCard samples present")
        largest = max(cc_samples, key=lambda s: len(_parsed_cache()[s["name"]]))
        txns = _parsed_cache()[largest["name"]]
        purchases = [t for t in txns if "PAYMENT" not in t.payee.upper()]
        negatives = [t for t in purchases if t.amount < 0]
        self.assertGreater(len(negatives) / max(1, len(purchases)), 0.9,
                           "At least 90% of credit-card purchases should be debits")

    def test_nab_bank_excludes_tax_summary(self):
        """The NAB bank statement has a 'Government charges' / tax-summary
        section at the end that is informational only and must not be parsed
        as transactions (otherwise the balance equation would fail – which is
        already covered, but we also assert no duplicate tax lines)."""
        s = _find_sample("NAB", "BankAcc")
        if not s:
            self.skipTest("No NAB BankAcc sample present")
        txns = _parsed_cache()[s["name"]]
        tax_lines = [t for t in txns if "government" in t.payee.lower()
                     or "tax summary" in t.payee.lower()]
        self.assertEqual(tax_lines, [],
                         "Tax summary section must not produce transactions")

    def test_split_sum_consistency(self):
        """Where splits exist, they should not exceed the parent amount in magnitude."""
        for name, txns in _parsed_cache().items():
            for t in txns:
                if not t.splits:
                    continue
                split_total = sum(a for _, a in t.splits)
                self.assertLessEqual(abs(split_total), abs(t.amount) + 0.01,
                                     f"{name}: splits exceed parent amount for {t.payee}")


# ---------------------------------------------------------------------------
# 4. Output format validation (QIF + CSV)
# ---------------------------------------------------------------------------
class TestQIFOutput(unittest.TestCase):

    def _build(self, account_type, txns):
        g = QIFGenerator(account_type)
        g.add_transactions(txns)
        return g.generate()

    def test_qif_bank_header(self):
        out = self._build("BankAcc", [Transaction(datetime(2024, 1, 2), -10.0, "Test")])
        self.assertTrue(out.startswith("!Type:Bank\r\n"))

    def test_qif_credit_card_header(self):
        out = self._build("CreditCard", [Transaction(datetime(2024, 1, 2), -10.0, "Test")])
        self.assertTrue(out.startswith("!Type:CCard\r\n"))

    def test_qif_record_structure(self):
        t = Transaction(datetime(2024, 3, 15), -42.50, "Coffee Shop", memo="Latte", num="123")
        out = t.to_qif()
        self.assertIn("D15/03/24", out)
        self.assertIn("T-42.50", out)
        self.assertIn("PCoffee Shop", out)
        self.assertIn("MLatte", out)
        self.assertIn("N123", out)
        self.assertTrue(out.endswith("^"))

    def test_qif_splits_rendered(self):
        t = Transaction(datetime(2024, 3, 15), -100.0, "Foreign Purchase")
        t.add_split("OVERSEAS TXN FEE", -3.0)
        out = t.to_qif()
        self.assertIn("SOVERSEAS TXN FEE", out)
        self.assertIn("$-3.00", out)

    def test_qif_full_pipeline_for_each_sample(self):
        for s in SAMPLES:
            txns = _parsed_cache()[s["name"]]
            out = self._build(s["acc"], txns)
            self.assertTrue(out.startswith("!Type:"), f"{s['name']} QIF missing header")
            self.assertEqual(out.count("^"), len(txns),
                             f"{s['name']}: each transaction must end with '^'")


class TestCSVOutput(unittest.TestCase):

    def _build(self, txns):
        g = CSVGenerator()
        g.add_transactions(txns)
        return g.generate()

    def test_csv_header(self):
        out = self._build([Transaction(datetime(2024, 1, 2), -10.0, "Test")])
        self.assertTrue(out.startswith("Date,Amount,Payee,Memo,Num"))

    def test_csv_row_count_and_parseable(self):
        for s in SAMPLES:
            txns = _parsed_cache()[s["name"]]
            out = self._build(txns)
            reader = list(csv.reader(io.StringIO(out)))
            self.assertEqual(reader[0], ["Date", "Amount", "Payee", "Memo", "Num"])
            self.assertEqual(len(reader) - 1, len(txns),
                             f"{s['name']}: CSV row count mismatch")
            # Amounts must all be valid floats
            for row in reader[1:]:
                float(row[1])

    def test_csv_amount_sum_matches_balance(self):
        for s in SAMPLES:
            txns = _parsed_cache()[s["name"]]
            out = self._build(txns)
            reader = list(csv.reader(io.StringIO(out)))
            total = sum(float(r[1]) for r in reader[1:])
            self.assertAlmostEqual(s["opening"] + total, s["closing"], places=2)


# ---------------------------------------------------------------------------
# 5. Parser dispatch / Transaction model sanity
# ---------------------------------------------------------------------------
class TestParserDispatch(unittest.TestCase):

    def test_dispatch_returns_correct_classes(self):
        self.assertIsInstance(get_parser("NAB", "BankAcc", "x"), NABBankAccountParser)
        self.assertIsInstance(get_parser("NAB", "CreditCard", "x"), NABCreditCardParser)
        self.assertIsInstance(get_parser("ANZ", "CreditCard", "x"), ANZCreditCardParser)
        self.assertIsInstance(get_parser("Macquarie", "BankAcc", "x"), MacquarieBankAccParser)

    def test_dispatch_case_insensitive(self):
        self.assertIsInstance(get_parser("nab", "bankacc", "x"), NABBankAccountParser)
        self.assertIsInstance(get_parser("MACQUARIE", "BANK", "x"), MacquarieBankAccParser)

    def test_transaction_zero_amount_normalised(self):
        t = Transaction(datetime(2024, 1, 1), -0.001, "Tiny")
        self.assertIn("T0.00", t.to_qif())


# ---------------------------------------------------------------------------
# 6. QIF specification compliance
# Reference: https://www.w3.org/2000/10/swap/pim/qif-doc/QIF-doc.htm
# (also pointed to by sample-qif-files/QIF Format Definition.txt)
# ---------------------------------------------------------------------------
class TestQIFSpecCompliance(unittest.TestCase):

    def _build(self, account_type, txns):
        g = QIFGenerator(account_type)
        g.add_transactions(txns)
        return g.generate()

    def test_validator_accepts_minimal_record(self):
        out = self._build("BankAcc", [Transaction(datetime(2024, 1, 2), -10.0, "Test")])
        self.assertEqual(validate_qif_compliance(out), [])

    def test_validator_rejects_missing_header(self):
        bad = "D01/01/24\r\nT-1.00\r\nPx\r\n^\r\n"
        self.assertTrue(any("!Type:" in p for p in validate_qif_compliance(bad)))

    def test_validator_rejects_illegal_type(self):
        bad = "!Type:Garbage\r\nD01/01/24\r\nT-1.00\r\nPx\r\n^\r\n"
        self.assertTrue(any("spec-legal" in p for p in validate_qif_compliance(bad)))

    def test_validator_rejects_illegal_tag(self):
        bad = "!Type:Bank\r\nD01/01/24\r\nT-1.00\r\nXbogus\r\nPx\r\n^\r\n"
        self.assertTrue(any("illegal QIF tag" in p for p in validate_qif_compliance(bad)))

    def test_validator_rejects_unterminated_record(self):
        bad = "!Type:Bank\r\nD01/01/24\r\nT-1.00\r\nPx\r\n"
        self.assertTrue(any("not terminated" in p for p in validate_qif_compliance(bad)))

    def test_validator_rejects_orphan_terminator(self):
        bad = "!Type:Bank\r\n^\r\n"
        self.assertTrue(any("orphan" in p for p in validate_qif_compliance(bad)))

    def test_validator_rejects_split_total_mismatch(self):
        bad = ("!Type:Bank\r\nD01/01/24\r\nT-100.00\r\nPx\r\n"
               "Sa\r\n$-10.00\r\nSb\r\n$-20.00\r\n^\r\n")
        # Splits sum to -30 but T is -100; generator would auto-balance, but
        # the validator must catch this when fed hand-crafted input.
        self.assertTrue(any("split" in p.lower() for p in validate_qif_compliance(bad)))

    def test_only_one_type_header(self):
        out = self._build("BankAcc", [Transaction(datetime(2024, 1, 2), -10.0, "Test")])
        self.assertEqual(out.count("!Type:"), 1)

    def test_type_header_value_is_spec_legal(self):
        for acc in ("BankAcc", "CreditCard", "Cash", "Invst"):
            out = self._build(acc, [Transaction(datetime(2024, 1, 2), -1.0, "T")])
            value = out.split("\r\n", 1)[0][len("!Type:"):]
            self.assertIn(value, QIF_SPEC_TYPES)

    def test_crlf_line_endings(self):
        out = self._build("BankAcc", [Transaction(datetime(2024, 1, 2), -10.0, "Test")])
        # Every newline in the file should be CRLF.
        self.assertNotIn("\n", out.replace("\r\n", ""))

    def test_every_line_uses_legal_tag(self):
        for s in SAMPLES:
            txns = _parsed_cache()[s["name"]]
            out = self._build(s["acc"], txns)
            for ln in out.split("\r\n"):
                if not ln or ln == "^" or ln.startswith("!"):
                    continue
                self.assertIn(ln[0], QIF_FIELD_TAGS,
                              f"{s['name']}: illegal QIF tag in line {ln!r}")

    def test_each_record_terminated_by_caret_on_own_line(self):
        for s in SAMPLES:
            txns = _parsed_cache()[s["name"]]
            out = self._build(s["acc"], txns)
            # Number of standalone '^' lines must equal the transaction count.
            terminator_lines = [ln for ln in out.split("\r\n") if ln == "^"]
            self.assertEqual(len(terminator_lines), len(txns),
                             f"{s['name']}: caret-terminator count mismatch")

    def test_split_amounts_sum_to_total(self):
        """Per spec: when splits exist, Σ$ must equal T (within rounding)."""
        for s in SAMPLES:
            txns = _parsed_cache()[s["name"]]
            out = self._build(s["acc"], txns)
            self.assertEqual(validate_qif_compliance(out), [],
                             f"{s['name']}: QIF compliance issues found")

    def test_no_embedded_control_chars_in_fields(self):
        t = Transaction(datetime(2024, 1, 2), -10.0,
                        "Bad\nPayee\twith\rbreaks", memo="multi\nline")
        out = t.to_qif()
        # Payee/memo lines must not embed CR/LF/TAB.
        for ln in out.split("\n"):
            if ln.startswith("P") or ln.startswith("M"):
                self.assertNotIn("\r", ln)
                self.assertNotIn("\t", ln)

    def test_round_trip_transaction_count_and_sum(self):
        """Re-parse the generated QIF and assert count/Σ amount round-trip."""
        for s in SAMPLES:
            txns = _parsed_cache()[s["name"]]
            out = self._build(s["acc"], txns)
            count = 0
            total = 0.0
            for ln in out.split("\r\n"):
                if ln.startswith("T"):
                    total += float(ln[1:])
                if ln == "^":
                    count += 1
            self.assertEqual(count, len(txns), f"{s['name']}: round-trip count")
            self.assertAlmostEqual(total, sum(t.amount for t in txns), places=2,
                                   msg=f"{s['name']}: round-trip sum")

    def test_full_pipeline_passes_validator_for_each_sample(self):
        for s in SAMPLES:
            txns = _parsed_cache()[s["name"]]
            out = self._build(s["acc"], txns)
            problems = validate_qif_compliance(out)
            self.assertEqual(problems, [],
                             f"{s['name']}: spec violations: {problems}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

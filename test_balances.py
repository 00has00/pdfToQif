"""
Lightweight balance verification script.

Discovers every PDF under `sample-statements/` dynamically — no hard-coded
sample list — and checks that for each statement:

    opening_balance + sum(parsed_transactions) == closing_balance

This is the same invariant covered by `test_suite.py::TestBalanceEquation`,
but exposed as a standalone runnable script for quick spot checks.
"""

from parsers import discover_samples, get_parser


def verify():
    samples = discover_samples()
    if not samples:
        print("No sample statements discovered in 'sample-statements/'.")
        return False

    all_ok = True
    for sample in samples:
        print(f"Verifying {sample['file']}...")
        parser = get_parser(sample["bank"], sample["acc"], sample["file"])
        if not parser:
            print(f"FAILED: No parser found for {sample['bank']} {sample['acc']}")
            all_ok = False
            continue

        try:
            transactions = parser.parse()
            total_sum = sum(t.amount for t in transactions)
            calculated_closing = sample["opening"] + total_sum

            diff = abs(calculated_closing - sample["closing"])
            if diff < 0.01:
                print(f"SUCCESS: Sum of {len(transactions)} transactions matches balances.")
                print(f"  Opening: {sample['opening']:.2f}, "
                      f"Sum: {total_sum:.2f}, "
                      f"Closing: {sample['closing']:.2f}")
            else:
                all_ok = False
                print("FAILED: Balance mismatch!")
                print(f"  Opening: {sample['opening']:.2f}")
                print(f"  Sum of transactions: {total_sum:.2f}")
                print(f"  Expected Closing: {sample['closing']:.2f}")
                print(f"  Calculated Closing: {calculated_closing:.2f}")
                print(f"  Difference: {diff:.2f}")
        except Exception as e:
            all_ok = False
            print(f"ERROR parsing {sample['file']}: {e}")
        print("-" * 40)

    return all_ok


if __name__ == "__main__":
    verify()

import pdfplumber
import re
from datetime import datetime

class Transaction:
    def __init__(self, date, amount, payee, memo=None, num=None):
        self.date = date
        self.amount = amount
        self.payee = payee
        self.memo = memo
        self.num = num
        self.splits = [] # List of (memo, amount)

    def add_split(self, memo, amount):
        self.splits.append((memo, amount))

    def to_qif(self):
        date_str = self.date.strftime("%d/%m/%y")
        # Ensure amount is not -0.00
        amount_val = self.amount if abs(self.amount) > 0.005 else 0.0
        lines = [f"D{date_str}", f"T{amount_val:.2f}", f"P{self.payee}"]
        if self.num is not None: lines.append(f"N{self.num}")
        if self.memo: lines.append(f"M{self.memo}")
        
        for s_memo, s_amount in self.splits:
            lines.append(f"S{s_memo}")
            lines.append(f"${s_amount:.2f}")

        lines.append("^")
        return "\n".join(lines)

    def to_csv_row(self):
        date_str = self.date.strftime("%Y-%m-%d")
        amount_val = self.amount if abs(self.amount) > 0.005 else 0.0
        # For CSV, we'll just include the total amount. 
        # Alternatively, we could include split info in memo.
        memo_with_splits = self.memo or ""
        if self.splits:
            split_info = "; ".join([f"{m}: {a:.2f}" for m, a in self.splits])
            if memo_with_splits:
                memo_with_splits += f" (Splits: {split_info})"
            else:
                memo_with_splits = f"Splits: {split_info}"
        
        return [date_str, f"{amount_val:.2f}", self.payee, memo_with_splits, self.num or ""]

class BaseParser:
    def __init__(self, filename):
        self.filename = filename
    def parse(self): raise NotImplementedError

class NABBankAccountParser(BaseParser):
    def parse(self):
        transactions = []
        with pdfplumber.open(self.filename) as pdf:
            prev_balance = None
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue
                lines = text.split('\n')
                current_date = None
                for line in lines:
                    if "Opening balance" in line:
                        match = re.search(r'Opening balance \$([\d,]+\.\d{2})', line)
                        if match:
                            prev_balance = float(match.group(1).replace(',', ''))
                        continue
                    
                    if "Brought forward" in line:
                        match = re.search(r'Brought forward\s+([\d,]+\.\d{2})', line)
                        if match:
                            prev_balance = float(match.group(1).replace(',', ''))
                        continue

                    # Match line with date
                    match = re.search(r'^(\d{1,2} [A-Z][a-z]{2} \d{4})\s+([\s\S]*?)(?:\s+([\d,]+\.\d{2}))(?:\s+([\d,]+\.\d{2}))?(?:\s+(Cr|Dr))?$', line)
                    if match:
                        date_str, particulars, amt1, amt2, suffix = match.groups()
                        current_date = datetime.strptime(date_str, "%d %b %Y")
                        
                        if "Brought forward" in particulars:
                            prev_balance = float(amt1.replace(',', ''))
                            continue

                        # If two amounts, amt2 is balance. amt1 is transaction.
                        if amt2:
                            amount = float(amt1.replace(',', ''))
                            balance = float(amt2.replace(',', ''))
                            # Determine sign by balance change
                            if prev_balance is not None:
                                if abs((prev_balance + amount) - balance) < 0.01:
                                    pass # positive
                                elif abs((prev_balance - amount) - balance) < 0.01:
                                    amount = -amount
                            prev_balance = balance
                            t = Transaction(current_date, amount, particulars.strip().rstrip('.'))
                            transactions.append(t)
                        else:
                            # Only one amount, could be transaction or balance.
                            amount = float(amt1.replace(',', ''))
                            t = Transaction(current_date, amount, particulars.strip().rstrip('.'))
                            transactions.append(t)
                        continue

                    # Match line without date but with amount (could be a multi-line part of transaction OR a fee)
                    match_no_date = re.search(r'^([\s\S]*?)\s+([\d,]+\.\d{2})(?:\s+([\d,]+\.\d{2}))?(?:\s+(Cr|Dr))?$', line)
                    if match_no_date and current_date:
                        particulars, amt1, amt2, suffix = match_no_date.groups()
                        part_strip = particulars.strip().rstrip('.')
                        
                        # Check if it looks like a fee and we have a previous transaction to attach to
                        if transactions and ("Fee" in part_strip or "Charge" in part_strip):
                            amount = float(amt1.replace(',', ''))
                            # For NAB Bank, fees are usually debits (negative)
                            # We can try to use balance if available to be sure
                            if amt2:
                                balance = float(amt2.replace(',', ''))
                                if prev_balance is not None:
                                    if abs((prev_balance - amount) - balance) < 0.01:
                                        amount = -amount
                                prev_balance = balance
                            else:
                                # Default to negative for fees in bank account if not sure
                                amount = -abs(amount)
                                
                            transactions[-1].add_split(part_strip, amount)
                            # Update total amount of the transaction to include the fee?
                            # In QIF, the T amount is usually the TOTAL of all splits.
                            transactions[-1].amount += amount
                            continue

                        if len(particulars.strip()) > 3 and not particulars.strip().startswith("Statement") and not particulars.strip().startswith("Carried"):
                            amount = float(amt1.replace(',', ''))
                            if amt2:
                                balance = float(amt2.replace(',', ''))
                                if prev_balance is not None:
                                    if abs((prev_balance + amount) - balance) < 0.01:
                                        pass
                                    elif abs((prev_balance - amount) - balance) < 0.01:
                                        amount = -amount
                                prev_balance = balance
                            else:
                                # For NAB, if it's the second line of a transaction, it might be a debit
                                # BUT we only want to assume debit if it's not a credit.
                                # Let's stick to the safer logic for now or improve it.
                                pass
                            
                            transactions.append(Transaction(current_date, amount, particulars.strip().rstrip('.')))

        return transactions

class NABCreditCardParser(BaseParser):
    def parse(self):
        transactions = []
        with pdfplumber.open(self.filename) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue
                # Date processed Date transaction Details Amount
                # 27/02/26 25/02/26 V4274 ASUPER2000VALEDESAOC 1.42
                matches = re.findall(r'(\d{2}/\d{2}/\d{2})\s+\d{2}/\d{2}/\d{2}\s+V\d{4}\s+([\s\S]*?)\s+([\d,]+\.\d{2})', text)
                for date_str, details, amount_str in matches:
                    date = datetime.strptime(date_str, "%d/%m/%y")
                    amount = float(amount_str.replace(',', ''))
                    # Credit card debits are positive in some systems, but QIF usually wants negative for expenses.
                    # Sample QIF has T-190.59.
                    transactions.append(Transaction(date, -amount, details))
        return transactions

class ANZCreditCardParser(BaseParser):
    def parse(self):
        transactions = []
        with pdfplumber.open(self.filename) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue
                lines = text.split('\n')
                for line in lines:
                    # Match standard transaction lines
                    # DateProc DateTrans [Card] Details Amount Balance
                    match = re.search(r'(\d{2}/\d{2}/\d{4})\s+\d{2}/\d{2}/\d{4}\s+(?:\d{4}\s+)?([\s\S]*?)\s+\$([\d,]+\.\d{2})(?:\s+\$([\d,]+\.\d{2}))?(?:\s*(CR|C R))?', line)
                    if match:
                        date_str, details, amount_str, balance_str, cr = match.groups()
                        date = datetime.strptime(date_str, "%d/%m/%Y")
                        amount = float(amount_str.replace(',', ''))
                        
                        # If it's a payment, it's CR (Credit to the card account)
                        # If it's a purchase, it's a debit from the account (positive in statement, but should be negative in QIF)
                        if cr or "PAYMENT" in details:
                            pass # Keep positive
                        else:
                            amount = -amount
                            
                        transactions.append(Transaction(date, amount, details.strip()))
                        continue

                    # Match fee lines that follow a transaction
                    # Example: 05/10/2020 INCL OVERSEAS TXN FEE 0.09 AUD $8,974.24
                    # Note: These lines have only ONE date (processed date)
                    match_fee = re.search(r'(\d{2}/\d{2}/\d{4})\s+(INCL OVERSEAS TXN FEE\s+([\d,]+\.\d{2})\s+AUD)', line)
                    if match_fee and transactions:
                        date_str, fee_details, fee_amount_str = match_fee.groups()
                        fee_amount = float(fee_amount_str.replace(',', ''))
                        # For credit cards, fees are usually part of the previous transaction's total on the statement?
                        # Wait, let's look at the ANZ statement again.
                        # 05/10/2020 01/10/2020 2586 PAYPAL *PATREON MEMBER 4029357733 $3.02 $8,974.24
                        # 05/10/2020 INCL OVERSEAS TXN FEE 0.09 AUD $8,974.24
                        # The balance is the SAME ($8,974.24). This means the $3.02 ALREADY INCLUDES the $0.09 fee.
                        # So we should split the $3.02 into $2.93 and $0.09 fee.
                        
                        # Update the base amount of the last transaction
                        transactions[-1].amount += fee_amount # amount was negative, adding fee_amount (which should be treated as negative)
                        # Actually if amount is -3.02 and fee is 0.09, we want base -2.93 and fee -0.09.
                        # So base = -3.02 - (-0.09) = -2.93.
                        # But wait, fee_amount is extracted as positive 0.09.
                        
                        transactions[-1].amount += fee_amount # -3.02 + 0.09 = -2.93
                        transactions[-1].add_split(fee_details, -fee_amount)
                        continue
                    
                    # Also match "INTEREST CHARGED" or other lines that might have slightly different format
                    if "INTEREST CHARGED" in line:
                        match_int = re.search(r'(\d{2}/\d{2}/\d{4})\s+\d{2}/\d{2}/\d{4}\s+([\s\S]*?)\s+\$([\d,]+\.\d{2})', line)
                        if match_int:
                            date_str, details, amount_str = match_int.groups()
                            date = datetime.strptime(date_str, "%d/%m/%Y")
                            amount = -float(amount_str.replace(',', ''))
                            transactions.append(Transaction(date, amount, details.strip()))

        return transactions

class MacquarieBankAccParser(BaseParser):
    def parse(self):
        transactions = []
        with pdfplumber.open(self.filename) as pdf:
            current_year = datetime.now().year
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue
                # Date doesn't have year in transaction list, but "Jan 2021" header exists.
                lines = text.split('\n')
                current_month_year = None
                for line in lines:
                    month_match = re.search(r'([A-Z][a-z]{2}) (\d{4})', line)
                    if month_match:
                        current_month_year = month_match.groups()
                    
                    trans_match = re.match(r'^(\d{2} [A-Z][a-z]{2})\s+([\s\S]*?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})(CR)?', line)
                    if trans_match and current_month_year:
                        day_month, details, amt1, amt2, cr = trans_match.groups()
                        date_str = f"{day_month} {current_month_year[1]}"
                        date = datetime.strptime(date_str, "%d %b %Y")
                        amount = float(amt1.replace(',', ''))
                        if not cr: # If not CR, it might be a debit
                             amount = -amount
                        transactions.append(Transaction(date, amount, details))
        return transactions

def get_parser(bank, account_type, filename):
    bank = bank.lower()
    acc = account_type.lower()
    if "nab" in bank:
        if "bank" in acc: return NABBankAccountParser(filename)
        if "credit" in acc: return NABCreditCardParser(filename)
    if "anz" in bank:
        return ANZCreditCardParser(filename)
    if "macquarie" in bank:
        return MacquarieBankAccParser(filename)
    return None

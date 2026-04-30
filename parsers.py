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
                
                # Use words to get X positions for better accuracy
                words = page.extract_words()
                
                lines = text.split('\n')
                current_date = None
                in_transactions = False
                
                for line in lines:
                    line = line.strip()
                    if not line: continue
                    
                    if "Opening balance" in line:
                        match = re.search(r'Opening balance \$([\d,]+\.\d{2})', line)
                        if match:
                            prev_balance = float(match.group(1).replace(',', ''))
                        continue
                    
                    if "Transaction Details" in line:
                        in_transactions = True
                        continue
                    
                    if not in_transactions: continue
                    
                    # Look for date
                    date_match = re.match(r'^(\d{1,2} [A-Z][a-z]{2} \d{4})', line)
                    if date_match:
                        current_date = datetime.strptime(date_match.group(1), "%d %b %Y")
                        line = line[len(date_match.group(1)):].strip()

                    if not current_date: continue
                    if "Carried forward" in line: continue
                    if "Brought forward" in line and "Particulars" not in line:
                        match = re.search(r'Brought forward\s+([\d,]+\.\d{2})', line)
                        if match:
                            prev_balance = float(match.group(1).replace(',', ''))
                        continue

                    amt_matches = re.findall(r'([\d,]+\.\d{2})', line)
                    if not amt_matches: continue
                    
                    # For each amount, find its X position to determine if it's Debit, Credit, or Balance
                    amounts_with_x = []
                    # We need to find the word in the words list that matches this amount and is on this line?
                    # Searching by text is okay as long as we are careful.
                    for amt_str in amt_matches:
                         # Find word with this text. We might have multiple.
                         # Let's just find the one that hasn't been used yet on this page?
                         # Or simpler: just use the regex to find the transaction part.
                         pass
                    
                    # Actually, let's use the balance change logic first, it's more general if we have balances.
                    # But we only have balances on some lines.
                    
                    first_amt_pos = line.find(amt_matches[0])
                    particulars = line[:first_amt_pos].strip()
                    
                    if len(amt_matches) >= 2:
                        amount = float(amt_matches[-2].replace(',', ''))
                        balance = float(amt_matches[-1].replace(',', ''))
                        
                        if prev_balance is not None:
                            if abs((prev_balance + amount) - balance) < 0.01:
                                pass # positive
                            elif abs((prev_balance - amount) - balance) < 0.01:
                                amount = -amount
                            else:
                                if abs((prev_balance - amount) - balance) < abs((prev_balance + amount) - balance):
                                    amount = -amount
                        
                        prev_balance = balance
                        transactions.append(Transaction(current_date, amount, particulars))
                    elif len(amt_matches) == 1:
                        amount = float(amt_matches[0].replace(',', ''))
                        # For NAB, we can use the "guess by particulars" then verify when next balance comes.
                        # OR, since I saw the X positions, I'll use a hack: check if the amount is shifted right.
                        # But I don't have X positions here easily without re-parsing.
                        
                        # Let's try balance tracking with a "pending" list.
                        if "Brought forward" in particulars or "Carried forward" in particulars:
                            prev_balance = float(amt_matches[0].replace(',', ''))
                        else:
                            # Default to negative for now, but we'll fix it if it fails balance check?
                            # Actually, most transactions are debits.
                            if "Fee" in particulars or "Charge" in particulars:
                                amount = -amount
                            elif "Payroll" in particulars or "Interest" in particulars or "Benefit" in particulars or "Rebate" in particulars:
                                pass # keep positive
                            else:
                                # Guess debit for now. 
                                # Wait, I'll just use the X-position hack by looking at the line's length or something?
                                # No, let's use the X0 from words.
                                
                                # FIND X0 for this amount
                                amt_x0 = 0
                                for word in words:
                                     if word['text'] == amt_matches[0]:
                                          # Check if it's roughly on the same line (this is hard)
                                          # Let's just use the first one we find that is > previous found word
                                          amt_x0 = word['x0']
                                          # If there are multiple, this is risky.
                                          # But usually amounts are unique enough on a page? No.
                                          
                                # OK, forget X0 for now. Let's use the "Buffer" logic.
                                transactions.append(Transaction(current_date, amount, particulars))
                                # We'll fix the sign of the LAST transaction when we see a balance on the NEXT line.
        
        # Post-process to fix signs using balance tracking
        fixed_transactions = []
        pb = None
        # Find initial balance
        with pdfplumber.open(self.filename) as pdf:
             for page in pdf.pages:
                  text = page.extract_text()
                  m = re.search(r'Opening balance \$([\d,]+\.\d{2})', text)
                  if m:
                       pb = float(m.group(1).replace(',', ''))
                       break
        
        # We need to re-parse and keep track of when we see balances.
        # This is getting complicated. Let's try the X position properly.
        return self._parse_with_x()

    def _parse_with_x(self):
        transactions = []
        with pdfplumber.open(self.filename) as pdf:
            prev_balance = None
            for page in pdf.pages:
                words = page.extract_words()
                current_date = None
                in_transactions = False
                
                # Group words into lines
                lines_words = []
                if not words: continue
                current_line_y = words[0]['top']
                current_line = []
                for w in words:
                    if abs(w['top'] - current_line_y) > 3:
                        lines_words.append(current_line)
                        current_line = []
                        current_line_y = w['top']
                    current_line.append(w)
                lines_words.append(current_line)
                
                for line_w in lines_words:
                    line_text = " ".join([w['text'] for w in line_w])
                    
                    if "Opening balance" in line_text:
                        m = re.search(r'Opening balance \$([\d,]+\.\d{2})', line_text)
                        if m: prev_balance = float(m.group(1).replace(',', ''))
                        continue
                    if "Transaction Details" in line_text:
                        in_transactions = True
                        continue
                    if "The Following Information Concerning This Account Is" in line_text:
                        in_transactions = False
                        continue
                    if not in_transactions: continue
                    
                    # Look for date
                    if re.match(r'^\d{1,2} [A-Z][a-z]{2} \d{4}', line_text):
                        date_str = " ".join([w['text'] for w in line_w[:3]])
                        current_date = datetime.strptime(date_str, "%d %b %Y")
                        line_w = line_w[3:]
                    
                    if not current_date: continue
                    
                    # Find amounts
                    amts = []
                    for w in line_w:
                        m = re.search(r'^[\d,]+\.\d{2}$', w['text'].replace('$', ''))
                        if m:
                            amts.append({'val': float(m.group(0).replace(',', '')), 'x0': w['x0']})
                    
                    if not amts: continue
                    
                    particulars = " ".join([w['text'] for w in line_w if not any(w['text'] == str(a['val']) or w['text'] == f"{a['val']:.2f}" for a in amts)])
                    
                    # Use X positions
                    # Debit < 410, Credit 410-480, Balance > 480
                    trans_amt = None
                    for a in amts:
                        if a['x0'] < 410: # Debit
                            trans_amt = -a['val']
                        elif a['x0'] < 480: # Credit
                            trans_amt = a['val']
                        else: # Balance
                            prev_balance = a['val']
                    
                    if trans_amt is not None:
                        transactions.append(Transaction(current_date, trans_amt, particulars.strip()))
            
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
                matches = re.findall(r'(\d{2}/\d{2}/\d{2})\s+\d{2}/\d{2}/\d{2}\s+V\d{4}\s+([\s\S]*?)\s+([\d,]+\.\d{2})(CR)?', text)
                for date_str, details, amount_str, cr in matches:
                    date = datetime.strptime(date_str, "%d/%m/%y")
                    amount = float(amount_str.replace(',', ''))
                    # Credit card debits (purchases) should be negative in QIF.
                    # Payments (CR) should be positive.
                    if cr:
                        pass 
                    else:
                        amount = -amount
                    transactions.append(Transaction(date, amount, details.strip()))
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
                    match = re.search(r'(\d{2}/\d{2}/\d{4})\s+\d{2}/\d{2}/\d{4}\s+(?:\d{4}\s+)?([\s\S]*?)\s+\$([\d,]+\.\d{2})\s*(CR|C R)?(?:\s+\$([\d,]+\.\d{2}))?(?:\s*(CR|C R))?', line)
                    if match:
                        date_str, details, amount_str, amt_cr, balance_str, bal_cr = match.groups()
                        date = datetime.strptime(date_str, "%d/%m/%Y")
                        amount = float(amount_str.replace(',', ''))
                        
                        # If it's a payment, it's CR (Credit to the card account)
                        if amt_cr or "PAYMENT" in details:
                            pass # Keep positive
                        else:
                            amount = -amount
                            
                        transactions.append(Transaction(date, amount, details.strip()))
                        continue

                    # Match fee lines that follow a transaction
                    match_fee = re.search(r'(\d{2}/\d{2}/\d{4})\s+(INCL OVERSEAS TXN FEE\s+([\d,]+\.\d{2})\s+AUD)', line)
                    if match_fee and transactions:
                        date_str, fee_details, fee_amount_str = match_fee.groups()
                        fee_amount = float(fee_amount_str.replace(',', ''))
                        # Fee is already included in the previous transaction's total on ANZ statement.
                        # We just add a split but DO NOT change the total amount.
                        transactions[-1].add_split(fee_details, -fee_amount)
                        # We also need to adjust the "base" part of the transaction if we want splits to sum up.
                        # But Transaction.to_qif doesn't currently handle the base vs splits well.
                        # For now, let's just NOT change the total amount to keep the sum correct.
                        continue
                    
                    if "INTEREST CHARGED" in line:
                        match_int = re.search(r'(\d{2}/\d{2}/\d{4})\s+\d{2}/\d{2}/\d{4}\s+([\s\S]*?)\s+\$([\d,]+\.\d{2})', line)
                        if match_int:
                            date_str, details, amount_str = match_int.groups()
                            date = datetime.strptime(date_str, "%d/%m/%Y")
                            amount = -float(amount_str.replace(',', ''))
                            transactions.append(Transaction(date, amount, details.strip()))

        return transactions

        return transactions

class MacquarieBankAccParser(BaseParser):
    def parse(self):
        transactions = []
        with pdfplumber.open(self.filename) as pdf:
            prev_balance = None
            current_year = datetime.now().year
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue
                lines = text.split('\n')
                current_month_year = None
                for line in lines:
                    line = line.strip()
                    month_match = re.search(r'([A-Z][a-z]{2}) (\d{4})', line)
                    if month_match:
                        current_month_year = month_match.groups()
                    
                    if "Opening balance" in line:
                        match = re.search(r'Opening balance\s+([\d,]+\.\d{2})', line)
                        if match:
                             prev_balance = float(match.group(1).replace(',', ''))
                        continue

                    trans_match = re.match(r'^(\d{2} [A-Z][a-z]{2})\s+([\s\S]*?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})(CR)?', line)
                    if trans_match and current_month_year:
                        day_month, details, amt1, amt2, cr = trans_match.groups()
                        date_str = f"{day_month} {current_month_year[1]}"
                        date = datetime.strptime(date_str, "%d %b %Y")
                        amount = float(amt1.replace(',', ''))
                        balance = float(amt2.replace(',', ''))
                        
                        if prev_balance is not None:
                             if abs((prev_balance + amount) - balance) < 0.01:
                                 pass
                             elif abs((prev_balance - amount) - balance) < 0.01:
                                 amount = -amount
                             else:
                                 if abs((prev_balance - amount) - balance) < abs((prev_balance + amount) - balance):
                                     amount = -amount
                        
                        prev_balance = balance
                        transactions.append(Transaction(date, amount, details.strip()))
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

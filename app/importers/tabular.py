"""Structured statement formats: CSV, OFX/QFX, QIF, MT940.

These are deterministic and always preferred over OCR when available.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import List, Optional

from app.importers.profiles import column_aliases
from app.importers.types import ImportError_, ParsedRow, ParseResult

MONEY_RE = re.compile(r'[-+]?[\d.,]+')
DATE_FORMATS_DEFAULT = ['%Y-%m-%d', '%d/%m/%Y', '%d/%m/%y', '%m/%d/%Y',
                        '%d %b %y', '%d %b %Y', '%d-%m-%Y', '%Y%m%d']


def parse_amount(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text in {'-', '—'}:
        return None
    negative = text.startswith('(') and text.endswith(')')
    text = text.replace('(', '').replace(')', '')
    text = text.replace('\u00a0', ' ').replace('£', '').replace('$', '').replace('€', '')
    text = text.replace('GBP', '').replace('EUR', '').replace('USD', '').strip()
    credit = bool(re.search(r'\bCR\b', text, re.I))
    debit = bool(re.search(r'\bDR\b', text, re.I))
    text = re.sub(r'\b(CR|DR)\b', '', text, flags=re.I).strip()
    text = text.replace(' ', '')
    if not MONEY_RE.fullmatch(text):
        return None
    # European decimal comma: 1.234,56 or 42,10 or 3200,00
    if ',' in text and ('.' not in text
                        or re.fullmatch(r'-?\+?\d+(\.\d{3})*,\d{2}', text)):
        if re.fullmatch(r'-?\+?\d+(\.\d{3})*,\d{2}', text):
            text = text.replace('.', '').replace(',', '.')
        else:
            text = text.replace(',', '')
    else:
        text = text.replace(',', '')
    try:
        value = float(text)
    except ValueError:
        return None
    if negative or debit:
        value = -abs(value)
    if credit:
        value = abs(value)
    return value


def parse_date(value, formats=None) -> Optional[datetime]:
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    candidates = list(formats or []) + DATE_FORMATS_DEFAULT
    for fmt in candidates:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        from dateutil import parser as dateparser
        return dateparser.parse(text, dayfirst=True, fuzzy=True)
    except Exception:
        return None


def _clean_description(*parts) -> str:
    text = ' '.join(str(p) for p in parts if p not in (None, ''))
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:500]


def _row_confidence(date, amount, description) -> tuple:
    score, issues = 0.0, []
    if date:
        score += 0.4
    else:
        issues.append('date not parsed')
    if amount is not None:
        score += 0.4
    else:
        issues.append('amount not parsed')
    if description:
        score += 0.2
    else:
        issues.append('no description')
    return round(score, 2), issues


# ------------------------------------------------------------------ CSV

def _find_header(rows, profile):
    aliases = {field: set(column_aliases(profile, field))
               for field in ('date', 'description', 'amount', 'out', 'in', 'balance', 'type')}
    for index, row in enumerate(rows[:30]):
        cells = [str(c or '').strip().lower() for c in row]
        found = {field: next((i for i, cell in enumerate(cells) if cell in names), None)
                 for field, names in aliases.items()}
        if found['date'] is not None and any(found[f] is not None for f in ('amount', 'out', 'in')):
            return index, found
    return None, None


def parse_csv(path: str, profile: dict, encoding: str = 'utf-8-sig') -> ParseResult:
    raw = open(path, 'rb').read()
    text = None
    for enc in (encoding, 'utf-8', 'cp1252', 'latin-1'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ImportError_('Could not decode CSV file')

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t|')
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ','
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    header_index, cols = _find_header(rows, profile)
    if header_index is None:
        raise ImportError_('Could not find a header row with date and amount columns')

    formats = profile.get('dates') or DATE_FORMATS_DEFAULT
    parsed: List[ParsedRow] = []
    for row in rows[header_index + 1:]:
        if not any(str(c).strip() for c in row):
            continue

        def cell(field):
            idx = cols.get(field)
            return row[idx] if idx is not None and idx < len(row) else None

        date = parse_date(cell('date'), formats)
        description = _clean_description(cell('description'), cell('type'))
        amount = None
        kind = 'expense'
        out_idx, in_idx = cols.get('out'), cols.get('in')
        if out_idx is not None and out_idx == in_idx:
            # Single signed amount column (Monzo/Starling style)
            value = parse_amount(cell('amount'))
            if value is not None:
                amount = abs(value)
                kind = 'income' if value > 0 else 'expense'
        elif out_idx is not None and parse_amount(cell('out')) is not None:
            amount, kind = abs(parse_amount(cell('out'))), 'expense'
        elif in_idx is not None and parse_amount(cell('in')) is not None:
            amount, kind = abs(parse_amount(cell('in'))), 'income'
        else:
            value = parse_amount(cell('amount'))
            if value is not None:
                amount = abs(value)
                kind = 'income' if value > 0 else 'expense'

        score, issues = _row_confidence(date, amount, description)
        if amount is None or not description:
            continue
        parsed.append(ParsedRow(date=date, description=description, amount=amount, kind=kind,
                                balance=parse_amount(cell('balance')),
                                raw=' | '.join(str(c) for c in row),
                                confidence=score, issues=issues))

    return ParseResult(rows=parsed, file_type='csv', profile_id=profile['id'],
                       bank=profile.get('name'), currency=profile.get('currency', 'GBP'),
                       raw_text=text[:20000],
                       warnings=[] if parsed else ['No transactions found in CSV'])


# ------------------------------------------------------------------ OFX / QFX

def parse_ofx(path: str, profile: dict) -> ParseResult:
    text = open(path, 'r', encoding='utf-8', errors='ignore').read()
    blocks = re.findall(r'<STMTTRN>(.*?)</STMTTRN>', text, re.S | re.I)

    def field(block, tag):
        match = re.search(rf'<{tag}>\s*([^<\r\n]+)', block, re.I)
        return match.group(1).strip() if match else None

    rows = []
    for block in blocks:
        date_text = field(block, 'DTPOSTED') or ''
        date_text = re.sub(r'[^0-9]', '', date_text)[:8]
        date = parse_date(date_text, ['%Y%m%d'])
        amount = parse_amount(field(block, 'TRNAMT'))
        description = _clean_description(field(block, 'NAME'), field(block, 'MEMO'))
        if amount is None:
            continue
        score, issues = _row_confidence(date, amount, description)
        rows.append(ParsedRow(date=date, description=description or 'OFX transaction',
                              amount=abs(amount), kind='income' if amount > 0 else 'expense',
                              external_id=field(block, 'FITID'),
                              raw=block.strip()[:1000], confidence=score, issues=issues))

    currency = profile.get('currency', 'GBP')
    match = re.search(r'<CURDEF>([A-Z]{3})', text)
    return ParseResult(rows=rows, file_type='ofx', profile_id=profile['id'],
                       bank=profile.get('name'), currency=match.group(1) if match else currency,
                       raw_text=text[:20000], warnings=[] if rows else ['No OFX transactions found'])


# ------------------------------------------------------------------ QIF

def parse_qif(path: str, profile: dict) -> ParseResult:
    text = open(path, 'r', encoding='utf-8', errors='ignore').read()
    entries = re.split(r'\n\^?\s*\n|\n\^\s*$', text)
    rows = []
    for entry in entries:
        if not entry.strip():
            continue
        date = amount = None
        payee = memo = ''
        for line in entry.splitlines():
            if not line:
                continue
            tag, value = line[0], line[1:].strip()
            if tag == 'D':
                date = parse_date(value, profile.get('dates'))
            elif tag == 'T':
                amount = parse_amount(value)
            elif tag == 'P':
                payee = value
            elif tag == 'M':
                memo = value
        if amount is None:
            continue
        description = _clean_description(payee, memo)
        score, issues = _row_confidence(date, amount, description)
        rows.append(ParsedRow(date=date, description=description or 'QIF transaction',
                              amount=abs(amount), kind='income' if amount > 0 else 'expense',
                              raw=entry.strip()[:500], confidence=score, issues=issues))
    return ParseResult(rows=rows, file_type='qif', profile_id=profile['id'],
                       bank=profile.get('name'), currency=profile.get('currency', 'GBP'),
                       raw_text=text[:20000], warnings=[] if rows else ['No QIF transactions found'])


# ------------------------------------------------------------------ MT940

def parse_mt940(path: str, profile: dict) -> ParseResult:
    text = open(path, 'r', encoding='utf-8', errors='ignore').read()
    rows = []
    pending = None
    for line in text.splitlines():
        if line.startswith(':61:'):
            match = re.match(r':61:(\d{6})(\d{4})?(C|D|RC|RD)([A-Z]?)([\d.,]+)', line)
            if not match:
                continue
            value_date, _, dc, _, amount = match.groups()
            date = parse_date(value_date, ['%y%m%d'])
            value = parse_amount(amount)
            if value is None:
                continue
            pending = {'date': date, 'amount': value, 'kind': 'income' if 'C' in dc else 'expense'}
        elif line.startswith(':86:') and pending:
            description = _clean_description(line[4:])
            score, issues = _row_confidence(pending['date'], pending['amount'], description)
            rows.append(ParsedRow(date=pending['date'], description=description or 'MT940 entry',
                                  amount=abs(pending['amount']), kind=pending['kind'],
                                  raw=line[:500], confidence=score, issues=issues))
            pending = None
        elif line.startswith(':60F:') or line.startswith(':60M:'):
            pass
    return ParseResult(rows=rows, file_type='mt940', profile_id=profile['id'],
                       bank=profile.get('name'), currency=profile.get('currency', 'GBP'),
                       raw_text=text[:20000], warnings=[] if rows else ['No MT940 transactions found'])


def parse_structured(path: str, profile: dict) -> ParseResult:
    lowered = path.lower()
    if lowered.endswith(('.ofx', '.qfx')):
        return parse_ofx(path, profile)
    if lowered.endswith('.qif'):
        return parse_qif(path, profile)
    if lowered.endswith(('.sta', '.mt940', '.940')):
        return parse_mt940(path, profile)
    if lowered.endswith(('.csv', '.tsv', '.txt')):
        return parse_csv(path, profile)
    # Sniff content
    head = open(path, 'rb').read(4096).decode('latin-1', errors='ignore')
    if '<OFX' in head.upper() or '<STMTTRN>' in head.upper():
        return parse_ofx(path, profile)
    if head.lstrip().startswith('!Type:'):
        return parse_qif(path, profile)
    if head.startswith(':20:') or ':61:' in head:
        return parse_mt940(path, profile)
    return parse_csv(path, profile)

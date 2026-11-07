"""PDF statement parsing from the text layer, with OCR only as a fallback.

The algorithm is deliberately layout-agnostic:

  1. group words into visual lines
  2. find the header row from column *labels* (profile aliases)
  3. discover numeric columns by clustering the right edges of money tokens
  4. bind each header label to its nearest column
  5. read rows: date from leading tokens, description between date and first number,
     amounts assigned by column binding

No per-bank pixel constants. A profile only supplies aliases and date formats.
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.importers.profiles import column_aliases
from app.importers.tabular import parse_amount, parse_date
from app.importers.types import ImportError_, ParsedRow, ParseResult

try:
    import pdfplumber
    PDFPLUMBER = True
except ImportError:  # pragma: no cover
    PDFPLUMBER = False

try:
    import pytesseract
    from pdf2image import convert_from_path
    OCR = True
except ImportError:  # pragma: no cover
    OCR = False

MONEY_TOKEN = re.compile(r'^[+-]?[£$€]?\(?[\d.,]+\)?$')
HEADER_SCAN_LINES = 30
LINE_TOLERANCE = 3
LABEL_GAP = 9


def _money(text: str) -> Optional[float]:
    if not text or text in {'-', '—'}:
        return None
    if not re.search(r'\d', text):
        return None
    return parse_amount(text)


def _group_lines(words: List[dict]) -> List[List[dict]]:
    buckets: Dict[int, List[dict]] = {}
    for word in words:
        bucket = round(word['top'] / LINE_TOLERANCE)
        buckets.setdefault(bucket, []).append(word)
    return [sorted(ws, key=lambda w: w['x0']) for _, ws in sorted(buckets.items())]


def _merge_labels(header_words: List[dict]) -> List[dict]:
    groups, current = [], []
    for word in header_words:
        if current and word['x0'] - current[-1]['x1'] < LABEL_GAP:
            current.append(word)
        else:
            if current:
                groups.append(current)
            current = [word]
    if current:
        groups.append(current)
    return [{'text': ' '.join(w['text'] for w in g).lower().strip(),
             'x0': g[0]['x0'], 'x1': g[-1]['x1']} for g in groups]


def _alias_sets(profile: dict) -> Dict[str, set]:
    return {
        'date': set(column_aliases(profile, 'date')) | {'date'},
        'description': set(column_aliases(profile, 'description')),
        'amount': set(column_aliases(profile, 'amount')) | {'amount'},
        'out': set(column_aliases(profile, 'out')) | {'paid out', 'money out', 'debit'},
        'in': set(column_aliases(profile, 'in')) | {'paid in', 'money in', 'credit'},
        'balance': set(column_aliases(profile, 'balance')) | {'balance'},
    }


def _label_field(text: str, aliases: Dict[str, set]) -> Optional[str]:
    for field in ('date', 'balance', 'out', 'in', 'amount', 'description'):
        if text in aliases.get(field, set()):
            return field
    # tolerate "paid out" split into two lines or extra words
    for field in ('out', 'in', 'balance', 'amount'):
        for alias in aliases.get(field, set()):
            if alias in text:
                return field
    return None


def _find_header(lines: List[List[dict]], aliases: Dict[str, set]) -> Tuple[Optional[int], List[dict]]:
    for index, words in enumerate(lines[:HEADER_SCAN_LINES]):
        labels = _merge_labels(words)
        fields = [_label_field(label['text'], aliases) for label in labels]
        if 'date' in fields and any(f in fields for f in ('amount', 'out', 'in')):
            return index, labels
    return None, []


def _cluster_columns(body: List[List[dict]], tolerance: int = 14) -> List[dict]:
    edges = sorted(w['x1'] for words in body for w in words if _money(w['text']) is not None)
    clusters: List[dict] = []
    for edge in edges:
        if clusters and edge - clusters[-1]['last'] <= tolerance:
            cluster = clusters[-1]
            cluster['last'] = edge
            cluster['n'] += 1
            cluster['sum'] += edge
        else:
            clusters.append({'last': edge, 'n': 1, 'sum': edge})
    return [{'x': c['sum'] / c['n'], 'n': c['n']} for c in clusters]


def _bind(labels: List[dict], columns: List[dict], aliases: Dict[str, set],
          tolerance: int = 50) -> List[Tuple[str, float]]:
    candidates = []
    for label in labels:
        field = _label_field(label['text'], aliases)
        if not field or field in ('date', 'description'):
            continue
        for column in columns:
            distance = abs(column['x'] - label['x1'])
            if distance <= tolerance:
                candidates.append((distance, field, column['x']))
    candidates.sort(key=lambda item: item[0])
    used_fields, used_columns, binding = set(), set(), []
    for distance, field, x in candidates:
        if field in used_fields or x in used_columns:
            continue
        used_fields.add(field)
        used_columns.add(x)
        binding.append((field, x))
    if not binding and len(columns) == 1:
        binding = [('amount', columns[0]['x'])]
    return binding


def _take_date(words: List[dict], max_x: float, formats: List[str]):
    tokens = [w for w in words if w['x0'] < max_x]
    for count in (3, 2, 1):
        if len(tokens) >= count:
            joined = ' '.join(w['text'] for w in tokens[:count])
            parsed = parse_date(joined, formats)
            if parsed:
                return parsed, tokens[:count]
    return None, []


def _page_words_text(page) -> List[dict]:
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    return [{'text': w['text'], 'x0': w['x0'], 'x1': w['x1'], 'top': w['top']} for w in words]


def _page_words_ocr(image) -> List[dict]:
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    words = []
    for i, text in enumerate(data['text']):
        text = (text or '').strip()
        if not text or float(data['conf'][i] or 0) < 40:
            continue
        left, top, width, height = (data['left'][i], data['top'][i],
                                   data['width'][i], data['height'][i])
        words.append({'text': text, 'x0': left, 'x1': left + width, 'top': top + height / 2})
    return words


def _extract_pages(path: str, force_ocr: bool = False):
    if not PDFPLUMBER:
        raise ImportError_('pdfplumber is not installed; cannot read PDF statements')
    pages, used_ocr = [], False
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            words = [] if force_ocr else _page_words_text(page)
            text_len = len(''.join(w['text'] for w in words))
            if text_len < 20 and OCR:
                images = convert_from_path(path, dpi=200, first_page=page.page_number,
                                           last_page=page.page_number)
                if images:
                    words = _page_words_ocr(images[0])
                    used_ocr = True
            pages.append({'words': words, 'text': page.extract_text() or '',
                          'width': page.width, 'height': page.height})
    return pages, used_ocr


def _statement_balances(text: str) -> Tuple[Optional[float], Optional[float]]:
    opening = closing = None
    for line in text.splitlines():
        lowered = line.lower()
        amounts = re.findall(r'[£$€]?\s?-?[\d,]+\.\d{2}', line)
        if not amounts:
            continue
        value = parse_amount(amounts[-1])
        if value is None:
            continue
        if opening is None and any(k in lowered for k in
                                   ('balance brought forward', 'opening balance',
                                    'balance at start', 'previous balance')):
            opening = value
        if any(k in lowered for k in ('closing balance', 'balance carried forward',
                                      'balance at end', 'new balance')):
            closing = value
    return opening, closing


def parse_pdf(path: str, profile: dict, force_ocr: bool = False) -> ParseResult:
    pages, used_ocr = _extract_pages(path, force_ocr=force_ocr)
    aliases = _alias_sets(profile)
    formats = profile.get('dates') or []
    skip_markers = [s.lower() for s in profile.get('skip_rows', [])]

    all_rows: List[ParsedRow] = []
    warnings: List[str] = []
    raw_text_parts = []

    for page in pages:
        raw_text_parts.append(page['text'])
        lines = _group_lines(page['words'])
        header_index, labels = _find_header(lines, aliases)
        if header_index is None:
            continue
        body = lines[header_index + 1:]
        columns = _cluster_columns(body)
        binding = _bind(labels, columns, aliases)
        if not binding:
            continue
        date_label = next((label for label in labels if _label_field(label['text'], aliases) == 'date'), None)
        date_limit = (date_label['x1'] + 60) if date_label else 120
        first_number = min(x for _, x in binding)

        pending_description: List[str] = []
        for words in body:
            line_text = ' '.join(w['text'] for w in words).strip()
            lowered = line_text.lower()
            if any(marker in lowered for marker in skip_markers):
                continue

            date_value, consumed = _take_date(words, date_limit, formats)
            consumed_ids = {id(w) for w in consumed}
            numbers = [(w, _money(w['text'])) for w in words if _money(w['text']) is not None]
            numbers = [(w, v) for w, v in numbers if v is not None]

            description = ' '.join(w['text'] for w in words
                                   if w['x1'] < first_number - 6
                                   and w['x0'] >= (date_label['x1'] - 5 if date_label else 0)
                                   and id(w) not in consumed_ids).strip()
            prefixes = {p.lower() for p in profile.get('strip_prefixes', [])}
            if prefixes and description:
                tokens = description.split()
                while tokens and (tokens[0].lower().strip('()') in prefixes
                                  or tokens[0].strip() in (')', '((')):
                    tokens.pop(0)
                description = ' '.join(tokens).strip()

            if not date_value and not numbers:
                if description:
                    pending_description.append(description)
                continue

            row = {'date': date_value, 'description': ' '.join(pending_description + [description]).strip()}
            pending_description = []
            got_amount = False
            for field, target in binding:
                match = next((pair for pair in numbers if abs(pair[0]['x1'] - target) <= 22), None)
                if not match:
                    continue
                value = match[1]
                if field == 'balance':
                    row['balance'] = value
                elif field == 'amount':
                    row['amount'] = abs(value)
                    row['kind'] = 'income' if value > 0 else 'expense'
                    got_amount = True
                elif field == 'out':
                    row['amount'] = abs(value)
                    row['kind'] = 'expense'
                    got_amount = True
                elif field == 'in':
                    row['amount'] = abs(value)
                    row['kind'] = 'income'
                    got_amount = True

            if not got_amount:
                if description and not date_value:
                    pending_description.append(description)
                continue
            if not row['date'] and all_rows:
                row['date'] = all_rows[-1].date
            if not row['date']:
                warnings.append(f"Row without a date: {line_text[:80]}")
                continue

            score = 0.4 + 0.4 + (0.2 if row['description'] else 0)
            issues = []
            if not row['description']:
                issues.append('no description')
                score -= 0.1
            all_rows.append(ParsedRow(
                date=row['date'], description=row['description'] or 'Unknown',
                amount=round(row['amount'], 2), kind=row['kind'],
                balance=row.get('balance'), raw=line_text,
                confidence=round(max(0.1, score), 2), issues=issues))

    opening, closing = _statement_balances('\n'.join(raw_text_parts))
    if all_rows and opening is None and all_rows[0].balance is not None:
        first = all_rows[0]
        opening = round(first.balance - (first.amount if first.kind == 'income' else -first.amount), 2)
    if all_rows and closing is None and all_rows[-1].balance is not None:
        closing = all_rows[-1].balance

    # Balance continuity: the strongest possible correctness signal.
    previous = None
    breaks = 0
    for row in all_rows:
        if previous is not None and row.balance is not None:
            expected = previous + (row.amount if row.kind == 'income' else -row.amount)
            if abs(expected - row.balance) > 0.02:
                breaks += 1
                row.issues.append('balance does not match previous row')
                row.confidence = round(max(0.2, row.confidence - 0.25), 2)
        if row.balance is not None:
            previous = row.balance
    if breaks:
        warnings.append(f'{breaks} row(s) break the running balance; review before committing')
    if opening is not None and closing is not None and all_rows:
        signed = sum(r.amount if r.kind == 'income' else -r.amount for r in all_rows)
        if abs(round(opening + signed, 2) - round(closing, 2)) > 0.02:
            warnings.append(
                f'Opening + transactions ({round(opening + signed, 2)}) does not equal closing '
                f'({closing}); a row may be missing or misread')

    if used_ocr:
        warnings.append('Scanned PDF: text recovered with OCR, verify amounts')
    elif not all_rows and not page_has_text(pages):
        warnings.append('No text layer found. Install tesseract for OCR on scanned statements.')

    return ParseResult(rows=all_rows, bank=profile.get('name'), file_type='pdf',
                       profile_id=profile['id'], currency=profile.get('currency', 'GBP'),
                       opening_balance=opening, closing_balance=closing,
                       warnings=warnings,
                       raw_text='\n'.join(raw_text_parts)[:40000])


def page_has_text(pages) -> bool:
    return any(len(page['text'].strip()) > 20 for page in pages)


def extract_text_sample(path: str, max_chars: int = 6000) -> str:
    """First pages' text, for bank detection before choosing a profile."""
    if not PDFPLUMBER:
        return ''
    parts = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:2]:
                parts.append(page.extract_text() or '')
                if sum(len(p) for p in parts) >= max_chars:
                    break
    except Exception:
        return ''
    return '\n'.join(parts)[:max_chars]

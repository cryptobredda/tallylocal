"""OCR engines for scanned statements.

RapidOCR is the default: PP-OCRv6 detection/recognition models exported to
ONNX, running on CPU through onnxruntime (~31 MB of models, no PyTorch, no
PaddlePaddle). Tesseract stays as a fallback, and both are optional: CSV, OFX,
QIF and MT940 imports never touch this module.

Pin the backend with TALLY_OCR_ENGINE=rapidocr|tesseract|none.
"""
from __future__ import annotations

import os
import re
import shutil
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from PIL import Image

try:
    from rapidocr import RapidOCR
    RAPIDOCR_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    RapidOCR = None
    RAPIDOCR_AVAILABLE = False

try:
    import pytesseract
    TESSERACT_AVAILABLE = shutil.which('tesseract') is not None
except ImportError:  # pragma: no cover - optional dependency
    pytesseract = None
    TESSERACT_AVAILABLE = False

try:
    from pdf2image import convert_from_path
    PDF_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    PDF_AVAILABLE = False


def _pkg_version(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return ''


class OCRService:
    """One engine per process: loading the models is the expensive part."""

    def __init__(self):
        self.engine: Optional[str] = None      # rapidocr | tesseract | None
        self.ocr = None
        self._initialized = False
        self._lock = threading.Lock()
        self.last_error = ''

    # ------------------------------------------------------------- lifecycle

    def _pick_engine(self) -> Optional[str]:
        forced = (os.getenv('TALLY_OCR_ENGINE') or 'auto').strip().lower()
        if forced in ('none', 'off', 'disabled'):
            return None
        if forced in ('rapidocr', 'tesseract'):
            return forced
        if RAPIDOCR_AVAILABLE:
            return 'rapidocr'
        if TESSERACT_AVAILABLE:
            return 'tesseract'
        return None

    def _ensure_initialized(self):
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            forced = (os.getenv('TALLY_OCR_ENGINE') or 'auto').strip().lower()
            choice = self._pick_engine()
            if choice == 'rapidocr':
                try:
                    self.ocr = RapidOCR()
                    self.engine = 'rapidocr'
                except Exception as exc:
                    self.last_error = f'RapidOCR init failed: {exc}'
                    self.ocr = None
                    if TESSERACT_AVAILABLE and forced != 'rapidocr':
                        self.engine = 'tesseract'
            elif choice == 'tesseract' and TESSERACT_AVAILABLE:
                self.engine = 'tesseract'
            self._initialized = True

    def reset(self):
        with self._lock:
            self.ocr = None
            self.engine = None
            self._initialized = False
            self.last_error = ''

    def is_available(self) -> bool:
        self._ensure_initialized()
        return self.engine in ('rapidocr', 'tesseract')

    def engine_name(self) -> str:
        self._ensure_initialized()
        if self.engine == 'rapidocr':
            version = _pkg_version('rapidocr')
            return f'RapidOCR {version} (PP-OCRv6, ONNX CPU)'.replace('  ', ' ')
        if self.engine == 'tesseract':
            try:
                return f'Tesseract {pytesseract.get_tesseract_version()}'
            except Exception:
                return 'Tesseract'
        return 'unavailable'

    def status(self) -> Dict:
        available = self.is_available()
        return {
            'available': available,
            'engine': self.engine or 'none',
            'name': self.engine_name(),
            'version': _pkg_version('rapidocr') if self.engine == 'rapidocr' else '',
            'error': self.last_error,
        }

    # ------------------------------------------------------------- extraction

    def _rapidocr_items(self, image) -> List[Dict]:
        result = self.ocr(image)
        if result is None:
            return []
        texts = getattr(result, 'txts', None)
        boxes = getattr(result, 'boxes', None)
        scores = getattr(result, 'scores', None)
        if texts is None:
            return []
        items: List[Dict] = []
        for idx, text in enumerate(texts):
            text = (text or '').strip()
            if not text:
                continue
            try:
                score = float(scores[idx]) if scores is not None else 1.0
            except Exception:
                score = 1.0
            if score < 0.35:
                continue
            if boxes is None or idx >= len(boxes):
                continue
            box = boxes[idx]
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            top = (min(ys) + max(ys)) / 2
            items.append({
                'text': text,
                'x0': min(xs),
                'x1': max(xs),
                'top': top,
                'x': int(min(xs)),
                'y': int(top),
                'conf': score,
            })
        return items

    def _tesseract_items(self, image) -> List[Dict]:
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        items: List[Dict] = []
        for i, text in enumerate(data['text']):
            text = (text or '').strip()
            if not text or float(data['conf'][i] or 0) < 40:
                continue
            left, top, width, height = (data['left'][i], data['top'][i],
                                        data['width'][i], data['height'][i])
            items.append({
                'text': text,
                'x0': left,
                'x1': left + width,
                'top': top + height / 2,
                'x': left,
                'y': int(top + height / 2),
                'conf': float(data['conf'][i]) / 100.0,
            })
        return items

    def extract_from_image(self, image: Image.Image) -> List[Dict]:
        """Words with pixel coordinates from a PIL image."""
        self._ensure_initialized()
        if not self.is_available():
            raise RuntimeError('No OCR engine is installed.')
        rgb = image.convert('RGB')
        if self.engine == 'rapidocr':
            import numpy as np
            return self._rapidocr_items(np.array(rgb))
        return self._tesseract_items(rgb)

    def extract_with_positions(self, image_path: str) -> List[Dict]:
        """Words with X/Y positions from an image file."""
        self._ensure_initialized()
        if not self.is_available():
            raise RuntimeError('No OCR engine is installed.')
        if self.engine == 'rapidocr':
            return self._rapidocr_items(image_path)
        with Image.open(image_path) as image:
            return self._tesseract_items(image.convert('RGB'))

    def process_statement_structured(self, file_path: str) -> List[Dict]:
        """Every page's words, tagged with a page number."""
        if not PDF_AVAILABLE:
            raise RuntimeError('PDF processing libraries not installed.')
        images = convert_from_path(file_path)
        all_items: List[Dict] = []
        pages_to_process = len(images) - 1 if len(images) > 1 else len(images)
        for page_num in range(pages_to_process):
            items = self.extract_from_image(images[page_num])
            for item in items:
                item['page'] = page_num + 1
            all_items.extend(items)
        return all_items

    def process_statement(self, file_path: str) -> str:
        """Extracted text, lines reconstructed from word positions."""
        items = self.process_statement_structured(file_path)
        items.sort(key=lambda i: (i.get('page', 1), i['y'], i['x']))
        lines: List[str] = []
        current_y = -1
        current_line: List[Dict] = []
        y_threshold = 20
        for item in items:
            if current_y == -1 or abs(item['y'] - current_y) <= y_threshold:
                current_line.append(item)
                if current_y == -1:
                    current_y = item['y']
            else:
                if current_line:
                    current_line.sort(key=lambda x: x['x'])
                    lines.append(' '.join(i['text'] for i in current_line))
                current_line = [item]
                current_y = item['y']
        if current_line:
            current_line.sort(key=lambda x: x['x'])
            lines.append(' '.join(i['text'] for i in current_line))
        return '\n'.join(lines)


# Lazy-loaded OCR service instance (singleton)
_ocr_service_instance = None

def get_ocr_service():
    """Get singleton OCR service instance to avoid reloading models"""
    global _ocr_service_instance
    if _ocr_service_instance is None:
        _ocr_service_instance = OCRService()
    return _ocr_service_instance

def reset_ocr_service():
    """Reset the OCR service (creates fresh instance on next get)"""
    global _ocr_service_instance
    _ocr_service_instance = None


class HSBCStatementParser:
    """Parser specifically designed for HSBC bank statements using column positions"""
    
    # Column X position ranges (based on typical HSBC statement layout)
    # These are approximate and may need adjustment
    COL_DATE = (80, 200)
    COL_TYPE = (250, 320)
    COL_DETAILS = (320, 950)
    COL_PAID_OUT = (980, 1150)
    COL_PAID_IN = (1200, 1350)
    COL_BALANCE = (1400, 1600)
    
    PAYMENT_TYPES = ['CR', 'DD', 'VIS', 'BP', 'SO', 'TFR', 'ATM', 'CHQ', 'FPI', 'FPO', 'BGC', 'DEP', 'CLS', 'DEB']
    
    # Contactless symbols - can appear as ))  )) ) (( ((( etc
    CONTACTLESS_PATTERNS = [r'\)\)\)?', r'\(\(\(?', r'\)\s*\)', r'\(\s*\(']
    
    DATE_PATTERNS = [
        r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{2,4})',
        r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
    ]
    
    SKIP_PATTERNS = [
        r'balance\s*(brought|carried)\s*forward',
        r'opening\s*balance',
        r'closing\s*balance',
        r'account\s*summary',
        r'contact\s*tel',
        r'text\s*phone',
        r'www\.',
        r'your\s*statement',
        r'account\s*name',
        r'sort\s*code',
        r'sheet\s*number',
        r'payment\s*type',
        r'paid\s*out',
        r'paid\s*in',
        r'^date$',
        r'iban',
        r'branch\s*identifier',
        r'international\s*bank',
        r'aer\s*arranged',
        r'arranged\s*overdraft',
        r'credit\s*interest',
        r'interest\s*rates',
    ]
    
    # Patterns to clean from description text
    CLEAN_PATTERNS = [
        r'\bAER\b.*$',
        r'\bArranged\s+O.*$',
        r'\bCredit\s+Interest.*$',
    ]
    
    def __init__(self):
        self.ocr_service = get_ocr_service()  # Use singleton to avoid reloading models
    
    def is_payment_type(self, text: str) -> bool:
        """Check if text is a payment type code"""
        cleaned = text.strip()
        # Check for contactless symbol first (doesn't need uppercase)
        if self.is_contactless_symbol(cleaned):
            return True
        return cleaned.upper() in self.PAYMENT_TYPES
    
    def is_contactless_symbol(self, text: str) -> bool:
        """Check if text is a contactless payment symbol like )) or (("""
        cleaned = text.strip()
        # Match variations: )) ))) (( ((( ) ) ( (
        for pattern in self.CONTACTLESS_PATTERNS:
            if re.fullmatch(pattern, cleaned):
                return True
        return False
    
    def get_column(self, x: int) -> str:
        """Determine which column an X position belongs to"""
        if self.COL_DATE[0] <= x <= self.COL_DATE[1]:
            return 'date'
        elif self.COL_TYPE[0] <= x <= self.COL_TYPE[1]:
            return 'type'
        elif self.COL_DETAILS[0] <= x <= self.COL_DETAILS[1]:
            return 'details'
        elif self.COL_PAID_OUT[0] <= x <= self.COL_PAID_OUT[1]:
            return 'paid_out'
        elif self.COL_PAID_IN[0] <= x <= self.COL_PAID_IN[1]:
            return 'paid_in'
        elif self.COL_BALANCE[0] <= x <= self.COL_BALANCE[1]:
            return 'balance'
        return 'unknown'
    
    def parse_date(self, text: str) -> Optional[datetime]:
        """Parse date from text"""
        for pattern in self.DATE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                date_str = match.group(1)
                for fmt in ['%d %b %y', '%d %b %Y', '%d %B %y', '%d %B %Y',
                           '%d/%m/%Y', '%d-%m-%Y', '%d/%m/%y', '%d-%m-%y']:
                    try:
                        return datetime.strptime(date_str, fmt)
                    except ValueError:
                        continue
        return None
    
    def parse_amount(self, text: str) -> Optional[float]:
        """Parse amount from text, excluding percentages"""
        # Skip if it's a percentage
        if '%' in text:
            return None
        match = re.search(r'([\d,]+\.\d{2})', text)
        if match:
            return float(match.group(1).replace(',', ''))
        return None
    
    def should_skip(self, text: str) -> bool:
        """Check if text should be skipped"""
        text_lower = text.lower()
        for pattern in self.SKIP_PATTERNS:
            if re.search(pattern, text_lower):
                return True
        return False
    
    def parse_statement(self, file_path: str) -> Dict:
        """Parse bank statement and extract transactions with balances
        
        Returns:
            Dict with 'transactions', 'opening_balance', 'closing_balance'
        """
        items = self.ocr_service.process_statement_structured(file_path)
        
        # Sort by page, then Y position
        items.sort(key=lambda i: (i.get('page', 1), i['y']))
        
        # First pass: find header balance (opening balance in top area of page 1)
        # This is usually labeled "Balance" or appears before transaction rows
        header_balance = None
        for item in items:
            if item.get('page', 1) == 1 and item['y'] < 800:  # Top portion of first page
                # Check if it's in the balance column area
                if self.COL_BALANCE[0] <= item['x'] <= self.COL_BALANCE[1]:
                    amount = self.parse_amount(item['text'])
                    if amount is not None:
                        header_balance = amount
                        break  # Take the first valid balance in header
        
        # Group items into rows based on Y position
        # Use a smaller threshold to avoid merging separate transaction lines
        rows = []
        current_row = []
        current_y = -1
        y_threshold = 18  # Reduced from 25 to better separate lines
        
        for item in items:
            if current_y == -1 or abs(item['y'] - current_y) <= y_threshold:
                current_row.append(item)
                if current_y == -1:
                    current_y = item['y']
            else:
                if current_row:
                    rows.append(current_row)
                current_row = [item]
                current_y = item['y']
        
        if current_row:
            rows.append(current_row)
        
        # Process rows to extract transactions
        transactions = []
        current_date = None
        current_tx = None
        all_balances = []  # Track all transaction balances found
        
        for row in rows:
            row_data = {
                'date': None,
                'type': None,
                'details': [],
                'paid_out': None,
                'paid_in': None,
                'balance': None,
                'y': row[0]['y'] if row else 0,
                'page': row[0].get('page', 1) if row else 1
            }
            
            for item in row:
                text = item['text'].strip()
                x = item['x']
                col = self.get_column(x)
                
                if self.should_skip(text):
                    continue
                
                if col == 'date':
                    parsed_date = self.parse_date(text)
                    if parsed_date:
                        row_data['date'] = parsed_date
                
                elif col == 'type':
                    if self.is_payment_type(text):
                        # Normalize contactless symbols to ))
                        if self.is_contactless_symbol(text):
                            row_data['type'] = '))'
                        else:
                            row_data['type'] = text.upper()
                    else:
                        row_data['details'].append(text)
                
                elif col == 'details':
                    # Check if this detail text contains a contactless symbol
                    # which would indicate a new transaction started
                    if self.is_contactless_symbol(text):
                        # This is actually a payment type marker, not a detail
                        if not row_data['type']:
                            row_data['type'] = '))'
                    else:
                        row_data['details'].append(text)
                
                elif col == 'paid_out':
                    amount = self.parse_amount(text)
                    if amount:
                        row_data['paid_out'] = amount
                
                elif col == 'paid_in':
                    amount = self.parse_amount(text)
                    if amount:
                        row_data['paid_in'] = amount
                
                elif col == 'balance':
                    amount = self.parse_amount(text)
                    if amount:
                        row_data['balance'] = amount
                        all_balances.append({
                            'amount': amount,
                            'date': row_data['date'] or current_date,
                            'page': row_data['page'],
                            'y': row_data['y']
                        })
            
            # Update current date if we found one
            if row_data['date']:
                current_date = row_data['date']
            
            # Check if this row starts a new transaction (has payment type)
            if row_data['type']:
                # Save previous transaction if exists and has amount
                if current_tx and (current_tx.get('paid_out') or current_tx.get('paid_in')):
                    transactions.append(self._finalize_transaction(current_tx))
                
                # Start new transaction
                current_tx = {
                    'date': current_date,
                    'type': row_data['type'],
                    'details': row_data['details'].copy(),
                    'paid_out': row_data['paid_out'],
                    'paid_in': row_data['paid_in'],
                    'balance': row_data['balance'],
                }
            
            elif current_tx:
                # Continue current transaction
                current_tx['details'].extend(row_data['details'])
                
                if row_data['paid_out'] and not current_tx.get('paid_out'):
                    current_tx['paid_out'] = row_data['paid_out']
                
                if row_data['paid_in'] and not current_tx.get('paid_in'):
                    current_tx['paid_in'] = row_data['paid_in']
                
                if row_data['balance'] and not current_tx.get('balance'):
                    current_tx['balance'] = row_data['balance']
        
        # Don't forget the last transaction
        if current_tx and (current_tx.get('paid_out') or current_tx.get('paid_in')):
            transactions.append(self._finalize_transaction(current_tx))
        
        # Post-process: split any transactions that appear to have merged descriptions
        # This happens when contactless symbols appear in the middle of text
        transactions = self._split_merged_transactions(transactions)
        
        # Determine opening and closing balances
        opening_balance = header_balance  # Use header balance as opening balance
        closing_balance = None
        
        if all_balances:
            # Sort balances by page, then Y position to get chronological order
            all_balances.sort(key=lambda b: (b['page'], b['y']))
            closing_balance = all_balances[-1]['amount']
            
            # If we didn't find a header balance, calculate from first transaction balance
            if opening_balance is None and transactions:
                first_tx = transactions[0]
                first_balance = all_balances[0]['amount']
                if first_tx['type'] == 'expense':
                    opening_balance = first_balance + first_tx['amount']
                else:
                    opening_balance = first_balance - first_tx['amount']
        
        return {
            'transactions': transactions,
            'opening_balance': opening_balance,
            'closing_balance': closing_balance
        }
    
    def _split_merged_transactions(self, transactions: List[Dict]) -> List[Dict]:
        """
        Post-process transactions to identify and flag potentially merged entries.
        Some transactions may have been merged if:
        - Description contains payment type codes mid-text (like FIRST WEST VIS LIDL)
        - Description contains multiple merchant names
        
        For now, we clean up the descriptions rather than splitting (since we don't have
        the individual amounts for split transactions)
        """
        result = []
        
        # Pattern to detect embedded payment type indicators
        embedded_payment_pattern = re.compile(
            r'\s+(VIS|DD|BP|SO|CR|TFR|ATM|\)\s*\)|\(\s*\()\s+',
            re.IGNORECASE
        )
        
        for tx in transactions:
            desc = tx.get('description', '')
            
            # Check if description contains embedded payment types
            match = embedded_payment_pattern.search(desc)
            if match:
                # Split at the payment type marker and keep only the first part
                parts = embedded_payment_pattern.split(desc, maxsplit=1)
                if parts:
                    tx['description'] = parts[0].strip()[:200]
            
            result.append(tx)
        
        return result
    
    def _finalize_transaction(self, tx: Dict) -> Dict:
        """Finalize a transaction record"""
        description = ' '.join(tx.get('details', []))
        description = re.sub(r'\s+', ' ', description).strip()
        
        # Clean up description by removing footer text that might have been picked up
        for pattern in self.CLEAN_PATTERNS:
            description = re.sub(pattern, '', description, flags=re.IGNORECASE).strip()
        
        # Remove any contactless symbols from the description
        description = re.sub(r'\s*\)\s*\)\s*\)?\s*', ' ', description)
        description = re.sub(r'\s*\(\s*\(\s*\(?\s*', ' ', description)
        description = re.sub(r'\s+', ' ', description).strip()
        
        if tx.get('paid_in'):
            tx_type = 'income'
            amount = tx['paid_in']
        else:
            tx_type = 'expense'
            amount = tx.get('paid_out', 0)
        
        return {
            'date': tx.get('date'),
            'description': description[:200] if description else tx.get('type', 'Unknown'),
            'amount': amount,
            'type': tx_type,
            'payment_type': tx.get('type'),
        }
    
    def detect_bank(self, file_path: str) -> Optional[str]:
        """Detect bank from statement"""
        text = self.ocr_service.process_statement(file_path)
        text_lower = text.lower()
        
        bank_patterns = {
            'HSBC': r'\bhsbc\b',
            'Barclays': r'barclays',
            'Lloyds': r'lloyds',
            'NatWest': r'natwest',
            'Santander': r'santander',
            'Nationwide': r'nationwide',
            'Halifax': r'halifax',
        }
        
        for bank_name, pattern in bank_patterns.items():
            if re.search(pattern, text_lower):
                return bank_name
        
        return None


# Legacy instances for backward compatibility (lazy-loaded)
class LazyOCRService:
    def __getattr__(self, name):
        return getattr(get_ocr_service(), name)

ocr_service = LazyOCRService()

class TransactionParser:
    """Legacy parser - redirects to HSBCStatementParser"""
    
    def __init__(self):
        self.hsbc_parser = HSBCStatementParser()
    
    def parse_transactions(self, raw_text: str) -> List[Dict]:
        """This method is deprecated - use HSBCStatementParser.parse_statement() instead"""
        return []
    
    def detect_bank(self, raw_text: str) -> Optional[str]:
        text_lower = raw_text.lower()
        if 'hsbc' in text_lower:
            return 'HSBC'
        return None

transaction_parser = TransactionParser()

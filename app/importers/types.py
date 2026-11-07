"""Shared types for statement importers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class ParsedRow:
    date: Optional[datetime]
    description: str
    amount: float                      # always positive
    kind: str = 'expense'              # income | expense
    balance: Optional[float] = None    # running balance after the row, if present
    external_id: Optional[str] = None
    raw: str = ''
    confidence: float = 1.0
    issues: List[str] = field(default_factory=list)
    category_hint: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            'date': self.date.isoformat() if self.date else None,
            'description': self.description,
            'amount': round(self.amount, 2),
            'type': self.kind,
            'balance': self.balance,
            'external_id': self.external_id,
            'raw': self.raw,
            'confidence': round(self.confidence, 2),
            'issues': self.issues,
            'category_hint': self.category_hint,
        }


@dataclass
class ParseResult:
    rows: List[ParsedRow]
    bank: Optional[str] = None
    currency: str = 'GBP'
    file_type: str = 'unknown'
    profile_id: Optional[str] = None
    opening_balance: Optional[float] = None
    closing_balance: Optional[float] = None
    warnings: List[str] = field(default_factory=list)
    raw_text: str = ''

    @property
    def confidence(self) -> float:
        if not self.rows:
            return 0.0
        return round(sum(r.confidence for r in self.rows) / len(self.rows), 2)

    def to_dict(self) -> dict:
        return {
            'bank': self.bank,
            'currency': self.currency,
            'file_type': self.file_type,
            'profile': self.profile_id,
            'row_count': len(self.rows),
            'confidence': self.confidence,
            'opening_balance': self.opening_balance,
            'closing_balance': self.closing_balance,
            'warnings': self.warnings,
        }


class ImportError_(Exception):
    """Raised when a file cannot be parsed at all."""

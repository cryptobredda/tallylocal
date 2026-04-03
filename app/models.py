from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, Enum as SQLEnum, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.database import Base


class TransactionType(enum.Enum):
    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"


class AssetType(enum.Enum):
    CRYPTO = "crypto"
    PRECIOUS_METAL = "precious_metal"
    STOCK = "stock"  # Future use
    OTHER = "other"


class AssetTransactionType(enum.Enum):
    BUY = "buy"
    SELL = "sell"
    TRANSFER = "transfer"
    OTHER = "other"


class WalletNetwork(enum.Enum):
    BTC = "btc"
    EVM = "evm"
    SOL = "sol"


class WalletTransactionType(enum.Enum):
    SEND = "send"
    RECEIVE = "receive"
    INTERNAL = "internal"
    OTHER = "other"


class AccountType(enum.Enum):
    MAIN = "main"
    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"
    CASH = "cash"
    INVESTMENT = "investment"
    OTHER = "other"


class Category(Base):
    __tablename__ = 'categories'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    color = Column(String(7), default='#6366f1')
    icon = Column(String(50), default='tag')
    is_system = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    transactions = relationship('Transaction', back_populates='category')
    rules = relationship('TransactionRule', back_populates='category')


class Account(Base):
    """Financial account - checking, savings, credit card, cash, investment, etc."""
    __tablename__ = 'accounts'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    account_type = Column(SQLEnum(AccountType), default=AccountType.CHECKING)
    color = Column(String(7), default='#10b981')
    icon = Column(String(50), default='wallet')
    description = Column(String(255))
    opening_balance = Column(Float, default=0.0)  # Starting balance when account was created
    is_active = Column(Boolean, default=True)
    is_archived = Column(Boolean, default=False)  # For closed accounts
    created_at = Column(DateTime, default=datetime.utcnow)
    
    transactions = relationship('Transaction', back_populates='account', foreign_keys='Transaction.account_id')
    statements = relationship('BankStatement', back_populates='account')
    
    @property
    def is_main(self):
        return self.account_type == AccountType.MAIN


# Keep Pot as alias for backward compatibility during migration
Pot = Account


class Transaction(Base):
    __tablename__ = 'transactions'
    
    id = Column(Integer, primary_key=True)
    date = Column(DateTime, nullable=False)
    description = Column(String(500), nullable=False)
    original_description = Column(String(500))
    amount = Column(Float, nullable=False)
    transaction_type = Column(SQLEnum(TransactionType), default=TransactionType.EXPENSE)
    balance_after = Column(Float)
    
    category_id = Column(Integer, ForeignKey('categories.id'))
    account_id = Column(Integer, ForeignKey('accounts.id'))  # Which account this transaction belongs to
    
    # For internal transfers - links the two sides of a transfer
    linked_transaction_id = Column(Integer, ForeignKey('transactions.id'))
    is_internal_transfer = Column(Boolean, default=False)  # Flag to exclude from reports
    
    is_one_off = Column(Boolean, default=False)
    is_recurring = Column(Boolean, default=False)
    
    notes = Column(Text)  # User notes/memo for the transaction
    tags = Column(String(500))  # Comma-separated tags for organization
    
    bank_name = Column(String(100))
    account_number = Column(String(50))
    statement_file = Column(String(255))
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    category = relationship('Category', back_populates='transactions')
    account = relationship('Account', back_populates='transactions', foreign_keys=[account_id])
    linked_transaction = relationship('Transaction', remote_side=[id], foreign_keys=[linked_transaction_id])
    
    # Backward compatibility alias
    @property
    def pot_id(self):
        return self.account_id
    
    @pot_id.setter
    def pot_id(self, value):
        self.account_id = value
    
    @property
    def pot(self):
        return self.account


class TransactionRule(Base):
    __tablename__ = 'transaction_rules'
    
    id = Column(Integer, primary_key=True)
    pattern = Column(String(255), nullable=False)
    match_type = Column(String(20), default='contains')
    category_id = Column(Integer, ForeignKey('categories.id'))
    account_id = Column(Integer, ForeignKey('accounts.id'))
    is_active = Column(Boolean, default=True)
    priority = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    category = relationship('Category', back_populates='rules')
    account = relationship('Account')
    
    # Backward compatibility
    @property
    def pot_id(self):
        return self.account_id
    
    @pot_id.setter
    def pot_id(self, value):
        self.account_id = value


class BankStatement(Base):
    __tablename__ = 'bank_statements'
    
    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_hash = Column(String(64), unique=True)  # SHA256 hash for duplicate detection
    bank_name = Column(String(100))
    account_number = Column(String(50))
    account_id = Column(Integer, ForeignKey('accounts.id'))  # Target account for import
    statement_date = Column(DateTime)
    opening_balance = Column(Float)  # Starting balance from statement
    closing_balance = Column(Float)  # Ending balance from statement
    processed = Column(Boolean, default=False)
    transactions_count = Column(Integer, default=0)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime)
    raw_text = Column(Text)
    error_message = Column(Text)
    
    account = relationship('Account', back_populates='statements')


class MonthlyBudget(Base):
    __tablename__ = 'monthly_budgets'
    
    id = Column(Integer, primary_key=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    category_id = Column(Integer, ForeignKey('categories.id'))
    budget_amount = Column(Float, nullable=False)
    rollover = Column(Boolean, default=False)  # Roll unused budget to next month
    created_at = Column(DateTime, default=datetime.utcnow)
    
    category = relationship('Category')


class Settings(Base):
    __tablename__ = 'settings'
    
    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ==================== ASSET MANAGEMENT MODELS ====================

class Asset(Base):
    """Definition of a trackable asset (crypto, precious metal, etc.)"""
    __tablename__ = 'assets'
    
    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), nullable=False, unique=True)  # BTC, ETH, XAU (gold), XAG (silver)
    name = Column(String(100), nullable=False)  # Bitcoin, Ethereum, Gold, Silver
    asset_type = Column(SQLEnum(AssetType), nullable=False)
    api_id = Column(String(100))  # CoinGecko ID or metals API identifier
    is_tracked = Column(Boolean, default=True)  # Whether we can get live prices
    icon = Column(String(50))  # Icon identifier
    color = Column(String(7), default='#f59e0b')
    created_at = Column(DateTime, default=datetime.utcnow)
    
    holdings = relationship('AssetHolding', back_populates='asset', cascade='all, delete-orphan')
    transactions = relationship('AssetTransaction', back_populates='asset', cascade='all, delete-orphan')
    price_cache = relationship('AssetPriceCache', back_populates='asset', cascade='all, delete-orphan')


class AssetHolding(Base):
    """User's current holdings of an asset"""
    __tablename__ = 'asset_holdings'
    
    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey('assets.id'), nullable=False)
    quantity = Column(Float, nullable=False, default=0.0)
    total_cost_basis = Column(Float, nullable=False, default=0.0)  # Total amount invested
    average_buy_price = Column(Float)  # Calculated from transactions
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    asset = relationship('Asset', back_populates='holdings')


class AssetTransaction(Base):
    """Individual buy/sell/transfer transactions for assets"""
    __tablename__ = 'asset_transactions'
    
    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey('assets.id'), nullable=False)
    transaction_type = Column(SQLEnum(AssetTransactionType), nullable=False)
    quantity = Column(Float, nullable=False)
    price_per_unit = Column(Float, nullable=False)  # Price at time of transaction
    total_value = Column(Float, nullable=False)  # quantity * price_per_unit
    fee = Column(Float, default=0.0)  # Transaction fees
    transaction_date = Column(DateTime, nullable=False)
    notes = Column(Text)
    is_historical_price = Column(Boolean, default=False)  # Was price auto-fetched?
    created_at = Column(DateTime, default=datetime.utcnow)
    
    asset = relationship('Asset', back_populates='transactions')


class AssetPriceCache(Base):
    """Cached current price data for assets (refreshed daily for live display)"""
    __tablename__ = 'asset_price_cache'
    
    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey('assets.id'), nullable=False)
    price_usd = Column(Float)
    price_gbp = Column(Float)
    price_date = Column(DateTime, nullable=False)  # Date this price is for
    change_24h = Column(Float)  # 24h price change percentage
    change_7d = Column(Float)  # 7d price change percentage
    market_cap = Column(Float)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    
    asset = relationship('Asset', back_populates='price_cache')


class AssetPriceHistory(Base):
    """Historical daily prices for assets - used for portfolio value calculation"""
    __tablename__ = 'asset_price_history'
    
    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey('assets.id'), nullable=False)
    date = Column(DateTime, nullable=False)  # The date (no time component)
    price_gbp = Column(Float, nullable=False)
    price_usd = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    asset = relationship('Asset', backref='price_history')


class PortfolioSnapshot(Base):
    """Pre-calculated daily portfolio values - graph reads directly from here"""
    __tablename__ = 'portfolio_snapshots'
    
    id = Column(Integer, primary_key=True)
    snapshot_date = Column(DateTime, nullable=False, unique=True)
    total_portfolio_value = Column(Float, nullable=False)  # Holdings * prices
    base_investment = Column(Float, nullable=False)  # Cost basis (BUY - SELL basis)
    holdings_breakdown = Column(Text)  # JSON string of {asset_id: {quantity, value}}
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Wallet(Base):
    """User-tracked blockchain wallet"""
    __tablename__ = 'wallets'
    
    id = Column(Integer, primary_key=True)
    label = Column(String(120))
    address = Column(String(120), nullable=False, unique=True)
    network = Column(SQLEnum(WalletNetwork), nullable=False)
    native_symbol = Column(String(10))
    notes = Column(Text)
    native_balance = Column(Float, default=0.0)
    total_value_usd = Column(Float)
    token_balances = Column(Text, default='[]')  # JSON array of balances
    last_synced_at = Column(DateTime)
    last_sync_status = Column(String(20), default='never')
    last_sync_error = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    transactions = relationship('WalletTransaction', back_populates='wallet', cascade='all, delete-orphan')
    snapshots = relationship('WalletBalanceSnapshot', back_populates='wallet', cascade='all, delete-orphan')


class WalletTransaction(Base):
    """Normalized on-chain transaction attached to a tracked wallet"""
    __tablename__ = 'wallet_transactions'
    __table_args__ = (
        UniqueConstraint('wallet_id', 'tx_hash', name='uq_wallet_tx_hash'),
    )
    
    id = Column(Integer, primary_key=True)
    wallet_id = Column(Integer, ForeignKey('wallets.id'), nullable=False)
    tx_hash = Column(String(130), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    chain = Column(String(50))
    tx_type = Column(SQLEnum(WalletTransactionType), default=WalletTransactionType.OTHER)
    amount_native = Column(Float, default=0.0)  # Absolute value
    net_amount_native = Column(Float, default=0.0)  # Signed (+in, -out)
    native_symbol = Column(String(10))
    token_symbol = Column(String(20))
    amount_usd = Column(Float)
    from_address = Column(String(120))
    to_address = Column(String(120))
    description = Column(Text)
    explorer_url = Column(String(255))
    fee_native = Column(Float)
    metadata_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    wallet = relationship('Wallet', back_populates='transactions')


class WalletBalanceSnapshot(Base):
    """Historical snapshot of wallet balance for charting"""
    __tablename__ = 'wallet_balance_snapshots'
    __table_args__ = (
        UniqueConstraint('wallet_id', 'snapshot_time', name='uq_wallet_snapshot'),
    )
    
    id = Column(Integer, primary_key=True)
    wallet_id = Column(Integer, ForeignKey('wallets.id'), nullable=False)
    snapshot_time = Column(DateTime, nullable=False)
    balance_native = Column(Float, default=0.0)
    balance_usd = Column(Float)
    token_breakdown = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    wallet = relationship('Wallet', back_populates='snapshots')

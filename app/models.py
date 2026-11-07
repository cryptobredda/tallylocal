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
    currency = Column(String(3), default='GBP')
    color = Column(String(7), default='#10b981')
    icon = Column(String(50), default='wallet')
    description = Column(String(255))
    opening_balance = Column(Float, default=0.0)  # Starting balance when account was created
    is_active = Column(Boolean, default=True)
    is_archived = Column(Boolean, default=False)  # For closed accounts
    created_at = Column(DateTime, default=datetime.utcnow)

    # Bank connection linkage (null for manual accounts)
    connection_id = Column(Integer, ForeignKey('bank_connections.id'))
    provider_account_id = Column(String(160))
    iban_masked = Column(String(40))
    balance_current = Column(Float)
    balance_available = Column(Float)
    balance_synced_at = Column(DateTime)
    balance_source = Column(String(20), default='opening')  # opening | bank_sync | manual

    transactions = relationship('Transaction', back_populates='account', foreign_keys='Transaction.account_id')
    statements = relationship('BankStatement', back_populates='account')
    connection = relationship('BankConnection', back_populates='accounts')

    
    @property
    def is_main(self):
        return self.account_type == AccountType.MAIN


# Keep Pot as alias for backward compatibility during migration
Pot = Account


class Transaction(Base):
    """The raw, append-only ledger.

    Bank and statement rows are never edited in place. Every annotation (category,
    description, notes, tags, exclusion) lives in TransactionOverride, so the
    original data stays auditable and a sync can never be corrupted by a UI edit.
    """
    __tablename__ = 'transactions'
    __table_args__ = (
        UniqueConstraint('account_id', 'external_id', name='uq_tx_account_external'),
        UniqueConstraint('account_id', 'import_hash', name='uq_tx_account_import_hash'),
    )
    
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

    # Provenance & bank-sync identity
    source = Column(String(20), default='manual')  # manual | statement | bank_sync | agent | legacy
    external_id = Column(String(160))
    import_hash = Column(String(64))
    import_batch_id = Column(Integer, ForeignKey('import_batches.id'))
    currency = Column(String(3))
    amount_base = Column(Float)
    fx_rate = Column(Float)
    pending = Column(Boolean, default=False)
    value_date = Column(DateTime)
    counterparty = Column(String(200))
    merchant_normalized = Column(String(120))
    raw_json = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    category = relationship('Category', back_populates='transactions')
    account = relationship('Account', back_populates='transactions', foreign_keys=[account_id])
    linked_transaction = relationship('Transaction', remote_side=[id], foreign_keys=[linked_transaction_id])
    override = relationship('TransactionOverride', back_populates='transaction', uselist=False,
                            cascade='all, delete-orphan')
    edit_log = relationship('TransactionEdit', back_populates='transaction',
                            cascade='all, delete-orphan')

    @property
    def is_raw_locked(self):
        """True when this row came from an external source and must not be edited."""
        return (self.source or 'manual') in ('statement', 'bank_sync')

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


# ==================== EDIT OVERLAY (raw ledger stays immutable) ====================

class TransactionOverride(Base):
    """Current user/agent annotation for a raw transaction.

    One row per edited transaction. Nulls mean "not overridden". The raw row in
    `transactions` is never updated by the API, CLI or an agent.
    """
    __tablename__ = 'transaction_overrides'

    transaction_id = Column(Integer, ForeignKey('transactions.id', ondelete='CASCADE'), primary_key=True)
    category_id = Column(Integer, ForeignKey('categories.id'))
    description = Column(String(500))
    notes = Column(Text)
    tags = Column(String(500))
    merchant = Column(String(120))
    is_excluded = Column(Boolean)
    is_recurring = Column(Boolean)
    is_one_off = Column(Boolean)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String(80), default='user')

    transaction = relationship('Transaction', back_populates='override')
    category = relationship('Category')


class TransactionEdit(Base):
    """Append-only log of every annotation change. One change group = one undo."""
    __tablename__ = 'transaction_edits'

    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False)
    change_group = Column(String(36), nullable=False)
    field = Column(String(40), nullable=False)
    old_value = Column(Text)
    new_value = Column(Text)
    actor = Column(String(80), default='user')
    created_at = Column(DateTime, default=datetime.utcnow)

    transaction = relationship('Transaction', back_populates='edit_log')


class TransactionView(Base):
    """Read-only projection: raw transaction + its annotation overlay.

    Created by app.migrate.create_views(). Never insert/update this mapping.
    """
    __tablename__ = 'v_transactions_effective'

    id = Column(Integer, primary_key=True)
    date = Column(DateTime)
    description = Column(String(500))
    original_description = Column(String(500))
    amount = Column(Float)
    transaction_type = Column(SQLEnum(TransactionType))
    balance_after = Column(Float)
    category_id = Column(Integer)
    account_id = Column(Integer)
    linked_transaction_id = Column(Integer)
    is_internal_transfer = Column(Boolean)
    is_one_off = Column(Boolean)
    is_recurring = Column(Boolean)
    notes = Column(Text)
    tags = Column(String(500))
    bank_name = Column(String(100))
    account_number = Column(String(50))
    statement_file = Column(String(255))
    source = Column(String(20))
    external_id = Column(String(160))
    import_hash = Column(String(64))
    import_batch_id = Column(Integer)
    currency = Column(String(3))
    amount_base = Column(Float)
    fx_rate = Column(Float)
    pending = Column(Boolean)
    value_date = Column(DateTime)
    counterparty = Column(String(200))
    merchant_normalized = Column(String(120))
    raw_json = Column(Text)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    is_excluded = Column(Boolean)
    is_edited = Column(Boolean)

    category = relationship('Category',
                            primaryjoin='foreign(TransactionView.category_id) == Category.id',
                            viewonly=True)
    account = relationship('Account',
                           primaryjoin='foreign(TransactionView.account_id) == Account.id',
                           viewonly=True)


# ==================== IMPORTS ====================

class ImportBatch(Base):
    __tablename__ = 'import_batches'

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('accounts.id'))
    source = Column(String(20), default='statement')  # statement | bank_sync | csv | api
    provider = Column(String(40))
    bank_name = Column(String(120))
    profile_id = Column(String(60))
    filename = Column(String(255))
    file_hash = Column(String(64))
    status = Column(String(20), default='preview')  # preview | committed | reverted | failed
    parsed_count = Column(Integer, default=0)
    imported_count = Column(Integer, default=0)
    skipped_count = Column(Integer, default=0)
    opening_balance = Column(Float)
    closing_balance = Column(Float)
    currency = Column(String(3))
    confidence = Column(Float)
    issues_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    committed_at = Column(DateTime)
    reverted_at = Column(DateTime)
    error_message = Column(Text)

    account = relationship('Account')
    rows = relationship('ImportRow', back_populates='batch', cascade='all, delete-orphan')


class ImportRow(Base):
    __tablename__ = 'import_rows'

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey('import_batches.id', ondelete='CASCADE'), nullable=False)
    row_index = Column(Integer, default=0)
    raw = Column(Text)
    parsed_json = Column(Text)
    confidence = Column(Float)
    issues_json = Column(Text)
    status = Column(String(20), default='pending')  # pending | imported | skipped | edited
    transaction_id = Column(Integer, ForeignKey('transactions.id'))
    external_id = Column(String(160))

    batch = relationship('ImportBatch', back_populates='rows')


# ==================== BANK CONNECTIONS ====================

class Institution(Base):
    __tablename__ = 'institutions'

    id = Column(Integer, primary_key=True)
    provider = Column(String(40), nullable=False)
    provider_ref = Column(String(160))
    name = Column(String(160), nullable=False)
    country = Column(String(2))
    logo_url = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint('provider', 'provider_ref', name='uq_institution_provider_ref'),)


class BankConnection(Base):
    __tablename__ = 'bank_connections'

    id = Column(Integer, primary_key=True)
    provider = Column(String(40), nullable=False)
    institution_id = Column(Integer, ForeignKey('institutions.id'))
    display_name = Column(String(160))
    status = Column(String(20), default='active')  # active | expired | error | revoked
    credentials = Column(Text)  # Fernet-encrypted JSON (session id / access URL)
    consent_expires_at = Column(DateTime)
    last_sync_at = Column(DateTime)
    last_sync_error = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    institution = relationship('Institution')
    accounts = relationship('Account', back_populates='connection')


# ==================== AGENT ACCESS ====================

class AgentAudit(Base):
    __tablename__ = 'agent_audit'

    id = Column(Integer, primary_key=True)
    token_id = Column(Integer)  # references agent_tokens in users.db (kept loose: separate DB)
    actor = Column(String(80))
    tool = Column(String(80), nullable=False)
    args_json = Column(Text)
    result_summary = Column(Text)
    status = Column(String(20), default='ok')
    duration_ms = Column(Integer)
    dry_run = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ==================== DASHBOARDS, GOALS ====================

class DashboardPage(Base):
    __tablename__ = 'dashboard_pages'

    id = Column(Integer, primary_key=True)
    name = Column(String(80), nullable=False)
    slug = Column(String(80), unique=True, nullable=False)
    icon = Column(String(40), default='layout-dashboard')
    description = Column(String(255))
    position = Column(Integer, default=0)
    is_default = Column(Boolean, default=False)
    created_by = Column(String(80), default='user')
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    widgets = relationship('DashboardWidget', back_populates='page',
                           cascade='all, delete-orphan', order_by='DashboardWidget.position')


class DashboardWidget(Base):
    __tablename__ = 'dashboard_widgets'

    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey('dashboard_pages.id', ondelete='CASCADE'), nullable=False)
    type = Column(String(30), nullable=False)  # metric | chart | list | budget | goal | text | progress
    title = Column(String(120), nullable=False)
    config_json = Column(Text, default='{}')
    layout_json = Column(Text, default='{}')
    position = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    page = relationship('DashboardPage', back_populates='widgets')


class Goal(Base):
    __tablename__ = 'goals'

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    target_amount = Column(Float, nullable=False)
    currency = Column(String(3), default='GBP')
    deadline = Column(DateTime)
    account_id = Column(Integer, ForeignKey('accounts.id'))
    category_id = Column(Integer, ForeignKey('categories.id'))
    status = Column(String(20), default='active')  # active | achieved | paused | cancelled
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    contributions = relationship('GoalContribution', back_populates='goal',
                                 cascade='all, delete-orphan')


class GoalContribution(Base):
    __tablename__ = 'goal_contributions'

    id = Column(Integer, primary_key=True)
    goal_id = Column(Integer, ForeignKey('goals.id', ondelete='CASCADE'), nullable=False)
    amount = Column(Float, nullable=False)
    date = Column(DateTime, default=datetime.utcnow)
    note = Column(String(255))
    transaction_id = Column(Integer, ForeignKey('transactions.id'))
    created_at = Column(DateTime, default=datetime.utcnow)

    goal = relationship('Goal', back_populates='contributions')


# ==================== MISC ====================

class FxRate(Base):
    __tablename__ = 'fx_rates'

    id = Column(Integer, primary_key=True)
    date = Column(DateTime, nullable=False)
    base = Column(String(3), nullable=False)
    quote = Column(String(3), nullable=False)
    rate = Column(Float, nullable=False)
    source = Column(String(40))
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint('date', 'base', 'quote', name='uq_fx_date_pair'),)


class SchemaVersion(Base):
    __tablename__ = 'schema_version'

    version = Column(Integer, primary_key=True)
    applied_at = Column(DateTime, default=datetime.utcnow)

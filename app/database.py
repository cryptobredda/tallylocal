import os
from contextlib import contextmanager
from contextvars import ContextVar

from flask import g, session as flask_session, has_request_context
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

# Paths
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.getenv('DATA_DIR', APP_DIR)
os.makedirs(DATA_ROOT, exist_ok=True)
DB_PATH = os.path.join(DATA_ROOT, 'finance_tracker.db')

# Engine/session caches keyed by db path
_engine_cache = {}
_session_factory_cache = {}
# Lets non-request code (CLI, MCP) point every helper at one database.
_active_db_path = ContextVar('tally_active_db_path', default=None)

Base = declarative_base()


@contextmanager
def activate_db(path):
    token = _active_db_path.set(path)
    try:
        yield
    finally:
        _active_db_path.reset(token)


def _install_pragmas(engine):
    """WAL + busy timeout: the app, the CLI and the MCP server share one file.

    Without these, an agent writing while the UI reads raises
    'database is locked' under normal use.
    """
    @event.listens_for(engine, 'connect')
    def _set_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        try:
            cur.execute('PRAGMA journal_mode=WAL')
            cur.execute('PRAGMA synchronous=NORMAL')
            cur.execute('PRAGMA busy_timeout=5000')
            cur.execute('PRAGMA foreign_keys=ON')
        finally:
            cur.close()


def make_engine(path):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    engine = create_engine(f'sqlite:///{path}', echo=False,
                           connect_args={'check_same_thread': False, 'timeout': 10})
    _install_pragmas(engine)
    return engine



def _resolve_db_path(db_path=None):
    """Return the active database path for this request/context."""
    if db_path:
        return db_path
    active = _active_db_path.get()
    if active:
        return active
    if has_request_context():
        requested = getattr(g, 'current_db_path', None)
        if requested:
            return requested
        stored = flask_session.get('db_path')
        if stored:
            g.current_db_path = stored
            return stored
    return DB_PATH


def get_engine(db_path=None):
    """Return (and cache) a SQLAlchemy engine for the given DB path."""
    path = _resolve_db_path(db_path)
    engine = _engine_cache.get(path)
    if not engine:
        engine = make_engine(path)
        _engine_cache[path] = engine
    return engine


def get_session(db_path=None):
    """Create a new session bound to the resolved engine."""
    path = _resolve_db_path(db_path)
    factory = _session_factory_cache.get(path)
    if not factory:
        engine = get_engine(path)
        factory = sessionmaker(bind=engine)
        _session_factory_cache[path] = factory
    return factory()


def init_db(db_path=None):
    """Initialise a finance database (schema + defaults)."""
    target_path = _resolve_db_path(db_path)
    engine = get_engine(target_path)
    from app import models  # noqa: F401
    from app.migrate import VIEW_NAME, ensure_schema, create_views
    # The projection is a VIEW, not a table: exclude it from create_all, then
    # create it once the base tables and their columns exist.
    Base.metadata.create_all(
        engine,
        tables=[t for t in Base.metadata.sorted_tables if t.name != VIEW_NAME],
    )
    # Legacy installs already have accounts/transactions; create_all() never
    # adds columns, so bring them up to date before anything queries them.
    ensure_schema(target_path)
    with engine.begin() as conn:
        create_views(conn)
    print(f"Database initialized at: {target_path}")
    _init_default_categories(target_path)
    _init_default_accounts(target_path)


def _init_default_categories(db_path):
    """Create default categories if they don't exist."""
    from app.models import Category, TransactionRule
    DEFAULT_CATEGORIES = [
        {'name': 'Groceries', 'color': '#22c55e', 'icon': 'shopping-cart', 'keywords': ['tesco', 'sainsbury', 'asda', 'aldi', 'lidl', 'morrisons', 'waitrose', 'co-op', 'grocery', 'supermarket']},
        {'name': 'Transport', 'color': '#3b82f6', 'icon': 'car', 'keywords': ['uber', 'lyft', 'taxi', 'train', 'bus', 'tfl', 'oyster', 'petrol', 'fuel', 'shell', 'bp', 'esso']},
        {'name': 'Utilities', 'color': '#f59e0b', 'icon': 'zap', 'keywords': ['electric', 'gas', 'water', 'internet', 'broadband', 'bt', 'virgin media', 'sky', 'ee', 'vodafone', 'o2', 'energy']},
        {'name': 'Entertainment', 'color': '#8b5cf6', 'icon': 'tv', 'keywords': ['netflix', 'spotify', 'amazon prime', 'disney', 'cinema', 'theatre', 'concert', 'gaming']},
        {'name': 'Dining Out', 'color': '#ec4899', 'icon': 'utensils', 'keywords': ['restaurant', 'cafe', 'coffee', 'starbucks', 'costa', 'mcdonalds', 'kfc', 'nandos', 'deliveroo', 'uber eats', 'just eat']},
        {'name': 'Shopping', 'color': '#14b8a6', 'icon': 'shopping-bag', 'keywords': ['amazon', 'ebay', 'argos', 'john lewis', 'currys', 'next', 'asos', 'clothing']},
        {'name': 'Health', 'color': '#ef4444', 'icon': 'heart', 'keywords': ['pharmacy', 'boots', 'superdrug', 'doctor', 'dentist', 'hospital', 'gym', 'fitness', 'pure gym']},
        {'name': 'Subscriptions', 'color': '#6366f1', 'icon': 'repeat', 'keywords': ['subscription', 'membership', 'monthly', 'annual']},
        {'name': 'Income', 'color': '#10b981', 'icon': 'trending-up', 'keywords': ['salary', 'wages', 'payment received', 'refund', 'cashback']},
        {'name': 'Transfers', 'color': '#64748b', 'icon': 'arrow-right-left', 'keywords': ['transfer', 'standing order']},
        {'name': 'Housing', 'color': '#f97316', 'icon': 'home', 'keywords': ['rent', 'mortgage', 'council tax', 'insurance']},
        {'name': 'Uncategorized', 'color': '#94a3b8', 'icon': 'help-circle', 'keywords': []},
    ]

    session = get_session(db_path)
    try:
        existing = session.query(Category).count()
        if existing == 0:
            for cat_data in DEFAULT_CATEGORIES:
                category = Category(
                    name=cat_data['name'],
                    color=cat_data['color'],
                    icon=cat_data['icon'],
                    is_system=True
                )
                session.add(category)
                session.flush()

                for keyword in cat_data['keywords']:
                    rule = TransactionRule(
                        pattern=keyword,
                        match_type='contains',
                        category_id=category.id,
                        is_active=True
                    )
                    session.add(rule)

            session.commit()
            print(f"Created {len(DEFAULT_CATEGORIES)} default categories in {db_path}")
    except Exception as e:
        session.rollback()
        print(f"Error creating default categories: {e}")
    finally:
        session.close()


def _init_default_accounts(db_path):
    """Create default Main Account if it doesn't exist."""
    from app.models import Account, AccountType
    session = get_session(db_path)
    try:
        main_exists = session.query(Account).filter(
            Account.account_type == AccountType.MAIN,
            Account.is_active.is_(True)
        ).first()

        if not main_exists:
            main_account = Account(
                name='Main Account',
                account_type=AccountType.MAIN,
                color='#3b82f6',
                icon='landmark',
                description='Primary bank account',
                is_active=True
            )
            session.add(main_account)
            session.commit()
            print(f"Created default Main Account in {db_path}")
    except Exception as e:
        session.rollback()
        print(f"Error creating default account: {e}")
    finally:
        session.close()


def set_request_db_path(db_path):
    """Store the db path for the current request context."""
    if has_request_context():
        g.current_db_path = db_path

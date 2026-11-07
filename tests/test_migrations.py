"""Upgrading a v1 install must keep its data and add the new columns.

The first release only ever called create_all(), which never alters an existing
table. Anyone updating a hosted instance has a v1 database, so this path is the
one that matters most.
"""
import sqlite3

LEGACY_SCHEMA = """
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    account_type VARCHAR(20) NOT NULL,
    color VARCHAR(20),
    icon VARCHAR(50),
    description TEXT,
    opening_balance FLOAT DEFAULT 0,
    is_active BOOLEAN DEFAULT 1,
    is_archived BOOLEAN DEFAULT 0,
    created_at DATETIME
);
CREATE TABLE categories (
    id INTEGER PRIMARY KEY,
    name VARCHAR(80) NOT NULL,
    color VARCHAR(20),
    icon VARCHAR(50),
    is_system BOOLEAN DEFAULT 0,
    created_at DATETIME
);
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY,
    date DATE NOT NULL,
    description VARCHAR(500) NOT NULL,
    original_description VARCHAR(500),
    amount FLOAT NOT NULL,
    transaction_type VARCHAR(20),
    balance_after FLOAT,
    category_id INTEGER,
    account_id INTEGER,
    linked_transaction_id INTEGER,
    is_internal_transfer BOOLEAN DEFAULT 0,
    is_one_off BOOLEAN DEFAULT 0,
    is_recurring BOOLEAN DEFAULT 0,
    notes TEXT,
    tags VARCHAR(500),
    bank_name VARCHAR(100),
    account_number VARCHAR(50),
    statement_file VARCHAR(255),
    created_at DATETIME,
    updated_at DATETIME
);
"""


def make_legacy_db(path):
    con = sqlite3.connect(path)
    con.executescript(LEGACY_SCHEMA)
    con.execute(
        "INSERT INTO accounts (name, account_type, opening_balance, is_active, is_archived, created_at)"
        " VALUES ('HSBC Current', 'CHECKING', 2500.0, 1, 0, '2026-01-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO categories (name, color, icon, is_system, created_at)"
        " VALUES ('Groceries', '#22c55e', 'cart', 0, '2026-01-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO transactions (date, description, original_description, amount,"
        " transaction_type, balance_after, category_id, account_id, is_internal_transfer,"
        " is_one_off, is_recurring, created_at, updated_at)"
        " VALUES ('2026-10-01', 'TESCO STORES', 'TESCO STORES', -42.5, 'expense',"
        " 2457.5, 1, 1, 0, 0, 0, '2026-10-01 00:00:00', '2026-10-01 00:00:00')"
    )
    con.commit()
    con.close()


def test_legacy_database_gains_new_columns_and_keeps_rows(tmp_path):
    path = tmp_path / 'finance_tracker.db'
    make_legacy_db(path)

    from app.database import init_db
    init_db(str(path))

    con = sqlite3.connect(path)
    tx_cols = {row[1] for row in con.execute('PRAGMA table_info(transactions)')}
    acc_cols = {row[1] for row in con.execute('PRAGMA table_info(accounts)')}
    assert {'source', 'external_id', 'import_hash', 'currency', 'pending'} <= tx_cols
    assert {'currency', 'connection_id', 'balance_current'} <= acc_cols

    assert con.execute('SELECT COUNT(*) FROM transactions').fetchone()[0] == 1
    assert con.execute('SELECT source FROM transactions').fetchone()[0] == 'manual'
    assert con.execute('SELECT currency FROM accounts').fetchone()[0] == 'GBP'

    # The projection must resolve on the upgraded table.
    assert con.execute('SELECT COUNT(*) FROM v_transactions_effective').fetchone()[0] == 1
    row = con.execute(
        'SELECT description, amount, category_id FROM v_transactions_effective'
    ).fetchone()
    assert row == ('TESCO STORES', -42.5, 1)
    assert con.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] == 2
    con.close()


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / 'finance_tracker.db'
    make_legacy_db(path)

    from app.database import init_db
    init_db(str(path))
    init_db(str(path))

    con = sqlite3.connect(path)
    assert con.execute('SELECT COUNT(*) FROM transactions').fetchone()[0] == 1
    assert len(con.execute('SELECT version FROM schema_version').fetchall()) == 1
    con.close()


def test_legacy_accounts_are_queryable_through_the_orm(tmp_path):
    path = tmp_path / 'finance_tracker.db'
    make_legacy_db(path)

    from app.database import init_db, get_session
    from app.models import Account
    init_db(str(path))

    session = get_session(str(path))
    try:
        accounts = {a.name: a for a in session.query(Account).all()}
        assert 'HSBC Current' in accounts
        legacy = accounts['HSBC Current']
        assert legacy.currency == 'GBP'
        assert legacy.opening_balance == 2500.0
        assert legacy.balance_current is None
        assert legacy.account_type.value == 'checking'
    finally:
        session.close()

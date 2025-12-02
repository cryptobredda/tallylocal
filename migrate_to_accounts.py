#!/usr/bin/env python3
"""
Migration script to convert pots system to accounts system.

This script:
1. Renames 'pots' table to 'accounts'
2. Adds new columns to accounts table
3. Renames 'pot_id' to 'account_id' in transactions and rules
4. Adds new columns for transfer tracking
5. Creates default Main Account
6. Removes pot_allocations table (transfers replace this)
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'finance_tracker.db')

def backup_database():
    """Create a backup of the database before migration"""
    backup_path = DB_PATH.replace('.db', f'_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db')
    import shutil
    shutil.copy2(DB_PATH, backup_path)
    print(f"Database backed up to: {backup_path}")
    return backup_path

def migrate():
    print(f"Starting migration on: {DB_PATH}")
    
    if not os.path.exists(DB_PATH):
        print("Database not found. Nothing to migrate.")
        return
    
    # Backup first
    backup_database()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Check if migration already done
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'")
        if cursor.fetchone():
            print("Migration already completed (accounts table exists)")
            return
        
        print("Step 1: Creating new accounts table...")
        cursor.execute("""
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                account_type VARCHAR(10) DEFAULT 'sub',
                color VARCHAR(7) DEFAULT '#10b981',
                icon VARCHAR(50) DEFAULT 'wallet',
                description VARCHAR(255),
                is_active BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        print("Step 2: Migrating data from pots to accounts...")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pots'")
        if cursor.fetchone():
            cursor.execute("""
                INSERT INTO accounts (id, name, color, icon, is_active, created_at)
                SELECT id, name, color, icon, is_active, created_at FROM pots
            """)
            print(f"  Migrated {cursor.rowcount} pots to accounts")
        
        print("Step 3: Creating Main Account if no accounts exist or none is main...")
        cursor.execute("SELECT COUNT(*) FROM accounts")
        count = cursor.fetchone()[0]
        
        # Check if we need to create main account
        cursor.execute("SELECT id FROM accounts WHERE account_type = 'main'")
        main_exists = cursor.fetchone()
        
        if not main_exists:
            if count > 0:
                # Make the first account the main account
                cursor.execute("UPDATE accounts SET account_type = 'main' WHERE id = (SELECT MIN(id) FROM accounts)")
                print("  Converted first account to Main Account")
            else:
                # Create new main account
                cursor.execute("""
                    INSERT INTO accounts (name, account_type, color, icon, description)
                    VALUES ('Main Account', 'main', '#3b82f6', 'landmark', 'Primary bank account')
                """)
                print("  Created new Main Account")
        
        print("Step 4: Adding new columns to transactions table...")
        # Check existing columns
        cursor.execute("PRAGMA table_info(transactions)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'pot_id' in columns and 'account_id' not in columns:
            # Rename pot_id to account_id by recreating table
            print("  Renaming pot_id to account_id...")
            cursor.execute("""
                CREATE TABLE transactions_new (
                    id INTEGER PRIMARY KEY,
                    date DATETIME NOT NULL,
                    description VARCHAR(500) NOT NULL,
                    original_description VARCHAR(500),
                    amount FLOAT NOT NULL,
                    transaction_type VARCHAR(10) DEFAULT 'expense',
                    balance_after FLOAT,
                    category_id INTEGER REFERENCES categories(id),
                    account_id INTEGER REFERENCES accounts(id),
                    linked_transaction_id INTEGER REFERENCES transactions(id),
                    is_internal_transfer BOOLEAN DEFAULT 0,
                    is_one_off BOOLEAN DEFAULT 0,
                    is_recurring BOOLEAN DEFAULT 0,
                    bank_name VARCHAR(100),
                    account_number VARCHAR(50),
                    statement_file VARCHAR(255),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                INSERT INTO transactions_new 
                (id, date, description, original_description, amount, transaction_type, 
                 balance_after, category_id, account_id, is_one_off, is_recurring,
                 bank_name, account_number, statement_file, created_at, updated_at)
                SELECT id, date, description, original_description, amount, transaction_type,
                       balance_after, category_id, pot_id, is_one_off, is_recurring,
                       bank_name, account_number, statement_file, created_at, updated_at
                FROM transactions
            """)
            
            cursor.execute("DROP TABLE transactions")
            cursor.execute("ALTER TABLE transactions_new RENAME TO transactions")
            print("  Transactions table migrated")
        
        elif 'account_id' not in columns:
            # Just add new columns
            cursor.execute("ALTER TABLE transactions ADD COLUMN account_id INTEGER REFERENCES accounts(id)")
            cursor.execute("ALTER TABLE transactions ADD COLUMN linked_transaction_id INTEGER REFERENCES transactions(id)")
            cursor.execute("ALTER TABLE transactions ADD COLUMN is_internal_transfer BOOLEAN DEFAULT 0")
        
        print("Step 5: Updating transaction_rules table...")
        cursor.execute("PRAGMA table_info(transaction_rules)")
        rule_columns = [col[1] for col in cursor.fetchall()]
        
        if 'pot_id' in rule_columns and 'account_id' not in rule_columns:
            cursor.execute("""
                CREATE TABLE transaction_rules_new (
                    id INTEGER PRIMARY KEY,
                    pattern VARCHAR(255) NOT NULL,
                    match_type VARCHAR(20) DEFAULT 'contains',
                    category_id INTEGER REFERENCES categories(id),
                    account_id INTEGER REFERENCES accounts(id),
                    is_active BOOLEAN DEFAULT 1,
                    priority INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                INSERT INTO transaction_rules_new
                (id, pattern, match_type, category_id, account_id, is_active, priority, created_at)
                SELECT id, pattern, match_type, category_id, pot_id, is_active, priority, created_at
                FROM transaction_rules
            """)
            
            cursor.execute("DROP TABLE transaction_rules")
            cursor.execute("ALTER TABLE transaction_rules_new RENAME TO transaction_rules")
            print("  Transaction rules table migrated")
        
        print("Step 6: Adding account_id to bank_statements...")
        cursor.execute("PRAGMA table_info(bank_statements)")
        stmt_columns = [col[1] for col in cursor.fetchall()]
        
        if 'account_id' not in stmt_columns:
            cursor.execute("ALTER TABLE bank_statements ADD COLUMN account_id INTEGER REFERENCES accounts(id)")
            # Set existing statements to main account
            cursor.execute("""
                UPDATE bank_statements 
                SET account_id = (SELECT id FROM accounts WHERE account_type = 'main' LIMIT 1)
            """)
            print("  Bank statements table updated")
        
        print("Step 7: Dropping old pots table...")
        cursor.execute("DROP TABLE IF EXISTS pots")
        
        print("Step 8: Dropping pot_allocations table (replaced by transfers)...")
        cursor.execute("DROP TABLE IF EXISTS pot_allocations")
        
        conn.commit()
        print("\nMigration completed successfully!")
        
        # Verify
        cursor.execute("SELECT id, name, account_type FROM accounts")
        accounts = cursor.fetchall()
        print(f"\nAccounts after migration:")
        for acc in accounts:
            print(f"  ID: {acc[0]}, Name: {acc[1]}, Type: {acc[2]}")
        
    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        raise
    finally:
        conn.close()

if __name__ == '__main__':
    migrate()

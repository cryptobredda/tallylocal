from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.ext.declarative import declarative_base
import os

# Get the directory where the app is located
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(APP_DIR, 'finance_tracker.db')
DATABASE_URL = os.getenv('DATABASE_URL', f'sqlite:///{DB_PATH}')

engine = create_engine(DATABASE_URL, echo=False)
Session = scoped_session(sessionmaker(bind=engine))
Base = declarative_base()

def init_db():
    from app import models
    Base.metadata.create_all(engine)
    print(f"Database initialized at: {DB_PATH}")
    
    # Initialize default categories and accounts
    _init_default_categories()
    _init_default_accounts()

def _init_default_categories():
    """Create default categories if they don't exist"""
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
    
    session = Session()
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
            print(f"Created {len(DEFAULT_CATEGORIES)} default categories")
    except Exception as e:
        session.rollback()
        print(f"Error creating default categories: {e}")
    finally:
        session.close()

def _init_default_accounts():
    """Create default Main Account if it doesn't exist"""
    from app.models import Account, AccountType
    
    session = Session()
    try:
        # Check if main account exists
        main_exists = session.query(Account).filter(
            Account.account_type == AccountType.MAIN,
            Account.is_active == True
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
            print("Created default Main Account")
    except Exception as e:
        session.rollback()
        print(f"Error creating default account: {e}")
    finally:
        session.close()

def get_session():
    return Session()

def remove_session():
    """Remove the current session - call this at the end of requests"""
    Session.remove()

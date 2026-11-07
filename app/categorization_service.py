import re
from typing import Optional, Dict, List
from app.database import get_session
from app.models import Category, TransactionRule, Transaction


DEFAULT_CATEGORIES = [
    {'name': 'Groceries', 'color': '#22c55e', 'icon': 'shopping-cart', 'keywords': ['tesco', 'sainsbury', 'asda', 'aldi', 'lidl', 'morrisons', 'waitrose', 'co-op', 'grocery', 'supermarket']},
    {'name': 'Transport', 'color': '#3b82f6', 'icon': 'car', 'keywords': ['uber', 'lyft', 'taxi', 'train', 'bus', 'tfl', 'oyster', 'petrol', 'fuel', 'shell', 'bp', 'esso']},
    {'name': 'Utilities', 'color': '#f59e0b', 'icon': 'zap', 'keywords': ['electric', 'gas', 'water', 'internet', 'broadband', 'bt', 'virgin media', 'sky', 'ee', 'vodafone', 'o2']},
    {'name': 'Entertainment', 'color': '#8b5cf6', 'icon': 'tv', 'keywords': ['netflix', 'spotify', 'amazon prime', 'disney', 'cinema', 'theatre', 'concert', 'gaming']},
    {'name': 'Dining Out', 'color': '#ec4899', 'icon': 'utensils', 'keywords': ['restaurant', 'cafe', 'coffee', 'starbucks', 'costa', 'mcdonalds', 'kfc', 'nandos', 'deliveroo', 'uber eats', 'just eat']},
    {'name': 'Shopping', 'color': '#14b8a6', 'icon': 'shopping-bag', 'keywords': ['amazon', 'ebay', 'argos', 'john lewis', 'currys', 'next', 'asos', 'clothing']},
    {'name': 'Health', 'color': '#ef4444', 'icon': 'heart', 'keywords': ['pharmacy', 'boots', 'superdrug', 'doctor', 'dentist', 'hospital', 'gym', 'fitness']},
    {'name': 'Subscriptions', 'color': '#6366f1', 'icon': 'repeat', 'keywords': ['subscription', 'membership', 'monthly', 'annual']},
    {'name': 'Income', 'color': '#10b981', 'icon': 'trending-up', 'keywords': ['salary', 'wages', 'payment received', 'refund', 'cashback']},
    {'name': 'Transfers', 'color': '#64748b', 'icon': 'arrow-right-left', 'keywords': ['transfer', 'standing order', 'direct debit']},
    {'name': 'Housing', 'color': '#f97316', 'icon': 'home', 'keywords': ['rent', 'mortgage', 'council tax', 'insurance']},
    {'name': 'Uncategorized', 'color': '#94a3b8', 'icon': 'help-circle', 'keywords': []},
]


class CategorizationService:
    def __init__(self):
        pass
    
    def categorize_transaction(self, description: str, amount: float = None, session=None) -> Optional[Dict]:
        """Categorize a transaction based on description.
        
        Args:
            description: Transaction description
            amount: Transaction amount (optional)
            session: SQLAlchemy session (optional - will create one if not provided)
        """
        own_session = session is None
        if own_session:
            session = get_session()
        
        try:
            rules = session.query(TransactionRule).filter(
                TransactionRule.is_active == True
            ).order_by(TransactionRule.priority.desc()).all()
            
            desc_lower = description.lower()
            
            for rule in rules:
                pattern = rule.pattern.lower()
                
                if rule.match_type == 'contains':
                    # Short patterns must match whole words: 'tfl' inside 'netflix'
                    # would otherwise file Netflix under Transport.
                    if len(pattern) <= 4:
                        hit = re.search(rf'\b{re.escape(pattern)}\b', desc_lower) is not None
                    else:
                        hit = pattern in desc_lower
                    if hit:
                        return {
                            'category_id': rule.category_id,
                            'category_name': rule.category.name if rule.category else None,
                            'pot_id': rule.pot_id,
                            'rule_id': rule.id,
                            'confidence': 'high'
                        }
                elif rule.match_type == 'exact':
                    if pattern == desc_lower:
                        return {
                            'category_id': rule.category_id,
                            'category_name': rule.category.name if rule.category else None,
                            'pot_id': rule.pot_id,
                            'rule_id': rule.id,
                            'confidence': 'high'
                        }
                elif rule.match_type == 'regex':
                    if re.search(pattern, desc_lower):
                        return {
                            'category_id': rule.category_id,
                            'category_name': rule.category.name if rule.category else None,
                            'pot_id': rule.pot_id,
                            'rule_id': rule.id,
                            'confidence': 'high'
                        }
            
            uncategorized = session.query(Category).filter(Category.name == 'Uncategorized').first()
            if uncategorized:
                return {
                    'category_id': uncategorized.id,
                    'category_name': 'Uncategorized',
                    'pot_id': None,
                    'rule_id': None,
                    'confidence': 'none'
                }
            
            return None
        finally:
            if own_session:
                session.close()
    
    def learn_from_categorization(self, description: str, category_id: int, 
                                   pot_id: int = None, is_one_off: bool = False) -> Optional[int]:
        if is_one_off:
            return None
        
        session = get_session()
        try:
            existing_rule = session.query(TransactionRule).filter(
                TransactionRule.pattern == description.lower(),
                TransactionRule.match_type == 'contains'
            ).first()
            
            if existing_rule:
                existing_rule.category_id = category_id
                existing_rule.pot_id = pot_id
                session.commit()
                return existing_rule.id
            
            words = description.lower().split()
            significant_words = [w for w in words if len(w) > 3 and not w.isdigit()]
            
            if significant_words:
                pattern = significant_words[0]
                if len(significant_words) > 1:
                    pattern = ' '.join(significant_words[:2])
            else:
                pattern = description.lower()
            
            new_rule = TransactionRule(
                pattern=pattern,
                match_type='contains',
                category_id=category_id,
                pot_id=pot_id,
                is_active=True,
                priority=10
            )
            session.add(new_rule)
            session.commit()
            return new_rule.id
        finally:
            session.close()
    
    def get_all_categories(self) -> List[Dict]:
        session = get_session()
        try:
            categories = session.query(Category).all()
            return [
                {
                    'id': c.id,
                    'name': c.name,
                    'color': c.color,
                    'icon': c.icon,
                    'is_system': c.is_system
                }
                for c in categories
            ]
        finally:
            session.close()
    
    def create_category(self, name: str, color: str = '#6366f1', icon: str = 'tag') -> Dict:
        session = get_session()
        try:
            category = Category(name=name, color=color, icon=icon, is_system=False)
            session.add(category)
            session.commit()
            return {
                'id': category.id,
                'name': category.name,
                'color': category.color,
                'icon': category.icon
            }
        finally:
            session.close()
    
    def get_rules(self) -> List[Dict]:
        session = get_session()
        try:
            rules = session.query(TransactionRule).order_by(
                TransactionRule.priority.desc()
            ).all()
            return [
                {
                    'id': r.id,
                    'pattern': r.pattern,
                    'match_type': r.match_type,
                    'category_id': r.category_id,
                    'category_name': r.category.name if r.category else None,
                    'pot_id': r.pot_id,
                    'is_active': r.is_active,
                    'priority': r.priority
                }
                for r in rules
            ]
        finally:
            session.close()


categorization_service = CategorizationService()

from typing import List, Dict, Optional
from datetime import datetime
from sqlalchemy import func, and_
from app.database import get_session
from app.models import Account, AccountType, Transaction, TransactionType


class AccountService:
    def get_all_accounts(self) -> List[Dict]:
        """Get all active accounts with their balances"""
        session = get_session()
        try:
            accounts = session.query(Account).filter(Account.is_active == True).all()
            result = []
            
            for account in accounts:
                balance_info = self._calculate_account_balance(session, account.id)
                
                result.append({
                    'id': account.id,
                    'name': account.name,
                    'account_type': account.account_type.value,
                    'is_main': account.account_type == AccountType.MAIN,
                    'color': account.color,
                    'icon': account.icon,
                    'description': account.description,
                    'balance': balance_info['balance'],
                    'total_in': balance_info['total_in'],
                    'total_out': balance_info['total_out'],
                    'transaction_count': balance_info['transaction_count']
                })
            
            # Sort: main account first, then by name
            result.sort(key=lambda x: (not x['is_main'], x['name']))
            return result
        finally:
            session.close()
    
    def _calculate_account_balance(self, session, account_id: int) -> Dict:
        """Calculate balance for an account based on its transactions"""
        # Income to this account
        income = session.query(func.sum(Transaction.amount)).filter(
            and_(
                Transaction.account_id == account_id,
                Transaction.transaction_type == TransactionType.INCOME
            )
        ).scalar() or 0
        
        # Expenses from this account
        expenses = session.query(func.sum(Transaction.amount)).filter(
            and_(
                Transaction.account_id == account_id,
                Transaction.transaction_type == TransactionType.EXPENSE
            )
        ).scalar() or 0
        
        # Transfer IN (transfers where this account receives money)
        transfer_in = session.query(func.sum(Transaction.amount)).filter(
            and_(
                Transaction.account_id == account_id,
                Transaction.transaction_type == TransactionType.TRANSFER,
                Transaction.is_internal_transfer == True,
                Transaction.amount > 0  # Positive = incoming
            )
        ).scalar() or 0
        
        # Transfer OUT (transfers where this account sends money) - stored as positive, deducted
        transfer_out = session.query(func.sum(Transaction.amount)).filter(
            and_(
                Transaction.account_id == account_id,
                Transaction.transaction_type == TransactionType.TRANSFER,
                Transaction.is_internal_transfer == True,
                Transaction.amount < 0  # Negative = outgoing
            )
        ).scalar() or 0
        
        transaction_count = session.query(func.count(Transaction.id)).filter(
            Transaction.account_id == account_id
        ).scalar() or 0
        
        total_in = float(income) + float(transfer_in)
        total_out = float(expenses) + abs(float(transfer_out))
        balance = total_in - total_out
        
        return {
            'balance': balance,
            'total_in': total_in,
            'total_out': total_out,
            'transaction_count': transaction_count
        }
    
    def get_account(self, account_id: int) -> Optional[Dict]:
        """Get a single account by ID"""
        session = get_session()
        try:
            account = session.query(Account).filter(Account.id == account_id).first()
            if not account:
                return None
            
            balance_info = self._calculate_account_balance(session, account.id)
            
            return {
                'id': account.id,
                'name': account.name,
                'account_type': account.account_type.value,
                'is_main': account.account_type == AccountType.MAIN,
                'color': account.color,
                'icon': account.icon,
                'description': account.description,
                'balance': balance_info['balance'],
                'total_in': balance_info['total_in'],
                'total_out': balance_info['total_out'],
                'transaction_count': balance_info['transaction_count']
            }
        finally:
            session.close()
    
    def get_main_account(self) -> Optional[Dict]:
        """Get the main account"""
        session = get_session()
        try:
            account = session.query(Account).filter(
                Account.account_type == AccountType.MAIN,
                Account.is_active == True
            ).first()
            
            if not account:
                return None
            
            balance_info = self._calculate_account_balance(session, account.id)
            
            return {
                'id': account.id,
                'name': account.name,
                'account_type': account.account_type.value,
                'is_main': True,
                'color': account.color,
                'icon': account.icon,
                'description': account.description,
                'balance': balance_info['balance'],
                'total_in': balance_info['total_in'],
                'total_out': balance_info['total_out'],
                'transaction_count': balance_info['transaction_count']
            }
        finally:
            session.close()
    
    def create_account(self, name: str, account_type: str = 'checking',
                       color: str = '#10b981', icon: str = 'wallet',
                       description: str = None) -> Dict:
        """Create a new account"""
        session = get_session()
        try:
            # Map account type string to enum
            type_map = {
                'main': AccountType.MAIN,
                'checking': AccountType.CHECKING,
                'savings': AccountType.SAVINGS,
                'credit_card': AccountType.CREDIT_CARD,
                'cash': AccountType.CASH,
                'investment': AccountType.INVESTMENT,
                'other': AccountType.OTHER,
                'sub': AccountType.CHECKING,  # Backward compatibility
            }
            acc_type = type_map.get(account_type.lower(), AccountType.CHECKING)
            
            # Only one main account allowed
            if acc_type == AccountType.MAIN:
                existing_main = session.query(Account).filter(
                    Account.account_type == AccountType.MAIN,
                    Account.is_active == True
                ).first()
                if existing_main:
                    raise ValueError("A main account already exists")
            
            account = Account(
                name=name,
                account_type=acc_type,
                color=color,
                icon=icon,
                description=description,
                is_active=True
            )
            session.add(account)
            session.commit()
            
            return {
                'id': account.id,
                'name': account.name,
                'account_type': account.account_type.value,
                'is_main': account.account_type == AccountType.MAIN,
                'color': account.color,
                'icon': account.icon,
                'description': account.description,
                'balance': 0,
                'total_in': 0,
                'total_out': 0
            }
        finally:
            session.close()
    
    def update_account(self, account_id: int, **kwargs) -> Optional[Dict]:
        """Update an account"""
        session = get_session()
        try:
            account = session.query(Account).filter(Account.id == account_id).first()
            if not account:
                return None
            
            # Don't allow changing main account type
            if 'account_type' in kwargs and account.account_type == AccountType.MAIN:
                del kwargs['account_type']
            
            for key, value in kwargs.items():
                if hasattr(account, key) and key not in ['id', 'created_at']:
                    setattr(account, key, value)
            
            session.commit()
            
            balance_info = self._calculate_account_balance(session, account.id)
            
            return {
                'id': account.id,
                'name': account.name,
                'account_type': account.account_type.value,
                'is_main': account.account_type == AccountType.MAIN,
                'color': account.color,
                'icon': account.icon,
                'description': account.description,
                'balance': balance_info['balance']
            }
        finally:
            session.close()
    
    def delete_account(self, account_id: int) -> bool:
        """Soft delete an account (can't delete main account)"""
        session = get_session()
        try:
            account = session.query(Account).filter(Account.id == account_id).first()
            if not account:
                return False
            
            # Can't delete main account
            if account.account_type == AccountType.MAIN:
                raise ValueError("Cannot delete the main account")
            
            account.is_active = False
            session.commit()
            return True
        finally:
            session.close()
    
    def create_transfer(self, from_account_id: int, to_account_id: int, 
                        amount: float, description: str = None,
                        date: datetime = None) -> Dict:
        """
        Create a transfer between two accounts.
        Creates two linked transactions:
        - Expense (negative) on source account
        - Income (positive) on destination account
        """
        if amount <= 0:
            raise ValueError("Transfer amount must be positive")
        
        if from_account_id == to_account_id:
            raise ValueError("Cannot transfer to the same account")
        
        session = get_session()
        try:
            # Verify both accounts exist
            from_account = session.query(Account).filter(Account.id == from_account_id).first()
            to_account = session.query(Account).filter(Account.id == to_account_id).first()
            
            if not from_account or not to_account:
                raise ValueError("One or both accounts not found")
            
            transfer_date = date or datetime.utcnow()
            transfer_desc = description or f"Transfer to {to_account.name}"
            transfer_desc_in = description or f"Transfer from {from_account.name}"
            
            # Create outgoing transaction (expense from source)
            tx_out = Transaction(
                date=transfer_date,
                description=transfer_desc,
                original_description=transfer_desc,
                amount=amount,  # Store as positive, type indicates direction
                transaction_type=TransactionType.EXPENSE,
                account_id=from_account_id,
                is_internal_transfer=True,
                is_one_off=True
            )
            session.add(tx_out)
            session.flush()  # Get the ID
            
            # Create incoming transaction (income to destination)
            tx_in = Transaction(
                date=transfer_date,
                description=transfer_desc_in,
                original_description=transfer_desc_in,
                amount=amount,
                transaction_type=TransactionType.INCOME,
                account_id=to_account_id,
                linked_transaction_id=tx_out.id,
                is_internal_transfer=True,
                is_one_off=True
            )
            session.add(tx_in)
            session.flush()
            
            # Link the outgoing transaction to incoming
            tx_out.linked_transaction_id = tx_in.id
            
            session.commit()
            
            return {
                'success': True,
                'from_account': {
                    'id': from_account.id,
                    'name': from_account.name,
                    'transaction_id': tx_out.id
                },
                'to_account': {
                    'id': to_account.id,
                    'name': to_account.name,
                    'transaction_id': tx_in.id
                },
                'amount': amount,
                'date': transfer_date.isoformat(),
                'description': transfer_desc
            }
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()
    
    def get_account_transactions(self, account_id: int, 
                                  include_transfers: bool = True,
                                  limit: int = 50) -> List[Dict]:
        """Get transactions for a specific account"""
        session = get_session()
        try:
            query = session.query(Transaction).filter(
                Transaction.account_id == account_id
            )
            
            if not include_transfers:
                query = query.filter(Transaction.is_internal_transfer == False)
            
            transactions = query.order_by(Transaction.date.desc()).limit(limit).all()
            
            return [
                {
                    'id': t.id,
                    'date': t.date.isoformat(),
                    'description': t.description,
                    'amount': t.amount,
                    'type': t.transaction_type.value,
                    'is_internal_transfer': t.is_internal_transfer,
                    'linked_transaction_id': t.linked_transaction_id,
                    'category_id': t.category_id,
                    'category_name': t.category.name if t.category else None
                }
                for t in transactions
            ]
        finally:
            session.close()
    
    def get_transfer_history(self, limit: int = 50) -> List[Dict]:
        """Get history of all transfers"""
        session = get_session()
        try:
            # Get outgoing transfer transactions (to avoid duplicates)
            transfers = session.query(Transaction).filter(
                and_(
                    Transaction.is_internal_transfer == True,
                    Transaction.transaction_type == TransactionType.EXPENSE
                )
            ).order_by(Transaction.date.desc()).limit(limit).all()
            
            result = []
            for t in transfers:
                linked = session.query(Transaction).filter(
                    Transaction.id == t.linked_transaction_id
                ).first()
                
                from_account = session.query(Account).filter(
                    Account.id == t.account_id
                ).first()
                
                to_account = None
                if linked:
                    to_account = session.query(Account).filter(
                        Account.id == linked.account_id
                    ).first()
                
                result.append({
                    'id': t.id,
                    'date': t.date.isoformat(),
                    'amount': t.amount,
                    'description': t.description,
                    'from_account': {
                        'id': from_account.id,
                        'name': from_account.name
                    } if from_account else None,
                    'to_account': {
                        'id': to_account.id,
                        'name': to_account.name
                    } if to_account else None
                })
            
            return result
        finally:
            session.close()
    
    def get_total_balance(self) -> Dict:
        """Get main account balance (as specified, not aggregated with sub-accounts)"""
        main = self.get_main_account()
        
        session = get_session()
        try:
            # Get all sub-account balances
            sub_accounts = session.query(Account).filter(
                and_(
                    Account.account_type == AccountType.SUB,
                    Account.is_active == True
                )
            ).all()
            
            sub_balances = []
            total_sub = 0
            for acc in sub_accounts:
                balance_info = self._calculate_account_balance(session, acc.id)
                sub_balances.append({
                    'id': acc.id,
                    'name': acc.name,
                    'balance': balance_info['balance']
                })
                total_sub += balance_info['balance']
            
            return {
                'main_account_balance': main['balance'] if main else 0,
                'main_account_name': main['name'] if main else 'Main Account',
                'sub_accounts': sub_balances,
                'total_in_sub_accounts': total_sub,
                'combined_total': (main['balance'] if main else 0) + total_sub
            }
        finally:
            session.close()


# Singleton instance
account_service = AccountService()

# Backward compatibility alias
pot_service = account_service

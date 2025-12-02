from typing import List, Dict, Optional
from datetime import datetime, timedelta
from sqlalchemy import func, and_
from app.database import get_session
from app.models import Pot, PotAllocation, Transaction, TransactionType


class PotService:
    def get_all_pots(self) -> List[Dict]:
        session = get_session()
        try:
            pots = session.query(Pot).filter(Pot.is_active == True).all()
            result = []
            for pot in pots:
                # Money explicitly added to pot via "Add Money"
                manual_allocations = session.query(func.sum(PotAllocation.amount)).filter(
                    PotAllocation.pot_id == pot.id
                ).scalar() or 0
                
                # Income transactions assigned to this pot (adds to pot)
                income_assigned = session.query(func.sum(Transaction.amount)).filter(
                    and_(
                        Transaction.pot_id == pot.id,
                        Transaction.transaction_type == TransactionType.INCOME
                    )
                ).scalar() or 0
                
                # Expense transactions assigned to this pot (spends from pot)
                expenses_assigned = session.query(func.sum(Transaction.amount)).filter(
                    and_(
                        Transaction.pot_id == pot.id,
                        Transaction.transaction_type == TransactionType.EXPENSE
                    )
                ).scalar() or 0
                
                # Total available = manual allocations + income assigned - expenses assigned
                total_in = float(manual_allocations) + float(income_assigned)
                total_out = float(expenses_assigned)
                available = total_in - total_out
                
                result.append({
                    'id': pot.id,
                    'name': pot.name,
                    'target_amount': pot.target_amount,
                    'current_amount': pot.current_amount,
                    'allocated_total': total_in,
                    'spent_total': total_out,
                    'available': available,
                    'color': pot.color,
                    'icon': pot.icon,
                    'auto_allocate_percentage': pot.auto_allocate_percentage,
                    'progress': (available / pot.target_amount * 100) if pot.target_amount > 0 else 0
                })
            return result
        finally:
            session.close()
    
    def create_pot(self, name: str, target_amount: float = 0, 
                   color: str = '#10b981', icon: str = 'piggy-bank',
                   auto_allocate_percentage: float = 0) -> Dict:
        session = get_session()
        try:
            pot = Pot(
                name=name,
                target_amount=target_amount,
                color=color,
                icon=icon,
                auto_allocate_percentage=auto_allocate_percentage,
                is_active=True
            )
            session.add(pot)
            session.commit()
            return {
                'id': pot.id,
                'name': pot.name,
                'target_amount': pot.target_amount,
                'current_amount': pot.current_amount,
                'color': pot.color,
                'icon': pot.icon,
                'auto_allocate_percentage': pot.auto_allocate_percentage
            }
        finally:
            session.close()
    
    def update_pot(self, pot_id: int, **kwargs) -> Optional[Dict]:
        session = get_session()
        try:
            pot = session.query(Pot).filter(Pot.id == pot_id).first()
            if not pot:
                return None
            
            for key, value in kwargs.items():
                if hasattr(pot, key):
                    setattr(pot, key, value)
            
            session.commit()
            return {
                'id': pot.id,
                'name': pot.name,
                'target_amount': pot.target_amount,
                'current_amount': pot.current_amount,
                'color': pot.color,
                'icon': pot.icon,
                'auto_allocate_percentage': pot.auto_allocate_percentage
            }
        finally:
            session.close()
    
    def delete_pot(self, pot_id: int) -> bool:
        session = get_session()
        try:
            pot = session.query(Pot).filter(Pot.id == pot_id).first()
            if pot:
                pot.is_active = False
                session.commit()
                return True
            return False
        finally:
            session.close()
    
    def allocate_to_pot(self, pot_id: int, amount: float, 
                        source: str = 'manual', notes: str = None) -> Dict:
        session = get_session()
        try:
            pot = session.query(Pot).filter(Pot.id == pot_id).first()
            if not pot:
                raise ValueError(f"Pot with id {pot_id} not found")
            
            allocation = PotAllocation(
                pot_id=pot_id,
                amount=amount,
                source=source,
                notes=notes
            )
            session.add(allocation)
            
            pot.current_amount += amount
            
            session.commit()
            return {
                'id': allocation.id,
                'pot_id': pot_id,
                'amount': amount,
                'source': source,
                'pot_current_amount': pot.current_amount
            }
        finally:
            session.close()
    
    def auto_allocate_from_income(self, income_amount: float, 
                                   source_description: str = None) -> List[Dict]:
        session = get_session()
        try:
            pots = session.query(Pot).filter(
                and_(
                    Pot.is_active == True,
                    Pot.auto_allocate_percentage > 0
                )
            ).all()
            
            allocations = []
            for pot in pots:
                allocation_amount = income_amount * (pot.auto_allocate_percentage / 100)
                
                if allocation_amount > 0:
                    allocation = PotAllocation(
                        pot_id=pot.id,
                        amount=allocation_amount,
                        source='auto_income',
                        notes=f"Auto-allocated from income: {source_description or 'Unknown'}"
                    )
                    session.add(allocation)
                    pot.current_amount += allocation_amount
                    
                    allocations.append({
                        'pot_id': pot.id,
                        'pot_name': pot.name,
                        'amount': allocation_amount,
                        'percentage': pot.auto_allocate_percentage
                    })
            
            session.commit()
            return allocations
        finally:
            session.close()
    
    def assign_transaction_to_pot(self, transaction_id: int, pot_id: int) -> bool:
        session = get_session()
        try:
            transaction = session.query(Transaction).filter(
                Transaction.id == transaction_id
            ).first()
            
            if transaction:
                transaction.pot_id = pot_id
                session.commit()
                return True
            return False
        finally:
            session.close()
    
    def get_pot_transactions(self, pot_id: int) -> List[Dict]:
        session = get_session()
        try:
            transactions = session.query(Transaction).filter(
                Transaction.pot_id == pot_id
            ).order_by(Transaction.date.desc()).all()
            
            return [
                {
                    'id': t.id,
                    'date': t.date.isoformat(),
                    'description': t.description,
                    'amount': t.amount,
                    'type': t.transaction_type.value
                }
                for t in transactions
            ]
        finally:
            session.close()
    
    def get_pot_allocations(self, pot_id: int) -> List[Dict]:
        session = get_session()
        try:
            allocations = session.query(PotAllocation).filter(
                PotAllocation.pot_id == pot_id
            ).order_by(PotAllocation.allocated_at.desc()).all()
            
            return [
                {
                    'id': a.id,
                    'amount': a.amount,
                    'source': a.source,
                    'notes': a.notes,
                    'allocated_at': a.allocated_at.isoformat()
                }
                for a in allocations
            ]
        finally:
            session.close()
    
    def get_pot_projection(self, pot_id: int, months_ahead: int = 12) -> Dict:
        session = get_session()
        try:
            pot = session.query(Pot).filter(Pot.id == pot_id).first()
            if not pot:
                return None
            
            monthly_allocations = session.query(
                func.avg(PotAllocation.amount)
            ).filter(
                and_(
                    PotAllocation.pot_id == pot_id,
                    PotAllocation.allocated_at >= datetime.utcnow() - timedelta(days=90)
                )
            ).scalar() or 0
            
            monthly_spending = session.query(
                func.avg(Transaction.amount)
            ).filter(
                and_(
                    Transaction.pot_id == pot_id,
                    Transaction.transaction_type == TransactionType.EXPENSE,
                    Transaction.date >= datetime.utcnow() - timedelta(days=90)
                )
            ).scalar() or 0
            
            current = pot.current_amount
            projections = []
            
            for month in range(1, months_ahead + 1):
                projected = current + (monthly_allocations - monthly_spending) * month
                projections.append({
                    'month': month,
                    'projected_amount': max(0, projected),
                    'target_reached': projected >= pot.target_amount if pot.target_amount > 0 else False
                })
            
            months_to_target = None
            if pot.target_amount > 0 and monthly_allocations > monthly_spending:
                net_monthly = monthly_allocations - monthly_spending
                remaining = pot.target_amount - current
                if net_monthly > 0:
                    months_to_target = remaining / net_monthly
            
            return {
                'pot_id': pot_id,
                'pot_name': pot.name,
                'current_amount': current,
                'target_amount': pot.target_amount,
                'avg_monthly_allocation': float(monthly_allocations),
                'avg_monthly_spending': float(monthly_spending),
                'net_monthly_change': float(monthly_allocations - monthly_spending),
                'months_to_target': months_to_target,
                'projections': projections
            }
        finally:
            session.close()


pot_service = PotService()

from typing import Dict, List, Optional
from datetime import datetime, timedelta
from sqlalchemy import func, and_, extract, or_
from app.database import get_session
from app.models import TransactionView as Transaction, Category, Account, BankStatement, TransactionType


class AnalyticsService:
    def get_monthly_summary(self, year: int = None, month: int = None) -> Dict:
        session = get_session()
        try:
            # If no year/month specified, use the most recent transaction's month
            if year is None or month is None:
                latest_tx = session.query(func.max(Transaction.date)).scalar()
                if latest_tx:
                    year = latest_tx.year
                    month = latest_tx.month
                else:
                    now = datetime.now()
                    year = now.year
                    month = now.month
            
            start_date = datetime(year, month, 1)
            if month == 12:
                end_date = datetime(year + 1, 1, 1)
            else:
                end_date = datetime(year, month + 1, 1)
            
            transactions = session.query(Transaction).filter(
                and_(
                    Transaction.date >= start_date,
                    Transaction.date < end_date,
                    or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
                )
            ).all()
            
            total_income = sum(t.amount for t in transactions 
                              if t.transaction_type == TransactionType.INCOME)
            total_expenses = sum(t.amount for t in transactions 
                                if t.transaction_type == TransactionType.EXPENSE)
            
            category_spending = {}
            for t in transactions:
                if t.transaction_type == TransactionType.EXPENSE and t.category:
                    cat_name = t.category.name
                    if cat_name not in category_spending:
                        category_spending[cat_name] = {
                            'amount': 0,
                            'count': 0,
                            'color': t.category.color
                        }
                    category_spending[cat_name]['amount'] += t.amount
                    category_spending[cat_name]['count'] += 1
            
            return {
                'year': year,
                'month': month,
                'total_income': total_income,
                'total_expenses': total_expenses,
                'net_savings': total_income - total_expenses,
                'savings_rate': ((total_income - total_expenses) / total_income * 100) if total_income > 0 else 0,
                'transaction_count': len(transactions),
                'category_breakdown': category_spending
            }
        finally:
            session.close()
    
    def get_date_range_summary(self, start_date_str: str = None, end_date_str: str = None) -> Dict:
        """Get summary for a custom date range"""
        session = get_session()
        try:
            # Parse dates
            if start_date_str:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            else:
                # Default to start of current month
                now = datetime.now()
                start_date = datetime(now.year, now.month, 1)
            
            if end_date_str:
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1)  # Include end date
            else:
                end_date = datetime.now() + timedelta(days=1)
            
            transactions = session.query(Transaction).filter(
                and_(
                    Transaction.date >= start_date,
                    Transaction.date < end_date,
                    or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
                )
            ).all()
            
            total_income = sum(t.amount for t in transactions 
                              if t.transaction_type == TransactionType.INCOME)
            total_expenses = sum(t.amount for t in transactions 
                                if t.transaction_type == TransactionType.EXPENSE)
            
            category_spending = {}
            for t in transactions:
                if t.transaction_type == TransactionType.EXPENSE and t.category:
                    cat_name = t.category.name
                    if cat_name not in category_spending:
                        category_spending[cat_name] = {
                            'amount': 0,
                            'count': 0,
                            'color': t.category.color
                        }
                    category_spending[cat_name]['amount'] += t.amount
                    category_spending[cat_name]['count'] += 1
            
            # Get top merchants for this period
            merchant_totals = {}
            for t in transactions:
                if t.transaction_type == TransactionType.EXPENSE:
                    if t.description not in merchant_totals:
                        merchant_totals[t.description] = {'total': 0, 'count': 0}
                    merchant_totals[t.description]['total'] += t.amount
                    merchant_totals[t.description]['count'] += 1
            
            top_merchants = sorted(
                [{'merchant': k, 'total_spent': v['total'], 'transaction_count': v['count']} 
                 for k, v in merchant_totals.items()],
                key=lambda x: x['total_spent'],
                reverse=True
            )[:5]
            
            return {
                'start_date': start_date.strftime('%Y-%m-%d'),
                'end_date': (end_date - timedelta(days=1)).strftime('%Y-%m-%d'),
                'total_income': total_income,
                'total_expenses': total_expenses,
                'net': total_income - total_expenses,
                'savings_rate': ((total_income - total_expenses) / total_income * 100) if total_income > 0 else 0,
                'transaction_count': len(transactions),
                'category_breakdown': category_spending,
                'top_merchants': top_merchants
            }
        finally:
            session.close()
    
    def get_comprehensive_analytics(self, start_date_str: str = None, end_date_str: str = None) -> Dict:
        """Get comprehensive analytics for a date range including all charts and statistics"""
        session = get_session()
        try:
            # Parse dates
            if start_date_str:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            else:
                now = datetime.now()
                start_date = datetime(now.year, now.month, 1)
            
            if end_date_str:
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1)
            else:
                end_date = datetime.now() + timedelta(days=1)
            
            # Get all transactions in range
            transactions = session.query(Transaction).filter(
                and_(
                    Transaction.date >= start_date,
                    Transaction.date < end_date,
                    or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
                )
            ).all()
            
            # Basic totals
            income_txs = [t for t in transactions if t.transaction_type == TransactionType.INCOME]
            expense_txs = [t for t in transactions if t.transaction_type == TransactionType.EXPENSE]
            
            total_income = sum(t.amount for t in income_txs)
            total_expenses = sum(t.amount for t in expense_txs)
            net = total_income - total_expenses
            savings_rate = ((total_income - total_expenses) / total_income * 100) if total_income > 0 else 0
            
            # Days in period
            days_diff = max(1, (end_date - start_date).days)
            daily_avg = total_expenses / days_diff
            
            # Category breakdown
            category_spending = {}
            for t in expense_txs:
                cat_name = t.category.name if t.category else 'Uncategorized'
                cat_color = t.category.color if t.category else '#888888'
                if cat_name not in category_spending:
                    category_spending[cat_name] = {'amount': 0, 'count': 0, 'color': cat_color}
                category_spending[cat_name]['amount'] += t.amount
                category_spending[cat_name]['count'] += 1
            
            # Top merchants
            merchant_totals = {}
            for t in expense_txs:
                if t.description not in merchant_totals:
                    merchant_totals[t.description] = {'total': 0, 'count': 0}
                merchant_totals[t.description]['total'] += t.amount
                merchant_totals[t.description]['count'] += 1
            
            top_merchants = sorted(
                [{'merchant': k, 'total_spent': v['total'], 'transaction_count': v['count']} 
                 for k, v in merchant_totals.items()],
                key=lambda x: x['total_spent'],
                reverse=True
            )[:10]
            
            # Statistics
            expense_amounts = [t.amount for t in expense_txs]
            avg_expense = sum(expense_amounts) / len(expense_amounts) if expense_amounts else 0
            max_expense = max(expense_amounts) if expense_amounts else 0
            min_expense = min(expense_amounts) if expense_amounts else 0
            
            # Day of week analysis
            dow_spending = {i: 0 for i in range(7)}  # 0=Mon, 6=Sun
            for t in expense_txs:
                dow_spending[t.date.weekday()] += t.amount
            
            dow_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
            day_of_week = [{'day': dow_names[i], 'amount': dow_spending[i]} for i in range(7)]
            
            # Most expensive day
            daily_totals = {}
            for t in expense_txs:
                day_str = t.date.strftime('%Y-%m-%d')
                if day_str not in daily_totals:
                    daily_totals[day_str] = 0
                daily_totals[day_str] += t.amount
            
            most_expensive_day = None
            max_day_amount = 0
            for day, amount in daily_totals.items():
                if amount > max_day_amount:
                    max_day_amount = amount
                    most_expensive_day = day
            
            # Monthly trends within the period
            monthly_data = {}
            for t in transactions:
                key = f"{t.date.year}-{t.date.month:02d}"
                if key not in monthly_data:
                    monthly_data[key] = {'income': 0, 'expenses': 0}
                if t.transaction_type == TransactionType.INCOME:
                    monthly_data[key]['income'] += t.amount
                elif t.transaction_type == TransactionType.EXPENSE:
                    monthly_data[key]['expenses'] += t.amount
            
            monthly_trends = []
            for key in sorted(monthly_data.keys()):
                data = monthly_data[key]
                monthly_trends.append({
                    'period': key,
                    'income': data['income'],
                    'expenses': data['expenses'],
                    'net': data['income'] - data['expenses']
                })
            
            # Recurring transactions (appear 2+ times with similar amounts)
            recurring = session.query(
                Transaction.description,
                func.count(Transaction.id).label('occurrences'),
                func.avg(Transaction.amount).label('avg_amount'),
                func.max(Transaction.date).label('last_date')
            ).filter(
                and_(
                    Transaction.date >= start_date,
                    Transaction.date < end_date,
                    Transaction.transaction_type == TransactionType.EXPENSE,
                    or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
                )
            ).group_by(
                Transaction.description
            ).having(
                func.count(Transaction.id) >= 2
            ).order_by(
                func.count(Transaction.id).desc()
            ).limit(10).all()
            
            recurring_list = [
                {
                    'description': row.description,
                    'occurrences': row.occurrences,
                    'avg_amount': float(row.avg_amount),
                    'last_date': row.last_date.isoformat() if row.last_date else None
                }
                for row in recurring
            ]
            
            return {
                'period': {
                    'start_date': start_date.strftime('%Y-%m-%d'),
                    'end_date': (end_date - timedelta(days=1)).strftime('%Y-%m-%d'),
                    'days': days_diff
                },
                'summary': {
                    'total_income': total_income,
                    'total_expenses': total_expenses,
                    'net': net,
                    'savings_rate': round(savings_rate, 1),
                    'transaction_count': len(transactions),
                    'daily_average': round(daily_avg, 2)
                },
                'statistics': {
                    'avg_expense': round(avg_expense, 2),
                    'max_expense': max_expense,
                    'min_expense': min_expense,
                    'categories_used': len(category_spending),
                    'expense_per_day': round(daily_avg, 2),
                    'most_expensive_day': most_expensive_day,
                    'most_expensive_day_amount': max_day_amount
                },
                'category_breakdown': category_spending,
                'top_merchants': top_merchants,
                'monthly_trends': monthly_trends,
                'day_of_week': day_of_week,
                'recurring': recurring_list
            }
        finally:
            session.close()
    
    def get_spending_trends(self, months: int = 6) -> List[Dict]:
        """Get spending trends. Uses actual transaction date range, not relative to now."""
        session = get_session()
        try:
            # Find the actual date range of transactions
            date_range = session.query(
                func.min(Transaction.date).label('min_date'),
                func.max(Transaction.date).label('max_date')
            ).filter(
                or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
            ).first()
            
            if not date_range.max_date:
                return []
            
            # Calculate start date based on months parameter from the latest transaction
            end_date = date_range.max_date
            start_date = end_date - timedelta(days=months * 30)
            
            results = session.query(
                extract('year', Transaction.date).label('year'),
                extract('month', Transaction.date).label('month'),
                Transaction.transaction_type,
                func.sum(Transaction.amount).label('total')
            ).filter(
                and_(
                    Transaction.date >= start_date,
                    or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
                )
            ).group_by(
                extract('year', Transaction.date),
                extract('month', Transaction.date),
                Transaction.transaction_type
            ).all()
            
            monthly_data = {}
            for row in results:
                key = f"{int(row.year)}-{int(row.month):02d}"
                if key not in monthly_data:
                    monthly_data[key] = {'income': 0, 'expenses': 0}
                
                if row.transaction_type == TransactionType.INCOME:
                    monthly_data[key]['income'] = float(row.total)
                elif row.transaction_type == TransactionType.EXPENSE:
                    monthly_data[key]['expenses'] = float(row.total)
            
            trends = []
            for key in sorted(monthly_data.keys()):
                data = monthly_data[key]
                trends.append({
                    'period': key,
                    'income': data['income'],
                    'expenses': data['expenses'],
                    'net': data['income'] - data['expenses']
                })
            
            return trends
        finally:
            session.close()
    
    def get_category_trends(self, category_id: int, months: int = 6) -> List[Dict]:
        session = get_session()
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=months * 30)
            
            results = session.query(
                extract('year', Transaction.date).label('year'),
                extract('month', Transaction.date).label('month'),
                func.sum(Transaction.amount).label('total'),
                func.count(Transaction.id).label('count')
            ).filter(
                and_(
                    Transaction.category_id == category_id,
                    Transaction.date >= start_date
                )
            ).group_by(
                extract('year', Transaction.date),
                extract('month', Transaction.date)
            ).all()
            
            return [
                {
                    'period': f"{int(row.year)}-{int(row.month):02d}",
                    'total': float(row.total),
                    'count': row.count
                }
                for row in results
            ]
        finally:
            session.close()
    
    def get_top_merchants(self, limit: int = 10, days: int = 0, year: int = None, month: int = None) -> List[Dict]:
        """Get top merchants by spending. 
        - If year/month provided, filter to that month
        - If days > 0, filter relative to latest transaction date
        - If days=0 and no year/month, use the latest transaction's month
        """
        session = get_session()
        try:
            filters = [
                Transaction.transaction_type == TransactionType.EXPENSE,
                or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
            ]
            
            # Determine date filter
            if year and month:
                # Filter by specific month
                start_date = datetime(year, month, 1)
                if month == 12:
                    end_date = datetime(year + 1, 1, 1)
                else:
                    end_date = datetime(year, month + 1, 1)
                filters.append(Transaction.date >= start_date)
                filters.append(Transaction.date < end_date)
            elif days > 0:
                # Filter relative to latest transaction
                latest_tx = session.query(func.max(Transaction.date)).scalar()
                if latest_tx:
                    start_date = latest_tx - timedelta(days=days)
                    filters.append(Transaction.date >= start_date)
            else:
                # Default: use the latest transaction's month
                latest_tx = session.query(func.max(Transaction.date)).scalar()
                if latest_tx:
                    start_date = datetime(latest_tx.year, latest_tx.month, 1)
                    if latest_tx.month == 12:
                        end_date = datetime(latest_tx.year + 1, 1, 1)
                    else:
                        end_date = datetime(latest_tx.year, latest_tx.month + 1, 1)
                    filters.append(Transaction.date >= start_date)
                    filters.append(Transaction.date < end_date)
            
            results = session.query(
                Transaction.description,
                func.sum(Transaction.amount).label('total'),
                func.count(Transaction.id).label('count')
            ).filter(
                and_(*filters)
            ).group_by(
                Transaction.description
            ).order_by(
                func.sum(Transaction.amount).desc()
            ).limit(limit).all()
            
            return [
                {
                    'merchant': row.description,
                    'total_spent': float(row.total),
                    'transaction_count': row.count
                }
                for row in results
            ]
        finally:
            session.close()
    
    def get_recurring_transactions(self) -> List[Dict]:
        session = get_session()
        try:
            results = session.query(
                Transaction.description,
                func.count(Transaction.id).label('occurrences'),
                func.avg(Transaction.amount).label('avg_amount'),
                func.max(Transaction.date).label('last_date')
            ).filter(
                Transaction.transaction_type == TransactionType.EXPENSE
            ).group_by(
                Transaction.description
            ).having(
                func.count(Transaction.id) >= 2
            ).order_by(
                func.count(Transaction.id).desc()
            ).all()
            
            recurring = []
            for row in results:
                recurring.append({
                    'description': row.description,
                    'occurrences': row.occurrences,
                    'avg_amount': float(row.avg_amount),
                    'last_date': row.last_date.isoformat() if row.last_date else None,
                    'estimated_monthly': float(row.avg_amount) if row.occurrences >= 2 else 0
                })
            
            return recurring
        finally:
            session.close()
    
    def get_all_time_category_breakdown(self) -> Dict:
        """Get category breakdown for ALL transactions (not just current month), excluding internal transfers"""
        session = get_session()
        try:
            # Get all expense transactions with their categories (excluding internal transfers)
            transactions = session.query(Transaction).filter(
                and_(
                    Transaction.transaction_type == TransactionType.EXPENSE,
                    or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
                )
            ).all()
            
            category_spending = {}
            for t in transactions:
                if t.category:
                    cat_name = t.category.name
                    cat_id = t.category.id
                    if cat_name not in category_spending:
                        category_spending[cat_name] = {
                            'id': cat_id,
                            'amount': 0,
                            'count': 0,
                            'color': t.category.color
                        }
                    category_spending[cat_name]['amount'] += t.amount
                    category_spending[cat_name]['count'] += 1
            
            total_expenses = sum(c['amount'] for c in category_spending.values())
            
            return {
                'category_breakdown': category_spending,
                'total_expenses': total_expenses
            }
        finally:
            session.close()
    
    def get_current_balance(self) -> Dict:
        """Calculate current balance using oldest statement opening balance as starting point"""
        session = get_session()
        try:
            # Find the oldest statement with an opening balance
            oldest_statement = session.query(BankStatement).filter(
                BankStatement.opening_balance != None,
                BankStatement.processed == True
            ).order_by(BankStatement.statement_date.asc(), BankStatement.uploaded_at.asc()).first()
            
            starting_balance = 0.0
            starting_date = None
            
            if oldest_statement and oldest_statement.opening_balance:
                starting_balance = oldest_statement.opening_balance
                # Get the earliest transaction date from this statement
                earliest_tx = session.query(func.min(Transaction.date)).filter(
                    Transaction.statement_file == oldest_statement.filename
                ).scalar()
                starting_date = earliest_tx
            
            # Calculate all income and expenses
            all_income = session.query(func.sum(Transaction.amount)).filter(
                Transaction.transaction_type == TransactionType.INCOME
            ).scalar() or 0
            
            all_expenses = session.query(func.sum(Transaction.amount)).filter(
                Transaction.transaction_type == TransactionType.EXPENSE
            ).scalar() or 0
            
            current_balance = starting_balance + all_income - all_expenses
            
            return {
                'starting_balance': starting_balance,
                'starting_date': starting_date.isoformat() if starting_date else None,
                'total_income': float(all_income),
                'total_expenses': float(all_expenses),
                'current_balance': current_balance,
                'source': oldest_statement.filename if oldest_statement else None
            }
        finally:
            session.close()
    
    def get_balance_breakdown(self) -> Dict:
        """Get detailed breakdown of how the balance was calculated"""
        session = get_session()
        try:
            # Get all processed statements ordered by date
            statements = session.query(BankStatement).filter(
                BankStatement.processed == True
            ).order_by(BankStatement.statement_date.asc(), BankStatement.uploaded_at.asc()).all()
            
            statement_details = []
            for stmt in statements:
                # Get transactions for this statement
                stmt_transactions = session.query(Transaction).filter(
                    Transaction.statement_file == stmt.filename
                ).all()
                
                stmt_income = sum(t.amount for t in stmt_transactions 
                                 if t.transaction_type == TransactionType.INCOME)
                stmt_expenses = sum(t.amount for t in stmt_transactions 
                                   if t.transaction_type == TransactionType.EXPENSE)
                
                statement_details.append({
                    'filename': stmt.filename,
                    'statement_date': stmt.statement_date.isoformat() if stmt.statement_date else None,
                    'opening_balance': stmt.opening_balance,
                    'closing_balance': stmt.closing_balance,
                    'transaction_count': len(stmt_transactions),
                    'income': stmt_income,
                    'expenses': stmt_expenses,
                    'net': stmt_income - stmt_expenses
                })
            
            # Find the oldest statement with opening balance
            oldest_with_balance = None
            for stmt in statement_details:
                if stmt['opening_balance'] is not None:
                    oldest_with_balance = stmt
                    break
            
            # Calculate totals
            all_income = session.query(func.sum(Transaction.amount)).filter(
                Transaction.transaction_type == TransactionType.INCOME
            ).scalar() or 0
            
            all_expenses = session.query(func.sum(Transaction.amount)).filter(
                Transaction.transaction_type == TransactionType.EXPENSE
            ).scalar() or 0
            
            income_count = session.query(func.count(Transaction.id)).filter(
                Transaction.transaction_type == TransactionType.INCOME
            ).scalar() or 0
            
            expense_count = session.query(func.count(Transaction.id)).filter(
                Transaction.transaction_type == TransactionType.EXPENSE
            ).scalar() or 0
            
            starting_balance = oldest_with_balance['opening_balance'] if oldest_with_balance else 0.0
            calculated_balance = starting_balance + float(all_income) - float(all_expenses)
            
            # Get the most recent closing balance for comparison
            newest_statement = statements[-1] if statements else None
            expected_balance = newest_statement.closing_balance if newest_statement else None
            
            discrepancy = None
            if expected_balance is not None:
                discrepancy = round(calculated_balance - expected_balance, 2)
            
            return {
                'calculation': {
                    'starting_balance': starting_balance,
                    'starting_balance_source': oldest_with_balance['filename'] if oldest_with_balance else None,
                    'starting_balance_date': oldest_with_balance['statement_date'] if oldest_with_balance else None,
                    'total_income': float(all_income),
                    'income_transaction_count': income_count,
                    'total_expenses': float(all_expenses),
                    'expense_transaction_count': expense_count,
                    'calculated_balance': calculated_balance,
                    'formula': f"{starting_balance} + {float(all_income)} - {float(all_expenses)} = {calculated_balance}"
                },
                'verification': {
                    'newest_statement': newest_statement.filename if newest_statement else None,
                    'newest_statement_date': newest_statement.statement_date.isoformat() if newest_statement and newest_statement.statement_date else None,
                    'expected_closing_balance': expected_balance,
                    'calculated_balance': calculated_balance,
                    'discrepancy': discrepancy,
                    'matches': discrepancy == 0 if discrepancy is not None else None
                },
                'statements': statement_details
            }
        finally:
            session.close()
    
    def get_dashboard_stats(self) -> Dict:
        session = get_session()
        try:
            # Get the most recent transaction date to determine the "current" period
            latest_tx = session.query(func.max(Transaction.date)).scalar()
            
            if latest_tx:
                # Use the month of the latest transaction as "current month"
                current_year = latest_tx.year
                current_month = latest_tx.month
            else:
                now = datetime.now()
                current_year = now.year
                current_month = now.month
            
            start_of_month = datetime(current_year, current_month, 1)
            if current_month == 12:
                end_of_month = datetime(current_year + 1, 1, 1)
            else:
                end_of_month = datetime(current_year, current_month + 1, 1)
            
            # Exclude internal transfers from monthly stats
            month_transactions = session.query(Transaction).filter(
                and_(
                    Transaction.date >= start_of_month,
                    Transaction.date < end_of_month,
                    or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
                )
            ).all()
            
            month_income = sum(t.amount for t in month_transactions 
                              if t.transaction_type == TransactionType.INCOME)
            month_expenses = sum(t.amount for t in month_transactions 
                                if t.transaction_type == TransactionType.EXPENSE)
            
            # Previous month
            if current_month == 1:
                prev_start = datetime(current_year - 1, 12, 1)
                prev_end = start_of_month
            else:
                prev_start = datetime(current_year, current_month - 1, 1)
                prev_end = start_of_month
            
            prev_expenses = session.query(func.sum(Transaction.amount)).filter(
                and_(
                    Transaction.date >= prev_start,
                    Transaction.date < prev_end,
                    Transaction.transaction_type == TransactionType.EXPENSE,
                    or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
                )
            ).scalar() or 0
            
            # All-time totals (excluding internal transfers)
            all_transactions = session.query(Transaction).filter(
                or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
            ).all()
            total_income = sum(t.amount for t in all_transactions 
                              if t.transaction_type == TransactionType.INCOME)
            total_expenses = sum(t.amount for t in all_transactions 
                                if t.transaction_type == TransactionType.EXPENSE)
            
            uncategorized = session.query(func.count(Transaction.id)).outerjoin(
                Category, Transaction.category_id == Category.id
            ).filter(
                or_(Transaction.category_id.is_(None), Category.name == 'Uncategorized')
            ).scalar() or 0
            
            # Get account totals (non-main accounts)
            from app.models import AccountType
            accounts = session.query(Account).filter(Account.is_active == True).all()
            total_in_sub_accounts = 0.0
            
            for account in accounts:
                if account.account_type != AccountType.MAIN:
                    # Calculate sub-account balance
                    acc_income = session.query(func.sum(Transaction.amount)).filter(
                        and_(
                            Transaction.account_id == account.id,
                            Transaction.transaction_type == TransactionType.INCOME
                        )
                    ).scalar() or 0
                    
                    acc_expenses = session.query(func.sum(Transaction.amount)).filter(
                        and_(
                            Transaction.account_id == account.id,
                            Transaction.transaction_type == TransactionType.EXPENSE
                        )
                    ).scalar() or 0
                    
                    total_in_sub_accounts += float(acc_income) - float(acc_expenses)
            
            # Get current balance
            balance_info = self.get_current_balance()
            
            return {
                'current_month': {
                    'year': current_year,
                    'month': current_month,
                    'income': month_income,
                    'expenses': month_expenses,
                    'net': month_income - month_expenses,
                    'transaction_count': len(month_transactions)
                },
                'all_time': {
                    'income': total_income,
                    'expenses': total_expenses,
                    'net': total_income - total_expenses,
                    'transaction_count': len(all_transactions)
                },
                'balance': balance_info,
                'comparison': {
                    'prev_month_expenses': float(prev_expenses),
                    'expense_change': ((month_expenses - prev_expenses) / prev_expenses * 100) if prev_expenses > 0 else 0
                },
                'totals': {
                    'all_transactions': len(all_transactions),
                    'uncategorized': uncategorized,
                    'active_accounts': len(accounts),
                    'total_in_sub_accounts': total_in_sub_accounts
                }
            }
        finally:
            session.close()


analytics_service = AnalyticsService()

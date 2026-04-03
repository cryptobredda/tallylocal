"""
Asset Service - Handles asset management, holdings, transactions, and portfolio analytics
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy import and_, func
from app.database import get_session
from app.models import (
    Asset, AssetHolding, AssetTransaction, AssetPriceCache, 
    PortfolioSnapshot, AssetType, AssetTransactionType
)
from app.price_service import price_service, TOP_CRYPTOCURRENCIES, PRECIOUS_METALS


class AssetService:
    
    # ==================== ASSET CRUD ====================
    
    def get_all_assets(self) -> List[Dict]:
        """Get all assets with their current holdings and prices"""
        session = get_session()
        try:
            assets = session.query(Asset).all()
            result = []
            
            for asset in assets:
                # Get holding
                holding = session.query(AssetHolding).filter(
                    AssetHolding.asset_id == asset.id
                ).first()
                
                # Get latest price
                price_cache = session.query(AssetPriceCache).filter(
                    AssetPriceCache.asset_id == asset.id
                ).order_by(AssetPriceCache.price_date.desc()).first()
                
                quantity = holding.quantity if holding else 0
                cost_basis = holding.total_cost_basis if holding else 0
                avg_price = holding.average_buy_price if holding else 0
                
                current_price = price_cache.price_gbp if price_cache else avg_price
                current_value = quantity * current_price if current_price else 0
                gain_loss = current_value - cost_basis if cost_basis > 0 else 0
                gain_loss_pct = (gain_loss / cost_basis * 100) if cost_basis > 0 else 0
                
                result.append({
                    'id': asset.id,
                    'symbol': asset.symbol,
                    'name': asset.name,
                    'asset_type': asset.asset_type.value,
                    'is_tracked': asset.is_tracked,
                    'color': asset.color,
                    'quantity': quantity,
                    'cost_basis': cost_basis,
                    'average_buy_price': avg_price,
                    'current_price': current_price,
                    'current_value': current_value,
                    'gain_loss': gain_loss,
                    'gain_loss_pct': round(gain_loss_pct, 2),
                    'change_24h': price_cache.change_24h if price_cache else None,
                    'change_7d': price_cache.change_7d if price_cache else None,
                    'last_updated': price_cache.fetched_at.isoformat() if price_cache else None
                })
            
            return result
        finally:
            session.close()
    
    def get_asset(self, asset_id: int) -> Optional[Dict]:
        """Get single asset with full details"""
        session = get_session()
        try:
            asset = session.query(Asset).filter(Asset.id == asset_id).first()
            if not asset:
                return None
            
            holding = session.query(AssetHolding).filter(
                AssetHolding.asset_id == asset_id
            ).first()
            
            price_cache = session.query(AssetPriceCache).filter(
                AssetPriceCache.asset_id == asset_id
            ).order_by(AssetPriceCache.price_date.desc()).first()
            
            transactions = session.query(AssetTransaction).filter(
                AssetTransaction.asset_id == asset_id
            ).order_by(AssetTransaction.transaction_date.desc()).all()
            
            quantity = holding.quantity if holding else 0
            cost_basis = holding.total_cost_basis if holding else 0
            avg_price = holding.average_buy_price if holding else 0
            current_price = price_cache.price_gbp if price_cache else avg_price
            current_value = quantity * current_price if current_price else 0
            gain_loss = current_value - cost_basis if cost_basis > 0 else 0
            gain_loss_pct = (gain_loss / cost_basis * 100) if cost_basis > 0 else 0
            
            return {
                'id': asset.id,
                'symbol': asset.symbol,
                'name': asset.name,
                'asset_type': asset.asset_type.value,
                'api_id': asset.api_id,
                'is_tracked': asset.is_tracked,
                'color': asset.color,
                'quantity': quantity,
                'cost_basis': cost_basis,
                'average_buy_price': avg_price,
                'current_price': current_price,
                'current_value': current_value,
                'gain_loss': gain_loss,
                'gain_loss_pct': round(gain_loss_pct, 2),
                'change_24h': price_cache.change_24h if price_cache else None,
                'change_7d': price_cache.change_7d if price_cache else None,
                'last_updated': price_cache.fetched_at.isoformat() if price_cache else None,
                'transactions': [
                    {
                        'id': t.id,
                        'type': t.transaction_type.value,
                        'quantity': t.quantity,
                        'price_per_unit': t.price_per_unit,
                        'total_value': t.total_value,
                        'fee': t.fee,
                        'date': t.transaction_date.isoformat(),
                        'notes': t.notes
                    }
                    for t in transactions
                ]
            }
        finally:
            session.close()
    
    def create_asset(self, symbol: str, name: str, asset_type: str, 
                     is_tracked: bool = True, api_id: str = None, 
                     color: str = None) -> Dict:
        """Create a new asset"""
        session = get_session()
        try:
            # Check if already exists
            existing = session.query(Asset).filter(Asset.symbol == symbol.upper()).first()
            if existing:
                return {'error': f'Asset {symbol} already exists', 'id': existing.id}
            
            # Determine asset type enum
            type_enum = AssetType.OTHER
            if asset_type == 'crypto':
                type_enum = AssetType.CRYPTO
            elif asset_type == 'precious_metal':
                type_enum = AssetType.PRECIOUS_METAL
            
            # Auto-set API ID and color if known asset
            if not api_id:
                if symbol.upper() in TOP_CRYPTOCURRENCIES:
                    api_id = TOP_CRYPTOCURRENCIES[symbol.upper()]['api_id']
                    color = color or TOP_CRYPTOCURRENCIES[symbol.upper()]['color']
                elif symbol.upper() in PRECIOUS_METALS:
                    api_id = PRECIOUS_METALS[symbol.upper()]['api_id']
                    color = color or PRECIOUS_METALS[symbol.upper()]['color']
            
            asset = Asset(
                symbol=symbol.upper(),
                name=name,
                asset_type=type_enum,
                api_id=api_id,
                is_tracked=is_tracked,
                color=color or '#f59e0b'
            )
            session.add(asset)
            session.commit()
            
            # Capture values before session closes
            asset_id = asset.id
            asset_symbol = asset.symbol
            asset_name = asset.name
            asset_type_val = asset.asset_type.value
            asset_tracked = asset.is_tracked
            
            # Create empty holding
            holding = AssetHolding(
                asset_id=asset_id,
                quantity=0,
                total_cost_basis=0
            )
            session.add(holding)
            session.commit()
            
            # Fetch initial price if tracked
            if is_tracked:
                price_service.update_price_cache([asset_id])
            
            return {
                'id': asset_id,
                'symbol': asset_symbol,
                'name': asset_name,
                'asset_type': asset_type_val,
                'is_tracked': asset_tracked
            }
        except Exception as e:
            session.rollback()
            return {'error': str(e)}
        finally:
            session.close()
    
    def delete_asset(self, asset_id: int) -> Dict:
        """Delete an asset and all related data"""
        session = get_session()
        try:
            asset = session.query(Asset).filter(Asset.id == asset_id).first()
            if not asset:
                return {'error': 'Asset not found'}
            
            session.delete(asset)
            session.commit()
            return {'success': True, 'message': f'Asset {asset.symbol} deleted'}
        except Exception as e:
            session.rollback()
            return {'error': str(e)}
        finally:
            session.close()
    
    # ==================== TRANSACTION CRUD ====================
    
    def add_transaction(self, asset_id: int, transaction_type: str, quantity: float,
                        price_per_unit: float, transaction_date: datetime,
                        fee: float = 0, notes: str = None, 
                        auto_fetch_price: bool = False) -> Dict:
        """Add a new asset transaction"""
        session = get_session()
        try:
            asset = session.query(Asset).filter(Asset.id == asset_id).first()
            if not asset:
                return {'error': 'Asset not found'}
            
            # Auto-fetch historical price if requested
            if auto_fetch_price and asset.is_tracked:
                if asset.asset_type == AssetType.CRYPTO:
                    fetched_price = price_service.get_crypto_historical_price(
                        asset.symbol, transaction_date
                    )
                    if fetched_price:
                        price_per_unit = fetched_price
            
            # Determine transaction type
            tx_type = AssetTransactionType.BUY
            if transaction_type == 'sell':
                tx_type = AssetTransactionType.SELL
            elif transaction_type == 'transfer':
                tx_type = AssetTransactionType.TRANSFER
            elif transaction_type == 'other':
                tx_type = AssetTransactionType.OTHER
            
            total_value = quantity * price_per_unit
            
            transaction = AssetTransaction(
                asset_id=asset_id,
                transaction_type=tx_type,
                quantity=quantity,
                price_per_unit=price_per_unit,
                total_value=total_value,
                fee=fee,
                transaction_date=transaction_date,
                notes=notes,
                is_historical_price=auto_fetch_price
            )
            session.add(transaction)
            
            # Update holding
            self._update_holding(session, asset_id)
            
            session.commit()
            
            return {
                'id': transaction.id,
                'asset_id': asset_id,
                'type': tx_type.value,
                'quantity': quantity,
                'price_per_unit': price_per_unit,
                'total_value': total_value
            }
        except Exception as e:
            session.rollback()
            return {'error': str(e)}
        finally:
            session.close()
    
    def update_transaction(self, transaction_id: int, **kwargs) -> Dict:
        """Update an existing transaction"""
        session = get_session()
        try:
            tx = session.query(AssetTransaction).filter(
                AssetTransaction.id == transaction_id
            ).first()
            
            if not tx:
                return {'error': 'Transaction not found'}
            
            if 'quantity' in kwargs:
                tx.quantity = kwargs['quantity']
            if 'price_per_unit' in kwargs:
                tx.price_per_unit = kwargs['price_per_unit']
            if 'transaction_date' in kwargs:
                tx.transaction_date = kwargs['transaction_date']
            if 'fee' in kwargs:
                tx.fee = kwargs['fee']
            if 'notes' in kwargs:
                tx.notes = kwargs['notes']
            if 'transaction_type' in kwargs:
                tx.transaction_type = AssetTransactionType(kwargs['transaction_type'])
            
            tx.total_value = tx.quantity * tx.price_per_unit
            
            # Update holding
            self._update_holding(session, tx.asset_id)
            
            session.commit()
            return {'success': True, 'id': transaction_id}
        except Exception as e:
            session.rollback()
            return {'error': str(e)}
        finally:
            session.close()
    
    def delete_transaction(self, transaction_id: int) -> Dict:
        """Delete a transaction and update holdings"""
        session = get_session()
        try:
            tx = session.query(AssetTransaction).filter(
                AssetTransaction.id == transaction_id
            ).first()
            
            if not tx:
                return {'error': 'Transaction not found'}
            
            asset_id = tx.asset_id
            session.delete(tx)
            
            # Update holding
            self._update_holding(session, asset_id)
            
            session.commit()
            return {'success': True}
        except Exception as e:
            session.rollback()
            return {'error': str(e)}
        finally:
            session.close()
    
    def _update_holding(self, session, asset_id: int):
        """Recalculate holding based on all transactions"""
        holding = session.query(AssetHolding).filter(
            AssetHolding.asset_id == asset_id
        ).first()
        
        if not holding:
            holding = AssetHolding(asset_id=asset_id)
            session.add(holding)
        
        transactions = session.query(AssetTransaction).filter(
            AssetTransaction.asset_id == asset_id
        ).all()
        
        total_quantity = 0
        total_cost = 0
        buy_quantity = 0
        buy_cost = 0
        
        for tx in transactions:
            if tx.transaction_type == AssetTransactionType.BUY:
                total_quantity += tx.quantity
                total_cost += tx.total_value + tx.fee
                buy_quantity += tx.quantity
                buy_cost += tx.total_value + tx.fee
            elif tx.transaction_type == AssetTransactionType.SELL:
                total_quantity -= tx.quantity
                # For sells, we reduce cost basis proportionally
                if buy_quantity > 0:
                    cost_per_unit = buy_cost / buy_quantity
                    total_cost -= tx.quantity * cost_per_unit
            elif tx.transaction_type == AssetTransactionType.TRANSFER:
                # Transfers don't affect cost basis, just quantity (in or out)
                total_quantity += tx.quantity  # Can be negative for outgoing
        
        holding.quantity = max(0, total_quantity)
        holding.total_cost_basis = max(0, total_cost)
        holding.average_buy_price = (buy_cost / buy_quantity) if buy_quantity > 0 else 0
        holding.updated_at = datetime.now()
    
    # ==================== PORTFOLIO ANALYTICS ====================
    
    def get_portfolio_summary(self) -> Dict:
        """Get overall portfolio summary"""
        session = get_session()
        try:
            assets = self.get_all_assets()
            
            total_value = sum(a['current_value'] for a in assets)
            total_cost_basis = sum(a['cost_basis'] for a in assets)
            total_gain_loss = total_value - total_cost_basis
            total_gain_loss_pct = (total_gain_loss / total_cost_basis * 100) if total_cost_basis > 0 else 0
            
            # Group by asset type
            by_type = {}
            for asset in assets:
                asset_type = asset['asset_type']
                if asset_type not in by_type:
                    by_type[asset_type] = {'value': 0, 'cost': 0, 'count': 0}
                by_type[asset_type]['value'] += asset['current_value']
                by_type[asset_type]['cost'] += asset['cost_basis']
                by_type[asset_type]['count'] += 1
            
            # Best/worst performers
            performers = sorted(
                [a for a in assets if a['cost_basis'] > 0],
                key=lambda x: x['gain_loss_pct'],
                reverse=True
            )
            
            from app.wallet_service import wallet_service
            wallet_summary = wallet_service.get_value_summary()
            wallet_value = wallet_summary.get('total_value', 0) if wallet_summary else 0
            return {
                'total_value': round(total_value, 2),
                'asset_value': round(total_value, 2),
                'wallet_value': round(wallet_value, 2),
                'combined_value': round(total_value + wallet_value, 2),
                'total_cost_basis': round(total_cost_basis, 2),
                'total_gain_loss': round(total_gain_loss, 2),
                'total_gain_loss_pct': round(total_gain_loss_pct, 2),
                'holdings_count': len([a for a in assets if a['quantity'] > 0]),
                'assets_count': len(assets),
                'by_type': by_type,
                'best_performer': performers[0] if performers else None,
                'worst_performer': performers[-1] if performers else None,
                'holdings': assets,
                'wallet_breakdown': wallet_summary.get('wallets', []) if wallet_summary else []
            }
        finally:
            session.close()
    
    def get_portfolio_allocation(self) -> List[Dict]:
        """Get portfolio allocation for pie chart"""
        assets = self.get_all_assets()
        total_value = sum(a['current_value'] for a in assets)
        
        if total_value == 0:
            return []
        
        return [
            {
                'symbol': a['symbol'],
                'name': a['name'],
                'value': a['current_value'],
                'percentage': round((a['current_value'] / total_value) * 100, 2),
                'color': a['color']
            }
            for a in assets if a['current_value'] > 0
        ]
    
    def get_portfolio_history(self, days: int = 90) -> List[Dict]:
        """Get portfolio value history calculated from transactions (weekly intervals)"""
        session = get_session()
        try:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start_date = today - timedelta(days=days)
            
            # Get all transactions ordered by date
            transactions = session.query(AssetTransaction).order_by(
                AssetTransaction.transaction_date
            ).all()
            
            if not transactions:
                return []
            
            # Get current prices for all assets
            assets = session.query(Asset).all()
            current_prices = {}
            for asset in assets:
                price_cache = session.query(AssetPriceCache).filter(
                    AssetPriceCache.asset_id == asset.id
                ).order_by(AssetPriceCache.price_date.desc()).first()
                current_prices[asset.id] = price_cache.price_gbp if price_cache else 0
            
            # Pre-calculate holdings at start_date by processing all transactions before it
            holdings = {}  # asset_id -> {quantity, cost_basis}
            processed_tx_ids = set()
            
            for tx in transactions:
                tx_date = tx.transaction_date.replace(hour=0, minute=0, second=0, microsecond=0)
                if tx_date < start_date:
                    processed_tx_ids.add(tx.id)
                    if tx.asset_id not in holdings:
                        holdings[tx.asset_id] = {'quantity': 0, 'cost_basis': 0}
                    
                    if tx.transaction_type == AssetTransactionType.BUY:
                        holdings[tx.asset_id]['quantity'] += tx.quantity
                        holdings[tx.asset_id]['cost_basis'] += tx.total_value + (tx.fee or 0)
                    elif tx.transaction_type == AssetTransactionType.SELL:
                        old_qty = holdings[tx.asset_id]['quantity']
                        if old_qty > 0:
                            ratio = tx.quantity / old_qty
                            holdings[tx.asset_id]['cost_basis'] *= (1 - ratio)
                        holdings[tx.asset_id]['quantity'] -= tx.quantity
            
            # Generate weekly data points from start_date to today
            history = []
            checkpoints = []
            current_date = start_date
            while current_date <= today:
                checkpoints.append(current_date)
                current_date += timedelta(days=7)
            
            # Always include today
            if not checkpoints or checkpoints[-1] != today:
                checkpoints.append(today)
            
            for checkpoint in checkpoints:
                # Process transactions up to this checkpoint that haven't been processed
                for tx in transactions:
                    tx_date = tx.transaction_date.replace(hour=0, minute=0, second=0, microsecond=0)
                    if tx_date <= checkpoint and tx.id not in processed_tx_ids:
                        processed_tx_ids.add(tx.id)
                        
                        if tx.asset_id not in holdings:
                            holdings[tx.asset_id] = {'quantity': 0, 'cost_basis': 0}
                        
                        if tx.transaction_type == AssetTransactionType.BUY:
                            holdings[tx.asset_id]['quantity'] += tx.quantity
                            holdings[tx.asset_id]['cost_basis'] += tx.total_value + (tx.fee or 0)
                        elif tx.transaction_type == AssetTransactionType.SELL:
                            old_qty = holdings[tx.asset_id]['quantity']
                            if old_qty > 0:
                                ratio = tx.quantity / old_qty
                                holdings[tx.asset_id]['cost_basis'] *= (1 - ratio)
                            holdings[tx.asset_id]['quantity'] -= tx.quantity
                
                # Calculate portfolio value at this checkpoint using current prices
                total_value = 0
                total_cost = 0
                for asset_id, data in holdings.items():
                    if data['quantity'] > 0:
                        price = current_prices.get(asset_id, 0)
                        total_value += data['quantity'] * price
                        total_cost += data['cost_basis']
                
                if total_cost > 0 or total_value > 0:
                    gain_loss = total_value - total_cost
                    gain_loss_pct = (gain_loss / total_cost * 100) if total_cost > 0 else 0
                    
                    history.append({
                        'date': checkpoint.isoformat(),
                        'total_value': round(total_value, 2),
                        'cost_basis': round(total_cost, 2),
                        'gain_loss': round(gain_loss, 2),
                        'gain_loss_pct': round(gain_loss_pct, 2)
                    })
            
            return history
        finally:
            session.close()
    
    def take_portfolio_snapshot(self) -> Dict:
        """Take a snapshot of current portfolio value (upsert for today)"""
        session = get_session()
        try:
            summary = self.get_portfolio_summary()
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Check if snapshot exists for today
            existing = session.query(PortfolioSnapshot).filter(
                PortfolioSnapshot.snapshot_date == today
            ).first()
            
            if existing:
                # Update existing
                existing.total_value_gbp = summary['total_value']
                existing.total_cost_basis = summary['total_cost_basis']
                existing.total_gain_loss = summary['total_gain_loss']
                existing.total_gain_loss_pct = summary['total_gain_loss_pct']
                existing.holdings_breakdown = json.dumps(summary['holdings'])
                session.commit()
                return {'success': True, 'snapshot_id': existing.id, 'updated': True}
            else:
                # Create new
                snapshot = PortfolioSnapshot(
                    snapshot_date=today,
                    total_value_gbp=summary['total_value'],
                    total_cost_basis=summary['total_cost_basis'],
                    total_gain_loss=summary['total_gain_loss'],
                    total_gain_loss_pct=summary['total_gain_loss_pct'],
                    holdings_breakdown=json.dumps(summary['holdings'])
                )
                session.add(snapshot)
                session.commit()
                return {'success': True, 'snapshot_id': snapshot.id, 'created': True}
        except Exception as e:
            session.rollback()
            return {'error': str(e)}
        finally:
            session.close()
    
    def get_transaction_history(self, asset_id: int = None, 
                                 limit: int = 50) -> List[Dict]:
        """Get transaction history, optionally filtered by asset"""
        session = get_session()
        try:
            query = session.query(AssetTransaction).join(Asset)
            
            if asset_id:
                query = query.filter(AssetTransaction.asset_id == asset_id)
            
            transactions = query.order_by(
                AssetTransaction.transaction_date.desc()
            ).limit(limit).all()
            
            return [
                {
                    'id': t.id,
                    'asset_id': t.asset_id,
                    'asset_symbol': t.asset.symbol,
                    'asset_name': t.asset.name,
                    'type': t.transaction_type.value,
                    'quantity': t.quantity,
                    'price_per_unit': t.price_per_unit,
                    'total_value': t.total_value,
                    'fee': t.fee,
                    'date': t.transaction_date.isoformat(),
                    'notes': t.notes
                }
                for t in transactions
            ]
        finally:
            session.close()
    
    def get_performance_metrics(self, asset_id: int = None) -> Dict:
        """Get detailed performance metrics"""
        session = get_session()
        try:
            if asset_id:
                assets = [self.get_asset(asset_id)] if self.get_asset(asset_id) else []
            else:
                assets = self.get_all_assets()
            
            total_invested = sum(a['cost_basis'] for a in assets)
            total_value = sum(a['current_value'] for a in assets)
            total_gain = total_value - total_invested
            
            # Calculate ROI
            roi = (total_gain / total_invested * 100) if total_invested > 0 else 0
            
            # Get first transaction date for annualized return
            first_tx = session.query(AssetTransaction).order_by(
                AssetTransaction.transaction_date
            ).first()
            
            annualized_return = None
            if first_tx and total_invested > 0:
                days_held = (datetime.now() - first_tx.transaction_date).days
                if days_held > 0:
                    years = days_held / 365.25
                    if years > 0:
                        annualized_return = ((total_value / total_invested) ** (1 / years) - 1) * 100
            
            # Realized vs unrealized
            realized_gains = 0
            for asset_data in assets:
                if asset_data.get('transactions'):
                    sells = [t for t in asset_data['transactions'] if t['type'] == 'sell']
                    for sell in sells:
                        avg_cost = asset_data.get('average_buy_price', 0)
                        realized_gains += (sell['price_per_unit'] - avg_cost) * sell['quantity']
            
            unrealized_gains = total_gain - realized_gains
            
            return {
                'total_invested': round(total_invested, 2),
                'current_value': round(total_value, 2),
                'total_gain_loss': round(total_gain, 2),
                'roi_pct': round(roi, 2),
                'annualized_return': round(annualized_return, 2) if annualized_return else None,
                'realized_gains': round(realized_gains, 2),
                'unrealized_gains': round(unrealized_gains, 2)
            }
        finally:
            session.close()


# Singleton instance
asset_service = AssetService()

"""
Portfolio Service - Handles historical price fetching and portfolio snapshot calculations

Key principles:
1. ALL calculations are pre-computed and stored in database
2. Graph display only reads data, never calculates
3. Historical prices are fetched from transaction date to today
4. Portfolio snapshots store daily pre-calculated values
"""

import json
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy import and_, func
from app.database import get_session
from app.models import (
    Asset, AssetTransaction, AssetPriceHistory, PortfolioSnapshot,
    AssetTransactionType, AssetType
)


class PortfolioService:
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'FinanceTracker/1.0'
        })
    
    # ==================== HISTORICAL PRICE FETCHING ====================
    
    def fetch_historical_prices_for_asset(self, asset_id: int, asset_symbol: str, 
                                           asset_api_id: str, asset_type: AssetType,
                                           start_date: datetime, end_date: datetime) -> int:
        """
        Fetch historical prices for an asset from start_date to end_date.
        Only fetches prices for dates we don't already have.
        Returns number of prices fetched.
        """
        db_session = get_session()
        try:
            # Normalize dates
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = end_date.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Get existing price dates for this asset
            existing_dates = set(
                row[0].date() for row in db_session.query(AssetPriceHistory.date).filter(
                    AssetPriceHistory.asset_id == asset_id,
                    AssetPriceHistory.date >= start_date,
                    AssetPriceHistory.date <= end_date
                ).all()
            )
            
            # Determine which dates we need to fetch
            dates_needed = []
            current = start_date
            while current <= end_date:
                if current.date() not in existing_dates:
                    dates_needed.append(current)
                current += timedelta(days=1)
            
            if not dates_needed:
                return 0
            
            # Fetch prices based on asset type
            if asset_type == AssetType.CRYPTO:
                prices = self._fetch_crypto_history(asset_api_id, asset_symbol, dates_needed[0], dates_needed[-1])
            elif asset_type == AssetType.PRECIOUS_METAL:
                prices = self._fetch_metal_history_simple(asset_symbol, dates_needed[0], dates_needed[-1])
            else:
                return 0  # Untracked assets don't have historical prices
            
            # Store prices in database
            count = 0
            for date, price_data in prices.items():
                date_dt = datetime.strptime(date, '%Y-%m-%d') if isinstance(date, str) else date
                if date_dt.date() not in existing_dates:
                    price_entry = AssetPriceHistory(
                        asset_id=asset_id,
                        date=date_dt,
                        price_gbp=price_data.get('gbp', 0),
                        price_usd=price_data.get('usd', 0)
                    )
                    db_session.add(price_entry)
                    count += 1
            
            db_session.commit()
            return count
            
        except Exception as e:
            db_session.rollback()
            print(f"Error fetching historical prices: {e}")
            return 0
        finally:
            db_session.close()
    
    def _fetch_crypto_history(self, asset_api_id: str, asset_symbol: str, start_date: datetime, end_date: datetime) -> Dict:
        """Fetch historical crypto prices from CoinGecko"""
        try:
            if not asset_api_id:
                return {}
            
            # CoinGecko free tier: use market_chart endpoint with days parameter
            # Calculate days from start to end
            days = (end_date - start_date).days + 1
            if days > 365:
                days = 365  # CoinGecko free tier limit
            
            url = f"https://api.coingecko.com/api/v3/coins/{asset_api_id}/market_chart"
            params = {
                'vs_currency': 'gbp',
                'days': days,
                'interval': 'daily'
            }
            
            import time
            time.sleep(1)  # Rate limiting
            
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            # Parse prices - CoinGecko returns [timestamp_ms, price] pairs
            prices = {}
            for ts_ms, price in data.get('prices', []):
                date = datetime.fromtimestamp(ts_ms / 1000).strftime('%Y-%m-%d')
                if date not in prices:  # Take first price of each day
                    prices[date] = {'gbp': price, 'usd': None}
            
            return prices
            
        except Exception as e:
            print(f"Error fetching crypto history for {asset_symbol}: {e}")
            return {}
    
    def _fetch_metal_history_simple(self, asset_symbol: str, start_date: datetime, end_date: datetime) -> Dict:
        """
        Fetch historical precious metal prices.
        Note: Free APIs for historical metal prices are limited.
        We'll use current price as approximation for recent dates.
        """
        try:
            # metals.live doesn't provide historical data for free
            # For now, we'll fetch current price and use it for recent dates
            url = "https://api.metals.live/v1/spot"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            # Get GBP rate
            gbp_rate = self._get_usd_to_gbp_rate()
            
            symbol_map = {'gold': 'XAU', 'silver': 'XAG', 'platinum': 'XPT', 'palladium': 'XPD'}
            current_price_usd = None
            
            for item in data:
                metal = item.get('metal', '').lower()
                if symbol_map.get(metal) == asset_symbol:
                    current_price_usd = item.get('price', 0)
                    break
            
            if not current_price_usd:
                return {}
            
            # Generate prices for each date (using current price as approximation)
            prices = {}
            current = start_date
            while current <= end_date:
                date_str = current.strftime('%Y-%m-%d')
                prices[date_str] = {
                    'gbp': current_price_usd * gbp_rate,
                    'usd': current_price_usd
                }
                current += timedelta(days=1)
            
            return prices
            
        except Exception as e:
            print(f"Error fetching metal history for {asset_symbol}: {e}")
            return {}
    
    def _get_usd_to_gbp_rate(self) -> float:
        """Get current USD to GBP exchange rate"""
        try:
            url = "https://api.exchangerate-api.com/v4/latest/USD"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get('rates', {}).get('GBP', 0.79)
        except:
            return 0.79
    
    # ==================== PORTFOLIO SNAPSHOT CALCULATION ====================
    
    def calculate_portfolio_snapshots(self, from_date: datetime = None) -> int:
        """
        Calculate and store portfolio snapshots from from_date to today.
        If from_date is None, calculates from the earliest transaction date.
        Returns number of snapshots created/updated.
        """
        db_session = get_session()
        try:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Get all transactions ordered by date
            transactions = db_session.query(AssetTransaction).order_by(
                AssetTransaction.transaction_date
            ).all()
            
            if not transactions:
                return 0
            
            # Determine start date
            if from_date:
                start_date = from_date.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                start_date = transactions[0].transaction_date.replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            
            # Build holdings state up to start_date (from transactions before start_date)
            holdings = {}  # asset_id -> quantity
            cost_basis = {}  # asset_id -> cost basis
            
            for tx in transactions:
                tx_date = tx.transaction_date.replace(hour=0, minute=0, second=0, microsecond=0)
                if tx_date < start_date:
                    self._apply_transaction(tx, holdings, cost_basis)
            
            # Calculate snapshots for each day from start_date to today
            count = 0
            current_date = start_date
            
            while current_date <= today:
                # Apply any transactions on this date
                for tx in transactions:
                    tx_date = tx.transaction_date.replace(hour=0, minute=0, second=0, microsecond=0)
                    if tx_date == current_date:
                        self._apply_transaction(tx, holdings, cost_basis)
                
                # Calculate portfolio value using historical prices
                total_value = 0
                holdings_detail = {}
                
                for asset_id, quantity in holdings.items():
                    if quantity > 0:
                        # Get price for this date
                        price = self._get_price_for_date(db_session, asset_id, current_date)
                        value = quantity * price
                        total_value += value
                        holdings_detail[str(asset_id)] = {
                            'quantity': quantity,
                            'price': price,
                            'value': value
                        }
                
                # Calculate base investment (total cost basis of current holdings)
                base_investment = sum(
                    cost_basis.get(aid, 0) for aid, qty in holdings.items() if qty > 0
                )
                
                # Store or update snapshot
                self._store_snapshot(
                    db_session, current_date, total_value, base_investment, holdings_detail
                )
                count += 1
                
                current_date += timedelta(days=1)
            
            db_session.commit()
            return count
            
        except Exception as e:
            db_session.rollback()
            print(f"Error calculating portfolio snapshots: {e}")
            return 0
        finally:
            db_session.close()
    
    def _apply_transaction(self, tx: AssetTransaction, holdings: Dict, cost_basis: Dict):
        """Apply a transaction to holdings and cost_basis dictionaries"""
        if tx.asset_id not in holdings:
            holdings[tx.asset_id] = 0
            cost_basis[tx.asset_id] = 0
        
        if tx.transaction_type == AssetTransactionType.BUY:
            holdings[tx.asset_id] += tx.quantity
            cost_basis[tx.asset_id] += tx.total_value + (tx.fee or 0)
        elif tx.transaction_type == AssetTransactionType.SELL:
            if holdings[tx.asset_id] > 0:
                # Calculate cost basis to remove (proportional)
                ratio = tx.quantity / holdings[tx.asset_id]
                cost_basis[tx.asset_id] *= (1 - ratio)
            holdings[tx.asset_id] -= tx.quantity
    
    def _get_price_for_date(self, session, asset_id: int, date: datetime) -> float:
        """Get historical price for an asset on a specific date"""
        # Try exact date
        price = session.query(AssetPriceHistory).filter(
            AssetPriceHistory.asset_id == asset_id,
            func.date(AssetPriceHistory.date) == date.date()
        ).first()
        
        if price:
            return price.price_gbp
        
        # Fall back to most recent price before this date
        price = session.query(AssetPriceHistory).filter(
            AssetPriceHistory.asset_id == asset_id,
            AssetPriceHistory.date <= date
        ).order_by(AssetPriceHistory.date.desc()).first()
        
        if price:
            return price.price_gbp
        
        # Last resort: use current price from cache
        from app.models import AssetPriceCache
        cache = session.query(AssetPriceCache).filter(
            AssetPriceCache.asset_id == asset_id
        ).order_by(AssetPriceCache.price_date.desc()).first()
        
        return cache.price_gbp if cache else 0
    
    def _store_snapshot(self, session, date: datetime, total_value: float, 
                        base_investment: float, holdings_detail: Dict):
        """Store or update a portfolio snapshot"""
        existing = session.query(PortfolioSnapshot).filter(
            func.date(PortfolioSnapshot.snapshot_date) == date.date()
        ).first()
        
        if existing:
            existing.total_portfolio_value = total_value
            existing.base_investment = base_investment
            existing.holdings_breakdown = json.dumps(holdings_detail)
            existing.updated_at = datetime.now()
        else:
            snapshot = PortfolioSnapshot(
                snapshot_date=date,
                total_portfolio_value=total_value,
                base_investment=base_investment,
                holdings_breakdown=json.dumps(holdings_detail)
            )
            session.add(snapshot)
    
    # ==================== TRANSACTION HANDLING ====================
    
    def on_transaction_added(self, transaction: AssetTransaction):
        """
        Called when a new transaction is added.
        1. Fetch historical prices from transaction date to today
        2. Recalculate portfolio snapshots from transaction date
        """
        db_session = get_session()
        try:
            asset = db_session.query(Asset).filter(Asset.id == transaction.asset_id).first()
            if not asset or not asset.is_tracked:
                return
            
            tx_date = transaction.transaction_date.replace(hour=0, minute=0, second=0, microsecond=0)
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Capture asset data before closing session
            asset_id = asset.id
            asset_symbol = asset.symbol
            asset_api_id = asset.api_id
            asset_type = asset.asset_type
            
            db_session.close()
            
            # Step 1: Fetch historical prices
            print(f"Fetching historical prices for {asset_symbol} from {tx_date.date()} to {today.date()}")
            prices_fetched = self.fetch_historical_prices_for_asset(
                asset_id, asset_symbol, asset_api_id, asset_type, tx_date, today
            )
            print(f"Fetched {prices_fetched} price records")
            
            # Step 2: Recalculate portfolio snapshots
            print(f"Recalculating portfolio snapshots from {tx_date.date()}")
            snapshots_updated = self.calculate_portfolio_snapshots(tx_date)
            print(f"Updated {snapshots_updated} portfolio snapshots")
            return
            
        finally:
            pass  # Session already closed above
    
    def on_transaction_deleted(self, transaction_date: datetime):
        """
        Called when a transaction is deleted.
        Recalculate snapshots from that date forward.
        """
        self.calculate_portfolio_snapshots(transaction_date)
    
    # ==================== DAILY UPDATE JOB ====================
    
    def run_daily_update(self) -> Dict:
        """
        Daily job to update prices and snapshots.
        1. For each tracked asset, fetch prices from last stored date to today
        2. Calculate portfolio snapshots from last stored date to today
        """
        db_session = get_session()
        try:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            result = {'prices_fetched': 0, 'snapshots_updated': 0}
            
            # Step 1: Update prices for all tracked assets
            assets = db_session.query(Asset).filter(Asset.is_tracked == True).all()
            
            # Collect asset data before any operations
            asset_data = []
            for asset in assets:
                latest = db_session.query(func.max(AssetPriceHistory.date)).filter(
                    AssetPriceHistory.asset_id == asset.id
                ).scalar()
                
                if latest:
                    start_date = latest + timedelta(days=1)
                else:
                    earliest_tx = db_session.query(func.min(AssetTransaction.transaction_date)).filter(
                        AssetTransaction.asset_id == asset.id
                    ).scalar()
                    start_date = earliest_tx if earliest_tx else today
                
                asset_data.append({
                    'id': asset.id,
                    'symbol': asset.symbol,
                    'api_id': asset.api_id,
                    'asset_type': asset.asset_type,
                    'start_date': start_date
                })
            
            db_session.close()
            
            # Now fetch prices using primitive data
            for ad in asset_data:
                if ad['start_date'] <= today:
                    fetched = self.fetch_historical_prices_for_asset(
                        ad['id'], ad['symbol'], ad['api_id'], ad['asset_type'],
                        ad['start_date'], today
                    )
                    result['prices_fetched'] += fetched
            
            # Step 2: Update portfolio snapshots
            db_session2 = get_session()
            try:
                latest_snapshot = db_session2.query(func.max(PortfolioSnapshot.snapshot_date)).scalar()
                
                if latest_snapshot:
                    from_date = latest_snapshot + timedelta(days=1)
                else:
                    from_date = None
            finally:
                db_session2.close()
            
            if from_date is None or from_date <= today:
                result['snapshots_updated'] = self.calculate_portfolio_snapshots(from_date)
            
            return result
            
        except Exception as e:
            print(f"Error in daily update: {e}")
            return result
    
    # ==================== DATA RETRIEVAL (READ-ONLY) ====================
    
    def get_portfolio_history(self, days: int = 90) -> List[Dict]:
        """
        Get portfolio history for graph display.
        Simply reads from pre-calculated snapshots - NO calculations here.
        Returns daily data points.
        """
        db_session = get_session()
        try:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start_date = today - timedelta(days=days)
            
            snapshots = db_session.query(PortfolioSnapshot).filter(
                PortfolioSnapshot.snapshot_date >= start_date
            ).order_by(PortfolioSnapshot.snapshot_date).all()
            
            return [
                {
                    'date': s.snapshot_date.isoformat(),
                    'portfolio_value': s.total_portfolio_value,
                    'base_investment': s.base_investment
                }
                for s in snapshots
            ]
            
        finally:
            db_session.close()


# Singleton instance
portfolio_service = PortfolioService()

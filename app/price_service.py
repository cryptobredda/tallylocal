"""
Price Service - Fetches and caches asset prices from free APIs
- CoinGecko for cryptocurrencies (free, no auth required)
- GoldAPI/MetalPriceAPI for precious metals
"""

import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from app.database import get_session
from app.models import Asset, AssetPriceCache, AssetType


# Top 100 cryptocurrencies with CoinGecko IDs
TOP_CRYPTOCURRENCIES = {
    'BTC': {'name': 'Bitcoin', 'api_id': 'bitcoin', 'color': '#f7931a'},
    'ETH': {'name': 'Ethereum', 'api_id': 'ethereum', 'color': '#627eea'},
    'USDT': {'name': 'Tether', 'api_id': 'tether', 'color': '#26a17b'},
    'BNB': {'name': 'BNB', 'api_id': 'binancecoin', 'color': '#f3ba2f'},
    'SOL': {'name': 'Solana', 'api_id': 'solana', 'color': '#00ffa3'},
    'XRP': {'name': 'XRP', 'api_id': 'ripple', 'color': '#00aae4'},
    'USDC': {'name': 'USD Coin', 'api_id': 'usd-coin', 'color': '#2775ca'},
    'ADA': {'name': 'Cardano', 'api_id': 'cardano', 'color': '#0033ad'},
    'AVAX': {'name': 'Avalanche', 'api_id': 'avalanche-2', 'color': '#e84142'},
    'DOGE': {'name': 'Dogecoin', 'api_id': 'dogecoin', 'color': '#c2a633'},
    'TRX': {'name': 'TRON', 'api_id': 'tron', 'color': '#ff0013'},
    'DOT': {'name': 'Polkadot', 'api_id': 'polkadot', 'color': '#e6007a'},
    'LINK': {'name': 'Chainlink', 'api_id': 'chainlink', 'color': '#2a5ada'},
    'MATIC': {'name': 'Polygon', 'api_id': 'matic-network', 'color': '#8247e5'},
    'TON': {'name': 'Toncoin', 'api_id': 'the-open-network', 'color': '#0098ea'},
    'SHIB': {'name': 'Shiba Inu', 'api_id': 'shiba-inu', 'color': '#ffa409'},
    'ICP': {'name': 'Internet Computer', 'api_id': 'internet-computer', 'color': '#29abe2'},
    'DAI': {'name': 'Dai', 'api_id': 'dai', 'color': '#f5ac37'},
    'LTC': {'name': 'Litecoin', 'api_id': 'litecoin', 'color': '#bfbbbb'},
    'BCH': {'name': 'Bitcoin Cash', 'api_id': 'bitcoin-cash', 'color': '#8dc351'},
    'ATOM': {'name': 'Cosmos', 'api_id': 'cosmos', 'color': '#2e3148'},
    'UNI': {'name': 'Uniswap', 'api_id': 'uniswap', 'color': '#ff007a'},
    'ETC': {'name': 'Ethereum Classic', 'api_id': 'ethereum-classic', 'color': '#328332'},
    'XLM': {'name': 'Stellar', 'api_id': 'stellar', 'color': '#14b6e7'},
    'XMR': {'name': 'Monero', 'api_id': 'monero', 'color': '#ff6600'},
    'OKB': {'name': 'OKB', 'api_id': 'okb', 'color': '#2d60e0'},
    'APT': {'name': 'Aptos', 'api_id': 'aptos', 'color': '#000000'},
    'CRO': {'name': 'Cronos', 'api_id': 'crypto-com-chain', 'color': '#002d74'},
    'NEAR': {'name': 'NEAR Protocol', 'api_id': 'near', 'color': '#000000'},
    'LDO': {'name': 'Lido DAO', 'api_id': 'lido-dao', 'color': '#00a3ff'},
    'ARB': {'name': 'Arbitrum', 'api_id': 'arbitrum', 'color': '#28a0f0'},
    'FIL': {'name': 'Filecoin', 'api_id': 'filecoin', 'color': '#0090ff'},
    'VET': {'name': 'VeChain', 'api_id': 'vechain', 'color': '#15bdff'},
    'HBAR': {'name': 'Hedera', 'api_id': 'hedera-hashgraph', 'color': '#000000'},
    'QNT': {'name': 'Quant', 'api_id': 'quant-network', 'color': '#000000'},
    'MKR': {'name': 'Maker', 'api_id': 'maker', 'color': '#1aab9b'},
    'OP': {'name': 'Optimism', 'api_id': 'optimism', 'color': '#ff0420'},
    'AAVE': {'name': 'Aave', 'api_id': 'aave', 'color': '#b6509e'},
    'ALGO': {'name': 'Algorand', 'api_id': 'algorand', 'color': '#000000'},
    'GRT': {'name': 'The Graph', 'api_id': 'the-graph', 'color': '#6747ed'},
    'STX': {'name': 'Stacks', 'api_id': 'blockstack', 'color': '#5546ff'},
    'EGLD': {'name': 'MultiversX', 'api_id': 'elrond-erd-2', 'color': '#23f7dd'},
    'INJ': {'name': 'Injective', 'api_id': 'injective-protocol', 'color': '#00f2fe'},
    'THETA': {'name': 'Theta Network', 'api_id': 'theta-token', 'color': '#2ab8e6'},
    'FTM': {'name': 'Fantom', 'api_id': 'fantom', 'color': '#1969ff'},
    'SAND': {'name': 'The Sandbox', 'api_id': 'the-sandbox', 'color': '#00adef'},
    'AXS': {'name': 'Axie Infinity', 'api_id': 'axie-infinity', 'color': '#0055d5'},
    'MANA': {'name': 'Decentraland', 'api_id': 'decentraland', 'color': '#ff2d55'},
    'XTZ': {'name': 'Tezos', 'api_id': 'tezos', 'color': '#2c7df7'},
    'EOS': {'name': 'EOS', 'api_id': 'eos', 'color': '#000000'},
    'FLOW': {'name': 'Flow', 'api_id': 'flow', 'color': '#00ef8b'},
    'NEO': {'name': 'Neo', 'api_id': 'neo', 'color': '#00e599'},
    'KAVA': {'name': 'Kava', 'api_id': 'kava', 'color': '#ff564f'},
    'XEC': {'name': 'eCash', 'api_id': 'ecash', 'color': '#0074c2'},
    'IOTA': {'name': 'IOTA', 'api_id': 'iota', 'color': '#131f37'},
    'KCS': {'name': 'KuCoin Token', 'api_id': 'kucoin-shares', 'color': '#0093dd'},
    'RUNE': {'name': 'THORChain', 'api_id': 'thorchain', 'color': '#33ff99'},
    'CRV': {'name': 'Curve DAO', 'api_id': 'curve-dao-token', 'color': '#ff5733'},
    'SNX': {'name': 'Synthetix', 'api_id': 'havven', 'color': '#00d1ff'},
    'ZEC': {'name': 'Zcash', 'api_id': 'zcash', 'color': '#f4b728'},
    'COMP': {'name': 'Compound', 'api_id': 'compound-governance-token', 'color': '#00d395'},
    'DASH': {'name': 'Dash', 'api_id': 'dash', 'color': '#008ce7'},
    'CHZ': {'name': 'Chiliz', 'api_id': 'chiliz', 'color': '#cd0124'},
    'ENJ': {'name': 'Enjin Coin', 'api_id': 'enjincoin', 'color': '#7866d5'},
    'BAT': {'name': 'Basic Attention', 'api_id': 'basic-attention-token', 'color': '#ff5000'},
    'ZIL': {'name': 'Zilliqa', 'api_id': 'zilliqa', 'color': '#49c1bf'},
    'HOT': {'name': 'Holo', 'api_id': 'holotoken', 'color': '#00838d'},
    'WAVES': {'name': 'Waves', 'api_id': 'waves', 'color': '#0155ff'},
    '1INCH': {'name': '1inch', 'api_id': '1inch', 'color': '#1b314f'},
    'CAKE': {'name': 'PancakeSwap', 'api_id': 'pancakeswap-token', 'color': '#d1884f'},
    'GALA': {'name': 'Gala', 'api_id': 'gala', 'color': '#000000'},
    'GMT': {'name': 'STEPN', 'api_id': 'stepn', 'color': '#dce775'},
    'LRC': {'name': 'Loopring', 'api_id': 'loopring', 'color': '#1c60ff'},
    'KLAY': {'name': 'Klaytn', 'api_id': 'klay-token', 'color': '#ff3d00'},
    'ROSE': {'name': 'Oasis Network', 'api_id': 'oasis-network', 'color': '#0092f6'},
    'CELO': {'name': 'Celo', 'api_id': 'celo', 'color': '#35d07f'},
    'QTUM': {'name': 'Qtum', 'api_id': 'qtum', 'color': '#2e9ad0'},
    'ENS': {'name': 'Ethereum Name Service', 'api_id': 'ethereum-name-service', 'color': '#5298ff'},
    'PEPE': {'name': 'Pepe', 'api_id': 'pepe', 'color': '#3d9400'},
    'SUI': {'name': 'Sui', 'api_id': 'sui', 'color': '#6fbcf0'},
    'SEI': {'name': 'Sei', 'api_id': 'sei-network', 'color': '#9b1c1c'},
    'WLD': {'name': 'Worldcoin', 'api_id': 'worldcoin-wld', 'color': '#000000'},
    'BLUR': {'name': 'Blur', 'api_id': 'blur', 'color': '#ff6b00'},
    'FET': {'name': 'Fetch.ai', 'api_id': 'fetch-ai', 'color': '#1d2951'},
    'RNDR': {'name': 'Render', 'api_id': 'render-token', 'color': '#000000'},
    'IMX': {'name': 'Immutable', 'api_id': 'immutable-x', 'color': '#17b5cb'},
    'MINA': {'name': 'Mina', 'api_id': 'mina-protocol', 'color': '#e39844'},
    'CFX': {'name': 'Conflux', 'api_id': 'conflux-token', 'color': '#1a1a1a'},
    'AGIX': {'name': 'SingularityNET', 'api_id': 'singularitynet', 'color': '#562dc2'},
}

# Precious metals with their symbols
PRECIOUS_METALS = {
    'XAU': {'name': 'Gold', 'api_id': 'gold', 'color': '#ffd700'},
    'XAG': {'name': 'Silver', 'api_id': 'silver', 'color': '#c0c0c0'},
    'XPT': {'name': 'Platinum', 'api_id': 'platinum', 'color': '#e5e4e2'},
    'XPD': {'name': 'Palladium', 'api_id': 'palladium', 'color': '#ced0dd'},
}

COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"


class PriceService:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'FinanceTracker/1.0'
        })
    
    def get_supported_assets(self) -> Dict:
        """Return all supported assets for selection"""
        return {
            'crypto': TOP_CRYPTOCURRENCIES,
            'precious_metals': PRECIOUS_METALS
        }
    
    def get_crypto_prices(self, symbols: List[str] = None) -> Dict[str, Dict]:
        """Fetch current prices for cryptocurrencies from CoinGecko"""
        try:
            # If no specific symbols, get all
            if not symbols:
                symbols = list(TOP_CRYPTOCURRENCIES.keys())
            
            # Get CoinGecko IDs for requested symbols
            ids = []
            symbol_map = {}
            for symbol in symbols:
                if symbol in TOP_CRYPTOCURRENCIES:
                    api_id = TOP_CRYPTOCURRENCIES[symbol]['api_id']
                    ids.append(api_id)
                    symbol_map[api_id] = symbol
            
            if not ids:
                return {}
            
            # CoinGecko simple price endpoint (free, no auth)
            url = f"{COINGECKO_API_BASE}/simple/price"
            params = {
                'ids': ','.join(ids),
                'vs_currencies': 'gbp,usd',
                'include_24hr_change': 'true',
                'include_7d_change': 'true',
                'include_market_cap': 'true'
            }
            
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            # Transform response to our format
            result = {}
            for api_id, price_data in data.items():
                symbol = symbol_map.get(api_id)
                if symbol:
                    result[symbol] = {
                        'price_gbp': price_data.get('gbp', 0),
                        'price_usd': price_data.get('usd', 0),
                        'change_24h': price_data.get('gbp_24h_change', 0),
                        'change_7d': price_data.get('gbp_7d_change'),
                        'market_cap': price_data.get('gbp_market_cap', 0)
                    }
            
            return result
            
        except Exception as e:
            print(f"Error fetching crypto prices: {e}")
            return {}
    
    def get_crypto_historical_price(self, symbol: str, date: datetime) -> Optional[float]:
        """Fetch historical price for a specific date from CoinGecko"""
        try:
            if symbol not in TOP_CRYPTOCURRENCIES:
                return None
            
            api_id = TOP_CRYPTOCURRENCIES[symbol]['api_id']
            date_str = date.strftime('%d-%m-%Y')
            
            url = f"{COINGECKO_API_BASE}/coins/{api_id}/history"
            params = {'date': date_str, 'localization': 'false'}
            
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            market_data = data.get('market_data', {})
            return market_data.get('current_price', {}).get('gbp')
            
        except Exception as e:
            print(f"Error fetching historical crypto price: {e}")
            return None
    
    def get_metal_prices(self) -> Dict[str, Dict]:
        """Fetch current prices for precious metals using free API"""
        try:
            # Using metals.live free API (no auth required)
            url = "https://api.metals.live/v1/spot"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            result = {}
            # metals.live returns prices per troy ounce in USD
            # We need to convert to GBP
            gbp_rate = self._get_usd_to_gbp_rate()
            
            for item in data:
                metal = item.get('metal', '').lower()
                price_usd = item.get('price', 0)
                
                symbol_map = {
                    'gold': 'XAU',
                    'silver': 'XAG',
                    'platinum': 'XPT',
                    'palladium': 'XPD'
                }
                
                if metal in symbol_map:
                    symbol = symbol_map[metal]
                    result[symbol] = {
                        'price_gbp': price_usd * gbp_rate,
                        'price_usd': price_usd,
                        'change_24h': 0,  # This API doesn't provide change data
                        'change_7d': None,
                        'market_cap': None
                    }
            
            return result
            
        except Exception as e:
            print(f"Error fetching metal prices: {e}")
            # Fallback: return None to indicate untracked
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
            return 0.79  # Fallback rate
    
    def update_price_cache(self, asset_ids: List[int] = None) -> Dict[str, int]:
        """Update price cache for all tracked assets (or specific ones)"""
        db_session = get_session()
        try:
            # Get assets to update
            query = db_session.query(Asset).filter(Asset.is_tracked == True)
            if asset_ids:
                query = query.filter(Asset.id.in_(asset_ids))
            assets = query.all()
            
            if not assets:
                return {'updated': 0, 'failed': 0}
            
            # Group by type
            crypto_assets = [a for a in assets if a.asset_type == AssetType.CRYPTO]
            metal_assets = [a for a in assets if a.asset_type == AssetType.PRECIOUS_METAL]
            
            updated = 0
            failed = 0
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Fetch crypto prices
            if crypto_assets:
                crypto_symbols = [a.symbol for a in crypto_assets]
                crypto_prices = self.get_crypto_prices(crypto_symbols)
                
                for asset in crypto_assets:
                    if asset.symbol in crypto_prices:
                        price_data = crypto_prices[asset.symbol]
                        self._update_or_create_cache(
                            db_session, asset.id, today, price_data
                        )
                        updated += 1
                    else:
                        failed += 1
            
            # Fetch metal prices
            if metal_assets:
                metal_prices = self.get_metal_prices()
                
                for asset in metal_assets:
                    if asset.symbol in metal_prices:
                        price_data = metal_prices[asset.symbol]
                        self._update_or_create_cache(
                            db_session, asset.id, today, price_data
                        )
                        updated += 1
                    else:
                        failed += 1
            
            db_session.commit()
            
            # Take portfolio snapshot after price update
            try:
                from app.asset_service import asset_service
                asset_service.take_portfolio_snapshot()
            except Exception as snapshot_err:
                print(f"Failed to take snapshot: {snapshot_err}")
            
            return {'updated': updated, 'failed': failed}
            
        except Exception as e:
            db_session.rollback()
            print(f"Error updating price cache: {e}")
            return {'updated': 0, 'failed': 0, 'error': str(e)}
        finally:
            db_session.close()
    
    def _update_or_create_cache(self, session, asset_id: int, price_date: datetime, price_data: Dict):
        """Update existing cache entry or create new one"""
        existing = session.query(AssetPriceCache).filter(
            AssetPriceCache.asset_id == asset_id,
            AssetPriceCache.price_date == price_date
        ).first()
        
        if existing:
            existing.price_gbp = price_data.get('price_gbp')
            existing.price_usd = price_data.get('price_usd')
            existing.change_24h = price_data.get('change_24h')
            existing.change_7d = price_data.get('change_7d')
            existing.market_cap = price_data.get('market_cap')
            existing.fetched_at = datetime.now()
        else:
            cache_entry = AssetPriceCache(
                asset_id=asset_id,
                price_date=price_date,
                price_gbp=price_data.get('price_gbp'),
                price_usd=price_data.get('price_usd'),
                change_24h=price_data.get('change_24h'),
                change_7d=price_data.get('change_7d'),
                market_cap=price_data.get('market_cap')
            )
            session.add(cache_entry)
    
    def get_cached_price(self, asset_id: int) -> Optional[Dict]:
        """Get most recent cached price for an asset"""
        db_session = get_session()
        try:
            cache = db_session.query(AssetPriceCache).filter(
                AssetPriceCache.asset_id == asset_id
            ).order_by(AssetPriceCache.price_date.desc()).first()
            
            if cache:
                return {
                    'price_gbp': cache.price_gbp,
                    'price_usd': cache.price_usd,
                    'change_24h': cache.change_24h,
                    'change_7d': cache.change_7d,
                    'market_cap': cache.market_cap,
                    'price_date': cache.price_date.isoformat(),
                    'fetched_at': cache.fetched_at.isoformat()
                }
            return None
        finally:
            db_session.close()
    
    def should_refresh_prices(self) -> bool:
        """Check if prices should be refreshed (once per day for all tracked assets)"""
        db_session = get_session()
        try:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Count tracked assets
            tracked_count = db_session.query(Asset).filter(Asset.is_tracked == True).count()
            if tracked_count == 0:
                return False
            
            # Count assets with today's prices
            cached_today = db_session.query(AssetPriceCache.asset_id).filter(
                AssetPriceCache.price_date >= today
            ).distinct().count()
            
            # Need refresh if not all tracked assets have today's prices
            return cached_today < tracked_count
        finally:
            db_session.close()


# Singleton instance
price_service = PriceService()

"""
Wallet Service
--------------
Tracks external blockchain wallets (BTC, EVM, SOL) and stores transaction/balance
snapshots so the frontend can display portfolio-style analytics.
"""

import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

import requests
from sqlalchemy import func, or_

from app.database import get_session
from app.integration_service import get_wallet_api_key
from app.models import (
    Wallet,
    WalletBalanceSnapshot,
    WalletNetwork,
    WalletTransaction,
    WalletTransactionType,
)
from app.price_service import price_service


BTC_REGEX = re.compile(r"^(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,39}$")
EVM_REGEX = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOL_REGEX = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


class WalletSyncError(Exception):
    """Raised when remote wallet data cannot be retrieved."""


class WalletService:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "TallyLocal/WalletTracker",
            }
        )
        self._load_api_keys()

    def _load_api_keys(self):
        self.moralis_key = get_wallet_api_key("moralis")
        self.helius_key = get_wallet_api_key("helius")

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def list_wallets(self) -> Dict:
        """Return every tracked wallet plus simple aggregates."""
        session = get_session()
        try:
            wallets = (
                session.query(Wallet)
                .order_by(Wallet.created_at.desc())
                .all()
            )
            summaries = [self._serialize_wallet(w, session) for w in wallets]
            totals = self._build_totals(wallets)
            return {"wallets": summaries, "totals": totals}
        finally:
            session.close()

    def create_wallet(self, address: str, label: Optional[str], notes: Optional[str]):
        """Create a new wallet entry after detecting its network."""
        address = (address or "").strip()
        if not address:
            return {"error": "Wallet address is required"}

        network = self.detect_network(address)
        if not network:
            return {"error": "Unable to detect wallet network (BTC/EVM/SOL)"}

        session = get_session()
        try:
            existing = (
                session.query(Wallet)
                .filter(func.lower(Wallet.address) == address.lower())
                .first()
            )
            if existing:
                return {"error": "Wallet already tracked"}

            wallet = Wallet(
                label=(label or "").strip() or None,
                address=address,
                network=network,
                notes=(notes or "").strip() or None,
                native_symbol=self._default_symbol(network),
            )
            session.add(wallet)
            session.commit()
            return {"wallet": self._serialize_wallet(wallet, session)}
        except Exception as exc:
            session.rollback()
            return {"error": str(exc)}
        finally:
            session.close()

    def delete_wallet(self, wallet_id: int) -> Dict:
        session = get_session()
        try:
            wallet = session.query(Wallet).filter(Wallet.id == wallet_id).first()
            if not wallet:
                return {"error": "Wallet not found"}
            session.delete(wallet)
            session.commit()
            return {"status": "ok"}
        except Exception as exc:
            session.rollback()
            return {"error": str(exc)}
        finally:
            session.close()

    def detect_network(self, address: str) -> Optional[WalletNetwork]:
        """Heuristically detect which network a wallet address belongs to."""
        if not address:
            return None
        addr = address.strip()
        if BTC_REGEX.match(addr):
            return WalletNetwork.BTC
        if EVM_REGEX.match(addr):
            return WalletNetwork.EVM
        if SOL_REGEX.match(addr):
            return WalletNetwork.SOL
        return None

    def sync_wallet(self, wallet_id: int) -> Dict:
        """Fetch latest data for a wallet and store transactions/snapshots."""
        session = get_session()
        try:
            wallet = session.query(Wallet).filter(Wallet.id == wallet_id).first()
            if not wallet:
                return {"error": "Wallet not found"}

            payload = self._fetch_wallet_payload(wallet)
            self._store_wallet_payload(session, wallet, payload)
            session.commit()
            session.refresh(wallet)
            return {"wallet": self._serialize_wallet(wallet, session)}
        except WalletSyncError as exc:
            session.rollback()
            if wallet_id:
                self._mark_sync_failure(wallet_id, str(exc))
            return {"error": str(exc)}
        except Exception as exc:
            session.rollback()
            if wallet_id:
                self._mark_sync_failure(wallet_id, str(exc))
            return {"error": "Failed to sync wallet: " + str(exc)}
        finally:
            session.close()

    def get_wallet_transactions(self, wallet_id: Optional[int] = None,
                                page: int = 1, per_page: int = 25) -> Dict:
        session = get_session()
        try:
            page = max(1, page)
            per_page = max(1, per_page)
            base_query = session.query(WalletTransaction, Wallet).join(Wallet)
            if wallet_id:
                base_query = base_query.filter(WalletTransaction.wallet_id == wallet_id)
            total = base_query.count()
            entries = base_query.order_by(WalletTransaction.timestamp.desc()).offset(
                (page - 1) * per_page
            ).limit(per_page).all()
            total_pages = max(1, (total + per_page - 1) // per_page)
            return {
                "transactions": [
                    self._serialize_transaction(tx, wallet)
                    for tx, wallet in entries
                ],
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "total_pages": total_pages,
                }
            }
        finally:
            session.close()

    def get_wallet_history(self, wallet_id: int, days: Optional[int] = 90) -> List[Dict]:
        session = get_session()
        try:
            query = session.query(WalletBalanceSnapshot).filter(
                WalletBalanceSnapshot.wallet_id == wallet_id
            )
            if days and days > 0:
                cutoff = datetime.utcnow() - timedelta(days=days)
                query = query.filter(WalletBalanceSnapshot.snapshot_time >= cutoff)
            snapshots = (
                query.order_by(WalletBalanceSnapshot.snapshot_time.asc())
                .all()
            )
            return [
                {
                    "timestamp": snap.snapshot_time.isoformat(),
                    "balance": snap.balance_native,
                }
                for snap in snapshots
            ]
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    # Serialization helpers
    # ------------------------------------------------------------------ #

    def _serialize_wallet(self, wallet: Wallet, session=None) -> Dict:
        owns_session = session is None
        session = session or get_session()
        try:
            tx_count = (
                session.query(func.count(WalletTransaction.id))
                .filter(WalletTransaction.wallet_id == wallet.id)
                .scalar()
            ) or 0
            tokens = self._safe_load_json(wallet.token_balances) or []
            return {
                "id": wallet.id,
                "label": wallet.label,
                "address": wallet.address,
                "network": wallet.network.value,
                "native_symbol": wallet.native_symbol,
                "native_balance": wallet.native_balance or 0,
                "token_balances": tokens,
                "total_value_usd": wallet.total_value_usd,
                "notes": wallet.notes,
                "last_synced_at": wallet.last_synced_at.isoformat()
                if wallet.last_synced_at
                else None,
                "last_sync_status": wallet.last_sync_status,
                "last_sync_error": wallet.last_sync_error,
                "transactions_count": tx_count,
                "created_at": wallet.created_at.isoformat()
                if wallet.created_at
                else None,
            }
        finally:
            if owns_session:
                session.close()

    def _serialize_transaction(self, tx: WalletTransaction, wallet: Optional[Wallet] = None) -> Dict:
        data = {
            "id": tx.id,
            "hash": tx.tx_hash,
            "timestamp": tx.timestamp.isoformat() if tx.timestamp else None,
            "chain": tx.chain,
            "type": tx.tx_type.value if tx.tx_type else WalletTransactionType.OTHER.value,
            "amount": tx.amount_native,
            "net_amount": tx.net_amount_native,
            "native_symbol": tx.native_symbol,
            "token_symbol": tx.token_symbol,
            "usd_value": tx.amount_usd,
            "from_address": tx.from_address,
            "to_address": tx.to_address,
            "description": tx.description,
            "explorer_url": tx.explorer_url,
            "fee_native": tx.fee_native,
        }
        if wallet:
            data["wallet"] = {
                "id": wallet.id,
                "label": wallet.label or ((wallet.address[:8] + "…") if wallet.address else f"Wallet {wallet.id}"),
                "network": wallet.network.value,
            }
        return data

    def _build_totals(self, wallets: Sequence[Wallet]) -> Dict:
        totals = {
            "count": len(wallets),
            "by_network": {},
            "synced": 0,
            "pending": 0,
            "last_sync": None,
        }
        latest = None
        for wallet in wallets:
            net = wallet.network.value
            totals["by_network"][net] = totals["by_network"].get(net, 0) + 1
            if wallet.last_sync_status == "ok":
                totals["synced"] += 1
                if wallet.last_synced_at and (
                    latest is None or wallet.last_synced_at > latest
                ):
                    latest = wallet.last_synced_at
            else:
                totals["pending"] += 1
        if latest:
            totals["last_sync"] = latest.isoformat()
        return totals

    def get_value_summary(self) -> Dict:
        session = get_session()
        try:
            wallets = session.query(Wallet).all()
        finally:
            session.close()

        symbols = {w.native_symbol.upper() for w in wallets if w.native_symbol}
        price_map = {}
        if symbols:
            price_data = price_service.get_crypto_prices(list(symbols))
            for symbol in symbols:
                info = price_data.get(symbol)
                if info:
                    price_map[symbol] = info.get('price_gbp')

        total_value = 0
        breakdown = []
        for wallet in wallets:
            symbol = (wallet.native_symbol or '').upper()
            price = price_map.get(symbol, 0)
            value = (wallet.native_balance or 0) * price if price else 0
            total_value += value
            label = wallet.label or ((wallet.address[:8] + "…") if wallet.address else f"Wallet {wallet.id}")
            breakdown.append({
                'id': wallet.id,
                'label': label,
                'network': wallet.network.value,
                'native_symbol': wallet.native_symbol,
                'native_balance': wallet.native_balance,
                'value_gbp': round(value, 2),
                'last_synced_at': wallet.last_synced_at.isoformat() if wallet.last_synced_at else None
            })

        return {
            'total_value': round(total_value, 2),
            'wallets': breakdown
        }

    # ------------------------------------------------------------------ #
    # Persistence helpers
    # ------------------------------------------------------------------ #

    def _store_wallet_payload(self, session, wallet: Wallet, payload: Dict):
        wallet.native_symbol = payload.get("native_symbol") or wallet.native_symbol
        wallet.native_balance = payload.get("native_balance", 0) or 0
        wallet.total_value_usd = payload.get("total_value_usd")
        wallet.token_balances = json.dumps(payload.get("tokens", []))
        wallet.last_synced_at = datetime.utcnow()
        wallet.last_sync_status = "ok"
        wallet.last_sync_error = None

        transactions = payload.get("transactions", [])
        if transactions:
            existing = {
                tx_hash: tx_id
                for tx_hash, tx_id in session.query(
                    WalletTransaction.tx_hash, WalletTransaction.id
                )
                .filter(WalletTransaction.wallet_id == wallet.id)
                .all()
            }
            for tx_data in transactions:
                tx_values = self._build_transaction_model(wallet, tx_data)
                if tx_data["hash"] in existing:
                    (session.query(WalletTransaction)
                     .filter(WalletTransaction.id == existing[tx_data["hash"]])
                     .update(tx_values))
                else:
                    session.add(WalletTransaction(**tx_values))

        # Replace history snapshots so the chart stays consistent with remote data.
        session.query(WalletBalanceSnapshot).filter(
            WalletBalanceSnapshot.wallet_id == wallet.id
        ).delete()

        history = payload.get("history") or self._rebuild_history_from_transactions(
            session, wallet.id, wallet.native_balance
        )
        seen_times = set()
        for point in history:
            snap_time = point["timestamp"]
            if isinstance(snap_time, str):
                snap_time = self._parse_timestamp(snap_time)
            elif not isinstance(snap_time, datetime):
                snap_time = datetime.utcnow()
            while snap_time in seen_times:
                snap_time += timedelta(seconds=1)
            seen_times.add(snap_time)
            session.add(
                WalletBalanceSnapshot(
                    wallet_id=wallet.id,
                    snapshot_time=snap_time or datetime.utcnow(),
                    balance_native=point["balance"],
                    token_breakdown=json.dumps(payload.get("tokens", [])),
                )
            )

    def _build_transaction_model(self, wallet: Wallet, tx_data: Dict) -> Dict:
        timestamp = tx_data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = self._parse_timestamp(timestamp)
        return {
            "wallet_id": wallet.id,
            "tx_hash": tx_data.get("hash"),
            "timestamp": timestamp or datetime.utcnow(),
            "chain": tx_data.get("chain"),
            "tx_type": self._to_tx_type(tx_data.get("direction")),
            "amount_native": abs(tx_data.get("amount", 0) or 0),
            "net_amount_native": tx_data.get("net_amount", 0) or 0,
            "native_symbol": tx_data.get("native_symbol") or wallet.native_symbol,
            "token_symbol": tx_data.get("token_symbol"),
            "amount_usd": tx_data.get("usd_value"),
            "from_address": tx_data.get("from_address"),
            "to_address": tx_data.get("to_address"),
            "description": tx_data.get("description"),
            "explorer_url": tx_data.get("explorer_url"),
            "fee_native": tx_data.get("fee_native"),
            "metadata_json": json.dumps(tx_data.get("raw", {})),
        }

    def _rebuild_history_from_transactions(self, session, wallet_id: int, final_balance: float):
        txs = (
            session.query(WalletTransaction)
            .filter(WalletTransaction.wallet_id == wallet_id)
            .order_by(WalletTransaction.timestamp.asc())
            .all()
        )
        history = []
        running = 0.0
        for tx in txs:
            running += tx.net_amount_native or 0
            history.append(
                {
                    "timestamp": tx.timestamp or datetime.utcnow(),
                    "balance": running,
                }
            )
        return self._normalize_history(history, final_balance)

    def _mark_sync_failure(self, wallet_id: int, message: str):
        session = get_session()
        try:
            session.query(Wallet).filter(Wallet.id == wallet_id).update(
                {
                    Wallet.last_sync_status: "error",
                    Wallet.last_sync_error: message[:500],
                    Wallet.last_synced_at: datetime.utcnow(),
                }
            )
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    # Remote API handlers
    # ------------------------------------------------------------------ #

    def _fetch_wallet_payload(self, wallet: Wallet) -> Dict:
        # Re-read env vars in case they changed after startup.
        self._load_api_keys()
        if wallet.network == WalletNetwork.BTC:
            return self._fetch_btc_wallet(wallet.address)
        if wallet.network == WalletNetwork.EVM:
            return self._fetch_evm_wallet(wallet.address)
        if wallet.network == WalletNetwork.SOL:
            return self._fetch_sol_wallet(wallet.address)
        raise WalletSyncError("Unsupported wallet network")

    def _fetch_btc_wallet(self, address: str) -> Dict:
        url = f"https://blockchain.info/rawaddr/{address}"
        response = self.session.get(url, timeout=30)
        if not response.ok:
            raise WalletSyncError(f"Blockchain.info error ({response.status_code})")
        data = response.json()

        final_balance = (data.get("final_balance", 0) or 0) / 1e8
        txs = data.get("txs", [])
        transactions = []
        history = []
        running = 0.0

        # rawaddr returns newest first; reverse for running balance
        for tx in sorted(txs, key=lambda t: t.get("time", 0)):
            timestamp = self._parse_timestamp(tx.get("time"))
            amount_sat = tx.get("result", 0) or 0
            amount_btc = amount_sat / 1e8
            direction = self._direction_from_amount(amount_btc)
            from_addr = self._summarize_addresses(
                [inp.get("prev_out", {}).get("addr") for inp in tx.get("inputs", [])]
            )
            to_addr = self._summarize_addresses([out.get("addr") for out in tx.get("out", [])])
            transactions.append(
                {
                    "hash": tx.get("hash"),
                    "timestamp": timestamp,
                    "direction": direction,
                    "amount": abs(amount_btc),
                    "net_amount": amount_btc,
                    "native_symbol": "BTC",
                    "from_address": from_addr,
                    "to_address": to_addr,
                    "explorer_url": f"https://www.blockchain.com/btc/tx/{tx.get('hash')}",
                    "raw": {"result": amount_sat},
                }
            )
            running += amount_btc
            history.append(
                {
                    "timestamp": timestamp,
                    "balance": running,
                }
            )

        normalized_history = self._normalize_history(history, final_balance)
        return {
            "native_symbol": "BTC",
            "native_balance": final_balance,
            "tokens": [{"symbol": "BTC", "amount": final_balance}],
            "transactions": transactions,
            "history": normalized_history,
        }

    def _fetch_evm_wallet(self, address: str) -> Dict:
        if not self.moralis_key:
            raise WalletSyncError("Add a Moralis API key in Admin → API Integrations to sync EVM wallets")

        headers = {"X-API-Key": self.moralis_key}
        params = {"limit": 100, "order": "DESC"}
        tx_url = f"https://deep-index.moralis.io/api/v2.2/{address}"
        response = self.session.get(tx_url, headers=headers, params=params, timeout=30)
        if response.status_code == 401:
            raise WalletSyncError("Invalid Moralis API key")
        if not response.ok:
            raise WalletSyncError(f"Moralis error ({response.status_code})")
        data = response.json()
        tx_items = self._extract_result_list(data)

        transactions = []
        history = []
        running = 0.0
        address_lower = address.lower()

        for tx in sorted(
            tx_items,
            key=lambda t: self._parse_timestamp(t.get("block_timestamp") or t.get("timestamp") or datetime.utcnow()),
        ):
            timestamp = self._parse_timestamp(
                tx.get("block_timestamp") or tx.get("timestamp") or tx.get("date")
            )
            raw_value = tx.get("value") or tx.get("value_decimal") or "0"
            amount = self._to_float(raw_value, 18)
            from_addr = (tx.get("from_address") or tx.get("from") or "").lower()
            to_addr = (tx.get("to_address") or tx.get("to") or "").lower()
            direction = WalletTransactionType.OTHER.value
            net_amount = 0.0
            if to_addr == address_lower and amount:
                direction = WalletTransactionType.RECEIVE.value
                net_amount = amount
            elif from_addr == address_lower and amount:
                direction = WalletTransactionType.SEND.value
                net_amount = -amount
            if from_addr == to_addr == address_lower:
                direction = WalletTransactionType.INTERNAL.value
                net_amount = 0.0
            running += net_amount
            history.append({"timestamp": timestamp, "balance": running})
            transactions.append(
                {
                    "hash": tx.get("hash"),
                    "timestamp": timestamp,
                    "direction": direction,
                    "amount": abs(net_amount),
                    "net_amount": net_amount,
                    "native_symbol": "ETH",
                    "from_address": tx.get("from_address") or tx.get("from"),
                    "to_address": tx.get("to_address") or tx.get("to"),
                    "chain": tx.get("chain") or tx.get("chain_name"),
                    "explorer_url": f"https://etherscan.io/tx/{tx.get('hash')}"
                    if not tx.get("chain")
                    else None,
                    "raw": {"value": raw_value},
                }
            )

        native_balance = self._fetch_moralis_native_balance(address)
        balance_history = self._normalize_history(history, native_balance)
        tokens = self._fetch_moralis_tokens(address)

        return {
            "native_symbol": "ETH",
            "native_balance": native_balance,
            "tokens": tokens,
            "transactions": transactions,
            "history": balance_history,
        }

    def _fetch_sol_wallet(self, address: str) -> Dict:
        if not self.helius_key:
            raise WalletSyncError("Add a Helius API key in Admin → API Integrations to sync Solana wallets")

        params = {"api-key": self.helius_key, "limit": 100}
        tx_url = f"https://api.helius.xyz/v0/addresses/{address}/transactions"
        response = self.session.get(tx_url, params=params, timeout=30)
        if response.status_code == 401:
            raise WalletSyncError("Invalid Helius API key")
        if not response.ok:
            raise WalletSyncError(f"Helius error ({response.status_code})")
        tx_items = response.json()
        if not isinstance(tx_items, list):
            tx_items = []

        transactions = []
        history = []
        running = 0.0

        for tx in sorted(tx_items, key=lambda t: t.get("timestamp", 0)):
            timestamp = self._parse_timestamp(tx.get("timestamp"))
            net_amount = 0.0
            native_transfers = tx.get("nativeTransfers") or []
            for transfer in native_transfers:
                amt = (transfer.get("amount") or 0) / 1e9
                if transfer.get("toUserAccount") == address:
                    net_amount += abs(amt)
                if transfer.get("fromUserAccount") == address:
                    net_amount -= abs(amt)
            direction = self._direction_from_amount(net_amount)
            running += net_amount
            history.append({"timestamp": timestamp, "balance": running})
            transactions.append(
                {
                    "hash": tx.get("signature"),
                    "timestamp": timestamp,
                    "direction": direction,
                    "amount": abs(net_amount),
                    "net_amount": net_amount,
                    "native_symbol": "SOL",
                    "description": tx.get("description"),
                    "from_address": self._summarize_addresses(
                        [t.get("fromUserAccount") for t in native_transfers]
                    ),
                    "to_address": self._summarize_addresses(
                        [t.get("toUserAccount") for t in native_transfers]
                    ),
                    "explorer_url": f"https://solscan.io/tx/{tx.get('signature')}",
                    "raw": {"type": tx.get("type")},
                }
            )

        balances = self._fetch_helius_balances(address)
        balance_history = self._normalize_history(history, balances.get("native_balance", 0))
        tokens = balances.get("tokens", [])

        return {
            "native_symbol": "SOL",
            "native_balance": balances.get("native_balance", 0),
            "tokens": tokens,
            "transactions": transactions,
            "history": balance_history,
        }

    def _fetch_moralis_native_balance(self, address: str) -> float:
        headers = {"X-API-Key": self.moralis_key}
        bal_url = f"https://deep-index.moralis.io/api/v2.2/{address}/balance"
        resp = self.session.get(bal_url, headers=headers, timeout=30)
        if not resp.ok:
            return 0.0
        data = resp.json()
        raw = data.get("balance") or 0
        return self._to_float(raw, 18)

    def _fetch_moralis_tokens(self, address: str) -> List[Dict]:
        headers = {"X-API-Key": self.moralis_key}
        url = f"https://deep-index.moralis.io/api/v2.2/{address}/erc20"
        resp = self.session.get(url, headers=headers, timeout=30)
        if not resp.ok:
            return []
        data = resp.json()
        tokens = self._extract_result_list(data)
        result = []
        for token in tokens:
            decimals = int(token.get("decimals") or token.get("token_decimals") or 18)
            symbol = token.get("symbol") or token.get("token_symbol")
            amount = self._to_float(token.get("balance") or token.get("token_balance") or 0, decimals)
            result.append(
                {
                    "symbol": symbol,
                    "amount": amount,
                    "address": token.get("token_address"),
                    "name": token.get("name") or token.get("token_name"),
                }
            )
        return result

    def _fetch_helius_balances(self, address: str) -> Dict:
        url = f"https://api.helius.xyz/v0/addresses/{address}/balances"
        params = {"api-key": self.helius_key}
        resp = self.session.get(url, params=params, timeout=30)
        if not resp.ok:
            return {"native_balance": 0, "tokens": []}
        data = resp.json()
        native_balance = (data.get("nativeBalance") or 0) / 1e9
        tokens = []
        for token in data.get("tokens", []):
            decimals = token.get("decimals") or 0
            divisor = 10 ** decimals if decimals else 1
            tokens.append(
                {
                    "symbol": token.get("symbol") or token.get("mint")[:4],
                    "amount": (token.get("amount") or 0) / divisor,
                    "mint": token.get("mint"),
                }
            )
        return {"native_balance": native_balance, "tokens": tokens}

    # ------------------------------------------------------------------ #
    # Utilities
    # ------------------------------------------------------------------ #

    def _default_symbol(self, network: WalletNetwork) -> str:
        return {
            WalletNetwork.BTC: "BTC",
            WalletNetwork.EVM: "ETH",
            WalletNetwork.SOL: "SOL",
        }.get(network, "")

    def _safe_load_json(self, payload: Optional[str]):
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    def _parse_timestamp(self, value) -> datetime:
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        if isinstance(value, (int, float)):
            return datetime.utcfromtimestamp(value)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                pass
        return datetime.utcnow()

    def _extract_result_list(self, payload):
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("result", "items", "transactions"):
                if key in payload and isinstance(payload[key], list):
                    return payload[key]
        return []

    def _to_float(self, value, decimals: int) -> float:
        if value in (None, "", 0):
            return 0.0
        try:
            if isinstance(value, str) and value.isdigit():
                value = int(value)
            elif isinstance(value, str):
                return float(value)
            return float(value) / (10 ** decimals)
        except Exception:
            try:
                return float(value)
            except Exception:
                return 0.0

    def _summarize_addresses(self, addresses: List[Optional[str]]) -> Optional[str]:
        filtered = [addr for addr in addresses if addr]
        if not filtered:
            return None
        uniq = list(dict.fromkeys(filtered))
        summary = ", ".join(uniq[:2])
        if len(uniq) > 2:
            summary += ", …"
        return summary

    def _direction_from_amount(self, amount: float) -> str:
        if amount > 0:
            return WalletTransactionType.RECEIVE.value
        if amount < 0:
            return WalletTransactionType.SEND.value
        return WalletTransactionType.OTHER.value

    def _normalize_history(self, history: List[Dict], final_balance: float) -> List[Dict]:
        """Ensure the reconstructed history ends at the supplied final balance."""
        if not history:
            return []
        history_sorted = sorted(history, key=lambda h: h["timestamp"])
        running_end = history_sorted[-1]["balance"]
        delta = (final_balance or 0) - running_end
        if abs(delta) > 1e-9:
            for point in history_sorted:
                point["balance"] += delta
        return history_sorted

    def _to_tx_type(self, direction: Optional[str]) -> WalletTransactionType:
        if not direction:
            return WalletTransactionType.OTHER
        try:
            return WalletTransactionType(direction)
        except ValueError:
            return WalletTransactionType.OTHER

    def sync_due_wallets(self, max_wallets: int = 2) -> Dict:
        cutoff = datetime.utcnow() - timedelta(hours=24)
        max_wallets = max(1, int(max_wallets or 1))
        session = get_session()
        try:
            wallets = session.query(Wallet).filter(
                or_(Wallet.last_synced_at.is_(None), Wallet.last_synced_at < cutoff)
            ).order_by(Wallet.last_synced_at.asc().nullsfirst()).limit(max_wallets).all()
        finally:
            session.close()

        synced = []
        errors = []
        for wallet in wallets:
            try:
                result = self.sync_wallet(wallet.id)
                if 'wallet' in result:
                    synced.append(wallet.id)
            except Exception as exc:
                errors.append({'id': wallet.id, 'error': str(exc)})

        return {'synced_count': len(synced), 'wallet_ids': synced, 'errors': errors}


wallet_service = WalletService()

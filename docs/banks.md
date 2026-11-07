# Bank connections

Tally supports three ways to get bank data, in order of reliability:

1. **Statement import** — always works, no third party, no consent expiry.
2. **Open banking aggregators** — live balances and transactions, requires a
   provider account and a consent flow.
3. **Bank-native APIs** — free where a bank offers one, usually token-based.

A connection is optional. Many people will be happiest importing a CSV or PDF
once a month.

---

## Provider matrix

| Provider | Region | Cost | Auth | Status |
|---|---|---|---|---|
| **Enable Banking** | EU + UK, ~2,700 banks | Free "restricted production" for your own linked accounts; volume-priced for public apps | OAuth redirect + SCA, RS256 JWT | Supported |
| **SimpleFIN Bridge** | US (some CA) | ~$15/year | Paste a setup token, then HTTP Basic | Supported |
| **Statement import** | Anywhere | Free | None | Supported |
| GoCardless (ex-Nordigen) | EU/UK | — | — | **Not supported**: closed to new signups |
| Plaid | US/EU | Paid | — | Not supported |

Why not scrape? It breaks constantly, violates most banks' terms, and can get an
account flagged. Tally does not do it.

---

## Enable Banking (UK / EU)

Enable Banking is the closest thing left to the old Nordigen free tier: you can
activate a production application in **restricted mode** by linking your own
accounts, with no contract, no KYB and no eIDAS certificate.

### 1. Create the application

1. Sign up at <https://enablebanking.com/>.
2. Create an application in the Control Panel, environment **production**.
3. Generate an RSA key pair and upload the public key:

```bash
openssl genrsa -out enablebanking.pem 2048
openssl rsa -in enablebanking.pem -pubout -out enablebanking.pub
```

4. Note the **Application ID**. That is the JWT `kid`.

### 2. Configure Tally

```bash
export ENABLEBANKING_APP_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export ENABLEBANKING_PRIVATE_KEY="$(cat enablebanking.pem)"
```

Optional overrides: `ENABLEBANKING_BASE_URL`, `ENABLEBANKING_JWT_ISSUER`,
`ENABLEBANKING_JWT_AUDIENCE`.

`tally bank providers` should now show `"configured": true`.

### 3. Link an account

```bash
tally bank accounts enablebanking --country GB
# pick the "id" field, e.g. "HSBC UK|GB"

tally bank link enablebanking --institution "HSBC UK|GB" \
  --redirect-uri "http://127.0.0.1:5000/api/v1/bank/enablebanking/callback"
# open the returned url, authorise at your bank

tally bank callback enablebanking --code "<code from the redirect>" --state "<state>"
tally bank sync
```

The redirect URI must be the URL your bank returns to. For a purely local install
it can be a loopback URL; if your provider rejects that, put Tally behind a small
HTTPS tunnel and use that host instead.

### 4. Activate restricted production

In the Control Panel, use **"Activate by linking accounts"** and link the same
accounts. Without this, a production app returns an empty account list.

### Limits

- Consent lasts up to 90 days; re-link when it expires. `tally bank connections`
  shows `consent_expires_at`.
- Banks rate-limit aggressively — as low as **4 calls/day per account**. Tally
  fetches balances and transactions only when you sync, and never polls on reads.
- History: up to 24 months, depending on the bank.

---

## SimpleFIN Bridge (US / CA)

SimpleFIN is a tiny read-only protocol: no OAuth, no redirect, no certificates.

1. Subscribe at <https://beta-bridge.simplefin.org/> (~$15/year, up to 25 banks).
2. Connect your bank there.
3. Copy the **setup token** it gives you (a base64 blob).
4. Run:

```bash
tally bank callback simplefin --code "<setup token>"
tally bank sync
```

Tally claims the token, stores the access URL encrypted, and imports accounts and
transactions. Revoke access from the SimpleFIN Bridge dashboard; then
`tally bank revoke <connection_id>` locally.

---

## How sync works

`tally bank sync` (or `POST /api/v1/bank/sync`):

1. decrypts the connection credentials (Fernet, key outside the database);
2. fetches accounts and authoritative balances;
3. fetches transactions since the last sync (first run: 90 days);
4. upserts by `(account_id, external_id)`, falling back to a content hash;
5. tags every inserted row `source='bank_sync'` inside an `import_batch`;
6. updates `balance_current` / `balance_available` on the account.

Re-running a sync is a no-op. Balances are displayed from the bank; the ledger is
never rewritten.

Schedule it with cron if you want it automatic:

```cron
0 7 * * * cd /opt/tallylocal && .venv/bin/python -m app.cli bank sync >> /var/log/tally-sync.log 2>&1
```

---

## Credential storage

Bank sessions and SimpleFIN access URLs are encrypted with a key that is **not**
in the database:

- `TALLY_ENCRYPTION_KEY` (urlsafe base64, 32 bytes), or
- `<DATA_DIR>/.encryption_key` — created `0600` on first link.

Back that key up with the database, or connections cannot be decrypted. Rotating
it means re-linking every bank.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Enable Banking is not configured` | Missing app id or key | Set both env vars, restart |
| Empty account list after linking | App not activated in restricted mode | Control Panel → Activate by linking accounts |
| `401` from the provider | JWT rejected | Check the app id matches the key's public half |
| Consent expired | 90-day limit | Re-link and re-authorise |
| `database is locked` | Old install without WAL | Tally enables WAL and a 5s busy timeout on connect |
| SimpleFIN `403` | Token already claimed or revoked | Create a fresh setup token |
| Amounts inverted | Provider sign convention | Tally uses signed amounts: negative = money out |

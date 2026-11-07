<p align="center">
  <img src="https://img.shields.io/badge/Status-Active-success?style=for-the-badge" alt="Status">
  <img src="https://img.shields.io/badge/License-PolyForm_Noncommercial-blue?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/badge/Python-3.10+-yellow?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Agent--Ready-MCP_✓-brightgreen?style=for-the-badge" alt="Agent Ready">
</p>

<h1 align="center">💰 Tally Local</h1>

<p align="center">
  <strong>The personal finance tracker that respects your privacy — and speaks to any agent.</strong><br>
  Local-first. Your data never leaves your device unless you connect a bank.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/No_Cloud-✓-green?style=flat-square" alt="No Cloud">
  <img src="https://img.shields.io/badge/No_Subscriptions-✓-green?style=flat-square" alt="No Subscriptions">
  <img src="https://img.shields.io/badge/No_Tracking-✓-green?style=flat-square" alt="No Tracking">
  <img src="https://img.shields.io/badge/Source_Available-✓-green?style=flat-square" alt="Source Available">
</p>

---

## 🌟 Why Tally Local?

In a world where every finance app wants to connect to your bank, sell your data, or charge monthly fees, **Tally Local** takes a different approach:

- **🔒 Truly Offline** — Your financial data is stored locally in SQLite. No accounts, no servers, no data harvesting.
- **📄 Bank Statement OCR** — Import PDF/image bank statements with intelligent text extraction. No bank API connections needed.
- **💎 Asset Portfolio Tracking** — Track crypto and precious metals with live pricing from free public APIs.
- **📊 Beautiful Analytics** — Comprehensive dashboards, charts, and insights without compromising your privacy.
- **🆓 Forever Free** — No subscriptions, no premium tiers, no "pro" features locked behind paywalls.

---

## ✨ Features

### 💳 Banking & Transactions

| Feature | Description |
|---------|-------------|
| **OCR Statement Import** | Upload PDF or image bank statements. Text-layer PDFs need no OCR; scans go through RapidOCR (PP-OCRv6, CPU) |
| **Multi-Account Support** | Track checking, savings, credit cards, cash, and investment accounts |
| **Smart Categorization** | Auto-categorize transactions with customizable rules |
| **Bulk Editing** | Edit multiple transactions at once with powerful batch operations |
| **Transfer Tracking** | Dual-entry accounting for transfers between accounts |
| **Merchant Recognition** | Automatic merchant detection with logo display |
| **Notes & Tags** | Add personal notes and tags to any transaction |

### 📈 Asset Portfolio Management

| Feature | Description |
|---------|-------------|
| **Cryptocurrency Tracking** | Support for 90+ cryptocurrencies with live CoinGecko pricing |
| **Precious Metals** | Track Gold (XAU), Silver (XAG), Platinum (XPT), and Palladium (XPD) |
| **Wallet Tracking** | Monitor Bitcoin, EVM (Ethereum, Polygon, BSC, etc.), and Solana wallets with historical balances |
| **Auto Wallet Sync** | Wallet balances auto-refresh daily (with manual sync controls when you need them) |
| **Portfolio Analytics** | ROI calculations, gain/loss tracking, and performance metrics |
| **Historical Charts** | Daily portfolio value charts with customizable date ranges (7D, 1M, 3M, 6M, 1Y, All) |
| **Transaction History** | Complete buy/sell/transfer records with cost basis tracking |
| **Custom Assets** | Add untracked assets with manual price entry |

### 📊 Analytics & Insights

| Feature | Description |
|---------|-------------|
| **Interactive Dashboard** | At-a-glance view of your financial health |
| **Spending Breakdown** | Category-wise expense analysis with charts |
| **Income vs Expenses** | Cash flow trends over time |
| **Period Comparisons** | MTD, Last Month, 3 Months, YTD, and custom date ranges |
| **Top Merchants** | See where your money goes most frequently |
| **Budget Tracking** | Set monthly budgets by category with progress indicators |

### 🛡️ Privacy & Data

| Feature | Description |
|---------|-------------|
| **Local SQLite Database** | All data stored on your machine |
| **Full Export/Import** | JSON backup for migration or peace of mind |
| **No Internet Required** | Core functionality works completely offline |
| **No Account Needed** | Just download and run — no sign-ups |

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+ (3.12 recommended)
- pip (Python package manager)

### Installation (Local)

```bash
# Clone the repository
git clone https://github.com/yourusername/tally-local.git
cd tally-local

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Run with Docker

Build the image and start the container (exposes the app on port 5000):

```bash
docker compose up --build
```

This compose file keeps your data in named Docker volumes so user accounts, uploads, and local databases survive container restarts. If you prefer plain Docker commands:

```bash
docker build -t finance-ai .
docker run --rm -p 5000:5000 -p 8765:8765 \
  -e DATA_DIR=/data \
  -e USERS_DB_PATH=/data/users.db \
  -e USER_DATA_DIR=/data/user_data \
  -e UPLOADS_DIR=/data/uploads \
  -e TALLY_MCP_HTTP=1 \
  -e TALLY_MCP_PORT=8765 \
  -v finance_data:/data \
  finance-ai
```

> **Note:** The first time you run inside Docker you still need to create the initial admin account via the setup screen.
>
> Port `5000` is the UI/REST API; port `8765` is MCP over Streamable HTTP.

### Connect an agent over the network

The image runs both services. MCP on `8765` uses the same scoped bearer tokens as
the REST API, so a homelab agent needs no browser session.

1. Open **Settings → AI Agents (MCP)**.
2. Create a token — read-only tokens get an MCP server without mutating tools.
3. Copy the generated config for Claude, Cursor or Hermes. The endpoint is
   derived from the host you used, e.g. `http://192.168.1.50:8765/mcp`.
4. Press **Test** to check the port from the server side.

The skill file is downloadable in the same panel, or install it locally with
`tally skill install --target claude`.

### Updating a hosted install

The image is published to GHCR on every push to `main` as
`ghcr.io/cryptobredda/tallylocal:latest`. Data lives in the `finance_data`
volume, so an update only replaces the container.

Docker (Compose, any host):

```bash
cd /opt/tallylocal
docker compose pull
docker compose up -d
```

Proxmox LXC (installed with the helper script):

```bash
bash -c "$(wget -qLO - https://raw.githubusercontent.com/cryptobredda/tallylocal/main/deploy/install/tallylocal-install.sh)" -- --update
# if the container name is not "tallylocal":
#   … -- --update --ctid 105
```

Your accounts, statements and bank connections survive: they live in `/data`
inside the volume. Databases created by 1.x are migrated in place on first
start — new columns are added and existing rows are left untouched. If you set
`TALLY_ENCRYPTION_KEY`, keep it — without it, bank connections cannot be
decrypted.

### OCR Setup

Tally parses PDF statements from their **text layer** with `pdfplumber` — no OCR
needed for most digital statements. Scanned statements and photos go through
**RapidOCR**, which runs PP-OCRv6 detection + recognition models on CPU via
ONNX Runtime (~31 MB of models, no PyTorch, no PaddlePaddle). It is installed by
`requirements.txt`, so `pip install -r requirements.txt` is enough.

```bash
# what the Docker image runs, and what you get locally
python -c "from app.ocr_service import ocr_service; print(ocr_service.status())"
# {'available': True, 'engine': 'rapidocr', 'name': 'RapidOCR 3.10.0 (PP-OCRv6, ONNX CPU)'}
```

Tesseract remains as a fallback (`apt install tesseract-ocr`); pin the backend
with `TALLY_OCR_ENGINE=rapidocr|tesseract|none`. See
[docs/statements.md](docs/statements.md) for how parsing, profiles and confidence
scoring work.

### Run the Application

```bash
python run.py
```

Open your browser and navigate to `http://127.0.0.1:5000` (the server binds
loopback by default; set `TALLY_HOST`/`TALLY_ALLOW_REMOTE` to change that).

---

### 🔑 Wallet Tracker API Keys

Wallet syncing talks to public blockchain APIs. Bitcoin wallets use Blockchain.com without credentials, but EVM and Solana require free API keys:

- Moralis (EVM chains – Ethereum, Polygon, BSC…). Create a key at [moralis.com](https://moralis.com/).
- Helius (Solana). Create a key at [helius.dev](https://dashboard.helius.dev/).

Add these keys inside the app: sign in as an admin, open the **Admin** tab, and use the **API Integrations** card to paste your keys and toggle each provider on. Keys are stored locally in `integrations.json` under your `DATA_DIR` and never leave your machine. Once saved, head to **Assets → Wallets** and click **Sync** on a wallet to fetch balances and history.

---

## 🔌 Use it from any agent

Tally ships an MCP server, a CLI and a versioned HTTP API over the same ledger.
Any agent that can run a shell command or speak MCP can use it — Claude Desktop,
Hermes, Cursor, Codex CLI, or your own loop.

```bash
# 1. give the agent a scoped token (printed once)
tally token create --name claude --scopes read,write

# 2. print the MCP entry for this machine, then paste it into your client
tally skill mcp-config

# 3. teach the agent when to use it
tally skill install --target claude     # or: hermes, cursor, print

# 4. or just use the CLI
tally context --period 30d
tally tx list --from 2026-10-01 --group_by category
tally import preview statement.pdf
```

Then ask: *"What did I spend on groceries last month, and which subscriptions went up?"*

- [Agent setup: MCP, CLI, API, scopes](docs/agent-setup.md)
- [Bank connections: Enable Banking, SimpleFIN](docs/banks.md)
- [Statement import: formats, profiles, confidence](docs/statements.md)

Safety model: bank rows are immutable, annotations live in a separate table, every
agent write is audited, imports are revertible, and deletes are soft. See the
agent guide for the details.

---

## 📸 Screenshots

### Dashboard
<p align="center">
  <img src="screenshots/dashboard.png" alt="Dashboard" width="800">
</p>

### Transactions
<p align="center">
  <img src="screenshots/transactions.png" alt="Transactions" width="800">
</p>

### Analytics
<p align="center">
  <img src="screenshots/analytics.png" alt="Analytics" width="800">
</p>

### Accounts
<p align="center">
  <img src="screenshots/accounts.png" alt="Accounts" width="800">
</p>

### Budgets
<p align="center">
  <img src="screenshots/budgets.png" alt="Budgets" width="800">
</p>

### Asset Portfolio
<p align="center">
  <img src="screenshots/assets-1.png" alt="Asset Portfolio Overview" width="800">
</p>
<p align="center">
  <img src="screenshots/assets-2.png" alt="Asset Holdings" width="800">
</p>
<p align="center">
  <img src="screenshots/assets-3.png" alt="Asset Analytics" width="800">
</p>

### Statement Import
<p align="center">
  <img src="screenshots/import.png" alt="Statement Import" width="800">
</p>

### Settings
<p align="center">
  <img src="screenshots/settings.png" alt="Settings" width="800">
</p>

---

## 🗺️ Roadmap

We're constantly improving Tally Local. Here's what's coming:

### 🔜 Coming Soon

- [ ] **Receipt Support** — Attach and scan receipts for transaction breakdowns
- [ ] **UI Polish** — Fixing remaining UI bugs and improving responsiveness
- [ ] **Advanced Asset Tracking** — Stocks, ETFs, bonds, and real estate tracking
- [ ] **Crypto Wallet Import** — Track wallets directly via blockchain address
- [ ] **More to come...** — We're just getting started!

### 💡 Under Consideration

- Calendar view with bill reminders
- Recurring transaction management
- Multi-currency support
- Category hierarchies (subcategories)
- Data visualization exports
- Mobile-responsive PWA

---

## 🛠️ Tech Stack

| Component | Technology |
|-----------|------------|
| **Backend** | Python, Flask |
| **Database** | SQLite with SQLAlchemy |
| **Frontend** | HTML, Tailwind CSS, DaisyUI, Chart.js |
| **OCR Engine** | RapidOCR (PP-OCRv6, ONNX, CPU) with Tesseract fallback |
| **Price APIs** | CoinGecko (crypto), Metals.live (precious metals) |

---

## 📁 Project Structure

```
tally-local/
├── app/
│   ├── __init__.py          # Flask app factory
│   ├── models.py             # SQLAlchemy models
│   ├── routes.py             # API endpoints
│   ├── database.py           # Database configuration
│   ├── ocr_service.py        # Bank statement OCR
│   ├── account_service.py    # Account management
│   ├── asset_service.py      # Asset portfolio logic
│   ├── price_service.py      # Live price fetching
│   ├── portfolio_service.py  # Portfolio calculations
│   ├── analytics_service.py  # Analytics computations
│   └── merchant_service.py   # Merchant recognition
├── static/
│   ├── index.html            # Single-page application
│   └── app.js                # Frontend JavaScript
├── uploads/                  # Uploaded statement files
├── requirements.txt          # Python dependencies
├── run.py                    # Application entry point
└── README.md
```

---

## 🤝 Contributing

Contributions are welcome! Feel free to:

- 🐛 Report bugs
- 💡 Suggest features
- 🔧 Submit pull requests

---

## 📄 License

Tally Local is licensed under the **PolyForm Noncommercial License 1.0.0** — free
for personal use, hobby projects, study, charities, schools and government, but
**commercial use requires a paid licence**. See [LICENSE](LICENSE) and
[COMMERCIAL.md](COMMERCIAL.md).

---

## ⭐ Support

If you find Tally Local useful, consider giving it a star on GitHub! It helps others discover the project.

<a href="https://www.buymeacoffee.com/breddadev" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" style="height: 60px !important;width: 217px !important;" ></a>

---

<p align="center">
  <strong>Tally Local</strong> — Your finances, your data, your control.
</p>

<p align="center">
  Made with ❤️ for people who value privacy
</p>

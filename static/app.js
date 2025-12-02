const API = '/api';
let categories = [];
let pots = [];
let accounts = [];
let currentPage = 1;
let perPage = 15;
let sortColumn = 'date';
let sortDirection = 'desc';
let allTransactions = [];
let filteredTransactions = [];
let categoryChart = null;
let cashflowChart = null;
let selectedTransactions = new Set();
let cashflowMonths = 6;
let dashboardPeriod = 'mtd';  // Default: Month to date
let dashboardStartDate = null;
let dashboardEndDate = null;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

document.addEventListener('DOMContentLoaded', () => {
    checkStatus();
    loadCategories();
    loadPots();
    loadDashboard();
    showTab('dashboard');
});

async function checkStatus() {
    try {
        const res = await fetch(`${API}/status`);
        const data = await res.json();
        const statusEl = document.getElementById('ocr-status');
        if (data.ocr_available) {
            statusEl.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-green-400"></span><span class="text-green-400">OCR Ready</span>';
        } else {
            statusEl.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-yellow-400"></span><span class="text-yellow-400">OCR Unavailable</span>';
        }
    } catch (e) {
        console.error('Status check failed:', e);
    }
}

function showTab(tabName) {
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.mobile-nav-item').forEach(b => {
        b.classList.remove('text-accent-yellow');
        b.classList.add('text-gray-500');
    });
    
    document.getElementById(`tab-${tabName}`).classList.add('active');
    
    // Desktop nav
    const btn = document.querySelector(`.nav-item[data-tab="${tabName}"]`);
    if (btn) btn.classList.add('active');
    
    // Mobile nav
    const mobileBtn = document.querySelector(`.mobile-nav-item[data-tab="${tabName}"]`);
    if (mobileBtn) {
        mobileBtn.classList.remove('text-gray-500');
        mobileBtn.classList.add('text-accent-yellow');
    }
    
    if (tabName === 'dashboard') loadDashboard();
    if (tabName === 'transactions') loadTransactions();
    if (tabName === 'pots') loadPots();
    if (tabName === 'analytics') loadAnalytics();
    if (tabName === 'budgets') loadBudgets();
    if (tabName === 'upload') loadStatements();
    if (tabName === 'settings') { loadCategoriesView(); loadRules(); }
    if (tabName === 'assets') loadAssets();
}

async function loadDashboard() {
    try {
        // Calculate date range based on selected period
        const { start, end, label } = getDashboardDateRange();
        
        const [dashRes, summaryRes, trendsRes, txRes] = await Promise.all([
            fetch(`${API}/analytics/dashboard`),
            fetch(`${API}/analytics/summary?start=${start}&end=${end}`),
            fetch(`${API}/analytics/trends?months=${cashflowMonths}`),
            fetch(`${API}/transactions?per_page=6`)
        ]);
        
        const dash = await dashRes.json();
        const summary = await summaryRes.json();
        const trends = await trendsRes.json();
        const transactions = await txRes.json();
        
        // Show the period label
        document.getElementById('period-label').textContent = label;
        
        // Stats from date range summary
        const balanceEl = document.getElementById('stat-balance');
        if (dash.balance && dash.balance.current_balance !== null) {
            balanceEl.textContent = formatCurrencyCompact(dash.balance.current_balance);
        } else {
            balanceEl.textContent = formatCurrencyCompact(summary.net);
        }
        
        document.getElementById('stat-income').textContent = formatCurrencyCompact(summary.total_income);
        document.getElementById('stat-expenses').textContent = formatCurrencyCompact(summary.total_expenses);
        document.getElementById('stat-net').textContent = formatCurrencyCompact(summary.net);
        document.getElementById('stat-pots').textContent = formatCurrencyCompact(dash.totals.total_in_sub_accounts || 0);
        
        // Quick insights - calculate daily avg based on days in the period
        const startDate = new Date(summary.start_date);
        const endDate = new Date(summary.end_date);
        const daysDiff = Math.max(1, Math.ceil((endDate - startDate) / (1000 * 60 * 60 * 24)) + 1);
        const dailyAvg = summary.total_expenses / daysDiff;
        document.getElementById('daily-avg').textContent = formatCurrencyCompact(dailyAvg);
        document.getElementById('tx-count').textContent = summary.transaction_count || 0;
        
        // Category breakdown from summary
        const catBreakdown = summary.category_breakdown || {};
        let biggestCat = { amount: 0, name: '-' };
        Object.entries(catBreakdown).forEach(([name, data]) => {
            if (data.amount > biggestCat.amount) {
                biggestCat = { amount: data.amount, name };
            }
        });
        document.getElementById('biggest-expense').textContent = formatCurrencyCompact(biggestCat.amount);
        document.getElementById('biggest-expense-cat').textContent = biggestCat.name;
        
        const catTotal = Object.values(catBreakdown).reduce((sum, c) => sum + c.amount, 0);
        document.getElementById('category-total').textContent = formatCurrencyCompact(catTotal);
        
        renderCategoryChart(catBreakdown);
        renderCashflowChart(trends.trends);
        renderMerchants(summary.top_merchants || []);
        renderRecentTransactions(transactions.transactions);
    } catch (e) {
        console.error('Dashboard load failed:', e);
    }
}

function getDashboardDateRange() {
    const now = new Date();
    let start, end, label;
    
    if (dashboardPeriod === 'custom' && dashboardStartDate && dashboardEndDate) {
        start = dashboardStartDate;
        end = dashboardEndDate;
        label = `${formatDateShort(start, true)} - ${formatDateShort(end, true)}`;
    } else if (dashboardPeriod === 'mtd') {
        // Month to date: start of current month to today
        start = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
        end = now.toISOString().split('T')[0];
        label = `${MONTHS[now.getMonth()]} 1 - ${now.getDate()}, ${now.getFullYear()}`;
    } else if (dashboardPeriod === 'month') {
        // Full previous month
        const prevMonth = new Date(now.getFullYear(), now.getMonth() - 1, 1);
        const lastDay = new Date(now.getFullYear(), now.getMonth(), 0).getDate();
        start = `${prevMonth.getFullYear()}-${String(prevMonth.getMonth() + 1).padStart(2, '0')}-01`;
        end = `${prevMonth.getFullYear()}-${String(prevMonth.getMonth() + 1).padStart(2, '0')}-${lastDay}`;
        label = `${MONTHS[prevMonth.getMonth()]} ${prevMonth.getFullYear()}`;
    } else if (dashboardPeriod === 'ytd') {
        // Year to date
        start = `${now.getFullYear()}-01-01`;
        end = now.toISOString().split('T')[0];
        label = `Jan 1 - ${MONTHS[now.getMonth()]} ${now.getDate()}, ${now.getFullYear()}`;
    } else if (dashboardPeriod === 'year') {
        // Full previous year
        const prevYear = now.getFullYear() - 1;
        start = `${prevYear}-01-01`;
        end = `${prevYear}-12-31`;
        label = `${prevYear}`;
    } else if (dashboardPeriod === 'all') {
        // All time - use very early start date
        start = '2000-01-01';
        end = now.toISOString().split('T')[0];
        label = 'All Time';
    } else {
        // Default to MTD
        start = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
        end = now.toISOString().split('T')[0];
        label = `${MONTHS[now.getMonth()]} ${now.getFullYear()} MTD`;
    }
    
    return { start, end, label };
}

function formatDateShort(dateStr, includeYear = false) {
    const d = new Date(dateStr);
    if (includeYear) {
        return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
    }
    return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

function setDashboardPeriod(period) {
    dashboardPeriod = period;
    dashboardStartDate = null;
    dashboardEndDate = null;
    
    // Update active button
    document.querySelectorAll('.period-btn').forEach(btn => btn.classList.remove('period-active'));
    document.getElementById(`period-${period}`)?.classList.add('period-active');
    
    loadDashboard();
}

function showCustomPeriodModal() {
    const now = new Date();
    const startOfMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
    const today = now.toISOString().split('T')[0];
    
    document.getElementById('custom-start-date').value = dashboardStartDate || startOfMonth;
    document.getElementById('custom-end-date').value = dashboardEndDate || today;
    
    showModal('custom-period-modal');
}

function applyCustomPeriod() {
    dashboardStartDate = document.getElementById('custom-start-date').value;
    dashboardEndDate = document.getElementById('custom-end-date').value;
    
    if (!dashboardStartDate || !dashboardEndDate) {
        alert('Please select both start and end dates');
        return;
    }
    
    dashboardPeriod = 'custom';
    
    // Update active button
    document.querySelectorAll('.period-btn').forEach(btn => btn.classList.remove('period-active'));
    document.getElementById('period-custom')?.classList.add('period-active');
    
    hideModal('custom-period-modal');
    loadDashboard();
}

function setCashFlowPeriod(months) {
    cashflowMonths = months;
    document.querySelectorAll('[id^="cf-"]').forEach(b => b.classList.remove('cf-active'));
    document.getElementById(`cf-${months}m`).classList.add('cf-active');
    loadDashboard();
}

function renderCategoryChart(breakdown) {
    const ctx = document.getElementById('category-chart').getContext('2d');
    const labels = Object.keys(breakdown);
    const data = labels.map(l => breakdown[l].amount);
    const colors = labels.map(l => breakdown[l].color);
    
    if (categoryChart) categoryChart.destroy();
    
    if (labels.length === 0) {
        ctx.font = '13px Inter';
        ctx.fillStyle = '#666';
        ctx.textAlign = 'center';
        ctx.fillText('No data', ctx.canvas.width / 2, ctx.canvas.height / 2);
        return;
    }
    
    categoryChart = new Chart(ctx, {
        type: 'doughnut',
        data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 0 }] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '70%',
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { color: '#888', padding: 12, usePointStyle: true, pointStyle: 'circle', font: { size: 11 } }
                }
            }
        }
    });
}

function renderCashflowChart(trends) {
    const ctx = document.getElementById('cashflow-chart').getContext('2d');
    
    if (cashflowChart) cashflowChart.destroy();
    
    if (!trends || trends.length === 0) {
        ctx.font = '13px Inter';
        ctx.fillStyle = '#666';
        ctx.textAlign = 'center';
        ctx.fillText('No data', ctx.canvas.width / 2, ctx.canvas.height / 2);
        return;
    }
    
    const labels = trends.map(t => {
        const [year, month] = t.period.split('-');
        return MONTHS[parseInt(month) - 1];
    });
    
    cashflowChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                { label: 'Income', data: trends.map(t => t.income), backgroundColor: '#22c55e', borderRadius: 4 },
                { label: 'Expenses', data: trends.map(t => t.expenses), backgroundColor: '#ef4444', borderRadius: 4 }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { grid: { display: false }, ticks: { color: '#666', font: { size: 11 } } },
                y: { grid: { color: '#22222e' }, ticks: { color: '#666', font: { size: 11 } } }
            },
            plugins: {
                legend: { labels: { color: '#888', usePointStyle: true, pointStyle: 'circle', font: { size: 11 } } }
            }
        }
    });
}

function renderMerchants(merchants) {
    const container = document.getElementById('merchants-list');
    if (!merchants.length) {
        container.innerHTML = '<p class="text-gray-500 text-sm text-center py-4">No data yet</p>';
        return;
    }
    
    const maxAmount = Math.max(...merchants.map(m => m.total_spent));
    container.innerHTML = merchants.map(m => {
        const pct = (m.total_spent / maxAmount) * 100;
        return `
            <div>
                <div class="flex justify-between items-center mb-1">
                    <span class="text-sm truncate flex-1 mr-2">${m.merchant}</span>
                    <span class="text-sm font-medium text-red-400 mono">${formatCurrencyCompact(m.total_spent)}</span>
                </div>
                <div class="h-1.5 bg-surface-300 rounded-full overflow-hidden">
                    <div class="h-full bg-red-400 rounded-full" style="width:${pct}%"></div>
                </div>
            </div>
        `;
    }).join('');
}

function renderRecentTransactions(transactions) {
    const container = document.getElementById('recent-transactions');
    if (!transactions.length) {
        container.innerHTML = '<p class="text-gray-500 text-sm text-center py-4">No transactions</p>';
        return;
    }
    
    container.innerHTML = transactions.map(t => {
        const isIncome = t.type === 'income';
        const color = isIncome ? 'green' : 'red';
        
        // Merchant logo or fallback icon
        let iconHtml;
        if (t.merchant && t.merchant.logo_url) {
            iconHtml = `<img src="${t.merchant.logo_url}" alt="${t.merchant.name}" 
                            class="w-8 h-8 rounded-full object-contain bg-white flex-shrink-0"
                            onerror="this.onerror=null;this.outerHTML='<div class=\\'w-8 h-8 rounded-full bg-${color}-500/20 flex items-center justify-center flex-shrink-0\\'><i class=\\'fas fa-arrow-${isIncome ? 'down' : 'up'} text-${color}-400 text-xs\\'></i></div>';">`;
        } else {
            iconHtml = `<div class="w-8 h-8 rounded-full bg-${color}-500/20 flex items-center justify-center flex-shrink-0">
                <i class="fas fa-arrow-${isIncome ? 'down' : 'up'} text-${color}-400 text-xs"></i>
            </div>`;
        }
        
        return `
            <div class="flex items-center gap-3 py-2 border-b border-surface-300 last:border-0">
                ${iconHtml}
                <div class="flex-1 min-w-0">
                    <p class="text-sm truncate">${t.description}</p>
                    <p class="text-xs text-gray-500">${new Date(t.date).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}</p>
                </div>
                <span class="text-sm font-medium mono text-${color}-400">${isIncome ? '+' : '-'}${formatCurrencyCompact(t.amount)}</span>
            </div>
        `;
    }).join('');
}

async function loadCategories() {
    try {
        const res = await fetch(`${API}/categories`);
        const data = await res.json();
        categories = data.categories;
        
        ['edit-category', 'filter-category', 'rule-category'].forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                const val = el.value;
                el.innerHTML = id === 'filter-category' ? '<option value="">All Categories</option>' : '';
                categories.forEach(c => el.innerHTML += `<option value="${c.id}">${c.name}</option>`);
                if (val) el.value = val;
            }
        });
    } catch (e) {
        console.error('Categories load failed:', e);
    }
}

async function loadCategoriesView() {
    await loadCategories();
    const container = document.getElementById('categories-grid');
    container.innerHTML = categories.map(c => `
        <div class="flex items-center justify-between p-2 rounded-lg bg-surface-200 border border-surface-300">
            <div class="flex items-center gap-2">
                <div class="w-3 h-3 rounded" style="background:${c.color}"></div>
                <span class="text-sm">${c.name}</span>
            </div>
            ${!c.is_system ? `<button onclick="deleteCategory(${c.id})" class="text-red-400 hover:text-red-300 text-xs"><i class="fas fa-times"></i></button>` : ''}
        </div>
    `).join('');
}

async function loadPots() {
    try {
        const [accountsRes, transfersRes] = await Promise.all([
            fetch(`${API}/accounts`),
            fetch(`${API}/transfers?limit=10`)
        ]);
        const accountsData = await accountsRes.json();
        const transfersData = await transfersRes.json();
        
        accounts = accountsData.accounts;
        pots = accounts; // Backward compatibility
        
        // Update edit-pot select (for transaction editing)
        const editPotSelect = document.getElementById('edit-pot');
        if (editPotSelect) {
            editPotSelect.innerHTML = '<option value="">None</option>';
            accounts.forEach(a => editPotSelect.innerHTML += `<option value="${a.id}">${a.name}</option>`);
        }
        
        // Update filter select
        const filterPotSelect = document.getElementById('filter-pot');
        if (filterPotSelect) {
            const val = filterPotSelect.value;
            filterPotSelect.innerHTML = '<option value="">All Accounts</option><option value="none">No Account</option>';
            accounts.forEach(a => filterPotSelect.innerHTML += `<option value="${a.id}">${a.name}</option>`);
            if (val) filterPotSelect.value = val;
        }
        
        // Update upload account select (default to main account)
        const uploadAccountSelect = document.getElementById('upload-account');
        if (uploadAccountSelect) {
            uploadAccountSelect.innerHTML = accounts.map(a => 
                `<option value="${a.id}" ${a.is_main ? 'selected' : ''}>${a.name}${a.is_main ? ' (Main)' : ''}</option>`
            ).join('');
        }
        
        renderAccountsView(transfersData.transfers || []);
    } catch (e) {
        console.error('Accounts load failed:', e);
    }
}

function renderAccountsView(transfers) {
    const mainAccount = accounts.find(a => a.is_main);
    const subAccounts = accounts.filter(a => !a.is_main);
    
    // Main Account Card
    if (mainAccount) {
        document.getElementById('main-account-name').textContent = mainAccount.name;
        document.getElementById('main-account-balance').textContent = formatCurrency(mainAccount.balance);
        document.getElementById('main-account-in').textContent = '+' + formatCurrencyCompact(mainAccount.total_in);
        document.getElementById('main-account-out').textContent = '-' + formatCurrencyCompact(mainAccount.total_out);
    }
    
    // Sub Accounts Total
    const subTotal = subAccounts.reduce((sum, a) => sum + a.balance, 0);
    document.getElementById('sub-accounts-total').textContent = subAccounts.length > 0 ? `Total: ${formatCurrency(subTotal)}` : '';
    
    // Sub Accounts Grid
    const container = document.getElementById('accounts-grid');
    if (!subAccounts.length) {
        container.innerHTML = `
            <div class="col-span-full text-center py-8">
                <div class="w-12 h-12 rounded-xl bg-purple-500/20 flex items-center justify-center mx-auto mb-3">
                    <i class="fas fa-wallet text-purple-400"></i>
                </div>
                <h3 class="font-semibold text-sm mb-1">No sub-accounts</h3>
                <p class="text-xs text-gray-500 mb-3">Create accounts to organize your money</p>
                <button onclick="showCreateAccountModal()" class="btn-nb btn-nb-primary px-3 py-1.5 text-xs">Create Account</button>
            </div>
        `;
    } else {
        container.innerHTML = subAccounts.map(a => `
            <div class="card-nb p-4">
                <div class="flex items-center gap-3 mb-3">
                    <div class="w-10 h-10 rounded-lg flex items-center justify-center" style="background:${a.color}20">
                        <i class="fas fa-${a.icon || 'wallet'}" style="color:${a.color}"></i>
                    </div>
                    <div class="flex-1 min-w-0">
                        <h4 class="font-semibold text-sm truncate">${a.name}</h4>
                        ${a.description ? `<p class="text-xs text-gray-500 truncate">${a.description}</p>` : ''}
                    </div>
                </div>
                <div class="mb-3">
                    <span class="text-xl font-bold mono" style="color:${a.color}">${formatCurrency(a.balance)}</span>
                    <div class="flex gap-2 text-xs text-gray-500 mt-1">
                        <span class="text-green-400">+${formatCurrencyCompact(a.total_in)}</span>
                        <span class="text-red-400">-${formatCurrencyCompact(a.total_out)}</span>
                    </div>
                </div>
                <div class="flex gap-2">
                    <button onclick="showTransferModalTo(${a.id})" class="btn-nb btn-nb-secondary flex-1 py-1.5 text-xs">Transfer</button>
                    <button onclick="deleteAccount(${a.id})" class="btn-nb bg-red-500/20 text-red-400 px-3 py-1.5 text-xs"><i class="fas fa-trash"></i></button>
                </div>
            </div>
        `).join('');
    }
    
    // Recent Transfers
    const transfersList = document.getElementById('transfers-list');
    if (!transfers.length) {
        transfersList.innerHTML = '<p class="text-gray-500 text-sm text-center py-4">No transfers yet</p>';
    } else {
        transfersList.innerHTML = transfers.map(t => `
            <div class="flex items-center justify-between py-2 border-b border-surface-300 last:border-0">
                <div class="flex items-center gap-3 min-w-0">
                    <div class="w-8 h-8 rounded-lg bg-cyan-500/20 flex items-center justify-center flex-shrink-0">
                        <i class="fas fa-exchange-alt text-cyan-400 text-xs"></i>
                    </div>
                    <div class="min-w-0">
                        <p class="text-sm truncate">${t.from_account?.name || 'Unknown'} → ${t.to_account?.name || 'Unknown'}</p>
                        <p class="text-xs text-gray-500">${new Date(t.date).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })}</p>
                    </div>
                </div>
                <span class="text-sm mono font-medium text-cyan-400">${formatCurrencyCompact(t.amount)}</span>
            </div>
        `).join('');
    }
}

async function loadTransactions() {
    const categoryId = document.getElementById('filter-category').value;
    const typeFilter = document.getElementById('filter-type').value;
    const potFilter = document.getElementById('filter-pot')?.value || '';
    const startDate = document.getElementById('filter-start').value;
    const endDate = document.getElementById('filter-end').value;
    const searchQuery = document.getElementById('search-input').value.toLowerCase().trim();
    
    let url = `${API}/transactions?page=1&per_page=10000`;
    if (categoryId) url += `&category_id=${categoryId}`;
    if (startDate) url += `&start_date=${startDate}`;
    if (endDate) url += `&end_date=${endDate}`;
    
    try {
        const res = await fetch(url);
        const data = await res.json();
        allTransactions = data.transactions;
        
        filteredTransactions = allTransactions.filter(t => {
            if (typeFilter && t.type !== typeFilter) return false;
            if (potFilter) {
                if (potFilter === 'none' && t.pot_id) return false;
                if (potFilter !== 'none' && t.pot_id != potFilter) return false;
            }
            if (searchQuery) {
                const fields = [t.description, t.category_name, t.pot_name, formatCurrency(t.amount)].filter(Boolean).join(' ').toLowerCase();
                if (!fields.includes(searchQuery)) return false;
            }
            return true;
        });
        
        sortTransactions();
        
        const totalItems = filteredTransactions.length;
        const totalPages = Math.ceil(totalItems / perPage);
        if (currentPage > totalPages) currentPage = Math.max(1, totalPages);
        
        const startIndex = (currentPage - 1) * perPage;
        const endIndex = startIndex + perPage;
        const pageTransactions = filteredTransactions.slice(startIndex, endIndex);
        
        updateTransactionsInfo(totalItems, startIndex, endIndex, allTransactions.length);
        renderTransactionsTable(pageTransactions);
        renderPagination({ pages: totalPages, total: totalItems });
        updateSortIcons();
    } catch (e) {
        console.error('Transactions load failed:', e);
    }
}

function sortTransactions() {
    filteredTransactions.sort((a, b) => {
        let aVal, bVal;
        switch (sortColumn) {
            case 'date': aVal = new Date(a.date).getTime(); bVal = new Date(b.date).getTime(); break;
            case 'description': aVal = a.description.toLowerCase(); bVal = b.description.toLowerCase(); break;
            case 'category': aVal = (a.category_name || 'zzz').toLowerCase(); bVal = (b.category_name || 'zzz').toLowerCase(); break;
            case 'amount': aVal = a.type === 'income' ? a.amount : -a.amount; bVal = b.type === 'income' ? b.amount : -b.amount; break;
            default: return 0;
        }
        if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
        if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
        return 0;
    });
}

function sortBy(column) {
    if (sortColumn === column) {
        sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
    } else {
        sortColumn = column;
        sortDirection = column === 'date' ? 'desc' : 'asc';
    }
    currentPage = 1;
    loadTransactions();
}

function updateSortIcons() {
    ['date', 'description', 'category', 'amount'].forEach(col => {
        const icon = document.getElementById(`sort-icon-${col}`);
        if (icon) {
            if (col === sortColumn) {
                icon.className = 'text-accent-yellow ml-1';
                icon.innerHTML = sortDirection === 'asc' ? '<i class="fas fa-sort-up"></i>' : '<i class="fas fa-sort-down"></i>';
            } else {
                icon.className = 'text-gray-600 ml-1';
                icon.innerHTML = '<i class="fas fa-sort"></i>';
            }
        }
    });
}

function updateTransactionsInfo(filtered, start, end, total) {
    const el = document.getElementById('transactions-info');
    const exportEl = document.getElementById('export-filtered-count');
    const showing = Math.min(end, filtered);
    el.textContent = filtered === total ? `${filtered} transactions` : `${filtered} of ${total}`;
    if (exportEl) exportEl.textContent = filtered;
}

function renderTransactionsTable(transactions) {
    const tbody = document.getElementById('transactions-table');
    if (!transactions.length) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center py-8 text-gray-500">No transactions</td></tr>`;
        return;
    }
    
    tbody.innerHTML = transactions.map(t => {
        const isIncome = t.type === 'income';
        const color = isIncome ? 'green' : 'red';
        const isSelected = selectedTransactions.has(t.id);
        
        // Merchant logo or fallback icon
        let iconHtml;
        if (t.merchant && t.merchant.logo_url) {
            iconHtml = `<img src="${t.merchant.logo_url}" alt="${t.merchant.name}" 
                            class="w-5 h-5 flex-shrink-0 rounded object-contain bg-white"
                            onerror="this.onerror=null;this.parentElement.innerHTML='<span class=\\'w-5 h-5 flex-shrink-0 rounded flex items-center justify-center bg-${color}-500/20\\'><i class=\\'fas fa-arrow-${isIncome ? 'down' : 'up'} text-${color}-400 text-[10px]\\'></i></span>';">`;
        } else {
            iconHtml = `<span class="w-5 h-5 flex-shrink-0 rounded flex items-center justify-center bg-${color}-500/20">
                <i class="fas fa-arrow-${isIncome ? 'down' : 'up'} text-${color}-400 text-[10px]"></i>
            </span>`;
        }
        
        return `
            <tr class="${isSelected ? 'bg-accent-yellow/10' : ''}">
                <td>
                    <input type="checkbox" class="checkbox checkbox-sm" ${isSelected ? 'checked' : ''} 
                           onchange="toggleTransactionSelect(${t.id})">
                </td>
                <td class="mono text-gray-400">${new Date(t.date).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })}</td>
                <td class="max-w-0 w-full">
                    <div class="flex items-center gap-2">
                        ${iconHtml}
                        <span class="truncate">${highlightSearch(t.description)}</span>
                    </div>
                </td>
                <td class="hidden sm:table-cell">
                    <span class="badge-nb" style="background:${t.category_color}20;color:${t.category_color}">${t.category_name || 'Uncategorized'}</span>
                </td>
                <td class="text-right font-medium mono text-${color}-400">${isIncome ? '+' : '-'}${formatCurrencyCompact(t.amount)}</td>
                <td>
                    <div class="flex gap-1 justify-end">
                        <button onclick="editTransaction(${t.id})" class="btn-nb btn-nb-secondary p-1.5 text-xs"><i class="fas fa-edit"></i></button>
                        <button onclick="deleteTransaction(${t.id})" class="btn-nb bg-red-500/20 text-red-400 p-1.5 text-xs"><i class="fas fa-trash"></i></button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
    
    updateBulkEditBar();
}

function highlightSearch(text) {
    const query = document.getElementById('search-input').value.trim();
    if (!query) return text;
    const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    return text.replace(regex, '<mark class="bg-yellow-500/30 px-0.5 rounded">$1</mark>');
}

function handleSearch(event) {
    if (event.key === 'Enter') { currentPage = 1; loadTransactions(); }
}

function changePerPage() {
    perPage = parseInt(document.getElementById('filter-per-page').value);
    currentPage = 1;
    loadTransactions();
}

function setQuickFilter(period) {
    const now = new Date();
    let start, end = now.toISOString().split('T')[0];
    switch (period) {
        case 'today': start = end; break;
        case 'week':
            const ws = new Date(now); ws.setDate(now.getDate() - now.getDay());
            start = ws.toISOString().split('T')[0]; break;
        case 'month': start = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().split('T')[0]; break;
        case 'year': start = new Date(now.getFullYear(), 0, 1).toISOString().split('T')[0]; break;
    }
    document.getElementById('filter-start').value = start;
    document.getElementById('filter-end').value = end;
    currentPage = 1;
    loadTransactions();
}

function clearFilters() {
    document.getElementById('filter-category').value = '';
    document.getElementById('filter-type').value = '';
    if (document.getElementById('filter-pot')) document.getElementById('filter-pot').value = '';
    document.getElementById('filter-start').value = '';
    document.getElementById('filter-end').value = '';
    document.getElementById('search-input').value = '';
    document.getElementById('filter-per-page').value = '15';
    perPage = 15; sortColumn = 'date'; sortDirection = 'desc'; currentPage = 1;
    loadTransactions();
}

function renderPagination(data) {
    const container = document.getElementById('transactions-pagination');
    const pageInfo = document.getElementById('transactions-page-info');
    if (pageInfo) pageInfo.textContent = `Page ${currentPage}/${data.pages || 1}`;
    
    if (data.pages <= 1) { container.innerHTML = ''; return; }
    
    let html = '';
    if (currentPage > 1) html += `<button onclick="goToPage(${currentPage - 1})" class="btn-nb btn-nb-secondary px-2 py-1 text-xs"><i class="fas fa-chevron-left"></i></button>`;
    
    const start = Math.max(1, currentPage - 2);
    const end = Math.min(data.pages, currentPage + 2);
    for (let i = start; i <= end; i++) {
        html += `<button onclick="goToPage(${i})" class="btn-nb ${i === currentPage ? 'btn-nb-primary' : 'btn-nb-secondary'} px-2 py-1 text-xs">${i}</button>`;
    }
    
    if (currentPage < data.pages) html += `<button onclick="goToPage(${currentPage + 1})" class="btn-nb btn-nb-secondary px-2 py-1 text-xs"><i class="fas fa-chevron-right"></i></button>`;
    container.innerHTML = html;
}

function goToPage(page) { currentPage = page; loadTransactions(); }

// Analytics state
let analyticsPeriod = 'mtd';
let analyticsStartDate = null;
let analyticsEndDate = null;
let analyticsTrendChart = null;
let analyticsPieChart = null;
let analyticsMonthlyChart = null;
let analyticsDowChart = null;

function getAnalyticsDateRange() {
    const now = new Date();
    let start, end, label;
    
    if (analyticsPeriod === 'custom' && analyticsStartDate && analyticsEndDate) {
        start = analyticsStartDate;
        end = analyticsEndDate;
        label = `${formatDateShort(start, true)} - ${formatDateShort(end, true)}`;
    } else if (analyticsPeriod === 'mtd') {
        start = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
        end = now.toISOString().split('T')[0];
        label = `Month to Date (${MONTHS[now.getMonth()]} ${now.getFullYear()})`;
    } else if (analyticsPeriod === 'month') {
        // Previous complete month
        const prevMonth = new Date(now.getFullYear(), now.getMonth() - 1, 1);
        const lastDayOfPrevMonth = new Date(prevMonth.getFullYear(), prevMonth.getMonth() + 1, 0).getDate();
        start = `${prevMonth.getFullYear()}-${String(prevMonth.getMonth() + 1).padStart(2, '0')}-01`;
        end = `${prevMonth.getFullYear()}-${String(prevMonth.getMonth() + 1).padStart(2, '0')}-${String(lastDayOfPrevMonth).padStart(2, '0')}`;
        label = `${MONTHS[prevMonth.getMonth()]} ${prevMonth.getFullYear()}`;
    } else if (analyticsPeriod === '3month') {
        const threeMonthsAgo = new Date(now.getFullYear(), now.getMonth() - 3, 1);
        start = `${threeMonthsAgo.getFullYear()}-${String(threeMonthsAgo.getMonth() + 1).padStart(2, '0')}-01`;
        end = now.toISOString().split('T')[0];
        label = `Last 3 Months`;
    } else if (analyticsPeriod === 'ytd') {
        start = `${now.getFullYear()}-01-01`;
        end = now.toISOString().split('T')[0];
        label = `Year to Date (${now.getFullYear()})`;
    } else if (analyticsPeriod === 'year') {
        const prevYear = now.getFullYear() - 1;
        start = `${prevYear}-01-01`;
        end = `${prevYear}-12-31`;
        label = `${prevYear}`;
    } else if (analyticsPeriod === 'all') {
        start = '2000-01-01';
        end = now.toISOString().split('T')[0];
        label = 'All Time';
    } else {
        start = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
        end = now.toISOString().split('T')[0];
        label = `Month to Date`;
    }
    
    return { start, end, label };
}

function setAnalyticsPeriod(period) {
    analyticsPeriod = period;
    analyticsStartDate = null;
    analyticsEndDate = null;
    
    document.querySelectorAll('.analytics-period-btn').forEach(btn => btn.classList.remove('analytics-period-active'));
    document.getElementById(`analytics-${period}`)?.classList.add('analytics-period-active');
    
    // Update date inputs
    const { start, end } = getAnalyticsDateRange();
    document.getElementById('analytics-start').value = start;
    document.getElementById('analytics-end').value = end;
    
    loadAnalytics();
}

function applyAnalyticsCustomDate() {
    const start = document.getElementById('analytics-start').value;
    const end = document.getElementById('analytics-end').value;
    
    if (start && end) {
        analyticsPeriod = 'custom';
        analyticsStartDate = start;
        analyticsEndDate = end;
        
        document.querySelectorAll('.analytics-period-btn').forEach(btn => btn.classList.remove('analytics-period-active'));
        loadAnalytics();
    }
}

async function loadAnalytics() {
    try {
        const { start, end, label } = getAnalyticsDateRange();
        
        // Update date inputs to reflect current selection
        document.getElementById('analytics-start').value = start;
        document.getElementById('analytics-end').value = end;
        document.getElementById('analytics-period-label').textContent = label;
        
        const res = await fetch(`${API}/analytics/comprehensive?start=${start}&end=${end}`);
        const data = await res.json();
        
        // Update summary metrics
        document.getElementById('analytics-income').textContent = formatCurrency(data.summary.total_income);
        document.getElementById('analytics-expenses').textContent = formatCurrency(data.summary.total_expenses);
        document.getElementById('analytics-net').textContent = formatCurrency(data.summary.net);
        document.getElementById('analytics-net').className = `text-xl font-bold mono ${data.summary.net >= 0 ? 'text-green-400' : 'text-red-400'}`;
        document.getElementById('analytics-savings-rate').textContent = `${data.summary.savings_rate}%`;
        document.getElementById('analytics-savings-rate').className = `text-xl font-bold mono ${data.summary.savings_rate >= 0 ? 'text-green-400' : 'text-red-400'}`;
        document.getElementById('analytics-tx-count').textContent = data.summary.transaction_count;
        document.getElementById('analytics-daily-avg').textContent = formatCurrency(data.summary.daily_average);
        
        // Update statistics
        document.getElementById('analytics-avg-expense').textContent = formatCurrency(data.statistics.avg_expense);
        document.getElementById('analytics-max-expense').textContent = formatCurrency(data.statistics.max_expense);
        document.getElementById('analytics-min-expense').textContent = formatCurrency(data.statistics.min_expense);
        document.getElementById('analytics-cats-used').textContent = data.statistics.categories_used;
        document.getElementById('analytics-per-day').textContent = formatCurrency(data.statistics.expense_per_day);
        
        if (data.statistics.most_expensive_day) {
            const expDay = new Date(data.statistics.most_expensive_day);
            document.getElementById('analytics-expensive-day').textContent = 
                `${expDay.getDate()} ${MONTHS[expDay.getMonth()]} (${formatCurrencyCompact(data.statistics.most_expensive_day_amount)})`;
        } else {
            document.getElementById('analytics-expensive-day').textContent = '-';
        }
        
        // Render charts
        renderAnalyticsTrendChart(data.monthly_trends);
        renderAnalyticsPieChart(data.category_breakdown);
        renderAnalyticsMonthlyChart(data.monthly_trends);
        renderAnalyticsDowChart(data.day_of_week);
        
        // Render category breakdown list
        renderAnalyticsCategoryBreakdown(data.category_breakdown, data.summary.total_expenses);
        
        // Render top merchants
        renderAnalyticsTopMerchants(data.top_merchants, data.summary.total_expenses);
        
        // Render recurring
        renderAnalyticsRecurring(data.recurring);
        
    } catch (e) {
        console.error('Analytics load failed:', e);
    }
}

function renderAnalyticsTrendChart(trends) {
    const ctx = document.getElementById('analytics-trend-chart');
    if (!ctx) return;
    
    if (analyticsTrendChart) analyticsTrendChart.destroy();
    
    analyticsTrendChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: trends.map(t => t.period),
            datasets: [
                {
                    label: 'Income',
                    data: trends.map(t => t.income),
                    borderColor: '#22c55e',
                    backgroundColor: '#22c55e20',
                    fill: true,
                    tension: 0.3
                },
                {
                    label: 'Expenses',
                    data: trends.map(t => t.expenses),
                    borderColor: '#ef4444',
                    backgroundColor: '#ef444420',
                    fill: true,
                    tension: 0.3
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { labels: { color: '#888' } } },
            scales: {
                x: { grid: { color: '#22222e' }, ticks: { color: '#666' } },
                y: { grid: { color: '#22222e' }, ticks: { color: '#666' } }
            }
        }
    });
}

function renderAnalyticsPieChart(categories) {
    const ctx = document.getElementById('analytics-pie-chart');
    if (!ctx) return;
    
    if (analyticsPieChart) analyticsPieChart.destroy();
    
    const sorted = Object.entries(categories).sort((a, b) => b[1].amount - a[1].amount).slice(0, 8);
    
    analyticsPieChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: sorted.map(([name]) => name),
            datasets: [{
                data: sorted.map(([, data]) => data.amount),
                backgroundColor: sorted.map(([, data]) => data.color),
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'right',
                    labels: { color: '#888', font: { size: 11 }, boxWidth: 12 }
                }
            }
        }
    });
}

function renderAnalyticsMonthlyChart(trends) {
    const ctx = document.getElementById('analytics-monthly-chart');
    if (!ctx) return;
    
    if (analyticsMonthlyChart) analyticsMonthlyChart.destroy();
    
    analyticsMonthlyChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: trends.map(t => t.period),
            datasets: [
                {
                    label: 'Income',
                    data: trends.map(t => t.income),
                    backgroundColor: '#22c55e'
                },
                {
                    label: 'Expenses',
                    data: trends.map(t => t.expenses),
                    backgroundColor: '#ef4444'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { labels: { color: '#888' } } },
            scales: {
                x: { grid: { display: false }, ticks: { color: '#666' } },
                y: { grid: { color: '#22222e' }, ticks: { color: '#666' } }
            }
        }
    });
}

function renderAnalyticsDowChart(dayOfWeek) {
    const ctx = document.getElementById('analytics-dow-chart');
    if (!ctx) return;
    
    if (analyticsDowChart) analyticsDowChart.destroy();
    
    const maxAmount = Math.max(...dayOfWeek.map(d => d.amount));
    
    analyticsDowChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: dayOfWeek.map(d => d.day),
            datasets: [{
                data: dayOfWeek.map(d => d.amount),
                backgroundColor: dayOfWeek.map(d => d.amount === maxAmount ? '#facc15' : '#3b82f6'),
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { grid: { display: false }, ticks: { color: '#666' } },
                y: { grid: { color: '#22222e' }, ticks: { color: '#666' } }
            }
        }
    });
}

function renderAnalyticsCategoryBreakdown(categories, total) {
    const container = document.getElementById('analytics-category-breakdown');
    if (!container) return;
    
    const sorted = Object.entries(categories).sort((a, b) => b[1].amount - a[1].amount);
    
    if (sorted.length === 0) {
        container.innerHTML = '<p class="text-gray-500 text-sm text-center py-4">No data for this period</p>';
        return;
    }
    
    container.innerHTML = sorted.map(([name, data]) => {
        const pct = total > 0 ? (data.amount / total) * 100 : 0;
        return `
            <div>
                <div class="flex justify-between items-center mb-1">
                    <div class="flex items-center gap-2">
                        <div class="w-3 h-3 rounded" style="background:${data.color}"></div>
                        <span class="text-sm">${name}</span>
                        <span class="text-xs text-gray-500">(${data.count})</span>
                    </div>
                    <div class="text-right">
                        <span class="text-sm mono font-medium">${formatCurrency(data.amount)}</span>
                        <span class="text-xs text-gray-500 ml-1">${pct.toFixed(1)}%</span>
                    </div>
                </div>
                <div class="h-2 bg-surface-300 rounded-full overflow-hidden">
                    <div class="h-full rounded-full transition-all" style="width:${pct}%;background:${data.color}"></div>
                </div>
            </div>
        `;
    }).join('');
}

function renderAnalyticsTopMerchants(merchants, total) {
    const container = document.getElementById('analytics-top-merchants');
    if (!container) return;
    
    if (merchants.length === 0) {
        container.innerHTML = '<p class="text-gray-500 text-sm text-center py-4">No data for this period</p>';
        return;
    }
    
    const maxAmount = Math.max(...merchants.map(m => m.total_spent));
    
    container.innerHTML = merchants.map((m, i) => {
        const pct = maxAmount > 0 ? (m.total_spent / maxAmount) * 100 : 0;
        const totalPct = total > 0 ? (m.total_spent / total) * 100 : 0;
        return `
            <div>
                <div class="flex justify-between items-center mb-1">
                    <div class="flex items-center gap-2">
                        <span class="w-5 h-5 rounded bg-surface-300 flex items-center justify-center text-xs text-gray-400">${i + 1}</span>
                        <span class="text-sm truncate max-w-[150px]">${m.merchant}</span>
                        <span class="text-xs text-gray-500">(${m.transaction_count}x)</span>
                    </div>
                    <div class="text-right">
                        <span class="text-sm mono font-medium">${formatCurrency(m.total_spent)}</span>
                        <span class="text-xs text-gray-500 ml-1">${totalPct.toFixed(1)}%</span>
                    </div>
                </div>
                <div class="h-1.5 bg-surface-300 rounded-full overflow-hidden">
                    <div class="h-full rounded-full bg-blue-500 transition-all" style="width:${pct}%"></div>
                </div>
            </div>
        `;
    }).join('');
}

function renderAnalyticsRecurring(recurring) {
    const container = document.getElementById('analytics-recurring');
    if (!container) return;
    
    if (recurring.length === 0) {
        container.innerHTML = '<p class="text-gray-500 text-sm text-center py-4">No recurring payments detected</p>';
        return;
    }
    
    container.innerHTML = recurring.map(r => `
        <div class="flex justify-between items-center py-2 border-b border-surface-300 last:border-0">
            <div class="flex-1 min-w-0">
                <p class="text-sm truncate">${r.description}</p>
                <p class="text-xs text-gray-500">${r.occurrences}x occurrences</p>
            </div>
            <span class="text-sm mono font-medium ml-2">${formatCurrency(r.avg_amount)}</span>
        </div>
    `).join('');
}

async function editTransaction(id) {
    try {
        await loadPots();
        const res = await fetch(`${API}/transactions?page=1&per_page=10000`);
        const data = await res.json();
        const tx = data.transactions.find(t => t.id === id);
        
        if (tx) {
            document.getElementById('edit-tx-id').value = tx.id;
            document.getElementById('edit-tx-type').value = tx.type;
            document.getElementById('edit-description').value = tx.description;
            document.getElementById('edit-category').value = tx.category_id || '';
            document.getElementById('edit-pot').value = tx.pot_id || '';
            document.getElementById('edit-one-off').checked = tx.is_one_off;
            
            // Populate receiving account dropdown
            populateReceivingAccountDropdown(tx.pot_id);
            
            // Check if category is "Transfers" and show/hide receiving account
            onEditCategoryChange();
            
            showModal('edit-modal');
        }
    } catch (e) { console.error('Edit failed:', e); }
}

function populateReceivingAccountDropdown(excludeAccountId) {
    const select = document.getElementById('edit-receiving-account');
    select.innerHTML = '<option value="">External (not tracked)</option>';
    
    pots.forEach(p => {
        if (p.id !== excludeAccountId) {
            select.innerHTML += `<option value="${p.id}">${p.name}</option>`;
        }
    });
}

function onEditCategoryChange() {
    const categoryId = document.getElementById('edit-category').value;
    const container = document.getElementById('edit-receiving-account-container');
    
    // Find if selected category is "Transfers"
    const selectedCategory = categories.find(c => c.id === parseInt(categoryId));
    const isTransfer = selectedCategory && selectedCategory.name.toLowerCase() === 'transfers';
    
    if (isTransfer) {
        container.classList.remove('hidden');
        // Update receiving account options based on current source account
        const sourceAccountId = parseInt(document.getElementById('edit-pot').value) || null;
        populateReceivingAccountDropdown(sourceAccountId);
    } else {
        container.classList.add('hidden');
    }
}

function onEditSourceAccountChange() {
    // Update receiving account dropdown to exclude the selected source account
    const sourceAccountId = parseInt(document.getElementById('edit-pot').value) || null;
    const currentReceiving = document.getElementById('edit-receiving-account').value;
    populateReceivingAccountDropdown(sourceAccountId);
    
    // Restore selection if still valid
    if (currentReceiving && parseInt(currentReceiving) !== sourceAccountId) {
        document.getElementById('edit-receiving-account').value = currentReceiving;
    }
}

async function saveTransaction() {
    const id = document.getElementById('edit-tx-id').value;
    const categoryId = parseInt(document.getElementById('edit-category').value) || null;
    const sourceAccountId = document.getElementById('edit-pot').value ? parseInt(document.getElementById('edit-pot').value) : null;
    const receivingAccountId = document.getElementById('edit-receiving-account').value ? parseInt(document.getElementById('edit-receiving-account').value) : null;
    const txType = document.getElementById('edit-tx-type').value;
    
    // Check if this is a transfer
    const selectedCategory = categories.find(c => c.id === categoryId);
    const isTransfer = selectedCategory && selectedCategory.name.toLowerCase() === 'transfers';
    
    const data = {
        description: document.getElementById('edit-description').value,
        category_id: categoryId,
        pot_id: sourceAccountId,
        is_one_off: document.getElementById('edit-one-off').checked
    };
    
    // Add transfer info if applicable
    if (isTransfer && receivingAccountId) {
        data.receiving_account_id = receivingAccountId;
        data.is_transfer = true;
    }
    
    try {
        await fetch(`${API}/transactions/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
        hideModal('edit-modal');
        loadTransactions();
        loadPots();
        loadDashboard();
    } catch (e) { console.error('Save failed:', e); }
}

async function deleteTransaction(id) {
    if (!confirm('Delete this transaction?')) return;
    try {
        await fetch(`${API}/transactions/${id}`, { method: 'DELETE' });
        loadTransactions();
        loadDashboard();
    } catch (e) { console.error('Delete failed:', e); }
}

async function loadStatements() {
    try {
        const res = await fetch(`${API}/statements`);
        const data = await res.json();
        
        const container = document.getElementById('statements-list');
        const pendingBtn = document.getElementById('process-pending-btn');
        
        if (!data.statements.length) {
            container.innerHTML = '<p class="text-gray-500 text-sm text-center py-4">No statements</p>';
            if (pendingBtn) pendingBtn.classList.add('hidden');
            return;
        }
        
        const pending = data.statements.filter(s => !s.processed && !s.error_message);
        if (pendingBtn) {
            if (pending.length > 0) {
                pendingBtn.classList.remove('hidden');
                pendingBtn.innerHTML = `Process ${pending.length}`;
            } else {
                pendingBtn.classList.add('hidden');
            }
        }
        
        container.innerHTML = data.statements.map(s => `
            <div class="flex items-center justify-between p-3 rounded-lg bg-surface-200 border border-surface-300">
                <div class="flex items-center gap-3 min-w-0">
                    <div class="w-8 h-8 rounded bg-orange-500/20 flex items-center justify-center flex-shrink-0">
                        <i class="fas fa-file-pdf text-orange-400 text-sm"></i>
                    </div>
                    <div class="min-w-0">
                        <p class="text-sm font-medium truncate">${s.filename}</p>
                        <p class="text-xs text-gray-500">${s.transactions_count || 0} txns • ${new Date(s.uploaded_at).toLocaleDateString('en-GB')}</p>
                    </div>
                </div>
                <div class="flex-shrink-0">
                    ${s.processed ? 
                        '<span class="badge-nb bg-green-500/20 text-green-400">Done</span>' :
                        s.error_message ?
                            '<span class="badge-nb bg-red-500/20 text-red-400">Error</span>' :
                            `<button onclick="processStatement(${s.id})" class="btn-nb btn-nb-primary px-3 py-1 text-xs">Process</button>`
                    }
                </div>
            </div>
        `).join('');
    } catch (e) { console.error('Statements load failed:', e); }
}

async function uploadFiles(files) {
    if (!files || files.length === 0) return;
    
    const queueDiv = document.getElementById('upload-queue');
    const itemsDiv = document.getElementById('upload-items');
    const summarySpan = document.getElementById('upload-summary');
    const processBtn = document.getElementById('process-all-btn');
    
    queueDiv.classList.remove('hidden');
    itemsDiv.innerHTML = '';
    processBtn.classList.add('hidden');
    
    const formData = new FormData();
    for (let f of files) formData.append('files', f);
    
    for (let i = 0; i < files.length; i++) {
        itemsDiv.innerHTML += `<div class="flex items-center gap-2 p-2 bg-surface-200 rounded"><span class="loading loading-spinner loading-xs"></span><span class="text-sm truncate">${files[i].name}</span></div>`;
    }
    summarySpan.textContent = `Uploading ${files.length}...`;
    
    try {
        const res = await fetch(`${API}/upload`, { method: 'POST', body: formData });
        const data = await res.json();
        
        itemsDiv.innerHTML = data.results.map(r => {
            const status = r.status === 'uploaded' ? 'bg-green-500/20 text-green-400' : r.status === 'duplicate' ? 'bg-yellow-500/20 text-yellow-400' : 'bg-red-500/20 text-red-400';
            return `<div class="flex items-center gap-2 p-2 bg-surface-200 rounded"><span class="badge-nb ${status}">${r.status}</span><span class="text-sm truncate">${r.original_filename || r.filename}</span></div>`;
        }).join('');
        
        summarySpan.textContent = `${data.uploaded} uploaded, ${data.duplicates} skipped`;
        if (data.uploaded > 0) processBtn.classList.remove('hidden');
        loadStatements();
    } catch (e) { summarySpan.textContent = 'Upload failed'; }
    
    document.getElementById('file-input').value = '';
}

let processingPollInterval = null;

async function processAllStatements() {
    const accountId = document.getElementById('upload-account')?.value;
    
    // Show progress UI immediately
    showProcessingProgress();
    
    try {
        // Start the processing (this will return when complete)
        const resPromise = fetch(`${API}/process-all`, { 
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ account_id: accountId ? parseInt(accountId) : null })
        });
        
        // Start polling for progress
        startProgressPolling();
        
        // Wait for processing to complete
        const res = await resPromise;
        const data = await res.json();
        
        // Stop polling
        stopProgressPolling();
        
        // Show completion
        if (data.error) {
            showProcessingComplete(false, data.error);
        } else {
            showProcessingComplete(true, `Processed ${data.processed} statements with ${data.errors} errors`);
            loadStatements();
            loadDashboard();
            loadPots();
        }
    } catch (e) { 
        stopProgressPolling();
        showProcessingComplete(false, 'Processing failed: ' + e.message);
    }
}

function showProcessingProgress() {
    const progressDiv = document.getElementById('processing-progress');
    progressDiv.classList.remove('hidden');
    
    // Reset all fields
    document.getElementById('processing-bar').style.width = '0%';
    document.getElementById('processing-status').textContent = 'Initializing...';
    document.getElementById('processing-count').textContent = '0 / 0';
    document.getElementById('processing-filename').textContent = 'Starting...';
    document.getElementById('processing-success').textContent = '0';
    document.getElementById('processing-errors').textContent = '0';
    document.getElementById('processing-eta').textContent = '--';
    document.getElementById('processing-time').textContent = '0:00';
    document.getElementById('processing-activity').innerHTML = '';
    
    // Hide the process button
    document.getElementById('process-all-btn')?.classList.add('hidden');
    document.getElementById('process-pending-btn')?.classList.add('hidden');
}

function startProgressPolling() {
    // Poll every 500ms for progress updates
    processingPollInterval = setInterval(pollProcessingStatus, 500);
}

function stopProgressPolling() {
    if (processingPollInterval) {
        clearInterval(processingPollInterval);
        processingPollInterval = null;
    }
}

async function pollProcessingStatus() {
    try {
        const res = await fetch(`${API}/process-status`);
        const status = await res.json();
        
        updateProcessingUI(status);
    } catch (e) {
        console.error('Failed to poll status:', e);
    }
}

function updateProcessingUI(status) {
    // Update progress bar
    const progress = status.total_files > 0 ? (status.current_index / status.total_files) * 100 : 0;
    document.getElementById('processing-bar').style.width = `${progress}%`;
    
    // Update status text
    let statusText = 'Processing...';
    if (status.phase === 'initializing') statusText = 'Initializing OCR engine...';
    else if (status.phase === 'processing') statusText = 'Processing statements...';
    else if (status.phase === 'complete') statusText = 'Complete!';
    document.getElementById('processing-status').textContent = statusText;
    
    // Update count
    document.getElementById('processing-count').textContent = `${status.current_index} / ${status.total_files}`;
    
    // Update current file
    if (status.current_file) {
        document.getElementById('processing-filename').textContent = status.current_file;
    }
    
    // Update stats
    document.getElementById('processing-success').textContent = status.completed_count;
    document.getElementById('processing-errors').textContent = status.error_count;
    
    // Update elapsed time
    if (status.elapsed_seconds) {
        const mins = Math.floor(status.elapsed_seconds / 60);
        const secs = Math.floor(status.elapsed_seconds % 60);
        document.getElementById('processing-time').textContent = `${mins}:${secs.toString().padStart(2, '0')}`;
        
        // Estimate remaining time
        if (status.current_index > 0 && status.total_files > status.current_index) {
            const avgPerFile = status.elapsed_seconds / status.current_index;
            const remaining = avgPerFile * (status.total_files - status.current_index);
            const remMins = Math.floor(remaining / 60);
            const remSecs = Math.floor(remaining % 60);
            document.getElementById('processing-eta').textContent = `${remMins}:${remSecs.toString().padStart(2, '0')}`;
        }
    }
    
    // Update activity log
    const activityDiv = document.getElementById('processing-activity');
    let activityHtml = '';
    
    // Show recent completions
    if (status.completed && status.completed.length > 0) {
        status.completed.slice(-3).forEach(item => {
            activityHtml += `<div class="text-green-400"><i class="fas fa-check mr-1"></i>${item.filename} - ${item.transactions_count} transactions</div>`;
        });
    }
    
    // Show recent errors
    if (status.errors && status.errors.length > 0) {
        status.errors.slice(-2).forEach(item => {
            activityHtml += `<div class="text-red-400"><i class="fas fa-times mr-1"></i>${item.filename} - Error</div>`;
        });
    }
    
    activityDiv.innerHTML = activityHtml;
}

function showProcessingComplete(success, message) {
    const progressDiv = document.getElementById('processing-progress');
    
    // Update UI to show completion
    document.getElementById('processing-bar').style.width = '100%';
    document.getElementById('processing-status').textContent = success ? 'Complete!' : 'Failed';
    document.getElementById('processing-filename').textContent = message;
    document.getElementById('processing-eta').textContent = 'Done';
    
    // Change border color based on success
    if (success) {
        progressDiv.classList.remove('border-accent-yellow');
        progressDiv.classList.add('border-green-500');
    } else {
        progressDiv.classList.remove('border-accent-yellow');
        progressDiv.classList.add('border-red-500');
    }
    
    // Hide spinner, show checkmark or X
    const spinner = progressDiv.querySelector('.loading');
    if (spinner) {
        spinner.classList.remove('loading', 'loading-spinner');
        spinner.innerHTML = success ? '<i class="fas fa-check-circle text-green-400"></i>' : '<i class="fas fa-times-circle text-red-400"></i>';
    }
    
    // Hide progress after 5 seconds
    setTimeout(() => {
        progressDiv.classList.add('hidden');
        progressDiv.classList.remove('border-green-500', 'border-red-500');
        progressDiv.classList.add('border-accent-yellow');
        // Restore spinner
        if (spinner) {
            spinner.classList.add('loading', 'loading-spinner');
            spinner.innerHTML = '';
        }
    }, 5000);
}

async function processStatement(id) {
    try {
        const res = await fetch(`${API}/process/${id}`, { method: 'POST' });
        const data = await res.json();
        if (data.error) alert('Error: ' + data.error);
        else { alert(`Processed ${data.transactions_count} transactions`); loadStatements(); loadDashboard(); }
    } catch (e) { console.error('Process failed:', e); }
}

async function loadRules() {
    try {
        const res = await fetch(`${API}/rules`);
        const data = await res.json();
        const tbody = document.getElementById('rules-table');
        
        if (!data.rules.length) {
            tbody.innerHTML = '<tr><td colspan="4" class="text-center py-4 text-gray-500 text-sm">No rules</td></tr>';
            return;
        }
        
        tbody.innerHTML = data.rules.slice(0, 20).map(r => `
            <tr>
                <td><code class="text-cyan-400 text-xs bg-cyan-500/10 px-1 rounded">${r.pattern}</code></td>
                <td class="text-gray-500">${r.match_type}</td>
                <td>${r.category_name || '-'}</td>
                <td><button onclick="deleteRule(${r.id})" class="btn-nb bg-red-500/20 text-red-400 p-1 text-xs"><i class="fas fa-trash"></i></button></td>
            </tr>
        `).join('');
    } catch (e) { console.error('Rules load failed:', e); }
}

function showModal(id) { document.getElementById(id).showModal(); }
function hideModal(id) { document.getElementById(id).close(); }

function showCreateAccountModal() {
    document.getElementById('account-name').value = '';
    document.getElementById('account-description').value = '';
    document.getElementById('account-icon').value = 'wallet';
    document.getElementById('account-color').value = '#a855f7';
    showModal('account-modal');
}

async function createAccount() {
    const data = {
        name: document.getElementById('account-name').value,
        description: document.getElementById('account-description').value || null,
        icon: document.getElementById('account-icon').value,
        color: document.getElementById('account-color').value,
        account_type: 'sub'
    };
    if (!data.name) { alert('Enter a name'); return; }
    try {
        await fetch(`${API}/accounts`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
        hideModal('account-modal');
        loadPots();
        loadDashboard();
    } catch (e) { console.error('Create account failed:', e); }
}

async function deleteAccount(id) {
    if (!confirm('Delete this account?')) return;
    try {
        const res = await fetch(`${API}/accounts/${id}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.error) { alert(data.error); return; }
        loadPots();
        loadDashboard();
    } catch (e) { console.error('Delete account failed:', e); }
}

function showTransferModal() {
    populateTransferSelects();
    document.getElementById('transfer-amount').value = '';
    document.getElementById('transfer-description').value = '';
    document.getElementById('transfer-date').value = new Date().toISOString().split('T')[0];
    showModal('transfer-modal');
}

function showTransferModalTo(toAccountId) {
    populateTransferSelects();
    document.getElementById('transfer-to').value = toAccountId;
    document.getElementById('transfer-amount').value = '';
    document.getElementById('transfer-description').value = '';
    document.getElementById('transfer-date').value = new Date().toISOString().split('T')[0];
    showModal('transfer-modal');
}

function populateTransferSelects() {
    const fromSelect = document.getElementById('transfer-from');
    const toSelect = document.getElementById('transfer-to');
    
    fromSelect.innerHTML = accounts.map(a => 
        `<option value="${a.id}">${a.name}${a.is_main ? ' (Main)' : ''} - ${formatCurrency(a.balance)}</option>`
    ).join('');
    
    toSelect.innerHTML = accounts.map(a => 
        `<option value="${a.id}">${a.name}${a.is_main ? ' (Main)' : ''}</option>`
    ).join('');
}

async function createTransfer() {
    const fromId = parseInt(document.getElementById('transfer-from').value);
    const toId = parseInt(document.getElementById('transfer-to').value);
    const amount = parseFloat(document.getElementById('transfer-amount').value);
    const description = document.getElementById('transfer-description').value;
    const date = document.getElementById('transfer-date').value;
    
    if (fromId === toId) { alert('Cannot transfer to the same account'); return; }
    if (!amount || amount <= 0) { alert('Enter a valid amount'); return; }
    
    try {
        const res = await fetch(`${API}/transfers`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                from_account_id: fromId,
                to_account_id: toId,
                amount: amount,
                description: description || null,
                date: date || null
            })
        });
        const data = await res.json();
        if (data.error) { alert(data.error); return; }
        hideModal('transfer-modal');
        loadPots();
        loadDashboard();
    } catch (e) { console.error('Transfer failed:', e); }
}

// Backward compatibility aliases
const showCreatePotModal = showCreateAccountModal;
const createPot = createAccount;
const deletePot = deleteAccount;

function showCreateCategoryModal() {
    document.getElementById('category-name').value = '';
    document.getElementById('category-color').value = '#06b6d4';
    showModal('category-modal');
}

async function createCategory() {
    const data = { name: document.getElementById('category-name').value, color: document.getElementById('category-color').value };
    if (!data.name) { alert('Enter name'); return; }
    try {
        await fetch(`${API}/categories`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
        hideModal('category-modal');
        loadCategories();
        loadCategoriesView();
    } catch (e) { console.error('Create category failed:', e); }
}

async function deleteCategory(id) {
    if (!confirm('Delete?')) return;
    try {
        await fetch(`${API}/categories/${id}`, { method: 'DELETE' });
        loadCategories();
        loadCategoriesView();
    } catch (e) { console.error('Delete category failed:', e); }
}

function showCreateRuleModal() {
    document.getElementById('rule-pattern').value = '';
    document.getElementById('rule-match').value = 'contains';
    showModal('rule-modal');
}

async function createRule() {
    const data = {
        pattern: document.getElementById('rule-pattern').value,
        match_type: document.getElementById('rule-match').value,
        category_id: parseInt(document.getElementById('rule-category').value) || null
    };
    if (!data.pattern) { alert('Enter pattern'); return; }
    try {
        await fetch(`${API}/rules`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
        hideModal('rule-modal');
        loadRules();
    } catch (e) { console.error('Create rule failed:', e); }
}

async function deleteRule(id) {
    if (!confirm('Delete?')) return;
    try {
        await fetch(`${API}/rules/${id}`, { method: 'DELETE' });
        loadRules();
    } catch (e) { console.error('Delete rule failed:', e); }
}

function formatCurrency(amount) {
    return new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP' }).format(amount || 0);
}

function formatCurrencyCompact(amount) {
    const abs = Math.abs(amount || 0);
    if (abs >= 1000000) return '£' + (amount / 1000000).toFixed(1) + 'M';
    if (abs >= 1000) return '£' + (amount / 1000).toFixed(1) + 'K';
    return '£' + (amount || 0).toFixed(0);
}

function exportCSV(mode) {
    const transactions = mode === 'all' ? allTransactions : filteredTransactions;
    if (!transactions.length) { alert('No data'); return; }
    
    const headers = ['Date', 'Description', 'Type', 'Category', 'Amount'];
    const rows = transactions.map(t => [
        new Date(t.date).toLocaleDateString('en-GB'),
        `"${(t.description || '').replace(/"/g, '""')}"`,
        t.type,
        t.category_name || 'Uncategorized',
        (t.type === 'income' ? t.amount : -t.amount).toFixed(2)
    ].join(','));
    
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `transactions_${new Date().toISOString().split('T')[0]}.csv`;
    link.click();
}

function exportFullBackup() {
    window.location.href = `${API}/export/full`;
}

async function importFullBackup(input) {
    const file = input.files[0];
    if (!file) return;
    
    const statusEl = document.getElementById('import-status');
    statusEl.className = 'text-xs p-2 rounded bg-blue-500/20 text-blue-400';
    statusEl.textContent = 'Importing data...';
    statusEl.classList.remove('hidden');
    
    try {
        const formData = new FormData();
        formData.append('file', file);
        
        const res = await fetch(`${API}/import/full`, {
            method: 'POST',
            body: formData
        });
        
        const data = await res.json();
        
        if (data.success) {
            const stats = data.stats;
            
            statusEl.className = 'text-xs p-2 rounded bg-green-500/20 text-green-400';
            
            let html = '<p class="font-medium mb-1">Import successful!</p>';
            html += `<p><b>Banking:</b> ${stats.transactions_created} transactions, ${stats.categories_created} categories, ${stats.accounts_created} accounts, ${stats.rules_created} rules</p>`;
            
            // Include asset stats if present
            if (stats.assets_created !== undefined) {
                html += `<p><b>Assets:</b> ${stats.assets_created} assets, ${stats.asset_transactions_created || 0} transactions</p>`;
                if (stats.assets_created > 0 || stats.asset_transactions_created > 0) {
                    html += `<p class="text-blue-400 mt-1"><i class="fas fa-info-circle mr-1"></i>Use "Calculate History" in Assets > Analytics to rebuild price data</p>`;
                }
            }
            
            const totalSkipped = (stats.transactions_skipped || 0) + (stats.categories_skipped || 0) + 
                (stats.assets_skipped || 0) + (stats.asset_transactions_skipped || 0);
            if (totalSkipped > 0) {
                html += `<p class="text-gray-400 mt-1">Skipped ${totalSkipped} duplicate records</p>`;
            }
            
            statusEl.innerHTML = html;
            
            // Refresh data
            loadCategories();
            loadPots();
            loadDashboard();
            loadTransactions();
            loadRules();
            if (typeof loadAssets === 'function') loadAssets();
        } else {
            statusEl.className = 'text-xs p-2 rounded bg-red-500/20 text-red-400';
            statusEl.textContent = `Import failed: ${data.error}`;
        }
    } catch (e) {
        statusEl.className = 'text-xs p-2 rounded bg-red-500/20 text-red-400';
        statusEl.textContent = `Import failed: ${e.message}`;
    }
    
    // Reset file input
    input.value = '';
}

async function confirmResetDatabase() {
    if (!confirm('Delete ALL data? This cannot be undone.')) return;
    if (prompt('Type RESET to confirm:') !== 'RESET') return;
    
    try {
        await fetch(`${API}/reset`, { method: 'POST' });
        alert('Database reset');
        loadCategories();
        loadPots();
        loadDashboard();
        loadStatements();
    } catch (e) { alert('Reset failed'); }
}

// ==================== ADD TRANSACTION ====================

let newTxType = 'expense';

function showAddTransactionModal() {
    // Reset form
    document.getElementById('add-tx-date').value = new Date().toISOString().split('T')[0];
    document.getElementById('add-tx-description').value = '';
    document.getElementById('add-tx-amount').value = '';
    document.getElementById('add-tx-notes').value = '';
    newTxType = 'expense';
    setTxType('expense');
    
    // Populate dropdowns
    const catSelect = document.getElementById('add-tx-category');
    catSelect.innerHTML = categories.map(c => 
        `<option value="${c.id}">${c.name}</option>`
    ).join('');
    
    const accSelect = document.getElementById('add-tx-account');
    accSelect.innerHTML = accounts.map(a => 
        `<option value="${a.id}" ${a.is_main ? 'selected' : ''}>${a.name}</option>`
    ).join('');
    
    showModal('add-tx-modal');
}

function setTxType(type) {
    newTxType = type;
    const expBtn = document.getElementById('tx-type-expense');
    const incBtn = document.getElementById('tx-type-income');
    
    if (type === 'expense') {
        expBtn.className = 'flex-1 btn-nb btn-nb-primary py-2 text-sm';
        incBtn.className = 'flex-1 btn-nb btn-nb-secondary py-2 text-sm';
    } else {
        expBtn.className = 'flex-1 btn-nb btn-nb-secondary py-2 text-sm';
        incBtn.className = 'flex-1 btn-nb btn-nb-primary py-2 text-sm';
    }
}

async function createTransaction() {
    const date = document.getElementById('add-tx-date').value;
    const description = document.getElementById('add-tx-description').value.trim();
    const amount = parseFloat(document.getElementById('add-tx-amount').value);
    const categoryId = document.getElementById('add-tx-category').value;
    const accountId = document.getElementById('add-tx-account').value;
    const notes = document.getElementById('add-tx-notes').value.trim();
    
    if (!date || !description || !amount) {
        alert('Please fill in date, description and amount');
        return;
    }
    
    try {
        const res = await fetch(`${API}/transactions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                date,
                description,
                amount,
                type: newTxType,
                category_id: categoryId ? parseInt(categoryId) : null,
                account_id: accountId ? parseInt(accountId) : null,
                notes: notes || null
            })
        });
        
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        
        hideModal('add-tx-modal');
        loadTransactions();
        loadDashboard();
        loadPots();
        
    } catch (e) {
        alert('Failed to create transaction: ' + e.message);
    }
}

// ==================== BUDGET MANAGEMENT ====================

async function loadBudgets() {
    try {
        const now = new Date();
        const res = await fetch(`${API}/budgets/summary?year=${now.getFullYear()}&month=${now.getMonth() + 1}`);
        const data = await res.json();
        
        // Update summary stats
        document.getElementById('budget-total').textContent = formatCurrency(data.total_budgeted);
        document.getElementById('budget-spent').textContent = formatCurrency(data.total_spent);
        document.getElementById('budget-remaining').textContent = formatCurrency(data.total_remaining);
        
        // Show alerts
        const alertsContainer = document.getElementById('budget-alerts');
        if (data.alerts && data.alerts.length > 0) {
            alertsContainer.innerHTML = data.alerts.map(a => `
                <div class="alert ${a.type === 'exceeded' ? 'alert-error' : 'alert-warning'} mb-2">
                    <i class="fas fa-${a.type === 'exceeded' ? 'exclamation-triangle' : 'exclamation-circle'}"></i>
                    <span>${a.message}</span>
                </div>
            `).join('');
        } else {
            alertsContainer.innerHTML = '';
        }
        
        // Show budget list
        const listContainer = document.getElementById('budgets-list');
        if (data.categories && data.categories.length > 0) {
            listContainer.innerHTML = data.categories.map(b => {
                const percentage = Math.min(b.percentage, 100);
                const barColor = b.percentage >= 100 ? 'bg-red-500' : b.percentage >= 80 ? 'bg-yellow-500' : 'bg-green-500';
                return `
                    <div class="p-4 bg-surface-200 rounded-lg">
                        <div class="flex items-center justify-between mb-2">
                            <span class="font-medium">${b.category_name}</span>
                            <span class="text-sm text-gray-400">${formatCurrency(b.spent)} / ${formatCurrency(b.budgeted)}</span>
                        </div>
                        <div class="w-full bg-surface-300 rounded-full h-2 mb-2">
                            <div class="${barColor} h-2 rounded-full transition-all" style="width: ${percentage}%"></div>
                        </div>
                        <div class="flex justify-between text-xs text-gray-500">
                            <span>${b.percentage.toFixed(0)}% used</span>
                            <span>${formatCurrency(b.remaining)} remaining</span>
                        </div>
                    </div>
                `;
            }).join('');
        } else {
            listContainer.innerHTML = '<p class="text-gray-500 text-center py-8">No budgets set. Create one to start tracking!</p>';
        }
        
    } catch (e) {
        console.error('Failed to load budgets:', e);
    }
}

function showCreateBudgetModal() {
    document.getElementById('budget-amount').value = '';
    document.getElementById('budget-rollover').checked = false;
    
    // Populate category dropdown
    const catSelect = document.getElementById('budget-category');
    catSelect.innerHTML = categories
        .filter(c => c.name !== 'Income' && c.name !== 'Uncategorized')
        .map(c => `<option value="${c.id}">${c.name}</option>`)
        .join('');
    
    showModal('budget-modal');
}

async function saveBudget() {
    const categoryId = document.getElementById('budget-category').value;
    const amount = parseFloat(document.getElementById('budget-amount').value);
    const rollover = document.getElementById('budget-rollover').checked;
    
    if (!categoryId || !amount) {
        alert('Please select a category and enter an amount');
        return;
    }
    
    const now = new Date();
    
    try {
        const res = await fetch(`${API}/budgets`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                category_id: parseInt(categoryId),
                budget_amount: amount,
                year: now.getFullYear(),
                month: now.getMonth() + 1,
                rollover
            })
        });
        
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        
        hideModal('budget-modal');
        loadBudgets();
        
    } catch (e) {
        alert('Failed to save budget: ' + e.message);
    }
}

// ==================== BULK EDIT ====================

function toggleTransactionSelect(id) {
    if (selectedTransactions.has(id)) {
        selectedTransactions.delete(id);
    } else {
        selectedTransactions.add(id);
    }
    renderTransactionsTable(filteredTransactions.length ? filteredTransactions : allTransactions);
}

function toggleSelectAll() {
    const selectAllCheckbox = document.getElementById('select-all');
    const transactions = filteredTransactions.length ? filteredTransactions : allTransactions;
    
    if (selectAllCheckbox.checked) {
        transactions.forEach(t => selectedTransactions.add(t.id));
    } else {
        selectedTransactions.clear();
    }
    renderTransactionsTable(transactions);
}

function clearSelection() {
    selectedTransactions.clear();
    document.getElementById('select-all').checked = false;
    renderTransactionsTable(filteredTransactions.length ? filteredTransactions : allTransactions);
}

function updateBulkEditBar() {
    const bar = document.getElementById('bulk-edit-bar');
    const count = selectedTransactions.size;
    
    if (count > 0) {
        bar.classList.remove('hidden');
        document.getElementById('selected-count').textContent = `${count} selected`;
    } else {
        bar.classList.add('hidden');
    }
    
    // Update select-all checkbox state
    const transactions = filteredTransactions.length ? filteredTransactions : allTransactions;
    const selectAll = document.getElementById('select-all');
    if (transactions.length > 0 && transactions.every(t => selectedTransactions.has(t.id))) {
        selectAll.checked = true;
        selectAll.indeterminate = false;
    } else if (count > 0) {
        selectAll.checked = false;
        selectAll.indeterminate = true;
    } else {
        selectAll.checked = false;
        selectAll.indeterminate = false;
    }
}

function showBulkEditModal() {
    if (selectedTransactions.size === 0) {
        alert('Please select at least one transaction');
        return;
    }
    
    document.getElementById('bulk-edit-count').textContent = `${selectedTransactions.size} transactions selected`;
    
    // Populate category dropdown
    const catSelect = document.getElementById('bulk-edit-category');
    catSelect.innerHTML = '<option value="">-- Don\'t change --</option>' + 
        categories.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
    
    // Populate account dropdown
    const accSelect = document.getElementById('bulk-edit-account');
    accSelect.innerHTML = '<option value="">-- Don\'t change --</option>' + 
        accounts.map(a => `<option value="${a.id}">${a.name}</option>`).join('');
    
    // Populate receiving account dropdown
    populateBulkReceivingAccountDropdown();
    
    // Hide receiving account by default
    document.getElementById('bulk-edit-receiving-container').classList.add('hidden');
    
    showModal('bulk-edit-modal');
}

function populateBulkReceivingAccountDropdown() {
    const sourceAccountId = parseInt(document.getElementById('bulk-edit-account').value) || null;
    const select = document.getElementById('bulk-edit-receiving-account');
    select.innerHTML = '<option value="">-- Don\'t change --</option><option value="external">External (not tracked)</option>';
    
    accounts.forEach(a => {
        if (a.id !== sourceAccountId) {
            select.innerHTML += `<option value="${a.id}">${a.name}</option>`;
        }
    });
}

function onBulkEditCategoryChange() {
    const categoryId = document.getElementById('bulk-edit-category').value;
    const container = document.getElementById('bulk-edit-receiving-container');
    
    // Find if selected category is "Transfers"
    const selectedCategory = categories.find(c => c.id === parseInt(categoryId));
    const isTransfer = selectedCategory && selectedCategory.name.toLowerCase() === 'transfers';
    
    if (isTransfer) {
        container.classList.remove('hidden');
        populateBulkReceivingAccountDropdown();
    } else {
        container.classList.add('hidden');
    }
}

function onBulkEditAccountChange() {
    // Update receiving account dropdown to exclude selected source
    const container = document.getElementById('bulk-edit-receiving-container');
    if (!container.classList.contains('hidden')) {
        const currentReceiving = document.getElementById('bulk-edit-receiving-account').value;
        populateBulkReceivingAccountDropdown();
        // Restore selection if still valid
        const sourceAccountId = parseInt(document.getElementById('bulk-edit-account').value) || null;
        if (currentReceiving && currentReceiving !== 'external' && parseInt(currentReceiving) !== sourceAccountId) {
            document.getElementById('bulk-edit-receiving-account').value = currentReceiving;
        }
    }
}

async function saveBulkEdit() {
    const categoryId = document.getElementById('bulk-edit-category').value;
    const accountId = document.getElementById('bulk-edit-account').value;
    const receivingAccountId = document.getElementById('bulk-edit-receiving-account').value;
    
    // Check if category is transfers
    const selectedCategory = categories.find(c => c.id === parseInt(categoryId));
    const isTransfer = selectedCategory && selectedCategory.name.toLowerCase() === 'transfers';
    
    if (!categoryId && !accountId && !receivingAccountId) {
        alert('Please select at least one field to update');
        return;
    }
    
    const updates = {};
    if (categoryId) updates.category_id = parseInt(categoryId);
    if (accountId) updates.account_id = parseInt(accountId);
    
    // Add transfer info
    if (isTransfer && receivingAccountId && receivingAccountId !== 'external') {
        updates.is_transfer = true;
        updates.receiving_account_id = parseInt(receivingAccountId);
    } else if (isTransfer && receivingAccountId === 'external') {
        updates.is_transfer = true;
        updates.receiving_account_id = null; // External, no linked transaction
    }
    
    try {
        const res = await fetch(`${API}/transactions/bulk-update`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                transaction_ids: Array.from(selectedTransactions),
                updates
            })
        });
        
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        
        hideModal('bulk-edit-modal');
        clearSelection();
        loadTransactions();
        loadPots();
        loadDashboard();
        
    } catch (e) {
        alert('Failed to update transactions: ' + e.message);
    }
}


// ==================== ASSET MANAGEMENT ====================

let assetsData = [];
let supportedAssets = { crypto: {}, precious_metals: {} };
let portfolioAllocationChart = null;
let portfolioHistoryChart = null;
let portfolioHistoryRange = '30'; // Default 30 days

async function loadAssets() {
    try {
        // Check if prices need refresh
        const checkRes = await fetch(`${API}/assets/prices/check`);
        const checkData = await checkRes.json();
        if (checkData.needs_refresh) {
            await fetch(`${API}/assets/prices/refresh`, { method: 'POST' });
        }
        
        const [assetsRes, summaryRes, metricsRes, allocationRes] = await Promise.all([
            fetch(`${API}/assets`),
            fetch(`${API}/assets/portfolio/summary`),
            fetch(`${API}/assets/portfolio/metrics`),
            fetch(`${API}/assets/portfolio/allocation`)
        ]);
        
        const assets = await assetsRes.json();
        const summary = await summaryRes.json();
        const metrics = await metricsRes.json();
        const allocation = await allocationRes.json();
        
        assetsData = assets.assets || [];
        
        // Update overview
        updatePortfolioOverview(summary, metrics);
        updateAllocationChart(allocation.allocation || []);
        updatePerformers(summary);
        
        // Update holdings table
        renderHoldingsTable(assetsData);
        
        // Load transactions
        loadAssetTransactions();
        
        // Update analytics
        updateAssetAnalytics(summary, allocation.allocation || []);
        
        // Load portfolio history for chart
        loadPortfolioHistory();
        
    } catch (e) {
        console.error('Failed to load assets:', e);
    }
}

async function loadPortfolioHistory() {
    try {
        const days = portfolioHistoryRange === 'all' ? 3650 : parseInt(portfolioHistoryRange);
        const res = await fetch(`${API}/assets/portfolio/history?days=${days}`);
        const data = await res.json();
        renderPortfolioHistoryChart(data.history || [], days);
    } catch (e) {
        console.error('Failed to load portfolio history:', e);
    }
}

function setPortfolioHistoryRange(range) {
    portfolioHistoryRange = range;
    
    // Update button states
    document.querySelectorAll('.portfolio-range-btn').forEach(btn => {
        btn.classList.remove('portfolio-range-active');
    });
    document.getElementById(`portfolio-range-${range}`)?.classList.add('portfolio-range-active');
    
    // Reload chart
    loadPortfolioHistory();
}

function renderPortfolioHistoryChart(history, days = 30) {
    const ctx = document.getElementById('portfolio-history-chart');
    if (!ctx) return;
    
    if (portfolioHistoryChart) portfolioHistoryChart.destroy();
    
    if (!history.length) {
        // Show message with recalculate button
        ctx.parentElement.innerHTML = `
            <div class="text-center py-8">
                <p class="text-gray-500 text-sm mb-4">No history data yet. Click below to calculate portfolio history.</p>
                <button onclick="recalculatePortfolio()" class="btn-nb btn-nb-primary px-4 py-2 text-sm">
                    <i class="fas fa-calculator mr-1"></i>Calculate History
                </button>
            </div>
        `;
        return;
    }
    
    // Format date labels based on range
    const formatDate = (dateStr) => {
        const d = new Date(dateStr);
        if (days <= 7) {
            return d.toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit' });
        } else if (days <= 90) {
            return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
        } else {
            return d.toLocaleDateString('en-GB', { month: 'short', year: '2-digit' });
        }
    };
    
    // Determine point radius based on data density
    const pointRadius = history.length > 60 ? 0 : history.length > 30 ? 1 : 2;
    
    portfolioHistoryChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: history.map(h => formatDate(h.date)),
            datasets: [{
                label: 'Portfolio Value',
                data: history.map(h => h.portfolio_value),
                borderColor: '#facc15',
                backgroundColor: 'rgba(250, 204, 21, 0.1)',
                fill: true,
                tension: 0.2,
                pointRadius: pointRadius,
                pointHoverRadius: 4,
                borderWidth: 2
            }, {
                label: 'Base Investment',
                data: history.map(h => h.base_investment),
                borderColor: '#888',
                borderDash: [5, 5],
                fill: false,
                tension: 0,
                pointRadius: 0,
                stepped: true,
                borderWidth: 1.5
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index'
            },
            plugins: {
                legend: {
                    position: 'top',
                    labels: { color: '#888', font: { size: 11 } }
                },
                tooltip: {
                    callbacks: {
                        title: function(context) {
                            const idx = context[0].dataIndex;
                            return new Date(history[idx].date).toLocaleDateString('en-GB', { 
                                weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' 
                            });
                        },
                        label: function(context) {
                            const value = context.parsed.y;
                            const formatted = '£' + value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                            return context.dataset.label + ': ' + formatted;
                        },
                        afterBody: function(context) {
                            const idx = context[0].dataIndex;
                            const h = history[idx];
                            const gain = h.portfolio_value - h.base_investment;
                            const pct = h.base_investment > 0 ? (gain / h.base_investment * 100) : 0;
                            const sign = gain >= 0 ? '+' : '';
                            return `\nGain/Loss: ${sign}£${gain.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (${sign}${pct.toFixed(1)}%)`;
                        }
                    }
                }
            },
            scales: {
                x: { 
                    ticks: { 
                        color: '#888', 
                        maxTicksLimit: days <= 30 ? 10 : 12,
                        maxRotation: 0
                    }, 
                    grid: { color: '#2a2a38' } 
                },
                y: { 
                    ticks: { 
                        color: '#888',
                        callback: value => '£' + value.toLocaleString()
                    }, 
                    grid: { color: '#2a2a38' } 
                }
            }
        }
    });
}

async function recalculatePortfolio() {
    const btn = event.target;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Calculating...';
    
    try {
        const res = await fetch(`${API}/assets/portfolio/recalculate`, { method: 'POST' });
        const data = await res.json();
        
        if (data.error) {
            alert('Error: ' + data.error);
        } else {
            alert(`Fetched ${data.prices_fetched} prices, calculated ${data.snapshots_calculated} snapshots`);
            loadAssets();  // Reload to show updated chart
        }
    } catch (e) {
        alert('Failed to recalculate: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-calculator mr-1"></i>Calculate History';
    }
}

function updatePortfolioOverview(summary, metrics) {
    document.getElementById('portfolio-total-value').textContent = formatCurrency(summary.total_value || 0);
    document.getElementById('portfolio-cost-basis').textContent = formatCurrency(summary.total_cost_basis || 0);
    
    const gainLoss = summary.total_gain_loss || 0;
    const gainLossEl = document.getElementById('portfolio-gain-loss');
    gainLossEl.textContent = `${gainLoss >= 0 ? '+' : ''}${formatCurrency(gainLoss)}`;
    gainLossEl.className = `text-xl font-bold mono ${gainLoss >= 0 ? 'text-green-400' : 'text-red-400'}`;
    
    const roi = summary.total_gain_loss_pct || 0;
    const roiEl = document.getElementById('portfolio-roi');
    roiEl.textContent = `${roi >= 0 ? '+' : ''}${roi.toFixed(1)}%`;
    roiEl.className = `text-xl font-bold mono ${roi >= 0 ? 'text-green-400' : 'text-red-400'}`;
    
    // Metrics
    document.getElementById('metric-invested').textContent = formatCurrency(metrics.total_invested || 0);
    document.getElementById('metric-unrealized').textContent = formatCurrency(metrics.unrealized_gains || 0);
    document.getElementById('metric-realized').textContent = formatCurrency(metrics.realized_gains || 0);
    document.getElementById('metric-annualized').textContent = metrics.annualized_return ? `${metrics.annualized_return.toFixed(1)}%` : '-';
    document.getElementById('metric-holdings-count').textContent = summary.holdings_count || 0;
}

function updateAllocationChart(allocation) {
    const ctx = document.getElementById('portfolio-allocation-chart');
    if (!ctx) return;
    
    if (portfolioAllocationChart) portfolioAllocationChart.destroy();
    
    if (!allocation.length) {
        ctx.parentElement.innerHTML = '<p class="text-gray-500 text-sm text-center py-8">No holdings yet</p>';
        return;
    }
    
    portfolioAllocationChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: allocation.map(a => `${a.symbol} (${a.percentage}%)`),
            datasets: [{
                data: allocation.map(a => a.value),
                backgroundColor: allocation.map(a => a.color),
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'right',
                    labels: { color: '#888', font: { size: 11 }, boxWidth: 12 }
                }
            }
        }
    });
}

function updatePerformers(summary) {
    const bestEl = document.getElementById('best-performer');
    const worstEl = document.getElementById('worst-performer');
    
    if (summary.best_performer) {
        const b = summary.best_performer;
        bestEl.innerHTML = `
            <div class="flex items-center justify-between">
                <div>
                    <span class="font-medium">${b.symbol}</span>
                    <span class="text-gray-500 ml-1">${b.name}</span>
                </div>
                <span class="text-green-400 font-medium mono">+${b.gain_loss_pct}%</span>
            </div>
            <p class="text-xs text-gray-500 mt-1">${formatCurrency(b.current_value)} value, ${formatCurrency(b.gain_loss)} gain</p>
        `;
    } else {
        bestEl.innerHTML = '<p class="text-gray-500 text-sm">No data</p>';
    }
    
    if (summary.worst_performer && summary.worst_performer.gain_loss_pct < 0) {
        const w = summary.worst_performer;
        worstEl.innerHTML = `
            <div class="flex items-center justify-between">
                <div>
                    <span class="font-medium">${w.symbol}</span>
                    <span class="text-gray-500 ml-1">${w.name}</span>
                </div>
                <span class="text-red-400 font-medium mono">${w.gain_loss_pct}%</span>
            </div>
            <p class="text-xs text-gray-500 mt-1">${formatCurrency(w.current_value)} value, ${formatCurrency(w.gain_loss)} loss</p>
        `;
    } else {
        worstEl.innerHTML = '<p class="text-gray-500 text-sm">No losses</p>';
    }
}

function renderHoldingsTable(assets) {
    const tbody = document.getElementById('holdings-table');
    
    if (!assets.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center py-8 text-gray-500">No assets yet. Click "Add Asset" to get started.</td></tr>';
        return;
    }
    
    tbody.innerHTML = assets.map(a => {
        const gainColor = a.gain_loss >= 0 ? 'green' : 'red';
        const changeColor = (a.change_24h || 0) >= 0 ? 'green' : 'red';
        return `
            <tr>
                <td>
                    <div class="flex items-center gap-2">
                        <div class="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold" style="background:${a.color}20;color:${a.color}">${a.symbol.slice(0, 3)}</div>
                        <div>
                            <p class="font-medium">${a.symbol}</p>
                            <p class="text-xs text-gray-500">${a.name}${!a.is_tracked ? ' <span class="text-yellow-500">(Untracked)</span>' : ''}</p>
                        </div>
                    </div>
                </td>
                <td class="text-right mono">${formatAssetQuantity(a.quantity)}</td>
                <td class="text-right mono hidden sm:table-cell">${formatCurrency(a.average_buy_price)}</td>
                <td class="text-right">
                    <p class="mono">${formatCurrency(a.current_price)}</p>
                    ${a.change_24h ? `<p class="text-xs text-${changeColor}-400">${a.change_24h >= 0 ? '+' : ''}${a.change_24h.toFixed(1)}%</p>` : ''}
                </td>
                <td class="text-right mono hidden sm:table-cell">${formatCurrency(a.current_value)}</td>
                <td class="text-right">
                    <p class="mono text-${gainColor}-400">${a.gain_loss >= 0 ? '+' : ''}${formatCurrency(a.gain_loss)}</p>
                    <p class="text-xs text-${gainColor}-400">${a.gain_loss_pct >= 0 ? '+' : ''}${a.gain_loss_pct}%</p>
                </td>
                <td>
                    <div class="flex gap-1 justify-end">
                        <button onclick="showAddAssetTxModal(${a.id})" class="btn-nb btn-nb-secondary p-1.5 text-xs" title="Add Transaction">
                            <i class="fas fa-plus"></i>
                        </button>
                        <button onclick="deleteAsset(${a.id})" class="btn-nb bg-red-500/20 text-red-400 p-1.5 text-xs" title="Delete">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

async function loadAssetTransactions() {
    try {
        const res = await fetch(`${API}/assets/transactions?limit=50`);
        const data = await res.json();
        
        const tbody = document.getElementById('asset-transactions-table');
        const transactions = data.transactions || [];
        
        if (!transactions.length) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center py-8 text-gray-500">No transactions yet.</td></tr>';
            return;
        }
        
        tbody.innerHTML = transactions.map(t => {
            const typeColor = t.type === 'buy' ? 'green' : t.type === 'sell' ? 'red' : 'blue';
            return `
                <tr>
                    <td class="mono text-gray-400">${new Date(t.date).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: '2-digit' })}</td>
                    <td>
                        <span class="font-medium">${t.asset_symbol}</span>
                        <span class="text-gray-500 text-xs ml-1">${t.asset_name}</span>
                    </td>
                    <td class="text-center">
                        <span class="px-2 py-0.5 rounded text-xs bg-${typeColor}-500/20 text-${typeColor}-400">${t.type.toUpperCase()}</span>
                    </td>
                    <td class="text-right mono">${formatAssetQuantity(t.quantity)}</td>
                    <td class="text-right mono">${formatCurrency(t.price_per_unit)}</td>
                    <td class="text-right mono font-medium">${formatCurrency(t.total_value)}</td>
                    <td>
                        <button onclick="deleteAssetTransaction(${t.id})" class="btn-nb bg-red-500/20 text-red-400 p-1.5 text-xs">
                            <i class="fas fa-trash"></i>
                        </button>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (e) {
        console.error('Failed to load asset transactions:', e);
    }
}

function updateAssetAnalytics(summary, allocation) {
    // By type
    const byTypeContainer = document.getElementById('allocation-by-type');
    const byType = summary.by_type || {};
    
    if (Object.keys(byType).length === 0) {
        byTypeContainer.innerHTML = '<p class="text-gray-500 text-sm">No data</p>';
    } else {
        const total = Object.values(byType).reduce((sum, t) => sum + t.value, 0);
        byTypeContainer.innerHTML = Object.entries(byType).map(([type, data]) => {
            const pct = total > 0 ? (data.value / total * 100) : 0;
            const typeLabel = type === 'crypto' ? 'Cryptocurrency' : type === 'precious_metal' ? 'Precious Metals' : type;
            return `
                <div class="flex justify-between items-center py-2 border-b border-surface-300 last:border-0">
                    <span class="text-sm">${typeLabel}</span>
                    <span class="mono text-sm">${formatCurrency(data.value)} <span class="text-gray-500">(${pct.toFixed(1)}%)</span></span>
                </div>
            `;
        }).join('');
    }
    
    // Top holdings
    const topContainer = document.getElementById('top-holdings-list');
    const sortedHoldings = [...allocation].sort((a, b) => b.value - a.value).slice(0, 5);
    
    if (!sortedHoldings.length) {
        topContainer.innerHTML = '<p class="text-gray-500 text-sm">No data</p>';
    } else {
        topContainer.innerHTML = sortedHoldings.map((h, i) => `
            <div class="flex justify-between items-center py-2 border-b border-surface-300 last:border-0">
                <div class="flex items-center gap-2">
                    <span class="w-5 h-5 rounded bg-surface-300 flex items-center justify-center text-xs text-gray-400">${i + 1}</span>
                    <span class="text-sm font-medium">${h.symbol}</span>
                </div>
                <span class="mono text-sm">${formatCurrency(h.value)} <span class="text-gray-500">(${h.percentage}%)</span></span>
            </div>
        `).join('');
    }
}

function formatAssetQuantity(qty) {
    if (qty >= 1000) return qty.toLocaleString('en-GB', { maximumFractionDigits: 2 });
    if (qty >= 1) return qty.toFixed(4);
    return qty.toFixed(8);
}

function setAssetSubTab(tab) {
    document.querySelectorAll('.asset-subtab').forEach(el => el.classList.remove('asset-subtab-active'));
    document.querySelectorAll('.asset-subtab-content').forEach(el => el.classList.add('hidden'));
    
    document.getElementById(`asset-tab-${tab}`)?.classList.add('asset-subtab-active');
    document.getElementById(`asset-subtab-${tab}`)?.classList.remove('hidden');
}

async function loadSupportedAssets() {
    try {
        const res = await fetch(`${API}/assets/supported`);
        supportedAssets = await res.json();
    } catch (e) {
        console.error('Failed to load supported assets:', e);
    }
}

function showAddAssetModal() {
    loadSupportedAssets().then(() => {
        onAssetTypeChange();
        showModal('add-asset-modal');
    });
}

function onAssetTypeChange() {
    const type = document.getElementById('add-asset-type').value;
    const selectContainer = document.getElementById('asset-select-container');
    const customContainer = document.getElementById('custom-asset-container');
    const untrackedWarning = document.getElementById('untracked-warning');
    const select = document.getElementById('add-asset-select');
    
    if (type === 'other') {
        selectContainer.classList.add('hidden');
        customContainer.classList.remove('hidden');
        untrackedWarning.classList.remove('hidden');
    } else {
        selectContainer.classList.remove('hidden');
        customContainer.classList.add('hidden');
        untrackedWarning.classList.add('hidden');
        
        const assets = type === 'crypto' ? supportedAssets.crypto : supportedAssets.precious_metals;
        select.innerHTML = '<option value="">-- Select --</option>' +
            Object.entries(assets).map(([symbol, data]) => 
                `<option value="${symbol}" data-name="${data.name}">${symbol} - ${data.name}</option>`
            ).join('');
    }
}

async function addAsset() {
    const type = document.getElementById('add-asset-type').value;
    let symbol, name, isTracked = true;
    
    if (type === 'other') {
        symbol = document.getElementById('add-asset-symbol').value.trim().toUpperCase();
        name = document.getElementById('add-asset-name').value.trim();
        isTracked = false;
        
        if (!symbol || !name) {
            alert('Please enter symbol and name');
            return;
        }
    } else {
        const select = document.getElementById('add-asset-select');
        symbol = select.value;
        const option = select.options[select.selectedIndex];
        name = option?.dataset?.name || symbol;
        
        if (!symbol) {
            alert('Please select an asset');
            return;
        }
    }
    
    try {
        const res = await fetch(`${API}/assets`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                symbol,
                name,
                asset_type: type,
                is_tracked: isTracked
            })
        });
        
        const data = await res.json();
        if (data.error) {
            alert(data.error);
            return;
        }
        
        hideModal('add-asset-modal');
        loadAssets();
        
        // Show add transaction modal for the new asset
        showAddAssetTxModal(data.id);
        
    } catch (e) {
        alert('Failed to add asset: ' + e.message);
    }
}

function showAddAssetTxModal(assetId = null) {
    // Populate asset dropdown
    const select = document.getElementById('add-asset-tx-select');
    select.innerHTML = assetsData.map(a => 
        `<option value="${a.id}" ${a.id === assetId ? 'selected' : ''}>${a.symbol} - ${a.name}</option>`
    ).join('');
    
    // Set defaults
    document.getElementById('add-asset-tx-date').value = new Date().toISOString().split('T')[0];
    document.getElementById('add-asset-tx-quantity').value = '';
    document.getElementById('add-asset-tx-price').value = '';
    document.getElementById('add-asset-tx-fee').value = '0';
    document.getElementById('add-asset-tx-notes').value = '';
    document.getElementById('add-asset-tx-type').value = 'buy';
    document.getElementById('add-asset-tx-auto-price').checked = false;
    
    showModal('add-asset-tx-modal');
}

async function addAssetTransaction() {
    const assetId = parseInt(document.getElementById('add-asset-tx-select').value);
    const txType = document.getElementById('add-asset-tx-type').value;
    const quantity = parseFloat(document.getElementById('add-asset-tx-quantity').value);
    const price = parseFloat(document.getElementById('add-asset-tx-price').value);
    const date = document.getElementById('add-asset-tx-date').value;
    const fee = parseFloat(document.getElementById('add-asset-tx-fee').value) || 0;
    const notes = document.getElementById('add-asset-tx-notes').value;
    const autoPrice = document.getElementById('add-asset-tx-auto-price').checked;
    
    if (!assetId || !quantity || (!price && !autoPrice) || !date) {
        alert('Please fill in all required fields');
        return;
    }
    
    try {
        const res = await fetch(`${API}/assets/transactions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                asset_id: assetId,
                transaction_type: txType,
                quantity,
                price_per_unit: price || 0,
                transaction_date: date,
                fee,
                notes,
                auto_fetch_price: autoPrice
            })
        });
        
        const data = await res.json();
        if (data.error) {
            alert(data.error);
            return;
        }
        
        hideModal('add-asset-tx-modal');
        loadAssets();
        
    } catch (e) {
        alert('Failed to add transaction: ' + e.message);
    }
}

async function deleteAsset(id) {
    if (!confirm('Delete this asset and all its transactions?')) return;
    
    try {
        await fetch(`${API}/assets/${id}`, { method: 'DELETE' });
        loadAssets();
    } catch (e) {
        alert('Failed to delete asset: ' + e.message);
    }
}

async function deleteAssetTransaction(id) {
    if (!confirm('Delete this transaction?')) return;
    
    try {
        await fetch(`${API}/assets/transactions/${id}`, { method: 'DELETE' });
        loadAssets();
    } catch (e) {
        alert('Failed to delete transaction: ' + e.message);
    }
}

async function refreshAssetPrices() {
    const btn = document.getElementById('refresh-prices-btn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-sync-alt fa-spin mr-1"></i>Refreshing...';
    
    try {
        const res = await fetch(`${API}/assets/prices/refresh`, { method: 'POST' });
        const data = await res.json();
        
        if (data.error) {
            alert('Failed to refresh prices: ' + data.error);
        } else {
            loadAssets();
        }
    } catch (e) {
        alert('Failed to refresh prices: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-sync-alt mr-1"></i>Refresh Prices';
    }
}

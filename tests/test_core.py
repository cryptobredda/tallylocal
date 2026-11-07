"""The core invariant: bank rows are immutable, annotations are reversible."""
import pytest

from app.core import RawRecordLocked, TallyError
from app.database import activate_db, get_session
from app.models import Account, Transaction, TransactionType


def _raw_row(db_path, tx_id):
    with activate_db(db_path):
        session = get_session(db_path)
        try:
            return session.get(Transaction, tx_id)
        finally:
            session.close()


def test_edit_writes_overlay_not_raw(core, db_path):
    tx = core.create_transaction('TESCO STORES', 42.10, '2026-11-03')
    category = next(c for c in core.list_categories() if c['name'] == 'Groceries')['id']

    updated = core.update_transaction(tx['id'], {
        'category_id': category,
        'description': 'Tesco weekly shop',
        'notes': 'family shop',
    })

    assert updated['description'] == 'Tesco weekly shop'
    assert updated['category_name'] == 'Groceries'
    assert updated['edited'] is True

    raw = _raw_row(db_path, tx['id'])
    assert raw.description == 'TESCO STORES'
    assert raw.original_description == 'TESCO STORES'
    assert raw.category_id is None


def test_undo_restores_previous_values(core, db_path):
    tx = core.create_transaction('NETFLIX.COM', 10.99, '2026-11-04')
    core.update_transaction(tx['id'], {'notes': 'cancel this'})
    restored = core.undo(tx['id'])

    assert restored.get('notes') is None
    raw = _raw_row(db_path, tx['id'])
    assert raw.notes is None


def test_delete_and_restore(core):
    tx = core.create_transaction('COSTA', 4.15, '2026-11-05')
    core.delete_transaction(tx['id'], confirm=True)

    assert core.list_transactions()['total'] == 0
    assert core.list_deleted()[0]['id'] == tx['id']

    core.restore_transaction(tx['id'])
    assert core.list_transactions()['total'] == 1


def test_delete_requires_confirmation(core):
    tx = core.create_transaction('LIDL', 20.0, '2026-11-05')
    with pytest.raises(TallyError):
        core.delete_transaction(tx['id'])


def test_bank_rows_reject_raw_edits(core, db_path):
    with activate_db(db_path):
        session = get_session(db_path)
        account = session.query(Account).first()
        bank_tx = Transaction(date=__import__('datetime').datetime(2026, 11, 3),
                              description='BANK ROW', original_description='BANK ROW',
                              amount=12.5, transaction_type=TransactionType.EXPENSE,
                              account_id=account.id, source='bank_sync',
                              external_id='ext-1')
        session.add(bank_tx)
        session.commit()
        tx_id = bank_tx.id
        session.close()

    with pytest.raises(RawRecordLocked):
        core.update_transaction(tx_id, {'amount': 99.0})

    # Annotations are still allowed.
    updated = core.update_transaction(tx_id, {'notes': 'checked'})
    assert updated['notes'] == 'checked'
    assert _raw_row(db_path, tx_id).amount == 12.5


def test_bulk_update_shares_one_change_group(core, db_path):
    ids = [core.create_transaction(f'SHOP {i}', 10 + i, '2026-11-01')['id'] for i in range(3)]
    category = next(c for c in core.list_categories() if c['name'] == 'Shopping')['id']
    result = core.bulk_update(ids, {'category_id': category})

    assert result['updated'] == 3
    assert result['failed'] == []
    from app.models import TransactionEdit
    with activate_db(db_path):
        session = get_session(db_path)
        groups = {e.change_group for e in session.query(TransactionEdit).all()}
        session.close()
    assert len(groups) == 1


def test_context_has_the_agent_fields(core):
    core.create_transaction('ACME SALARY', 3200, '2026-11-01', transaction_type='income')
    core.create_transaction('TESCO', 42.10, '2026-11-02')
    context = core.get_context('30d')

    assert context['cashflow']['income'] == 3200
    assert context['cashflow']['expenses'] == 42.10
    assert context['cashflow']['net'] == pytest.approx(3157.90)
    assert 'alerts' in context and 'accounts' in context


def test_dashboard_page_renders_widget_data(core):
    core.create_transaction('TESCO', 42.10, '2026-11-02')
    page = core.create_dashboard('Goals board', [
        {'type': 'metric', 'title': 'Net worth', 'config': {'metric': 'net_worth'}},
        {'type': 'chart', 'title': 'By category', 'config': {'report': 'spending_by_category'}},
        {'type': 'text', 'title': 'Note', 'config': {'markdown': 'hello'}},
    ])

    assert page['slug'] == 'goals-board'
    assert len(page['widgets']) == 3
    assert page['widgets'][0]['data']['value'] is not None
    assert page['widgets'][1]['data']['groups'][0]['key'] == 'Uncategorised'
    assert page['widgets'][2]['data']['markdown'] == 'hello'


def test_goals_progress(core):
    goal = core.create_goal('Japan trip', 2000)
    core.contribute_to_goal(goal['id'], 500)
    goals = core.list_goals()
    assert goals[0]['percent'] == 25.0


def test_forecast_and_anomalies_run(core):
    for day in range(1, 9):
        core.create_transaction('NETFLIX.COM', 10.99, f'2026-10-{day:02d}')
    forecast = core.cashflow_forecast(30)
    assert forecast['projected_spend'] >= 0
    anomalies = core.find_anomalies('1y')
    assert anomalies['counts']['transactions'] == 8


def test_short_rule_does_not_match_inside_words(core):
    core.create_transaction('NETFLIX.COM', 10.99, '2026-11-02')
    core.apply_rules(dry_run=False)
    tx = core.list_transactions()['transactions'][0]
    assert tx.get('category_name') != 'Transport'


def test_dashboard_report_has_totals(core):
    core.create_transaction('TESCO', 42.10, '2026-11-02')
    stats = core.report('dashboard')
    assert stats['current_month']['transaction_count'] == 1
    assert 'total_in_sub_accounts' in stats['totals']
    assert stats['totals']['uncategorized'] >= 0

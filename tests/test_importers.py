"""Import engine: layouts, formats, idempotency, reversibility."""
import pytest

from app.importers.pdf import parse_pdf
from app.importers.profiles import load_profiles
from app.importers.tabular import parse_csv, parse_mt940, parse_ofx, parse_qif
from tests.statement_factory import (make_csv, make_mt940, make_ofx, make_pdf,
                                     make_qif)

NET = 3105.66


def _profile(profile_id):
    return next(p for p in load_profiles() if p['id'] == profile_id)


@pytest.mark.parametrize('layout,profile_id', [
    ('hsbc', 'hsbc_uk'),
    ('barclays', 'barclays_uk'),
    ('monzo', 'monzo_uk'),
])
def test_pdf_layouts_parse_identically(tmp_path, layout, profile_id):
    path = make_pdf(str(tmp_path / f'{layout}.pdf'), layout)
    result = parse_pdf(path, _profile(profile_id))

    assert len(result.rows) == 5, result.warnings
    net = sum(r.amount if r.kind == 'income' else -r.amount for r in result.rows)
    assert round(net, 2) == NET
    assert result.rows[0].description == 'TESCO STORES 1234'
    assert result.rows[2].kind == 'income'
    assert result.rows[2].amount == 3200.00
    assert result.rows[0].balance == 1284.55
    assert result.confidence == 1.0


def test_pdf_balance_continuity_detects_broken_rows(tmp_path):
    """A statement whose running balance skips must be flagged, not trusted."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    path = str(tmp_path / 'broken.pdf')
    c = canvas.Canvas(path, pagesize=A4)
    c.setFont('Helvetica-Bold', 8)
    c.drawString(70, 730, 'Date')
    c.drawString(300, 730, 'Details')
    c.drawRightString(500, 730, 'Paid out')
    c.drawRightString(630, 730, 'Balance')
    c.setFont('Helvetica', 8)
    c.drawString(70, 710, '03 Nov 26')
    c.drawString(300, 710, 'TESCO')
    c.drawRightString(500, 710, '42.10')
    c.drawRightString(630, 710, '100.00')
    c.drawString(70, 694, '04 Nov 26')
    c.drawString(300, 694, 'LIDL')
    c.drawRightString(500, 694, '28.45')
    c.drawRightString(630, 694, '999.99')  # wrong on purpose
    c.save()

    result = parse_pdf(path, _profile('generic'))
    assert any('break the running balance' in w for w in result.warnings)


def test_csv_income_sign(tmp_path):
    result = parse_csv(make_csv(str(tmp_path / 'monzo.csv')), _profile('generic'))
    assert len(result.rows) == 3
    assert result.rows[2].kind == 'income'
    assert result.rows[2].amount == 3200.00


def test_ofx(tmp_path):
    result = parse_ofx(make_ofx(str(tmp_path / 'a.ofx')), _profile('generic'))
    assert [r.kind for r in result.rows] == ['expense', 'income']
    assert result.rows[0].external_id == 'T1'


def test_qif(tmp_path):
    result = parse_qif(make_qif(str(tmp_path / 'a.qif')), _profile('generic'))
    assert len(result.rows) == 2
    assert result.rows[0].amount == 42.10
    assert result.rows[1].kind == 'income'


def test_mt940_decimal_comma(tmp_path):
    result = parse_mt940(make_mt940(str(tmp_path / 'a.sta')), _profile('generic'))
    assert [r.amount for r in result.rows] == [42.10, 3200.00]
    assert result.rows[0].kind == 'expense'


def test_preview_commit_dedupe_revert(core, tmp_path):
    path = make_pdf(str(tmp_path / 'hsbc.pdf'), 'hsbc')

    preview = core.import_preview(path)
    assert preview['batch']['parsed'] == 5
    assert preview['batch']['confidence'] == 1.0

    commit = core.import_commit(preview['batch']['id'])
    assert commit['imported'] == 5
    assert core.list_transactions()['total'] == 5

    again = core.import_preview(path)
    assert again['batch']['skipped'] == 5

    revert = core.import_revert(preview['batch']['id'])
    assert revert['deleted'] == 5
    assert core.list_transactions()['total'] == 0


def test_commit_is_atomic_per_row(core, tmp_path):
    """One bad row must not lose the rest of the batch."""
    path = make_csv(str(tmp_path / 'bad.csv'))
    preview = core.import_preview(path)
    rows = preview['rows']
    edits = [{'row_id': rows[1]['row_id'], 'amount': 0}]  # invalid: zero amount

    result = core.import_commit(preview['batch']['id'], edits=edits)
    assert result['imported'] == 2
    assert len(result['failed']) == 1
    assert core.list_transactions()['total'] == 2


def test_imported_rows_are_raw_locked(core, tmp_path):
    from app.core import RawRecordLocked
    path = make_csv(str(tmp_path / 'raw.csv'))
    preview = core.import_preview(path)
    core.import_commit(preview['batch']['id'])
    tx = core.list_transactions()['transactions'][0]

    with pytest.raises(RawRecordLocked):
        core.update_transaction(tx['id'], {'amount': 1.0})

    annotated = core.update_transaction(tx['id'], {'notes': 'reviewed'})
    assert annotated['notes'] == 'reviewed'


def test_statement_detection_prefers_the_right_profile(core, tmp_path):
    hsbc = make_pdf(str(tmp_path / 'a.pdf'), 'hsbc')
    monzo = make_pdf(str(tmp_path / 'b.pdf'), 'monzo')
    assert core.import_preview(hsbc)['batch']['profile'] == 'hsbc_uk'
    assert core.import_preview(monzo)['batch']['profile'] == 'monzo_uk'

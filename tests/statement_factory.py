"""Generate bank statements in several layouts for parser tests."""
from __future__ import annotations

from datetime import datetime


TXNS = [
    ('03 Nov 26', 'TESCO STORES 1234', '42.10', '', '1,284.55'),
    ('04 Nov 26', 'TFL TRAVEL CHARGE', '12.80', '', '1,271.75'),
    ('05 Nov 26', 'SALARY ACME LTD', '', '3,200.00', '4,471.75'),
    ('06 Nov 26', 'NETFLIX.COM', '10.99', '', '4,460.76'),
    ('07 Nov 26', 'LIDL GB', '28.45', '', '4,432.31'),
]


def make_pdf(path: str, layout: str) -> str:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path, pagesize=A4)
    if layout == 'hsbc':
        c.setFont('Helvetica-Bold', 16)
        c.drawString(60, 800, 'HSBC UK Bank plc')
        c.setFont('Helvetica', 9)
        c.drawString(60, 780, 'Statement of account   Sort code 40-11-22   Account 12345678')
        c.setFont('Helvetica-Bold', 8)
        c.drawString(70, 730, 'Date')
        c.drawString(190, 730, 'Payment type')
        c.drawString(300, 730, 'Details')
        c.drawRightString(500, 730, 'Paid out')
        c.drawRightString(560, 730, 'Paid in')
        c.drawRightString(630, 730, 'Balance')
        c.setFont('Helvetica', 8)
        y = 710
        for date, desc, out, inn, balance in TXNS:
            c.drawString(70, y, date)
            c.drawString(190, y, 'VIS')
            c.drawString(300, y, desc)
            if out:
                c.drawRightString(500, y, out)
            if inn:
                c.drawRightString(560, y, inn)
            c.drawRightString(630, y, balance)
            y -= 16
    elif layout == 'barclays':
        c.setFont('Helvetica-Bold', 14)
        c.drawString(60, 800, 'Barclays Bank UK PLC')
        c.setFont('Helvetica-Bold', 8)
        c.drawString(70, 740, 'Date')
        c.drawString(150, 740, 'Description')
        c.drawRightString(490, 740, 'Money out')
        c.drawRightString(560, 740, 'Money in')
        c.drawRightString(640, 740, 'Balance')
        c.setFont('Helvetica', 8)
        y = 720
        for date, desc, out, inn, balance in TXNS:
            c.drawString(70, y, date)
            c.drawString(150, y, desc)
            if out:
                c.drawRightString(490, y, '£' + out)
            if inn:
                c.drawRightString(560, y, '£' + inn)
            c.drawRightString(640, y, balance)
            y -= 18
    elif layout == 'monzo':
        c.setFont('Helvetica-Bold', 14)
        c.drawString(60, 800, 'Monzo Bank')
        c.setFont('Helvetica-Bold', 8)
        c.drawString(70, 750, 'Date')
        c.drawString(160, 750, 'Transaction')
        c.drawRightString(620, 750, 'Amount')
        c.drawRightString(700, 750, 'Balance')
        c.setFont('Helvetica', 8)
        y = 730
        for date, desc, out, inn, balance in TXNS:
            c.drawString(70, y, date)
            c.drawString(160, y, desc)
            amount = ('+' if inn else '-') + (inn or out)
            c.drawRightString(620, y, amount)
            c.drawRightString(700, y, balance)
            y -= 17
    else:
        raise ValueError(layout)
    c.save()
    return path


def make_csv(path: str) -> str:
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('Date,Description,Amount,Balance,Category\n')
        fh.write('03/11/2026,Tesco Stores,-42.10,1284.55,Groceries\n')
        fh.write('04/11/2026,TFL Travel,-12.80,1271.75,Transport\n')
        fh.write('05/11/2026,Acme Salary,3200.00,4471.75,Income\n')
    return path


def make_ofx(path: str) -> str:
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write("""OFXHEADER:100
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>GBP
<BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261103<TRNAMT>-42.10<FITID>T1<NAME>TESCO STORES</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261105<TRNAMT>3200.00<FITID>T2<NAME>ACME SALARY</STMTTRN>
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
""")
    return path


def make_qif(path: str) -> str:
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write("""!Type:Bank
D03/11/2026
T-42.10
PTesco Stores
^
D05/11/2026
T3200.00
PAcme Salary
^
""")
    return path


def make_mt940(path: str) -> str:
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(""":20:TALLY
:60F:C261101GBP1284,55
:61:2611031103D42,10NTRFNONREF
:86:TESCO STORES
:61:2611051105C3200,00NTRFNONREF
:86:ACME SALARY
:62F:C261107GBP4432,31
""")
    return path

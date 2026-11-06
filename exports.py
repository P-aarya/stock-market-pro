"""
Excel downloads:
  * my_data_workbook   - a person's own Portfolio and Transactions (Settings → Your data)
  * users_workbook     - every account with its profile details, plus a summary (Admin Center → Users)

Built with openpyxl. Text is always stored as text, never as a formula, so a name like "=HYPERLINK(...)" can't run when
the file is opened in Excel.
"""
import datetime as dt
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

NAVY = "000E21"
HEAD_FILL = PatternFill("solid", fgColor=NAVY)
HEAD_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14, color=NAVY)
NOTE_FONT = Font(italic=True, size=9, color="666666")
TOTAL_FONT = Font(bold=True)
GREEN, RED = Font(color="0A7A3D"), Font(color="C62828")
GREEN_BOLD, RED_BOLD = Font(color="0A7A3D", bold=True), Font(color="C62828", bold=True)
GOLD = Font(color="9A6700", bold=True)
THIN = Side(style="thin", color="BBBBBB")
TOP_LINE = Border(top=THIN)
MONEY = '"$"#,##0.00;-"$"#,##0.00'
PCT = '0.00"%";-0.00"%"'
QTY = '#,##0.####'
DATE = "dd mmm yyyy"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def put(cell, value):
    """Store a value; text is always kept as text (openpyxl would otherwise turn "=..." into a formula)."""
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"
    return cell


def _date(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and value:
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return value
    return value


def _sheet_header(ws, title, subtitle, columns):
    """Title, a grey note, a blank row, then the column headings (row 4). Returns the first data row."""
    put(ws["A1"], title).font = TITLE_FONT
    put(ws["A2"], subtitle).font = NOTE_FONT
    for i, c in enumerate(columns, start=1):
        put(ws.cell(row=4, column=i), c[0])
    for i, (_, width, _) in enumerate(columns, start=1):
        cell = ws.cell(row=4, column=i)
        cell.fill, cell.font = HEAD_FILL, HEAD_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[4].height = 30
    ws.freeze_panes = "A5"
    return 5


def _write_row(ws, row, columns, values):
    for i, ((_, _, fmt), v) in enumerate(zip(columns, values), start=1):
        cell = put(ws.cell(row=row, column=i), v)
        if fmt and v is not None and not isinstance(v, str):
            cell.number_format = fmt
    return row + 1


def _colour(cell, value, bold=False):
    if isinstance(value, (int, float)) and value:
        cell.font = (GREEN_BOLD if value > 0 else RED_BOLD) if bold else (GREEN if value > 0 else RED)


def _kind(row):
    """The Type the dashboard shows: "Stock", "Commodity company · Gold" or "Commodity"."""
    if row.get("asset_type") == "commodity":
        return "Commodity"
    return f"Commodity company · {row['commodity_company']}" if row.get("commodity_company") else "Stock"


def _name(row):
    """Company name, or for a commodity owned directly its name and unit: "Gold (per troy ounce)"."""
    if row.get("asset_type") == "commodity":
        return f"{row.get('commodity') or row.get('company_name') or ''} (per {row.get('unit') or 'unit'})"
    return row.get("company_name") or ""


def _bytes(wb):
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ── a person's own data ──

PORTFOLIO_COLUMNS = [("Ticker", 10, None), ("Name", 30, None), ("Type", 26, None), ("Sector", 22, None), ("Quantity", 11, QTY),
                     ("Average buy price (per share / unit)", 16, MONEY), ("Buy date", 13, DATE), ("Current price", 13, MONEY),
                     ("Invested", 14, MONEY), ("Current value", 14, MONEY), ("Profit / loss", 14, MONEY),
                     ("Profit / loss %", 13, PCT), ("Notes", 30, None)]
TX_COLUMNS = [("Date", 13, DATE), ("Type", 10, None), ("Ticker", 10, None), ("Name", 30, None), ("Stock / commodity", 26, None), ("Quantity", 11, QTY),
              ("Price", 13, MONEY), ("Total", 14, MONEY), ("Notes", 30, None)]
TX_TYPES = {"BUY": "Buy", "SELL": "Sell", "DIVIDEND": "Dividend"}


def my_data_workbook(email, holdings, summary, transactions, tx_summary, now=None):
    """Portfolio and Transactions sheets. `holdings` come from portfolio.list_holdings (with values),
    `summary` from portfolio.summarize, `transactions` from portfolio.list_transactions,
    `tx_summary` from portfolio.transaction_summary."""
    now = now or dt.datetime.now()
    stamp = now.strftime("%d %b %Y, %H:%M")
    wb = Workbook()

    ws = wb.active
    ws.title = "Portfolio"
    row = _sheet_header(ws, "Aymetrix · My portfolio", f"{email} · downloaded {stamp} · values use the latest prices on Aymetrix", PORTFOLIO_COLUMNS)
    if not holdings:
        ws.cell(row=row, column=1, value="No holdings yet.").font = NOTE_FONT
    for h in holdings:
        values = [h.get("ticker"), _name(h), _kind(h), h.get("sector"), h.get("quantity"), h.get("avg_buy_price"),
                  _date(h.get("buy_date")), h.get("current_price"), h.get("invested_value"), h.get("current_value"),
                  h.get("unrealized_pnl"), h.get("pnl_pct"), h.get("notes") or ""]
        _write_row(ws, row, PORTFOLIO_COLUMNS, values)
        if h.get("asset_type") == "commodity" or h.get("commodity_company"):
            ws.cell(row=row, column=3).font = GOLD
        _colour(ws.cell(row=row, column=11), h.get("unrealized_pnl")); _colour(ws.cell(row=row, column=12), h.get("pnl_pct"))
        row += 1
    if holdings:
        total = [("Total", 1), (summary.get("total_invested"), 9), (summary.get("total_value"), 10), (summary.get("total_pnl"), 11),
                 (summary.get("return_pct"), 12)]
        for value, col in total:
            cell = ws.cell(row=row, column=col, value=value)
            cell.font = TOTAL_FONT
            if col > 1 and value is not None:
                cell.number_format = PCT if col == 12 else MONEY
        for col in range(1, len(PORTFOLIO_COLUMNS) + 1):
            ws.cell(row=row, column=col).border = TOP_LINE
        _colour(ws.cell(row=row, column=11), summary.get("total_pnl"), bold=True)
        _colour(ws.cell(row=row, column=12), summary.get("return_pct"), bold=True)
        if summary.get("unpriced_holdings"):
            ws.cell(row=row + 2, column=1, value=f"No live price for: {', '.join(summary.get('unpriced_tickers') or [])}. "
                                                 "Those holdings are left out of the current value and profit / loss.").font = NOTE_FONT

    ws = wb.create_sheet("Transactions")
    row = _sheet_header(ws, "Aymetrix · My transactions", f"{email} · downloaded {stamp} · newest first", TX_COLUMNS)
    if not transactions:
        ws.cell(row=row, column=1, value="No transactions yet.").font = NOTE_FONT
    for t in transactions:
        qty, price = t.get("quantity"), t.get("price")
        total = t.get("total_value")
        if total is None and isinstance(qty, (int, float)) and isinstance(price, (int, float)):
            total = round(qty * price, 2)
        values = [_date(t.get("transaction_date")), TX_TYPES.get(t.get("transaction_type"), t.get("transaction_type")), t.get("ticker"),
                  _name(t), _kind(t), qty, price, total, t.get("notes") or ""]
        _write_row(ws, row, TX_COLUMNS, values)
        kind = t.get("transaction_type")
        ws.cell(row=row, column=2).font = GREEN if kind == "BUY" else RED if kind == "SELL" else Font(color="1565C0")
        if t.get("asset_type") == "commodity" or t.get("commodity_company"):
            ws.cell(row=row, column=5).font = GOLD
        row += 1
    if transactions:
        row += 1
        lines = [("Total bought", tx_summary.get("total_invested")), ("Total sold", tx_summary.get("total_sold")),
                 ("Dividends received", tx_summary.get("dividends")), ("Realised profit / loss", tx_summary.get("realized_pnl"))]
        for label, value in lines:
            ws.cell(row=row, column=7, value=label).font = TOTAL_FONT
            cell = ws.cell(row=row, column=8, value=value)
            cell.number_format, cell.font = MONEY, TOTAL_FONT
            if label.startswith("Realised"):
                _colour(cell, value, bold=True)
            row += 1
    return _bytes(wb)


# ── every account (administrators) ──

USER_COLUMNS = [("Email", 32, None), ("Name", 22, None), ("Status", 11, None), ("Admin", 8, None), ("Joined", 13, DATE),
                ("Last sign-in", 17, "dd mmm yyyy hh:mm"), ("Region / city", 20, None), ("Date of birth", 13, DATE), ("Age", 7, None),
                ("Gender", 16, None), ("Profession", 24, None)]
STATUS_LABELS = {"approved": "Approved", "pending": "Pending", "blocked": "Blocked"}


def _when(seconds):
    return dt.datetime.fromtimestamp(int(seconds)) if seconds else None


def users_workbook(users, details, genders, age_on, now=None):
    """Users sheet (one row per account, newest first) and a Summary sheet. `details` maps user id → profile details."""
    now = now or dt.datetime.now()
    stamp = now.strftime("%d %b %Y, %H:%M")
    wb = Workbook()
    ws = wb.active
    ws.title = "Users"
    row = _sheet_header(ws, "Aymetrix · Users", f"Downloaded {stamp} · {len(users)} accounts · personal data: keep this file private", USER_COLUMNS)
    for u in users:
        d = details.get(u["id"]) or {}
        birth = d.get("birth_date")
        values = [u["email"], u.get("full_name") or "", STATUS_LABELS.get(u["status"], u["status"]), "Yes" if u.get("is_admin") else "No",
                  _when(u.get("created_at")), _when(u.get("last_login_at")), d.get("city") or "", birth,
                  age_on(birth) if birth else None, genders.get(d.get("gender") or "", d.get("gender") or ""), d.get("profession") or ""]
        _write_row(ws, row, USER_COLUMNS, values)
        row += 1
    ws.auto_filter.ref = f"A4:{get_column_letter(len(USER_COLUMNS))}{max(4, row - 1)}"

    ts = now.timestamp()
    day, week = ts - 86400, ts - 7 * 86400
    s = wb.create_sheet("Summary")
    s.append(["Aymetrix · User summary"]); s["A1"].font = TITLE_FONT
    s.append([f"Downloaded {stamp}"]); s["A2"].font = NOTE_FONT
    s.append([])
    lines = [("Total accounts", len(users)),
             ("Approved", sum(1 for u in users if u["status"] == "approved")),
             ("Waiting for approval", sum(1 for u in users if u["status"] == "pending")),
             ("Blocked", sum(1 for u in users if u["status"] == "blocked")),
             ("Administrators", sum(1 for u in users if u.get("is_admin"))),
             ("New in the last 7 days", sum(1 for u in users if (u.get("created_at") or 0) >= week)),
             ("Signed in in the last 24 hours", sum(1 for u in users if (u.get("last_login_at") or 0) >= day)),
             ("Signed in in the last 7 days", sum(1 for u in users if (u.get("last_login_at") or 0) >= week)),
             ("Filled in their profile details", sum(1 for u in users if any((details.get(u["id"]) or {}).get(k) for k in ("city", "birth_date", "gender", "profession"))))]
    for label, value in lines:
        s.append([label, value])
        s.cell(row=s.max_row, column=1).font = TOTAL_FONT
    s.column_dimensions["A"].width, s.column_dimensions["B"].width = 34, 12
    return _bytes(wb)

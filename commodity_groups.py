"""
Which stocks are commodity companies, and of which commodity.

Two things live here:
  * COMMODITY_COMPANIES - stocks of companies that produce a commodity (Newmont → gold). They are shares, so they count
    as Stocks, labelled "Commodity company · Gold".
  * DIRECT_COMMODITIES - commodities owned directly (gold by the ounce, oil by the barrel), labelled "Commodity", with
    their unit and live price.
The companies are the ones on the dashboard's Commodities tab (COM_COMPANIES in dashboard.html), keeping only the real
commodity groups: metals and mining, oil and gas, uranium, agriculture, livestock and timber. Groups that are not
commodities (food and drink brands, crypto, clean energy, water, shipping, AI and robotics) stay ordinary stocks.
A test checks this list against the Commodities tab so the two never drift apart.
"""

GROUP_LABELS = {
    "gold": "Gold", "silver": "Silver", "copper": "Copper", "steel": "Steel", "aluminium": "Aluminium", "mining": "Mining",
    "lithium": "Lithium", "uranium": "Uranium", "oil": "Oil", "gas": "Natural gas", "agriculture": "Agriculture",
    "livestock": "Livestock", "timber": "Timber",
}

COMMODITY_COMPANIES = {
    # Gold
    "NEM": "gold",
    "GOLD": "gold",
    "AEM": "gold",
    "FNV": "gold",
    "WPM": "gold",
    "BTG": "gold",
    "IAG": "gold",
    # Silver
    "AG": "silver",
    "PAAS": "silver",
    "CDE": "silver",
    "HL": "silver",
    "EXK": "silver",
    # Copper
    "FCX": "copper",
    "SCCO": "copper",
    "TECK": "copper",
    "HBM": "copper",
    # Steel
    "NUE": "steel",
    "X": "steel",
    "STLD": "steel",
    "CLF": "steel",
    "CMC": "steel",
    # Aluminium
    "AA": "aluminium",
    "CENX": "aluminium",
    # Mining
    "VALE": "mining",
    "BHP": "mining",
    "RIO": "mining",
    # Lithium
    "ALB": "lithium",
    "MP": "lithium",
    "LAC": "lithium",
    "LTHM": "lithium",
    "SQM": "lithium",
    # Uranium
    "CCJ": "uranium",
    "UEC": "uranium",
    "UUUU": "uranium",
    "DNN": "uranium",
    # Oil
    "XOM": "oil",
    "CVX": "oil",
    "COP": "oil",
    "OXY": "oil",
    "PXD": "oil",
    "SLB": "oil",
    "HAL": "oil",
    "BKR": "oil",
    # Natural gas
    "EQT": "gas",
    "LNG": "gas",
    "AR": "gas",
    "RRC": "gas",
    "SWN": "gas",
    # Agriculture
    "ADM": "agriculture",
    "BG": "agriculture",
    "MOS": "agriculture",
    "DE": "agriculture",
    "NTR": "agriculture",
    "CF": "agriculture",
    "CTVA": "agriculture",
    "FMC": "agriculture",
    # Livestock
    "TSN": "livestock",
    "HRL": "livestock",
    "SAFM": "livestock",
    # Timber
    "WY": "timber",
    "RYN": "timber",
    "PCH": "timber",
}


# ── Commodities owned directly (2 oz of gold, 50 barrels of oil) ──
# Symbol: Yahoo Finance futures symbol, used for the live price and price history.
# divisor: Yahoo quotes grains, softs and livestock in US cents (shown as "USX"); dividing by 100 gives dollars per unit,
#          so the live price matches the dollar buy price people type in.
# Only commodities with a reliable Yahoo price in a plain unit are offered.
DIRECT_COMMODITIES = {
    # symbol: (name, group, unit, plural unit, short unit, divisor)
    "GC=F": ("Gold", "Precious metals", "troy ounce", "troy ounces", "oz", 1),
    "SI=F": ("Silver", "Precious metals", "troy ounce", "troy ounces", "oz", 1),
    "PL=F": ("Platinum", "Precious metals", "troy ounce", "troy ounces", "oz", 1),
    "PA=F": ("Palladium", "Precious metals", "troy ounce", "troy ounces", "oz", 1),
    "HG=F": ("Copper", "Industrial metals", "pound", "pounds", "lb", 1),
    "ALI=F": ("Aluminium", "Industrial metals", "metric ton", "metric tons", "t", 1),
    "CL=F": ("Crude Oil (WTI)", "Energy", "barrel", "barrels", "bbl", 1),
    "BZ=F": ("Brent Crude Oil", "Energy", "barrel", "barrels", "bbl", 1),
    "NG=F": ("Natural Gas", "Energy", "MMBtu", "MMBtu", "MMBtu", 1),
    "RB=F": ("Gasoline (RBOB)", "Energy", "gallon", "gallons", "gal", 1),
    "HO=F": ("Heating Oil", "Energy", "gallon", "gallons", "gal", 1),
    "ZC=F": ("Corn", "Agriculture", "bushel", "bushels", "bu", 100),
    "ZW=F": ("Wheat", "Agriculture", "bushel", "bushels", "bu", 100),
    "ZS=F": ("Soybeans", "Agriculture", "bushel", "bushels", "bu", 100),
    "ZO=F": ("Oats", "Agriculture", "bushel", "bushels", "bu", 100),
    "ZL=F": ("Soybean Oil", "Agriculture", "pound", "pounds", "lb", 100),
    "KC=F": ("Coffee (Arabica)", "Softs", "pound", "pounds", "lb", 100),
    "SB=F": ("Sugar", "Softs", "pound", "pounds", "lb", 100),
    "CT=F": ("Cotton", "Softs", "pound", "pounds", "lb", 100),
    "CC=F": ("Cocoa", "Softs", "metric ton", "metric tons", "t", 1),
    "LE=F": ("Live Cattle", "Livestock", "pound", "pounds", "lb", 100),
    "GF=F": ("Feeder Cattle", "Livestock", "pound", "pounds", "lb", 100),
    "HE=F": ("Lean Hogs", "Livestock", "pound", "pounds", "lb", 100),
}


def direct(symbol):
    """The catalogue entry for a directly owned commodity as a dict, or None."""
    row = DIRECT_COMMODITIES.get(str(symbol or "").strip().upper())
    if not row:
        return None
    name, group, unit, plural, short, divisor = row
    return {"symbol": str(symbol).strip().upper(), "name": name, "group": group, "unit": unit, "plural": plural, "short": short, "divisor": divisor}


def catalogue():
    """Every commodity that can be added, for the Add Holding / Add Transaction forms."""
    return [direct(s) for s in DIRECT_COMMODITIES]


def amount(quantity, symbol):
    """'2 troy ounces of Gold'."""
    c = direct(symbol)
    q = float(quantity)
    return f"{q:g} {c['unit'] if q == 1 else c['plural']} of {c['name']}"


def classify(ticker):
    """What a holding is:
       ("commodity", "Gold")          a commodity owned directly (GC=F)
       ("commodity_company", "Gold")  shares of a company that produces a commodity (NEM) - counted with Stocks
       ("stock", None)                any other stock"""
    t = str(ticker or "").strip().upper()
    if t in DIRECT_COMMODITIES:
        return ("commodity", DIRECT_COMMODITIES[t][0])
    group = COMMODITY_COMPANIES.get(t)
    return ("commodity_company", GROUP_LABELS[group]) if group else ("stock", None)


def label(ticker):
    """The text shown in the Type column: "Stock", "Commodity company · Gold" or "Commodity"."""
    kind, what = classify(ticker)
    return {"commodity": "Commodity", "commodity_company": f"Commodity company · {what}"}.get(kind, "Stock")


# ── live prices of directly owned commodities ──

PRICE_TTL = 300                      # seconds
_price_cache = {}                    # symbol -> (expires, {"price", "change_pct"} or None)


def _fetch(symbol):
    import yfinance as yf
    c = direct(symbol)
    try:
        hist = yf.Ticker(symbol).history(period="5d", timeout=15)
        closes = hist["Close"].dropna() if hist is not None and not hist.empty else []
        if len(closes) == 0:
            return None
        last = float(closes.iloc[-1]) / c["divisor"]
        prev = float(closes.iloc[-2]) / c["divisor"] if len(closes) > 1 else last
        return {"price": round(last, 4), "change_pct": round((last - prev) / prev * 100, 2) if prev else None}
    except Exception as e:                                   # noqa: BLE001 - a missing price just shows "no price"
        print(f"Commodity price failed for {symbol}: {e}")
        return None


def live_prices(symbols):
    """{symbol: {"price": dollars per unit, "change_pct": today's %} or None}, fetched in parallel and kept 5 minutes."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    now = time.time()
    wanted = sorted({str(s).upper() for s in symbols if direct(s)})
    out, missing = {}, []
    for s in wanted:
        hit = _price_cache.get(s)
        if hit and hit[0] > now:
            out[s] = hit[1]
        else:
            missing.append(s)
    if missing:
        with ThreadPoolExecutor(max_workers=min(8, len(missing))) as pool:
            for s, value in zip(missing, pool.map(_fetch, missing)):
                out[s] = value
                _price_cache[s] = (now + (PRICE_TTL if value else 60), value)
    return out

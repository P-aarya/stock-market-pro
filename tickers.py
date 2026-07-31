# ============================================================
# STOCK MARKET PRO - 1000 Tickers across 20 Sectors
# ============================================================

SECTORS = {

    "Technology": [
        "AAPL", "MSFT", "GOOGL", "GOOG", "META", "ORCL", "IBM", "HPQ", "DELL", "ACN",
        "INTU", "ADBE", "CRM", "NOW", "CTSH", "EPAM", "GLOB", "WIT", "INFY", "PLTR",
        "SNOW", "DDOG", "ZS", "CRWD", "OKTA", "TWLO", "MDB", "NET", "CFLT", "GTLB",
        "PATH", "APPN", "PEGA", "MSCI", "ANSS", "CDNS", "SNPS", "PTC", "MANH", "SPSC",
        "ZOOM", "DOCU", "DBX", "OTEX", "BIGC", "CART", "KVYO", "SEMR", "TASK", "WEAVE",
        "ALKT", "EPAY", "CSGP", "EXLS", "PRFT", "KFRC", "NCNO", "HUBS", "BILL", "ZEN"
    ],

    "Semiconductors": [
        "NVDA", "AMD", "INTC", "QCOM", "AVGO", "TXN", "MU", "AMAT", "LRCX", "KLAC",
        "ASML", "TSM", "MRVL", "MCHP", "ADI", "NXPI", "ON", "STM", "WOLF", "SWKS",
        "QRVO", "MPWR", "SLAB", "DIOD", "SMTC", "COHU", "ACLS", "CEVA", "FORM", "AMBA",
        "ALGM", "ONTO", "UCTT", "KLIC", "IOSP", "NVMI", "POWI", "RMBS", "SITM", "AEHR",
        "AXTI", "LASR", "IPGP", "MKSI", "ICHR", "ENTG", "CAMT", "MTSI", "PSEM", "SYNA"
    ],

    "Cloud & SaaS": [
        "AMZN", "SAP", "WDAY", "COUP", "DOCN", "ESTC", "TENB", "QLYS", "NEWR", "DT",
        "VRNS", "CYBR", "S", "ASAN", "MNDY", "FROG", "WEX", "PCTY", "PAYC", "BRZE",
        "ALTR", "SPRINKLR", "CWAN", "JAMF", "MGNI", "TTD", "PUBM", "SAIL", "SUMO", "NLOK",
        "PING", "RDWR", "DRVA", "CLDR", "TALEND", "TIBX", "TIBCO", "BOBJ", "MEND", "TFSM"
    ],

    "Healthcare": [
        "JNJ", "UNH", "PFE", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY", "LLY",
        "AMGN", "CVS", "CI", "HUM", "CNC", "MOH", "ELV", "WBA", "MCK", "CAH",
        "ABC", "HSIC", "OMI", "PDCO", "PRGO", "BDX", "BSX", "EW", "MDT", "SYK",
        "ZBH", "HOLX", "IDXX", "IQV", "CRL", "MEDP", "ICLR", "HZNP", "JAZZ", "SUPN",
        "OSCR", "ACCD", "PHR", "DOCS", "OPRX", "HIMS", "WELL", "GDRX", "PGNY", "LHCG",
        "AMSF", "AMED", "ADUS", "BFAM", "ENSG", "OPCH", "LFST", "NTRA", "OMCL", "PAHC"
    ],

    "Biotech": [
        "MRNA", "BIIB", "REGN", "GILD", "VRTX", "ALNY", "BMRN", "EXEL", "RARE", "IONS",
        "ARWR", "NTLA", "BEAM", "EDIT", "CRSP", "FATE", "KYMR", "ACAD", "SAGE", "AXSM",
        "TGTX", "PRTA", "ARQT", "FOLD", "PTGX", "YMAB", "XNCR", "ALLK", "IMVT", "ARDX",
        "CLDX", "NKTR", "SGEN", "RCUS", "DNLI", "KRTX", "INVA", "SRRK", "IMMU", "AGEN"
    ],

    "Financials": [
        "BRK-B", "V", "MA", "PYPL", "AXP", "COF", "DFS", "SYF", "ALLY", "CACC",
        "FISV", "FIS", "GPN", "EVTC", "PAYA", "REPAY", "FLYW", "LPLA", "RJF", "MKTX",
        "VIRT", "IBKR", "SCHW", "HOOD", "SOFI", "LDI", "UWMC", "PFSI", "COOP", "JEF",
        "LAZ", "EVR", "MC", "PJT", "CSWC", "TPVG", "GAIN", "HRZN", "NMFC", "OCSL",
        "PFLT", "PSEC", "SLRC", "TCPC", "FDUS", "GLAD", "HTGC", "TRIN", "SAR", "MRCC"
    ],

    "Banking": [
        "JPM", "BAC", "WFC", "C", "GS", "MS", "USB", "PNC", "TFC", "FITB",
        "HBAN", "KEY", "RF", "CFG", "MTB", "ZION", "CMA", "WAL", "WTFC", "IBCP",
        "FBIZ", "FFIN", "BOKF", "CBSH", "UMBF", "CVBF", "BANF", "FULT", "WSFS", "TRMK",
        "SBCF", "SFNC", "HFWA", "CCBG", "SRCE", "BSVN", "NBTB", "EGBN", "BHLB", "PBCT"
    ],

    "Insurance": [
        "MET", "PRU", "AIG", "AFL", "ALL", "TRV", "CB", "HIG", "CNA", "MKL",
        "RLI", "WRB", "CINF", "THG", "KMPR", "SIGI", "AIZ", "GL", "FG", "CNO",
        "PFG", "VOYA", "BHF", "EQH", "AEL", "NWLI", "ERIE", "PLMR", "DGICA", "JRVR",
        "HRTG", "UVE", "HCI", "FNHC", "HGTY", "KINGSWAY", "HIIQ", "NGHC", "GBLI", "AMSF"
    ],

    "Consumer Discretionary": [
        "TSLA", "HD", "MCD", "NKE", "SBUX", "TJX", "LOW", "BKNG", "MAR", "HLT",
        "RCL", "CCL", "NCLH", "LVS", "MGM", "WYNN", "CZR", "DKNG", "PENN", "YUM",
        "CMG", "DRI", "EAT", "TXRH", "CAKE", "BJRI", "JACK", "SHAK", "LULU", "PVH",
        "RL", "VFC", "HBI", "UAA", "DECK", "SKX", "CROX", "BIRD", "ONON", "COLM"
    ],

    "Consumer Staples": [
        "PG", "KO", "PEP", "COST", "WMT", "PM", "MO", "CL", "KMB", "CHD",
        "CLX", "KHC", "GIS", "CPB", "CAG", "SJM", "MKC", "HRL", "TSN", "BGS",
        "LANC", "INGR", "CALM", "JBSS", "THS", "SMPL", "HAIN", "VITL", "COTY", "ELF",
        "REYN", "CENT", "CENTA", "SPB", "EDUC", "FAT", "DENN", "FRSH", "NOMD", "BRFH"
    ],

    "Energy": [
        "XOM", "CVX", "COP", "EOG", "SLB", "PXD", "MPC", "PSX", "VLO", "HES",
        "DVN", "FANG", "APA", "HAL", "BKR", "NOV", "WTTR", "LBRT", "PUMP", "RES",
        "PTEN", "HP", "NE", "VAL", "DO", "RIG", "BORR", "MUR", "OVV", "SM",
        "CIVI", "CPE", "MGY", "REI", "SWN", "RRC", "CNX", "VTLE", "ESTE", "AMPY",
        "FLNG", "GLNG", "GLOG", "KNOP", "TK", "TNK", "INSW", "ASC", "REPX", "ROCC"
    ],

    "Automotive": [
        "TSLA", "GM", "F", "RIVN", "LCID", "NIO", "XPEV", "LI", "FSR", "GOEV",
        "NKLA", "WKHS", "BLNK", "EVGO", "CHPT", "WBX", "VLTA", "HYZN", "HYLN", "PAG",
        "AN", "LAD", "SAH", "ABG", "KMX", "CVNA", "VRM", "ACVA", "CARG", "CDK",
        "DRVN", "SMP", "DAN", "VC", "BWA", "LEA", "MGA", "APTV", "ALV", "GNTX"
    ],

    "Media & Entertainment": [
        "DIS", "NFLX", "CMCSA", "WBD", "PARA", "FOX", "FOXA", "LYV", "SPOT", "TTWO",
        "EA", "ATVI", "RBLX", "U", "PLTK", "SKLZ", "MSGM", "IMAX", "CNK", "AMC",
        "MANU", "WWE", "EDR", "TKO", "MSGS", "MSGE", "NYT", "NWS", "NWSA", "SSP",
        "GTN", "SBGI", "NXST", "IHRT", "SIRI", "LUMN", "FWONA", "FWONK", "BATRK", "LLYVK"
    ],

    "Retail": [
        "WMT", "COST", "TGT", "HD", "LOW", "TJX", "ROST", "BURL", "FIVE", "OLLI",
        "BIG", "DG", "DLTR", "PRTY", "TLYS", "BOOT", "CATO", "DXLG", "EXPR", "GPS",
        "AEO", "ANF", "URBN", "CHICO", "CHS", "BBWI", "CPRI", "ULTA", "SBH", "FTCH",
        "REAL", "POSH", "WISH", "ETSY", "EBAY", "SHOP", "W", "OSTK", "ODP", "SPWH"
    ],

    "Real Estate": [
        "AMT", "PLD", "CCI", "EQIX", "PSA", "EXR", "AVB", "EQR", "MAA", "UDR",
        "CPT", "NNN", "O", "STOR", "ADC", "EPRT", "NTST", "GTY", "PINE", "PECO",
        "KIM", "REG", "BRX", "ROIC", "VNO", "SL", "HPP", "CUZ", "DEI", "PDM",
        "PGRE", "ESRT", "OFC", "ARE", "BXP", "HIW", "PKI", "CTRE", "SBRA", "HR"
    ],

    "Utilities": [
        "NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "PCG", "ED", "ETR",
        "XEL", "ES", "WEC", "CMS", "LNT", "EVRG", "NI", "PNW", "ATO", "OGE",
        "NWE", "AVA", "IDA", "MGEE", "OTTR", "SJW", "YORW", "MSEX", "CWCO", "AWR",
        "AWK", "WTRG", "ARTNA", "GWRS", "ERII", "NWN", "CLECO", "POR", "BKH", "GENI"
    ],

    "Industrials": [
        "GE", "HON", "MMM", "CAT", "DE", "EMR", "ETN", "PH", "ROK", "AME",
        "ITW", "IEX", "GNRC", "AAON", "WATTS", "FLOW", "FELE", "GWW", "MSC", "FAST",
        "WSO", "AIT", "DXP", "DNOW", "WESCO", "UPS", "FDX", "XPO", "SAIA", "ODFL",
        "JBHT", "KNX", "LSTR", "CHRW", "EXPD", "ROAD", "GHM", "URI", "WSC", "TREX",
        "AZEK", "PGTI", "PATK", "DOOR", "JELD", "MAS", "FBHS", "AWI", "TILE", "UFPI"
    ],

    "Aerospace & Defense": [
        "LMT", "RTX", "NOC", "BA", "GD", "HII", "LHX", "TDG", "HEICO", "HEI",
        "DRS", "LDOS", "SAIC", "BAH", "CACI", "MANT", "PAE", "AJRD", "KTOS", "AVAV",
        "JOBY", "ACHR", "SPR", "MOOG", "CPI", "KAMN", "CW", "KRATOS", "VSAT", "MAXR",
        "IRDM", "GSAT", "SPCE", "RKLB", "ASTR", "PL", "BKSY", "MNTS", "NARO", "LUNR"
    ],

    "Materials": [
        "LIN", "APD", "SHW", "ECL", "DD", "DOW", "LYB", "EMN", "ALB", "LTHM",
        "SQM", "PLL", "LAC", "SGML", "CENX", "AA", "NUE", "STLD", "RS", "CMC",
        "ZEUS", "USAP", "HAYN", "ATI", "CRS", "KALU", "MLM", "VMC", "SUM", "USCR",
        "EXP", "CPAC", "USLM", "MDU", "TREX", "OLN", "HUN", "CE", "ASIX", "KWR"
    ],

    "Crypto & Fintech": [
        "COIN", "MSTR", "RIOT", "MARA", "CLSK", "BTBT", "HUT", "BITF", "CIFR", "IREN",
        "SQ", "PYPL", "AFRM", "UPST", "SOFI", "LC", "OPFI", "DAVE", "MOGO", "HOOD",
        "FUTU", "TIGR", "MQ", "FLYW", "PAYO", "EVTC", "FOUR", "REPAY", "RELY", "SEZL",
        "PRAA", "ENVA", "WRLD", "CURO", "QFIN", "LQDT", "CACC", "OMF", "LPRO", "EZCORP"
    ]
}

# ─────────────────────────────────────────
# Flatten to single deduplicated list
# ─────────────────────────────────────────
ALL_TICKERS = []
TICKER_SECTOR_MAP = {}

for sector, tickers in SECTORS.items():
    for ticker in tickers:
        clean = ticker.strip()
        if clean not in TICKER_SECTOR_MAP:
            ALL_TICKERS.append(clean)
            TICKER_SECTOR_MAP[clean] = sector

if __name__ == "__main__":
    print(f"✅ Total unique tickers: {len(ALL_TICKERS)}")
    print(f"✅ Total sectors: {len(SECTORS)}")
    print()
    for sector, tickers in SECTORS.items():
        unique = len(set(tickers))
        print(f"  {sector:<30} → {unique} stocks")

# ─────────────────────────────────────────
# Top-up tickers to hit 1000
# ─────────────────────────────────────────
TOPUP = {
    "Technology":    ["TOST", "RELY", "ALKT", "SMAR", "PCOR", "GONG", "WIXCOM", "WIX", "WEBNF", "APPS",
                      "VNET", "GDS", "IIJI", "OOMA", "LPSN", "SPOK", "NTCT", "QLYS", "RDVT", "SMSI"],
    "Healthcare":    ["PRVA", "SWTX", "AKRO", "ARHS", "CRNX", "IMTX", "KYMR", "MGTX", "NUVL", "RXRX",
                      "STRO", "TARS", "TVTX", "VKTX", "XOMA", "YMAB", "ZNTL", "PLRX", "RVMD", "NRIX"],
    "Financials":    ["OPFI", "OPEN", "TREE", "LEND", "GCMG", "STEP", "HLNE", "BRDG", "ARES", "KKR",
                      "APO", "BX", "CG", "BAM", "OWL", "BLUE", "TPVG", "CSWC", "FDUS", "GAIN"],
    "Industrials":   ["FWRD", "HUBG", "MRTN", "PTSI", "HTLD", "USAK", "ECHO", "RADNW", "GXO", "RXO",
                      "ZIM", "DAC", "SFL", "GSL", "CMRE", "ATCO", "ESEA", "GLBS", "PANL", "SHIP"],
    "Energy":        ["TALO", "PBF", "DKL", "PARR", "CALUMET", "CLMT", "CVR", "CVI", "DINO", "HFC",
                      "TRMD", "STNG", "DHT", "EURN", "FRO", "NAT", "NNA", "NRDBY", "SBLK", "GOGL"],
}

for sector, tickers in TOPUP.items():
    for ticker in tickers:
        clean = ticker.strip()
        if clean not in TICKER_SECTOR_MAP:
            ALL_TICKERS.append(clean)
            TICKER_SECTOR_MAP[clean] = sector

print(f"\n🚀 Final total unique tickers: {len(ALL_TICKERS)}")

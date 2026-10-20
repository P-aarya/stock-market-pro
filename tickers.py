# ============================================================
# tickers.py - Stock Market Pro universe
# 23 sectors incl. Physical AI, Robotics and Commodities.
# Every symbol was checked against the official NASDAQ / NYSE / NYSE American
# listings (17 Oct 2026): real, currently traded, one share class per company,
# each company in exactly one sector.
#
# sync_stocks.py verifies these on Yahoo Finance, swaps in RESERVES for any that
# fail, adds them to the database, and then rewrites this file with the final
# verified list.
# ============================================================

# Main list - 2095 tickers
SECTORS = {
    "Technology": [
        "AAPL", "GOOGL", "META", "AMZN", "IBM", "CSCO", "KLAC", "LRCX", "CTSH", "EPAM", "WIT", "INFY", "GLOB",
        "ZS", "PANW", "FTNT", "CIBR", "AKAM", "EGHT", "RNG", "ZM", "PAYX", "ADP", "G", "IT", "VRSK", "MSCI",
        "WDAY", "DELL", "HPE", "HPQ", "NTAP", "STX", "WDC", "P", "QCOM", "GRAB", "GNSS", "CODA", "SPOK",
        "IRDM", "CXT", "AIOT", "KEYS", "ATEN", "TTEC", "FFIV", "EXTR", "CALX", "TTGT", "CXDO", "VYX", "VNET",
        "SNDK", "VZ", "TMUS", "T", "SONY", "UBER", "GLW", "APP", "TEL", "NOK", "BSP", "UI", "CHT", "VOD",
        "ERIC", "RDDT", "VRSN", "ECHO", "SNX", "VIV", "BCE", "LOGI", "FN", "TIGO", "GDDY", "TU", "DOCN",
        "PINS", "TLK", "SKM", "ARW", "SNAP", "FDS", "NVMI", "MTCH", "LFUS", "QNT", "KT", "TIMB", "HNGE", "BZ",
        "AVT", "DOX", "OTEX", "INGM", "TTD", "VEON", "DOCS", "NIQ", "WAY", "TEO", "GDS", "LUMN", "PSN", "MDA",
        "TKC", "PHI", "JOYY", "BDC", "TDS", "SRAD", "MGNI", "FA", "LIF", "AGYS", "RUM", "PPLI", "KN", "AD",
        "KD", "LFTO", "KYIV", "CSQR", "IHS", "NTCT", "DGII", "CARG", "GRND", "FIVN", "PLUS", "ATHM", "RAMP",
        "DXC", "INFQ", "DV", "DJT", "IDT", "CDW", "NSIT", "CNXN", "ACN", "DASH", "GRMN", "FLEX", "JBL", "GIB",
    ],
    "Semiconductors": [
        "ACMR", "AMAT", "AVGO", "TXN", "ADI", "MCHP", "SWKS", "WOLF", "ON", "LSCC", "NVDA", "AMD", "INTC",
        "MPWR", "ENTG", "ONTO", "UCTT", "ICHR", "FORM", "SLAB", "SIMO", "DIOD", "SMTC", "AEHR", "COHU",
        "ACLS", "AXTI", "AMKR", "ASX", "ASML", "UMC", "SOXX", "MU", "RMBS", "SYNA", "MRVL", "MTSI", "LITE",
        "COHR", "CIEN", "VIAV", "POWI", "SITM", "ALGM", "GFS", "IDCC", "AOSL", "PI", "CAMT", "KLIC", "CRUS",
        "NVEC", "SKHY", "NXPI", "STM", "CBRS", "Q", "TSEM", "AAOI", "MXL", "PENG", "IMOS", "LASR", "PLAB",
        "AMBQ", "AIP", "LPTH", "LAES", "MRAM",
    ],
    "Cloud & SaaS": [
        "MSFT", "ADBE", "CRM", "NOW", "INTU", "ORCL", "SNPS", "CDNS", "DDOG", "MDB", "SNOW", "PLTR", "HUBS",
        "TWLO", "OKTA", "CRWD", "S", "NET", "FSLY", "BAND", "DOCU", "BOX", "DBX", "TEAM", "PCTY", "PAYC",
        "TYL", "VEEV", "NTNX", "APPF", "BRZE", "GTLB", "ESTC", "ALIT", "TASK", "PEGA", "SHOP", "WIX", "FVRR",
        "UPWK", "APPN", "LSPD", "AEYE", "ADSK", "VRNS", "ZETA", "FRSH", "ASAN", "MNDY", "AMPL", "DCBO",
        "FROG", "CLBT", "SAP", "NTES", "RBRK", "SSNC", "DT", "GEN", "GWRE", "CHKP", "MANH", "FIG", "SAIL",
        "BSY", "YMM", "PCOR", "NTSK", "DSGX", "TTAN", "NICE", "GLBE", "QLYS", "CVLT", "NAVN", "YOU", "OCTV",
        "KVYO", "BB", "CCC", "TENB", "WK", "QTWO", "INTA", "AVPT", "SPSC", "TDC", "ALRM", "VIA", "KC", "BLKB",
        "PRCH", "NCNO", "VERX", "KARO", "IQMX", "BL", "LIME", "PDFS", "CNXC",
    ],
    "Healthcare": [
        "DXCM", "LIVN", "OMCL", "ELAN", "PODD", "JNJ", "UNH", "PFE", "MRK", "ABT", "TMO", "DHR", "MDT", "BMY",
        "AMGN", "GILD", "CVS", "CI", "HUM", "ELV", "CNC", "MOH", "HCA", "THC", "UHS", "CYH", "ACHC", "ENSG",
        "OPCH", "TDOC", "AMWL", "HIMS", "HSTM", "EVH", "INVA", "PAHC", "PRGO", "JAZZ", "PRAX", "NVCR", "AXNX",
        "GKOS", "IRTC", "ITGR", "CERS", "SENS", "TNDM", "INSP", "LMAT", "XTLB", "MDXG", "KIDS", "NVST", "COR",
        "UTMD", "XTNT", "NPCE", "SYK", "ZBH", "BSX", "EW", "DGX", "LH", "NTRA", "QDEL", "MMSI", "OFIX",
        "ATRC", "BDX", "A", "MDLN", "IQV", "WAT", "IDXX", "RMD", "ALC", "MTD", "GEHC", "WST", "PHG", "GH",
        "STE", "MEDP", "RVTY", "ULS", "SOLV", "CRL", "ICLR", "BAX", "PEN", "EHC", "FMS", "BTSG", "SNN", "DVA",
        "COO", "AVTR", "ALGN", "OSCR", "BIO", "HSIC", "TXG", "CAI", "BRKR", "PACS", "MSA", "CHE", "GRAL",
        "BLCO", "RDNT", "HAE", "TFX", "STVN", "SHC", "MMED", "CON", "ORKA", "BLLN", "LFST", "HTFL", "MCK",
        "CAH",
    ],
    "Biotech": [
        "ARWR", "BIIB", "REGN", "VRTX", "ILMN", "MRNA", "BNTX", "NVAX", "ALNY", "BMRN", "EXEL", "FATE",
        "GOSS", "HALO", "IMVT", "INCY", "IONS", "LGND", "MGNX", "NKTR", "NTLA", "PCVX", "PGEN", "PTGX",
        "RARE", "RCUS", "RGNX", "RNAZ", "RPRX", "SRRK", "STOK", "TGTX", "TWST", "TXMD", "VCYT", "VKTX",
        "XNCR", "ZYME", "ARCT", "BEAM", "CRSP", "EDIT", "PRME", "ACRS", "AKBA", "ARQT", "CHRS", "DBVT",
        "DNLI", "EOLS", "EVAX", "GMAB", "JANX", "KPTI", "KYMR", "LNTH", "LYEL", "MCRB", "MDGL", "MGTX",
        "MIST", "MNKD", "RGEN", "DSGN", "ARVN", "AUTL", "AVXL", "BBIO", "BCTX", "BDTX", "BHVN", "CABA",
        "CCCC", "CELU", "CING", "CLRB", "CMPS", "CNTB", "COGT", "CPHI", "CRIS", "CRVS", "CSBR", "CTMX", "LLY",
        "ABBV", "NVS", "AZN", "NVO", "GSK", "SNY", "TAK", "ARGX", "TEVA", "ONC", "RVMD", "ZTS", "ROIV",
        "UTHR", "INSM", "VTRS", "ASND", "NBIX", "SMMT", "CORT", "TECH", "RDY", "IBRX", "KRYS", "QGEN", "CYTK",
        "AXSM", "SYRE", "ABVX", "ALKS", "AMRX", "IOVA", "KNSA", "BLTE", "RYTM", "KOD", "CGON", "MIRM", "ERAS",
        "GRFS", "PTCT", "TVTX", "MANE", "RGC", "DFTX", "DNTH", "EWTX", "ABCL", "ADPT", "INDV", "LEGN", "TNGX",
        "RLAY", "OGN", "TLX",
    ],
    "Financials": [
        "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "COF", "SYF", "ALLY", "OMF", "ENVA",
        "WRLD", "EZPW", "CACC", "PRAA", "TREE", "LDI", "UWMC", "RKT", "PFSI", "AGNC", "NLY", "EARN", "CHMI",
        "MFA", "RC", "BXMT", "KREF", "GPMT", "TPVG", "TCPC", "ARCC", "GBDC", "TRIN", "SLRC", "CGBD", "OCSL",
        "PSEC", "GAIN", "GLAD", "HTGC", "PNNT", "PFLT", "MCO", "SPGI", "ICE", "CME", "MKTX", "VIRT", "PIPR",
        "LAZ", "EVR", "HLI", "MC", "PJT", "FHN", "CFG", "HBAN", "KEY", "RF", "STT", "STEP", "WT", "V", "MA",
        "CASS", "HFWA", "FBIZ", "CFBK", "BX", "KKR", "APO", "NDAQ", "AMP", "IX", "IBKR", "ARES", "RJF", "NMR",
        "TW", "LPLA", "TROW", "EFX", "PS", "TPG", "BEN", "XP", "TRU", "OWL", "CG", "IVZ", "SEIC", "FRHC",
        "CHYM", "SF", "JEF", "AMG", "KLAR", "MORN", "SNEX", "VCTR", "MAAS", "BGC", "MRX", "IEP", "HLNE",
        "NNI", "FCFS", "BN", "BAM", "BR", "FICO",
    ],
    "Banking": [
        "USB", "PNC", "TFC", "FITB", "MTB", "ZION", "CFR", "BOH", "BOKF", "CBSH", "FFIN", "FULT", "IBOC",
        "INDB", "NBTB", "NWBI", "OFG", "RNST", "SFBS", "SFNC", "SMBC", "TCBK", "TRMK", "UBSI", "VLY", "WAFD",
        "WABC", "BANC", "HWC", "PNFP", "BUSE", "GABC", "HAFC", "ISBA", "MBWM", "OPHC", "PBFS", "PFIS", "PLBC",
        "QCRH", "SBCF", "SMBK", "STBA", "SYBT", "TBBK", "TFSL", "TOWN", "TRST", "UMBF", "UNTY", "UVSP", "ING",
        "ONB", "OSBC", "NKSH", "HSBC", "RY", "MUFG", "TD", "SAN", "SMFG", "UBS", "BBVA", "MFG", "HDB", "BMO",
        "BNS", "IBN", "CM", "BNY", "LYG", "BCS", "NWG", "DB", "BBD", "KB", "SHG", "BAP", "NTRS", "FCNCA",
        "BCH", "EWBC", "WF", "BSAC", "BPOP", "SSB", "WTFC", "WAL", "PB", "COLB", "CBC", "FNB", "IFS", "GGAL",
        "HOMB", "GBCI", "AUB", "ASB", "ABCB", "OZK",
    ],
    "Insurance": [
        "UFCS", "BRK-B", "MET", "PRU", "AFL", "AIG", "ALL", "CB", "TRV", "HIG", "CNA", "MKL", "RLI", "ERIE",
        "SIGI", "DGICA", "PLMR", "UVE", "HGTY", "GSHD", "KINS", "HCI", "HRTG", "BHF", "CNO", "EQH", "GL",
        "LNC", "ORI", "PFG", "RGA", "VOYA", "WRB", "AIZ", "FG", "GNW", "PGR", "MRSH", "MFC", "AJG", "AON",
        "SLF", "ACGL", "PUK", "WTW", "CINF", "BRO", "L", "CRBG", "AEG", "UNM", "EG", "RNR", "BNT", "AFG",
        "FNF", "RYAN", "JXN", "PRI", "KNSL", "THG", "AXS", "FAF", "ACT", "ESNT", "MTG", "MCY", "WTM", "BWIN",
        "RDN", "ARX", "CRVL", "NP", "LMND", "SLDE",
    ],
    "Consumer Discretionary": [
        "HD", "MCD", "NKE", "SBUX", "TGT", "LOW", "TJX", "ROST", "BURL", "FIVE", "OLLI", "GOOS", "LULU",
        "PVH", "RL", "UAA", "GIL", "COLM", "VFC", "CROX", "DECK", "SHOO", "BOOT", "BNED", "PRTS", "IPAR",
        "ELF", "ULTA", "COTY", "EPC", "CENT", "YUM", "QSR", "DPZ", "CMG", "JACK", "TXRH", "CAKE", "BLMN",
        "EAT", "CBRL", "BJRI", "KRUS", "RRGB", "FWRG", "BROS", "DRI", "ARCO", "WEN", "RRR", "CZR", "MGM",
        "WYNN", "LVS", "PENN", "DKNG", "BALY", "GENI", "WH", "WING", "GOLF", "PLBY", "PRPL", "TPR", "PTLO",
        "RGS", "HYFM", "CRCT", "VIRC", "BKNG", "ABNB", "MAR", "HLT", "SE", "DHI", "SN", "IHG", "ONON", "PHM",
        "LEN", "AS", "H", "NVR", "YUMC", "SGI", "ARMK", "HTHT", "TOL", "SCI", "RTO", "TTC", "EDU", "LTH",
        "MHK", "AOS", "CNM", "LEVI", "TAL", "CAVA", "BIRK", "RCL", "VIK", "CCL", "EXPE", "TCOM",
    ],
    "Consumer Staples": [
        "PG", "KO", "PEP", "PM", "MO", "MDLZ", "GIS", "CPB", "HRL", "SJM", "CAG", "MKC", "JJSF", "SENEA",
        "CALM", "NATH", "KHC", "CL", "CHD", "ENR", "SPB", "INGR", "SFM", "GO", "CHEF", "USFD", "SYY", "UNFI",
        "CASY", "MUSA", "ARKO", "TSN", "WMK", "VLGEA", "IMKTA", "NATR", "BUD", "UL", "MNST", "BTI", "DEO",
        "ABEV", "CCEP", "VYLR", "KDP", "HLN", "FMX", "EL", "KVUE", "HSY", "STZ", "PFGC", "JBS", "COKE",
        "MICC", "DAR", "CTVA", "LW", "TAP", "SFD", "CELH", "PPC", "PRMB", "SEB", "POST", "COCO", "FRPT",
        "FIZZ", "TR", "MZTI", "ANDE", "UTZ", "CCU", "SAM", "NOMD", "KMB", "CLX",
    ],
    "Energy": [
        "TUSK", "XOM", "CVX", "COP", "SLB", "EOG", "DVN", "MPC", "VLO", "PSX", "PBF", "DK", "PARR", "CLMT",
        "CAPL", "WMB", "KMI", "MPLX", "TRGP", "OKE", "AM", "HESM", "MMLP", "APA", "OXY", "AR", "EQT", "CNX",
        "RRC", "GPOR", "SM", "MTDR", "CRC", "REI", "NOG", "OVV", "BTU", "AMR", "HAL", "BKR", "FTI", "RES",
        "LBRT", "ACDC", "WTTR", "OIS", "PTEN", "GLNG", "PLUG", "BLDP", "FCEL", "SPWR", "ENPH", "SEDG", "FSLR",
        "ARRY", "SHLS", "CSIQ", "JKS", "DQ", "FANG", "PR", "MGY", "BATL", "ALTO", "REX", "AMPY", "TALO",
        "NRGV", "NXT", "TE", "PBR", "EQNR", "E", "LNG", "WDS", "EC", "VG", "CQP", "PBA", "TPL", "EXE", "YPF",
        "VNOM", "SSL", "UGP", "CHRD", "VIST", "NE", "NOV", "LB", "CVI", "RIG", "VAL", "MUR", "WFRD", "SEI",
        "WHD", "HCC", "CNR", "OII", "CRGY", "CRK", "HP", "EE", "EPD", "DINO", "WES", "PAA", "DTM", "KNTK",
        "SOBO",
    ],
    "Automotive": [
        "TSLA", "F", "GM", "RIVN", "LCID", "WKHS", "XPEV", "LI", "CRMT", "NIO", "KNDI", "LEA", "ALV", "BWA",
        "GT", "THRM", "DAN", "APTV", "CVNA", "KMX", "AN", "PAG", "SAH", "ABG", "GPI", "DRVN", "MNRO", "ORLY",
        "AZO", "GPC", "LKQ", "CPRT", "STRT", "MPAA", "BLNK", "TM", "RACE", "PCAR", "HMC", "MGA", "STLA",
        "ALSN", "MOD", "OSK", "VFS", "FSS", "LAD", "RUSHA", "GTX", "OPLN", "GNTX", "DORM", "ATMU", "VGNT",
        "HOG",
    ],
    "Media & Entertainment": [
        "NFLX", "ANGI", "CARS", "DIS", "CMCSA", "SKYD", "FOXA", "NYT", "SSP", "NXST", "GTN", "SBGI", "NWSA",
        "AMCX", "IMAX", "CNK", "AMC", "TTWO", "RBLX", "U", "GDEV", "HUYA", "IQ", "DOYU", "BILI", "BZUN",
        "COUR", "DUOL", "CHGG", "LAUR", "STRA", "PRDO", "TME", "SPOT", "TRI", "LYV", "TKO", "ROKU", "FWONK",
        "OMC", "RCI", "WMG", "HAS", "FLUT", "CHTR", "PSO", "MSGS", "LLYVK", "SIRI", "WPP", "CHDN", "SGHC",
        "MTN", "MAT", "RSI", "VSNT", "LBTYB", "SPHR", "MSGE", "MANU", "BATRA", "LION", "YETI", "MH", "WLY",
        "CALY", "ADEA", "PTON", "STGW", "STUB", "CMPR", "BRSL", "LILA", "TV", "PRSU", "RELX",
    ],
    "Retail": [
        "MELI", "ETSY", "PLCE", "WMT", "COST", "DG", "DLTR", "CHWY", "WOOF", "W", "EBAY", "ARHS", "WSM", "RH",
        "PLAY", "CAL", "M", "KSS", "ANF", "AEO", "URBN", "GAP", "CATO", "TSCO", "LQDT", "BABA", "JD", "PDD",
        "KR", "CPNG", "BBY", "BJ", "DKS", "QXO", "VSXY", "TBBB", "VIPS", "ACI", "BLDR", "PSMT", "FND", "BBWI",
        "ASO", "MNSO", "PTRN", "BCC", "GRDN", "BKE", "GCT", "BOBS", "RVLV", "SBH", "SVV", "YSWY", "LUXE",
        "REAL", "HEPS", "WINA", "RERE", "JMIA", "NGVC", "BWMX", "HNST", "DNUT", "DDL", "QVCG", "HVT", "SGU",
        "CTRN", "GCO", "SHOE", "SFIX", "DBI", "TDUP", "LE",
    ],
    "Real Estate": [
        "OPEN", "VICI", "AMT", "PLD", "CCI", "EQIX", "PSA", "SPG", "O", "WELL", "MAA", "UDR", "ESS", "CPT",
        "NNN", "ADC", "EPRT", "NTST", "FCPT", "PECO", "BRX", "KIM", "REG", "SITC", "CBL", "MAC", "SKT",
        "NXRT", "IRT", "BRT", "NREF", "CLPR", "SQFT", "GOOD", "PINE", "GLPI", "CHCT", "LTC", "SBRA", "CTRE",
        "OHI", "NHI", "DOC", "HR", "GEO", "DLR", "LAND", "DRH", "COMP", "STRW", "VMRK", "VTR", "IRM", "EXR",
        "SBAC", "HST", "LAMR", "WPC", "SUI", "WY", "AHR", "ELS", "AMH", "EGP", "BXP", "FRT", "JAN", "CUBE",
        "RHP", "REXR", "FR", "LINE", "ARE", "STAG", "VNO", "RYN", "OUT", "KRG", "RITM", "STWD", "CUZ", "EPR",
        "HHH", "KRC", "APLE", "COLD", "CDP", "LXP", "BNL", "SLG", "HIW", "CXW", "DX", "AKR", "IVT", "ARR",
        "PEB", "FRMI", "SMA", "MPT",
    ],
    "Utilities": [
        "NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "PEG", "ED", "XEL", "WEC", "ES", "FE", "ETR", "PPL",
        "EIX", "NI", "CMS", "AEE", "LNT", "EVRG", "AVA", "NWE", "PNW", "IDA", "OTTR", "MSEX", "AWK", "ARTNA",
        "GWRS", "YORW", "CWCO", "PCYO", "ATO", "SWX", "NJR", "SR", "UGI", "RGCO", "NFG", "CWT", "CLNE",
        "MGEE", "BE", "RUN", "SPH", "GEV", "CEG", "NGG", "VST", "PCG", "FTS", "DTE", "CNP", "SBS", "NRG",
        "TLN", "EMA", "KEP", "WTRG", "AES", "ELPC", "BEPC", "ENLT", "OGE", "CIG", "TXNM", "ENIC", "BKH",
        "OKLO",
    ],
    "Industrials": [
        "NPKI", "LIQT", "KFRC", "IEX", "WERN", "TBI", "KELYA", "CSL", "GE", "MMM", "CAT", "ETN", "PH", "ITW",
        "GNRC", "AAON", "WTS", "FELE", "GWW", "MSM", "FAST", "WSO", "AIT", "DNOW", "WCC", "UPS", "FDX", "XPO",
        "SAIA", "ODFL", "JBHT", "KNX", "LSTR", "CHRW", "EXPD", "ROAD", "URI", "WSC", "TREX", "PATK", "JELD",
        "MAS", "AWI", "TILE", "UFPI", "WMS", "BCPC", "CECO", "SPXC", "PLOW", "ASTE", "POWL", "HY", "ESAB",
        "ENOV", "RRX", "ACM", "DY", "PRIM", "PWR", "ADT", "ITRI", "VTSI", "MIND", "GXO", "CTAS", "TITN",
        "APH", "UNP", "TT", "CSX", "WM", "JCI", "CP", "CMI", "NSC", "CNI", "RSG", "FIX", "DAL", "WAB", "CARR",
        "WCN", "ROP", "UAL", "EME", "SUNB", "IR", "RYAAY", "OTIS", "DOV", "VLTO", "HUBB", "XYL", "NVT", "AER",
        "LUV", "GFL", "SNA", "ITT", "FTV", "APG", "FTAI", "CLH", "FDXF", "ROL", "RBA", "RBC", "ZTO", "MTZ",
        "CNH", "SWK", "LTM", "MLI", "GGG", "ALLE", "LII", "INIO", "CART", "CR", "MAIR", "FRO", "UHAL", "IESC",
        "TTMI", "FER", "J", "STRL", "PAC", "TTEK", "STN", "ASR", "FLR", "MWH", "GVA", "KBR", "OMAB", "CAAP",
        "WLDN", "MG",
    ],
    "Aerospace & Defense": [
        "SAIC", "LDOS", "BAH", "CACI", "LMT", "RTX", "NOC", "BA", "GD", "HII", "LHX", "TDG", "HEI", "DRS",
        "CW", "VSAT", "GSAT", "RKLB", "PL", "BKSY", "LUNR", "MNTS", "SPCE", "VSEC", "AIR", "HXL", "MRLN",
        "OSS", "DFEN", "ITA", "XAR", "IHAK", "BWXT", "ASTS", "SPCX", "HWM", "HONA", "ESLT", "ARXS", "WWD",
        "EMBJ", "TXT", "SARO", "LOAR", "KRMN", "BETA", "FLY", "DCO", "RDW", "AADX", "AVEX", "VOYG", "NPK",
        "YSS", "CDNL", "RGR", "SWBI", "EVEX", "PKE", "TATT",
    ],
    "Materials": [
        "SLGN", "HWKN", "LIN", "APD", "SHW", "ECL", "DD", "DOW", "LYB", "EMN", "CENX", "AA", "NUE", "STLD",
        "RS", "CMC", "ATI", "CRS", "KALU", "MLM", "VMC", "EXP", "CPAC", "USLM", "MDU", "OLN", "HUN", "CE",
        "ASIX", "KWR", "RPM", "FUL", "IOSP", "KOP", "NEU", "PPG", "ASPN", "AMCR", "CRH", "MT", "TS", "IFF",
        "SW", "PKG", "AMRZ", "IP", "PKX", "BALL", "CX", "JHX", "AVY", "CCK", "TX", "SUZ", "GGB", "SOLS",
        "ESI", "WLK", "ATR", "AXTA", "ICL", "SXT", "WFG", "MEOH", "SON", "LPX", "REYN", "FBIN", "SIM", "PRM",
        "VVV", "CBT", "AVNT", "ASH", "SMG", "WOR", "KNF", "AMBP", "WDFC", "GPK",
    ],
    "Crypto & Fintech": [
        "COIN", "MSTR", "GLXY", "CRCL", "BLSH", "GEMI", "FIGR", "CORZ", "IREN", "CIFR", "WULF", "HUT", "BTDR",
        "CLSK", "RIOT", "MARA", "HIVE", "XYZ", "PYPL", "AFRM", "UPST", "SOFI", "HOOD", "FUTU", "TIGR", "DAVE",
        "TOST", "FOUR", "FISV", "FIS", "GPN", "JKHY", "WEX", "FLYW", "PAYO", "RELY", "SEZL", "MQ", "BILL",
        "NU", "STNE", "PAGS", "DLO", "OPFI", "QFIN", "LX", "BTCS", "BITO", "GBTC", "IBIT", "CPAY", "ACIW",
        "EEFT", "RPAY", "PSFE", "BTBT", "EVTC", "NCTY", "CAN", "SBET", "WSE",
    ],
    "Physical AI": [
        "MBLY", "AUR", "PONY", "WRD", "HSAI", "KDK", "AEVA", "OUST", "INVZ", "ARBE", "MVIS", "IOT", "PATH",
        "SOUN", "AI", "BBAI", "TRMB", "PTC", "DDD", "SSYS", "NNDM", "XMTR", "PRLB", "POET", "LWLG", "CRNC",
        "INDI", "VUZI", "KOPN", "HIMX", "AMBA", "CEVA", "RGTI", "IONQ", "QBTS", "QUBT", "ARQQ", "NBIS",
        "CRWV", "APLD", "ALAB", "CRDO", "ARM", "BIDU", "VRT", "SMCI", "CLS", "ANET", "TSM", "TEM", "RXRX",
        "SDGR", "SKYX", "LIDR", "NVTS",
    ],
    "Robotics": [
        "ISRG", "SYM", "SERV", "RR", "PRCT", "CGNX", "ROK", "ZBRA", "NDSN", "KITT", "TER", "GMED", "AVAV",
        "KTOS", "JOBY", "ACHR", "PDYN", "GFAI", "ATS", "ALNT", "AIRO", "ZENA", "LECO", "TDY", "ATRO", "ESE",
        "AME", "HURC", "OSIS", "NOVT", "MKSI", "IPGP", "GHM", "KMT", "TKR", "AMSC", "ROBO", "BOTZ", "ARKQ",
        "ROBT", "DE", "EMR", "HON", "MSI", "AXON", "UMAC", "RCAT", "ONDS", "DPRO", "EH",
    ],
    "Commodities": [
        "GC=F", "SI=F", "PL=F", "PA=F", "CL=F", "NG=F", "HO=F", "RB=F", "BZ=F", "ZC=F", "ZS=F", "ZW=F",
        "ZO=F", "ZR=F", "CC=F", "KC=F", "CT=F", "SB=F", "OJ=F", "HG=F", "ALI=F", "LE=F", "GF=F", "HE=F",
        "GLD", "SLV", "IAU", "PPLT", "PALL", "USO", "UNG", "DBA", "PDBC", "COMT", "DBC", "CPER", "URA", "LIT",
        "REMX", "NEM", "GOLD", "AEM", "KGC", "AU", "HL", "PAAS", "EXK", "FSM", "AG", "SSRM", "CDE", "RGLD",
        "WPM", "OR", "GFI", "HMY", "BTG", "EGO", "IAG", "SVM", "AUGO", "FCX", "TECK", "HBM", "SCCO", "BHP",
        "RIO", "VALE", "MP", "TMC", "CCJ", "NXE", "UEC", "DNN", "UUUU", "LEU", "ALB", "SQM", "LAC", "SGML",
        "MOS", "NTR", "CF", "ADM", "BG", "TROX", "CMP", "IPI", "LXU", "ASM", "HYMC", "EU", "URG", "LTBR", "B",
        "SA", "BP", "SHEL", "TTE", "ENB", "TRP", "SU", "CNQ", "CVE", "IMO", "BTE", "CLF", "FMC", "ARIS",
    ],
}

# Same-sector replacements, used only if a main ticker has no data on Yahoo
RESERVES = {
    "Technology": [
        "ZD", "INOD", "FORTY", "TGLS", "ATEX", "WB", "CRSR", "MGRT", "VISN", "NSSC", "HAWK", "PSQL", "SCSC",
        "GDRX", "HLIT", "CLVT",
    ],
    "Semiconductors": [
    ],
    "Cloud & SaaS": [
        "EVCM", "NYAX", "PRGS", "OPRA", "ALKT", "XZO", "CERT", "CXM", "PD", "RDVT", "GTM", "TUYA",
    ],
    "Healthcare": [
        "ICUI", "NHC", "CDNA", "PLSE", "WRBY", "CELC", "PRVA", "AVAH", "NEOG", "TMDX", "CLOV", "UFPT", "CLDX",
        "MD",
    ],
    "Biotech": [
        "TARS", "AMLX", "ACAD", "XENE", "IDYA", "LQDA", "PBLS", "ELVN", "DYN", "NRIX", "ZLAB", "VCRE", "NAMS",
        "MBX", "HCM", "ADMA",
    ],
    "Financials": [
        "FHI", "SLM", "GPGI", "CNS", "BULL", "AAMI", "MIAX", "FSK", "IOND", "SII", "PURR", "APAM", "GCMG",
    ],
    "Banking": [
        "AX", "EBC", "FLG", "BMA", "FBP", "UCB", "CATY", "WSFS", "CVBF", "BANF", "WSBC", "NIC",
    ],
    "Insurance": [
        "AGO", "NMIH", "HG", "SPNT", "SKWD", "LIFE", "PLGO", "HMN", "KMPR",
    ],
    "Consumer Discretionary": [
        "HRB", "JMKE", "ECG", "BYD", "IBP", "CHH", "UNF", "CSW", "ATAT", "SIG", "SKY", "CVSA",
    ],
    "Consumer Staples": [
        "AGRO", "DMC", "SCL", "ODD", "FLO", "DOLE", "TPB", "AIIR", "AVO",
    ],
    "Energy": [
        "WBI", "ARLP", "CPK", "BSM", "SDRL", "BKV", "EROK", "NWN", "CTRI", "VET", "NESR", "INVX",
    ],
    "Automotive": [
        "AAP", "VC", "PHIN", "LCII", "ADNT", "DCH",
    ],
    "Media & Entertainment": [
        "PRKS", "WBTN", "NMAX", "IBTA", "TDAY", "DLX", "FBYD", "FUBO", "ACEL",
    ],
    "Retail": [
        "ZKH", "CURV", "NEGG", "HITI", "ZUMZ", "LOVE",
    ],
    "Real Estate": [
        "DHC", "RLJ", "GNL", "DEI", "UNIT", "AAT", "UMH", "ALX", "PDM", "LADR", "NHP", "DEA",
    ],
    "Utilities": [
        "ORA", "PAGP", "POR", "AROC", "CWEN", "KGS", "OGS", "PAM",
    ],
    "Industrials": [
        "DCI", "VICR", "SANM", "AEIS", "PAYP", "FPS", "TFII", "R", "AYI", "FLS", "OC", "VMI", "PNR", "AAL",
        "ZWS",
    ],
    "Aerospace & Defense": [
        "ORN", "LEGO", "POWW",
    ],
    "Materials": [
        "TTAM", "ROG", "NWL", "NGVT", "WS", "MTX", "CC", "STDN", "AIN",
    ],
    "Crypto & Fintech": [
        "BMNR", "XXI", "ASST", "BVC", "BTGO", "ABTC",
    ],
    "Physical AI": [
    ],
    "Robotics": [
        "XTND",
    ],
    "Commodities": [
        "FNV", "AGI", "EQX", "BVN", "SBSW", "TFPM", "CGAU", "USAR", "SKE", "AYA", "ERO", "TGB",
    ],
}

# Symbols from the previous list that are not in the listings file - kept only if live
CHECK = {
    "Technology": [
        "ANSS", "TCS", "ZOOM", "WORK", "SMAR", "COUP", "VMW", "PVTL", "JAMF", "QUAL", "QRVO", "MXIM", "JNPR",
        "FNSR", "NDAY", "TIFY", "AGIW", "CELO", "EVTV", "INST",
    ],
    "Semiconductors": [
        "QRVO", "CCMP", "XLNX", "ALTR", "SMIC", "IFNNY", "INPHI", "OCLR", "FNSR", "INFN", "PSEM", "VLT",
        "RADI", "IPHI", "BESI", "AIXA",
    ],
    "Cloud & SaaS": [
        "ZOOM", "SMAR", "COUP", "JAMF", "CFLT", "NEWR", "SUMO", "SPLK", "ALTR", "OZON", "SEA", "RIDE", "BIGC",
        "TRUE", "CDK", "BOOZ", "MANT", "ICF", "GFED", "NUAN", "LPSN", "HCP", "PHYH", "ACCD",
    ],
    "Healthcare": [
        "WCG", "LPNT", "SEM", "LHCG", "AMED", "EHAB", "BKD", "VCRA", "ONEM", "TLND", "OTRK", "CANO", "MDRX",
        "NXGN", "ENDP", "HZNP", "PRXS", "AVNS", "SWAV", "NVRO", "SPNV", "ICAD", "MZOR", "HOLX", "NARI",
        "RVNC", "SILK", "MELA", "GHDX", "OPGN", "ABMD",
    ],
    "Biotech": [
        "SGEN", "EXAS", "FOLD", "ITOS", "KRTX", "LOXO", "SAGE", "TBPH", "VIRS", "NTGN", "VERV", "GRPH",
        "BGNE", "BLUE", "CLVS", "EPZM", "ERYP", "ETNB", "FIXX", "FUSN", "HGEN", "HOOK", "IMAB", "ITCI",
        "KDNY", "LBPH", "LUMO", "ATTU", "AZYO", "BCAB", "CALT", "CARZ", "CASI", "CPRX", "CRTX", "CUTR",
        "CVAC", "CYCN",
    ],
    "Financials": [
        "DFS", "LPRO", "GHLD", "COOP", "NRZ", "TWO", "NYMT", "FSCO", "KCAP", "MRCC", "TICC", "TCAP", "NEFF",
        "VCNX", "CBOE", "EVRI",
    ],
    "Banking": [
        "NBT", "SASR", "SNV", "UMPQ", "PACW", "FBMS", "HTBI", "LBAI", "MOFG", "NWIN", "PBIP", "PPBI", "SMST",
        "SVB", "UCBI",
    ],
    "Insurance": [
        "NAVG", "KW", "MHLD", "FNHC", "UPCIC", "SWFT", "ARGO", "AWH", "GBL", "HIQ", "IHC", "NWLI", "PLICY",
        "SFG", "SLNV", "TBIZ", "TMK", "TPRE", "XL", "ANAT", "HMST", "MGLN", "PFBX", "PTVCB", "STFC",
    ],
    "Consumer Discretionary": [
        "BIG", "HBI", "SKX", "SCVL", "REV", "DINE", "SONC", "FSR", "GOEV", "NKLA",
    ],
    "Consumer Staples": [
        "K", "LANC", "SNAR", "SAFM", "PILG", "ELY", "LESL", "WBA", "RAD", "HIBB", "BGFV", "SPTN", "PF",
        "CORE", "NAFC", "GPMP", "ATD", "HEIF", "WEIS",
    ],
    "Energy": [
        "PXD", "HES", "ALJ", "HFC", "CVRR", "ET", "DCP", "CEQP", "ENLC", "SMLP", "NBLX", "HMLP", "CMLP",
        "PBFX", "BPMP", "MRO", "ESTE", "CPE", "CDEV", "PDCE", "WPX", "LPI", "BCEI", "SWN", "VTLE", "BTEGF",
        "ARCH", "CEIX", "NACCO", "NR", "CTRA", "SNDE", "TELL", "GATO", "GTLS", "HPNW", "PHX", "RINC", "ROCC",
    ],
    "Automotive": [
        "FSR", "GOEV", "NKLA", "RIDE", "BYDDY", "SOLO", "AYRO", "HYRE", "ZEV", "DLPH", "APTIV", "GENCO",
        "CHRA", "LAZR", "VLDR", "IRBT", "LOTZ", "RUSG", "IAA", "KAR", "GUSA", "WLKR", "OPNT",
    ],
    "Media & Entertainment": [
        "WBD", "GCI", "GRAY", "TGNA", "SJR", "CORUS", "LGF-A", "LGF-B", "CINE", "MDC", "EA", "ATVI", "DENA",
        "SKLZ", "COGEN", "UDMY", "LERN", "ARPU", "IACE", "GMBL", "IGMS", "AGS", "SGMS", "BALLY", "ISLE",
        "FULL", "TREH",
    ],
    "Retail": [
        "BIG", "PRTY", "PETQ", "OSTK", "OVST", "STLY", "PIR", "BED", "BBBY", "DSW", "FL", "BONT", "JWN",
        "JCP", "GES", "EXPR", "CHICO", "CHS", "MNCS", "KCLY",
    ],
    "Real Estate": [
        "AVB", "EQR", "STOR", "ROIC", "RPAI", "WRI", "AIRC", "GMRE", "NLCP", "MGP", "NXHS", "QTS", "CONE",
        "AFIN", "AHH", "CALI",
    ],
    "Utilities": [
        "SJW", "WTR", "CTWS", "PNMR", "SWSJ", "STRN", "NW", "GAS", "GBNK", "NTG", "SWJE", "STRP",
    ],
    "Industrials": [
        "FLOW", "DXP", "AZEK", "PGTI", "DOOR", "FBHS", "RXN", "ZURN",
    ],
    "Aerospace & Defense": [
        "MANT", "SPR", "CPI", "KAMN", "MAXR", "ASTR", "RCO", "TPHS", "NGAS", "FUVI",
    ],
    "Materials": [
        "LTHM", "PLL", "ZEUS", "USAP", "HAYN", "SUM", "USCR", "HBFR", "IPHS", "VNTR", "ZINC",
    ],
    "Crypto & Fintech": [
        "BITF", "LC", "MOGO", "REPAY", "CURO", "LPRO", "SFT", "ZASH", "BTCM", "WBTC", "IBTC", "PRPB", "BTAI",
        "MNMD", "TBIG",
    ],
    "Physical AI": [
        "INTUV", "IROQ", "SMTX", "ALIF", "ALGO", "MENT", "SYSC", "VOXX", "NOVA", "MAXN", "SUNW", "PECK",
        "SOL", "TCEHY",
    ],
    "Robotics": [
        "IRBT", "ABB", "FANUC", "YASKY", "DENSO", "KUKA", "BRKS", "KION", "MOBA", "PRFT", "PROC", "RBOT",
        "ROBX", "THNK", "VRBT", "TECB", "MTTR", "UNITY",
    ],
    "Commodities": [
        "MAG", "GPL", "SILV", "SAND", "HSE", "ERF", "CPG", "SGY", "WCP", "TOU",
    ],
}

# ------------------------------------------------------------
# Flat list + sector lookup (used by fetch_stocks.py, fetch_earnings.py, ...)
# ------------------------------------------------------------
ALL_TICKERS = []
TICKER_SECTOR_MAP = {}
for _sector, _tickers in SECTORS.items():
    for _t in _tickers:
        _t = _t.strip()
        if _t and _t not in TICKER_SECTOR_MAP:
            ALL_TICKERS.append(_t)
            TICKER_SECTOR_MAP[_t] = _sector

if __name__ == "__main__":
    print("Total unique tickers: {}".format(len(ALL_TICKERS)))
    print("Total sectors: {}".format(len(SECTORS)))
    for _sector, _tickers in SECTORS.items():
        print("  {:28s} -> {}".format(_sector, len(_tickers)))

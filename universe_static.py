"""Built-in large-cap universe: the scan's backbone when Yahoo's screener
API is unavailable (it hard-blocks datacenter IPs long before the price API).

Roughly cap-ordered, US then Europe, financial-sector names deliberately
omitted (the screener excludes Financial Services as a hard rule). A stale
or renamed ticker is harmless — it comes back with no price data and is
rejected. Refresh occasionally; exactness is not required because market
cap, sector, and liquidity are re-checked from live data during the scan.
"""

US_CORE = [
    # mega/large tech & communications
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "ORCL",
    "NFLX", "AMD", "CRM", "ADBE", "QCOM", "TXN", "INTU", "IBM", "NOW", "CSCO",
    "UBER", "PLTR", "INTC", "MU", "AMAT", "LRCX", "KLAC", "ANET", "PANW",
    "SNPS", "CDNS", "CRWD", "MRVL", "ADI", "NXPI", "APP", "WDAY", "FTNT",
    "DDOG", "ZS", "SNOW", "MDB", "TEAM", "DELL", "HPQ", "HPE", "ON", "MCHP",
    "SWKS", "TER", "QRVO", "GLW", "APH", "TEL", "KEYS", "CTSH", "EPAM",
    "GDDY", "ABNB", "DASH", "SPOT", "SHOP", "NET", "OKTA", "TWLO", "ZM",
    "DOCU", "ROP", "TDY", "TRMB", "ZBRA", "AKAM", "FFIV", "GEN", "VRSN",
    "EA", "TTWO", "RBLX", "PINS", "SNAP", "TMUS", "T", "VZ", "CMCSA", "CHTR",
    "DIS", "WBD", "FOXA", "OMC", "IPG", "TTD", "LYV", "NWSA", "MTCH", "GRMN",
    "IT", "ACN", "ADSK", "TSM", "MSI", "FSLR", "NTAP", "WDC", "STX", "PSTG",
    "COHR", "JBL", "FLEX", "GFS", "SMCI", "VRT", "CDW", "ANSS", "PTC",
    "MANH", "TYL", "FICO", "BR", "PAYX", "ADP", "PAYC", "PCTY", "DAY",
    "HUBS", "VEEV", "TOST", "BILL", "ESTC", "IOT", "GTLB", "PATH", "ROKU",
    # healthcare
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "PFE", "AMGN",
    "ISRG", "SYK", "BSX", "VRTX", "GILD", "BMY", "MDT", "ELV", "CI", "CVS",
    "ZTS", "BDX", "REGN", "MCK", "HCA", "COR", "CNC", "HUM", "EW", "A",
    "IDXX", "IQV", "RMD", "GEHC", "MTD", "WST", "DXCM", "BIIB", "MRNA",
    "ILMN", "BAX", "HOLX", "ALGN", "LH", "DGX", "ZBH", "STE", "PODD", "COO",
    "INCY", "VTRS", "TECH", "CRL", "RVTY", "UHS", "MOH", "DVA", "EXAS",
    "NBIX", "JAZZ", "BMRN", "UTHR", "RGEN", "THC",
    # consumer
    "WMT", "COST", "PG", "KO", "PEP", "HD", "MCD", "NKE", "SBUX", "TGT",
    "LOW", "TJX", "BKNG", "MAR", "HLT", "CMG", "YUM", "DRI", "ROST", "BURL",
    "DG", "DLTR", "KR", "GIS", "KHC", "HSY", "MDLZ", "CL", "KMB", "CHD",
    "CLX", "STZ", "TAP", "BF-B", "MO", "PM", "EL", "ULTA", "LULU", "DECK",
    "RL", "PVH", "TPR", "HAS", "POOL", "WSM", "RH", "BBY", "ORLY", "AZO",
    "GPC", "KMX", "CVNA", "F", "GM", "RIVN", "EXPE", "RCL", "CCL", "NCLH",
    "LVS", "WYNN", "MGM", "CZR", "DKNG", "DPZ", "WING", "TXRH", "CAVA",
    "KDP", "MNST", "CELH", "TSN", "HRL", "CAG", "SJM", "CPB", "MKC", "LW",
    "BG", "ADM", "COTY", "VFC", "MAT",
    # industrials
    "GE", "CAT", "RTX", "HON", "MMM", "UNP", "BA", "DE", "LMT", "GD", "NOC",
    "ETN", "EMR", "ITW", "PH", "CMI", "PCAR", "CSX", "NSC", "FDX", "UPS",
    "WM", "RSG", "LHX", "TDG", "HWM", "AXON", "URI", "PWR", "AME", "ROK",
    "DOV", "FTV", "XYL", "IR", "CARR", "OTIS", "JCI", "TT", "GWW", "FAST",
    "SWK", "MAS", "PNR", "ALLE", "BLDR", "VMC", "MLM", "NUE", "STLD", "AA",
    "HUBB", "EME", "FIX", "J", "ACM", "LII", "WAB", "CHRW", "EXPD", "JBHT",
    "ODFL", "SAIA", "XPO", "KNX", "UAL", "DAL", "LUV", "ALK", "GEV", "LDOS",
    "BAH", "CACI", "SAIC", "TXT", "HII", "CW", "HEI",
    # energy, materials, utilities
    "XOM", "CVX", "COP", "EOG", "SLB", "OXY", "PSX", "VLO", "MPC", "WMB",
    "KMI", "OKE", "LNG", "HAL", "BKR", "DVN", "FANG", "HES", "CTRA", "APA",
    "EQT", "AR", "TRGP", "FCX", "NEM", "SCCO", "ALB", "DOW", "DD", "LYB",
    "PPG", "SHW", "ECL", "APD", "LIN", "IFF", "CE", "EMN", "CF", "MOS",
    "CTVA", "FMC", "NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL",
    "ED", "WEC", "ES", "PEG", "EIX", "PCG", "AEE", "CMS", "DTE", "FE",
    "PPL", "CNP", "NI", "ATO", "NRG", "CEG", "VST", "AES", "LNT", "EVRG",
    # real estate (allowed sector)
    "PLD", "AMT", "EQIX", "CCI", "PSA", "SPG", "O", "WELL", "DLR", "AVB",
    "EQR", "VTR", "ESS", "MAA", "INVH", "SUI", "UDR", "ARE", "BXP", "HST",
    "KIM", "REG", "FRT", "CPT", "EXR", "IRM", "WY", "CBRE",
]

EU_CORE = [
    # Germany
    "SAP.DE", "SIE.DE", "DTE.DE", "RHM.DE", "MBG.DE", "BMW.DE", "VOW3.DE",
    "P911.DE", "BAS.DE", "BAYN.DE", "MRK.DE", "HEI.DE", "IFX.DE", "ADS.DE",
    "PUM.DE", "DHL.DE", "EOAN.DE", "RWE.DE", "ENR.DE", "SHL.DE", "ZAL.DE",
    "HEN3.DE", "BEI.DE", "SRT3.DE", "QIA.DE", "FRE.DE", "FME.DE", "SY1.DE",
    "DTG.DE", "MTX.DE", "KGX.DE", "HOT.DE", "VNA.DE",
    # France
    "MC.PA", "OR.PA", "TTE.PA", "SAN.PA", "AI.PA", "SU.PA", "AIR.PA",
    "EL.PA", "KER.PA", "RMS.PA", "CAP.PA", "DG.PA", "VIE.PA", "SGO.PA",
    "ML.PA", "RI.PA", "CA.PA", "DSY.PA", "PUB.PA", "ENGI.PA", "ORA.PA",
    "LR.PA", "ALO.PA", "EN.PA", "SAF.PA", "TEP.PA", "STM",
    # Netherlands
    "ASML.AS", "AD.AS", "PHIA.AS", "HEIA.AS", "KPN.AS", "WKL.AS", "RAND.AS",
    "AKZA.AS", "BESI.AS", "ASM.AS", "IMCD.AS",
    # Switzerland
    "NESN.SW", "ROG.SW", "NOVN.SW", "ABBN.SW", "SIKA.SW", "LONN.SW",
    "GIVN.SW", "CFR.SW", "UHR.SW", "ALC.SW", "GEBN.SW", "SGSN.SW",
    "STMN.SW", "LOGN.SW",
    # United Kingdom
    "AZN.L", "SHEL.L", "ULVR.L", "GSK.L", "DGE.L", "RIO.L", "BP.L",
    "GLEN.L", "REL.L", "NG.L", "BATS.L", "IMB.L", "CPG.L", "RKT.L",
    "EXPN.L", "SSE.L", "BA.L", "VOD.L", "TSCO.L", "HLN.L", "SGE.L",
    "INF.L", "AHT.L", "CRH", "SN.L", "SPX.L", "HLMA.L", "WEIR.L", "IMI.L",
    "PSON.L", "WPP.L", "BT-A.L", "SVT.L", "UU.L", "BNZL.L", "DPLM.L",
    "RR.L",
    # Italy & Spain
    "RACE.MI", "ENI.MI", "ENEL.MI", "PRY.MI", "MONC.MI", "TRN.MI",
    "SRG.MI", "REC.MI", "CPR.MI", "ITX.MC", "IBE.MC", "REP.MC", "TEF.MC",
    "AMS.MC", "ANA.MC", "ACS.MC", "FER.MC", "AENA.MC", "ELE.MC",
    # Nordics
    "NOVO-B.CO", "DSV.CO", "MAERSK-B.CO", "CARL-B.CO", "COLO-B.CO", "GN.CO",
    "PNDORA.CO", "VWS.CO", "ORSTED.CO", "GMAB.CO", "ATCO-A.ST", "VOLV-B.ST",
    "ERIC-B.ST", "SAND.ST", "ASSA-B.ST", "ALFA.ST", "HEXA-B.ST", "SKF-B.ST",
    "ESSITY-B.ST", "TELIA.ST", "HM-B.ST", "EQNR.OL", "TEL.OL", "MOWI.OL",
    "NHY.OL", "AKRBP.OL", "ORK.OL", "YAR.OL",
]

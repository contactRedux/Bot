"""
data/feeds/newsapi_feed.py — News headlines and ticker search via NewsAPI.org.

NewsAPI.org provides an HTTP API for fetching breaking news from thousands of
sources.  In this system it is the primary news ingestion source for the
Sentiment strategy (Sub-Task 5).

Free tier limits
----------------
* 100 requests/day
* Headlines only (no full article body) on the free plan
* Up to 100 articles per request
* Historical search limited to the past 30 days (Developer plan extends to 3 months)

Usage patterns
--------------
Two NewsAPI endpoints are used:

1. **Top Headlines** (``/v2/top-headlines``) — real-time breaking news,
   optionally filtered by category, country, or source.  Used for general
   market news polling.

2. **Everything** (``/v2/everything``) — full-text search by keyword.
   All tickers are batched into a single OR query to stay within the
   100 requests/day free-tier limit.  Per-article ticker attribution is
   done via title/description substring matching after the fetch.

Both return the same article structure:
    ``{title, description, content, url, author, publishedAt, source: {name}}``

We map ``publishedAt`` to ``event_timestamp`` (UTC) — this is the authoritative
timestamp used by the backtesting engine.

Article de-duplication
----------------------
The ``article_id`` field in ``NewsArticle`` is a SHA-256 hash of
``(source + title + event_timestamp)``.  This allows the DataStore to use
``INSERT OR IGNORE`` semantics to avoid duplicating the same article when
polling repeatedly.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import structlog

from data.feeds.base import DataFeed
from data.schemas import NewsArticle

logger = structlog.get_logger(__name__)

# NewsAPI ``/v2/everything`` OR-query is capped at 500 chars; we stay safe at 490.
_MAX_QUERY_CHARS = 490


def _make_article_id(source: str, title: str, published_at: str) -> str:
    """Create a stable article identifier from its key fields."""
    raw = f"{source}|{title}|{published_at}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# Map ticker symbol → common name variants for better attribution.
# Articles often say "Apple" or "Microsoft" rather than "AAPL" or "MSFT".
# Covers the VOO (S&P 500), QQQ (Nasdaq-100), and DJIA universes.
_TICKER_ALIASES: dict[str, list[str]] = {
    # ── Mega-cap tech ──────────────────────────────────────────────────────────
    "AAPL":    ["apple", "aapl", "iphone", "ipad", "macbook", "apple inc", "apple watch", "airpods"],
    "MSFT":    ["microsoft", "msft", "azure", "windows", "copilot", "activision", "linkedin", "github"],
    "NVDA":    ["nvidia", "nvda", "cuda", "geforce", "jensen huang", "blackwell", "hopper"],
    "TSLA":    ["tesla", "tsla", "elon musk", "elon", "cybertruck", "gigafactory", "autopilot"],
    "AMZN":    ["amazon", "amzn", "aws", "prime", "bezos", "whole foods", "twitch"],
    "GOOGL":   ["google", "alphabet", "googl", "goog", "gemini", "waymo", "deepmind", "youtube", "pixel"],
    # GOOG is the non-voting share class; articles almost never say "GOOG" specifically.
    # Keeping an empty-ish entry prevents the fallback from matching the raw symbol.
    "META":    ["meta", "facebook", "instagram", "whatsapp", "zuckerberg", "threads", "oculus", "ray-ban"],
    "NFLX":    ["netflix", "nflx", "reed hastings"],
    "ORCL":    ["oracle", "orcl", "larry ellison"],
    "CRM":     ["salesforce", "crm", "marc benioff"],
    "ADBE":    ["adobe", "adbe", "photoshop", "illustrator", "acrobat"],
    "NOW":     ["servicenow", "service now"],
    "INTU":    ["intuit", "turbotax", "quickbooks", "mailchimp"],
    "SNPS":    ["synopsys", "snps"],
    "CDNS":    ["cadence", "cdns"],
    "WDAY":    ["workday", "wday"],
    "ADSK":    ["autodesk", "adsk", "autocad"],
    "PANW":    ["palo alto networks", "panw", "palo alto"],
    "FTNT":    ["fortinet", "ftnt"],
    "CRWD":    ["crowdstrike", "crwd"],
    "ZS":      ["zscaler", "zs"],
    "DDOG":    ["datadog", "ddog"],
    "UBER":    ["uber"],
    "LYFT":    ["lyft"],
    "SNAP":    ["snapchat", "snap inc"],
    "PINS":    ["pinterest", "pins"],
    "SPOT":    ["spotify", "spot"],
    "TWLO":    ["twilio", "twlo"],
    "ABNB":    ["airbnb", "abnb"],
    "RBLX":    ["roblox", "rblx"],
    "COIN":    ["coinbase", "coin"],
    "HOOD":    ["robinhood", "hood"],
    "MELI":    ["mercadolibre", "meli"],
    "SHOP":    ["shopify", "shop"],
    "BABA":    ["alibaba", "baba", "jack ma"],
    # ── Semiconductors ────────────────────────────────────────────────────────
    "AMD":     ["amd", "advanced micro devices", "radeon", "ryzen", "epyc", "lisa su"],
    "INTC":    ["intel", "intc", "gelsinger", "intel foundry"],
    "QCOM":    ["qualcomm", "qcom", "snapdragon"],
    "MU":      ["micron", " mu ", "dram", "nand"],
    "AVGO":    ["broadcom", "avgo"],
    "TSM":     ["tsmc", "taiwan semiconductor", "tsm"],
    "AMAT":    ["applied materials", "amat"],
    "SNDK":    ["sandisk", "sndk", "western digital", "wdc"],
    "LRCX":    ["lam research", "lrcx"],
    "KLAC":    ["kla", "klac"],
    "ASML":    ["asml"],
    "TXN":     ["texas instruments", "txn"],
    "MRVL":    ["marvell", "mrvl"],
    "MCHP":    ["microchip technology", "mchp"],
    "NXPI":    ["nxp semiconductors", "nxpi", "nxp"],
    "ON":      ["on semiconductor", "onsemi"],
    "ADI":     ["analog devices", "adi"],
    "MPWR":    ["monolithic power", "mpwr"],
    "WOLF":    ["wolfspeed", "wolf", "cree"],
    "SWKS":    ["skyworks", "swks"],
    "QRVO":    ["qorvo", "qrvo"],
    # ── ETFs / indices ────────────────────────────────────────────────────────
    "SPY":     ["spy", "s&p 500", "s&p500", "spdr", "s&p five hundred"],
    "QQQ":     ["qqq", "nasdaq 100", "nasdaq-100", "invesco qqq", "nasdaq composite"],
    "VOO":     ["voo", "vanguard s&p", "vanguard 500"],
    "IWM":     ["iwm", "russell 2000", "small cap"],
    "DIA":     ["dia", "dow jones", "dow industrial"],
    "GLD":     ["gold", "gld", "gold price"],
    "SLV":     ["silver", "slv"],
    "SMH":     ["smh", "semiconductor etf", "van eck semiconductor"],
    "XLF":     ["xlf", "financial sector", "financials etf"],
    "XLE":     ["xle", "energy sector", "energy etf"],
    "XLK":     ["xlk", "technology etf", "technology sector"],
    "XLV":     ["xlv", "healthcare etf", "health care sector"],
    "XLI":     ["xli", "industrials etf", "industrial sector"],
    "XLY":     ["xly", "consumer discretionary etf"],
    "XLP":     ["xlp", "consumer staples etf"],
    "XLU":     ["xlu", "utilities etf"],
    "XLRE":    ["xlre", "real estate etf"],
    "TLT":     ["tlt", "20 year treasury", "long bond etf"],
    "HYG":     ["hyg", "high yield bonds", "junk bonds"],
    "VXX":     ["vxx", "volatility etf", "vix etf"],
    "ARKK":    ["ark innovation", "arkk", "cathie wood"],
    # ── Crypto ────────────────────────────────────────────────────────────────
    "BTC-USD": ["bitcoin", "btc", "satoshi", "cryptocurrency", "crypto market"],
    "ETH-USD": ["ethereum", "eth", "ether", "vitalik", "defi", "smart contract"],
    "SOL-USD": ["solana", "sol"],
    "BNB-USD": ["binance", "bnb"],
    "XRP-USD": ["ripple", "xrp"],
    "ADA-USD": ["cardano", "ada"],
    "DOGE-USD":["dogecoin", "doge"],
    # ── Financials ────────────────────────────────────────────────────────────
    "JPM":     ["jpmorgan", "jp morgan", "jpm", "jamie dimon", "chase bank"],
    "GS":      ["goldman sachs", "goldman", "gs", "david solomon"],
    "BAC":     ["bank of america", "bac", "bofa"],
    "MS":      ["morgan stanley", "james gorman"],
    "WFC":     ["wells fargo", "wfc"],
    "C":       ["citigroup", "citibank", "citi", "jane fraser"],
    "V":       ["visa"],
    "MA":      ["mastercard"],
    "AXP":     ["american express", "amex", "axp"],
    "PYPL":    ["paypal", "pypl", "venmo"],
    "SQ":      ["block", "square", "cash app"],
    "BX":      ["blackstone", "bx"],
    "BLK":     ["blackrock", "blk", "larry fink"],
    "SCHW":    ["charles schwab", "schwab", "schw"],
    "ICE":     ["intercontinental exchange", "ice"],
    "CME":     ["cme group", "cme"],
    "SPGI":    ["s&p global", "spgi"],
    "MCO":     ["moody's", "mco"],
    "TFC":     ["truist", "tfc"],
    "USB":     ["us bancorp", "us bank", "usb"],
    "PNC":     ["pnc financial", "pnc"],
    "MTB":     ["m&t bank", "mtb"],
    "COF":     ["capital one", "cof"],
    "DFS":     ["discover", "dfs"],
    "SYF":     ["synchrony", "syf"],
    "NDAQ":    ["nasdaq inc", "ndaq"],
    "FI":      ["fiserv", "fi"],
    "FIS":     ["fidelity national", "fis"],
    "GPN":     ["global payments", "gpn"],
    "TROW":    ["t. rowe price", "trow"],
    "BEN":     ["franklin templeton", "ben"],
    "IVZ":     ["invesco", "ivz"],
    "STT":     ["state street", "stt"],
    "NTRS":    ["northern trust", "ntrs"],
    "AFL":     ["aflac", "afl"],
    "MET":     ["metlife", "met"],
    "PRU":     ["prudential", "pru"],
    "ALL":     ["allstate", "all"],
    "TRV":     ["travelers", "trv"],
    "HIG":     ["hartford financial", "hig"],
    "CB":      ["chubb", "cb"],
    "MMC":     ["marsh mclennan", "mmc"],
    "AON":     ["aon", "aon plc"],
    "WTW":     ["willis towers watson", "wtw"],
    "PGR":     ["progressive", "pgr"],
    "CINF":    ["cincinnati financial", "cinf"],
    # ── Consumer / retail ────────────────────────────────────────────────────
    "WMT":     ["walmart", "wmt"],
    "TGT":     ["target", "tgt"],
    "COST":    ["costco", "cost"],
    "HD":      ["home depot", " hd "],
    "NKE":     ["nike", "nke"],
    "SBUX":    ["starbucks", "sbux"],
    "MCD":     ["mcdonald", "mcd"],
    "KO":      ["coca-cola", "coca cola", "coke"],
    "PEP":     ["pepsi", "pepsico", "pep"],
    "PG":      ["procter & gamble", "p&g", "pg"],
    "CL":      ["colgate", "colgate-palmolive", "cl"],
    "KHC":     ["kraft heinz", "khc"],
    "MDLZ":    ["mondelez", "mdlz", "nabisco"],
    "GIS":     ["general mills", "gis", "cheerios"],
    "K":       ["kellogg", "kellanova", " k "],
    "HSY":     ["hershey", "hsy"],
    "MKC":     ["mccormick", "mkc"],
    "SJM":     ["j.m. smucker", "sjm", "smucker"],
    "CPB":     ["campbell soup", "cpb"],
    "HRL":     ["hormel", "hrl"],
    "LW":      ["lamb weston", "lw"],
    "STZ":     ["constellation brands", "stz", "corona beer"],
    "BF-B":    ["brown-forman", "bf-b", "jack daniel"],
    "TAP":     ["molson coors", "tap"],
    "PM":      ["philip morris", "pm"],
    "MO":      ["altria", "mo"],
    "BTI":     ["british american tobacco", "bti"],
    "LOW":     ["lowe's", "lowes", "low"],
    "TJX":     ["tjx", "tj maxx", "marshalls", "homegoods"],
    "ROST":    ["ross stores", "rost"],
    "BURL":    ["burlington", "burl"],
    "GPS":     ["gap", "gps"],
    "URBN":    ["urban outfitters", "urbn"],
    "LULU":    ["lululemon", "lulu"],
    "RL":      ["ralph lauren", "rl"],
    "PVH":     ["pvh", "tommy hilfiger", "calvin klein"],
    "HBI":     ["hanesbrands", "hbi"],
    "CMG":     ["chipotle", "cmg"],
    "YUM":     ["yum brands", "yum", "kfc", "pizza hut", "taco bell"],
    "DRI":     ["darden restaurants", "dri", "olive garden"],
    "TXRH":    ["texas roadhouse", "txrh"],
    "DPZ":     ["domino's", "dpz"],
    "QSR":     ["restaurant brands", "qsr", "burger king", "tim hortons"],
    "SHAK":    ["shake shack", "shak"],
    "CBRL":    ["cracker barrel", "cbrl"],
    "BJRI":    ["bj's restaurants", "bjri"],
    "PLAY":    ["dave & buster's", "play"],
    "DINE":    ["dine brands", "dine", "ihop", "applebee's"],
    "WEN":     ["wendy's", "wen"],
    "JACK":    ["jack in the box", "jack"],
    "SONC":    ["sonic drive-in", "sonc"],
    "CAKE":    ["cheesecake factory", "cake"],
    "EAT":     ["brinker", "eat", "chili's"],
    "WINGSTOP":["wingstop"],
    "WING":    ["wingstop", "wing"],
    "RRGB":    ["red robin", "rrgb"],
    "BLMN":    ["bloomin brands", "blmn", "outback steakhouse"],
    "NATH":    ["nathan's famous", "nath"],
    "FWRG":    ["five guys", "fwrg"],
    "CHUY":    ["chuy's", "chuy"],
    "NDLS":    ["noodles & company", "ndls"],
    "PZZA":    ["papa john's", "pzza"],
    "FRSH":    ["freshpet", "frsh"],
    "HIMS":    ["hims & hers", "hims"],
    "EBAY":    ["ebay"],
    "ETSY":    ["etsy"],
    "W":       ["wayfair", " w "],
    "CHWY":    ["chewy", "chwy"],
    "PRTS":    ["carparts.com", "prts"],
    "KRTX":    ["karuna therapeutics", "krtx"],
    # ── Healthcare / biotech ─────────────────────────────────────────────────
    "JNJ":     ["johnson & johnson", "j&j", "jnj"],
    "PFE":     ["pfizer", "pfe"],
    "MRNA":    ["moderna", "mrna"],
    "BNTX":    ["biontech", "bntx"],
    "LLY":     ["eli lilly", "lly"],
    "ABBV":    ["abbvie", "abbv"],
    "BMY":     ["bristol-myers squibb", "bmy"],
    "MRK":     ["merck", "mrk"],
    "GILD":    ["gilead", "gild"],
    "REGN":    ["regeneron", "regn"],
    "VRTX":    ["vertex pharmaceuticals", "vrtx"],
    "BIIB":    ["biogen", "biib"],
    "AMGN":    ["amgen", "amgn"],
    "TMO":     ["thermo fisher", "tmo"],
    "ABT":     ["abbott", "abt"],
    "DHR":     ["danaher", "dhr"],
    "MDT":     ["medtronic", "mdt"],
    "SYK":     ["stryker", "syk"],
    "BSX":     ["boston scientific", "bsx"],
    "EW":      ["edwards lifesciences", "ew"],
    "ISRG":    ["intuitive surgical", "isrg"],
    "BDX":     ["becton dickinson", "bdx"],
    "ZTS":     ["zoetis", "zts"],
    "IDXX":    ["idexx", "idxx"],
    "MTD":     ["mettler-toledo", "mtd"],
    "WAT":     ["waters corporation", "wat"],
    "IQV":     ["iqvia", "iqv"],
    "CI":      ["cigna", "ci"],
    "CVS":     ["cvs health", "cvs"],
    "UNH":     ["unitedhealth", "unh", "optum"],
    "HUM":     ["humana", "hum"],
    "MOH":     ["molina healthcare", "moh"],
    "CNC":     ["centene", "cnc"],
    "ELV":     ["elevance health", "elv", "anthem"],
    "HCA":     ["hca healthcare", "hca"],
    "THC":     ["tenet healthcare", "thc"],
    "UHS":     ["universal health services", "uhs"],
    "MCK":     ["mckesson", "mck"],
    "ABC":     ["amerisourcebergen", "cencora", "abc"],
    "CAH":     ["cardinal health", "cah"],
    # ── Energy ───────────────────────────────────────────────────────────────
    "XOM":     ["exxon", "exxonmobil", "xom", "esso"],
    "CVX":     ["chevron", "cvx"],
    "COP":     ["conocophillips", "cop"],
    "OXY":     ["occidental", "oxy"],
    "SLB":     ["schlumberger", "slb", "slb oilfield"],
    "HAL":     ["halliburton", "hal"],
    "BKR":     ["baker hughes", "bkr"],
    "EOG":     ["eog resources", "eog"],
    "PXD":     ["pioneer natural resources", "pxd"],
    "FANG":    ["diamondback energy", "fang"],
    "DVN":     ["devon energy", "dvn"],
    "MPC":     ["marathon petroleum", "mpc"],
    "VLO":     ["valero", "vlo"],
    "PSX":     ["phillips 66", "psx"],
    "HES":     ["hess", "hes"],
    "OKE":     ["oneok", "oke"],
    "KMI":     ["kinder morgan", "kmi"],
    "WMB":     ["williams companies", "wmb"],
    "ET":      ["energy transfer", "et"],
    "EPD":     ["enterprise products", "epd"],
    "MMP":     ["magellan midstream", "mmp"],
    "LNG":     ["cheniere energy", "lng"],
    "FCG":     ["first trust natural gas", "fcg"],
    # ── Industrials ──────────────────────────────────────────────────────────
    "CAT":     ["caterpillar", "cat"],
    "HON":     ["honeywell", "hon"],
    "GE":      ["general electric", "ge"],
    "MMM":     ["3m", "mmm"],
    "IBM":     ["ibm", "international business machines", "watson"],
    "BA":      ["boeing", "ba"],
    "RTX":     ["raytheon", "rtx", "pratt & whitney", "collins aerospace"],
    "LMT":     ["lockheed martin", "lmt", "f-35"],
    "NOC":     ["northrop grumman", "noc"],
    "GD":      ["general dynamics", "gd", "gulfstream"],
    "L3H":     ["l3harris", "l3h"],
    "HWM":     ["howmet aerospace", "hwm"],
    "TDG":     ["transdigm", "tdg"],
    "HII":     ["huntington ingalls", "hii"],
    "LDOS":    ["leidos", "ldos"],
    "SAIC":    ["saic", "science applications"],
    "CACI":    ["caci international", "caci"],
    "EMR":     ["emerson electric", "emr"],
    "ITW":     ["illinois tool works", "itw"],
    "PH":      ["parker hannifin", "ph"],
    "ETN":     ["eaton", "etn"],
    "ROK":     ["rockwell automation", "rok"],
    "IR":      ["ingersoll rand", "ir"],
    "CARR":    ["carrier global", "carr"],
    "JCI":     ["johnson controls", "jci"],
    "TT":      ["trane technologies", "trane", "tt"],
    "XYL":     ["xylem", "xyl"],
    "PCAR":    ["paccar", "pcar", "peterbilt", "kenworth"],
    "CMI":     ["cummins", "cmi"],
    "DE":      ["john deere", "deere", "de"],
    "AGCO":    ["agco", "fendt", "massey ferguson"],
    "CNH":     ["cnh industrial", "cnh"],
    "TEX":     ["terex", "tex"],
    "MTW":     ["manitowoc", "mtw"],
    "ACCO":    ["acco brands", "acco"],
    "AOS":     ["a.o. smith", "aos"],
    "GWW":     ["w.w. grainger", "gww"],
    "MSC":     ["msc industrial", "msc"],
    "FAST":    ["fastenal", "fast"],
    "URI":     ["united rentals", "uri"],
    "GATX":    ["gatx", "gatx rail"],
    "NSC":     ["norfolk southern", "nsc"],
    "CSX":     ["csx", "csx transportation"],
    "UNP":     ["union pacific", "unp"],
    "ODFL":    ["old dominion freight", "odfl"],
    "SAIA":    ["saia inc", "saia"],
    "XPO":     ["xpo logistics", "xpo"],
    "CHRW":    ["c.h. robinson", "chrw"],
    "EXPD":    ["expeditors", "expd"],
    "KNX":     ["knight-swift", "knx"],
    "JBHT":    ["j.b. hunt", "jbht"],
    "WERN":    ["werner enterprises", "wern"],
    "LSTR":    ["landstar", "lstr"],
    "RXO":     ["rxo"],
    "GXO":     ["gxo logistics", "gxo"],
    "NCLH":    ["norwegian cruise", "nclh"],
    "CCL":     ["carnival", "ccl"],
    "RCL":     ["royal caribbean", "rcl"],
    "DAL":     ["delta air lines", "dal"],
    "UAL":     ["united airlines", "ual"],
    "AAL":     ["american airlines", "aal"],
    "LUV":     ["southwest airlines", "luv"],
    "ALK":     ["alaska airlines", "alk"],
    "HA":      ["hawaiian airlines", "ha"],
    "JBLU":    ["jetblue", "jblu"],
    "SAVE":    ["spirit airlines", "save"],
    "UPS":     ["ups", "united parcel service"],
    "FDX":     ["fedex", "fdx"],
    # ── Technology (hardware / enterprise) ──────────────────────────────────
    "CSCO":    ["cisco", "csco"],
    "HPQ":     ["hp inc", "hewlett packard", "hpq"],
    "HPE":     ["hewlett packard enterprise", "hpe"],
    "DELL":    ["dell", "dell technologies"],
    "NTAP":    ["netapp", "ntap"],
    "WDC":     ["western digital", "wdc"],
    "STX":     ["seagate", "stx"],
    "ANET":    ["arista networks", "anet"],
    "JNPR":    ["juniper networks", "jnpr"],
    "AKAM":    ["akamai", "akam"],
    "CTSH":    ["cognizant", "ctsh"],
    "ACN":     ["accenture", "acn"],
    "WIT":     ["wipro", "wit"],
    "INFY":    ["infosys", "infy"],
    "TCS":     ["tata consultancy", "tcs"],
    "EPAM":    ["epam systems", "epam"],
    "GLOB":    ["globant", "glob"],
    "JD":      ["jd.com", "jd"],
    "PDD":     ["pinduoduo", "temu", "pdd"],
    "BIDU":    ["baidu", "bidu"],
    "MSI":     ["motorola solutions", "msi"],
    "KEYS":    ["keysight", "keys"],
    "TRMB":    ["trimble", "trmb"],
    "VRSK":    ["verisk", "vrsk"],
    "GARTNER": ["gartner"],
    "IT":      ["gartner", "it"],
    "MTSI":    ["macom technology", "mtsi"],
    "VRSN":    ["verisign", "vrsn"],
    # ── Healthcare devices / life sciences ──────────────────────────────────
    "DXCM":    ["dexcom", "dxcm"],
    "PODD":    ["insulet", "podd"],
    "TNDM":    ["tandem diabetes", "tndm"],
    "INVA":    ["innoviva", "inva"],
    "NVCR":    ["novocure", "nvcr"],
    "GMED":    ["globus medical", "gmed"],
    "NVT":     ["nvent electric", "nvt"],
    "RMD":     ["resmed", "rmd"],
    "HOLX":    ["hologic", "holx"],
    "ICU":     ["seachange", "icu"],
    "ALGN":    ["align technology", "algn", "invisalign"],
    "STE":     ["steris", "ste"],
    "LMAT":    ["lemaitre vascular", "lmat"],
    "IRTC":    ["irhythm technologies", "irtc"],
    "TMDX":    ["transmedics", "tmdx"],
    "GEHC":    ["ge healthcare", "gehc"],
    "HAE":     ["haemonetics", "hae"],
    "OSUR":    ["orasure technologies", "osur"],
    "NVST":    ["envista", "nvst"],
    # ── Real estate / REITs ──────────────────────────────────────────────────
    "AMT":     ["american tower", "amt"],
    "CCI":     ["crown castle", "cci"],
    "EQIX":    ["equinix", "eqix"],
    "DLR":     ["digital realty", "dlr"],
    "SBAC":    ["sba communications", "sbac"],
    "PLD":     ["prologis", "pld"],
    "EQR":     ["equity residential", "eqr"],
    "AVB":     ["avalonbay", "avb"],
    "SPG":     ["simon property group", "spg"],
    "O":       ["realty income", " o "],
    "WPC":     ["w. p. carey", "wpc"],
    "VICI":    ["vici properties", "vici"],
    "GLPI":    ["gaming and leisure properties", "glpi"],
    "IRM":     ["iron mountain", "irm"],
    "PSA":     ["public storage", "psa"],
    "EXR":     ["extra space storage", "exr"],
    "NSA":     ["national storage affiliates", "nsa"],
    "LSI":     ["life storage", "lsi"],
    "CUBE":    ["cubesmart", "cube"],
    "DOC":     ["physicians realty", "doc"],
    "PEAK":    ["healthpeak properties", "peak"],
    "VTR":     ["ventas", "vtr"],
    "WELL":    ["welltower", "well"],
    "NNN":     ["national retail properties", "nnn"],
    "ADC":     ["agree realty", "adc"],
    "NTST":    ["netstreit", "ntst"],
    # ── Utilities ────────────────────────────────────────────────────────────
    "NEE":     ["nextera energy", "nee", "florida power & light"],
    "SO":      ["southern company", "so"],
    "DUK":     ["duke energy", "duk"],
    "SRE":     ["sempra energy", "sre"],
    "AEP":     ["american electric power", "aep"],
    "EXC":     ["exelon", "exc"],
    "PCG":     ["pg&e", "pcg", "pacific gas and electric"],
    "XEL":     ["xcel energy", "xel"],
    "ED":      ["consolidated edison", "ed"],
    "ES":      ["eversource", "es"],
    "ETR":     ["entergy", "etr"],
    "FE":      ["firstenergy", "fe"],
    "PPL":     ["ppl corporation", "ppl"],
    "WEC":     ["wec energy", "wec"],
    "LNT":     ["alliant energy", "lnt"],
    "EVRG":    ["evergy", "evrg"],
    "OGE":     ["oge energy", "oge"],
    "PNW":     ["pinnacle west", "pnw"],
    "NI":      ["nisource", "ni"],
    "ATO":     ["atmos energy", "ato"],
    "CNP":     ["centerpoint energy", "cnp"],
    "CEG":     ["constellation energy", "ceg"],
    "VST":     ["vistra", "vst"],
    "NRG":     ["nrg energy", "nrg"],
    "AWK":     ["american water works", "awk"],
    "WTRG":    ["essential utilities", "wtrg"],
    # ── Materials ────────────────────────────────────────────────────────────
    "NEM":     ["newmont", "nem"],
    "FCX":     ["freeport-mcmoran", "fcx", "copper"],
    "ALB":     ["albemarle", "alb", "lithium"],
    "LIN":     ["linde", "lin"],
    "APD":     ["air products", "apd"],
    "SHW":     ["sherwin-williams", "shw"],
    "PPG":     ["ppg industries", "ppg"],
    "EMN":     ["eastman chemical", "emn"],
    "DOW":     ["dow inc", "dow chemical"],
    "DD":      ["dupont", "dd"],
    "LYB":     ["lyondellbasell", "lyb"],
    "CE":      ["celanese", "ce"],
    "PKG":     ["packaging corp", "pkg"],
    "IP":      ["international paper", "ip"],
    "WRK":     ["westrock", "wrk"],
    "SEE":     ["sealed air", "see"],
    "AMCR":    ["amcor", "amcr"],
    "BALL":    ["ball corporation", "ball"],
    "CCK":     ["crown holdings", "cck"],
    "SON":     ["sonoco", "son"],
    "GEF":     ["greif", "gef"],
    "OLN":     ["olin corporation", "oln"],
    "ASH":     ["ashland global", "ash"],
    "RPM":     ["rpm international", "rpm"],
    "FMC":     ["fmc corporation", "fmc"],
    "MOS":     ["the mosaic company", "mos", "potash"],
    "CF":      ["cf industries", "cf", "fertilizer"],
    "NUE":     ["nucor", "nue", "steel"],
    "STLD":    ["steel dynamics", "stld"],
    "X":       ["us steel", "x steel"],
    "CLF":     ["cleveland-cliffs", "clf"],
    "WLK":     ["westlake chemical", "wlk"],
    "VMC":     ["vulcan materials", "vmc"],
    "MLM":     ["martin marietta", "mlm"],
    "SUM":     ["summit materials", "sum"],
    "EXP":     ["eagle materials", "exp"],
    # ── Consumer durables / autos ────────────────────────────────────────────
    "RIVN":    ["rivian", "rivn"],
    "LCID":    ["lucid", "lcid"],
    "NIO":     ["nio"],
    "F":       ["ford"],
    "GM":      ["general motors", " gm "],
    "STLA":    ["stellantis", "stla", "jeep", "dodge", "chrysler"],
    "TM":      ["toyota", "tm"],
    "HMC":     ["honda", "hmc"],
    "NSANY":   ["nissan", "nsany"],
    "VLVLY":   ["volvo", "vlvly"],
    "HOG":     ["harley-davidson", "hog"],
    "DOOO":    ["brp", "dooo", "ski-doo"],
    "PII":     ["polaris", "pii"],
    "WHR":     ["whirlpool", "whr"],
    "LZB":     ["la-z-boy", "lzb"],
    "LESL":    ["leslie's", "lesl"],
    "SWK":     ["stanley black & decker", "swk"],
    "MHK":     ["mohawk industries", "mhk"],
    "WSM":     ["williams-sonoma", "wsm"],
    "RH":      ["restoration hardware", "rh"],
    "BBWI":    ["bath & body works", "bbwi"],
    "BBY":     ["best buy", "bby"],
    "PRGO":    ["perrigo", "prgo"],
    "ANF":     ["abercrombie & fitch", "anf"],
    "AEO":     ["american eagle", "aeo"],
    "GES":     ["guess", "ges"],
    "SHOO":    ["steven madden", "shoo"],
    "SKX":     ["skechers", "skx"],
    "ONON":    ["on holding", "onon"],
    # ── Miscellaneous / DJIA-specific ────────────────────────────────────────
    "DIS":     ["disney", "dis", "pixar", "marvel", "espn", "hulu"],
    "VZ":      ["verizon", "vz"],
    "T":       ["at&t", "t "],
    "TMUS":    ["t-mobile", "tmus"],
    "CMCSA":   ["comcast", "cmcsa", "xfinity", "nbcuniversal"],
    "CHTR":    ["charter communications", "chtr", "spectrum"],
    "FOXA":    ["fox corporation", "foxa"],
    "WBD":     ["warner bros discovery", "wbd", "hbo", "cnn"],
    "PARA":    ["paramount", "para", "cbs"],
    "LGF-A":   ["lionsgate", "lgf"],
    "EA":      ["electronic arts", "ea"],
    "TTWO":    ["take-two interactive", "ttwo", "rockstar games", "2k games"],
    "ZNGA":    ["zynga", "znga"],
    "SCI":     ["service corporation", "sci"],
    "CTAS":    ["cintas", "ctas"],
    "RSG":     ["republic services", "rsg"],
    "WM":      ["waste management", "wm"],
    "TRMK":    ["trustmark", "trmk"],
    "VLTO":    ["veralto", "vlto"],
    "ADP":     ["automatic data processing", "adp"],
    "PAYX":    ["paychex", "payx"],
    "BR":      ["broadridge", "br"],
    "MSCI":    ["msci inc", "msci"],
    "IFF":     ["international flavors", "iff"],
    "ECL":     ["ecolab", "ecl"],
    "ZBH":     ["zimmer biomet", "zbh"],
    "EFX":     ["equifax", "efx"],
    "TRU":     ["transunion", "tru"],
    "EXPN":    ["experian", "expn"],
    "BKNG":    ["booking holdings", "bkng", "booking.com", "priceline"],
    "EXPE":    ["expedia", "expe"],
    "MAR":     ["marriott", "mar"],
    "HLT":     ["hilton", "hlt"],
    "H":       ["hyatt", " h "],
    "IHG":     ["intercontinental hotels", "ihg"],
    "WYNDM":   ["wyndham", "wyndm"],
    "CHH":     ["choice hotels", "chh"],
    "VAC":     ["marriott vacations", "vac"],
    "TNL":     ["travel + leisure", "tnl"],
    "HGV":     ["hilton grand vacations", "hgv"],
    "BYD":     ["boyd gaming", "byd"],
    "LVS":     ["las vegas sands", "lvs"],
    "WYNN":    ["wynn resorts", "wynn"],
    "MGM":     ["mgm resorts", "mgm"],
    "CZR":     ["caesars entertainment", "czr"],
    "PENN":    ["penn entertainment", "penn"],
    "DKNG":    ["draftkings", "dkng"],
    # ── Consumer internet ─────────────────────────────────────────────────────
    "IAC":     ["iac", "iac inc"],
    "MTCH":    ["match group", "mtch", "tinder", "hinge"],
    "BMBL":    ["bumble", "bmbl"],
    "GRPN":    ["groupon", "grpn"],
    "YELP":    ["yelp"],
    "ANGI":    ["angi homeservices", "angi"],
    "CARG":    ["cargurus", "carg"],
    "CDK":     ["cdk global", "cdk"],
    "CARS":    ["cars.com", "cars"],
    "TRDN":    ["tradeweb", "trdn"],
    "GDOT":    ["green dot", "gdot"],
}


def _attribute_tickers(title: str, description: str | None, tickers: list[str]) -> list[str]:
    """
    Return the subset of *tickers* that appear in the article title or description.

    Matches both the raw ticker symbol and known company name aliases so that
    an article mentioning "Apple" correctly maps to AAPL.  If no tickers match,
    returns an empty list (the article is attributed to GENERAL by the caller)
    rather than incorrectly assigning every queried ticker.
    """
    haystack = (title + " " + (description or "")).lower()
    matched: list[str] = []
    for t in tickers:
        aliases = _TICKER_ALIASES.get(t.upper(), [t.lower()])
        if any(alias in haystack for alias in aliases):
            matched.append(t)
    return matched


class NewsApiFeed(DataFeed):
    """
    News feed backed by NewsAPI.org (``newsapi-python`` library).

    Parameters
    ----------
    config : dict, optional
        Required keys:
        - ``"api_key"`` : NewsAPI.org API key (read from settings by default)

    Example
    -------
    ::

        from data.feeds.newsapi_feed import NewsApiFeed
        from datetime import datetime, timezone

        feed = NewsApiFeed(config={"api_key": "your-key"})

        # Search for Apple news
        articles = feed.fetch_news(tickers=["AAPL"], max_results=20)
        for a in articles:
            print(a.title, a.sentiment_score)
    """

    SOURCE = "newsapi"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._api_key: str | None = self.config.get("api_key")

    def _get_client(self):
        """Lazily create the NewsAPI client."""
        if not self._api_key:
            raise ValueError(
                "NewsAPI key is required.  Set NEWSAPI_KEY in .env or "
                "pass config={'api_key': 'your-key'}."
            )
        try:
            from newsapi import NewsApiClient
        except ImportError:
            raise ImportError(
                "newsapi-python is not installed.  "
                "Run: pip install 'quant-engine[data]'"
            )
        return NewsApiClient(api_key=self._api_key)

    def fetch_news(
        self,
        tickers: list[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        max_results: int = 100,
    ) -> list[NewsArticle]:
        """
        Fetch news articles from NewsAPI using a **single batched request**.

        All tickers are combined into one ``OR`` query (e.g. ``AAPL OR MSFT OR
        NVDA``) so the entire poll cycle costs exactly **one API request**
        regardless of how many tickers are watched.  Per-article ticker
        attribution is resolved via title/description substring matching after
        the fetch.

        If ``tickers`` is not provided, fetches general business top headlines.

        Parameters
        ----------
        tickers :
            List of ticker symbols to search for.
        start, end :
            UTC datetimes for the search window.
        max_results :
            Maximum number of articles to return (capped at 100 by NewsAPI).

        Returns
        -------
        list[NewsArticle]
            Articles sorted descending by ``event_timestamp`` (newest first).
            ``sentiment_score`` is ``None`` — populated later by the sentiment module.
        """
        client = self._get_client()
        fetch_ts = datetime.now(tz=timezone.utc)

        # Format timestamps for NewsAPI (ISO 8601 without timezone suffix)
        from_ts = start.strftime("%Y-%m-%dT%H:%M:%S") if start else None
        to_ts = end.strftime("%Y-%m-%dT%H:%M:%S") if end else None

        seen_ids: set[str] = set()
        articles: list[NewsArticle] = []

        def _parse_raw(raw_articles: list[dict], query_tickers: list[str]) -> None:
            """Convert raw NewsAPI dicts → NewsArticle, attributing tickers by title match."""
            for raw in raw_articles:
                try:
                    published_at = raw.get("publishedAt", "")
                    source_name = raw.get("source", {}).get("name", "unknown")
                    title = raw.get("title", "") or ""
                    description = raw.get("description") or ""
                    article_id = _make_article_id(source_name, title, published_at)

                    if article_id in seen_ids:
                        continue
                    seen_ids.add(article_id)

                    # Parse ISO 8601 timestamp → UTC datetime
                    if published_at:
                        ts = datetime.fromisoformat(
                            published_at.replace("Z", "+00:00")
                        )
                    else:
                        ts = fetch_ts

                    # Attribute only the tickers that actually appear in the article
                    attributed = (
                        _attribute_tickers(title, description, query_tickers)
                        if query_tickers
                        else []
                    )

                    article = NewsArticle(
                        article_id=article_id,
                        title=title,
                        body=raw.get("content"),  # truncated on free tier
                        url=raw.get("url"),
                        source=self.SOURCE,
                        author=raw.get("author"),
                        tickers=attributed,
                        event_timestamp=ts,
                        fetch_timestamp=fetch_ts,
                        sentiment_score=None,
                    )
                    articles.append(article)
                except Exception as exc:
                    logger.warning("newsapi.article_parse_error", error=str(exc))

        if tickers:
            # Build a single OR query — one request for all tickers.
            # Truncate the query if it would exceed NewsAPI's 500-char limit.
            query_parts: list[str] = []
            for t in tickers:
                candidate = " OR ".join(query_parts + [t])
                if len(candidate) > _MAX_QUERY_CHARS:
                    break
                query_parts.append(t)

            query = " OR ".join(query_parts)
            logger.info("newsapi.fetch_news", query=query, max_results=max_results)
            try:
                response = client.get_everything(
                    q=query,
                    from_param=from_ts,
                    to=to_ts,
                    language="en",
                    sort_by="publishedAt",
                    page_size=min(max_results, 100),
                )
                _parse_raw(response.get("articles", []), tickers)
            except Exception as exc:
                logger.error("newsapi.fetch_news.error", query=query, error=str(exc))
        else:
            # Fetch general business top headlines
            logger.info("newsapi.fetch_top_headlines")
            try:
                response = client.get_top_headlines(
                    category="business",
                    language="en",
                    page_size=min(max_results, 100),
                )
                _parse_raw(response.get("articles", []), [])
            except Exception as exc:
                logger.error("newsapi.fetch_top_headlines.error", error=str(exc))

        # Sort newest first
        articles.sort(key=lambda a: a.event_timestamp, reverse=True)
        logger.info("newsapi.fetch_news.done", articles_returned=len(articles))
        return articles

    def fetch_top_headlines(self, max_results: int = 100) -> list[NewsArticle]:
        """
        Convenience method to fetch current top business headlines.

        Returns
        -------
        list[NewsArticle]
            Top headlines, sorted newest first.
        """
        return self.fetch_news(tickers=None, max_results=max_results)

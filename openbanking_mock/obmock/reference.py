"""Georgian reference data that makes simulated statements look like Georgian statements.

Everything here is cosmetic realism. Nothing is a real person or a real transaction. Items
marked ASSUMED are plausible guesses, not taken from an authoritative source.
"""
from __future__ import annotations

from dataclasses import dataclass

# (Georgian script, Latin transliteration as printed on cards/IBAN statements)
MALE_FIRST = [
    ("გიორგი", "GIORGI"), ("დავით", "DAVIT"), ("ლევან", "LEVAN"), ("ნიკა", "NIKA"),
    ("ლაშა", "LASHA"), ("ირაკლი", "IRAKLI"), ("ზურაბ", "ZURAB"), ("მიხეილ", "MIKHEIL"),
    ("თორნიკე", "TORNIKE"), ("სანდრო", "SANDRO"), ("გოგა", "GOGA"), ("ალექსანდრე", "ALEKSANDRE"),
    ("ბექა", "BEKA"), ("ვახტანგ", "VAKHTANG"), ("გურამ", "GURAM"), ("რევაზ", "REVAZ"),
    ("თამაზ", "TAMAZ"), ("შალვა", "SHALVA"), ("კახა", "KAKHA"), ("ბადრი", "BADRI"),
    ("გია", "GIA"), ("მერაბ", "MERAB"), ("ვაჟა", "VAZHA"), ("ნუგზარ", "NUGZAR"),
    ("გოჩა", "GOCHA"), ("ოთარ", "OTAR"), ("არჩილ", "ARCHIL"), ("ლუკა", "LUKA"),
    ("საბა", "SABA"), ("ემზარ", "EMZAR"), ("ზაზა", "ZAZA"), ("დათო", "DATO"),
]
FEMALE_FIRST = [
    ("ნინო", "NINO"), ("მარიამ", "MARIAM"), ("ანა", "ANA"), ("თამარ", "TAMAR"),
    ("ეკა", "EKA"), ("ნათია", "NATIA"), ("ხატია", "KHATIA"), ("ქეთევან", "KETEVAN"),
    ("სალომე", "SALOME"), ("ნანა", "NANA"), ("ლელა", "LELA"), ("მაია", "MAIA"),
    ("მანანა", "MANANA"), ("ნატო", "NATO"), ("თეა", "TEA"), ("ლიკა", "LIKA"),
    ("ელენე", "ELENE"), ("თინათინ", "TINATIN"), ("ირინა", "IRINA"), ("დალი", "DALI"),
    ("ნესტან", "NESTAN"), ("ლამარა", "LAMARA"), ("ციალა", "TSIALA"), ("მზია", "MZIA"),
    ("ნინელი", "NINELI"), ("მაკა", "MAKA"), ("სოფო", "SOPO"), ("ნუცა", "NUTSA"),
    ("თაკო", "TAKO"), ("ლიზი", "LIZI"), ("მარიკა", "MARIKA"), ("ქრისტინე", "KRISTINE"),
]
SURNAMES = [
    ("ბერიძე", "BERIDZE"), ("კაპანაძე", "KAPANADZE"), ("გელაშვილი", "GELASHVILI"),
    ("მაისურაძე", "MAISURADZE"), ("ლომიძე", "LOMIDZE"), ("გიორგაძე", "GIORGADZE"),
    ("წიკლაური", "TSIKLAURI"), ("ხუციშვილი", "KHUTSISHVILI"), ("მგელაძე", "MGELADZE"),
    ("ჭიკოვანი", "CHIKOVANI"), ("ბოლქვაძე", "BOLKVADZE"), ("ჯავახიშვილი", "JAVAKHISHVILI"),
    ("მამულაშვილი", "MAMULASHVILI"), ("ნოდია", "NODIA"), ("ხარაიშვილი", "KHARAISHVILI"),
    ("გოგიჩაიშვილი", "GOGICHAISHVILI"), ("სამხარაძე", "SAMKHARADZE"),
    ("კვარაცხელია", "KVARATSKHELIA"), ("მჭედლიშვილი", "MCHEDLISHVILI"), ("ჩხეიძე", "CHKHEIDZE"),
    ("ცინცაძე", "TSINTSADZE"), ("ლორთქიფანიძე", "LORTKIPANIDZE"), ("აბაშიძე", "ABASHIDZE"),
    ("ქავთარაძე", "KAVTARADZE"), ("თავაძე", "TAVADZE"), ("ხვედელიძე", "KHVEDELIDZE"),
    ("მეგრელიშვილი", "MEGRELISHVILI"), ("ჯაფარიძე", "JAPARIDZE"), ("გოგოლაძე", "GOGOLADZE"),
    ("სულაბერიძე", "SULABERIDZE"), ("მაჭავარიანი", "MACHAVARIANI"), ("ბაქრაძე", "BAKRADZE"),
    ("ასათიანი", "ASATIANI"), ("ბარათაშვილი", "BARATASHVILI"), ("ონიანი", "ONIANI"),
    ("გვენეტაძე", "GVENETADZE"), ("კუპრაძე", "KUPRADZE"), ("ფანჯიკიძე", "PANJIKIDZE"),
    ("ღლონტი", "GHLONTI"), ("ზარქუა", "ZARKUA"),
]
MONTHS_KA = ["იანვარი", "თებერვალი", "მარტი", "აპრილი", "მაისი", "ივნისი", "ივლისი",
             "აგვისტო", "სექტემბერი", "ოქტომბერი", "ნოემბერი", "დეკემბერი"]
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December"]

# ASSUMED: df CLIENT_GEOGRAPHICAL_REGION is an unlabelled 1-12 code. Code 3 holds ~49% of loans,
# which matches Tbilisi's share; the other labels are cosmetic guesses ordered by loan share.
REGION_CITIES: dict[int, tuple[str, list[str]]] = {
    1: ("Samegrelo", ["Zugdidi", "Senaki", "Poti"]),
    2: ("Racha", ["Ambrolauri", "Oni"]),
    3: ("Tbilisi", ["Tbilisi"]),
    4: ("Imereti", ["Kutaisi", "Zestaponi", "Samtredia"]),
    5: ("Shida Kartli", ["Gori", "Kaspi"]),
    6: ("Samtskhe-Javakheti", ["Akhaltsikhe", "Akhalkalaki"]),
    7: ("Mtskheta-Mtianeti", ["Mtskheta"]),
    8: ("Kvemo Kartli", ["Rustavi", "Marneuli", "Gardabani"]),
    9: ("Guria", ["Ozurgeti", "Lanchkhuti"]),
    10: ("Adjara", ["Batumi", "Kobuleti"]),
    11: ("Kakheti", ["Telavi", "Gurjaani", "Sagarejo"]),
    12: ("Kakheti/Other", ["Rustavi", "Telavi", "Kutaisi"]),
}

# df ECONOMIC_SECTORS id -> short employer noun (ids 0..26 from the supplied data dictionary)
SECTOR_NOUN = {
    0: "Service", 1: "State Agency", 2: "Finance", 3: "Pawn", 4: "Development", 5: "Property",
    6: "Construction", 7: "Building Materials", 8: "Trade", 9: "Foods", 10: "Home Goods",
    11: "Textile", 12: "Trade", 13: "Manufacturing", 14: "Hospitality", 15: "Catering",
    16: "Industry", 17: "Fuel", 18: "Energy", 19: "Auto", 20: "Medical", 21: "Pharma",
    22: "Telecom", 23: "Services", 24: "Agro", 25: "Group", 26: "Remittance",
}
EMPLOYER_STEMS = ["Kolkheti", "Iveria", "Caucasus", "Tbilisi", "Rustavi", "Argo", "Aragvi",
                  "Mtkvari", "Svaneti", "Kartli", "Imereti", "Lazika", "Meskheti", "Alazani",
                  "Rioni", "Algeti", "Phasis", "Orbeli", "Samgori", "Vake"]


@dataclass(frozen=True)
class Category:
    """Spend category: monthly event rate and lognormal amount at median income (GEL)."""

    key: str
    lam: float
    median: float
    sigma: float
    mcc: str
    channel: str  # POS | ECOM | ATM
    merchants: tuple[str, ...]
    income_elasticity: float = 0.35
    prob: float = 1.0  # share of customers that use the category at all
    stress_boost: float = 0.0


# Monthly frequencies (lam) are tuned so that the simulated spend mix matches Geostat 2025 household
# expenditure (food 38.7, transport 11.4, housing/utilities 11.3, health 11.0, clothing 5.8, durables
# 5.1 % of cash consumption), renormalised over the groups modelled here (education and "other" are
# not). Ticket sizes, merchants and MCCs are ASSUMED.
CATEGORIES: dict[str, Category] = {c.key: c for c in [
    Category("grocery", 10.4, 28.0, 0.70, "5411", "POS",
             ("CARREFOUR", "AGROHUB", "SPAR", "NIKORA", "GOODWILL", "LIBRE", "FRESCO",
              "EUROPRODUCT", "MAGNITI"), 0.30),
    Category("kiosk", 5.5, 7.5, 0.60, "5499", "POS", ("DAILY MARKET", "MINI MARKET", "KIOSK"), 0.1),
    Category("restaurant", 4.9, 32.0, 0.75, "5812", "POS",
             ("KHINKALI HOUSE", "MACHAKHELA", "SHAVI LOMI", "CAFE LEILA", "BARBARESTAN",
              "PIZZA HUT", "CAFE GALLERY", "SALOBIE"), 0.55),
    Category("fastfood", 3.05, 14.0, 0.50, "5814", "POS",
             ("MCDONALDS", "KFC", "SUBWAY", "COFFEE LAB", "DUNKIN", "STARBUCKS"), 0.35),
    Category("pharmacy", 2.5, 30.0, 0.80, "5912", "POS", ("GPC", "PSP", "AVERSI", "PHARMADEPOT"), 0.2),
    Category("fuel", 2.85, 68.0, 0.45, "5541", "POS",
             ("WISSOL", "SOCAR", "GULF", "ROMPETROL", "PORTAL"), 0.55, prob=0.55),
    Category("rideshare", 4.75, 7.0, 0.60, "4121", "ECOM", ("BOLT", "YANDEX GO", "MAXIM"), 0.45,
             prob=0.65),
    Category("transit", 1.67, 12.0, 0.40, "4111", "POS", ("TBILISI TRANSPORT COMPANY", "METROMONEY"),
             0.05),
    Category("delivery", 2.9, 27.0, 0.60, "5812", "ECOM", ("WOLT", "GLOVO"), 0.6, prob=0.6),
    Category("ecommerce", 0.5, 55.0, 1.00, "5999", "ECOM",
             ("TEMU", "ALIEXPRESS", "AMAZON", "EXTRA.GE", "ZOOMMER.GE", "MYPARTS.GE"), 0.7, prob=0.6),
    Category("clothing", 0.73, 85.0, 0.90, "5651", "POS",
             ("ZARA", "LC WAIKIKI", "H&M", "KOTON", "MANGO", "ADIDAS"), 0.8),
    Category("electronics", 0.16, 260.0, 1.00, "5732", "POS",
             ("ZOOMMER", "ELIT ELECTRONICS", "OPTIMAL", "ALTA"), 0.9),
    Category("entertainment", 1.5, 13.0, 0.55, "7832", "ECOM",
             ("NETFLIX", "SPOTIFY", "APPLE.COM/BILL", "GOOGLE *YOUTUBE", "CAVEA CINEMA"), 0.5),
    Category("health", 0.83, 90.0, 0.90, "8011", "POS",
             ("EVEX HOSPITALS", "MEDICLUB GEORGIA", "AVERSI CLINIC", "ACADEMIK ZURAB ZHVANIA"), 0.5),
    Category("travel", 0.167, 480.0, 0.90, "4722", "ECOM",
             ("GEORGIAN AIRWAYS", "WIZZ AIR", "PEGASUS", "BOOKING.COM", "AIRBNB"), 1.0, prob=0.5),
    Category("gambling", 0.05, 42.0, 0.80, "7995", "ECOM", ("ADJARABET", "CROCOBET", "EUROPEBET"),
             0.2, prob=0.7, stress_boost=3.0),
    Category("atm_cash", 2.2, 150.0, 0.65, "6011", "ATM", ("ATM",), 0.5),
]}

# Fixed monthly bills: (key, biller name, mcc, narrative_ka, narrative_en, median GEL, winter factor)
BILLERS = [
    ("electricity", "TELASI", "4900", "ელექტროენერგია", "Electricity", 48.0, 1.5),
    ("water", "GWP", "4900", "წყალი", "Water", 12.0, 1.0),
    ("gas", "TBILISI ENERGY", "4900", "ბუნებრივი აირი", "Natural gas", 28.0, 3.4),
    ("waste", "TBILSERVICE GROUP", "4900", "დასუფთავება", "Waste collection", 5.0, 1.0),
    ("internet", "SILKNET", "4814", "ინტერნეტი", "Internet", 32.0, 1.0),
    ("mobile", "MAGTICOM", "4814", "მობილური კავშირი", "Mobile", 24.0, 1.0),
]

# Inbound private remittances: (country, account currency, weight, providers).
# Weights = NBG money-transfer inflows by source country, Aug-2026 (USD m: US 64.1, IT 55.2, DE 31.3,
# GR 28.2, IL 27.9, TR 10.6, ES 10.3, KZ 8.0, GB 7.3, FR 6.9, RU ~5), as reported by JAMnews/NBG
# (docs/CALIBRATION.md). Russia collapsed after Zolotaya Korona stopped serving Georgia in July
# 2026. The currency each corridor lands in and the provider lists are ASSUMED.
REMITTANCE_CORRIDORS = [
    ("US", "USD", 0.2516, ["WESTERN UNION", "MONEYGRAM", "RIA", "WISE"]),
    ("IT", "EUR", 0.2166, ["WESTERN UNION", "RIA", "MONEYGRAM", "WISE"]),
    ("DE", "EUR", 0.1228, ["WISE", "WESTERN UNION", "PAYSERA"]),
    ("GR", "EUR", 0.1108, ["WESTERN UNION", "MONEYGRAM", "RIA"]),
    ("IL", "USD", 0.1095, ["WESTERN UNION", "WISE", "RIA"]),
    ("TR", "USD", 0.0414, ["WESTERN UNION", "PAYSERA"]),
    ("ES", "EUR", 0.0403, ["WESTERN UNION", "RIA", "WISE"]),
    ("KZ", "USD", 0.0315, ["INTEL EXPRESS", "WESTERN UNION"]),
    ("GB", "EUR", 0.0286, ["WISE", "REVOLUT"]),
    ("FR", "EUR", 0.0271, ["WESTERN UNION", "WISE"]),
    ("RU", "USD", 0.0196, ["INTEL EXPRESS", "UNISTREAM"]),
]

# Fee for a GEL transfer to another bank, by amount ceiling in GEL (intra-bank transfers are free).
# TBC: fixed GEL 1 / 2 / 5 / 50 since 1 May 2023 (gbc.ge); the ceilings 1,000 / 10,000 / 100,000 come
# from an unattributed NBG package-comparison schedule, so other banks' use is ASSUMED.
XFER_FEE_TIERS = ((1_000.0, 1.0), (10_000.0, 2.0), (100_000.0, 5.0), (float("inf"), 50.0))

# Narratives: key -> (ka, en). {mon} is a month name.
NARR = {
    "salary": ("ხელფასი {mon}", "Salary {mon}"),
    "business_income": ("გადახდა მომსახურებაზე", "Payment for services"),
    "rent": ("ბინის ქირა {mon}", "Apartment rent {mon}"),
    "utility": ("კომუნალური გადასახადი", "Utility payment"),
    "loan": ("სესხის დაფარვა", "Loan repayment"),
    "loan_penalty": ("ჯარიმა ვადაგადაცილებაზე", "Late payment penalty"),
    "own": ("საკუთარ ანგარიშზე გადარიცხვა", "Transfer between own accounts"),
    "p2p_out": ("პირადი გადარიცხვა", "Personal transfer"),
    "p2p_in": ("პირადი გადარიცხვა", "Personal transfer"),
    "remittance": ("ფულადი გზავნილი", "Money transfer"),
    "cash_dep": ("ნაღდი ფულის შეტანა", "Cash deposit"),
    "atm": ("ბანკომატიდან განაღდება", "ATM cash withdrawal"),
    "fee": ("მომსახურების საკომისიო", "Service fee"),
    "xfer_fee": ("ბანკთაშორისი გადარიცხვის საკომისიო", "Interbank transfer fee"),
    "interest": ("ანაბრის პროცენტი", "Deposit interest"),
    "deposit_open": ("ანაბრის გახსნა", "Deposit opening"),
    "deposit_close": ("ანაბრის დახურვა", "Deposit closing"),
    "card": ("ბარათით გადახდა", "Card payment"),
    "tax": ("საგადასახადო გადახდა", "Tax payment"),
    "fx_out": ("ვალუტის კონვერტაცია", "Currency conversion"),
    "fx_in": ("ვალუტის კონვერტაცია", "Currency conversion"),
    "cc_repay": ("საკრედიტო ბარათის დაფარვა", "Credit card repayment"),
    "overdraft_fee": ("ოვერდრაფტის საკომისიო", "Overdraft fee"),
}

# ISO 18245 MCC -> short label (used for card transaction detail / proprietary codes)
MCC_LABEL = {
    "5411": "Grocery Stores", "5499": "Misc Food Stores", "5812": "Restaurants",
    "5814": "Fast Food", "5912": "Drug Stores", "5541": "Service Stations", "4121": "Taxi/Rideshare",
    "4111": "Local Transit", "5999": "Misc Retail", "5651": "Family Clothing",
    "5732": "Electronics", "7832": "Cinemas", "8011": "Doctors", "4722": "Travel Agencies",
    "7995": "Betting", "6011": "ATM Cash", "4900": "Utilities", "4814": "Telecom",
}

# Fallback FX level (GEL per unit) for the AR(1) rate walk, used only when the vendored NBG series
# (specs/reference/nbg_official_fx_2025_2026.csv) does not cover the window. Levels = mean of that
# series over 2025-09-15..2026-10-21 (USD 2.674, EUR 3.103, GBP 3.584).
FX_BASE = {"USD": 2.67, "EUR": 3.10, "GBP": 3.58}
FX_SPREAD = 0.012  # bank buy/sell half-spread

# Annual term-deposit rates by currency and tenor in months. The LEVEL is NBG's weighted average of
# July 2026 (GEL 8.91%, foreign currency 2.52%; 1tv.ge); the term structure around it is ASSUMED.
DEPOSIT_RATES = {
    "GEL": {3: 0.080, 6: 0.087, 12: 0.092, 24: 0.094},
    "USD": {3: 0.020, 6: 0.025, 12: 0.030, 24: 0.032},
    "EUR": {3: 0.012, 6: 0.016, 12: 0.020, 24: 0.022},
}

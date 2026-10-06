"""H3.1: Quantity model matrix tests (DOM-1, DOM-8).

All test cases in this suite are strictly independent of app/demo/corpus.
Every test case is asserted to have zero overlap with the demo corpus.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.claims.numbers import extract_dates, extract_quantities

CORPUS_DIR = Path("app/demo/corpus")


@pytest.fixture(scope="module")
def corpus_text() -> str:
    """Concatenated contents of all files in app/demo/corpus."""
    texts: list[str] = []
    for file_path in CORPUS_DIR.glob("*.md"):
        texts.append(file_path.read_text(encoding="utf-8"))
    return "\n".join(texts)


# --- 1. Multipliers / Scale factors (10 cases) ---
SCALE_CASES = [
    ("The dataset contains 7B parameters.", [(7e9, "params", "params", "")]),
    ("The company raised $5M in seed funding.", [(5e6, "usd", "currency", "")]),
    ("Cluster memory capacity is 10K gigabytes.", [(10000.0, "gb", "measurement", "")]),
    ("The model was trained on 2.5T tokens.", [(2.5e12, "tokens", "tokens", "")]),
    ("The factory produced 3.5万 units of vehicles.", [(35000.0, "", "count", "vehicles")]),
    ("Annual revenue reached 5000万 美元.", [(5e7, "usd", "currency", "")]),
    ("Government invested 300亿 元 in renewable energy.", [(3e10, "cny", "currency", "")]),
    ("The telescope detected 8 billion stars.", [(8e9, "", "count", "stars")]),
    ("The organization has 15 thousand members.", [(15000.0, "", "count", "members")]),
    ("The library archives 4 million documents.", [(4e6, "", "count", "documents")]),
]

# --- 2. Bare currencies (10 cases) ---
CURRENCY_CASES = [
    ("The total startup cost was $300 million.", [(3e8, "usd", "currency", "")]),
    ("Each ticket costs $2000.", [(2000.0, "usd", "currency", "")]),
    ("The grant provided €500 in research support.", [(500.0, "eur", "currency", "")]),
    ("Membership costs £40 annually.", [(40.0, "gbp", "currency", "")]),
    ("The application fee is 50元.", [(50.0, "cny", "currency", "")]),
    ("Project investment totaled 300亿元.", [(3e10, "cny", "currency", "")]),
    ("The monthly allowance was 100 欧元.", [(100.0, "eur", "currency", "")]),
    ("Daily expense limit was 50 美元.", [(50.0, "usd", "currency", "")]),
    ("The repair estimated at ¥2500.", [(2500.0, "cny", "currency", "")]),
    ("The price of the textbook is 85 人民币.", [(85.0, "cny", "currency", "")]),
]

# --- 3. Currencies with denominators (8 cases) ---
CURRENCY_DENOM_CASES = [
    ("Electricity generation costs $120/kWh in peak hours.", [(120.0, "usd/kwh", "currency", "")]),
    ("Highway toll charges 30 元/公里.", [(30.0, "cny/km", "currency", "")]),
    ("Consulting services cost 5 €/hour.", [(5.0, "eur/hr", "currency", "")]),
    ("Software subscription is 100 $/month.", [(100.0, "usd/month", "currency", "")]),
    ("API inference billed at 0.002 美元/次.", [(0.002, "usd/次", "currency", "")]),
    ("Public parking fee is $15 per day.", [(15.0, "usd/day", "currency", "")]),
    ("Subscription costs 12 欧元 per month.", [(12.0, "eur/month", "currency", "")]),
    ("High-speed charging tariff is 2.5 元每度.", [(2.5, "cny/度", "currency", "")]),
]

# --- 4. Percent vs percentage points (8 cases) ---
PERCENT_CASES = [
    ("Market share grew to 24%.", [(24.0, "%", "percentage", "")]),
    ("Approval rating fell by 12 percent.", [(12.0, "%", "percentage", "")]),
    ("Inflation index rose by 0.5 percentage points.", [(0.5, "pp", "percentage_points", "")]),
    ("Benchmark spread widened by 1.5 percentage point.", [(1.5, "pp", "percentage_points", "")]),
    ("Interest rate increased by 0.25 pp.", [(0.25, "pp", "percentage_points", "")]),
    ("Economic margin expanded by 2 个百分点.", [(2.0, "pp", "percentage_points", "")]),
    ("Voting threshold raised by 3 百分点.", [(3.0, "pp", "percentage_points", "")]),
    ("Yield curve rose by 0.75 pp in August.", [(0.75, "pp", "percentage_points", "")]),
]

# --- 5. Generic denominators (8 cases) ---
GENERIC_DENOM_CASES = [
    ("EV fuel efficiency reached 50 Wh/mile.", [(50.0, "wh/mile", "measurement", "")]),
    ("Carbon capture fee is 120 $/tonne.", [(120.0, "usd/tonne", "currency", "")]),
    ("Cloud service usage quota is 10 usd/user.", [(10.0, "usd/user", "currency", "")]),
    ("Server throughput averaged 500 tokens/second.", [(500.0, "tokens/second", "tokens", "")]),
    ("Vehicular speed limit is 40 km/h.", [(40.0, "km/h", "distance", "")]),
    ("Thermal dissipation capacity was 85 W/m2.", [(85.0, "w/m2", "power", "")]),
    ("Water treatment capacity reached 1000 m3/day.", [(1000.0, "m3/day", "measurement", "")]),
    ("Pipeline flow measured at 200 kg/sec.", [(200.0, "kg/sec", "mass", "")]),
]

# --- 6. Intervals / Ranges (10 cases) ---
INTERVAL_CASES = [
    (
        "Expected operating margin is 10–20%.",
        15.0,
        "%",
        "percentage",
        "",
        10.0,
        20.0,
    ),
    (
        "Battery pack output from 380 to 450 Wh/kg.",
        415.0,
        "wh/kg",
        "energy_density",
        "",
        380.0,
        450.0,
    ),
    (
        "Estimated budget is between $100 and $200.",
        150.0,
        "usd",
        "currency",
        "",
        100.0,
        200.0,
    ),
    (
        "电池容量从 380 到 450 Wh/kg.",
        415.0,
        "wh/kg",
        "energy_density",
        "",
        380.0,
        450.0,
    ),
    (
        "预计增长区间为 10% 到 20%.",
        15.0,
        "%",
        "percentage",
        "",
        10.0,
        20.0,
    ),
    (
        "Production volume between 5000 and 8000 units.",
        6500.0,
        "",
        "count",
        "units",
        5000.0,
        8000.0,
    ),
    (
        "Ambient temperature range from 15 to 25 °C.",
        20.0,
        "c",
        "temperature",
        "",
        15.0,
        25.0,
    ),
    (
        "Flight endurance spans from 8 to 12 hours.",
        10.0,
        "hr",
        "duration",
        "",
        8.0,
        12.0,
    ),
    (
        "Target price set between 50 and 80 元.",
        65.0,
        "cny",
        "currency",
        "",
        50.0,
        80.0,
    ),
    (
        "Discount rate between 5% and 10%.",
        7.5,
        "%",
        "percentage",
        "",
        5.0,
        10.0,
    ),
]

# --- 7. Negative numbers and decrease signals (8 cases) ---
NEGATIVE_CASES = [
    ("Operating loss widened to -2%.", [(-2.0, "%", "percentage", "")]),
    ("Outdoor temperature dropped to −5.0 °C.", [(-5.0, "c", "temperature", "")]),
    ("Q3 revenue dropped by -10 million usd.", [(-1e7, "usd", "currency", "")]),
    ("季度出货量下降 15%.", [(-15.0, "%", "percentage", "")]),
    ("企业运营成本减少 10%.", [(-10.0, "%", "percentage", "")]),
    ("出口总额同比下跌 8%.", [(-8.0, "%", "percentage", "")]),
    ("Net yield variance was -1.5 pp.", [(-1.5, "pp", "percentage_points", "")]),
    ("Energy consumption lowered to −12 kWh.", [(-12.0, "kwh", "energy", "")]),
]

# --- 8. Exclusions: Years, Model names, Version identifiers (10 cases) ---
EXCLUSION_CASES = [
    "In 2024, OpenAI introduced exciting capabilities.",
    "The GPT-4 architecture revolutionized reasoning.",
    "Engineers redesigned the HX-2 cooling manifold.",
    "Apple launched iPhone 15 with titanium enclosure.",
    "Chapter 3 explains the mathematical formulation.",
    "Microsoft released Windows 11 update worldwide.",
    "Meta trained Llama 3 on diverse data sources.",
    "The protocol complies with Wi-Fi 6 specifications.",
    "Boeing 737 completed certification flight tests.",
    "Section 4 summarizes regulatory guidelines.",
]

# --- 9. Chinese numerals (10 cases) ---
CHINESE_CASES = [
    ("该基金投资规模达三亿美元.", [(3e8, "usd", "currency", "")]),
    ("实验招募了一千二百名参与者.", [(1200.0, "", "count", "参与者")]),
    ("产品及格率为百分之十八.", [(18.0, "%", "percentage", "")]),
    ("首批用户规模突破两万五千人.", [(25000.0, "", "count", "人")]),
    ("不良反应发生率小于百分之零点五.", [(0.5, "%", "percentage", "")]),
    ("集团注资三亿人民币用于研发中心.", [(3e8, "cny", "currency", "")]),
    ("新车型续航达到六百公里.", [(600.0, "km", "distance", "")]),
    ("全厂配置了五百台服务器.", [(500.0, "", "count", "服务器")]),
    ("该企业拥有四万名员工.", [(40000.0, "", "count", "员工")]),
    ("项目共计两千个核心进行并行运算.", [(2000.0, "", "count", "核心")]),
]

# --- 10. Unitless counts with center nouns (10 cases) ---
COUNT_CASES = [
    ("The clinical trial enrolled 900 participants.", [(900.0, "", "count", "participants")]),
    ("The compute cluster contains 2048 cores.", [(2048.0, "", "count", "cores")]),
    ("Hospital admitted 120 patients during the trial.", [(120.0, "", "count", "patients")]),
    ("Platform registered 5000 users on launch day.", [(5000.0, "", "count", "users")]),
    ("Data center operates 1000台服务器.", [(1000.0, "", "count", "服务器")]),
    ("Logistics fleet deployed 50辆车.", [(50.0, "", "count", "车")]),
    ("Meeting attended by 300人 in the auditorium.", [(300.0, "", "count", "人")]),
    ("Survey gathered responses from 450 subjects.", [(450.0, "", "count", "subjects")]),
    ("Warehouse stored 800 samples under cold chain.", [(800.0, "", "count", "samples")]),
    ("Testing lab processed 600 devices sequentially.", [(600.0, "", "count", "devices")]),
]

ALL_TEST_SENTENCES = (
    [text for text, _ in SCALE_CASES]
    + [text for text, _ in CURRENCY_CASES]
    + [text for text, _ in CURRENCY_DENOM_CASES]
    + [text for text, _ in PERCENT_CASES]
    + [text for text, _ in GENERIC_DENOM_CASES]
    + [text for text, *_ in INTERVAL_CASES]
    + [text for text, _ in NEGATIVE_CASES]
    + list(EXCLUSION_CASES)
    + [text for text, _ in CHINESE_CASES]
    + [text for text, _ in COUNT_CASES]
)


def test_zero_overlap_with_demo_corpus(corpus_text: str):
    """MANDATORY: Assert that none of the matrix test cases overlap with app/demo/corpus."""
    assert len(ALL_TEST_SENTENCES) >= 80, f"Must have >= 80 test cases, got {len(ALL_TEST_SENTENCES)}"
    for sentence in ALL_TEST_SENTENCES:
        assert sentence not in corpus_text, f"Test sentence leaks into demo corpus: {sentence!r}"


@pytest.mark.parametrize(("text", "expected"), SCALE_CASES)
def test_scales_and_multipliers(text: str, expected: list):
    got = [(q.value, q.unit, q.dimension, q.head) for q in extract_quantities(text)]
    assert got == expected


@pytest.mark.parametrize(("text", "expected"), CURRENCY_CASES)
def test_bare_currencies(text: str, expected: list):
    got = [(q.value, q.unit, q.dimension, q.head) for q in extract_quantities(text)]
    assert got == expected


@pytest.mark.parametrize(("text", "expected"), CURRENCY_DENOM_CASES)
def test_currency_denominators(text: str, expected: list):
    got = [(q.value, q.unit, q.dimension, q.head) for q in extract_quantities(text)]
    assert got == expected


@pytest.mark.parametrize(("text", "expected"), PERCENT_CASES)
def test_percent_and_percentage_points(text: str, expected: list):
    got = [(q.value, q.unit, q.dimension, q.head) for q in extract_quantities(text)]
    assert got == expected


@pytest.mark.parametrize(("text", "expected"), GENERIC_DENOM_CASES)
def test_generic_denominators(text: str, expected: list):
    got = [(q.value, q.unit, q.dimension, q.head) for q in extract_quantities(text)]
    assert got == expected


@pytest.mark.parametrize(
    ("text", "val", "unit", "dim", "head", "low", "high"),
    INTERVAL_CASES,
)
def test_interval_quantities(
    text: str,
    val: float,
    unit: str,
    dim: str,
    head: str,
    low: float,
    high: float,
):
    quantities = extract_quantities(text)
    assert len(quantities) == 1, f"Expected 1 quantity for {text!r}, got {quantities}"
    q = quantities[0]
    assert q.value == pytest.approx(val)
    assert q.unit == unit
    assert q.dimension == dim
    assert q.head == head
    assert q.low == pytest.approx(low)
    assert q.high == pytest.approx(high)
    assert q.is_interval is True
    assert q.span == (q.start, q.end)


@pytest.mark.parametrize(("text", "expected"), NEGATIVE_CASES)
def test_negative_numbers_and_decrease(text: str, expected: list):
    got = [(q.value, q.unit, q.dimension, q.head) for q in extract_quantities(text)]
    assert got == expected


@pytest.mark.parametrize("text", EXCLUSION_CASES)
def test_model_names_and_bare_years_excluded(text: str):
    """Years and model numbers must NOT be extracted as quantities."""
    quantities = extract_quantities(text)
    assert quantities == [], f"Expected 0 quantities for {text!r}, got {quantities}"


@pytest.mark.parametrize(("text", "expected"), CHINESE_CASES)
def test_chinese_quantities(text: str, expected: list):
    got = [(q.value, q.unit, q.dimension, q.head) for q in extract_quantities(text)]
    assert got == expected


@pytest.mark.parametrize(("text", "expected"), COUNT_CASES)
def test_unitless_count_heads(text: str, expected: list):
    got = [(q.value, q.unit, q.dimension, q.head) for q in extract_quantities(text)]
    assert got == expected


def test_date_iso_does_not_parse_invalid_month():
    """DOM-8: '2025-26' must not be parsed as a date with month 26."""
    dates = extract_dates("Fiscal period was 2025-26.")
    for d in dates:
        if d.month is not None:
            assert 1 <= d.month <= 12

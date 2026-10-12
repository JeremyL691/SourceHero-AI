"""Generate the hand-written evaluation datasets under `evals/data/`.

Written as a script rather than a static blob so every case sits next to the note
explaining *what it probes*. Regenerating is a reviewable diff.

    uv run python scripts/build_eval_datasets.py

Rules these datasets follow (from the plan):
  * claim_pairs: >= 80 pairs, >= 20% Chinese, covering same-unit disagreement, unit
    conversion, negation, date postponement, paraphrase, and three families of trap
    cases (same number / different subject, different metric, qualifier explains the
    difference) -- all labelled `unrelated`.
  * None of these sentences come from the demo corpus, and none are used as
    few-shot examples in a prompt. A benchmark that shares text with the thing it
    measures does not measure it.
  * verify_cases: >= 60 cases, >= 10 per verdict.
  * extraction_cases: >= 15 passages with gold quote offsets.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evals" / "data"


def claim_pairs() -> list[dict]:
    """(a, b, label, lang, phenomenon)."""
    rows: list[tuple[str, str, str, str, str]] = [
        # --- same unit, different value -> contradicts
        ("The turbine rated 4.8 MW.", "The turbine rated 5.1 MW.", "contradicts", "en", "same_unit_value"),
        (
            "The reactor core reaches 320 °C.",
            "The reactor core reaches 355 °C.",
            "contradicts",
            "en",
            "same_unit_value",
        ),
        (
            "The reservoir holds 1,200 megalitres.",
            "The reservoir holds 1,450 megalitres.",
            "contradicts",
            "en",
            "same_unit_value",
        ),
        (
            "The bridge deck spans 410 metres.",
            "The bridge deck spans 388 metres.",
            "contradicts",
            "en",
            "same_unit_value",
        ),
        (
            "Sensor drift stayed under 0.4% over the trial.",
            "Sensor drift stayed under 1.2% over the trial.",
            "contradicts",
            "en",
            "same_unit_value",
        ),
        ("该电池组的容量为 82 kWh。", "该电池组的容量为 96 kWh。", "contradicts", "zh", "same_unit_value"),
        ("隧道全长为 3.6 公里。", "隧道全长为 4.1 公里。", "contradicts", "zh", "same_unit_value"),
        (
            "该配方的含糖量为每百克 12 克。",
            "该配方的含糖量为每百克 19 克。",
            "contradicts",
            "zh",
            "same_unit_value",
        ),
        # --- identical wording -> duplicate (K1.1: these were labelled supports, which
        # contradicted pair-071/072; see DECISIONS 2026-10-12 K1.1)
        (
            "The array produced 250 MW at midday.",
            "The array produced 250 MW at midday.",
            "duplicate",
            "en",
            "same_value",
        ),
        (
            "The survey covered 1,800 households.",
            "The survey covered 1,800 households.",
            "duplicate",
            "en",
            "same_value",
        ),
        ("该站点日均处理 4,500 次请求。", "该站点日均处理 4,500 次请求。", "duplicate", "zh", "same_value"),
        (
            "The vaccine was stored at -70 °C.",
            "The vaccine was stored at -70 °C.",
            "duplicate",
            "en",
            "same_value",
        ),
        # --- paraphrase, different words, same claim -> supports
        (
            "The engine reached 2,000 revolutions per minute.",
            "The motor turned at 2,000 rpm.",
            "supports",
            "en",
            "paraphrase",
        ),
        (
            "The study enrolled 240 participants.",
            "240 people took part in the study.",
            "supports",
            "en",
            "paraphrase",
        ),
        (
            "The glacier retreated 30 metres.",
            "The glacier moved back by 30 metres.",
            "supports",
            "en",
            "paraphrase",
        ),
        (
            "The subsidy totals €2 million.",
            "The subsidy amounts to 2 million euros.",
            "supports",
            "en",
            "paraphrase",
        ),
        (
            "该管线的设计输量为每天 800 万立方米。",
            "该管线每天可输送 800 万立方米。",
            "supports",
            "zh",
            "paraphrase",
        ),
        ("The pilot ran for eleven weeks.", "The trial lasted 11 weeks.", "supports", "en", "paraphrase"),
        # --- negation -> contradicts
        (
            "The coating prevented corrosion.",
            "The coating did not prevent corrosion.",
            "contradicts",
            "en",
            "negation",
        ),
        (
            "The levy reduced congestion.",
            "The levy did not reduce congestion.",
            "contradicts",
            "en",
            "negation",
        ),
        ("The device is waterproof.", "The device is not waterproof.", "contradicts", "en", "negation"),
        ("该方案降低了能耗。", "该方案没有降低能耗。", "contradicts", "zh", "negation"),
        (
            "The plant never reached full capacity.",
            "The plant reached full capacity.",
            "contradicts",
            "en",
            "negation",
        ),
        # --- date moved -> contradicts (and would be supersedes with an update cue)
        (
            "The opening is scheduled for March 2027.",
            "The opening is scheduled for November 2027.",
            "contradicts",
            "en",
            "date_moved",
        ),
        ("The trial ends in Q2 2028.", "The trial ends in Q4 2028.", "contradicts", "en", "date_moved"),
        ("项目将于 2027 年 5 月启动。", "项目将于 2027 年 9 月启动。", "contradicts", "zh", "date_moved"),
        ("The service retires in 2030.", "The service retires in 2031.", "contradicts", "en", "date_moved"),
        # --- unit conversion of the same quantity -> supports
        (
            "The panel generates 500 watts.",
            "The panel generates 0.5 kilowatts.",
            "supports",
            "en",
            "unit_conversion",
        ),
        ("The flight took 3 hours.", "The flight took 180 minutes.", "supports", "en", "unit_conversion"),
        (
            "The tank holds 2,000 litres.",
            "The tank holds 2 cubic metres.",
            "supports",
            "en",
            "unit_conversion",
        ),
        (
            "The distance was 5 kilometres.",
            "The distance was 5,000 metres.",
            "supports",
            "en",
            "unit_conversion",
        ),
        # --- TRAP: same number, different subject -> unrelated
        (
            "The turbine produced 5.1 MW.",
            "The pump produced 5.1 MW.",
            "unrelated",
            "en",
            "trap_same_number_diff_subject",
        ),
        (
            "The hotel has 120 rooms.",
            "The hotel has 120 bathrooms.",
            "unrelated",
            "en",
            "trap_same_number_diff_subject",
        ),
        (
            "The library holds 40,000 books.",
            "The library holds 40,000 members.",
            "unrelated",
            "en",
            "trap_same_number_diff_subject",
        ),
        (
            "该电站装机容量为 2,400 MW。",
            "该电站年发电量为 2,400 MWh。",
            "unrelated",
            "zh",
            "trap_same_number_diff_subject",
        ),
        (
            "The school has 600 students.",
            "The school has 600 parking spaces.",
            "unrelated",
            "en",
            "trap_same_number_diff_subject",
        ),
        # --- TRAP: different metric entirely -> unrelated
        (
            "Revenue rose 12% in 2026.",
            "Headcount rose 12% in 2026.",
            "unrelated",
            "en",
            "trap_different_metric",
        ),
        (
            "The engine is 92% efficient.",
            "The engine failed 92% of tests.",
            "unrelated",
            "en",
            "trap_different_metric",
        ),
        (
            "The drug reduced mortality by 8%.",
            "The drug reduced cost by 8%.",
            "unrelated",
            "en",
            "trap_different_metric",
        ),
        ("该产品的良率为 98%。", "该产品的退货率为 98%。", "unrelated", "zh", "trap_different_metric"),
        (
            "The team shipped 30 features.",
            "The team shipped 30 features per week.",
            "refines",
            "en",
            "trap_qualifier_explains",
        ),
        # --- TRAP: a qualifier explains the difference. `refines` when the second statement
        # adds a qualifier to the same claim (K1.1); different values under different
        # conditions stay `unrelated`, as relation_judge.md prefers.
        (
            "The car does 30 mpg on the highway.",
            "The car does 22 mpg in city driving.",
            "unrelated",
            "en",
            "trap_qualifier_explains",
        ),
        (
            "The model scores 0.82 in English.",
            "The model scores 0.71 in Arabic.",
            "unrelated",
            "en",
            "trap_qualifier_explains",
        ),
        (
            "The clinic treated 300 patients in March.",
            "The clinic treated 300 patients in March alone.",
            "refines",
            "en",
            "trap_qualifier_explains",
        ),
        (
            "该材料在室温下强度为 400 MPa。",
            "该材料在 200 °C 下强度为 400 MPa。",
            "unrelated",
            "zh",
            "trap_qualifier_explains",
        ),
        (
            "The loan has a 5% rate.",
            "The loan has a 5% rate for the first year.",
            "refines",
            "en",
            "trap_qualifier_explains",
        ),
        # --- more same-unit disagreements
        (
            "The wind farm has 96 turbines.",
            "The wind farm has 84 turbines.",
            "contradicts",
            "en",
            "same_unit_value",
        ),
        (
            "The pipeline is 1,180 kilometres long.",
            "The pipeline is 1,240 kilometres long.",
            "contradicts",
            "en",
            "same_unit_value",
        ),
        (
            "The school built 6 new classrooms.",
            "The school built 4 new classrooms.",
            "contradicts",
            "en",
            "same_unit_value",
        ),
        ("The deposit rate is 3.1%.", "The deposit rate is 2.6%.", "contradicts", "en", "same_unit_value"),
        (
            "该仓库面积为 12,000 平方米。",
            "该仓库面积为 15,500 平方米。",
            "contradicts",
            "zh",
            "same_unit_value",
        ),
        (
            "该市民中心每日接待 3,200 人次。",
            "该市民中心每日接待 4,100 人次。",
            "contradicts",
            "zh",
            "same_unit_value",
        ),
        (
            "该路段限速为 80 公里/小时。",
            "该路段限速为 100 公里/小时。",
            "contradicts",
            "zh",
            "same_unit_value",
        ),
        (
            "该企业研发投入为 2.3 亿元。",
            "该企业研发投入为 3.1 亿元。",
            "contradicts",
            "zh",
            "same_unit_value",
        ),
        # --- more Chinese paraphrase / supports
        ("该模型的准确率为 87%。", "该模型的准确率是 87%。", "duplicate", "zh", "duplicate"),
        ("该项目于 2024 年竣工。", "该项目在 2024 年完成建设。", "supports", "zh", "paraphrase"),
        ("该线路全长 26 公里。", "该线路总长度为 26 公里。", "supports", "zh", "paraphrase"),
        ("该产品的售价为 2,499 元。", "该产品标价 2,499 元。", "supports", "zh", "paraphrase"),
        # --- more Chinese negation
        ("该方案提高了通行效率。", "该方案并未提高通行效率。", "contradicts", "zh", "negation"),
        ("该结论已被后续研究推翻。", "该结论未被后续研究推翻。", "contradicts", "zh", "negation"),
        # --- more Chinese traps
        (
            "该球队的主场容量为 5.2 万人。",
            "该球队的客场容量为 5.2 万人。",
            "unrelated",
            "zh",
            "trap_same_number_diff_subject",
        ),
        ("该指数上涨了 6%。", "该基金的持仓上涨了 6%。", "unrelated", "zh", "trap_different_metric"),
        ("该建筑层高为 4.2 米。", "该建筑总层数为 4.2。", "unrelated", "zh", "trap_same_number_diff_subject"),
        # --- more English traps and qualifiers
        (
            "The margin was 18%.",
            "The margin was 18% last quarter.",
            "refines",
            "en",
            "trap_qualifier_explains",
        ),
        (
            "The assay detects 5 ng/mL.",
            "The assay detects 5 µg/mL.",
            "unrelated",
            "en",
            "trap_different_metric",
        ),
        (
            "The factory employs 2,100 people.",
            "The factory covers 2,100 square metres.",
            "unrelated",
            "en",
            "trap_different_metric",
        ),
        # --- supersedes
        (
            "The policy takes effect in July.",
            "The policy was deferred to November.",
            "supersedes",
            "en",
            "revised_schedule",
        ),
        ("该项目原定于 5 月开工。", "该项目已推迟至 9 月开工。", "supersedes", "zh", "revised_schedule"),
        # --- supersedes-shaped: a revised schedule
        (
            "The launch is set for April 2026.",
            "The launch has been postponed to October 2026.",
            "supersedes",
            "en",
            "revised_schedule",
        ),
        (
            "The report will be published in May.",
            "The report was revised for July.",
            "supersedes",
            "en",
            "revised_schedule",
        ),
        # --- duplicate: identical wording
        ("The alloy melts at 660 °C.", "The alloy melts at 660 °C.", "duplicate", "en", "duplicate"),
        ("该实验重复了三次。", "该实验重复了三次。", "duplicate", "zh", "duplicate"),
        # --- K1.1: four distinct pairs, added so the set keeps its 80-pair floor after the
        # duplicated padding was removed (see DECISIONS 2026-10-12 K1.1)
        (
            "The campus enrolls 4,000 students.",
            "The campus enrolls 4,000 students full-time.",
            "refines",
            "en",
            "qualifier_refines",
        ),
        (
            "The bridge was not closed for repairs in 2025.",
            "The bridge was closed for repairs in 2025.",
            "contradicts",
            "en",
            "negation",
        ),
        ("该基金规模为 80 亿元。", "该基金的年化收益率为 8%。", "unrelated", "zh", "trap_different_metric"),
        ("该工程于 2025 年底完工。", "该工程在 2025 年末竣工。", "supports", "zh", "paraphrase"),
    ]

    out: list[dict] = []
    for index, (a, b, label, lang, phenomenon) in enumerate(rows, start=1):
        out.append(
            {
                "id": f"pair-{index:03d}",
                "a": a,
                "b": b,
                "a_date": "2026-03-15",
                "b_date": "2026-06-20",
                "label": label,
                "lang": lang,
                "phenomenon": phenomenon,
            }
        )

    # Pad with same-unit disagreements to clear the 80-pair floor. K1.1: the earlier padding
    # repeated these four pairs twice (EVAL-9), so every row here is now distinct.
    # One string per pair, so `format` fills both sides at once.
    templates = [
        "The spillway discharges {n} cubic metres per second. | The spillway discharges {m} cubic metres per second.",
        "The sensor array has {n} nodes. | The sensor array has {m} nodes.",
        "The cargo ship carries {n} containers. | The cargo ship carries {m} containers.",
        "The hospital has {n} beds. | The hospital has {m} beds.",
    ]
    index = len(out)
    pairs = [(1850, 1620), (64, 48), (7200, 6400), (460, 412)]
    for template, (n, m) in zip(templates, pairs, strict=True):
        index += 1
        left, right = template.format(n=n, m=m).split(" | ")
        out.append(
            {
                "id": f"pair-{index:03d}",
                "a": left,
                "b": right,
                "a_date": "2026-02-01",
                "b_date": "2026-05-01",
                "label": "contradicts",
                "lang": "en",
                "phenomenon": "same_unit_value",
            }
        )
    return out


def verify_cases() -> list[dict]:
    """(sentence, [(evidence_id, text, source, date)], label)."""
    rows: list[tuple[str, list[tuple[int, str, str, str]], str]] = [
        # --- supported (>= 10)
        (
            "The Kestrel array generates 320 MW of electricity.",
            [(1, "The Kestrel array generates 320 MW of electricity.", "Grid Weekly", "2026-02-01")],
            "supported",
        ),
        (
            "The Meridian tunnel is 3.6 kilometres long.",
            [(1, "The Meridian tunnel is 3.6 kilometres long.", "Civil Record", "2026-01-15")],
            "supported",
        ),
        (
            "Vaccination coverage reached 78% in the district.",
            [(1, "Vaccination coverage reached 78% in the district.", "Health Bulletin", "2026-03-02")],
            "supported",
        ),
        (
            "The Northgate bridge cost £41 million to build.",
            [(1, "The Northgate bridge cost £41 million to build.", "Civic Ledger", "2026-04-10")],
            "supported",
        ),
        (
            "该水电站的年发电量为 32 亿千瓦时。",
            [(1, "该水电站的年发电量为 32 亿千瓦时。", "能源周报", "2026-02-20")],
            "supported",
        ),
        (
            "The alloy tolerates 400 MPa at room temperature.",
            [(1, "The alloy tolerates 400 MPa at room temperature.", "Materials Review", "2026-05-06")],
            "supported",
        ),
        (
            "The pilot programme ran for eleven weeks.",
            [(1, "The pilot programme ran for eleven weeks.", "Programme Office", "2026-06-01")],
            "supported",
        ),
        (
            "Rainfall in the catchment was 620 mm last year.",
            [(1, "Rainfall in the catchment was 620 mm last year.", "Meteorological Digest", "2026-01-08")],
            "supported",
        ),
        (
            "The clinic treated 312 patients in March.",
            [(1, "The clinic treated 312 patients in March.", "District Health", "2026-04-02")],
            "supported",
        ),
        (
            "The satellite completed its orbit in 94 minutes.",
            [(1, "The satellite completed its orbit in 94 minutes.", "Orbital Notes", "2026-02-11")],
            "supported",
        ),
        (
            "Recycling rates reached 64% in the metropolitan area.",
            [(1, "Recycling rates reached 64% in the metropolitan area.", "City Report", "2026-03-18")],
            "supported",
        ),
        (
            "该基金的净资产为 4.7 亿美元。",
            [(1, "该基金的净资产为 4.7 亿美元。", "财经观察", "2026-05-22")],
            "supported",
        ),
        # --- contradicted (>= 10)
        (
            "The Kestrel array generates 320 MW of electricity.",
            [(1, "The Kestrel array generates 210 MW of electricity.", "Grid Weekly", "2026-02-01")],
            "contradicted",
        ),
        (
            "The Meridian tunnel is 3.6 kilometres long.",
            [(1, "The Meridian tunnel is 4.2 kilometres long.", "Civil Record", "2026-01-15")],
            "contradicted",
        ),
        (
            "Vaccination coverage reached 78% in the district.",
            [(1, "Vaccination coverage reached 61% in the district.", "Health Bulletin", "2026-03-02")],
            "contradicted",
        ),
        (
            "The coating prevented corrosion on the steel.",
            [(1, "The coating did not prevent corrosion on the steel.", "Materials Review", "2026-05-06")],
            "contradicted",
        ),
        (
            "The levy reduced congestion in the city centre.",
            [(1, "The levy did not reduce congestion in the city centre.", "Civic Ledger", "2026-04-10")],
            "contradicted",
        ),
        (
            "The reservoir holds 1,200 megalitres.",
            [(1, "The reservoir holds 1,450 megalitres.", "Water Review", "2026-02-14")],
            "contradicted",
        ),
        (
            "该水电站的年发电量为 32 亿千瓦时。",
            [(1, "该水电站的年发电量为 41 亿千瓦时。", "能源周报", "2026-02-20")],
            "contradicted",
        ),
        (
            "The train service runs every 15 minutes.",
            [(1, "The train service runs every 45 minutes.", "Transit Report", "2026-06-12")],
            "contradicted",
        ),
        (
            "The device is waterproof to a depth of 30 metres.",
            [(1, "The device is not waterproof to a depth of 30 metres.", "Field Notes", "2026-03-05")],
            "contradicted",
        ),
        (
            "The survey covered 1,800 households.",
            [(1, "The survey covered 920 households.", "Methodology Page", "2026-01-30")],
            "contradicted",
        ),
        (
            "The reactor will begin operations in March 2028.",
            [(1, "The reactor will begin operations in November 2028.", "Energy Outlook", "2026-05-30")],
            "contradicted",
        ),
        # --- disputed (>= 10): one source supports, another contradicts
        (
            "The Kestrel array generates 320 MW of electricity.",
            [
                (1, "The Kestrel array generates 320 MW of electricity.", "Grid Weekly", "2026-02-01"),
                (2, "The Kestrel array generates 210 MW of electricity.", "Independent Audit", "2026-03-04"),
            ],
            "disputed",
        ),
        (
            "The Meridian tunnel is 3.6 kilometres long.",
            [
                (1, "The Meridian tunnel is 3.6 kilometres long.", "Civil Record", "2026-01-15"),
                (2, "The Meridian tunnel is 4.2 kilometres long.", "Engineering Review", "2026-02-09"),
            ],
            "disputed",
        ),
        (
            "Vaccination coverage reached 78% in the district.",
            [
                (1, "Vaccination coverage reached 78% in the district.", "Health Bulletin", "2026-03-02"),
                (2, "Vaccination coverage reached 61% in the district.", "District Health", "2026-03-20"),
            ],
            "disputed",
        ),
        (
            "The Northgate bridge cost £41 million to build.",
            [
                (1, "The Northgate bridge cost £41 million to build.", "Civic Ledger", "2026-04-10"),
                (2, "The Northgate bridge cost £58 million to build.", "Audit Office", "2026-05-02"),
            ],
            "disputed",
        ),
        (
            "The pilot programme ran for eleven weeks.",
            [
                (1, "The pilot programme ran for eleven weeks.", "Programme Office", "2026-06-01"),
                (2, "The pilot programme ran for nineteen weeks.", "Independent Review", "2026-06-18"),
            ],
            "disputed",
        ),
        (
            "The reservoir holds 1,200 megalitres.",
            [
                (1, "The reservoir holds 1,200 megalitres.", "Water Review", "2026-02-14"),
                (2, "The reservoir holds 1,450 megalitres.", "Regional Audit", "2026-03-01"),
            ],
            "disputed",
        ),
        (
            "Rainfall in the catchment was 620 mm last year.",
            [
                (1, "Rainfall in the catchment was 620 mm last year.", "Meteorological Digest", "2026-01-08"),
                (2, "Rainfall in the catchment was 505 mm last year.", "Regional Water Board", "2026-01-22"),
            ],
            "disputed",
        ),
        (
            "该水电站的年发电量为 32 亿千瓦时。",
            [
                (1, "该水电站的年发电量为 32 亿千瓦时。", "能源周报", "2026-02-20"),
                (2, "该水电站的年发电量为 41 亿千瓦时。", "独立核查", "2026-03-15"),
            ],
            "disputed",
        ),
        (
            "The satellite completed its orbit in 94 minutes.",
            [
                (1, "The satellite completed its orbit in 94 minutes.", "Orbital Notes", "2026-02-11"),
                (2, "The satellite completed its orbit in 101 minutes.", "Tracking Service", "2026-02-27"),
            ],
            "disputed",
        ),
        (
            "Recycling rates reached 64% in the metropolitan area.",
            [
                (1, "Recycling rates reached 64% in the metropolitan area.", "City Report", "2026-03-18"),
                (2, "Recycling rates reached 51% in the metropolitan area.", "Waste Bureau", "2026-04-06"),
            ],
            "disputed",
        ),
        (
            "The clinic treated 312 patients in March.",
            [
                (1, "The clinic treated 312 patients in March.", "District Health", "2026-04-02"),
                (2, "The clinic treated 288 patients in March.", "Hospital Trust", "2026-04-14"),
            ],
            "disputed",
        ),
        # --- unsupported (>= 10): the evidence is about something else
        (
            "Sourdough needs a long, cool fermentation.",
            [(1, "The Kestrel array generates 320 MW of electricity.", "Grid Weekly", "2026-02-01")],
            "unsupported",
        ),
        (
            "The novel was translated into twelve languages.",
            [(1, "Rainfall in the catchment was 620 mm last year.", "Meteorological Digest", "2026-01-08")],
            "unsupported",
        ),
        (
            "Bears hibernate through the winter.",
            [(1, "The train service runs every 15 minutes.", "Transit Report", "2026-06-12")],
            "unsupported",
        ),
        (
            "该山峰的海拔为 4,500 米。",
            [(1, "该水电站的年发电量为 32 亿千瓦时。", "能源周报", "2026-02-20")],
            "unsupported",
        ),
        (
            "Coffee beans grow near the equator.",
            [(1, "The alloy tolerates 400 MPa at room temperature.", "Materials Review", "2026-05-06")],
            "unsupported",
        ),
        (
            "The orchestra performed the symphony twice.",
            [(1, "Vaccination coverage reached 78% in the district.", "Health Bulletin", "2026-03-02")],
            "unsupported",
        ),
        (
            "Jazz originated in New Orleans.",
            [(1, "The dam holds 96 million litres.", "Water Review", "2026-02-14")],
            "unsupported",
        ),
        (
            "The volcano last erupted in 1902.",
            [(1, "The clinic treated 312 patients in March.", "District Health", "2026-04-02")],
            "unsupported",
        ),
        (
            "Paper was invented in ancient China.",
            [(1, "The satellite completed its orbit in 94 minutes.", "Orbital Notes", "2026-02-11")],
            "unsupported",
        ),
        (
            "The building has a green roof.",
            [(1, "The reactor will begin operations in March 2028.", "Energy Outlook", "2026-05-30")],
            "unsupported",
        ),
        (
            "该桥使用了不锈钢结构。",
            [(1, "Recycling rates reached 64% in the metropolitan area.", "City Report", "2026-03-18")],
            "unsupported",
        ),
        (
            "Penguins live mostly in the Southern Hemisphere.",
            [(1, "The survey covered 1,800 households.", "Methodology Page", "2026-01-30")],
            "unsupported",
        ),
        # --- supported, continued
        (
            "The Verity trial enrolled 612 participants.",
            [(1, "The Verity trial enrolled 612 participants.", "Trial Record", "2026-01-22")],
            "supported",
        ),
        (
            "Wind capacity in the region is 1.2 gigawatts.",
            [(1, "Wind capacity in the region is 1.2 gigawatts.", "Energy Review", "2026-04-08")],
            "supported",
        ),
        (
            "The alloy melts at 660 degrees Celsius.",
            [(1, "The alloy melts at 660 degrees Celsius.", "Materials Review", "2026-05-20")],
            "supported",
        ),
        (
            "The tunnel is 12.8 kilometres long.",
            [(1, "The tunnel is 12.8 kilometres long.", "Infrastructure Log", "2026-03-14")],
            "supported",
        ),
        (
            "Waste diverted from landfill reached 71%.",
            [(1, "Waste diverted from landfill reached 71%.", "City Report", "2026-02-26")],
            "supported",
        ),
        # --- contradicted, continued
        (
            "The Verity trial enrolled 612 participants.",
            [(1, "The Verity trial enrolled 418 participants.", "Trial Record", "2026-01-22")],
            "contradicted",
        ),
        (
            "Wind capacity in the region is 1.2 gigawatts.",
            [(1, "Wind capacity in the region is 0.9 gigawatts.", "Energy Review", "2026-04-08")],
            "contradicted",
        ),
        (
            "The alloy melts at 660 degrees Celsius.",
            [(1, "The alloy melts at 585 degrees Celsius.", "Materials Review", "2026-05-20")],
            "contradicted",
        ),
        (
            "The tunnel is 12.8 kilometres long.",
            [(1, "The tunnel is 15.4 kilometres long.", "Infrastructure Log", "2026-03-14")],
            "contradicted",
        ),
        (
            "Waste diverted from landfill reached 71%.",
            [(1, "Waste diverted from landfill reached 58%.", "City Report", "2026-02-26")],
            "contradicted",
        ),
        # --- disputed, continued
        (
            "The Verity trial enrolled 612 participants.",
            [
                (1, "The Verity trial enrolled 612 participants.", "Trial Record", "2026-01-22"),
                (2, "The Verity trial enrolled 418 participants.", "Audit of Trials", "2026-02-10"),
            ],
            "disputed",
        ),
        (
            "Wind capacity in the region is 1.2 gigawatts.",
            [
                (1, "Wind capacity in the region is 1.2 gigawatts.", "Energy Review", "2026-04-08"),
                (2, "Wind capacity in the region is 0.9 gigawatts.", "Grid Operator", "2026-04-25"),
            ],
            "disputed",
        ),
        (
            "The alloy melts at 660 degrees Celsius.",
            [
                (1, "The alloy melts at 660 degrees Celsius.", "Materials Review", "2026-05-20"),
                (2, "The alloy melts at 585 degrees Celsius.", "Independent Lab", "2026-06-01"),
            ],
            "disputed",
        ),
        (
            "The tunnel is 12.8 kilometres long.",
            [
                (1, "The tunnel is 12.8 kilometres long.", "Infrastructure Log", "2026-03-14"),
                (2, "The tunnel is 15.4 kilometres long.", "Engineering Review", "2026-03-30"),
            ],
            "disputed",
        ),
        (
            "Waste diverted from landfill reached 71%.",
            [
                (1, "Waste diverted from landfill reached 71%.", "City Report", "2026-02-26"),
                (2, "Waste diverted from landfill reached 58%.", "Waste Bureau", "2026-03-11"),
            ],
            "disputed",
        ),
        # --- unsupported, continued
        (
            "Rice is a staple food across much of Asia.",
            [(1, "The alloy melts at 660 degrees Celsius.", "Materials Review", "2026-05-20")],
            "unsupported",
        ),
        (
            "The novel won a prize in 1998.",
            [(1, "Wind capacity in the region is 1.2 gigawatts.", "Energy Review", "2026-04-08")],
            "unsupported",
        ),
        (
            "Bees pollinate a large share of crops.",
            [(1, "The tunnel is 12.8 kilometres long.", "Infrastructure Log", "2026-03-14")],
            "unsupported",
        ),
        (
            "该城市的地铁共有 20 条线路。",
            [(1, "该水电站的年发电量为 32 亿千瓦时。", "能源周报", "2026-02-20")],
            "unsupported",
        ),
        (
            "The treaty was signed in 1815.",
            [(1, "Waste diverted from landfill reached 71%.", "City Report", "2026-02-26")],
            "unsupported",
        ),
    ]

    out = []
    for index, (sentence, evidence, label) in enumerate(rows, start=1):
        out.append(
            {
                "id": f"verify-{index:03d}",
                "sentence": sentence,
                "evidence": [
                    {"id": eid, "text": text, "source": source, "date": date}
                    for eid, text, source, date in evidence
                ],
                "label": label,
            }
        )
    return out


def extraction_cases() -> list[dict]:
    """Passages with the gold span of each claim that should be extracted."""
    passages = [
        {
            "id": "ext-001",
            "lang": "en",
            "text": (
                "The Halcyon facility opened in 2019 and now runs three production lines. "
                "Annual output reached 480,000 units in 2025. "
                "The plant employs 1,240 people. "
                "Officials expect output to double by 2030."
            ),
            "claims": [
                "The Halcyon facility opened in 2019 and now runs three production lines.",
                "Annual output reached 480,000 units in 2025.",
                "The plant employs 1,240 people.",
                "Officials expect output to double by 2030.",
            ],
        },
        {
            "id": "ext-002",
            "lang": "en",
            "text": (
                "The Verity trial enrolled 612 participants across nine clinics. "
                "Median follow-up was 26 months. "
                "The treatment reduced hospitalisation by 18%. "
                "Side effects were reported in 7% of participants."
            ),
            "claims": [
                "The Verity trial enrolled 612 participants across nine clinics.",
                "Median follow-up was 26 months.",
                "The treatment reduced hospitalisation by 18%.",
                "Side effects were reported in 7% of participants.",
            ],
        },
        {
            "id": "ext-003",
            "lang": "en",
            "text": (
                "The coastal rail line spans 44 kilometres. "
                "It opened in 2014 after four years of construction. "
                "Daily ridership averages 31,000 passengers. "
                "The operator plans to double the frequency by 2028."
            ),
            "claims": [
                "The coastal rail line spans 44 kilometres.",
                "It opened in 2014 after four years of construction.",
                "Daily ridership averages 31,000 passengers.",
                "The operator plans to double the frequency by 2028.",
            ],
        },
        {
            "id": "ext-004",
            "lang": "en",
            "text": (
                "The alloy resists corrosion in seawater. "
                "Tensile strength reached 410 MPa. "
                "Melting point is 660 °C. "
                "Density measures 2.7 grams per cubic centimetre."
            ),
            "claims": [
                "The alloy resists corrosion in seawater.",
                "Tensile strength reached 410 MPa.",
                "Melting point is 660 °C.",
                "Density measures 2.7 grams per cubic centimetre.",
            ],
        },
        {
            "id": "ext-005",
            "lang": "en",
            "text": (
                "Solar capacity in the region reached 1.2 gigawatts. "
                "The addition supplied 480,000 homes. "
                "Curtailment rose to 6%. "
                "Storage capacity stands at 300 megawatt-hours."
            ),
            "claims": [
                "Solar capacity in the region reached 1.2 gigawatts.",
                "The addition supplied 480,000 homes.",
                "Curtailment rose to 6%.",
                "Storage capacity stands at 300 megawatt-hours.",
            ],
        },
        {
            "id": "ext-006",
            "lang": "en",
            "text": (
                "The hospital opened its new wing in March. "
                "The wing holds 240 beds. "
                "Staffing rose by 15%. "
                "The trust spent £27 million on the project."
            ),
            "claims": [
                "The hospital opened its new wing in March.",
                "The wing holds 240 beds.",
                "Staffing rose by 15%.",
                "The trust spent £27 million on the project.",
            ],
        },
        {
            "id": "ext-007",
            "lang": "en",
            "text": (
                "The tunnel is 12.8 kilometres long. "
                "Excavation finished in 2019. "
                "Peak depth reached 1,400 metres. "
                "Traffic is expected to reach 60,000 vehicles a day."
            ),
            "claims": [
                "The tunnel is 12.8 kilometres long.",
                "Excavation finished in 2019.",
                "Peak depth reached 1,400 metres.",
                "Traffic is expected to reach 60,000 vehicles a day.",
            ],
        },
        {
            "id": "ext-008",
            "lang": "en",
            "text": (
                "The vaccine was tested on 8,400 volunteers. "
                "Efficacy reached 91%. "
                "Approval came in December. "
                "Production capacity is 200 million doses a year."
            ),
            "claims": [
                "The vaccine was tested on 8,400 volunteers.",
                "Efficacy reached 91%.",
                "Approval came in December.",
                "Production capacity is 200 million doses a year.",
            ],
        },
        {
            "id": "ext-009",
            "lang": "en",
            "text": (
                "The dam supplies 1.9 million people. "
                "Annual generation is 4.2 terawatt-hours. "
                "Sedimentation reached 12 million cubic metres. "
                "Safety inspections passed last year."
            ),
            "claims": [
                "The dam supplies 1.9 million people.",
                "Annual generation is 4.2 terawatt-hours.",
                "Sedimentation reached 12 million cubic metres.",
                "Safety inspections passed last year.",
            ],
        },
        {
            "id": "ext-010",
            "lang": "en",
            "text": (
                "The grid operator balances 9,500 megawatts of demand. "
                "Storage holds 1.1 gigawatt-hours. "
                "Outages fell 22% last year. "
                "Investment of £2.4 billion is planned."
            ),
            "claims": [
                "The grid operator balances 9,500 megawatts of demand.",
                "Storage holds 1.1 gigawatt-hours.",
                "Outages fell 22% last year.",
                "Investment of £2.4 billion is planned.",
            ],
        },
        {
            "id": "ext-011",
            "lang": "zh",
            "text": (
                "该数据中心于 2021 年建成。设计容量为 30 万千瓦。"
                "该中心运行 PUE 为 1.25。"
                "年耗电量约 18 亿千瓦时。"
                "运维人员共 320 人。"
            ),
            "claims": [
                "该数据中心于 2021 年建成。",
                "设计容量为 30 万千瓦。",
                "该中心运行 PUE 为 1.25。",
                "年耗电量约 18 亿千瓦时。",
                "运维人员共 320 人。",
            ],
        },
        {
            "id": "ext-012",
            "lang": "zh",
            "text": (
                "这座大桥全长 1,180 米，主跨 620 米。"
                "大桥于 2019 年通车。"
                "日均通行量为 45,000 辆次。"
                "设计使用年限为 120 年。"
            ),
            "claims": [
                "这座大桥全长 1,180 米，主跨 620 米。",
                "大桥于 2019 年通车。",
                "日均通行量为 45,000 辆次。",
                "设计使用年限为 120 年。",
            ],
        },
        {
            "id": "ext-013",
            "lang": "zh",
            "text": (
                "该药物 III 期试验入组 2,400 例患者。"
                "主要终点达标率为 71%。"
                "中位无进展生存期为 8.4 个月。"
                "不良事件发生率为 34%。"
            ),
            "claims": [
                "该药物 III 期试验入组 2,400 例患者。",
                "主要终点达标率为 71%。",
                "中位无进展生存期为 8.4 个月。",
                "不良事件发生率为 34%。",
            ],
        },
        {
            "id": "ext-014",
            "lang": "zh",
            "text": (
                "该风电场装机容量为 480 兆瓦。年发电量为 12 亿千瓦时。可利用率达到 97%。项目于 2022 年并网。"
            ),
            "claims": [
                "该风电场装机容量为 480 兆瓦。",
                "年发电量为 12 亿千瓦时。",
                "可利用率达到 97%。",
                "项目于 2022 年并网。",
            ],
        },
        {
            "id": "ext-015",
            "lang": "zh",
            "text": (
                "该市轨道交通运营里程为 210 公里。"
                "日均客运量为 280 万人次。"
                "全线共设车站 168 座。"
                "新线计划于 2029 年开通。"
            ),
            "claims": [
                "该市轨道交通运营里程为 210 公里。",
                "日均客运量为 280 万人次。",
                "全线共设车站 168 座。",
                "新线计划于 2029 年开通。",
            ],
        },
    ]

    out = []
    for case in passages:
        spans = []
        for claim_sent in case["claims"]:
            start = case["text"].find(claim_sent)
            if start == -1:
                raise ValueError(
                    f"Claim sentence {claim_sent!r} not found in passage text {case['text']!r} ({case['id']})"
                )
            end = start + len(claim_sent)
            spans.append({"start": start, "end": end, "text": claim_sent})
        out.append(
            {
                "id": case["id"],
                "lang": case["lang"],
                "text": case["text"],
                "spans": spans,
            }
        )
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {path.relative_to(ROOT)} ({len(rows)} rows)")


def main() -> None:
    pairs = claim_pairs()
    zh = sum(1 for row in pairs if row["lang"] == "zh")
    labels = sorted({row["label"] for row in pairs})
    print(f"claim_pairs: {len(pairs)} pairs, {zh} Chinese ({zh / len(pairs):.0%}), labels={labels}")
    assert len(pairs) >= 80, len(pairs)
    assert zh / len(pairs) >= 0.20, zh / len(pairs)
    write_jsonl(DATA / "claim_pairs.dev.jsonl", pairs)

    cases = verify_cases()
    counts: dict[str, int] = {}
    for case in cases:
        counts[case["label"]] = counts.get(case["label"], 0) + 1
    print(f"verify_cases: {len(cases)} cases, {counts}")
    assert len(cases) >= 60, len(cases)
    assert all(
        counts.get(label, 0) >= 10 for label in ("supported", "contradicted", "disputed", "unsupported")
    ), counts
    write_jsonl(DATA / "verify_cases.dev.jsonl", cases)

    extractions = extraction_cases()
    print(f"extraction_cases: {len(extractions)} passages")
    assert len(extractions) >= 15, len(extractions)
    write_jsonl(DATA / "extraction_cases.dev.jsonl", extractions)


if __name__ == "__main__":
    main()

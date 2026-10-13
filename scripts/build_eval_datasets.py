"""Generate the hand-written evaluation datasets under `evals/data/`.

Written as a script rather than a static blob so every case sits next to the note
explaining *what it probes*. Regenerating is a reviewable diff.

    uv run python scripts/build_eval_datasets.py                 # dev split (regenerated freely)
    uv run python scripts/build_eval_datasets.py --split test    # held-out split, written once

The test split is hand-written here, never produced by a language model: the system under
test uses one, so a shared author would bias the benchmark. Its hashes are recorded in
`evals/data/LOCK.json`; a change to a test file must update that lock deliberately.

Rules these datasets follow:
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

import argparse
import hashlib
import json
import sys
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
        # --- identical wording -> duplicate (these were labelled supports, which
        # contradicted pair-071/072)
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
        # adds a qualifier to the same claim; different values under different
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
        # --- four distinct pairs, added so the set keeps its 80-pair floor after the
        # duplicated padding was removed
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

    # Pad with same-unit disagreements to clear the 80-pair floor. The earlier padding
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


# ---------------------------------------------------------------------------
# Held-out test split. Hand-written tuples, locked by sha256 in LOCK.json.
# Domains: health, finance, transport, climate, sport, education are the "new" fields
# the spec asks for; they are kept distinct from the energy and infrastructure
# material of the dev split.
# ---------------------------------------------------------------------------

_TEST_PAIR_ROWS: list[tuple[str, str, str, str, str, str]] = [
    # (a, b, label, lang, phenomenon, domain)
    # --- same unit, different value -> contradicts
    (
        "The cardiac trial enrolled 1,250 patients.",
        "The cardiac trial enrolled 1,520 patients.",
        "contradicts",
        "en",
        "same_unit_value",
        "health",
    ),
    ("该疫苗的保护率为 91%。", "该疫苗的保护率为 84%。", "contradicts", "zh", "same_unit_value", "health"),
    (
        "The city's annual budget reached 2.8 billion dollars.",
        "The city's annual budget reached 3.2 billion dollars.",
        "contradicts",
        "en",
        "same_unit_value",
        "finance",
    ),
    (
        "该市地铁一号线全长 38 公里。",
        "该市地铁一号线全长 45 公里。",
        "contradicts",
        "zh",
        "same_unit_value",
        "transport",
    ),
    (
        "The highway bridge carries 62,000 vehicles per day.",
        "The highway bridge carries 26,000 vehicles per day.",
        "contradicts",
        "en",
        "same_unit_value",
        "transport",
    ),
    (
        "The sea level rose 6 centimetres since 1990.",
        "The sea level rose 9 centimetres since 1990.",
        "contradicts",
        "en",
        "same_unit_value",
        "climate",
    ),
    (
        "全球平均气温较工业化前上升 1.2 摄氏度。",
        "全球平均气温较工业化前上升 1.6 摄氏度。",
        "contradicts",
        "zh",
        "same_unit_value",
        "climate",
    ),
    (
        "The stadium seats 48,000 spectators.",
        "The stadium seats 36,000 spectators.",
        "contradicts",
        "en",
        "same_unit_value",
        "sport",
    ),
    (
        "该运动员的百米最好成绩为 9.98 秒。",
        "该运动员的百米最好成绩为 10.12 秒。",
        "contradicts",
        "zh",
        "same_unit_value",
        "sport",
    ),
    (
        "The university admitted 4,800 first-year students.",
        "The university admitted 5,600 first-year students.",
        "contradicts",
        "en",
        "same_unit_value",
        "education",
    ),
    (
        "该中学高三年级共有 320 名学生。",
        "该中学高三年级共有 260 名学生。",
        "contradicts",
        "zh",
        "same_unit_value",
        "education",
    ),
    (
        "The grant covers 70% of tuition costs.",
        "The grant covers 45% of tuition costs.",
        "contradicts",
        "en",
        "same_unit_value",
        "education",
    ),
    (
        "该药品的半衰期为 6 小时。",
        "该药品的半衰期为 14 小时。",
        "contradicts",
        "zh",
        "same_unit_value",
        "health",
    ),
    # --- same quantity, different units -> supports
    (
        "The trial ran for 2 years.",
        "The trial ran for 24 months.",
        "supports",
        "en",
        "unit_conversion",
        "health",
    ),
    (
        "该药物每日剂量为 0.5 克。",
        "该药物每日剂量为 500 毫克。",
        "supports",
        "zh",
        "unit_conversion",
        "health",
    ),
    (
        "The city budget totals 1.5 billion dollars.",
        "The city budget totals 1,500 million dollars.",
        "supports",
        "en",
        "unit_conversion",
        "finance",
    ),
    (
        "该基金规模约为 2 亿元。",
        "该基金规模约为 20,000 万元。",
        "supports",
        "zh",
        "unit_conversion",
        "finance",
    ),
    (
        "The railway line is 120 kilometres long.",
        "The railway line is 120,000 metres long.",
        "supports",
        "en",
        "unit_conversion",
        "transport",
    ),
    (
        "The tunnel is 4.5 km long.",
        "The tunnel is 4,500 metres long.",
        "supports",
        "en",
        "unit_conversion",
        "transport",
    ),
    (
        "该高铁站的站台长度为 0.4 公里。",
        "该高铁站的站台长度为 400 米。",
        "supports",
        "zh",
        "unit_conversion",
        "transport",
    ),
    (
        "The country's emissions fell by 1 million tonnes last year.",
        "The country's emissions fell by 1,000,000 tonnes last year.",
        "supports",
        "en",
        "unit_conversion",
        "climate",
    ),
    (
        "该地区年降水量为 1,200 毫米。",
        "该地区年降水量为 1.2 米。",
        "supports",
        "zh",
        "unit_conversion",
        "climate",
    ),
    (
        "The marathon course is 42.2 kilometres.",
        "The marathon course is 42,200 metres.",
        "supports",
        "en",
        "unit_conversion",
        "sport",
    ),
    (
        "该体育馆的看台容量为 1.5 万人。",
        "该体育馆的看台容量为 15,000 人。",
        "supports",
        "zh",
        "unit_conversion",
        "sport",
    ),
    (
        "The campus covers 0.3 square kilometres.",
        "The campus covers 30 hectares.",
        "supports",
        "en",
        "unit_conversion",
        "education",
    ),
    (
        "该学校占地面积为 2 公顷。",
        "该学校占地面积为 20,000 平方米。",
        "supports",
        "zh",
        "unit_conversion",
        "education",
    ),
    # --- one side denies what the other asserts -> contradicts
    (
        "The vaccine reduced hospital admissions.",
        "The vaccine did not reduce hospital admissions.",
        "contradicts",
        "en",
        "negation",
        "health",
    ),
    ("该药物降低了复发率。", "该药物未降低复发率。", "contradicts", "zh", "negation", "health"),
    (
        "The bond issue raised the expected revenue.",
        "The bond issue did not raise the expected revenue.",
        "contradicts",
        "en",
        "negation",
        "finance",
    ),
    (
        "该税收政策增加了地方财政收入。",
        "该税收政策没有增加地方财政收入。",
        "contradicts",
        "zh",
        "negation",
        "finance",
    ),
    (
        "The new bypass cut commuting times.",
        "The new bypass never cut commuting times.",
        "contradicts",
        "en",
        "negation",
        "transport",
    ),
    ("该航线的准点率有所提高。", "该航线的准点率并未提高。", "contradicts", "zh", "negation", "transport"),
    (
        "The forest cover expanded after 2015.",
        "The forest cover did not expand after 2015.",
        "contradicts",
        "en",
        "negation",
        "climate",
    ),
    (
        "该地区的降雨量已经恢复正常。",
        "该地区的降雨量尚未恢复正常。",
        "contradicts",
        "zh",
        "negation",
        "climate",
    ),
    (
        "The team qualified for the final.",
        "The team did not qualify for the final.",
        "contradicts",
        "en",
        "negation",
        "sport",
    ),
    (
        "该运动员在决赛中获得了金牌。",
        "该运动员在决赛中未获得金牌。",
        "contradicts",
        "zh",
        "negation",
        "sport",
    ),
    (
        "The programme improved reading scores.",
        "The programme did not improve reading scores.",
        "contradicts",
        "en",
        "negation",
        "education",
    ),
    (
        "该课程提高了学生的通过率。",
        "该课程并未提高学生的通过率。",
        "contradicts",
        "zh",
        "negation",
        "education",
    ),
    (
        "The clinic is open on Sundays.",
        "The clinic is not open on Sundays.",
        "contradicts",
        "en",
        "negation",
        "health",
    ),
    # --- same claim in different words -> supports
    (
        "The trial recruited 880 volunteers.",
        "880 volunteers were recruited for the trial.",
        "supports",
        "en",
        "paraphrase",
        "health",
    ),
    (
        "该药物在三期试验中显示出显著疗效。",
        "该药物的三期试验疗效显著。",
        "supports",
        "zh",
        "paraphrase",
        "health",
    ),
    (
        "The council approved a budget of 40 million dollars.",
        "A 40 million dollar budget was approved by the council.",
        "supports",
        "en",
        "paraphrase",
        "finance",
    ),
    (
        "该省财政赤字规模为 180 亿元。",
        "该省的财政赤字达到 180 亿元。",
        "supports",
        "zh",
        "paraphrase",
        "finance",
    ),
    (
        "The metro line opened to passengers in 2028.",
        "Passenger service on the metro line began in 2028.",
        "supports",
        "en",
        "paraphrase",
        "transport",
    ),
    ("该公路于 2019 年通车。", "该公路在 2019 年开通运营。", "supports", "zh", "paraphrase", "transport"),
    (
        "Global sea ice cover shrank over the decade.",
        "Sea ice across the globe declined during the decade.",
        "supports",
        "en",
        "paraphrase",
        "climate",
    ),
    (
        "该地区过去十年的平均风速有所下降。",
        "该地区过去十年的平均风速呈下降趋势。",
        "supports",
        "zh",
        "paraphrase",
        "climate",
    ),
    (
        "The marathon attracted 12,000 runners.",
        "12,000 runners took part in the marathon.",
        "supports",
        "en",
        "paraphrase",
        "sport",
    ),
    (
        "该俱乐部赢得了五次联赛冠军。",
        "该俱乐部共获得 5 次联赛冠军。",
        "supports",
        "zh",
        "paraphrase",
        "sport",
    ),
    (
        "The school enrols about 900 pupils.",
        "Roughly 900 pupils attend the school.",
        "supports",
        "en",
        "paraphrase",
        "education",
    ),
    ("该大学的毕业率达到 88%。", "该大学的毕业生比例为 88%。", "supports", "zh", "paraphrase", "education"),
    (
        "The vaccine must be refrigerated at 4 degrees.",
        "Refrigeration at 4 degrees is required for the vaccine.",
        "supports",
        "en",
        "paraphrase",
        "health",
    ),
    # --- the same event, a different date, and no sign of an update -> contradicts
    (
        "The vaccine trial results will be published in March 2027.",
        "The vaccine trial results will be published in October 2027.",
        "contradicts",
        "en",
        "date_moved",
        "health",
    ),
    (
        "该医院新住院楼将于 2026 年 4 月竣工。",
        "该医院新住院楼将于 2026 年 8 月竣工。",
        "contradicts",
        "zh",
        "date_moved",
        "health",
    ),
    (
        "The regional tax review is scheduled for June 2026.",
        "The regional tax review is scheduled for December 2026.",
        "contradicts",
        "en",
        "date_moved",
        "finance",
    ),
    (
        "该市政府债券发行计划定于 2027 年 1 月进行。",
        "该市政府债券发行计划定于 2027 年 7 月进行。",
        "contradicts",
        "zh",
        "date_moved",
        "finance",
    ),
    (
        "The ring road section 3 opens in 2028.",
        "The ring road section 3 opens in 2030.",
        "contradicts",
        "en",
        "date_moved",
        "transport",
    ),
    (
        "该城际铁路支线预计 2029 年通车。",
        "该城际铁路支线预计 2031 年通车。",
        "contradicts",
        "zh",
        "date_moved",
        "transport",
    ),
    (
        "The coastal wetland restoration finishes in 2026.",
        "The coastal wetland restoration finishes in 2029.",
        "contradicts",
        "en",
        "date_moved",
        "climate",
    ),
    (
        "该流域的水土保持工程于 2025 年完工。",
        "该流域的水土保持工程于 2027 年完工。",
        "contradicts",
        "zh",
        "date_moved",
        "climate",
    ),
    (
        "The national stadium reopens in August 2026.",
        "The national stadium reopens in February 2027.",
        "contradicts",
        "en",
        "date_moved",
        "sport",
    ),
    (
        "该城市马拉松定于 2026 年 9 月举行。",
        "该城市马拉松定于 2026 年 11 月举行。",
        "contradicts",
        "zh",
        "date_moved",
        "sport",
    ),
    (
        "The new library wing opens in September 2026.",
        "The new library wing opens in January 2027.",
        "contradicts",
        "en",
        "date_moved",
        "education",
    ),
    (
        "该中学的实验楼将于 2025 年秋季投入使用。",
        "该中学的实验楼将于 2026 年春季投入使用。",
        "contradicts",
        "zh",
        "date_moved",
        "education",
    ),
    (
        "The summer reading scheme starts in April 2026.",
        "The summer reading scheme starts in July 2026.",
        "contradicts",
        "en",
        "date_moved",
        "education",
    ),
    # --- the newer statement announces a change to the older schedule -> supersedes
    (
        "The hospital wing opens in March 2027.",
        "The hospital wing opening has been postponed to October 2027.",
        "supersedes",
        "en",
        "revised_schedule",
        "health",
    ),
    (
        "该疫苗接种计划将于 2026 年 4 月启动。",
        "该疫苗接种计划已推迟至 2026 年 8 月启动。",
        "supersedes",
        "zh",
        "revised_schedule",
        "health",
    ),
    (
        "The city budget vote is set for June 2026.",
        "The city budget vote was rescheduled to December 2026.",
        "supersedes",
        "en",
        "revised_schedule",
        "finance",
    ),
    (
        "该税制改革将于 2027 年 1 月生效。",
        "该税制改革已延至 2027 年 7 月生效。",
        "supersedes",
        "zh",
        "revised_schedule",
        "finance",
    ),
    (
        "The bypass is due to open in 2028.",
        "The bypass opening was deferred to 2030.",
        "supersedes",
        "en",
        "revised_schedule",
        "transport",
    ),
    (
        "该高速公路预计 2029 年通车。",
        "该高速公路的通车时间已调整为 2031 年。",
        "supersedes",
        "zh",
        "revised_schedule",
        "transport",
    ),
    (
        "The flood defence works finish in 2026.",
        "The flood defence completion date was revised to 2029.",
        "supersedes",
        "en",
        "revised_schedule",
        "climate",
    ),
    (
        "该地区的植树计划于 2025 年完成。",
        "该地区的植树计划已推迟至 2027 年完成。",
        "supersedes",
        "zh",
        "revised_schedule",
        "climate",
    ),
    (
        "The cycling velodrome reopens in August 2026.",
        "The velodrome reopening was postponed to February 2027.",
        "supersedes",
        "en",
        "revised_schedule",
        "sport",
    ),
    (
        "该运动会将于 2026 年 9 月举办。",
        "该运动会已推迟至 2026 年 11 月举办。",
        "supersedes",
        "zh",
        "revised_schedule",
        "sport",
    ),
    (
        "The new campus opens in September 2026.",
        "The campus opening has been pushed back to January 2027.",
        "supersedes",
        "en",
        "revised_schedule",
        "education",
    ),
    (
        "该学院新楼将于 2025 年秋季投入使用。",
        "该学院新楼的启用时间已延至 2026 年春季。",
        "supersedes",
        "zh",
        "revised_schedule",
        "education",
    ),
    (
        "The reading programme starts in April 2026.",
        "The reading programme start was deferred to July 2026.",
        "supersedes",
        "en",
        "revised_schedule",
        "education",
    ),
    # --- identical or near-identical wording, same value -> duplicate
    (
        "The filter removes 99.9% of particulates.",
        "The filter removes 99.9% of particulates.",
        "duplicate",
        "en",
        "duplicate",
        "climate",
    ),
    (
        "该检测试剂盒的灵敏度为 98%。",
        "该检测试剂盒的灵敏度为 98%。",
        "duplicate",
        "zh",
        "duplicate",
        "health",
    ),
    (
        "The council meets every second Tuesday.",
        "The council meets every second Tuesday.",
        "duplicate",
        "en",
        "duplicate",
        "finance",
    ),
    (
        "该车站每日早高峰客流约为 3 万人次。",
        "该车站每日早高峰客流约为 3 万人次。",
        "duplicate",
        "zh",
        "duplicate",
        "transport",
    ),
    (
        "The league final drew 61,000 spectators.",
        "The league final drew 61,000 spectators.",
        "duplicate",
        "en",
        "duplicate",
        "sport",
    ),
    ("该学校的入学年龄为六周岁。", "该学校的入学年龄为六周岁。", "duplicate", "zh", "duplicate", "education"),
    (
        "The reserve covers 4,200 hectares.",
        "The reserve covers 4,200 hectares.",
        "duplicate",
        "en",
        "duplicate",
        "climate",
    ),
    (
        "The tunnel carries 18,000 cars a day.",
        "The tunnel carries 18,000 cars each day.",
        "duplicate",
        "en",
        "duplicate",
        "transport",
    ),
    ("该项目总投资为 6.5 亿元。", "该项目总投资 6.5 亿元。", "duplicate", "zh", "duplicate", "finance"),
    (
        "The study followed 3,000 children for five years.",
        "the study followed 3,000 children for five years",
        "duplicate",
        "en",
        "duplicate",
        "education",
    ),
    (
        "该药物的推荐剂量为每天一次。",
        "该药物的推荐剂量为每日一次。",
        "duplicate",
        "zh",
        "duplicate",
        "health",
    ),
    (
        "Vaccination coverage reached 93 percent nationwide.",
        "Vaccination coverage reached 93% nationwide.",
        "duplicate",
        "en",
        "duplicate",
        "health",
    ),
    (
        "该公园的年游客量超过 200 万人次。",
        "该公园的年游客量超过 200 万人次",
        "duplicate",
        "zh",
        "duplicate",
        "culture",
    ),
    # --- same subject and time, a different metric -> unrelated
    (
        "The hospital treated 420 patients.",
        "The hospital discharged 420 patients.",
        "unrelated",
        "en",
        "trap_different_metric",
        "health",
    ),
    (
        "该基金去年的回报率为 7%。",
        "该基金去年的管理费率为 7%。",
        "unrelated",
        "zh",
        "trap_different_metric",
        "finance",
    ),
    (
        "Tax revenue grew 5% this year.",
        "Tax compliance grew 5% this year.",
        "unrelated",
        "en",
        "trap_different_metric",
        "finance",
    ),
    (
        "该线路的客流量同比增长 8%。",
        "该线路的票价同比增长 8%。",
        "unrelated",
        "zh",
        "trap_different_metric",
        "transport",
    ),
    (
        "The airport handled 18,000 flights last year.",
        "The airport handled 18,000 tonnes of cargo last year.",
        "unrelated",
        "en",
        "trap_different_metric",
        "transport",
    ),
    (
        "The rainfall index rose 3 points.",
        "The temperature index rose 3 points.",
        "unrelated",
        "en",
        "trap_different_metric",
        "climate",
    ),
    (
        "该地区的森林覆盖率为 35%。",
        "该地区的森林损失率为 35%。",
        "unrelated",
        "zh",
        "trap_different_metric",
        "climate",
    ),
    (
        "The team's win rate was 62%.",
        "The team's ticket sales rose 62%.",
        "unrelated",
        "en",
        "trap_different_metric",
        "sport",
    ),
    (
        "The school's graduation rate reached 91%.",
        "The school's attendance rate reached 91%.",
        "unrelated",
        "en",
        "trap_different_metric",
        "education",
    ),
    (
        "该医院的床位使用率为 85%。",
        "该医院的药品采购额增长了 85%。",
        "unrelated",
        "zh",
        "trap_different_metric",
        "health",
    ),
    (
        "The reform cut the deficit by 12 billion dollars.",
        "The reform cut the debt by 12 billion dollars.",
        "unrelated",
        "en",
        "trap_different_metric",
        "finance",
    ),
    (
        "该车站的日均客流为 4 万人次。",
        "该车站的日均换乘量为 4 万人次。",
        "unrelated",
        "zh",
        "trap_different_metric",
        "transport",
    ),
    (
        "The vaccine efficacy was 94%.",
        "The vaccine coverage was 94%.",
        "unrelated",
        "en",
        "trap_different_metric",
        "health",
    ),
    # --- the same number about different subjects -> unrelated
    (
        "The ferry carries 320 passengers.",
        "The cinema seats 320 people.",
        "unrelated",
        "en",
        "trap_same_number_diff_subject",
        "transport",
    ),
    (
        "该河流的平均流量为 120 立方米每秒。",
        "该水库的设计库容为 120 亿立方米。",
        "unrelated",
        "zh",
        "trap_same_number_diff_subject",
        "climate",
    ),
    (
        "The clinic has 45 beds.",
        "The school has 45 classrooms.",
        "unrelated",
        "en",
        "trap_same_number_diff_subject",
        "health",
    ),
    (
        "该公司拥有 1,800 名员工。",
        "该博物馆收藏了 1,800 件文物。",
        "unrelated",
        "zh",
        "trap_same_number_diff_subject",
        "culture",
    ),
    (
        "The orchestra performed 96 concerts.",
        "The hospital operated 96 clinics.",
        "unrelated",
        "en",
        "trap_same_number_diff_subject",
        "culture",
    ),
    (
        "该队伍拥有 25 名球员。",
        "该学院拥有 25 个实验室。",
        "unrelated",
        "zh",
        "trap_same_number_diff_subject",
        "education",
    ),
    (
        "The reservoir stores 75 million cubic metres.",
        "The municipal fund holds 75 million dollars.",
        "unrelated",
        "en",
        "trap_same_number_diff_subject",
        "finance",
    ),
    (
        "该地块的绿化面积为 60 公顷。",
        "该医院的病床数量为 60 张。",
        "unrelated",
        "zh",
        "trap_same_number_diff_subject",
        "housing",
    ),
    (
        "The new line has 14 stations.",
        "The new course has 14 modules.",
        "unrelated",
        "en",
        "trap_same_number_diff_subject",
        "education",
    ),
    (
        "该品牌在全国有 300 家门店。",
        "该城市在本次调查中有 300 个社区。",
        "unrelated",
        "zh",
        "trap_same_number_diff_subject",
        "finance",
    ),
    (
        "The training course lasts 25 hours.",
        "The ferry crossing takes 25 minutes.",
        "unrelated",
        "en",
        "trap_same_number_diff_subject",
        "transport",
    ),
    (
        "该大桥的设计寿命为 100 年。",
        "该疫苗的保护期为 100 天。",
        "unrelated",
        "zh",
        "trap_same_number_diff_subject",
        "health",
    ),
    (
        "The stadium holds 40,000 fans.",
        "The hospital processed 40,000 forms.",
        "unrelated",
        "en",
        "trap_same_number_diff_subject",
        "sport",
    ),
    # --- different values under different conditions: not the same claim -> unrelated
    (
        "The drug lowers blood pressure by 12 mmHg in adults.",
        "The drug lowers blood pressure by 6 mmHg in children.",
        "unrelated",
        "en",
        "trap_qualifier_explains",
        "health",
    ),
    (
        "该材料在常温下的抗拉强度为 520 MPa。",
        "该材料在低温下的抗拉强度为 380 MPa。",
        "unrelated",
        "zh",
        "trap_qualifier_explains",
        "industry",
    ),
    (
        "The bridge carries 9,000 vehicles on weekdays.",
        "The bridge carries 14,000 vehicles on public holidays.",
        "unrelated",
        "en",
        "trap_qualifier_explains",
        "transport",
    ),
    (
        "该地区冬季平均气温为零下 8 摄氏度。",
        "该地区夏季平均气温为 26 摄氏度。",
        "unrelated",
        "zh",
        "trap_qualifier_explains",
        "climate",
    ),
    (
        "The team scores 2.4 goals at home.",
        "The team scores 1.1 goals away.",
        "unrelated",
        "en",
        "trap_qualifier_explains",
        "sport",
    ),
    (
        "该选手在室内赛道的最好成绩为 47.2 秒。",
        "该选手在室外赛道的最好成绩为 48.9 秒。",
        "unrelated",
        "zh",
        "trap_qualifier_explains",
        "sport",
    ),
    (
        "The tuition fee is 9,000 dollars for residents.",
        "The tuition fee is 27,000 dollars for international students.",
        "unrelated",
        "en",
        "trap_qualifier_explains",
        "education",
    ),
    (
        "该课程对本地学生的学费为每学期 3,000 元。",
        "该课程对外地学生的学费为每学期 6,500 元。",
        "unrelated",
        "zh",
        "trap_qualifier_explains",
        "education",
    ),
    (
        "The subsidy is 40% for small farms.",
        "The subsidy is 20% for large farms.",
        "unrelated",
        "en",
        "trap_qualifier_explains",
        "agriculture",
    ),
    (
        "该城市的平均车速在早高峰为 18 公里每小时。",
        "该城市的平均车速在夜间为 42 公里每小时。",
        "unrelated",
        "zh",
        "trap_qualifier_explains",
        "transport",
    ),
    (
        "The trial showed 78% response in the older group.",
        "The trial showed 52% response in the younger group.",
        "unrelated",
        "en",
        "trap_qualifier_explains",
        "health",
    ),
    (
        "该基金在牛市中的年化收益为 15%。",
        "该基金在熊市中的年化收益为负 9%。",
        "unrelated",
        "zh",
        "trap_qualifier_explains",
        "finance",
    ),
    (
        "The coastal flood height reached 2.1 metres in spring.",
        "The coastal flood height reached 1.4 metres in autumn.",
        "unrelated",
        "en",
        "trap_qualifier_explains",
        "climate",
    ),
    # --- same domain, nothing comparable -> unrelated
    (
        "The vaccine trial recruited adults in Lagos.",
        "The dental clinic opened a new paediatric wing.",
        "unrelated",
        "en",
        "unrelated_same_domain",
        "health",
    ),
    (
        "该地区的医保报销比例已经调整。",
        "该市的新冠疫苗接种点已经关闭。",
        "unrelated",
        "zh",
        "unrelated_same_domain",
        "health",
    ),
    (
        "The finance ministry published its annual report.",
        "The central bank adjusted its interest rate policy.",
        "unrelated",
        "en",
        "unrelated_same_domain",
        "finance",
    ),
    (
        "该市新设立了一项中小企业贷款贴息计划。",
        "该省的土地出让收入在第三季度有所回落。",
        "unrelated",
        "zh",
        "unrelated_same_domain",
        "finance",
    ),
    (
        "The new bus route connects the airport to the city centre.",
        "The highway toll on the eastern ring road was reviewed.",
        "unrelated",
        "en",
        "unrelated_same_domain",
        "transport",
    ),
    (
        "该城市正在试点自动驾驶出租车服务。",
        "该机场的国际货运航班数量有所增加。",
        "unrelated",
        "zh",
        "unrelated_same_domain",
        "transport",
    ),
    (
        "The heatwave forced several schools to close early.",
        "The river delta reported record sediment levels.",
        "unrelated",
        "en",
        "unrelated_same_domain",
        "climate",
    ),
    (
        "该地区近期开展了大规模的湿地监测项目。",
        "该地区的冰川融化速度引起了关注。",
        "unrelated",
        "zh",
        "unrelated_same_domain",
        "climate",
    ),
    (
        "The national team announced its squad for the tournament.",
        "The swimming federation changed its doping rules.",
        "unrelated",
        "en",
        "unrelated_same_domain",
        "sport",
    ),
    (
        "该体育场馆将举办一场青少年篮球训练营。",
        "该俱乐部宣布与一家赞助商续约。",
        "unrelated",
        "zh",
        "unrelated_same_domain",
        "sport",
    ),
    (
        "The university launched a new online master's programme.",
        "The primary school adopted a bilingual curriculum.",
        "unrelated",
        "en",
        "unrelated_same_domain",
        "education",
    ),
    (
        "该中学开设了机器人社团，吸引了许多学生报名。",
        "该学院的教师招聘会将于下月举行。",
        "unrelated",
        "zh",
        "unrelated_same_domain",
        "education",
    ),
    (
        "The clinic adopted an electronic patient record system.",
        "The ministry tightened rules on pharmacy advertising.",
        "unrelated",
        "en",
        "unrelated_same_domain",
        "health",
    ),
    # --- the second statement is the first plus a qualifier -> refines
    (
        "The programme trained 600 nurses.",
        "The programme trained 600 nurses in rural clinics.",
        "refines",
        "en",
        "qualifier_refines",
        "health",
    ),
    (
        "该基金累计投资了 42 个项目。",
        "该基金在过去三年累计投资了 42 个项目。",
        "refines",
        "zh",
        "qualifier_refines",
        "finance",
    ),
    (
        "The toll road carries 30,000 vehicles daily.",
        "The toll road carries 30,000 vehicles daily on weekdays.",
        "refines",
        "en",
        "qualifier_refines",
        "transport",
    ),
    (
        "该车站每日接送旅客约 8 万人次。",
        "该车站在工作日每日接送旅客约 8 万人次。",
        "refines",
        "zh",
        "qualifier_refines",
        "transport",
    ),
    (
        "The reservoir stores 90 million cubic metres.",
        "The reservoir stores 90 million cubic metres at full capacity.",
        "refines",
        "en",
        "qualifier_refines",
        "climate",
    ),
    (
        "该地区的年平均气温为 17 摄氏度。",
        "该地区在山区的年平均气温为 17 摄氏度。",
        "refines",
        "zh",
        "qualifier_refines",
        "climate",
    ),
    (
        "The marathon attracted 9,000 finishers.",
        "The marathon attracted 9,000 finishers in its first year.",
        "refines",
        "en",
        "qualifier_refines",
        "sport",
    ),
    (
        "该俱乐部拥有 3 座训练基地。",
        "该俱乐部在青训体系中拥有 3 座训练基地。",
        "refines",
        "zh",
        "qualifier_refines",
        "sport",
    ),
    (
        "The vaccine protects against the disease.",
        "The vaccine protects against the disease for at least two years.",
        "refines",
        "en",
        "qualifier_refines",
        "health",
    ),
    (
        "该药物可用于治疗高血压。",
        "该药物可用于治疗成年人的高血压。",
        "refines",
        "zh",
        "qualifier_refines",
        "health",
    ),
    (
        "The school offers 12 advanced courses.",
        "The school offers 12 advanced courses during the summer term.",
        "refines",
        "en",
        "qualifier_refines",
        "education",
    ),
    (
        "该大学设有 5 个研究生院。",
        "该大学在 2025 年设有 5 个研究生院。",
        "refines",
        "zh",
        "qualifier_refines",
        "education",
    ),
    (
        "The agency approved 150 new grants.",
        "The agency approved 150 new grants for small businesses.",
        "refines",
        "en",
        "qualifier_refines",
        "finance",
    ),
]

_TEST_VERIFY_ROWS: list[tuple[str, list[tuple[int, str, str, str]], str]] = [
    # --- supported: the evidence says the same thing in other words
    (
        "The Eastfield clinic serves 1,400 patients a week.",
        [
            (
                1,
                "Each week the Eastfield clinic treats about 1,400 patients.",
                "Clinic Annual Notes",
                "2026-01-12",
            )
        ],
        "supported",
    ),
    (
        "该地铁项目的造价为 86 亿元。",
        [(1, "该地铁项目的总造价为 86 亿元。", "城市交通月报", "2026-02-03")],
        "supported",
    ),
    (
        "The reservoir was completed in 2014.",
        [
            (
                1,
                "Construction of the reservoir finished in 2014, according to the agency.",
                "Water Authority Review",
                "2026-02-18",
            )
        ],
        "supported",
    ),
    (
        "该医院共有 12 个手术室。",
        [(1, "医院官网显示，该院目前设有 12 间手术室。", "医院简介", "2026-03-04")],
        "supported",
    ),
    (
        "The river has flooded twice since 2000.",
        [
            (
                1,
                "Records show two flood events on the river since the year 2000.",
                "Hydrology Digest",
                "2026-03-20",
            )
        ],
        "supported",
    ),
    (
        "The tax rate on fuel rose to 18%.",
        [(1, "Starting this year, the fuel tax rate is 18 percent.", "Finance Bulletin", "2026-04-02")],
        "supported",
    ),
    (
        "该校去年录取新生 3,600 人。",
        [(1, "去年该校共录取 3,600 名新生。", "招生办公室通报", "2026-04-15")],
        "supported",
    ),
    (
        "The new stadium holds 30,000 spectators.",
        [
            (
                1,
                "Capacity of the new stadium is 30,000 seats, the operator confirmed.",
                "Venue Operator Update",
                "2026-05-06",
            )
        ],
        "supported",
    ),
    (
        "The vaccine campaign reached 2 million children.",
        [
            (
                1,
                "The campaign has vaccinated roughly two million children so far.",
                "Health Ministry Brief",
                "2026-05-21",
            )
        ],
        "supported",
    ),
    (
        "该地区去年年均降水量为 980 毫米。",
        [(1, "据气象部门统计，该地区去年年均降水 980 毫米。", "气象服务简报", "2026-06-09")],
        "supported",
    ),
    (
        "The bridge opened to traffic in 2019.",
        [
            (
                1,
                "Traffic was first allowed on the bridge in 2019, per the transport authority.",
                "Transport Authority Log",
                "2026-06-25",
            )
        ],
        "supported",
    ),
    # --- contradicted: the evidence gives a different value or fact
    (
        "The Eastfield clinic serves 1,400 patients a week.",
        [(1, "The Eastfield clinic serves 900 patients a week.", "Clinic Annual Notes", "2026-01-12")],
        "contradicted",
    ),
    (
        "该地铁项目的造价为 86 亿元。",
        [(1, "该地铁项目的总造价为 62 亿元。", "城市交通月报", "2026-02-03")],
        "contradicted",
    ),
    (
        "The reservoir was completed in 2014.",
        [(1, "The reservoir was completed in 2017.", "Water Authority Review", "2026-02-18")],
        "contradicted",
    ),
    (
        "该医院共有 12 个手术室。",
        [(1, "该医院目前设有 8 间手术室。", "医院简介", "2026-03-04")],
        "contradicted",
    ),
    (
        "The river has flooded twice since 2000.",
        [(1, "The river has not flooded since 2000.", "Hydrology Digest", "2026-03-20")],
        "contradicted",
    ),
    (
        "The tax rate on fuel rose to 18%.",
        [(1, "The fuel tax rate was cut to 9 percent this year.", "Finance Bulletin", "2026-04-02")],
        "contradicted",
    ),
    (
        "该校去年录取新生 3,600 人。",
        [(1, "该校去年录取新生 2,900 人。", "招生办公室通报", "2026-04-15")],
        "contradicted",
    ),
    (
        "The new stadium holds 30,000 spectators.",
        [
            (
                1,
                "The new stadium holds 21,000 spectators, according to the operator.",
                "Venue Operator Update",
                "2026-05-06",
            )
        ],
        "contradicted",
    ),
    (
        "The vaccine campaign reached 2 million children.",
        [
            (
                1,
                "The vaccine campaign has reached only 300,000 children.",
                "Health Ministry Brief",
                "2026-05-21",
            )
        ],
        "contradicted",
    ),
    (
        "该地区去年年均降水量为 980 毫米。",
        [(1, "据气象部门统计，该地区去年年均降水 610 毫米。", "气象服务简报", "2026-06-09")],
        "contradicted",
    ),
    (
        "The bridge opened to traffic in 2019.",
        [(1, "The bridge did not open to traffic until 2022.", "Transport Authority Log", "2026-06-25")],
        "contradicted",
    ),
    # --- disputed: one source supports the sentence, another contradicts it
    (
        "The new metro line carries 120,000 riders a day.",
        [
            (1, "The new metro line carries 120,000 riders a day.", "Transit Weekly", "2026-03-02"),
            (2, "The new metro line carries 85,000 riders a day.", "City Transport Blog", "2026-03-09"),
        ],
        "disputed",
    ),
    (
        "该市的空气质量指数连续两年改善。",
        [
            (1, "该市空气质量指数连续两年改善。", "环境监测简报", "2026-02-14"),
            (2, "该市空气质量指数连续两年恶化。", "市民观察", "2026-02-20"),
        ],
        "disputed",
    ),
    (
        "The clinic will reopen in March.",
        [
            (1, "The clinic will reopen in March, the council says.", "Local Council Notice", "2026-01-30"),
            (2, "The clinic will not reopen before June.", "Patient Forum", "2026-02-05"),
        ],
        "disputed",
    ),
    (
        "The school enrolment fell last year.",
        [
            (1, "Enrolment at the school fell last year.", "District Report", "2026-04-01"),
            (2, "Enrolment at the school rose last year.", "Parents' Newsletter", "2026-04-08"),
        ],
        "disputed",
    ),
    (
        "该药物在老年患者中的有效率超过 70%。",
        [
            (1, "该药物在老年患者中的有效率超过 70%。", "临床研究简讯", "2026-05-11"),
            (2, "该药物在老年患者中的有效率仅为 45%。", "患者互助论坛", "2026-05-18"),
        ],
        "disputed",
    ),
    (
        "The flood barrier was finished in 2025.",
        [
            (1, "The flood barrier was finished in 2025.", "Engineering Gazette", "2026-06-02"),
            (2, "The flood barrier is still under construction.", "Resident Survey", "2026-06-10"),
        ],
        "disputed",
    ),
    (
        "该公交线路每天发车 240 班。",
        [
            (1, "该公交线路每天发车 240 班。", "公交公司公告", "2026-07-01"),
            (2, "该公交线路每天发车 160 班。", "通勤者调查", "2026-07-08"),
        ],
        "disputed",
    ),
    (
        "The grant covers the full cost of tuition.",
        [
            (1, "The grant covers the full cost of tuition.", "Education Office", "2026-08-12"),
            (2, "The grant covers only half of tuition costs.", "Student Union", "2026-08-19"),
        ],
        "disputed",
    ),
    (
        "The reserve protects 4,200 hectares of wetland.",
        [
            (1, "The reserve protects 4,200 hectares of wetland.", "Park Authority", "2026-09-03"),
            (2, "The reserve protects 2,400 hectares of wetland.", "Field Survey Notes", "2026-09-15"),
        ],
        "disputed",
    ),
    (
        "该体育馆的看台容量为 15,000 人。",
        [
            (1, "该体育馆的看台容量为 15,000 人。", "场馆管理方", "2026-10-01"),
            (2, "该体育馆的看台容量约为 9,000 人。", "赛事观察员", "2026-10-06"),
        ],
        "disputed",
    ),
    (
        "The rail fare was raised in January.",
        [
            (1, "The rail fare was raised in January.", "Rail Operator Notice", "2026-11-02"),
            (2, "The rail fare was not raised this year.", "Commuter Association", "2026-11-09"),
        ],
        "disputed",
    ),
    # --- unsupported: the evidence is about something else
    (
        "The mayor resigned in 2024.",
        [(1, "The council approved a new parking policy in 2024.", "City Gazette", "2026-01-18")],
        "unsupported",
    ),
    (
        "该公司去年在海外设立了三家工厂。",
        [(1, "该公司今年宣布扩大国内研发中心的规模。", "行业周刊", "2026-02-22")],
        "unsupported",
    ),
    (
        "The hospital employs 3,000 nurses.",
        [(1, "The hospital opened a new emergency wing in May.", "Hospital Newsletter", "2026-03-10")],
        "unsupported",
    ),
    (
        "该地区的人口在过去十年翻了一番。",
        [(1, "该地区的新建公园数量在过去五年有所增加。", "城市规划简报", "2026-04-14")],
        "unsupported",
    ),
    (
        "The lake contains a rare fish species.",
        [(1, "The lake was closed to swimmers during the summer.", "Parks Bulletin", "2026-05-05")],
        "unsupported",
    ),
    (
        "该学校的校车数量超过 20 辆。",
        [(1, "该学校在本学期推出了新的课后辅导项目。", "教育简讯", "2026-06-07")],
        "unsupported",
    ),
    (
        "The grant funded 40 apprenticeships.",
        [(1, "The agency reviewed its audit procedures in April.", "Oversight Digest", "2026-07-09")],
        "unsupported",
    ),
    (
        "该药物需要每周注射一次。",
        [(1, "该药物的包装设计在去年获得了奖项。", "医药资讯", "2026-08-03")],
        "unsupported",
    ),
    (
        "The tunnel is 6 kilometres long.",
        [(1, "The tunnel was inspected by engineers last winter.", "Engineering Notes", "2026-09-21")],
        "unsupported",
    ),
    (
        "该城市在本世纪初修建了第一条地铁线。",
        [(1, "该城市的公交票价去年保持不变。", "交通时报", "2026-10-11")],
        "unsupported",
    ),
    (
        "The festival attracted 20,000 visitors in 2025.",
        [(1, "The festival changed its ticket prices for students.", "Culture Pages", "2026-10-15")],
        "unsupported",
    ),
]

_TEST_EXTRACTION_ROWS: list[tuple[str, str, str, list[str]]] = [
    # (id, lang, passage, claim sentences; each claim must be one of split_sentences' outputs)
    (
        "ext-t01",
        "en",
        "The Redfern water plant treats 52 million litres a day. Read the full methodology on the project page. The new filters cut turbidity by 60 percent.",
        [
            "The Redfern water plant treats 52 million litres a day.",
            "The new filters cut turbidity by 60 percent.",
        ],
    ),
    (
        "ext-t02",
        "en",
        "Why did the city delay the metro? The answer lies in the budget review. Construction began in 2021 and the first line opened in 2024. Subscribe to our newsletter for updates.",
        [
            "The answer lies in the budget review.",
            "Construction began in 2021 and the first line opened in 2024.",
        ],
    ),
    (
        "ext-t03",
        "en",
        "The company reported revenue of 4.2 billion dollars in 2025. Its workforce grew to 18,500 employees. Share this article with a colleague.",
        [
            "The company reported revenue of 4.2 billion dollars in 2025.",
            "Its workforce grew to 18,500 employees.",
        ],
    ),
    (
        "ext-t04",
        "en",
        "Sign up for alerts. The bridge closed for repairs in March. Crews replaced 14 cables during the closure. What happens next? Traffic is expected to resume in the autumn.",
        [
            "The bridge closed for repairs in March.",
            "Crews replaced 14 cables during the closure.",
            "Traffic is expected to resume in the autumn.",
        ],
    ),
    (
        "ext-t05",
        "en",
        "The trial enrolled 2,100 adults across 14 sites. Participants were followed for 18 months. The primary endpoint was met in 63 percent of cases. Learn more about enrolment criteria.",
        [
            "The trial enrolled 2,100 adults across 14 sites.",
            "Participants were followed for 18 months.",
            "The primary endpoint was met in 63 percent of cases.",
        ],
    ),
    (
        "ext-t06",
        "en",
        "The programme funded 320 scholarships in 2025. Recipients came from 41 districts. Applications for next year open in May. Contact the office for eligibility rules.",
        [
            "The programme funded 320 scholarships in 2025.",
            "Recipients came from 41 districts.",
            "Applications for next year open in May.",
        ],
    ),
    (
        "ext-t07",
        "en",
        "Summer heat waves lasted an average of nine days in the region. Ocean temperatures reached a record high in August. Click here to download the data set.",
        [
            "Summer heat waves lasted an average of nine days in the region.",
            "Ocean temperatures reached a record high in August.",
        ],
    ),
    (
        "ext-t08",
        "en",
        "The grant programme supports 85 research teams. Each team receives up to 300,000 dollars over three years. Frequently asked questions appear below. Funding decisions are announced in December.",
        [
            "The grant programme supports 85 research teams.",
            "Each team receives up to 300,000 dollars over three years.",
            "Funding decisions are announced in December.",
        ],
    ),
    (
        "ext-t09",
        "zh",
        "该研究所共有研究人员 620 名。其中博士学历占比为 58%。研究经费每年约为 4.5 亿元。更多信息请访问官网。",
        ["该研究所共有研究人员 620 名。", "其中博士学历占比为 58%。", "研究经费每年约为 4.5 亿元。"],
    ),
    (
        "ext-t10",
        "zh",
        "该地区的失业率在本季度下降到 4.1%。为什么会这样？主要原因是制造业招聘增加。该数据来自统计局的最新报告。",
        ["该地区的失业率在本季度下降到 4.1%。", "主要原因是制造业招聘增加。", "该数据来自统计局的最新报告。"],
    ),
    (
        "ext-t11",
        "zh",
        "新建的水利枢纽总库容为 12 亿立方米。工程于 2023 年开工，预计 2028 年竣工。点击这里查看施工进度。",
        ["新建的水利枢纽总库容为 12 亿立方米。", "工程于 2023 年开工，预计 2028 年竣工。"],
    ),
    (
        "ext-t12",
        "zh",
        "该市公共图书馆全年接待读者 86 万人次。馆藏图书超过 120 万册。欢迎您的光临！",
        ["该市公共图书馆全年接待读者 86 万人次。", "馆藏图书超过 120 万册。"],
    ),
    (
        "ext-t13",
        "zh",
        "2025 年，该省制造业增加值增长 6.3%。高新技术企业数量达到 4,700 家。下载完整报告（PDF）。",
        ["2025 年，该省制造业增加值增长 6.3%。", "高新技术企业数量达到 4,700 家。"],
    ),
    (
        "ext-t14",
        "zh",
        "该足球俱乐部在本赛季共进球 61 个。主场上座率平均为 78%。球迷可以在线购票。",
        ["该足球俱乐部在本赛季共进球 61 个。", "主场上座率平均为 78%。"],
    ),
    (
        "ext-t15",
        "mixed",
        "该公司在 2025 年营收达到 36 亿元。The company said headcount rose to 2,300. 更多财报细节见附件。",
        ["该公司在 2025 年营收达到 36 亿元。", "The company said headcount rose to 2,300."],
    ),
    (
        "ext-t16",
        "mixed",
        "Our clinic now serves 3,000 patients. 该诊所同时开设了两个新的康复科室。Book an appointment online.",
        ["Our clinic now serves 3,000 patients.", "该诊所同时开设了两个新的康复科室。"],
    ),
]


def _test_claim_pairs() -> list[dict]:
    out = []
    for index, (a, b, label, lang, phenomenon, domain) in enumerate(_TEST_PAIR_ROWS, start=1):
        out.append(
            {
                "id": f"test-{index:03d}",
                "a": a,
                "b": b,
                "a_date": "2026-03-15",
                "b_date": "2026-06-20",
                "label": label,
                "lang": lang,
                "phenomenon": phenomenon,
                "domain": domain,
            }
        )
    return out


def _test_verify_cases() -> list[dict]:
    out = []
    for index, (sentence, evidence, label) in enumerate(_TEST_VERIFY_ROWS, start=1):
        out.append(
            {
                "id": f"vtest-{index:03d}",
                "sentence": sentence,
                "evidence": [
                    {"id": eid, "text": text, "source": source, "date": date}
                    for eid, text, source, date in evidence
                ],
                "label": label,
            }
        )
    return out


def _test_extraction_cases() -> list[dict]:
    """Gold spans come from `split_sentences` plus `str.find`; a claim that is not a sentence fails the build."""
    sys.path.insert(0, str(ROOT))
    from app.ingestion.segment import split_sentences

    out = []
    for case_id, lang, text, claims in _TEST_EXTRACTION_ROWS:
        sentences = [span.text_of(text).strip() for span in split_sentences(text)]
        spans = []
        for claim in claims:
            if claim not in sentences:
                raise ValueError(f"{case_id}: {claim!r} is not a sentence of split_sentences: {sentences}")
            start = text.find(claim)
            spans.append({"start": start, "end": start + len(claim), "text": claim})
        out.append({"id": case_id, "lang": lang, "text": text, "spans": spans})
    return out


def build_test() -> None:
    """Write the held-out split and its LOCK.json. Run once; a later change must be deliberate."""
    pairs = _test_claim_pairs()
    zh = sum(1 for row in pairs if row["lang"] == "zh")
    labels = sorted({row["label"] for row in pairs})
    new_domains = {"health", "finance", "transport", "climate", "sport", "education"}
    share_new = sum(1 for row in pairs if row["domain"] in new_domains) / len(pairs)
    print(
        f"claim_pairs.test: {len(pairs)} pairs, {zh / len(pairs):.0%} Chinese, {share_new:.0%} new domains, labels={labels}"
    )
    assert len(pairs) >= 150, len(pairs)
    assert zh / len(pairs) >= 0.30, zh / len(pairs)
    assert share_new >= 0.40, share_new
    write_jsonl(DATA / "claim_pairs.test.jsonl", pairs)

    cases = _test_verify_cases()
    counts: dict[str, int] = {}
    for case in cases:
        counts[case["label"]] = counts.get(case["label"], 0) + 1
    print(f"verify_cases.test: {len(cases)} cases, {counts}")
    assert len(cases) >= 40, len(cases)
    write_jsonl(DATA / "verify_cases.test.jsonl", cases)

    extractions = _test_extraction_cases()
    print(f"extraction_cases.test: {len(extractions)} passages")
    assert len(extractions) >= 15, len(extractions)
    write_jsonl(DATA / "extraction_cases.test.jsonl", extractions)

    lock = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(DATA.glob("*.test.jsonl"))
    }
    (DATA / "LOCK.json").write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {(DATA / 'LOCK.json').relative_to(ROOT)} ({len(lock)} files)")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {path.relative_to(ROOT)} ({len(rows)} rows)")


def build_dev() -> None:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate the evaluation datasets under evals/data/.")
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    args = parser.parse_args()
    if args.split == "test":
        build_test()
    else:
        build_dev()


if __name__ == "__main__":
    main()

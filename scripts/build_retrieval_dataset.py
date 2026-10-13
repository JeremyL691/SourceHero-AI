"""Generate `evals/data/demo_retrieval.json`.

The queries and their relevance labels are written by hand here rather than dumped
as a static blob so the intent behind each one is readable next to the data, and so
regenerating is a reviewable diff. Every query names the corpus document(s) that
should answer it; a query with more than one relevant document tests recall breadth.

Run: `uv run python scripts/build_retrieval_dataset.py`
"""

from __future__ import annotations

import json
from pathlib import Path

# (query, [relevant corpus ids], note)
# The note says what the query is probing, which is what makes a recall regression
# diagnosable rather than just a number that moved.
QUERIES: list[tuple[str, list[str], str]] = [
    # --- HX-2: energy density -------------------------------------------------
    ("HX-2 energy density", ["techwire-hx2-announce"], "the headline number"),
    ("What is the energy density of the HX-2 cell?", ["techwire-hx2-announce"], "rephrased"),
    ("450 Wh/kg", ["techwire-hx2-announce", "analyst-hx2-cost"], "bare number, two docs cite it"),
    ("energy density 380 Wh/kg independent test", ["greengrid-hx2-lab-test"], "the contradicting figure"),
    ("电池能量密度", ["zh-hx2-news"], "Chinese query"),
    ("HX-2 电池的能量密度是多少", ["zh-hx2-news"], "Chinese, natural phrasing"),
    (
        "solid state cell energy density Wh per kilogram",
        ["techwire-hx2-announce", "greengrid-hx2-lab-test"],
        "unit words",
    ),
    # --- HX-2: production schedule -------------------------------------------
    ("When does mass production of the HX-2 begin?", ["techwire-hx2-announce"], "original schedule"),
    ("HX-2 production start date", ["techwire-hx2-announce", "halcyon-press-release"], "two sources agree"),
    ("mass production postponed January 2027", ["halcyon-press-release"], "the update"),
    ("HX-2 量产时间", ["zh-hx2-news"], "Chinese"),
    ("Halcyon Cells 计划于 2026 年 9 月开始量产 HX-2", ["zh-hx2-news"], "Chinese, full sentence"),
    ("production delay ceramic separator yield", ["halcyon-press-release"], "cause of the delay"),
    # --- HX-2: cycle life ----------------------------------------------------
    ("HX-2 capacity retention after 1000 cycles", ["greengrid-hx2-lab-test"], "92% figure"),
    (
        "battery degradation charge cycles",
        ["greengrid-hx2-lab-test", "halcyon-press-release"],
        "both cite it",
    ),
    # --- HX-2: pricing --------------------------------------------------------
    ("HX-2 pack price at launch", ["techwire-hx2-announce"], "$120/kWh"),
    ("first year pack price above 150 per kWh", ["analyst-hx2-cost"], "the higher figure"),
    (
        "HX-2 cost per kWh pricing forecast",
        ["techwire-hx2-announce", "analyst-hx2-cost"],
        "both price claims",
    ),
    # --- HX-2: notes and analysis --------------------------------------------
    ("budget for HX-2 in Q4", ["note-hx2-budget"], "the user's own note"),
    ("Halcyon production MWh guidance", ["analyst-hx2-cost"], "volume assumption"),
    ("analyst view on Halcyon shares", ["analyst-hx2-cost"], "investment opinion"),
    # --- Ferro City: congestion charge ---------------------------------------
    ("congestion charge effect on downtown traffic", ["ferro-gazette-congestion"], "18% reduction"),
    ("traffic fell after congestion charge", ["ferro-council-report"], "council wording"),
    ("revised traffic reduction 11 percent", ["ferro-council-report"], "the corrected figure"),
    ("congestion charge daily cost drivers", ["ferro-gazette-congestion"], "$9 per day"),
    ("how much does the congestion charge cost per day", ["ferro-drivers-association"], "$12, disputed"),
    ("did the congestion charge reduce traffic", ["ferro-drivers-association"], "denial"),
    ("charge revenue neutral bus frequency", ["ferro-council-report"], "policy detail"),
    # --- Orchid-7 --------------------------------------------------------------
    ("Orchid-7 parameter count", ["orchid-release-blog"], "7 billion"),
    ("Orchid-7 training tokens", ["orchid-release-blog", "orchid-benchmark-review"], "2 trillion"),
    ("ReasonBench score Orchid-7", ["orchid-release-blog", "orchid-benchmark-review"], "68%"),
    ("Orchid-7 reproduction score 61 percent", ["orchid-forum-reproduction"], "the lower figure"),
    ("orchid model licence commercial use", ["orchid-release-blog"], "licence terms"),
    ("prompt template benchmark discrepancy", ["orchid-forum-reproduction"], "why reproductions differ"),
    ("mixture of experts active parameters", ["orchid-release-blog"], "architecture detail"),
    ("training mix languages Chinese", ["orchid-release-blog"], "data composition"),
]


def build() -> dict:
    return {
        "version": 1,
        "description": (
            "Retrieval queries over the demo corpus, with the corpus document ids that "
            "should answer each one. Used for the recall@5 regression baseline in "
            "M5.7 and the offline retrieval report in M12."
        ),
        "corpus": "app/demo/corpus",
        "queries": [
            {"id": f"q{i:03d}", "query": q, "relevant": rel, "note": note}
            for i, (q, rel, note) in enumerate(QUERIES, start=1)
        ],
    }


def main() -> None:
    target = Path("evals/data/demo_retrieval.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = build()
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target} with {len(payload['queries'])} queries")


if __name__ == "__main__":
    main()

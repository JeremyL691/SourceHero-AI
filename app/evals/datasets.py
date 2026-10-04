"""External datasets (SciFact) and user-feedback export.

Both are optional and network-dependent, so failures are reported and recorded rather
than raised: `make eval` must work with no network at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.evals.runner import CACHE, ROOT

SCIFACT_RETRIEVAL_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"
SCIFACT_CLAIM_URL = "https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz"
SCIFACT_MIRRORS = (
    "https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz",
    "https://huggingface.co/datasets/allenai/scifact/resolve/main/data.tar.gz",
)

NOTE = (
    "SciFact is used only in `full` mode. Its licences and terms are the dataset "
    "authors'; review them before redistributing anything derived from it."
)


def download(*, force: bool = False) -> int:
    """Fetch SciFact into `evals/cache/`. Reports what it could and could not get."""
    import httpx

    CACHE.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    retrieval_zip = CACHE / "scifact.zip"
    if force or not retrieval_zip.exists():
        try:
            response = httpx.get(SCIFACT_RETRIEVAL_URL, timeout=120.0, follow_redirects=True)
            response.raise_for_status()
            retrieval_zip.write_bytes(response.content)
            print(f"downloaded {retrieval_zip.name} ({len(response.content) / 1e6:.1f} MB)")
        except Exception as exc:  # noqa: BLE001 - offline is the normal case
            failures.append(f"retrieval corpus: {type(exc).__name__}: {exc}")
            print(f"could not download SciFact retrieval: {type(exc).__name__}: {exc}")
    else:
        print(f"{retrieval_zip.name} already cached")

    claim_bundle = CACHE / "scifact_claims.tar.gz"
    if force or not claim_bundle.exists():
        for url in SCIFACT_MIRRORS:
            try:
                response = httpx.get(url, timeout=120.0, follow_redirects=True)
                response.raise_for_status()
                claim_bundle.write_bytes(response.content)
                print(f"downloaded claim data from {url}")
                break
            except Exception as exc:  # noqa: BLE001 - try the next mirror
                failures.append(f"{url}: {type(exc).__name__}")
        else:
            print("could not download the SciFact claim data from any mirror")
    else:
        print(f"{claim_bundle.name} already cached")

    status = {"available": [], "unavailable": failures}
    (CACHE / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(f"\n{NOTE}")
    return 0


def export_feedback(out: str = "evals/data/user_labeled_pairs.jsonl") -> int:
    """Export every relation a user has labelled, as gold evaluation data.

    A human decision about whether two claims agree is the highest-quality signal this
    system can collect, so it is worth keeping in a form the evals can read.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import aliased

    from app.core.db import SessionLocal
    from app.models.claim import Claim, ClaimRelation

    target = Path(out)
    if not target.is_absolute():
        target = ROOT / target

    session = SessionLocal()
    try:
        # An alias is required: joining the same entity twice without one makes the
        # second join overwrite the first.
        other = aliased(Claim)
        rows = session.execute(
            select(ClaimRelation, Claim, other)
            .join(Claim, Claim.id == ClaimRelation.a_claim_id)
            .join(other, other.id == ClaimRelation.b_claim_id)
            .where(ClaimRelation.user_label.is_not(None))
        ).all()
    except Exception as exc:  # noqa: BLE001 - the table may not exist yet
        print(f"could not read labelled relations: {type(exc).__name__}: {exc}")
        return 1
    finally:
        session.close()

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for relation, a, b in rows:
            handle.write(
                json.dumps(
                    {
                        "relation_id": relation.id,
                        "a": a.statement,
                        "b": b.statement,
                        "a_date": a.asserted_at.isoformat(),
                        "b_date": b.asserted_at.isoformat(),
                        "label": relation.user_label,
                        "source": "user_label",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(f"wrote {target.relative_to(ROOT)} ({len(rows)} labelled pairs)")
    return 0


__all__ = ["NOTE", "SCIFACT_CLAIM_URL", "SCIFACT_RETRIEVAL_URL", "download", "export_feedback"]

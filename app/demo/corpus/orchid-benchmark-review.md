---
id: orchid-benchmark-review
title: "Reproducing the Orchid-7 benchmark numbers"
url: https://demo.sourcehero.local/lattice/orchid-benchmark-review
source: "Lattice Review"
source_type: webpage
published_at: 2026-02-14
---

Lattice Review re-ran Orchid AI's published evaluation harness on the released weights, using the same hardware configuration the company documented.

Orchid-7 scored 68% on the ReasonBench evaluation.

Our result matches Orchid's reported figure within the run-to-run variance of the harness, which we measured at about half a percentage point over five repeats.

Orchid-7 was trained on 2 trillion tokens.

The training-card figure is not independently verifiable from the released artefacts, so we report it as the company's own statement rather than an independently checked quantity.

Lattice did not reproduce the throughput figures, which appear to assume a batching configuration that was not documented.

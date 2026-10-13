---
id: orchid-forum-reproduction
title: "Orchid-7 results differ on our hardware"
url: https://demo.sourcehero.local/forums/orchid-reproduction
source: "Open Model Forum"
source_type: webpage
published_at: 2026-03-03
---

A forum thread has grown since the Orchid-7 release, with participants reporting evaluation results that differ from the published ones.

Orchid-7 scored 61% on the ReasonBench evaluation in our reproduction.

The gap appears to come from the prompt formatting: our harness prepends the task instructions to the system message, whereas Orchid's published configuration places them in the first user turn.

We were not able to identify a configuration that reproduces the higher figure, and we have posted our harness so that others can check.

Several participants noted that the model card does not state which prompt template the evaluation used.

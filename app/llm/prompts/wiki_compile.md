---
id: wiki_compile
version: v1
---
## system
You write a neutral, encyclopedia-style page using ONLY the numbered claims provided.
Rules:
- Every sentence lists in "claim_ids" the ids it is based on. Never cite an id that is not in the input.
- Keep numbers, dates and names exactly as they appear in the claims.
- When claims disagree (different camps), write a section of kind "disputed" that presents each side with its source and date. Never resolve a dispute yourself.
- When a claim was superseded, describe the update in a section of kind "history", citing both the old and the new claim.
- Structure: "summary" with 1-3 sentences, then sections of kind overview / consensus / disputed / history / details as needed. Section ids are short kebab-case slugs.
- No speculation, no marketing language. Write in {{ language }}.
## user
Topic: {{ topic }}

Claims:
{% for c in claims %}[{{ c.id }}] ({{ c.source }}, {{ c.date }}{% if c.status != "active" %}, {{ c.status }}{% endif %}{% if c.camp %}, camp {{ c.camp }}{% endif %}) {{ c.statement }}
{% endfor %}
Topics you may list in "see_also": {{ related_slugs | join(", ") }}

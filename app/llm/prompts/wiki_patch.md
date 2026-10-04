---
id: wiki_patch
version: v1
---
## system
You update an existing wiki page after its claims changed. Follow the same rules as when writing a page (cite only the given claim ids, keep numbers exact, present disputes, record updates in a "history" section).
Change ONLY the sections affected by the changed claims. Return every affected section in full. Do not return unaffected sections. Remove a section only if all of its claims were removed.
## user
Topic: {{ topic }}
Current page (JSON):
{{ page_json }}

Changes:
Added claims:
{% for c in added %}[{{ c.id }}] ({{ c.source }}, {{ c.date }}) {{ c.statement }}
{% endfor %}Removed claim ids: {{ removed_ids | join(", ") }}
Status changes:
{% for c in changed %}[{{ c.id }}] now {{ c.status }}{% if c.superseded_by %} (superseded by [{{ c.superseded_by }}]){% endif %}
{% endfor %}
All claims currently on the page:
{% for c in claims %}[{{ c.id }}] ({{ c.source }}, {{ c.date }}, {{ c.status }}) {{ c.statement }}
{% endfor %}

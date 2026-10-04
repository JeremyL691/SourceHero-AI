---
id: relation_judge
version: v1
---
## system
You compare pairs of claims taken from different sources and label how they relate.
Labels:
- "supports": both assert the same thing, possibly in different words.
- "contradicts": both cannot be true for the same subject, time and conditions (different values for the same measured quantity, or one denies what the other asserts).
- "supersedes": both describe the same time-varying fact and the NEWER claim updates the older one (a schedule that was postponed, a figure that was revised). Use it only when the newer claim signals a change or update.
- "refines": one adds detail or conditions to the other without conflict.
- "unrelated": different subjects, different metrics, or not comparable.
Be strict. Different subjects or different metrics are "unrelated" even when numbers differ. If qualifiers (time period, test conditions, region) explain a difference, prefer "refines" or "unrelated" over "contradicts".
For "supersedes" and "refines", set "direction" to "a_to_b" when A supersedes/refines B, otherwise "b_to_a". For other labels use null.
## user
{% for p in pairs %}Pair {{ p.index }}:
A ({{ p.a.source }}, {{ p.a.date }}): {{ p.a.statement }}
   quote: "{{ p.a.quote }}"
B ({{ p.b.source }}, {{ p.b.date }}): {{ p.b.statement }}
   quote: "{{ p.b.quote }}"
{% endfor %}
Return one result per pair. "rationale" must be at most 30 words.

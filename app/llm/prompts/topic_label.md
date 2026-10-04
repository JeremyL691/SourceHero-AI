---
id: topic_label
version: v1
---
## system
Name the common topic of these claims as a short noun phrase (at most 6 words, no trailing punctuation), in the claims' main language. Prefer the specific entity name when there is one.
## user
{% for c in claims %}- {{ c }}
{% endfor %}

"""Stopwords for search and verification.

Deliberately a plain module-level frozenset rather than a download: retrieval has to be
deterministic and offline. Roughly 150 English and 50 Chinese function words -- enough
that "the" and "的" stop dominating ranking, without dropping words that carry meaning
in this domain.
"""

from __future__ import annotations

_EN = """
a about above after again against all am an and any are aren't as at be because been
before being below between both but by can cannot could couldn't did didn't do does
doesn't doing don't down during each few for from further had hadn't has hasn't have
haven't having he her here hers herself him himself his how i if in into is isn't it
its itself let's me more most mustn't my myself no nor not of off on once only or
other ought our ours ourselves out over own same shan't she should shouldn't so some
such than that the their theirs them themselves then there these they this those
through to too under until up very was wasn't we were weren't what when where which
while who whom why will with won't would wouldn't you your yours yourself yourselves
said says say just also may might must shall upon us via per among within without
since although though yet still much many one two get got make made use used using
"""

_ZH = """
的 了 是 在 和 有 为 以 于 上 下 不 人 这 那 之 也 都 而 与 及 或 一个 我们 你们 他们
它们 一些 什么 怎么 因为 所以 但是 如果 这个 那个 可以 已经 还是 没有 就是 被 把
让 从 到 对 为之 以其 而且 并且 然后 因此 由于 通过 关于 对于 根据 按照 进行
"""

STOPWORDS: frozenset[str] = frozenset(w for w in (_EN.split() + _ZH.split()) if w)

__all__ = ["STOPWORDS"]

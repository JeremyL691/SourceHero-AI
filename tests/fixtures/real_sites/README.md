# Real-site snapshots

Raw response bytes recorded by `tests/test_real_sites.py` (live, `-m network`) and
checked offline by `tests/test_real_site_fixtures.py`. A snapshot is written only when
its own bytes state the licence.

| File | Source URL | Fetched (UTC) | Licence (as stated in the bytes) | Size (bytes) |
|---|---|---|---|---|
| `en-wikipedia-lithium-ion.html` | https://en.wikipedia.org/wiki/Lithium-ion_battery | 2026-10-12 ~21:40 | CC BY-SA 4.0 ("Attribution-ShareAlike 4.0 License" in footer) | 1156405 |
| `zh-wikipedia-lithium-ion-battery.html` | https://zh.wikipedia.org/wiki/锂离子电池 | 2026-10-12 ~21:40 | CC BY-SA 4.0 ("知识共享 署名-相同方式共享 4.0协议" in footer) | 287476 |
| `usgov-nist-sp800-12r1.pdf` | https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-12r1.pdf | 2026-10-12 ~21:40 | US federal government work: "not subject to copyright in the United States" (p. ii) | 1339030 |

Not saved:

- NASA news RSS (https://www.nasa.gov/news-release/feed/) and the NIH feed (403): the
  feed bytes carry no explicit licence, so no snapshot. The RSS offline test skips.
- News article (https://www.npr.org/2026/01/07/nx-s1-5667021/dietary-guidelines-rfk-jr-nutrition):
  copyrighted; its structure is asserted live only.

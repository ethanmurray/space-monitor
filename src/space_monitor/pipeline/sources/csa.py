"""Canadian Space Agency (asc-csa.gc.ca) RSS adapter.

Lunar Gateway, Canadarm, Earth observation, and a steady stream of bilateral
cooperation. Pure space focus.

Notes:
* The feed lives at ``/rss/default_eng.xml`` (the legacy ``/eng/rss-feeds/``
  path 404s).
* asc-csa.gc.ca's TLS chain is signed by the Government of Canada CA, which
  Python's bundled urllib root store doesn't include — feedparser's direct
  fetch fails with ``CERTIFICATE_VERIFY_FAILED``. The :class:`RSSSource`
  base goes through httpx (certifi-backed), so this works transparently.
"""

from ._rss import RSSSource


class CsaSource(RSSSource):
    name = "csa"
    domain = "asc-csa.gc.ca"
    feed_url = "https://www.asc-csa.gc.ca/rss/default_eng.xml"

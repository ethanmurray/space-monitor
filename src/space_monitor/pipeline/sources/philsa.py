"""Philippine Space Agency (philsa.gov.ph) RSS adapter.

PhilSA is a newer space agency (founded 2019); high partnership-seeking
posture. Pure space focus.
"""

from ._rss import RSSSource


class PhilSaSource(RSSSource):
    name = "philsa"
    domain = "philsa.gov.ph"
    feed_url = "https://philsa.gov.ph/feed/"

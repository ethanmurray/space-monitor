"""South African National Space Agency (sansa.org.za) RSS adapter.

National space agency; published mix of space and adjacent (financial, GNSS-
applications) content. Prefilter required to keep the review queue focused.
"""

from ._rss import RSSSource


class SansaSource(RSSSource):
    name = "sansa"
    domain = "sansa.org.za"
    feed_url = "https://www.sansa.org.za/feed/"
    prefilter_required = True

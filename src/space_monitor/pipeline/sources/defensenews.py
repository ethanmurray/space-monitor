"""Defense News (defensenews.com) RSS adapter.

Same posture as Breaking Defense — broad defense-industry coverage where
space stories are a minority. Prefilter required.
"""

from ._rss import RSSSource


class DefenseNewsSource(RSSSource):
    name = "defensenews"
    domain = "defensenews.com"
    feed_url = "https://www.defensenews.com/arc/outboundfeeds/rss/"
    prefilter_required = True

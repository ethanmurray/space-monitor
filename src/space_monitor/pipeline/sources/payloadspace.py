"""Payload Space (payloadspace.com) RSS adapter.

Newer US space-industry trade press; daily-cadence newsletter-style coverage
of commercial space deals, contracts, and launches. Pure space focus.
"""

from ._rss import RSSSource


class PayloadSpaceSource(RSSSource):
    name = "payloadspace"
    domain = "payloadspace.com"
    feed_url = "https://payloadspace.com/feed/"

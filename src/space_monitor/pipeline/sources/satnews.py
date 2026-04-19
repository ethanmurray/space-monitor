"""SatNews RSS adapter (https://news.satnews.com/feed)."""

from ._rss import RSSSource


class SatNewsSource(RSSSource):
    name = "satnews"
    domain = "satnews.com"
    feed_url = "https://news.satnews.com/feed"

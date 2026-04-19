"""Via Satellite (Satellite Today) RSS adapter (https://www.satellitetoday.com/feed/)."""

from ._rss import RSSSource


class SatelliteTodaySource(RSSSource):
    name = "satellitetoday"
    domain = "satellitetoday.com"
    feed_url = "https://www.satellitetoday.com/feed/"

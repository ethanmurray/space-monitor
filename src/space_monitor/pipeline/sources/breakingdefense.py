"""Breaking Defense (breakingdefense.com) RSS adapter.

Defense-industry trade press; covers space + air + cyber + land. Most articles
are non-space (Army contracts, fighter aircraft, etc.) so the prefilter is
required to keep the review queue clean.
"""

from ._rss import RSSSource


class BreakingDefenseSource(RSSSource):
    name = "breakingdefense"
    domain = "breakingdefense.com"
    feed_url = "https://breakingdefense.com/feed/"
    prefilter_required = True

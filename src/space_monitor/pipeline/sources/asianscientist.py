"""Asian Scientist RSS adapter (https://www.asianscientist.com/feed/).

Asia-Pacific science coverage; many non-space items will be tagged
is_partnership=false by the extractor.
"""

from ._rss import RSSSource


class AsianScientistSource(RSSSource):
    name = "asianscientist"
    domain = "asianscientist.com"
    feed_url = "https://www.asianscientist.com/feed/"
    prefilter_required = True

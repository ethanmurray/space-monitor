"""UK Government news Atom feed (https://www.gov.uk/).

The general news-and-communications feed covers many non-space topics; the
LLM extractor will tag the irrelevant ones is_partnership=false. A future
optimization is to pre-filter URLs by keyword before extracting.
"""

from ._rss import RSSSource


class GovUkSource(RSSSource):
    name = "govuk"
    domain = "gov.uk"
    feed_url = "https://www.gov.uk/search/news-and-communications.atom"
    prefilter_required = True

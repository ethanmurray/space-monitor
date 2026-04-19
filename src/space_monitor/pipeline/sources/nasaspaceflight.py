"""NASASpaceflight (nasaspaceflight.com / NSF) RSS adapter.

Deep-technical space journalism. Long-form coverage of launch vehicles,
agency programs, and crewed spaceflight. Pure space focus.
"""

from ._rss import RSSSource


class NasaSpaceflightSource(RSSSource):
    name = "nasaspaceflight"
    domain = "nasaspaceflight.com"
    feed_url = "https://www.nasaspaceflight.com/feed/"

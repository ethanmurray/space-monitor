"""SpacePolicyOnline (spacepolicyonline.com) RSS adapter.

US space-policy reporting. Strong coverage of congressional action, NASA
budget, and policy-side announcements that the launch-focused trade press
underweights. Pure space focus.
"""

from ._rss import RSSSource


class SpacePolicyOnlineSource(RSSSource):
    name = "spacepolicyonline"
    domain = "spacepolicyonline.com"
    feed_url = "https://spacepolicyonline.com/feed/"

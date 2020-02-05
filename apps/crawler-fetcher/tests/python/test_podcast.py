# noinspection PyProtectedMember
from crawler_fetcher.handlers.feed_podcast import (
    _get_feed_url_from_itunes_podcasts_url,
    _get_feed_url_from_google_podcasts_url,
)


def test_get_feed_url_from_itunes_podcasts_url():
    assert _get_feed_url_from_itunes_podcasts_url(None) is None
    assert _get_feed_url_from_itunes_podcasts_url('') == ''
    assert _get_feed_url_from_itunes_podcasts_url('http://www.example.com/') == 'http://www.example.com/'
    assert _get_feed_url_from_itunes_podcasts_url('totally not an URL') == 'totally not an URL'

    # Let's just kind of hope RA doesn't change their underlying feed URL
    ra_itunes_url = 'https://podcasts.apple.com/lt/podcast/ra-podcast/id129673441'
    ra_feed_url = 'https://www.residentadvisor.net/xml/podcast.xml'
    assert _get_feed_url_from_itunes_podcasts_url(ra_itunes_url) == ra_feed_url

    # Try uppercase host
    ra_itunes_url = 'https://PODCASTS.APPLE.COM/lt/podcast/ra-podcast/id129673441'
    ra_feed_url = 'https://www.residentadvisor.net/xml/podcast.xml'
    assert _get_feed_url_from_itunes_podcasts_url(ra_itunes_url) == ra_feed_url


def test_get_feed_url_from_google_podcasts_url():
    assert _get_feed_url_from_google_podcasts_url(None) is None
    assert _get_feed_url_from_google_podcasts_url('') == ''
    assert _get_feed_url_from_google_podcasts_url('http://www.example.com/') == 'http://www.example.com/'
    assert _get_feed_url_from_google_podcasts_url('totally not an URL') == 'totally not an URL'

    ra_google_url = (
        'https://podcasts.google.com/?feed=aHR0cHM6Ly93d3cucmVzaWRlbnRhZHZpc29yLm5ldC94bWwvcG9kY2FzdC54bWw&'
        'ved=0CAAQ4aUDahcKEwiot6W5hrnnAhUAAAAAHQAAAAAQAQ&hl=lt'
    )
    ra_feed_url = 'https://www.residentadvisor.net/xml/podcast.xml'
    assert _get_feed_url_from_google_podcasts_url(ra_google_url) == ra_feed_url

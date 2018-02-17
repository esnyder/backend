"""Creating, matching, and otherwise manipulating stories within topics."""

import datetime
import operator
import re
import typing

from mediawords.db import DatabaseHandler
from mediawords.tm.guess_date import guess_date, GuessDateResult
import mediawords.tm.media
from mediawords.util.log import create_logger
import mediawords.util.url

log = create_logger(__name__)

# url and title length limits necessary to fit within postgres field
_MAX_URL_LENGTH = 1024
_MAX_TITLE_LENGTH = 1024

_SPIDER_FEED_NAME = 'Spider Feed'


class McTMStoriesException(Exception):
    """Defaut exception for package."""

    pass


# Giving up on porting the extraction stuff for now because it requires porting all the way down to StoryVectors.pm.
# Will leave the extraction to the perl side Mine.pm code.
# -hal
# def queue_download_exrtaction(db: DatabaseHandler, download: dict) -> None:
#     """Queue an extraction job for the download
#
#     This just adds some checks not to re-extract and not to download obvious big binary file types.
#     """
#
#     for ext in 'jpg pdf doc mp3 mp4 zip l7 gz'.split():
#         if download['url'].endswith(ext):
#             return
#
#     if re.search(r'livejournal.com\/(tag|profile)', download['url'], flags=re.I) is not None:
#         return
#
#     dt = db.query("select 1 from download_texts where downloads_id = %(a)s", {'a': download['downloads_id']}).hash()
#     if dt is not None:
#         return
#
#     try:
#         mediawords.dbi.downloads.process_download_for_extractor(
#             db=db, download=download, use_cache=True, no_dedup_sentences=False)
#     except Exception as e:
#         log.warning("extract error processing download %d: %s" % (download['downloads_id'], str(e)))


def _get_story_with_most_sentences(db: DatabaseHandler, stories: list) -> dict:
    """Given a list of stories, return the story with the most sentences."""
    assert len(stories) > 0

    if len(stories) == 1:
        return stories[0]

    story = db.query(
        """
        select s.*
            from stories s
            where stories_id in (
                select stories_id
                    from story_sentences
                    where stories_id = any (%(a)s)
                    group by stories_id
                    order by count(*) desc
                    limit 1
            )
        """,
        {'a': [s['stories_id'] for s in stories]}).hash()

    if story is not None:
        return story
    else:
        return stories[0]


def _url_domain_matches_medium(medium: dict, urls: list) -> bool:
    """Return true if the domain of any of the story urls matches the domain of the medium url."""
    medium_domain = mediawords.util.url.get_url_distinctive_domain(medium['url'])

    story_domains = [mediawords.util.url.get_url_distinctive_domain(u) for u in urls]

    matches = list(filter(lambda d: medium_domain == d, story_domains))

    return len(matches) > 0


def get_preferred_story(db: DatabaseHandler, stories: list) -> dict:
    """Given a set of possible story matches, find the story that is likely the best to include in the topic.

    The best story is the one that first belongs to the media source that sorts first according to the following
    criteria, in descending order of importance:

    * pointed to by some dup_media_id
    * without a dup_media_id
    * url domain matches that of the story
    * lower media_id

    Within a media source, the preferred story is the one with the most sentences.

    Arguments:
    db - db handle
    url - url of matched story
    redirect_url - redirect_url of matched story
    stories - list of stories from which to choose

    Returns:
    a single preferred story

    """
    assert len(stories) > 0

    if len(stories) == 1:
        return stories[0]

    log.debug("get_preferred_story: %d stories" % len(stories))

    media = db.query(
        """
        select *,
                exists ( select 1 from media d where d.dup_media_id = m.media_id ) as is_dup_target
            from media m
            where media_id = any(%(a)s)
        """,
        {'a': [s['media_id'] for s in stories]}).hashes()

    story_urls = [s['url'] for s in stories]

    for medium in media:
        # is_dup_target defined in query above
        medium['is_dup_target'] = 0 if medium['is_dup_target'] else 1
        medium['is_not_dup_source'] = 1 if medium['dup_media_id'] else 0
        medium['matches_domain'] = 0 if _url_domain_matches_medium(medium, story_urls) else 1
        medium['stories'] = list(filter(lambda s: s['media_id'] == medium['media_id'], stories))

    sorted_media = sorted(
        media,
        key=operator.itemgetter('is_dup_target', 'is_not_dup_source', 'matches_domain', 'media_id'))

    preferred_story = _get_story_with_most_sentences(db, sorted_media[0]['stories'])

    return preferred_story


def ignore_redirect(db: DatabaseHandler, url: str, redirect_url: typing.Optional[str]) -> bool:
    """Return true if we should ignore redirects to the target media source.

    This is usually to avoid redirects to domain resellers for previously valid and important but now dead links."""
    if redirect_url is None or url == redirect_url:
        return False

    medium_url = mediawords.tm.media.generate_medium_url_and_name_from_url(redirect_url)[0]

    u = mediawords.util.url.normalize_url_lossy(medium_url)

    match = db.query("select 1 from topic_ignore_redirects where url = %(a)s", {'a': u}).hash()

    return match is not None


def get_story_match(db: DatabaseHandler, url: str, redirect_url: typing.Optional[str]=None) -> typing.Optional[dict]:
    """Search for any story within the database that matches the given url.

    Searches for any story whose guid or url matches either the url or redirect_url or the
    mediawords.util.url.normalized_url_lossy() version of either.

    If multiple stories are found, use get_preferred_story() to decide which story to return.

    Only mach the first _MAX_URL_LENGTH characters of the url / redirect_url.

    Arguments:
    db - db handle
    url - story url
    redirect_url - optional url to which the story url redirects

    Returns:
    the matched story or None

    """
    u = url[0:_MAX_URL_LENGTH]

    ru = ''
    if not ignore_redirect(db, url, redirect_url):
        ru = redirect_url[0:_MAX_URL_LENGTH] if redirect_url is not None else u

    nu = mediawords.util.url.normalize_url_lossy(u)
    nru = mediawords.util.url.normalize_url_lossy(ru)

    urls = list(set((u, ru, nu, nru)))

    # look for matching stories, ignore those in foreign_rss_links media
    stories = db.query(
        """
select distinct(s.*) from stories s
        join media m on s.media_id = m.media_id
    where
        ( ( s.url = any( %(a)s ) ) or
            ( s.guid = any ( %(a)s ) ) ) and
        m.foreign_rss_links = false

union

select distinct(s.*) from stories s
        join media m on s.media_id = m.media_id
        join topic_seed_urls csu on s.stories_id = csu.stories_id
    where
        csu.url = any ( %(a)s ) and
        m.foreign_rss_links = false
        """,
        {'a': urls}).hashes()

    story = get_preferred_story(db, stories)

    return story


def create_download_for_new_story(db: DatabaseHandler, story: dict, feed: dict) -> dict:
    """Create and return download object in database for the new story."""

    download = {
        'feeds_id': feed['feeds_id'],
        'stories_id': story['stories_id'],
        'url': story['url'],
        'host': mediawords.util.url.get_url_host(story['url']),
        'type': 'content',
        'sequence': 1,
        'state': 'success',
        'path': 'content:pending',
        'priority': 1,
        'extracted': 'f'
    }

    download = db.create('downloads', download)

    return download


def assign_date_guess_tag(
        db: DatabaseHandler,
        story: dict,
        date_guess: GuessDateResult,
        fallback_date: typing.Optional[str]) -> None:
    """Assign a guess method tag to the story based on the date_guess result.

    If date_guess found a result, assing a date_guess_method:guess_by_url, guess_by_tag_*, or guess_by_uknown tag.
    Otherwise if there is a fallback_date, assign date_guess_metehod:fallback_date.  Else assign
    date_invalid:date_invalid.

    Arguments:
    db - db handle
    story - story dict from db
    date_guess - GuessDateResult from guess_date() call

    Returns:
    None

    """
    if date_guess.found():
        tag_set = 'date_guess_method'
        guess_method = date_guess.guess_method()
        if guess_method.startswith('Extracted from url'):
            tag = 'guess_by_url'
        elif guess_method.startswith('Extracted from tag'):
            match = re.search(r'\<(\w+)', guess_method)
            html_tag = match.group(1) if match is not None else 'unknown'
            tag = 'guess_by_tag_' + str(html_tag)
        else:
            tag = 'guess_by_unknown'
    elif fallback_date is not None:
        tag_set = 'date_guess_method'
        tag = 'fallback_date'
    else:
        tag_set = 'date_invalid'
        tag = 'date_invalid'

    ts = db.find_or_create('tag_sets', {'name': tag_set})
    t = db.find_or_create('tags', {'tag': tag, 'tag_sets_id': ts['tag_sets_id']})

    db.create('stories_tags_map', {'stories_id': story['stories_id'], 'tags_id': t['tags_id']})


def get_spider_feed(db: DatabaseHandler, medium: dict) -> dict:
    """Find or create the 'Spider Feed' feed for the media source."""
    feed = db.query(
        """
        select * from feeds
            where
                media_id = %(a)s and
                url = %(b)s and
                name = %(c)s
        """,
        {'a': medium['media_id'], 'b': medium['url'], 'c': _SPIDER_FEED_NAME}).hash()

    if feed is not None:
        return feed

    feed = {'media_id': medium['media_id'], 'url': medium['url'], 'name': _SPIDER_FEED_NAME, 'feed_status': 'inactive'}

    return db.create('feeds', feed)


def add_new_story(
        db: DatabaseHandler,
        url: str,
        content: str,
        fallback_date: typing.Optional[datetime.datetime]=None) -> dict:
    """Add a new story to the database by guessing metadata using the given url and content.


    This function guesses the medium, feed, title, and date of the story from the url and content.

    Arguments:
    db - db handle
    url - story url
    content - story content
    fallback_date - fallback to this date if the date guesser fails to find a date
    """

    url = url[0:_MAX_URL_LENGTH]

    medium = mediawords.tm.media.guess_medium(db, url)
    feed = get_spider_feed(db, medium)
    spidered_tag = mediawords.tm.media.get_spidered_tag(db)
    title = mediawords.util.html.html_title(content, url, _MAX_TITLE_LENGTH)

    story = {
        'url': url,
        'guid': url,
        'media_id': medium['media_id'],
        'collect_date': datetime.datetime.now(),
        'title': title,
        'description': ''
    }

    # postgres refuses to insert text values with the null character
    for field in ('url', 'guid', 'title'):
        story[field] = re.sub('\x00', '', story[field])

    date_guess = guess_date(url, content)
    story['publish_date'] = date_guess.date() if date_guess.found() else fallback_date
    if story['publish_date'] is None:
        story['publish_date'] = datetime.datetime.now().isoformat()

    story = db.create('stories', story)

    db.create('stories_tags_map', {'stories_id': story['stories_id'], 'tags_id': spidered_tag['tags_id']})

    assign_date_guess_tag(db, story, date_guess, fallback_date)

    log.debug("add story: %s; %s; %s; %d" % (story['title'], story['url'], story['publish_date'], story['stories_id']))

    db.create('feeds_stories_map', {'stories_id': story['stories_id'], 'feeds_id': feed['feeds_id']})

    download = create_download_for_new_story(db, story, feed)

    download = mediawords.dbi.downloads.store_content(db, download, content)

    return story
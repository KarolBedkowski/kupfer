from __future__ import annotations

__kupfer_name__ = _("Firefox Keywords")
__kupfer_sources__ = ("KeywordsSource",)
__kupfer_text_sources__ = ("KeywordSearchSource",)
__kupfer_actions__ = ("SearchWithEngine",)
__description__ = _("Search the web with Firefox keywords")
__version__ = "2020.1"
__author__ = ""

import sqlite3
import time
import typing as ty
from contextlib import closing
from urllib.parse import quote, urlparse

from kupfer import plugin_support, utils
from kupfer.obj import (
    Action,
    Leaf,
    OpenUrl,
    RunnableLeaf,
    Source,
    TextLeaf,
    TextSource,
)
from kupfer.obj.apps import AppLeafContentMixin
from kupfer.obj.helplib import FilesystemWatchMixin

from ._firefox_support import get_ffdb_conn_str, get_firefox_home_file

__kupfer_settings__ = plugin_support.PluginSettings(
    {
        "key": "default",
        "label": _("Default for ?"),
        "type": str,
        "value": "https://www.google.com/search?ie=UTF-8&q=%s",
    },
    {
        "key": "profile",
        "label": _("Firefox profile name or path"),
        "type": str,
        "value": "",
    },
)

if ty.TYPE_CHECKING:
    _ = str


def _url_domain(text: str) -> str:
    components = list(urlparse(text))
    domain = "".join(components[1:2])
    return domain


class Keyword(Leaf):
    def __init__(self, title, kw, url):
        title = title or _url_domain(url)
        name = f"{kw} ({title})"
        super().__init__(url, name)
        self.keyword = kw
        self.is_search = "%s" in self.object

    def get_actions(self):
        if self.is_search:
            yield SearchFor()
        else:
            yield OpenUrl()

    def get_description(self):
        return self.object

    def get_icon_name(self):
        return "text-html"

    def get_text_representation(self):
        return self.object


_KEYWORDS_SQL = """
SELECT distinct moz_places.url, moz_places.title, moz_keywords.keyword
FROM moz_places, moz_keywords
WHERE moz_places.id = moz_keywords.place_id
"""


class KeywordsSource(AppLeafContentMixin, Source, FilesystemWatchMixin):
    appleaf_content_id = ("firefox", "firefox-esr")

    instance: KeywordsSource = None  # type: ignore

    def __init__(self):
        self.monitor_token = None
        super().__init__(_("Firefox Keywords"))

    def initialize(self):
        KeywordsSource.instance = self
        profile = __kupfer_settings__["profile"]
        if ff_home := get_firefox_home_file("", profile):
            self.monitor_token = self.monitor_directories(str(ff_home))

    def finalize(self):
        KeywordsSource.instance = None  # type: ignore

    def monitor_include_file(self, gfile):
        return gfile and gfile.get_basename() == "lock"

    def get_items(self):
        """Query the firefox places bookmark database"""
        fpath = get_ffdb_conn_str(
            __kupfer_settings__["profile"], "places.sqlite"
        )
        if not fpath:
            return []

        for _ in range(2):
            try:
                self.output_debug("Reading bookmarks from", fpath)
                with closing(
                    sqlite3.connect(fpath, uri=True, timeout=1)
                ) as conn:
                    cur = conn.cursor()
                    cur.execute(_KEYWORDS_SQL)
                    return [Keyword(title, kw, url) for url, title, kw in cur]
            except sqlite3.Error as err:
                self.output_debug("Read bookmarks error:", str(err))
                # Something is wrong with the database
                # wait short time and try again
                time.sleep(1)

        self.output_exc()
        return []

    def get_description(self):
        return None

    def get_icon_name(self):
        return "web-browser"

    def provides(self):
        yield Keyword


class SearchWithEngine(Action):
    """TextLeaf -> SearchWithEngine -> Keyword"""

    action_accelerator = "s"

    def __init__(self):
        Action.__init__(self, _("Search With..."))

    def activate(self, leaf, iobj=None, ctx=None):
        assert iobj
        url = iobj.object
        _do_search_engine(leaf.object, url)

    def item_types(self):
        yield TextLeaf

    def requires_object(self):
        return True

    def object_types(self):
        yield Keyword

    def valid_object(self, obj, for_item):
        return obj.is_search

    def object_source(self, for_item=None):
        return KeywordsSource()

    def get_description(self):
        return _("Search the web with Firefox keywords")

    def get_icon_name(self):
        return "edit-find"


class SearchFor(Action):
    """Keyword -> SearchFor -> TextLeaf

    This is the opposite action to SearchWithEngine
    """

    action_accelerator = "s"

    def __init__(self):
        Action.__init__(self, _("Search For..."))

    def activate(self, leaf, iobj=None, ctx=None):
        assert iobj
        url = leaf.object
        terms = iobj.object
        _do_search_engine(terms, url)

    def item_types(self):
        yield Keyword

    def requires_object(self):
        return True

    def object_types(self):
        yield TextLeaf

    def object_source(self, for_item=None):
        return TextSource(placeholder=_("Search Terms"))

    def valid_object(self, obj, for_item):
        # NOTE: Using exact class to skip subclasses
        return type(obj) == TextLeaf  # pylint: disable=unidiomatic-typecheck

    def get_description(self):
        return _("Search the web with Firefox keywords")

    def get_icon_name(self):
        return "edit-find"


class KeywordSearchSource(TextSource):
    """This source allow search using firefox keywords.

    To use - i.e. define bookmarks for "https://hn.algolia.com/?q=%s" with keyword
    "hn" and then you can search by '?hn something to search'

    """

    def __init__(self):
        super().__init__(_("Firefox Keywords (?-source)"))

    def get_text_items(self, text):
        if not text.startswith("?"):
            return

        parts = text[1:].split(maxsplit=1)
        if len(parts) < 1:
            return

        query = parts[1] if len(parts) > 1 else ""
        for keyword in KeywordsSource.instance.get_leaves():
            assert isinstance(keyword, Keyword)
            if keyword.is_search and keyword.keyword == parts[0]:
                yield SearchWithKeyword(keyword, query)
                return

        if default := __kupfer_settings__["default"].strip():
            if "%s" not in default:
                default += "%s"

            yield SearchWithKeyword(Keyword(None, "", default), text[1:])

    def get_description(self):
        return None

    def get_icon_name(self):
        return "web-browser"

    def provides(self):
        yield SearchWithKeyword

    def get_rank(self):
        return 80


class SearchWithKeyword(RunnableLeaf):
    def __init__(self, keyword: Keyword, text: str):
        super().__init__((keyword, text), _('Search for "%s"') % (text,))

    def run(self, ctx=None):
        kwl = self.keyword_leaf
        _do_search_engine(self.query, kwl.object)

    @property
    def keyword_leaf(self) -> Keyword:
        return self.object[0]  # type: ignore

    @property
    def query(self) -> str:
        return self.object[1]  # type: ignore

    def get_icon_name(self):
        return "web-browser"

    def get_description(self):
        return _("Search using %s") % self.keyword_leaf

    def get_text_representation(self):
        kwl = self.keyword_leaf
        return _query_url(self.query, kwl.object)


def _do_search_engine(
    terms: str, search_url: str, encoding: str = "UTF-8"
) -> None:
    """Show an url searching for @search_url with @terms"""
    utils.show_url(_query_url(terms, search_url))


def _query_url(terms: str, search_url: str) -> str:
    """Show an url searching for @search_url with @terms"""
    query_url = search_url.replace("%s", quote(terms))
    return query_url

__kupfer_name__ = _("Search the Web")
__kupfer_sources__ = ("OpenSearchSource", )
__kupfer_text_sources__ = ()
__kupfer_actions__ = (
        "SearchFor",
        "SearchWithEngine",
    )
__description__ = _("Search the web with OpenSearch search engines")
__version__ = "2020-04-19"
__author__ = "Ulrik Sverdrup <ulrik.sverdrup@gmail.com>"

import locale
import os
import urllib.parse
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from kupfer.objects import Action, Source, Leaf
from kupfer.objects import TextLeaf
from kupfer import utils, config

from kupfer.plugin import firefox


def _noescape_urlencode(items):
    """Assemble an url param string from @items, without
    using any url encoding.
    """
    return "?" + "&".join(f"{n}={v}" for n, v in items)


def _urlencode(word):
    """Urlencode a single string of bytes @word"""
    return urllib.parse.urlencode({"q": word})[2:]


def _do_search_engine(terms, search_url, encoding="UTF-8"):
    """Show an url searching for @search_url with @terms"""
    query_url = search_url.replace("{searchTerms}", _urlencode(terms))
    utils.show_url(query_url)


class SearchWithEngine (Action):
    """TextLeaf -> SearchWithEngine -> SearchEngine"""
    def __init__(self):
        Action.__init__(self, _("Search With..."))

    def activate(self, leaf, iobj):
        coding = iobj.object.get("InputEncoding")
        url = iobj.object["Url"]
        _do_search_engine(leaf.object, url, encoding=coding)

    def item_types(self):
        yield TextLeaf

    def requires_object(self):
        return True

    def object_types(self):
        yield SearchEngine

    def object_source(self, for_item=None):
        return OpenSearchSource()

    def get_description(self):
        return _("Search the web with OpenSearch search engines")

    def get_icon_name(self):
        return "edit-find"


class SearchFor (Action):
    """SearchEngine -> SearchFor -> TextLeaf

    This is the opposite action to SearchWithEngine
    """
    def __init__(self):
        Action.__init__(self, _("Search For..."))

    def activate(self, leaf, iobj):
        coding = leaf.object.get("InputEncoding")
        url = leaf.object["Url"]
        terms = iobj.object
        _do_search_engine(terms, url, encoding=coding)

    def item_types(self):
        yield SearchEngine

    def requires_object(self):
        return True

    def object_types(self):
        yield TextLeaf

    def get_description(self):
        return _("Search the web with OpenSearch search engines")

    def get_icon_name(self):
        return "edit-find"


class SearchEngine (Leaf):
    def get_description(self):
        desc = self.object.get("Description")
        return desc if desc != str(self) else None

    def get_icon_name(self):
        return "text-html"


def coroutine(func):
    """Coroutine decorator: Start the coroutine"""
    def startcr(*ar, **kw):
        cr = func(*ar, **kw)
        next(cr)
        return cr
    return startcr


class OpenSearchParseError (Exception):
    pass


def gettagname(tag):
    return tag.rsplit("}", 1)[-1]


class OpenSearchSource (Source):
    def __init__(self):
        Source.__init__(self, _("Search Engines"))

    @coroutine
    def _parse_opensearch(self, target):
        """This is a coroutine to parse OpenSearch files"""
        vital_keys = {"Url", "ShortName"}
        keys = {"Description", "Url", "ShortName", "InputEncoding"}
        roots = ('OpenSearchDescription', 'SearchPlugin')

        def parse_etree(etree, name=None):
            if not gettagname(etree.getroot().tag) in roots:
                raise OpenSearchParseError(f"Search {name} has wrong type")
            search = {}
            for child in etree.getroot():
                tagname = gettagname(child.tag)
                if tagname not in keys:
                    continue
                # Only pick up Url tags with type="text/html"
                if tagname == "Url":
                    if (child.get("type") == "text/html" and
                            child.get("template")):
                        text = child.get("template")
                        params = {}
                        for ch in child:
                            if gettagname(ch.tag) == "Param":
                                params[ch.get("name")] = ch.get("value")
                        if params:
                            text += _noescape_urlencode(list(params.items()))
                    else:
                        continue
                else:
                    text = (child.text or "").strip()
                search[tagname] = text
            if not vital_keys.issubset(list(search.keys())):
                raise OpenSearchParseError(f"Search {name} missing keys")
            return search

        while True:
            try:
                path = (yield)
                etree = ElementTree.parse(path)
                target.send(parse_etree(etree, name=path))
            except Exception as exc:
                self.output_debug(f"{type(exc).__name__}: {exc}")

    def get_items(self):
        plugin_dirs = []

        # accept in kupfer data dirs
        plugin_dirs.extend(config.get_data_dirs("searchplugins"))

        # firefox in home directory
        ffx_home = firefox.get_firefox_home_file("searchplugins")
        if ffx_home and os.path.isdir(ffx_home):
            plugin_dirs.append(ffx_home)

        plugin_dirs.extend(config.get_data_dirs("searchplugins",
                                                package="firefox"))
        plugin_dirs.extend(config.get_data_dirs("searchplugins",
                                                package="iceweasel"))

        addon_dir = "/usr/lib/firefox-addons/searchplugins"
        cur_lang, _ignored = locale.getlocale(locale.LC_MESSAGES)
        suffixes = ["en-US"]
        if cur_lang:
            suffixes = [cur_lang.replace("_", "-"), cur_lang[:2]] + suffixes
        for suffix in suffixes:
            addon_lang_dir = os.path.join(addon_dir, suffix)
            if Path(addon_lang_dir).exists():
                plugin_dirs.append(addon_lang_dir)
                break

        # debian iceweasel
        if Path("/etc/iceweasel/searchplugins/common").is_dir():
            plugin_dirs.append("/etc/iceweasel/searchplugins/common")
        for suffix in suffixes:
            addon_dir = Path("/etc/iceweasel/searchplugins/locale", suffix)
            if addon_dir.is_dir():
                plugin_dirs.append(str(addon_dir))

        # try to find all versions of firefox
        for prefix in ('/usr/lib', '/usr/share'):
            for dirname in os.listdir(prefix):
                if dirname.startswith("firefox") or \
                        dirname.startswith("iceweasel"):
                    addon_dir = os.path.join(prefix, dirname,
                                             "searchplugins")
                    if os.path.isdir(addon_dir):
                        plugin_dirs.append(addon_dir)

                    addon_dir = os.path.join(prefix, dirname,
                                             "distribution", "searchplugins",
                                             "common")
                    if os.path.isdir(addon_dir):
                        plugin_dirs.append(addon_dir)

        self.output_debug("Found following searchplugins directories",
                          sep="\n", *plugin_dirs)

        @coroutine
        def collect(seq):
            """Collect items in list @seq"""
            while True:
                seq.append((yield))

        searches = []
        collector = collect(searches)
        parser = self._parse_opensearch(collector)
        # files are unique by filename to allow override
        visited_files = set()
        for pdir in plugin_dirs:
            try:
                for f in os.listdir(pdir):
                    if f in visited_files:
                        continue
                    fpath = os.path.join(pdir, f)
                    if os.path.isdir(fpath):
                        continue
                    parser.send(fpath)
                    visited_files.add(f)
            except OSError as exc:
                self.output_error(exc)

        for s in searches:
            yield SearchEngine(s, s["ShortName"])

    def should_sort_lexically(self):
        return True

    def provides(self):
        yield SearchEngine

    def get_icon_name(self):
        return "applications-internet"

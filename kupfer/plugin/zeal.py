"""
This is a Zeal search plugin.
"""

from __future__ import annotations

__kupfer_name__ = _("Zeal Search")
__kupfer_sources__ = ("ZealDocsetsSource",)
__kupfer_actions__ = ("ZealSearch", "ZealSearchInDocset", "ZealSearchFor")
__description__ = _(
    "Search in Zeal, offline documentation browser for software developers."
)
__version__ = "1.1"
__author__ = "Karol Będkowski"

import json
import os
import typing as ty
from pathlib import Path

from kupfer import icons, launch, plugin_support
from kupfer.obj import Action, Leaf, Source, TextLeaf
from kupfer.obj.apps import AppLeafContentMixin
from kupfer.obj.helplib import FilesystemWatchMixin, NonpersistentToken

if ty.TYPE_CHECKING:
    from gettext import gettext as _

plugin_support.check_command_available("zeal")


class ZealSearch(Action):
    def __init__(self):
        Action.__init__(self, _("Zeal Search"))

    def activate(self, leaf, iobj=None, ctx=None):
        launch.spawn_async(["zeal", leaf.object])

    def get_description(self):
        return _("Search in Zeal")

    def get_gicon(self):
        return icons.ComposedIcon("zeal", "edit-find")

    def get_icon_name(self):
        return "zeal"

    def item_types(self):
        yield TextLeaf


class ZealDocsetsSource(AppLeafContentMixin, Source, FilesystemWatchMixin):
    appleaf_content_id = ("zeal", "org.zealdocs.zeal")
    source_scan_interval: int = 3600

    def __init__(self):
        self.docsets_home = os.path.expanduser(
            "~/.local/share/Zeal/Zeal/docsets"
        )
        super().__init__(_("Zeal Docsets"))
        self.monitor_token = None

    def initialize(self):
        self.monitor_token = self.monitor_directories(self.docsets_home)

    def monitor_include_file(self, gfile):
        return gfile and gfile.get_basename().endswith(".docset")

    def get_items(self):
        docsets_home = Path(self.docsets_home)
        if not docsets_home.is_dir():
            return

        with os.scandir(docsets_home) as docdirs:
            for docdir in docdirs:
                if not docdir.is_dir() or not docdir.name.endswith(".docset"):
                    continue

                meta_file = Path(docdir.path, "meta.json")
                if not meta_file.is_file():
                    continue

                try:
                    with meta_file.open("r", encoding="UTF-8") as meta:
                        content = json.load(meta)
                except OSError:
                    continue

                docset_dir = Path(docdir.path)
                name = content.get("name") or docset_dir.stem
                title = content.get("title") or name.replace("_", " ")
                # zeal require prefix without any _/' '. this may cause
                # finding in wrong docsets if prefix is the same (ie java_se17,
                # java_se19) but we can't do anything with this
                name = name.partition("_")[0]  # take part before "_"
                keywords = None
                extra = content.get("extra")
                if extra:
                    keywords = extra.get("keywords")

                icon_filename = None
                if (icon := docset_dir.joinpath("icon@2x.png")).is_file():
                    icon_filename = str(icon)

                yield ZealDocset(name, title, keywords, icon_filename)

    def get_icon_name(self):
        return "zeal"

    def provides(self):
        yield ZealDocset

    def should_sort_lexically(self):
        return True


class ZealDocset(Leaf):
    def __init__(self, name, title, keywords, icon):
        super().__init__(name, title)
        self.icon = icon
        self._icon: NonpersistentToken[icons.ComposedIcon] | None = None
        if keywords:
            for alias in keywords:
                self.kupfer_add_alias(alias)

    def get_description(self):
        return _("Zeal %s Docset") % self.name

    def get_gicon(self):
        """Because of we read gicon from file, cache whole icon."""
        if self._icon:
            return self._icon.data

        icon = None
        if self.icon:
            try:
                icon = icons.ComposedIcon(
                    "zeal", icons.get_gicon_from_file(self.icon)
                )
                self._icon = NonpersistentToken(icon)
            except Exception:
                # do not try load icon again
                self.icon = None
            else:
                return icon

        # this is cached in icons
        return icons.ComposedIcon("zeal", "emblem-documents")

    def get_icon_name(self):
        return "zeal"


class ZealSearchInDocset(Action):
    """TextLeaf -> ZealSearchInDocset -> ZealDocset"""

    def __init__(self):
        Action.__init__(self, _("Search In Zeal docset..."))

    def activate(self, leaf, iobj=None, ctx=None):
        assert iobj
        docset = iobj.object
        terms = leaf.object
        launch.spawn_async(["zeal", docset + ":" + terms])

    def item_types(self):
        yield TextLeaf

    def requires_object(self):
        return True

    def object_types(self):
        yield ZealDocset

    def object_source(self, for_item=None):
        return ZealDocsetsSource()

    def get_description(self):
        return _("Search in Zeal docsets")

    def get_gicon(self):
        return icons.ComposedIcon("zeal", "edit-find")

    def get_icon_name(self):
        return "zeal"


class ZealSearchFor(Action):
    """ZealDocset -> ZealSearchFor -> TextLeaf

    This is the opposite action to ZealSearchInDocset
    """

    def __init__(self):
        Action.__init__(self, _("Search For..."))

    def activate(self, leaf, iobj=None, ctx=None):
        assert iobj
        docset = leaf.object
        terms = iobj.object
        launch.spawn_async(["zeal", docset + ":" + terms])

    def item_types(self):
        yield ZealDocset

    def requires_object(self):
        return True

    def object_types(self):
        yield TextLeaf

    def get_description(self):
        return _("Search in Zeal docsets")

    def get_gicon(self):
        return icons.ComposedIcon("zeal", "edit-find")

    def get_icon_name(self):
        return "zeal"

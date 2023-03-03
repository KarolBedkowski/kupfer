from __future__ import annotations

__kupfer_name__ = _("Documents")
__kupfer_sources__ = (
    "RecentsSource",
    "PlacesSource",
    "IgnoredApps",
)
__kupfer_actions__ = ("Toggle",)
__kupfer_contents__ = ("ApplicationRecentsSource",)
__description__ = _("Recently used documents and bookmarked folders")
__version__ = "2017.3"
__author__ = ""

import functools
import operator
import typing as ty
from os import path
from pathlib import Path

import xdg.BaseDirectory as base
from gi.repository import Gio, Gtk

from kupfer import icons, launch, plugin_support
from kupfer.obj import (
    Action,
    AppLeaf,
    FileLeaf,
    Leaf,
    Source,
    SourceLeaf,
    UrlLeaf,
)
from kupfer.support import weaklib

__kupfer_settings__ = plugin_support.PluginSettings(
    {
        "key": "max_days",
        "label": _("Max recent document days"),
        "type": int,
        "value": 28,
    },
    {
        "key": "check_doc_exist",
        "label": _("Show only existing documents"),
        "type": bool,
        "value": True,
    },
)

ALIASES = {
    "libreoffice": "soffice",
}

# Libreoffice doesn't separate them out, so we'll hack that in manually
SEPARATE_APPS = {
    "libreoffice": {
        ".doc": "libreoffice-writer",
        ".docx": "libreoffice-writer",
        ".odt": "libreoffice-writer",
        ".ods": "libreoffice-calc",
        ".xlsx": "libreoffice-calc",
        ".csv": "libreoffice-calc",
        ".odp": "libreoffice-impress",
        ".ppt": "libreoffice-impress",
        ".pptx": "libreoffice-impress",
        ".odg": "libreoffice-draw",
        ".odf": "libreoffice-math",
        ".mml": "libreoffice-math",
    }
}


def _file_path(uri: str) -> Gio.File | None:
    try:
        return Gio.File.new_for_uri(uri).get_path()
    except Exception:
        return None


@functools.lru_cache(maxsize=1)
def _get(max_days):
    manager = Gtk.RecentManager.get_default()
    items = manager.get_items()
    item_leaves = []
    check_doc_exist = __kupfer_settings__["check_doc_exist"]
    for item in items:
        if item.get_age() > max_days >= 0:
            continue

        if check_doc_exist and not item.exists():
            continue

        if item.get_private_hint():
            continue

        if not item.is_local():
            continue

        uri = item.get_uri()
        if file_path := _file_path(uri):
            apps = item.get_applications()
            apps_name = [
                _first_word(item.get_application_info(a)[0]) for a in apps
            ] + [a.lower() for a in apps]
            item_leaves.append((file_path, item.get_modified(), apps_name))

    # sort by modified date
    item_leaves.sort(key=operator.itemgetter(1), reverse=True)
    return item_leaves


def _first_word(instr: str) -> str:
    return instr.split(None, 1)[0]


def _get_items(
    max_days: int, for_app_names: tuple[str, ...] | None = None
) -> ty.Iterator[FileLeaf]:
    """
    for_app_names: set of candidate app names, or None.
    """

    for file_path, _modified, apps in _get(max_days):
        if for_app_names:
            if not any(a in for_app_names for a in apps):
                continue

            ext = path.splitext(file_path)[1].lower()
            if any(
                sort_table.get(ext) not in for_app_names
                for app_id, sort_table in SEPARATE_APPS.items()
                if app_id in for_app_names
            ):
                continue

        yield FileLeaf(file_path)


class RecentsSource(Source):
    def __init__(self, name=None):
        super().__init__(name or _("Recent Items"))

    def initialize(self):
        """Set up change callback"""
        manager = Gtk.RecentManager.get_default()
        weaklib.gobject_connect_weakly(manager, "changed", self._recent_changed)

    def _recent_changed(self, *args):
        # FIXME: We don't get single item updates, might this be
        # too many updates?
        _get.cache_clear()
        self.mark_for_update()

    def get_items(self):
        max_days = __kupfer_settings__["max_days"]
        return _get_items(max_days)

    def get_description(self):
        return _("Recently used documents")

    def get_icon_name(self):
        return "document-open-recent"

    def provides(self):
        yield FileLeaf
        yield UrlLeaf


class ApplicationRecentsSource(RecentsSource):
    def __init__(self, application):
        # TRANS: Recent Documents for application %s
        name = _("%s Documents") % str(application)
        super().__init__(name)
        self.application = application

    def repr_key(self):
        return self.application.repr_key()

    def get_items(self):
        app_names = self.app_names(self.application)
        max_days = __kupfer_settings__["max_days"]
        self.output_debug("Items for", app_names)
        return _get_items(max_days, app_names)

    # Cache doesn't need to be large to serve main purpose:
    # there will be many identical queries in a row
    @staticmethod
    @functools.lru_cache(maxsize=10)
    def has_items_for_application(names):
        max_days = __kupfer_settings__["max_days"]
        for _item in _get_items(max_days, names):
            return True

        return False

    def get_gicon(self):
        return icons.ComposedIcon(
            self.get_icon_name(), self.application.get_icon()
        )

    def get_description(self):
        return _("Recently used documents for %s") % str(self.application)

    @classmethod
    def decorates_type(cls):
        return AppLeaf

    @classmethod
    def decorate_item(cls, leaf: AppLeaf) -> ApplicationRecentsSource | None:
        if IgnoredApps.contains(leaf):
            return None

        app_names = cls.app_names(leaf)
        if cls.has_items_for_application(app_names):
            return cls(leaf)

        return None

    @classmethod
    def app_names(cls, leaf: AppLeaf) -> tuple[str, ...]:
        "Return a frozenset of names"
        # in most cases, there are only 2-3 items, so there is not need to
        # built set
        svc = launch.get_applications_matcher_service()

        leaf_id = leaf.get_id()
        ids = [leaf_id]

        if (exe := leaf.object.get_executable()) != leaf_id:
            ids.append(exe)

        if app_name := svc.application_name(leaf_id):
            if (app_name := app_name.lower()) != leaf_id:
                ids.append(app_name)

        ids.extend(v for k, v in ALIASES.items() if k in ids)
        return tuple(ids)


class PlacesSource(Source):
    """
    Source for items from gtk bookmarks
    """

    def __init__(self):
        super().__init__(_("Places"))
        self.places_file = None
        self._version = 2

    def initialize(self):
        self.places_file = path.join(
            base.xdg_config_home, "gtk-3.0", "bookmarks"
        )

    def get_items(self):
        """
        gtk-bookmarks: each line has url and optional title
        file:///path/to/that.end [title]
        """
        assert self.places_file
        if Path(self.places_file).exists():
            return self._get_places(self.places_file)

        return ()

    def _get_places(self, fileloc):
        with open(fileloc, encoding="UTF-8") as fin:
            for line in fin:
                if not line.strip():
                    continue

                items = line.split(None, 1)
                uri = items[0]
                gfile = Gio.File.new_for_uri(uri)
                if len(items) > 1:
                    title = items[1].strip()
                else:
                    disp = gfile.get_parse_name()
                    title = path.basename(disp)

                if locpath := gfile.get_path():
                    yield FileLeaf(locpath, title)
                else:
                    yield UrlLeaf(gfile.get_uri(), title)

    def get_description(self):
        return _("Bookmarked folders")

    def get_icon_name(self):
        return "system-file-manager"

    def provides(self):
        yield FileLeaf
        yield UrlLeaf


class IgnoredApps(Source):
    # This Source is invisibile and has no content
    # It exists just to store (through the config mechanism) the list of apps
    # we ignore for recent documents content decoration
    instance: IgnoredApps = None  # type:ignore

    def __init__(self):
        super().__init__(_("Toggle Recent Documents"))
        # apps is a mapping: app id (str) -> empty dict
        self.apps = {}

    def config_save(self):
        return self.apps

    def config_save_name(self):
        return __name__

    def config_restore(self, state):
        self.apps = state

    def initialize(self):
        IgnoredApps.instance = self

    def finalize(self):
        del IgnoredApps.instance

    def get_items(self):
        return []

    def provides(self):
        return ()

    @classmethod
    def add(cls, app_leaf):
        assert cls.instance
        cls.instance.apps[app_leaf.get_id()] = {}
        # FIXME: Semi-hack to refresh the content
        cls.instance.mark_for_update()

    @classmethod
    def remove(cls, app_leaf):
        assert cls.instance
        cls.instance.apps.pop(app_leaf.get_id(), None)
        cls.instance.mark_for_update()

    @classmethod
    def contains(cls, app_leaf):
        assert cls.instance
        return app_leaf.get_id() in cls.instance.apps

    def get_leaf_repr(self):
        return InvisibleSourceLeaf(self)


class Toggle(Action):
    rank_adjust = -5

    def __init__(self):
        super().__init__(_("Toggle Recent Documents"))

    def item_types(self):
        yield AppLeaf

    def valid_for_item(self, leaf):
        if IgnoredApps.contains(leaf):
            return True

        app_names = ApplicationRecentsSource.app_names(leaf)
        return ApplicationRecentsSource.has_items_for_application(app_names)

    def has_result(self):
        return True

    def activate(self, leaf, iobj=None, ctx=None):
        if IgnoredApps.contains(leaf):
            IgnoredApps.remove(leaf)
        else:
            IgnoredApps.add(leaf)
        # Neat trick: We return the leaf, and that updates the decoration
        # pylint: disable=protected-access
        leaf._content_source = None
        return leaf

    def get_description(self):
        return _(
            "Enable/disable listing recent documents in content for this application"
        )


class InvisibleSourceLeaf(SourceLeaf):
    """Hack to hide this source"""

    def is_valid(self):
        return False

#! /usr/bin/env python3
# Distributed under terms of the GPLv3 license.

"""
File - related sources
"""

from __future__ import annotations

import os
import typing as ty
from contextlib import suppress
from os import path

from gi.repository import GdkPixbuf, Gio, GLib

from kupfer import icons, utils

from . import apps, files
from .base import Leaf, Source
from .exceptions import InvalidDataError
from .helplib import FilesystemWatchMixin

if ty.TYPE_CHECKING:
    _ = str


def construct_file_leaf(obj: str) -> Leaf:
    """
    If the path in @obj points to a Desktop Item file,
    return an AppLeaf, otherwise return a FileLeaf
    """
    if obj.endswith(".desktop"):
        with suppress(InvalidDataError):
            return apps.AppLeaf(init_path=obj)

    return files.FileLeaf(obj)


class DirectorySource(Source, FilesystemWatchMixin):
    def __init__(
        self,
        directory: str,
        show_hidden: bool = False,
        *,
        toplevel: bool = False,
    ) -> None:
        # Use glib filename reading to make display name out of filenames
        # this function returns a `unicode` object
        # TODO: need use glib?
        name = GLib.filename_display_basename(directory)
        super().__init__(name)
        self._directory = directory
        self._show_hidden = show_hidden
        self._toplevel = toplevel
        self.monitor: ty.Any = None

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__module__}.{self.__class__.__name__}"
            f'("{self._directory}", show_hidden={self._show_hidden})'
        )

    def initialize(self) -> None:
        # only toplevel directories are active monitored
        if self._toplevel:
            self.monitor = self.monitor_directories(self._directory)

    def finalize(self) -> None:
        if self.monitor:
            self.stop_monitor_directories(self.monitor)
            self.monitor = None

    def monitor_include_file(self, gfile: Gio.File) -> bool:
        return self._show_hidden or not gfile.get_basename().startswith(".")

    def get_items(self) -> ty.Iterator[Leaf]:
        try:
            files: ty.Iterable[str] = os.listdir(self._directory)
        except OSError as exc:
            self.output_error(exc)
        else:
            if not self._show_hidden:
                files = (f for f in files if f[0] != ".")

            yield from (
                construct_file_leaf(path.join(self._directory, fname))
                for fname in files
            )

    def should_sort_lexically(self) -> bool:
        return True

    def _parent_path(self) -> str:
        return path.normpath(path.join(self._directory, path.pardir))

    def has_parent(self) -> bool:
        return not path.samefile(self._directory, self._parent_path())

    def get_parent(self) -> ty.Optional[DirectorySource]:
        if not self.has_parent():
            return None

        return DirectorySource(self._parent_path())

    def get_description(self) -> str:
        return _("Directory source %s") % self._directory

    def get_gicon(self) -> GdkPixbuf.Pixbuf | None:
        return icons.get_gicon_for_file(self._directory)

    def get_icon_name(self) -> str:
        return "folder"

    def get_leaf_repr(self) -> ty.Optional[Leaf]:
        alias = None
        if os.path.isdir(self._directory) and os.path.samefile(
            self._directory, os.path.expanduser("~")
        ):
            alias = _("Home Folder")

        return files.FileLeaf(self._directory, alias=alias)

    def provides(self) -> ty.Iterable[ty.Type[Leaf]]:
        yield files.FileLeaf
        yield apps.AppLeaf


def _representable_fname(fname: str) -> bool:
    "Return False if fname contains surrogate escapes"
    # all string are utf so this is unnecessary
    try:
        fname.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


class FileSource(Source):
    def __init__(self, dirlist: ty.List[str], depth: int = 0) -> None:
        """
        @dirlist: Directories as byte strings
        """
        name = GLib.filename_display_basename(dirlist[0])
        if len(dirlist) > 1:
            name = _("%s et. al.") % name

        super().__init__(name)
        self.dirlist = dirlist
        self.depth = depth

    def __repr__(self) -> str:
        mod = self.__class__.__module__
        cname = self.__class__.__name__
        dirs = ", ".join(f'"{d}"' for d in sorted(self.dirlist))
        return f"{mod}.{cname}(({dirs}, ), depth={self.depth})"

    def get_items(self) -> ty.Iterable[Leaf]:
        for directory in self.dirlist:
            files = utils.get_dirlist(
                directory, max_depth=self.depth, exclude=self._exclude_file
            )
            yield from map(construct_file_leaf, files)

    def should_sort_lexically(self) -> bool:
        return True

    def _exclude_file(self, filename: str) -> bool:
        return filename.startswith(".")

    def get_description(self) -> str:
        return _("Recursive source of %(dir)s, (%(levels)d levels)") % {
            "dir": self.name,
            "levels": self.depth,
        }

    def get_icon_name(self) -> str:
        return "folder-saved-search"

    def provides(self) -> ty.Iterator[ty.Type[Leaf]]:
        yield files.FileLeaf
        yield apps.AppLeaf

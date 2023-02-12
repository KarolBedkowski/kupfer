from __future__ import annotations
import os
from os import path
import typing as ty

from gi.repository import GLib
from gi.repository import Gio
from gi.repository import GdkPixbuf

from kupfer.support import datatools
from kupfer import icons
from kupfer import utils

from kupfer.obj.base import Source, Leaf
from kupfer.obj.helplib import PicklingHelperMixin, FilesystemWatchMixin
from kupfer.obj.objects import FileLeaf, SourceLeaf
from kupfer.obj.objects import ConstructFileLeaf, ConstructFileLeafTypes

if ty.TYPE_CHECKING:
    _ = str


def _representable_fname(fname: str) -> bool:
    "Return False if fname contains surrogate escapes"
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
            files = list(
                utils.get_dirlist(
                    directory, max_depth=self.depth, exclude=self._exclude_file
                )
            )
            yield from map(ConstructFileLeaf, files)

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
        return ConstructFileLeafTypes()


class DirectorySource(Source, PicklingHelperMixin, FilesystemWatchMixin):
    def __init__(self, directory: str, show_hidden: bool = False) -> None:
        # Use glib filename reading to make display name out of filenames
        # this function returns a `unicode` object
        name = GLib.filename_display_basename(directory)
        super().__init__(name)
        self.directory = directory
        self.show_hidden = show_hidden
        self.monitor = None

    def __repr__(self) -> str:
        mod = self.__class__.__module__
        cname = self.__class__.__name__
        return f'{mod}.{cname}("{self.directory}", show_hidden={self.show_hidden})'

    def initialize(self) -> None:
        self.monitor = self.monitor_directories(self.directory)

    def finalize(self) -> None:
        self.monitor = None

    def monitor_include_file(self, gfile: Gio.File) -> bool:
        return self.show_hidden or not gfile.get_basename().startswith(".")

    def get_items(self) -> ty.Iterator[Leaf]:
        try:
            for fname in os.listdir(self.directory):
                if not _representable_fname(fname):
                    continue

                if self.show_hidden or not fname.startswith("."):
                    yield ConstructFileLeaf(path.join(self.directory, fname))

        except OSError as exc:
            self.output_error(exc)

    def should_sort_lexically(self) -> bool:
        return True

    def _parent_path(self) -> str:
        return path.normpath(path.join(self.directory, path.pardir))

    def has_parent(self) -> bool:
        return not path.samefile(self.directory, self._parent_path())

    def get_parent(self) -> ty.Optional[DirectorySource]:
        if not self.has_parent():
            return None

        return DirectorySource(self._parent_path())

    def get_description(self) -> str:
        return _("Directory source %s") % self.directory

    def get_gicon(self) -> GdkPixbuf.Pixbuf | None:
        return icons.get_gicon_for_file(self.directory)

    def get_icon_name(self) -> str:
        return "folder"

    def get_leaf_repr(self) -> ty.Optional[Leaf]:
        alias = None
        if os.path.isdir(self.directory) and os.path.samefile(
            self.directory, os.path.expanduser("~")
        ):
            alias = _("Home Folder")

        return FileLeaf(self.directory, alias=alias)

    def provides(self) -> ty.Iterable[ty.Type[Leaf]]:
        return ConstructFileLeafTypes()


class SourcesSource(Source):
    """A source whose items are SourceLeaves for @source"""

    def __init__(
        self,
        sources: ty.Collection[Source],
        name: ty.Optional[str] = None,
        use_reprs: bool = True,
    ) -> None:
        super().__init__(name or _("Catalog Index"))
        self.sources = sources
        self.use_reprs = use_reprs

    def get_items(self) -> ty.Iterable[Leaf]:
        """Ask each Source for a Leaf substitute, else
        yield a SourceLeaf"""
        for src in self.sources:
            yield (self.use_reprs and src.get_leaf_repr()) or SourceLeaf(src)

    def should_sort_lexically(self) -> bool:
        return True

    def get_description(self) -> str:
        return _("An index of all available sources")

    def get_icon_name(self) -> str:
        return "kupfer-catalog"


class MultiSource(Source):
    """
    A source whose items are the combined items
    of all @sources
    """

    fallback_icon_name = "kupfer-catalog"

    def __init__(self, sources: ty.Collection[Source]) -> None:
        super().__init__(_("Catalog"))
        self.sources = sources

    def is_dynamic(self) -> bool:
        """
        MultiSource should be dynamic so some of its content
        also can be
        """
        return True

    def get_items(self) -> ty.Iterable[Leaf]:
        uniq_srcs = datatools.UniqueIterator(
            S.toplevel_source() for S in self.sources
        )
        for src in uniq_srcs:
            yield from src.get_leaves()

    def get_description(self) -> str:
        return _("Root catalog")

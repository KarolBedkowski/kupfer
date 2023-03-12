#! /usr/bin/env python3

"""
File-related objects
"""
from __future__ import annotations

import os
import typing as ty
from os import path

from gi.repository import GdkPixbuf, Gio, GLib

from kupfer import icons, utils
from kupfer.support import pretty

from . import fileactions, filesrc
from .base import Action, Leaf, Source
from .exceptions import InvalidDataError
from .representation import TextRepresentation

if ty.TYPE_CHECKING:
    _ = str


class FileLeaf(Leaf, TextRepresentation):
    """
    Represents one file: the represented object is a string.
    """

    serializable: int | None = 1

    def __init__(
        self,
        obj: str,
        name: ty.Optional[str] = None,
        alias: ty.Optional[str] = None,
    ) -> None:
        """Construct a FileLeaf

        The display name of the file is normally derived from the full path,
        and @name should normally be left unspecified.

        @obj: byte string (file system encoding)
        @name: unicode name or None for using basename
        """
        if obj is None:
            raise InvalidDataError(f"File path for {name} may not be None")
        # Use glib filename reading to make display name out of filenames
        # this function returns a `unicode` object
        if not name:
            name = GLib.filename_display_basename(obj)

        assert name
        super().__init__(obj, name)
        if alias:
            self.kupfer_add_alias(alias)

    @classmethod
    def from_uri(cls, uri: str) -> ty.Optional[FileLeaf]:
        """
        Construct a FileLeaf

        uri: A local uri

        Return FileLeaf if it is supported, else None
        """
        gfile = Gio.File.new_for_uri(uri)
        fpath = gfile.get_path()
        if fpath:
            return cls(fpath)

        return None

    def __hash__(self) -> int:
        return hash(str(self))

    def __eq__(self, other: ty.Any) -> bool:
        try:
            return (
                type(self) is type(other)
                and str(self) == str(other)
                and path.samefile(self.object, other.object)
            )
        except OSError as exc:
            pretty.print_debug(__name__, exc)
            return False

    def repr_key(self) -> ty.Any:
        return self.object

    def canonical_path(self) -> str:
        """Return the true path of the File (without symlinks)"""
        return path.realpath(self.object)  # type: ignore

    def is_valid(self) -> bool:
        return os.access(self.object, os.R_OK)

    def is_writable(self) -> bool:
        return os.access(self.object, os.W_OK)

    def is_executable(self) -> bool:
        return os.access(self.object, os.R_OK | os.X_OK)

    def is_dir(self) -> bool:
        return path.isdir(self.object)

    def get_text_representation(self) -> str:
        return GLib.filename_display_name(self.object)  # type: ignore

    def get_urilist_representation(self) -> ty.List[str]:
        return [self.get_gfile().get_uri()]

    def get_gfile(self) -> Gio.File:
        """
        Return a Gio.File of self
        """
        return Gio.File.new_for_path(self.object)

    def get_description(self) -> ty.Optional[str]:
        return utils.get_display_path_for_bytestring(self.canonical_path())

    def get_actions(self) -> ty.Iterable[Action]:
        yield fileactions.Open()
        yield fileactions.GetParent()

        if self.is_dir():
            yield fileactions.OpenTerminal()

        elif self.is_valid():
            if self._is_good_executable():
                yield fileactions.Execute()
                yield fileactions.Execute(in_terminal=True)

    def has_content(self) -> bool:
        return self.is_dir() or Leaf.has_content(self)

    def content_source(self, alternate: bool = False) -> Source | None:
        if self.is_dir():
            return filesrc.DirectorySource(self.object, show_hidden=alternate)

        return Leaf.content_source(self)

    def get_thumbnail(self, width: int, height: int) -> GdkPixbuf.Pixbuf | None:
        if self.is_dir():
            return None

        return icons.get_thumbnail_for_gfile(self.get_gfile(), width, height)

    def get_gicon(self) -> GdkPixbuf.Pixbuf | None:
        return icons.get_gicon_for_file(self.object)

    def get_icon_name(self) -> str:
        if self.is_dir():
            return "folder"

        return "text-x-generic"

    def get_content_type(self) -> ty.Optional[str]:
        ret, uncertain = Gio.content_type_guess(self.object, None)
        if not uncertain:
            return ret  # type: ignore

        content_attr = Gio.FILE_ATTRIBUTE_STANDARD_CONTENT_TYPE
        gfile = self.get_gfile()
        if not gfile.query_exists(None):
            return None

        info = gfile.query_info(content_attr, Gio.FileQueryInfoFlags.NONE, None)
        content_type = info.get_attribute_string(content_attr)
        return content_type  # type: ignore

    def is_content_type(self, ctype: str) -> bool:
        """
        Return True if this file is of the type ctype

        ctype: A mime type, can have wildcards like 'image/*'
        """
        predicate = Gio.content_type_is_a
        ctype_guess, uncertain = Gio.content_type_guess(self.object, None)
        ret = predicate(ctype_guess, ctype)
        if ret or not uncertain:
            return ret  # type: ignore

        content_attr = Gio.FILE_ATTRIBUTE_STANDARD_CONTENT_TYPE
        gfile = self.get_gfile()
        if not gfile.query_exists(None):
            return False

        info = gfile.query_info(content_attr, Gio.FileQueryInfoFlags.NONE, None)
        content_type = info.get_attribute_string(content_attr)
        return predicate(content_type, ctype)  # type: ignore

    def _is_good_executable(self):
        if not self.is_executable():
            return False

        ctype, uncertain = Gio.content_type_guess(self.object, None)
        return uncertain or Gio.content_type_can_be_executable(ctype)

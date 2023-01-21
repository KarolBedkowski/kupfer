"""
Copyright 2007--2009 Ulrik Sverdrup <ulrik.sverdrup@gmail.com>

This file is a part of the program kupfer, which is
released under GNU General Public License v3 (or any later version),
see the main program file, and COPYING for details.
"""
from __future__ import annotations

import os
from os import path
import zlib
from contextlib import suppress
import typing as ty

from gi.repository import GLib, Gio
from gi.repository import GdkPixbuf

from kupfer import icons, launch, utils
from kupfer import pretty
from kupfer.obj.base import Leaf, Action, Source
from kupfer.obj.base import InvalidDataError, OperationError
from kupfer.obj import fileactions
from kupfer.interface import TextRepresentation
from kupfer.kupferstring import tounicode
from kupfer.version import DESKTOP_ID


def ConstructFileLeafTypes():
    """Return a seq of the Leaf types returned by ConstructFileLeaf"""
    yield FileLeaf
    yield AppLeaf


def ConstructFileLeaf(obj: str) -> Leaf:
    """
    If the path in @obj points to a Desktop Item file,
    return an AppLeaf, otherwise return a FileLeaf
    """
    _root, ext = path.splitext(obj)
    if ext == ".desktop":
        with suppress(InvalidDataError):
            return AppLeaf(init_path=obj)

    return FileLeaf(obj)


def _directory_content(dirpath: str, show_hidden: bool) -> Source:
    from kupfer.obj.sources import DirectorySource

    return DirectorySource(dirpath, show_hidden)


def _as_gfile(file_path: str) -> Gio.File:
    return Gio.File.new_for_path(file_path)


def _display_name(g_file: Gio.File) -> str:
    info = g_file.query_info(
        Gio.FILE_ATTRIBUTE_STANDARD_DISPLAY_NAME,
        Gio.FileQueryInfoFlags.NONE,
        None,
    )
    return info.get_attribute_string(Gio.FILE_ATTRIBUTE_STANDARD_DISPLAY_NAME)  # type: ignore


class FileLeaf(Leaf, TextRepresentation):
    """
    Represents one file: the represented object is a bytestring (important!)
    """

    serializable: int = 1

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
            unicode_path = tounicode(obj)
            name = GLib.filename_display_basename(unicode_path)

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

    def __eq__(self, other) -> bool:
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
        return path.realpath(self.object)

    def is_valid(self) -> bool:
        return os.access(self.object, os.R_OK)

    def _is_executable(self) -> bool:
        return os.access(self.object, os.R_OK | os.X_OK)

    def is_dir(self) -> bool:
        return path.isdir(self.object)

    def get_text_representation(self) -> str:
        return GLib.filename_display_name(self.object)

    def get_urilist_representation(self) -> ty.List[str]:
        return [self.get_gfile().get_uri()]

    def get_gfile(self) -> Gio.File:
        """
        Return a Gio.File of self
        """
        return _as_gfile(self.object)

    def get_description(self) -> str:
        return utils.get_display_path_for_bytestring(self.canonical_path())

    def get_actions(self) -> ty.Iterable[Action]:
        return fileactions.get_actions_for_file(self)

    def has_content(self) -> bool:
        return self.is_dir() or Leaf.has_content(self)

    def content_source(self, alternate: bool = False) -> Source:
        if self.is_dir():
            return _directory_content(self.object, alternate)

        return Leaf.content_source(self)

    def get_thumbnail(self, width, height) -> ty.Optional[GdkPixbuf]:
        if self.is_dir():
            return None

        return icons.get_thumbnail_for_gfile(self.get_gfile(), width, height)

    def get_gicon(self) -> ty.Optional[GdkPixbuf]:
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

        info = gfile.query_info(
            content_attr, Gio.FileQueryInfoFlags.NONE, None
        )
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

        info = gfile.query_info(
            content_attr, Gio.FileQueryInfoFlags.NONE, None
        )
        content_type = info.get_attribute_string(content_attr)
        return predicate(content_type, ctype)  # type: ignore


class SourceLeaf(Leaf):
    def __init__(self, obj: Source, name: ty.Optional[str] = None) -> None:
        """Create SourceLeaf for source @obj"""
        Leaf.__init__(self, obj, name or str(obj))

    def has_content(self) -> bool:
        return True

    def repr_key(self) -> str:
        return repr(self.object)

    def content_source(self, alternate: bool = False) -> Source:
        return self.object  # type: ignore

    def get_description(self) -> str:
        return self.object.get_description()  # type: ignore

    # FIXME: property vs class field
    @property
    def fallback_icon_name(self) -> str:
        return self.object.fallback_icon_name  # type: ignore

    def get_gicon(self) -> ty.Optional[GdkPixbuf]:
        return self.object.get_gicon()

    def get_icon_name(self) -> str:
        return self.object.get_icon_name()  # type: ignore


class AppLeaf(Leaf):
    def __init__(
        self,
        item: ty.Any = None,
        init_path: ty.Optional[str] = None,
        app_id: ty.Optional[str] = None,
        require_x: bool = True,
    ) -> None:
        """Try constructing an Application for GAppInfo @item,
        for file @path or for package name @app_id.

        @require_x: require executable file
        """
        self.init_item = item
        self.init_path = init_path
        self.init_item_id = app_id and app_id + ".desktop"
        # finish will raise InvalidDataError on invalid item
        self.finish(require_x)
        Leaf.__init__(self, self.object, self.object.get_name())
        self._add_aliases()

    def _add_aliases(self) -> None:
        # find suitable alias
        # use package name: non-extension part of ID
        lowername = str(self).lower()
        package_name = self._get_package_name()
        if package_name and package_name not in lowername:
            self.kupfer_add_alias(package_name)

    def __hash__(self) -> int:
        return hash(str(self))

    def __eq__(self, other: ty.Any) -> bool:
        return (
            isinstance(other, type(self)) and self.get_id() == other.get_id()
        )

    def __getstate__(self) -> ty.Dict[str, ty.Any]:
        self.init_item_id = self.object and self.object.get_id()
        state = dict(vars(self))
        state["object"] = None
        state["init_item"] = None
        return state

    def __setstate__(self, state: ty.Dict[str, ty.Any]) -> None:
        vars(self).update(state)
        self.finish()

    def finish(self, require_x: bool = False) -> None:
        """Try to set self.object from init's parameters"""
        item = None
        if self.init_item:
            item = self.init_item
        else:
            # Construct an AppInfo item from either path or item_id
            try:
                if self.init_path and (
                    not require_x or os.access(self.init_path, os.X_OK)
                ):
                    # serilizable if created from a "loose file"
                    self.serializable = 1
                    item = Gio.DesktopAppInfo.new_from_filename(self.init_path)
                elif self.init_item_id:
                    item = Gio.DesktopAppInfo.new(self.init_item_id)

            except TypeError:
                pretty.print_debug(
                    __name__,
                    "Application not found:",
                    self.init_item_id,
                    self.init_path,
                )
                raise InvalidDataError

        self.object = item
        if not self.object:
            raise InvalidDataError

    def repr_key(self) -> ty.Any:
        return self.get_id()

    def _get_package_name(self) -> str:
        return GLib.filename_display_basename(self.get_id())

    def launch(
        self,
        files: ty.Iterable[str] = (),
        paths: ty.Iterable[str] = (),
        activate: bool = False,
        ctx: ty.Any = None,
    ) -> bool:
        """
        Launch the represented applications

        @files: a seq of GFiles (Gio.File)
        @paths: a seq of bytestring paths
        @activate: activate instead of start new
        """
        try:
            return launch.launch_application(
                self.object,
                files=files,
                paths=paths,
                activate=activate,
                desktop_file=self.init_path,
                screen=ctx and ctx.environment.get_screen(),
            )
        except launch.SpawnError as exc:
            raise OperationError(exc)

    def get_id(self) -> str:
        """Return the unique ID for this app.

        This is the GIO id "gedit.desktop" minus the .desktop part for
        system-installed applications.
        """
        return launch.application_id(self.object, self.init_path)

    def get_actions(self) -> ty.Iterable[Action]:
        id_ = self.get_id()
        if id_ == DESKTOP_ID:
            return

        if launch.application_is_running(id_):
            yield Launch(_("Go To"), is_running=True)
            yield CloseAll()
        else:
            yield Launch()

        yield LaunchAgain()

    def get_description(self) -> str:
        # Use Application's description, else use executable
        # for "file-based" applications we show the path
        app_desc = tounicode(self.object.get_description())
        ret = tounicode(app_desc or self.object.get_executable())
        if self.init_path:
            app_path = utils.get_display_path_for_bytestring(self.init_path)
            return f"({app_path}) {ret}"

        return ret

    def get_gicon(self) -> ty.Optional[GdkPixbuf]:
        return self.object.get_icon()

    def get_icon_name(self) -> str:
        return "exec"


class OpenUrl(Action):
    action_accelerator: ty.Optional[str] = "o"
    rank_adjust: int = 5

    def __init__(self, name: ty.Optional[str] = None) -> None:
        super().__init__(name or _("Open URL"))

    def activate(
        self, leaf: ty.Any, iobj: ty.Any = None, ctx: ty.Any = None
    ) -> None:
        url = leaf.object
        self.open_url(url)

    def open_url(self, url: str) -> None:
        utils.show_url(url)

    def get_description(self) -> str:
        return _("Open URL with default viewer")

    def get_icon_name(self) -> str:
        return "forward"


class Launch(Action):
    """Launches an application (AppLeaf)"""

    action_accelerator: ty.Optional[str] = "o"
    rank_adjust = 5

    def __init__(
        self,
        name: ty.Optional[str] = None,
        is_running: bool = False,
        open_new: bool = False,
    ) -> None:
        """
        If @is_running, style as if the app is running (Show application)
        If @open_new, always start a new instance.
        """
        Action.__init__(self, name or _("Launch"))
        self.is_running = is_running
        self.open_new = open_new

    def wants_context(self) -> bool:
        return True

    def activate(
        self, leaf: ty.Any, iobj: ty.Any = None, ctx: ty.Any = None
    ) -> None:
        leaf.launch(activate=not self.open_new, ctx=ctx)

    def get_description(self) -> str:
        if self.is_running:
            return _("Show application window")

        return _("Launch application")

    def get_icon_name(self) -> str:
        if self.is_running:
            return "go-jump"

        return "kupfer-launch"


class LaunchAgain(Launch):
    action_accelerator: ty.Optional[str] = None
    rank_adjust = 0

    def __init__(self, name: ty.Optional[str] = None):
        Launch.__init__(self, name or _("Launch Again"), open_new=True)

    def item_types(self) -> ty.Iterator[ty.Type[Leaf]]:
        yield AppLeaf

    def valid_for_item(self, leaf: Leaf) -> bool:
        return launch.application_is_running(leaf.get_id())

    def get_description(self) -> str:
        return _("Launch another instance of this application")


class CloseAll(Action):
    """Attempt to close all application windows"""

    rank_adjust = -10

    def __init__(self):
        Action.__init__(self, _("Close"))

    def activate(
        self, leaf: ty.Any, iobj: ty.Any = None, ctx: ty.Any = None
    ) -> None:
        launch.application_close_all(leaf.get_id())

    def item_types(self) -> ty.Iterator[ty.Type[Leaf]]:
        yield AppLeaf

    def valid_for_item(self, leaf: Leaf) -> bool:
        return launch.application_is_running(leaf.get_id())

    def get_description(self) -> str:
        return _("Attempt to close all application windows")

    def get_icon_name(self) -> str:
        return "window-close"


class UrlLeaf(Leaf, TextRepresentation):
    serializable = 1

    def __init__(self, obj: str, name: str) -> None:
        super().__init__(obj, name or obj)
        if obj != name:
            self.kupfer_add_alias(obj)

    def get_actions(self) -> ty.Iterator[Action]:
        yield OpenUrl()

    def get_description(self) -> str:
        return self.object

    def get_icon_name(self) -> str:
        return "text-html"


class RunnableLeaf(Leaf):
    """Leaf where the Leaf is basically the action itself,
    for items such as Quit, Log out etc.
    """

    def __init__(self, obj: ty.Any = None, name: str = "") -> None:
        Leaf.__init__(self, obj, name)

    def get_actions(self) -> ty.Iterator[Action]:
        yield Perform()

    def run(self, ctx: ty.Any = None) -> None:
        raise NotImplementedError

    def wants_context(self) -> bool:
        """Return ``True`` if you want the actions' execution
        context passed as ctx= in RunnableLeaf.run
        """
        return False

    def repr_key(self) -> ty.Any:
        return ""

    def get_gicon(self) -> ty.Optional[GdkPixbuf]:
        iname = self.get_icon_name()
        if iname:
            return icons.get_gicon_with_fallbacks(None, (iname,))

        return icons.ComposedIcon("kupfer-object", "kupfer-execute")

    def get_icon_name(self) -> str:
        return ""


class Perform(Action):
    """Perform the action in a RunnableLeaf"""

    action_accelerator: ty.Optional[str] = "o"
    rank_adjust = 5

    def __init__(self, name: ty.Optional[str] = None):
        # TRANS: 'Run' as in Perform a (saved) command
        super().__init__(name=name or _("Run"))

    def wants_context(self) -> bool:
        return True

    def activate(
        self, leaf: ty.Any, iobj: ty.Any = None, ctx: ty.Any = None
    ) -> None:
        if leaf.wants_context():
            leaf.run(ctx=ctx)
            return

        leaf.run()

    def get_description(self) -> str:
        return _("Perform command")


class TextLeaf(Leaf, TextRepresentation):
    """Represent a text query
    The represented object is a unicode string
    """

    serializable = 1

    def __init__(self, text: str, name: ty.Optional[str] = None) -> None:
        """@text *must* be unicode or UTF-8 str"""
        text = tounicode(text)
        if not name:
            name = self.get_first_text_line(text)

        if len(text) == 0 or not name:
            name = _("(Empty Text)")

        assert name
        Leaf.__init__(self, text, name)

    def repr_key(self) -> ty.Any:
        return zlib.crc32(self.object.encode("utf-8", "surrogateescape"))

    @classmethod
    def get_first_text_line(cls, text: str) -> str:
        firstline = None
        if (firstnl := text.find("\n")) != -1:
            firstline = text[:firstnl].strip()
            if not firstline:
                splut = text.split(None, 1)
                firstline = splut[0] if splut else text
        else:
            firstline = text

        if not firstline:
            firstline = text.strip("\n")

        return firstline

    def get_description(self) -> str:
        numlines = self.object.count("\n") + 1
        desc = self.get_first_text_line(self.object)

        # TRANS: This is description for a TextLeaf, a free-text search
        # TRANS: The plural parameter is the number of lines %(num)d
        return ngettext(
            '"%(text)s"', '(%(num)d lines) "%(text)s"', numlines
        ) % {"num": numlines, "text": desc}

    def get_icon_name(self) -> str:
        return "edit-select-all"

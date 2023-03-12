"""
File actions moved into objects
"""
import os
import typing as ty
from collections import defaultdict

from gi.repository import Gio

from kupfer import launch, utils
from kupfer.desktop_launch import SpawnError

from . import files
from .base import Action
from .exceptions import NoDefaultApplicationError, OperationError

if ty.TYPE_CHECKING:
    _ = str


class Open(Action):
    """Open with default application"""

    action_accelerator = "o"
    rank_adjust = 5

    def __init__(self, name=_("Open")):
        Action.__init__(self, name)

    @classmethod
    def default_application_for_leaf(cls, leaf: files.FileLeaf) -> Gio.AppInfo:
        content_attr = Gio.FILE_ATTRIBUTE_STANDARD_CONTENT_TYPE
        gfile = leaf.get_gfile()
        info = gfile.query_info(content_attr, Gio.FileQueryInfoFlags.NONE, None)
        content_type = info.get_attribute_string(content_attr)
        def_app = Gio.app_info_get_default_for_type(content_type, False)
        if not def_app:
            raise NoDefaultApplicationError(
                (
                    _("No default application for %(file)s (%(type)s)")
                    % {"file": str(leaf), "type": content_type}
                )
                + "\n"
                + _('Please use "%s"') % _("Set Default Application...")
            )

        return def_app

    def wants_context(self) -> bool:
        return True

    def activate(self, leaf, iobj=None, ctx=None):
        assert ctx
        self.activate_multiple((leaf,), ctx)

    def activate_multiple(
        self, objects: ty.Iterable[files.FileLeaf], ctx: ty.Any
    ) -> None:
        appmap: dict[str, Gio.AppInfo] = {}
        leafmap: dict[str, list[files.FileLeaf]] = defaultdict(list)
        for obj in objects:
            app = self.default_application_for_leaf(obj)
            id_ = app.get_id()
            appmap[id_] = app
            leafmap[id_].append(obj)

        for id_, leaves in leafmap.items():
            app = appmap[id_]
            launch.launch_application(
                app,
                paths=[L.object for L in leaves],
                activate=False,
                screen=ctx and ctx.environment.get_screen(),
            )

    def get_description(self) -> ty.Optional[str]:
        return _("Open with default application")


class GetParent(Action):
    action_accelerator = "p"
    rank_adjust = -5

    def __init__(self, name=_("Get Parent Folder")):
        super().__init__(name)

    def has_result(self) -> bool:
        return True

    def activate(
        self, leaf: files.FileLeaf, iobj: ty.Any = None, ctx: ty.Any = None
    ) -> files.FileLeaf:
        fileloc = leaf.object
        parent = os.path.normpath(os.path.join(fileloc, os.path.pardir))
        return files.FileLeaf(parent)

    def get_description(self) -> ty.Optional[str]:
        return None

    def get_icon_name(self):
        return "folder-open"


class OpenTerminal(Action):
    action_accelerator = "t"

    def __init__(self, name=_("Open Terminal Here")):
        super().__init__(name)

    def wants_context(self):
        return True

    def activate(self, leaf, iobj=None, ctx=None):
        assert ctx
        try:
            utils.spawn_terminal(leaf.object, ctx.environment.get_screen())
        except SpawnError as exc:
            raise OperationError(exc) from exc

    def get_description(self) -> ty.Optional[str]:
        return _("Open this location in a terminal")

    def get_icon_name(self):
        return "utilities-terminal"


class Execute(Action):
    """Execute executable file (FileLeaf)"""

    rank_adjust = 10

    def __init__(self, in_terminal=False, quoted=True):
        name = _("Run in Terminal") if in_terminal else _("Run (Execute)")
        super().__init__(name)
        self.in_terminal = in_terminal
        self.quoted = quoted

    def repr_key(self):
        return (self.in_terminal, self.quoted)

    def activate(self, leaf, iobj=None, ctx=None):
        if self.quoted:
            argv = [leaf.object]
        else:
            argv = utils.argv_for_commandline(leaf.object)
        if self.in_terminal:
            utils.spawn_in_terminal(argv)
        else:
            utils.spawn_async(argv)

    def get_description(self) -> ty.Optional[str]:
        if self.in_terminal:
            return _("Run this program in a Terminal")

        return _("Run this program")


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

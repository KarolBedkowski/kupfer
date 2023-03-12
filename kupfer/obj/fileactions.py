"""
Actions for file leaves.

This file is a part of the program kupfer, which is
released under GNU General Public License v3 (or any later version),
see the main program file, and COPYING for details.
"""
import os
import typing as ty
from collections import defaultdict

from gi.repository import Gio

from kupfer import launch

from . import files
from .base import Action
from .exceptions import NoDefaultApplicationError

__all__ = (
    "Open",
    "GetParent",
)

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

__kupfer_name__ = _("Nemo")
__kupfer_sources__ = ("FmObjects",)
__kupfer_actions__ = ("Reveal", "GetInfo", "CopyTo")
__description__ = _("File manager actions")
__version__ = "2017.1"
__author__ = ""

import os
import typing as ty

import dbus

from kupfer import plugin_support
from kupfer.obj import Action, FileLeaf, OperationError, RunnableLeaf, Source
from kupfer.obj.apps import AppLeafContentMixin
from kupfer.support import pretty

if ty.TYPE_CHECKING:
    from gettext import gettext as _

plugin_support.check_dbus_connection()

_SERVICE_NAME = "org.Nemo"
_FO_OBJECT = "/org/Nemo"
_FO_IFACE = "org.Nemo.FileOperations"

_FM_OBJECT = "/org/freedesktop/FileManager1"
_FM_IFACE = "org.freedesktop.FileManager1"


def _get_fm1():
    bus = dbus.SessionBus()
    try:
        proxy_obj = bus.get_object(_SERVICE_NAME, _FM_OBJECT)
    except dbus.DBusException as exc:
        raise OperationError(exc) from exc

    return dbus.Interface(proxy_obj, _FM_IFACE)


def _get_nemo():
    bus = dbus.SessionBus()
    try:
        proxy_obj = bus.get_object(_SERVICE_NAME, _FO_OBJECT)
    except dbus.DBusException as exc:
        raise OperationError(exc) from exc

    return dbus.Interface(proxy_obj, _FO_IFACE)


def _dummy(*args):
    pass


def make_error_handler(ctx):
    def error_handler(exc):
        ctx.register_late_error(exc)

    return error_handler


class Reveal(Action):
    def __init__(self):
        Action.__init__(self, _("Select in File Manager"))

    def wants_context(self):
        return True

    def activate(self, leaf, iobj=None, ctx=None):
        assert ctx
        return self.activate_multiple((leaf,), ctx)

    def activate_multiple(self, leaves, ctx):
        uris = [leaf_uri(leaf) for leaf in leaves]
        id_ = ctx.environment.get_startup_notification_id()
        _get_fm1().ShowItems(
            uris,
            id_,
            reply_handler=_dummy,
            error_handler=make_error_handler(ctx),
        )

    def item_types(self):
        yield FileLeaf


class GetInfo(Action):
    def __init__(self):
        Action.__init__(self, _("Show Properties"))

    def wants_context(self):
        return True

    def activate(self, leaf, iobj=None, ctx=None):
        assert ctx
        return self.activate_multiple((leaf,), ctx)

    def activate_multiple(self, leaves, ctx):
        uris = [leaf_uri(leaf) for leaf in leaves]
        id_ = ctx.environment.get_startup_notification_id()
        _get_fm1().ShowItemProperties(
            uris,
            id_,
            reply_handler=_dummy,
            error_handler=make_error_handler(ctx),
        )

    def item_types(self):
        yield FileLeaf

    def get_description(self):
        return _("Show information about file in file manager")

    def get_icon_name(self):
        return "dialog-information"


def _good_destination(dpath, spath):
    """If directory path @dpath is a valid destination for file @spath
    to be copied or moved to.
    """
    if not os.path.isdir(dpath):
        return False

    spath = os.path.normpath(spath)
    dpath = os.path.normpath(dpath)
    cpfx = os.path.commonprefix((spath, dpath))
    return not (os.path.samefile(dpath, spath) or cpfx == spath)


def leaf_uri(leaf):
    return leaf.get_gfile().get_uri()


class CopyTo(Action, pretty.OutputMixin):
    def __init__(self):
        Action.__init__(self, _("Copy To..."))

    def wants_context(self):
        return True

    def activate(self, leaf, iobj=None, ctx=None):
        assert iobj
        assert ctx
        return self.activate_multiple((leaf,), (iobj,), ctx)

    def activate_multiple(self, leaves, iobjects, ctx):
        # Unroll by looping over the destinations,
        # copying everything into each destination
        nemo = _get_nemo()
        source_uris = [leaf_uri(L) for L in leaves]

        def _reply(*args):
            self.output_debug("reply got for copying", *args)

        for dest_iobj in iobjects:
            desturi = leaf_uri(dest_iobj)
            nemo.CopyURIs(
                source_uris,
                desturi,
                reply_handler=_reply,
                error_handler=make_error_handler(ctx),
            )

    def item_types(self):
        yield FileLeaf

    def valid_for_item(self, leaf):
        return True

    def requires_object(self):
        return True

    def object_types(self):
        yield FileLeaf

    def valid_object(self, obj, for_item):
        return _good_destination(obj.object, for_item.object)

    def get_description(self):
        return _("Copy file to a chosen location")


class EmptyTrash(RunnableLeaf):
    def __init__(self):
        RunnableLeaf.__init__(self, None, _("Empty Trash"))

    def wants_context(self):
        return True

    def run(self, ctx=None):
        assert ctx
        _get_nemo().EmptyTrash(
            reply_handler=_dummy, error_handler=make_error_handler(ctx)
        )

    def get_description(self):
        return None

    def get_icon_name(self):
        return "user-trash-full"


class FmObjects(AppLeafContentMixin, Source):
    appleaf_content_id = "nemo"

    def __init__(self):
        Source.__init__(self, _("Nemo"))

    def get_items(self):
        yield EmptyTrash()

    def provides(self):
        yield RunnableLeaf

    def get_icon_name(self):
        return "nemo"

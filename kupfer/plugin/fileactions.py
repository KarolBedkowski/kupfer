__kupfer_name__ = _("File Actions")
__kupfer_sources__ = ()
__kupfer_text_sources__ = ()
__kupfer_actions__ = (
    "MoveTo",
    "Rename",
    "CopyTo",
)
__description__ = _("More file actions")
__version__ = ""
__author__ = "Ulrik"

import os

# since "path" is a very generic name, you often forget..
from os import path as os_path
from pathlib import Path

from gi.repository import Gio, GLib

from kupfer.objects import Action, FileLeaf, TextLeaf, TextSource
from kupfer.objects import OperationError
from kupfer.support import pretty
from kupfer import task


def _good_destination(dpath, spath):
    """If directory path @dpath is a valid destination for file @spath
    to be copied or moved to.
    """
    if not os_path.isdir(dpath):
        return False
    spath = os_path.normpath(spath)
    dpath = os_path.normpath(dpath)
    if not os.access(dpath, os.R_OK | os.W_OK | os.X_OK):
        return False

    cpfx = os_path.commonprefix((spath, dpath))
    if os_path.samefile(dpath, spath) or cpfx == spath:
        return False

    return True


class MoveTo(Action, pretty.OutputMixin):
    def __init__(self):
        Action.__init__(self, _("Move To..."))

    def has_result(self):
        return True

    def activate(self, leaf, iobj=None, ctx=None):
        assert iobj

        sfile = leaf.get_gfile()
        bname = sfile.get_basename()
        dfile = iobj.get_gfile().get_child(bname)
        try:
            ret = sfile.move(
                dfile, Gio.FileCopyFlags.ALL_METADATA, None, None, None
            )
            self.output_debug(f"Move {sfile} to {dfile} (ret: {ret})")
        except GLib.Error as exc:
            raise OperationError(str(exc))
        else:
            return FileLeaf(dfile.get_path())

    def valid_for_item(self, leaf):
        return os.access(leaf.object, os.R_OK | os.W_OK)

    def requires_object(self):
        return True

    def item_types(self):
        yield FileLeaf

    def object_types(self):
        yield FileLeaf

    def valid_object(self, obj, for_item):
        return _good_destination(obj.object, for_item.object)

    def get_description(self):
        return _("Move file to new location")

    def get_icon_name(self):
        return "go-next"


class RenameSource(TextSource):
    """A source for new names for a file;
    here we "autopropose" the source file's extension,
    but allow overriding it as well as renaming to without
    extension (either using a terminating space, or selecting the
    normal TextSource-returned string).
    """

    def __init__(self, sourcefile):
        self.sourcefile = sourcefile
        name = _("Rename To...").rstrip(".")
        TextSource.__init__(self, name)

    def get_rank(self):
        # this should rank high
        return 100

    def get_items(self, text):
        if not text:
            return
        basename = os_path.basename(self.sourcefile.object)
        _root, ext = os_path.splitext(basename)
        t_root, t_ext = os_path.splitext(text)
        if text.endswith(" "):
            yield TextLeaf(text.rstrip())
        else:
            yield TextLeaf(text) if t_ext else TextLeaf(t_root + ext)

    def get_gicon(self):
        return self.sourcefile.get_gicon()


class Rename(Action, pretty.OutputMixin):
    def __init__(self):
        Action.__init__(self, _("Rename To..."))

    def has_result(self):
        return True

    def activate(self, leaf, iobj=None, ctx=None):
        assert iobj

        sfile = leaf.get_gfile()
        dest = os_path.join(os_path.dirname(leaf.object), iobj.object)
        dfile = Gio.File.new_for_path(dest)
        try:
            ret = sfile.move(
                dfile, Gio.FileCopyFlags.ALL_METADATA, None, None, None
            )
            self.output_debug(f"Move {sfile} to {dfile} (ret: {ret})")
        except GLib.Error as exc:
            raise OperationError(str(exc))
        else:
            return FileLeaf(dfile.get_path())

    def activate_multiple(self, objs, iobjs):
        raise NotImplementedError

    def item_types(self):
        yield FileLeaf

    def valid_for_item(self, leaf):
        return os.access(leaf.object, os.R_OK | os.W_OK)

    def requires_object(self):
        return True

    def object_types(self):
        yield TextLeaf

    def valid_object(self, obj, for_item):
        dest_dir = Path(for_item.object).parent
        return dest_dir.is_dir() and not dest_dir.joinpath(obj.object).exists()

    def object_source(self, for_item=None):
        assert for_item
        return RenameSource(for_item)

    def get_description(self):
        return None


class CopyTo(Action, pretty.OutputMixin):
    def __init__(self):
        Action.__init__(self, _("Copy To..."))

    def wants_context(self):
        return True

    def activate(self, leaf, iobj=None, ctx=None):
        assert iobj
        assert ctx
        sfile = leaf.get_gfile()
        dfile = iobj.get_gfile().get_child(os_path.basename(leaf.object))
        return CopyTask(str(leaf), sfile, dfile, ctx)

    def is_async(self):
        return True

    def item_types(self):
        yield FileLeaf

    def valid_for_item(self, leaf):
        return (not leaf.is_dir()) and os.access(leaf.object, os.R_OK)

    def requires_object(self):
        return True

    def object_types(self):
        yield FileLeaf

    def valid_object(self, obj, for_item):
        return _good_destination(obj.object, for_item.object)

    def get_description(self):
        return _("Copy file to a chosen location")


class CopyTask(task.ThreadTask, pretty.OutputMixin):
    def __init__(self, name, gsource, gdest, ctx):
        super().__init__(name)
        self.gsource = gsource
        self.gdest = gdest
        self.ctx = ctx

    def thread_do(self):
        try:
            # FIXME: This should be async
            self.output_debug(f"Copy {self.gsource} to {self.gdest}")
            ret = self.gsource.copy(
                self.gdest, Gio.FileCopyFlags.ALL_METADATA, None, None, None
            )
            self.output_debug(f"Copy ret {ret!r}")
        except GLib.Error as exc:
            raise OperationError(exc.message)

    def thread_finish(self):
        self.ctx.register_late_result(FileLeaf(self.gdest.get_path()))

    def thread_finally(self, exc_info):
        if exc_info is not None:
            self.ctx.register_late_error(exc_info)

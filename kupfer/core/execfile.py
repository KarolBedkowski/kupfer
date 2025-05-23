import hashlib
import os
import pickle
import typing as ty
from pathlib import Path

from gi.repository import GdkPixbuf, Gio, GLib

from kupfer import puid
from kupfer.obj.base import KupferObject
from kupfer.support import conspickle, pretty

if ty.TYPE_CHECKING:
    from gettext import gettext as _

KUPFER_COMMAND_SHEBANG = b"#!/usr/bin/env kupfer-exec\n"

__all__ = (
    "ExecutionError",
    "parse_kfcom_file",
    "save_to_file",
    "update_icon",
)


class ExecutionError(Exception):
    pass


def parse_kfcom_file(filepath: str) -> tuple[ty.Any, ...]:
    """Extract the serialized command inside @filepath

    The file must be executable (comparable to a shell script)
    >>> parse_kfcom_file(__file__)  # doctest: +ELLIPSIS
    Traceback (most recent call last):
        ...
    ExecutionError: ... (not executable)

    Return commands triple
    """
    if not os.access(filepath, os.X_OK):
        raise ExecutionError(
            _('No permission to run "%s" (not executable)')
            % GLib.filename_display_basename(filepath)
        )

    data = Path(filepath).read_bytes()

    # strip shebang away
    if data.startswith(b"#!") and b"\n" in data:
        _shebang, data = data.split(b"\n", 1)

    try:
        id_ = conspickle.BasicUnpickler.loads(data)
        command_object = puid.resolve_unique_id(id_)
    except pickle.UnpicklingError as err:
        raise ExecutionError(f"Could not parse: {err}") from err
    except Exception as err:
        raise ExecutionError(
            f'"{os.path.basename(filepath)}" is not a saved command'
        ) from err

    if command_object is None:
        raise ExecutionError(
            _('Command in "%s" is not available')
            % GLib.filename_display_basename(filepath)
        )

    try:
        return tuple(command_object.object)  # type: ignore
    except (AttributeError, TypeError) as exe:
        raise ExecutionError(
            f'"{os.path.basename(filepath)}" is not a saved command'
        ) from exe
    finally:
        GLib.idle_add(update_icon, command_object, filepath)


def save_to_file(command_leaf: ty.Any, filename: str) -> None:
    ofd = os.open(filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o777)
    with os.fdopen(ofd, "wb") as wfile:
        wfile.write(KUPFER_COMMAND_SHEBANG)
        pickle.dump(puid.get_unique_id(command_leaf), wfile, protocol=3)


def _write_thumbnail(gfile: Gio.File, pixbuf: GdkPixbuf.Pixbuf) -> Path:
    uri = gfile.get_uri()
    hashname = hashlib.md5(uri.encode("utf-8")).hexdigest()
    thumb_dir = Path("~/.thumbnails/normal").expanduser()
    thumb_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    thumb_filename = thumb_dir.joinpath(hashname + ".png")
    pixbuf.savev(str(thumb_filename), "png", [], [])
    return thumb_filename


def update_icon(kobj: KupferObject, filepath: str) -> None:
    """Give @filepath a custom icon taken from @kobj"""
    icon_key = "metadata::custom-icon"

    gfile = Gio.File.new_for_path(filepath)
    finfo = gfile.query_info(icon_key, Gio.FileQueryInfoFlags.NONE, None)
    custom_icon_uri = finfo.get_attribute_string(icon_key)
    if (
        custom_icon_uri
        and Gio.File.new_for_uri(custom_icon_uri).query_exists()
    ):
        return

    namespace = gfile.query_writable_namespaces()  # FileAttributeInfoList
    if namespace.n_infos > 0:
        pretty.print_debug(__name__, "Updating icon for", filepath)
        thumb_filename = _write_thumbnail(gfile, kobj.get_pixbuf(128))
        try:
            gfile.set_attribute_string(
                icon_key,
                Gio.File.new_for_path(str(thumb_filename)).get_uri(),
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
        except GLib.GError:
            pretty.print_exc(__name__)


if __name__ == "__main__":
    import doctest

    doctest.testmod()

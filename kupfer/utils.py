from __future__ import annotations

import itertools
import os
import tempfile
import typing as ty
from contextlib import suppress
from os import path as os_path
from pathlib import Path

from gi.repository import Gdk, Gio, GLib, Gtk

from kupfer.support import desktop_parse, pretty
from kupfer import launch

FilterFunc = ty.Callable[[str], bool]


def get_dirlist(
    folder: str,
    max_depth: int = 0,
    include: ty.Optional[FilterFunc] = None,
    exclude: ty.Optional[FilterFunc] = None,
) -> ty.Iterator[str]:
    """
    Return a list of absolute paths in folder
    include, exclude: a function returning a boolean
    def include(filename):
        return ShouldInclude

    """

    def include_file(file):
        return (not include or include(file)) and (
            not exclude or not exclude(file)
        )

    for dirname, dirnames, fnames in os.walk(folder):
        # skip deep directories
        depth = len(os.path.relpath(dirname, folder).split(os.path.sep)) - 1
        if depth >= max_depth:
            dirnames.clear()
            continue

        excl_dir = []
        for directory in dirnames:
            if include_file(directory):
                yield os_path.join(dirname, directory)
            else:
                excl_dir.append(directory)

        yield from (
            os_path.join(dirname, file) for file in fnames if include_file(file)
        )

        for directory in reversed(excl_dir):
            dirnames.remove(directory)


def argv_for_commandline(cli: str) -> list[str]:
    return desktop_parse.parse_argv(cli)


def show_path(path: str) -> None:
    """Open local @path with default viewer"""
    # Implemented using Gtk.show_uri
    gfile = Gio.File.new_for_path(path)
    if not gfile:
        return

    url = gfile.get_uri()
    show_url(url)


def show_url(url: str) -> bool:
    """Open any @url with default viewer"""
    try:
        pretty.print_debug(__name__, "show_url", url)
        return Gtk.show_uri(  # type: ignore
            Gdk.Screen.get_default(), url, Gtk.get_current_event_time()
        )
    except GLib.GError as exc:
        pretty.print_error(__name__, "Gtk.show_uri:", exc)

    return False


def show_help_url(url: str) -> bool:
    """
    Try at length to display a startup notification for the help browser.

    Return False if there is no handler for the help URL
    """
    ## Check that the system help viewer is Yelp,
    ## and if it is, launch its startup notification.
    scheme = Gio.File.new_for_uri(url).get_uri_scheme()
    default = Gio.app_info_get_default_for_uri_scheme(scheme)
    if not default:
        return False

    help_viewer_id = "yelp.desktop"

    try:
        yelp = Gio.DesktopAppInfo.new(help_viewer_id)
    except (TypeError, RuntimeError):
        return show_url(url)

    cmd_path = lookup_exec_path(default.get_executable())
    yelp_path = lookup_exec_path(yelp.get_executable())
    if cmd_path and yelp_path and os.path.samefile(cmd_path, yelp_path):
        with suppress(launch.SpawnError):
            launch.spawn_async_notify_as(help_viewer_id, [cmd_path, url])
            return True

    return show_url(url)


def lookup_exec_path(exename: str) -> ty.Optional[str]:
    "Return path for @exename in $PATH or None"
    env_path = os.environ.get("PATH") or os.defpath
    for execdir in env_path.split(os.pathsep):
        exepath = Path(execdir, exename)
        if os.access(exepath, os.R_OK | os.X_OK) and exepath.is_file():
            return str(exepath)

    return None


def is_directory_writable(dpath: str | Path) -> bool:
    """If directory path @dpath is a valid destination to write new files?"""
    if isinstance(dpath, str):
        dpath = Path(dpath)

    if not dpath.is_dir():
        return False

    return os.access(dpath, os.R_OK | os.W_OK | os.X_OK)


def is_file_writable(dpath: str | Path) -> bool:
    """If @dpath is a valid, writable file"""
    if isinstance(dpath, str):
        dpath = Path(dpath)

    if not dpath.is_file():
        return False

    return os.access(dpath, os.R_OK | os.W_OK)


def get_destpath_in_directory(
    directory: str, filename: str, extension: ty.Optional[str] = None
) -> str:
    """Find a good destpath for a file named @filename in path @directory
    Try naming the file as filename first, before trying numbered versions
    if the previous already exist.

    If @extension, it is used as the extension. Else the filename is split and
    the last extension is used
    """
    # find a nonexisting destname
    if extension:
        basename = filename + extension
        root, ext = filename, extension
    else:
        basename = filename
        root, ext = os_path.splitext(filename)

    ctr = itertools.count(1)
    destpath = Path(directory, basename)
    while destpath.exists():
        num = next(ctr)
        basename = f"{root}-{num}{ext}"
        destpath = Path(directory, basename)

    return str(destpath)


def get_destfile_in_directory(
    directory: str, filename: str, extension: ty.Optional[str] = None
) -> tuple[ty.Optional[ty.BinaryIO], ty.Optional[str]]:
    """Find a good destination for a file named @filename in path @directory.

    Like get_destpath_in_directory, but returns an open file object, opened
    atomically to avoid race conditions.

    Return (fileobj, filepath)
    """
    # retry if it fails
    for _retry in range(3):
        destpath = get_destpath_in_directory(directory, filename, extension)
        try:
            fileno = os.open(
                destpath, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666
            )
        except OSError as exc:
            pretty.print_error(__name__, exc)
        else:
            return (os.fdopen(fileno, "wb"), destpath)

    return (None, None)


def get_destfile(
    destpath: str | Path,
) -> tuple[ty.Optional[ty.BinaryIO], ty.Optional[str]]:
    """
    Open file object for full file path. Return the same object
    like get_destfile_in_directory.


    Return (fileobj, filepath)
    """
    # retry if it fails
    for _retry in range(3):
        try:
            fileno = os.open(
                destpath, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666
            )
        except OSError as exc:
            pretty.print_error(__name__, exc)
        else:
            return (os.fdopen(fileno, "wb"), str(destpath))

    return (None, None)


def get_safe_tempfile() -> tuple[ty.BinaryIO, str]:
    """Return (fileobj, filepath) pointing to an open temporary file"""

    fileno, path = tempfile.mkstemp()
    return (os.fdopen(fileno, "wb"), path)


_homedir = os.path.expanduser("~/")
_homedir_len = len(_homedir)


def get_display_path_for_bytestring(filepath: ty.AnyStr) -> str:
    """Return a unicode path for display for bytestring @filepath

    Will use glib's filename decoding functions, and will
    format nicely (denote home by ~/ etc)
    """
    desc: str = GLib.filename_display_name(filepath)
    if desc.startswith(_homedir) and _homedir != desc:
        desc = f"~/{desc[_homedir_len:]}"

    return desc


def parse_time_interval(tstr: str) -> int:
    """
    Parse a time interval in @tstr, return whole number of seconds

    >>> parse_time_interval("2")
    2
    >>> parse_time_interval("1h 2m 5s")
    3725
    >>> parse_time_interval("2 min")
    120
    """
    weights = {
        "s": 1,
        "sec": 1,
        "m": 60,
        "min": 60,
        "h": 3600,
        "hours": 3600,
    }

    with suppress(ValueError):
        return int(tstr)

    total = 0
    amount = 0
    # Split the string in runs of digits and runs of characters
    for isdigit, group in itertools.groupby(tstr, lambda k: k.isdigit()):
        if not (part := "".join(group).strip()):
            continue

        if isdigit:
            amount = int(part)
        else:
            total += amount * weights.get(part.lower(), 0)
            amount = 0

    return total


if __name__ == "__main__":
    import doctest

    doctest.testmod()

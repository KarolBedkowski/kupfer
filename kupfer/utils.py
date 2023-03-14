from __future__ import annotations

import os
import typing as ty
from contextlib import suppress

from gi.repository import Gdk, Gio, GLib, Gtk

from kupfer import launch
from kupfer.support import desktop_parse, fileutils, pretty


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

    cmd_path = fileutils.lookup_exec_path(default.get_executable())
    yelp_path = fileutils.lookup_exec_path(yelp.get_executable())
    if cmd_path and yelp_path and os.path.samefile(cmd_path, yelp_path):
        with suppress(launch.SpawnError):
            launch.spawn_async_notify_as(help_viewer_id, [cmd_path, url])
            return True

    return show_url(url)


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


if __name__ == "__main__":
    import doctest

    doctest.testmod()

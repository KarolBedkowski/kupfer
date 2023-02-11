"""
Access functions of Kupfer's Interface
"""
from __future__ import annotations

import typing as ty

from gi.repository import Gtk

from kupfer import utils, version
from kupfer.ui.uievents import GUIEnvironmentContext
from kupfer.ui import preferences


def _get_time(ctxenv: GUIEnvironmentContext | None) -> int:
    return ctxenv.get_timestamp() if ctxenv else Gtk.get_current_event_time()  # type: ignore


def show_help(ctxenv: GUIEnvironmentContext | None = None) -> None:
    """
    Show Kupfer help pages, if possible
    """
    if not utils.show_help_url(f"help:{version.PACKAGE_NAME}"):
        utils.show_url(version.HELP_WEBSITE)


class _AboutDialog:
    _dialog = None

    @classmethod
    def get(cls):
        if cls._dialog is None:
            cls._dialog = cls._create()

        return cls._dialog

    @classmethod
    def _create(cls):
        abdlg = Gtk.AboutDialog()
        abdlg.set_program_name(version.PROGRAM_NAME)
        abdlg.set_icon_name(version.ICON_NAME)
        abdlg.set_logo_icon_name(version.ICON_NAME)
        abdlg.set_version(version.VERSION)
        abdlg.set_comments(version.SHORT_DESCRIPTION)
        abdlg.set_copyright(version.COPYRIGHT)
        abdlg.set_website(version.WEBSITE)
        abdlg.set_license(version.LICENSE)
        abdlg.set_authors(version.AUTHORS)
        if version.DOCUMENTERS:
            abdlg.set_documenters(version.DOCUMENTERS)

        if version.TRANSLATOR_CREDITS:
            abdlg.set_translator_credits(version.TRANSLATOR_CREDITS)

        if version.ARTISTS:
            abdlg.set_artists(version.ARTISTS)

        abdlg.connect("response", cls._response_callback)
        abdlg.connect("delete-event", cls._close_callback)

        return abdlg

    @staticmethod
    def _response_callback(dialog: Gtk.Dialog, response_id: ty.Any) -> None:
        dialog.hide()

    @classmethod
    def _close_callback(cls, *_args):
        cls._dialog = None
        return True


def show_about_dialog(
    ctxenv: ty.Optional[GUIEnvironmentContext] = None,
) -> None:
    """
    create an about dialog and show it
    """
    dlg = _AboutDialog.get()
    if ctxenv:
        ctxenv.present_window(dlg)
    else:
        dlg.present()


def show_preferences(ctxenv: GUIEnvironmentContext) -> None:
    from kupfer.ui import preferences

    win = preferences.get_preferences_window_controller()
    if ctxenv:
        win.show_on_screen(ctxenv.get_timestamp(), ctxenv.get_screen())
    else:
        win.show(_get_time(ctxenv))


def show_plugin_info(
    plugin_id: str, ctxenv: ty.Optional[GUIEnvironmentContext] = None
) -> None:
    prefs = preferences.get_preferences_window_controller()
    prefs.show_focus_plugin(plugin_id, _get_time(ctxenv))

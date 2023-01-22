"""
Access functions of Kupfer's Interface
"""
import typing as ty

from gi.repository import Gtk

from kupfer import utils, version
from kupfer.ui.uievents import GUIEnvironmentContext


def _get_time(ctxenv: ty.Optional[GUIEnvironmentContext]) -> int:
    return ctxenv.get_timestamp() if ctxenv else Gtk.get_current_event_time()  # type: ignore


def show_help(ctxenv: ty.Optional[GUIEnvironmentContext] = None) -> None:
    """
    Show Kupfer help pages, if possible
    """
    if not utils.show_help_url(f"help:{version.PACKAGE_NAME}"):
        utils.show_url(version.HELP_WEBSITE)


_ABOUT_DIALOG = None


def show_about_dialog(
    ctxenv: ty.Optional[GUIEnvironmentContext] = None,
) -> None:
    """
    create an about dialog and show it
    """
    # Use only one instance, stored in _ABOUT_DIALOG
    global _ABOUT_DIALOG
    if _ABOUT_DIALOG:
        ab = _ABOUT_DIALOG
    else:
        ab = Gtk.AboutDialog()
        ab.set_program_name(version.PROGRAM_NAME)
        ab.set_icon_name(version.ICON_NAME)
        ab.set_logo_icon_name(version.ICON_NAME)
        ab.set_version(version.VERSION)
        ab.set_comments(version.SHORT_DESCRIPTION)
        ab.set_copyright(version.COPYRIGHT)
        ab.set_website(version.WEBSITE)
        ab.set_license(version.LICENSE)
        ab.set_authors(version.AUTHORS)
        if version.DOCUMENTERS:
            ab.set_documenters(version.DOCUMENTERS)
        if version.TRANSLATOR_CREDITS:
            ab.set_translator_credits(version.TRANSLATOR_CREDITS)
        if version.ARTISTS:
            ab.set_artists(version.ARTISTS)

        ab.connect("response", _response_callback)
        # do not delete window on close
        ab.connect("delete-event", lambda *ign: True)
        _ABOUT_DIALOG = ab

    if ctxenv:
        ctxenv.present_window(ab)
    else:
        ab.present()


def _response_callback(dialog: Gtk.Dialog, response_id: ty.Any) -> None:
    dialog.hide()


def show_preferences(ctxenv: GUIEnvironmentContext) -> None:
    from kupfer.ui import preferences

    win = preferences.GetPreferencesWindowController()
    if ctxenv:
        win.show_on_screen(ctxenv.get_timestamp(), ctxenv.get_screen())
    else:
        win.show(_get_time(ctxenv))


def show_plugin_info(
    plugin_id: str, ctxenv: ty.Optional[GUIEnvironmentContext] = None
) -> None:
    from kupfer.ui import preferences

    prefs = preferences.GetPreferencesWindowController()
    prefs.show_focus_plugin(plugin_id, _get_time(ctxenv))

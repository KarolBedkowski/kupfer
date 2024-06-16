from __future__ import annotations

from gi.repository import Gdk, Gtk

from kupfer import config, version


class _GetTextDialogController:
    def __init__(
        self,
        title: str,
        message: str,
        text: str | None = None,
        screen: Gdk.Screen | None = None,
        parent: Gtk.Window | None = None,
    ):
        """
        screen: Screen to use
        parent: Parent toplevel window
        """
        builder = Gtk.Builder()
        builder.set_translation_domain(version.PACKAGE_NAME)

        builder.add_from_file(config.get_data_file("gettext_dialog.ui"))
        builder.connect_signals(self)  # pylint: disable=no-member
        self.window = builder.get_object("dialoggettext")
        self.entry = builder.get_object("entry")
        builder.get_object("label_title").set_text(title or "")
        builder.get_object("label_message").set_text(message or "")

        if screen:
            self.window.set_screen(screen)

        if parent:
            self.window.set_transient_for(parent)

        self.entry.set_text(text or "")
        self.text = None

    def run(self) -> str | None:
        """Run dialog, return key codes or None when user press cancel"""

        self.window.set_keep_above(True)
        self.window.run()
        self.window.destroy()
        return self.text

    def _return(self, key: str | None) -> None:
        "Finish dialog with @key as result"
        self._key = key
        self.window.hide()

    def on_buttonok_activate(self, _widget: Gtk.Widget) -> bool:
        self.text = self.entry.get_text()
        self.window.hide()
        return True

    def on_buttonclose_activate(self, _widget: Gtk.Widget) -> bool:
        self.window.hide()
        return True


def ask_for_text(
    title: str,
    message: str,
    text: str | None = None,
    screen: Gdk.Screen | None = None,
    parent: Gtk.Window | None = None,
) -> str | None:
    dlg = _GetTextDialogController(
        title,
        message,
        text,
        screen=screen,
        parent=parent,
    )
    return dlg.run()

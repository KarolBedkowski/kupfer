__kupfer_name__ = _("GNU Screen")
__kupfer_sources__ = ("ScreenSessionsSource",)
__description__ = _("Active GNU Screen sessions")
__version__ = ""
__author__ = "Ulrik Sverdrup <ulrik.sverdrup@gmail.com>"

import os
import pwd
from pathlib import Path

from kupfer import utils
from kupfer.obj import Action, Leaf, Source
from kupfer.obj.helplib import FilesystemWatchMixin


def screen_sessions_infos():
    """
    Yield tuples of pid, name, time, status
    for running screen sessions
    """
    pipe = os.popen("screen -list")
    output = pipe.read()
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) == 4:
            _empty, pidname, time, status = fields
            pid, name = pidname.split(".", 1)
            time = time.strip("()")
            status = status.strip("()")
            yield (pid, name, time, status)


def get_username():
    """Return username for current user"""
    info = pwd.getpwuid(os.geteuid())
    return info[0]


class ScreenSession(Leaf):
    """Represented object is the session pid as string"""

    def get_actions(self):
        return (AttachScreen(),)

    def is_valid(self):
        for pid, *_rest in screen_sessions_infos():
            if self.object == pid:
                return True

        return False

    def get_description(self):
        for pid, _name, time, status in screen_sessions_infos():
            if self.object == pid:
                break
        else:
            return f"{self.name} ({self.object})"
        # Handle localization of status
        status_dict = {
            "Attached": _("Attached"),
            "Detached": _("Detached"),
        }
        status = status_dict.get(status, status)
        return _("%(status)s session (%(pid)s) created %(time)s") % {
            "status": status,
            "pid": pid,
            "time": time,
        }

    def get_icon_name(self):
        return "gnome-window-manager"


class ScreenSessionsSource(Source, FilesystemWatchMixin):
    """Source for GNU Screen sessions"""

    def __init__(self):
        super().__init__(_("Screen Sessions"))
        self.screen_dir = None

    def initialize(self):
        ## the screen dir might not exist when we start
        ## luckily, gio can monitor directories before they exist
        self.screen_dir = (
            os.getenv("SCREENDIR") or f"/var/run/screen/S-{get_username()}"
        )
        if not Path(self.screen_dir).exists():
            self.output_debug("Screen socket dir or SCREENDIR not found")

        self.monitor_token = self.monitor_directories(
            self.screen_dir, force=True
        )

    def get_items(self):
        assert self.screen_dir
        if not Path(self.screen_dir).exists():
            return

        for pid, name, _time, _status in screen_sessions_infos():
            yield ScreenSession(pid, name)

    def get_description(self):
        return _("Active GNU Screen sessions")

    def get_icon_name(self):
        return "terminal"

    def provides(self):
        yield ScreenSession


class AttachScreen(Action):
    """ """

    def __init__(self):
        name = _("Attach")
        super().__init__(name)

    def activate(self, leaf, iobj=None, ctx=None):
        pid = leaf.object
        action_argv = ["screen", "-x", "-R", str(pid)]
        utils.spawn_in_terminal(action_argv)

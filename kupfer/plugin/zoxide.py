"""
Load directories from zoxide (https://github.com/ajeetdsouza/zoxide)
with configured, minimal score.

Optionally, some directories can be excluded (by path) and only existing
files may be presented (this may slowdown loading when turned on).
"""

__kupfer_name__ = _("Zoxide Directories")
__kupfer_sources__ = ("ZoxideDirSource",)
__description__ = _("Load top directories from zoxide database")
__version__ = "2023-04-02"
__author__ = "Karol Będkowski <karol.bedkowski@gmail.com>"

import subprocess
import typing as ty

from kupfer import config, plugin_support
from kupfer.obj import FileLeaf, Source
from kupfer.obj.helplib import FilesystemWatchMixin

__kupfer_settings__ = plugin_support.PluginSettings(
    {
        "key": "exclude",
        "label": _("Exclude directories (;-separated):"),
        "type": str,
        "value": "",
    },
    {
        "key": "min_score",
        "label": _("Minimal score:"),
        "type": int,
        "value": 1,
    },
    {
        "key": "existing",
        "label": _("Show only existing directories"),
        "type": bool,
        "value": True,
    },
)

if ty.TYPE_CHECKING:
    _ = str


def _get_dirs(exclude: str, min_score: int, existing: bool) -> ty.Iterator[str]:
    cmd = ["zoxide", "query", "--list", "--score"]
    if not existing:
        cmd.append("--all")

    for excl in exclude.split(";"):
        if excl := excl.strip():
            cmd.extend(("--exclude", excl))

    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
        stdout, _stderr = proc.communicate()
        for line in stdout.splitlines():
            line = line.strip()
            score, _dummy, dirpath = line.partition(b" ")
            if not line:
                continue

            if float(score) < min_score:
                return

            yield dirpath.decode()


class ZoxideDirSource(Source, FilesystemWatchMixin):
    def __init__(self):
        super().__init__(name=_("Zoxide Directories"))

    def initialize(self):
        zoxide_home = config.get_data_dirs("", "zoxide")
        self.monitor = self.monitor_directories(*zoxide_home)

    def initialized(self):
        __kupfer_settings__.connect(
            "plugin-setting-changed", self._setting_changed
        )

    def monitor_include_file(self, gfile):
        return gfile and gfile.get_basename() == "zo.db"

    def get_items(self):
        for dirname in _get_dirs(
            __kupfer_settings__["exclude"],
            __kupfer_settings__["min_score"],
            __kupfer_settings__["existing"],
        ):
            yield FileLeaf(dirname)

    def _setting_changed(self, settings, key, value):
        if key in ("exclude", "min_score"):
            self.mark_for_update()
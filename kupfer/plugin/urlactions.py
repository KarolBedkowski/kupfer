from __future__ import annotations

__kupfer_name__ = _("URL Actions")
__kupfer_sources__ = ()
__kupfer_text_sources__ = ()
__kupfer_actions__ = (
    "DownloadAndOpen",
    "DownloadTo",
)
__description__ = _("URL Actions")
__version__ = ""
__author__ = "Ulrik Sverdrup <ulrik.sverdrup@gmail.com>"

import os
import shutil
import urllib.request
import urllib.parse
import urllib.error
import typing as ty

from kupfer.objects import Action, UrlLeaf, FileLeaf
from kupfer import utils, task


def url_name(url):
    return os.path.basename(url.rstrip("/"))


def header_name(headers):
    content_disp = headers.get("Content-Disposition", "")
    for part in content_disp.split(";"):
        if part.strip().lower().startswith("filename="):
            return part.split("=", 1)[-1]
    return content_disp


class DownloadTask(task.ThreadTask):
    def __init__(
        self, uri, destdir=None, tempfile=False, finish_callback=None
    ):
        super().__init__()
        self.uri = uri
        self.download_finish_callback = finish_callback
        self.destdir = destdir
        self.use_tempfile = tempfile
        self.destpath = None

    def _get_dst_file(
        self, destname: str
    ) -> tuple[ty.BinaryIO | None, str | None]:
        if self.use_tempfile:
            return utils.get_safe_tempfile()

        return utils.get_destfile_in_directory(self.destdir, destname)

    def thread_do(self):
        # TODO: check response and destfile was DownloadTask field
        with urllib.request.urlopen(self.uri) as response:
            destname = header_name(response.headers) or url_name(response.url)
            destfile, self.destpath = self._get_dst_file(destname)
            if not destfile:
                raise OSError("Could not write output file")

            try:
                shutil.copyfileobj(response, destfile)
            finally:
                destfile.close()

    def thread_finish(self):
        if self.download_finish_callback:
            self.download_finish_callback(self.destpath)


class DownloadAndOpen(Action):
    """Asynchronous action to download file and open it"""

    def __init__(self):
        Action.__init__(self, _("Download and Open"))

    def is_async(self):
        return True

    def wants_context(self):
        return True

    def activate(self, leaf, iobj=None, ctx=None):
        assert ctx

        uri = leaf.object

        def finish_action(filename):
            utils.show_path(filename)
            ctx.register_late_result(FileLeaf(filename), show=False)

        return DownloadTask(uri, None, True, finish_action)

    def item_types(self):
        yield UrlLeaf

    def get_description(self):
        return None


class DownloadTo(Action):
    def __init__(self):
        Action.__init__(self, _("Download To..."))

    def is_async(self):
        return True

    def wants_context(self):
        return True

    def activate(self, leaf, iobj=None, ctx=None):
        assert ctx

        uri = leaf.object

        def finish_action(filename):
            ctx.register_late_result(FileLeaf(filename))

        return DownloadTask(uri, obj.object, False, finish_action)

    def item_types(self):
        yield UrlLeaf

    def requires_object(self):
        return True

    def object_types(self):
        yield FileLeaf

    def valid_object(self, obj, for_item=None):
        return utils.is_directory_writable(obj.object)

    def get_description(self):
        return _("Download URL to a chosen location")

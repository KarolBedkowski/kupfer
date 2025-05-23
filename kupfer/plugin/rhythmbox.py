"""
Changes:
    2012-10-17 Karol Będkowski:
        + control rhythmbox via dbus interface
        + load songs via dbus interface
    2023-02-19 KB:
        + catch errors when no mpris is available via dbus
        + simplify dbus string conversion

NOTE: this require Rhythmbox with mpris support (i.e. rhythmbox-plugins package
installed)
"""

from __future__ import annotations

__kupfer_name__ = _("Rhythmbox")
__kupfer_sources__ = ("RhythmboxSource",)
__description__ = _("Play and enqueue tracks and browse the music library")
__version__ = "2023.1"
__author__ = "US, Karol Będkowski"


import itertools
import operator
import os
import typing as ty
from collections import defaultdict
from hashlib import md5

import dbus
from gi.repository import Gio

from kupfer import config, icons, launch, plugin_support
from kupfer.obj import (
    Action,
    FileLeaf,
    Leaf,
    NotAvailableError,
    OperationError,
    RunnableLeaf,
    Source,
    SourceLeaf,
)
from kupfer.obj.apps import AppLeafContentMixin
from kupfer.obj.helplib import PicklingHelperMixin
from kupfer.plugin import rhythmbox_support
from kupfer.support import kupferstring, pretty, weaklib

if ty.TYPE_CHECKING:
    from gettext import gettext as _


plugin_support.check_dbus_connection()
plugin_support.check_command_available("rhythmbox-client")

__kupfer_settings__ = plugin_support.PluginSettings(
    {
        "key": "toplevel_artists",
        "label": _("Include artists in top level"),
        "type": bool,
        "value": True,
    },
    {
        "key": "toplevel_albums",
        "label": _("Include albums in top level"),
        "type": bool,
        "value": False,
    },
    {
        "key": "toplevel_songs",
        "label": _("Include songs in top level"),
        "type": bool,
        "value": False,
    },
)

_BUS_NAME = "org.gnome.Rhythmbox3"
_OBJ_PATH_MPRIS = "/org/mpris/MediaPlayer2"
_OBJ_NAME_MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"
_OBJ_PATH_MEDIASERVC_ALL = "/org/gnome/UPnP/MediaServer2/Library/all"
_OBJ_NAME_MEDIA_CONT = "org.gnome.UPnP.MediaContainer2"


def _toutf8_lossy(ustr):
    return ustr.encode("UTF-8", "replace")


def _create_dbus_connection_mpris(obj_name, obj_path, activate=False):
    """Create dbus connection to Rhytmbox
    @activate: if True, start program if not running
    """
    interface = None
    sbus = dbus.SessionBus()
    try:
        proxy_obj = sbus.get_object(
            "org.freedesktop.DBus", "/org/freedesktop/DBus"
        )
        dbus_iface = dbus.Interface(proxy_obj, "org.freedesktop.DBus")
        if (activate or dbus_iface.NameHasOwner(_BUS_NAME)) and (
            obj := sbus.get_object(_BUS_NAME, obj_path)
        ):
            interface = dbus.Interface(obj, obj_name)

    except dbus.exceptions.DBusException as err:
        pretty.print_debug(err)

    return interface


def _tracknr(string: ty.Any) -> int | None:
    try:
        return int(string)
    except ValueError:
        return None


def _get_all_songs_via_dbus():
    try:
        iface = _create_dbus_connection_mpris(
            _OBJ_NAME_MEDIA_CONT, _OBJ_PATH_MEDIASERVC_ALL
        )
        if iface:
            for item in iface.ListItems(0, 9999, ["*"]):
                yield {
                    "album": str(item["Album"]),
                    "artist": str(item["Artist"]),
                    "title": str(item["DisplayName"]),
                    "track-number": _tracknr(item["TrackNumber"]),
                    "location": str(item["URLs"][0]),
                    "date": str(item["Date"]),
                }
    except Exception:
        pretty.print_exc(__name__, "_get_all_songs_via_dbus error")


def spawn_async(argv):
    try:
        launch.spawn_async_raise(argv)
    except launch.SpawnError as exc:
        raise OperationError(exc) from exc


def enqueue_songs(info, clear_queue=False, play_first=False):
    songs = list(info)
    if not songs:
        return

    qargv = ["rhythmbox-client"]
    if clear_queue:
        qargv.append("--clear-queue")

    if play_first and songs:
        song = songs[0]
        songs = songs[1:]
        uri = song["location"]
        qargv.extend(("--play-uri", uri))

    for song in songs:
        uri = song["location"]
        qargv.extend(("--enqueue", uri))

    spawn_async(qargv)


class ClearQueue(RunnableLeaf):
    def __init__(self):
        RunnableLeaf.__init__(self, name=_("Clear Queue"))

    def run(self, ctx=None):
        spawn_async(("rhythmbox-client", "--no-start", "--clear-queue"))

    def get_icon_name(self):
        return "edit-clear"


def _songs_from_leaf(leaf):
    "return a sequence of songs from @leaf"
    if isinstance(leaf, SongLeaf):
        return (leaf.object,)

    if isinstance(leaf, TrackCollection):
        return tuple(leaf.object)

    return ()


class PlayTracks(Action):
    action_accelerator = "o"

    rank_adjust = 5

    def __init__(self):
        Action.__init__(self, _("Play"))

    def activate(self, leaf, iobj=None, ctx=None):
        self.activate_multiple((leaf,))

    def activate_multiple(self, objects):
        # for multiple dispatch, play the first and enqueue the rest
        to_enqueue = []
        for leaf in objects:
            to_enqueue.extend(_songs_from_leaf(leaf))

        if to_enqueue:
            enqueue_songs(
                to_enqueue, clear_queue=len(to_enqueue) > 1, play_first=True
            )

    def get_description(self):
        return _("Play tracks in Rhythmbox")

    def get_icon_name(self):
        return "media-playback-start"


class Enqueue(Action):
    action_accelerator = "e"

    def __init__(self):
        Action.__init__(self, _("Enqueue"))

    def activate(self, leaf, iobj=None, ctx=None):
        self.activate_multiple((leaf,))

    def activate_multiple(self, objects):
        to_enqueue = []
        for leaf in objects:
            to_enqueue.extend(_songs_from_leaf(leaf))

        enqueue_songs(to_enqueue)

    def get_description(self):
        return _("Add tracks to the play queue")

    def get_gicon(self):
        return icons.ComposedIcon("gtk-execute", "media-playback-start")

    def get_icon_name(self):
        return "media-playback-start"


class SongLeaf(Leaf):
    serializable = 1

    def __init__(self, info, name=None):
        """Init with song info
        @info: Song information dictionary
        """
        Leaf.__init__(self, info, name or info["title"])

    def repr_key(self):
        """To distinguish songs by the same name"""
        return (
            self.object["title"],
            self.object["artist"],
            self.object["album"],
        )

    def get_actions(self):
        yield PlayTracks()
        yield Enqueue()
        yield GetFile()

    def get_description(self):
        # TRANS: Song description
        return _("by %(artist)s from %(album)s") % {
            "artist": self.object["artist"],
            "album": self.object["album"],
        }

    def get_icon_name(self):
        return "audio-x-generic"

    def get_text_representation(self):
        return self.name


class GetFile(Action):
    def __init__(self):
        super().__init__(_("Get File"))

    def has_result(self):
        return True

    def activate(self, leaf, iobj=None, ctx=None):
        gfile = Gio.File.new_for_uri(leaf.object["location"])
        try:
            path = gfile.get_path()
        except Exception as exc:
            # On utf-8 decode error
            # FIXME: Unrepresentable path
            raise OperationError(exc) from exc

        if path:
            result = FileLeaf(path)
            if result.is_valid():
                return result

        raise NotAvailableError(str(leaf))


class CollectionSource(Source):
    def __init__(self, leaf):
        Source.__init__(self, str(leaf))
        self.leaf = leaf

    def get_items(self):
        for song in self.leaf.object:
            yield SongLeaf(song)

    def repr_key(self):
        return self.leaf.repr_key()

    def get_description(self):
        return self.leaf.get_description()

    def get_thumbnail(self, width, height):
        return self.leaf.get_thumbnail(width, height)

    def get_gicon(self):
        return self.leaf.get_gicon()

    def get_icon_name(self):
        return self.leaf.get_icon_name()


class TrackCollection(Leaf):
    """A generic track collection leaf, such as one for
    an Album or an Artist
    """

    def __init__(self, info, name):
        """Init with track collection
        @info: Should be a sequence of song information dictionaries
        """
        Leaf.__init__(self, info, name)

    def get_actions(self):
        yield PlayTracks()
        yield Enqueue()

    def has_content(self):
        return True

    def content_source(self, alternate=False):
        return CollectionSource(self)

    def get_icon_name(self):
        return "media-optical"


class AlbumLeaf(TrackCollection):
    def get_description(self):
        artist = None
        for song in self.object:
            if not artist:
                artist = song["artist"]
            elif artist != song["artist"]:
                # TRANS: Multiple artist description "Artist1 et. al. "
                artist = _("%s et. al.") % artist
                break
        # TRANS: Album description "by Artist"
        return _("by %s") % (artist,)

    def _get_thumb_local(self):
        # try local filesystem
        uri = self.object[0]["location"]
        artist = self.object[0]["artist"].lower()
        album = self.object[0]["album"].lower()
        gfile = Gio.File.new_for_uri(uri)
        cdir = gfile.resolve_relative_path("../").get_path()
        # We don't support unicode ATM
        bs_artist_album = " - ".join([artist, album])
        bs_artist_album2 = "-".join([artist, album])
        # " - ".join([us.encode("ascii", "ignore") for us in (artist, album)])
        cover_names = (
            "cover.jpg",
            "album.jpg",
            "albumart.jpg",
            "cover.gif",
            "album.png",
            ".folder.jpg",
            "folder.jpg",
            bs_artist_album + ".jpg",
            bs_artist_album2 + ".jpg",
        )
        try:
            for cover_name in os.listdir(cdir):
                if cover_name.lower() in cover_names:
                    cfile = gfile.resolve_relative_path("../" + cover_name)
                    return cfile.get_path()

        except OSError:
            pretty.print_exc(__name__)

        return None

    def _get_thumb_mediaart(self):
        """old thumb location"""
        ltitle = str(self).lower()
        # ignore the track artist -- use the space fallback
        # hash of ' ' as fallback
        hspace = "7215ee9c7d9dc229d2921a40e899ec5f"
        htitle = md5(_toutf8_lossy(ltitle)).hexdigest()
        hartist = hspace
        cache_name = f"album-{hartist}-{htitle}.jpeg"
        return config.get_cache_file(("media-art", cache_name))

    def get_thumbnail(self, width, height):
        if not hasattr(self, "cover_file"):
            # pylint: disable=attribute-defined-outside-init
            self.cover_file = (
                self._get_thumb_mediaart() or self._get_thumb_local()
            )

        return icons.get_pixbuf_from_file(self.cover_file, width, height)


class ArtistAlbumsSource(CollectionSource):
    def get_items(self):
        albums: dict[str, list[dict[str, ty.Any]]] = defaultdict(list)
        for song in self.leaf.object:
            albums[song["album"]].append(song)

        names = kupferstring.locale_sort(albums.keys())
        names.sort(key=lambda name: albums[name][0]["date"])
        for album in names:
            yield AlbumLeaf(albums[album], album)

    def should_sort_lexically(self):
        return False


class ArtistLeaf(TrackCollection):
    def get_description(self):
        # TRANS: Artist songs collection description
        return _("Tracks by %s") % (str(self),)

    def get_gicon(self):
        return icons.ComposedIcon("media-optical", "system-users")

    def content_source(self, alternate=False):
        if alternate:
            return CollectionSource(self)

        return ArtistAlbumsSource(self)


class RhythmboxAlbumsSource(Source):
    def __init__(self, library):
        Source.__init__(self, _("Albums"))
        self.library = library

    def get_items(self):
        for album in self.library:
            yield AlbumLeaf(self.library[album], album)

    def should_sort_lexically(self):
        return True

    def get_description(self):
        return _("Music albums in Rhythmbox Library")

    def get_gicon(self):
        return icons.ComposedIcon(
            "rhythmbox", "media-optical", emblem_is_fallback=True
        )

    def get_icon_name(self):
        return "rhythmbox"

    def provides(self):
        yield AlbumLeaf


class RhythmboxArtistsSource(Source):
    def __init__(self, library):
        Source.__init__(self, _("Artists"))
        self.library = library

    def get_items(self):
        for artist in self.library:
            yield ArtistLeaf(self.library[artist], artist)

    def should_sort_lexically(self):
        return True

    def get_description(self):
        return _("Music artists in Rhythmbox Library")

    def get_gicon(self):
        return icons.ComposedIcon(
            "rhythmbox", "system-users", emblem_is_fallback=True
        )

    def get_icon_name(self):
        return "rhythmbox"

    def provides(self):
        yield ArtistLeaf


def _locale_sort_artist_album_songs(artists):
    """Locale sort dictionary @artists by Artist, then Album;
    each artist in @artists should already contain songs
    grouped by album and sorted by track number.
    """
    for artist in kupferstring.locale_sort(artists):
        artist_songs = artists[artist]
        albums: dict[str, list[rhythmbox_support.Song]] = defaultdict(list)
        for album, songs in itertools.groupby(
            artist_songs, operator.itemgetter("album")
        ):
            albums[album].extend(songs)

        for album in kupferstring.locale_sort(albums):
            yield from albums[album]


class RhythmboxSongsSource(Source):
    """The whole song library in Leaf representation"""

    def __init__(self, library):
        Source.__init__(self, _("Songs"))
        self.library = library

    def get_items(self):
        for song in _locale_sort_artist_album_songs(self.library):
            yield SongLeaf(song)

    def get_actions(self):
        return ()

    def get_description(self):
        return _("Songs in Rhythmbox library")

    def get_gicon(self):
        return icons.ComposedIcon(
            "rhythmbox", "audio-x-generic", emblem_is_fallback=True
        )

    def provides(self):
        yield SongLeaf


class RhythmboxSource(AppLeafContentMixin, Source, PicklingHelperMixin):
    appleaf_content_id = ("rhythmbox", "org.gnome.Rhythmbox3")
    source_scan_interval: int = 3600

    def __init__(self):
        super().__init__(_("Rhythmbox"))
        self._version = 3
        self._songs = []

    def initialize(self):
        bus = dbus.SessionBus()
        weaklib.dbus_signal_connect_weakly(
            bus,
            "NameOwnerChanged",
            self._name_owner_changed,
            dbus_interface="org.freedesktop.DBus",
            arg0=_BUS_NAME,
        )

    def _name_owner_changed(self, name, old, new):
        if new:
            self.mark_for_update()

    def pickle_prepare(self):
        self.mark_for_update()

    def get_items(self):
        # first try to load songs via dbus
        songs = list(_get_all_songs_via_dbus())
        self._songs = songs = songs or self._songs
        albums = rhythmbox_support.parse_rhythmbox_albums(songs)
        artists = rhythmbox_support.parse_rhythmbox_artists(songs)
        yield ClearQueue()
        artist_source = RhythmboxArtistsSource(artists)
        album_source = RhythmboxAlbumsSource(albums)
        songs_source = RhythmboxSongsSource(artists)
        yield SourceLeaf(artist_source)
        yield SourceLeaf(album_source)
        yield SourceLeaf(songs_source)
        # we use get_leaves here to get sorting etc right
        if __kupfer_settings__["toplevel_artists"]:
            yield from artist_source.get_leaves()

        if __kupfer_settings__["toplevel_albums"]:
            yield from album_source.get_leaves()

        if __kupfer_settings__["toplevel_songs"]:
            yield from songs_source.get_leaves()

    def get_description(self):
        return _("Play and enqueue tracks and browse the music library")

    def get_icon_name(self):
        return "rhythmbox"

    def provides(self):
        yield RunnableLeaf
        yield SourceLeaf
        yield SongLeaf

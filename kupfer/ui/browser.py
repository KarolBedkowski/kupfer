from __future__ import annotations

import io
import itertools
import signal
import sys
import textwrap
import math
from contextlib import suppress
import enum
import typing as ty

import gi
from gi.repository import Gtk, Gdk, GObject
from gi.repository import GLib, Gio, Pango
from gi.repository import GdkPixbuf

try:
    gi.require_version("AppIndicator3", "0.1")
except ValueError:
    AppIndicator3 = None
else:
    from gi.repository import AppIndicator3

import cairo

from kupfer import kupferui
from kupfer import version

from kupfer import scheduler
from kupfer.ui import accelerators
from kupfer.ui import keybindings
from kupfer.ui import listen
from kupfer.ui import uievents
from kupfer.core import data, relevance, learn
from kupfer.core.search import Rankable
from kupfer.core import settings, actionaccel
from kupfer.obj.base import Leaf, Action, KupferObject, AnySource
from kupfer.obj.objects import FileLeaf
from kupfer import icons
from kupfer import interface
from kupfer import pretty
import kupfer.config
import kupfer.environment

ELLIPSIZE_MIDDLE = Pango.EllipsizeMode.MIDDLE

if ty.TYPE_CHECKING:
    _ = str

_escape_table = {
    ord("&"): "&amp;",
    ord("<"): "&lt;",
    ord(">"): "&gt;",
}

AccelFunc = ty.Callable[[], ty.Any]


def tounicode(ustr: ty.AnyStr | None) -> str:
    if isinstance(ustr, str):
        return ustr

    return ustr.decode("UTF-8", "replace")


def _escape_markup_str(mstr: str) -> str:
    """
    Use a simeple homegrown replace table to replace &, <, > with
    entities in @mstr
    """
    return mstr.translate(_escape_table)


def text_direction_is_ltr() -> bool:
    return Gtk.Widget.get_default_direction() != Gtk.TextDirection.RTL


# # NOT IN USE
# def make_rounded_rect(cr, x, y, width, height, radius):
#     """
#     Draws a rounded rectangle with corners of @radius
#     """
#     MPI = math.pi
#     cr.save()

#     cr.move_to(radius, 0)
#     cr.line_to(width-radius,0)
#     cr.arc(width-radius, radius, radius, 3*MPI/2, 2*MPI)
#     cr.line_to(width, height-radius)
#     cr.arc(width-radius, height-radius, radius, 0, MPI/2)
#     cr.line_to(radius, height)
#     cr.arc(radius, height-radius, radius, MPI/2, MPI)
#     cr.line_to(0, radius)
#     cr.arc(radius, radius, radius, MPI, 3*MPI/2)
#     cr.close_path()
#     cr.restore()


def get_glyph_pixbuf(
    text: str,
    size: int,
    center_vert: bool = True,
    color: ty.Optional[tuple[int, int, int]] = None,
) -> GdkPixbuf:
    """Return pixbuf for @text

    if @center_vert, then center completely vertically
    """
    margin = size * 0.1
    ims = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cctx = cairo.Context(ims)

    cctx.move_to(margin, size - margin)
    cctx.set_font_size(size / 2)
    if color is None:
        cctx.set_source_rgba(0, 0, 0, 1)
    else:
        cctx.set_source_rgb(*color)

    cctx.text_path(text)
    x1, y1, x2, y2 = cctx.path_extents()
    skew_horiz = ((size - x2) - x1) / 2.0
    skew_vert = ((size - y2) - y1) / 2.0
    if not center_vert:
        skew_vert = skew_vert * 0.2 - margin * 0.5

    cctx.new_path()
    cctx.move_to(margin + skew_horiz, size - margin + skew_vert)
    cctx.text_path(text)
    cctx.fill()

    ims.flush()
    pngfile = io.BytesIO()
    ims.write_to_png(pngfile)

    loader = GdkPixbuf.PixbufLoader()
    loader.write(pngfile.getvalue())
    loader.close()

    return loader.get_pixbuf()


def _format_match(match: str) -> str:
    return f"<u><b>{_escape_markup_str(match)}</b></u>"


# State Constants
class State(enum.IntEnum):
    WAIT = 1
    MATCH = 2
    NO_MATCH = 3


class LeafModel:
    """A base for a tree view
    With a magic load-on-demand feature.

    self.set_base will set its base iterator
    and self.populate(num) will load @num items into
    the model

    Attributes:
    icon_size
    """

    def __init__(self, aux_info_callback: ty.Callable[[Leaf], str]) -> None:
        """
        First column is always the object -- returned by get_object
        it needs not be specified in columns
        """
        columns = (GObject.TYPE_OBJECT, str, str, str, str)
        self.store = Gtk.ListStore(GObject.TYPE_PYOBJECT, *columns)
        self.object_column = 0
        self.base: ty.Optional[ty.Iterator[Rankable]] = None
        self._setup_columns()
        self.icon_size = 32
        self.aux_info_callback = aux_info_callback

    def __len__(self) -> int:
        return len(self.store)

    def _setup_columns(self):
        self.icon_col = 1
        self.name_col = 2
        self.fav_col = 3
        self.info_col = 4
        self.rank_col = 5

        # only show in debug mode
        show_rank_col = pretty.DEBUG

        # Name and description column
        # Expands to the rest of the space
        name_cell = Gtk.CellRendererText()
        name_cell.set_property("ellipsize", ELLIPSIZE_MIDDLE)
        name_col = Gtk.TreeViewColumn("item", name_cell)
        name_col.set_expand(True)
        name_col.add_attribute(name_cell, "markup", self.name_col)

        fav_cell = Gtk.CellRendererText()
        fav_col = Gtk.TreeViewColumn("fav", fav_cell)
        fav_col.add_attribute(fav_cell, "text", self.fav_col)

        info_cell = Gtk.CellRendererText()
        info_col = Gtk.TreeViewColumn("info", info_cell)
        info_col.add_attribute(info_cell, "text", self.info_col)

        nbr_cell = Gtk.CellRendererText()
        nbr_col = Gtk.TreeViewColumn("rank", nbr_cell)
        nbr_cell.set_property("width-chars", 3)
        nbr_col.add_attribute(nbr_cell, "text", self.rank_col)

        icon_cell = Gtk.CellRendererPixbuf()
        # icon_cell.set_property("height", 32)
        # icon_cell.set_property("width", 32)
        # icon_cell.set_property("stock-size", Gtk.IconSize.LARGE_TOOLBAR)

        icon_col = Gtk.TreeViewColumn("icon", icon_cell)
        icon_col.add_attribute(icon_cell, "pixbuf", self.icon_col)

        self.columns = [
            icon_col,
            name_col,
            fav_col,
            info_col,
        ]
        if show_rank_col:
            self.columns += (nbr_col,)

    def _get_column(self, treepath: ty.Iterable[int], col: int) -> ty.Any:
        store_iter = self.store.get_iter(treepath)
        val = self.store.get_value(store_iter, col)
        return val

    def get_object(self, path: ty.Optional[ty.Iterable[int]]) -> ty.Any:
        if path is None:
            return None

        return self._get_column(path, self.object_column)

    def get_store(self) -> Gtk.ListStore:
        return self.store

    def clear(self) -> None:
        """Clear the model and reset its base"""
        self.store.clear()
        self.base = None

    def set_base(self, baseiter: ty.Iterable[Rankable]) -> None:
        self.base = iter(baseiter)

    def populate(
        self, num: ty.Optional[int] = None
    ) -> ty.Optional[KupferObject]:
        """
        populate model with num items from its base
        and return first item inserted
        if num is none, insert everything

        """
        if not self.base:
            return None

        # FIXME: there is now path for num=None, added this; check
        iterator: ty.Iterable[Rankable] = self.base
        if num:
            iterator = itertools.islice(self.base, num)

        first = None
        for item in iterator:
            self.add(item)
            if not first:
                first = item.object

        # first.object is a leaf
        return first

    def _get_row(
        self, rankable: Rankable
    ) -> tuple[Rankable, ty.Optional[GdkPixbuf], str, str, str, str]:
        """Use the UI description functions get_*
        to initialize @rankable into the model
        """
        leaf, rank = rankable.object, rankable.rank
        icon = self.get_icon(leaf)
        markup = self.get_label_markup(rankable)
        fav = self.get_fav(leaf)
        info = self.get_aux_info(leaf)
        rank_str = self.get_rank_str(rank)
        return (rankable, icon, markup, fav, info, rank_str)

    def add(self, rankable: Rankable) -> None:
        self.store.append(self._get_row(rankable))

    def add_first(self, rankable: Rankable) -> None:
        self.store.prepend(self._get_row(rankable))

    def get_icon(self, leaf: KupferObject) -> ty.Optional[GdkPixbuf]:
        if (size := self.icon_size) > 8:
            return leaf.get_thumbnail(size, size) or leaf.get_pixbuf(size)

        return None

    def get_label_markup(self, rankable: Rankable) -> str:
        leaf = rankable.object
        # Here we use the items real name
        # Previously we used the alias that was matched,
        # but it can be too confusing or ugly
        name = _escape_markup_str(str(leaf))
        if desc := _escape_markup_str(leaf.get_description() or ""):
            text = f"{name}\n<small>{desc}</small>"
        else:
            text = f"{name}"

        return text

    def get_fav(self, leaf: KupferObject) -> str:
        # fav: display star if it's a favourite
        if learn.is_favorite(leaf):
            return "\N{BLACK STAR}"

        return ""

    def get_aux_info(self, leaf: KupferObject) -> str:
        # For objects: Show arrow if it has content
        # For actions: Show accelerator
        #
        if self.aux_info_callback is not None:
            return self.aux_info_callback(leaf)

        return ""

    def get_rank_str(self, rank: ty.Optional[float]) -> str:
        # Display rank empty instead of 0 since it looks better
        return str(int(rank)) if rank else ""


def _dim_icon(icon: ty.Optional[GdkPixbuf]) -> ty.Optional[GdkPixbuf]:
    if not icon:
        return icon

    dim_icon = icon.copy()
    dim_icon.fill(0)
    icon.composite(
        dim_icon,
        0,
        0,
        icon.get_width(),
        icon.get_height(),
        0,
        0,
        1.0,
        1.0,
        GdkPixbuf.InterpType.NEAREST,
        127,
    )
    return dim_icon


class MatchViewOwner(pretty.OutputMixin):
    """
    Owner of the widget for displaying name, icon and name underlining (if
    applicable) of the current match.
    """

    def __init__(self):
        # object attributes
        self.label_char_width = 25
        self.preedit_char_width = 5
        self.match_state: State = State.WAIT

        self.object_stack = []

        # finally build widget
        self.build_widget()
        self.cur_icon: ty.Optional[GdkPixbuf] = None
        self.cur_text: ty.Optional[str] = None
        self.cur_match: ty.Optional[str] = None
        self._icon_size: ty.Optional[int] = None
        self._read_icon_size()

    @property
    def icon_size(self) -> int:
        assert self._icon_size
        return self._icon_size

    def _icon_size_changed(
        self,
        setctl: settings.SettingsController,
        section: ty.Optional[str],
        key: ty.Optional[str],
        value: ty.Any,
    ) -> None:
        self._icon_size = setctl.get_config_int(
            "Appearance", "icon_large_size"
        )

    def _read_icon_size(self, *_args: ty.Any) -> None:
        setctl = settings.GetSettingsController()
        setctl.connect(
            "value-changed::appearance.icon_large_size",
            self._icon_size_changed,
        )
        self._icon_size_changed(setctl, None, None, None)

    def build_widget(self) -> None:
        """
        Core initalization method that builds the widget
        """
        self.label = Gtk.Label.new("<match>")
        self.label.set_single_line_mode(True)
        self.label.set_width_chars(self.label_char_width)
        self.label.set_max_width_chars(self.label_char_width)
        self.label.set_ellipsize(ELLIPSIZE_MIDDLE)
        self.icon_view = Gtk.Image()

        # infobox: icon and match name
        icon_align = Gtk.Alignment.new(0.5, 0.5, 0, 0)
        icon_align.set_property("top-padding", 5)
        icon_align.add(self.icon_view)
        infobox = Gtk.HBox()
        infobox.pack_start(icon_align, True, True, 0)
        box = Gtk.VBox()
        box.pack_start(infobox, True, False, 0)
        self._editbox = Gtk.HBox()
        self._editbox.pack_start(self.label, True, True, 0)
        box.pack_start(self._editbox, False, False, 3)
        self.event_box = Gtk.EventBox()
        self.event_box.add(box)
        self.event_box.get_style_context().add_class("matchview")
        self.event_box.show_all()
        self._child = self.event_box

    def widget(self) -> Gtk.Widget:
        """
        Return the corresponding Widget
        """
        return self._child

    def _render_composed_icon(
        self, base: GdkPixbuf, pixbufs: list[GdkPixbuf], small_size: int
    ) -> GdkPixbuf:
        """
        Render the main selection + a string of objects on the stack.

        Scale the main image into the upper portion, leaving a clear
        strip at the bottom where we line up the small icons.

        @base: main selection pixbuf
        @pixbufs: icons of the object stack, in final (small) size
        @small_size: the size of the small icons
        """
        size = self.icon_size
        assert size
        base_scale = min(
            (size - small_size) * 1.0 / base.get_height(),
            size * 1.0 / base.get_width(),
        )
        new_sz_x = int(base_scale * base.get_width())
        new_sz_y = int(base_scale * base.get_height())
        if not base.get_has_alpha():
            base = base.add_alpha(False, 0, 0, 0)

        destbuf = base.scale_simple(size, size, GdkPixbuf.InterpType.NEAREST)
        destbuf.fill(0x00000000)
        # Align in the middle of the area
        offset_x = (size - new_sz_x) / 2
        offset_y = ((size - small_size) - new_sz_y) / 2
        base.composite(
            destbuf,
            offset_x,
            offset_y,
            new_sz_x,
            new_sz_y,
            offset_x,
            offset_y,
            base_scale,
            base_scale,
            GdkPixbuf.InterpType.BILINEAR,
            255,
        )

        # @fr is the scale compared to the destination pixbuf
        frac = small_size * 1.0 / size
        dest_y = offset_y = int((1 - frac) * size)
        n_small = size // small_size
        for idx, pbuf in enumerate(pixbufs[-n_small:]):
            dest_x = offset_x = int(frac * size) * idx
            pbuf.copy_area(
                0, 0, small_size, small_size, destbuf, dest_x, dest_y
            )

        return destbuf

    def update_match(self) -> None:
        """
        Update interface to display the currently selected match
        """
        # update icon
        if icon := self.cur_icon:
            if self.match_state is State.NO_MATCH:
                icon = _dim_icon(icon)

            if icon and self.object_stack:
                small_max = 16
                small_size = 16
                pixbufs = [
                    o.get_pixbuf(small_size)
                    for o in self.object_stack[-small_max:]
                ]
                icon = self._render_composed_icon(icon, pixbufs, small_size)

            self.icon_view.set_from_pixbuf(icon)
        else:
            self.icon_view.clear()
            self.icon_view.set_pixel_size(self.icon_size)

        if not self.cur_text:
            self.label.set_text("")
            return

        if not self.cur_match:
            if self.match_state is not State.MATCH:
                # Allow markup in the text string if we have no match
                self.label.set_markup(self.cur_text)
            else:
                self.label.set_text(self.cur_text)
            return

        # update the text label
        text = str(self.cur_text)
        key = str(self.cur_match).lower()

        markup = relevance.formatCommonSubstrings(
            text,
            key,
            format_clean=_escape_markup_str,
            format_match=_format_match,
        )

        self.label.set_markup(markup)

    def set_object(
        self, text: ty.Optional[str], icon: GdkPixbuf, update: bool = True
    ) -> None:
        self.cur_text = text
        self.cur_icon = icon
        if update:
            self.update_match()

    def set_match(
        self,
        match: ty.Optional[str] = None,
        state: ty.Optional[State] = None,
        update: bool = True,
    ) -> None:
        self.cur_match = match
        if state:
            self.match_state = state
        else:
            self.match_state = (
                State.MATCH if self.cur_match is not None else State.NO_MATCH
            )

        if update:
            self.update_match()

    def set_match_state(
        self,
        text: ty.Optional[str],
        icon: GdkPixbuf,
        match: ty.Optional[str] = None,
        state: ty.Optional[State] = None,
        update: bool = True,
    ) -> None:
        self.set_object(text, icon, update=False)
        self.set_match(match, state, update=False)
        if update:
            self.update_match()

    def set_match_text(
        self, text: ty.Optional[str], update: bool = True
    ) -> None:
        self.cur_match = text
        if update:
            self.update_match()

    def expand_preedit(self, preedit: Gtk.Entry) -> None:
        new_label_width = self.label_char_width - self.preedit_char_width
        self.label.set_width_chars(new_label_width)
        preedit.set_width_chars(self.preedit_char_width)
        preedit.get_style_context().remove_class(PREEDIT_HIDDEN_CLASS)

    def shrink_preedit(self, preedit: Gtk.Entry) -> None:
        self.label.set_width_chars(self.label_char_width)
        preedit.set_width_chars(0)
        preedit.get_style_context().add_class(PREEDIT_HIDDEN_CLASS)

    def inject_preedit(self, preedit: ty.Optional[Gtk.Entry]) -> None:
        """
        @preedit: Widget to be injected or None
        """
        if preedit:
            if old_parent := preedit.get_parent():
                old_parent.remove(preedit)

            self.shrink_preedit(preedit)
            self._editbox.pack_start(preedit, False, True, 0)
            # selectedc = self.style.dark[Gtk.StateType.SELECTED]
            # preedit.modify_bg(Gtk.StateType.SELECTED, selectedc)
            preedit.show()
            preedit.grab_focus()
            return

        self.label.set_width_chars(self.label_char_width)
        self.label.set_alignment(0.5, 0.5)


# number rows to skip when press PgUp/PgDown
_PAGE_STEP: ty.Final = 7
_SHOW_MORE: ty.Final = 10


class Search(GObject.GObject, pretty.OutputMixin):
    """
    Owner of a widget for displaying search results (using match view),
    keeping current search result list and its display.

    Signals
    * cursor-changed: def callback(widget, selection)
        called with new selected (represented) object or None
    * activate: def callback(widget, selection)
        called with activated leaf, when the widget is activated
        by double-click in table
    * table-event: def callback(widget, table, event)
        called when the user types in the table
    """

    # minimal length of list is MULT * icon size small
    LIST_MIN_MULT = 8
    __gtype_name__ = "Search"

    def __init__(self):
        GObject.GObject.__init__(self)
        # object attributes
        self.model = LeafModel(self.get_aux_info)
        self.match = None
        self.match_state = State.WAIT
        self.text: ty.Optional[str] = ""
        self.source: ty.Optional[AnySource] = None
        self._old_win_position = None
        self._has_search_result = False
        self._initialized = False
        # finally build widget
        self.build_widget()
        self._icon_size: int = 0
        self._icon_size_small: int = 0
        self._read_icon_size()
        self.setup_empty()

    def get_aux_info(self, leaf: KupferObject) -> str:
        # Return content for the aux info column
        return ""

    def set_name(self, name: str) -> None:
        """
        Set the name of the Search's widget

        name: str
        """
        self._child.set_name(name)

    def set_state(self, state: Gtk.StateType) -> None:
        self._child.set_state(state)

    def show(self) -> None:
        self._child.show()

    def hide(self) -> None:
        self._child.hide()

    def set_visible(self, flag: bool) -> None:
        if flag:
            self.show()
        else:
            self.hide()

    @property
    def icon_size(self) -> int:
        assert self._icon_size
        return self._icon_size

    def _icon_size_changed(
        self,
        setctl: settings.SettingsController,
        section: ty.Optional[str],
        key: ty.Optional[str],
        value: ty.Any,
    ) -> None:
        self._icon_size = setctl.get_config_int(
            "Appearance", "icon_large_size"
        )
        self._icon_size_small = setctl.get_config_int(
            "Appearance", "icon_small_size"
        )
        self.model.icon_size = self._icon_size_small

    def _read_icon_size(self, *args: ty.Any) -> None:
        setctl = settings.GetSettingsController()
        setctl.connect(
            "value-changed::appearance.icon_large_size",
            self._icon_size_changed,
        )
        setctl.connect(
            "value-changed::appearance.icon_small_size",
            self._icon_size_changed,
        )
        self._icon_size_changed(setctl, None, None, None)

    def build_widget(self) -> None:
        """
        Core initalization method that builds the widget
        """
        self.match_view = MatchViewOwner()

        self.table = Gtk.TreeView.new_with_model(self.model.get_store())
        self.table.set_name("kupfer-list-view")
        self.table.set_headers_visible(False)
        self.table.set_property("enable-search", False)

        for col in self.model.columns:
            self.table.append_column(col)

        self.table.connect("row-activated", self._row_activated)
        self.table.connect("cursor-changed", self._cursor_changed)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self.scroller.add(self.table)
        vscroll = self.scroller.get_vscrollbar()
        vscroll.connect("change-value", self._table_scroll_changed)

        self.list_window = Gtk.Window.new(Gtk.WindowType.POPUP)
        self.list_window.set_name("kupfer-list")

        self.list_window.add(self.scroller)
        self.scroller.show_all()
        self._child = self.match_view.widget()

    def widget(self) -> Gtk.Widget:
        """
        Return the corresponding Widget
        """
        return self._child

    def get_current(self) -> ty.Optional[KupferObject]:
        """
        return current selection
        """
        return self.match

    def set_object_stack(self, stack: list[Leaf]) -> None:
        self.match_view.object_stack = stack
        self.match_view.update_match()

    def set_source(self, source: AnySource) -> None:
        """Set current source (to get icon, name etc)"""
        self.source = source

    def get_match_state(self) -> State:
        return self.match_state

    def get_match_text(self) -> str | None:
        return self.text

    def get_table_visible(self) -> bool:
        return self.list_window.get_property("visible")  # type: ignore

    def hide_table(self) -> None:
        if self.get_table_visible():
            self.list_window.hide()

    def _show_table(self) -> None:
        setctl = settings.GetSettingsController()
        list_maxheight = setctl.get_config_int("Appearance", "list_height")
        if list_maxheight < self._icon_size_small * self.LIST_MIN_MULT:
            list_maxheight = self.LIST_MIN_MULT * self._icon_size_small

        widget = self.widget()
        window = widget.get_toplevel()

        win_width, win_height = window.get_size()

        parent_padding_x = WINDOW_BORDER_WIDTH

        self_x, _self_y = widget.translate_coordinates(window, 0, 0)
        pos_x, pos_y = window.get_position()
        self_width = widget.size_request().width
        self_end = self_x + self_width

        sub_x = pos_x
        sub_y = pos_y + win_height
        # to stop a warning
        _dummy_sr = self.table.size_request()

        # FIXME: Adapt list length
        subwin_height = list_maxheight
        subwin_width = self_width * 2 + parent_padding_x
        if not text_direction_is_ltr():
            sub_x += win_width - subwin_width + self_x

        if self_end < subwin_width:
            # Place snugly to left
            sub_x = pos_x + self_x
        else:
            # Place aligned with right side of window
            sub_x = pos_x + self_end - subwin_width

        self.list_window.move(sub_x, sub_y)
        self.list_window.resize(subwin_width, subwin_height)

        self.list_window.set_transient_for(window)
        self.list_window.set_property("focus-on-map", False)
        self.list_window.show()
        self._old_win_position = pos_x, pos_y

    def show_table(self) -> None:
        self.go_down(True)

    def show_table_quirk(self) -> None:
        "Show table after being hidden in the same event"
        # KWin bugs out if we hide and show the table during the same gtk event
        # issue #47
        if kupfer.environment.is_kwin():
            GLib.idle_add(self.show_table)
        else:
            self.show_table()

    def _table_scroll_changed(
        self, scrollbar: Gtk.Scrollbar, _scroll_type: ty.Any, value: int
    ) -> None:
        """When the scrollbar changes due to user interaction"""
        # page size: size of currently visible area
        adj = scrollbar.get_adjustment()
        upper = adj.get_property("upper")
        page_size = adj.get_property("page-size")

        if value + page_size >= upper:
            self.populate(_SHOW_MORE)

    # table methods
    def _table_set_cursor_at_row(self, row: int) -> None:
        self.table.set_cursor((row,))

    def _table_current_row(self) -> ty.Optional[int]:
        path, _col = self.table.get_cursor()
        return path[0] if path else None

    def go_up(self, rows_count: int = 1) -> None:
        """
        Upwards in the table
        """
        # go up, simply. close table if we go up from row 0
        path, _col = self.table.get_cursor()
        if not path:
            return

        if (row := path[0]) >= 1:
            self._table_set_cursor_at_row(row - min(rows_count, row))
        else:
            self.hide_table()

    def go_down(
        self, force: bool = False, rows_count: int = 1, show_table: bool = True
    ) -> None:
        """
        Down in the table
        """
        table_visible = self.get_table_visible()
        # if no data is loaded (frex viewing catalog), load
        # if too little data is loaded, try load more
        if len(self.model) <= 1:
            self.populate(_SHOW_MORE)

        if len(self.model) >= 1:
            path, _col = self.table.get_cursor()
            if path:
                row = path[0]
                if len(self.model) - rows_count <= row:
                    self.populate(_SHOW_MORE)
                # go down only if table is visible
                if table_visible:
                    if step := min(len(self.model) - row - 1, rows_count):
                        self._table_set_cursor_at_row(row + step)
            else:
                self._table_set_cursor_at_row(0)

            if show_table:
                self._show_table()

        if force and show_table:
            self._show_table()

    def go_page_up(self) -> None:
        """move list one page up"""
        self.go_up(_PAGE_STEP)

    def go_page_down(self) -> None:
        """move list one page down"""
        self.go_down(rows_count=_PAGE_STEP)

    def go_first(self) -> None:
        """Rewind to first item"""
        if self.get_table_visible():
            self._table_set_cursor_at_row(0)

    def _window_config(
        self, widget: Gtk.Widget, event: Gdk.EventConfigure
    ) -> None:
        """
        When the window moves
        """
        winpos = event.x, event.y
        # only hide on move, not resize
        # set old win position in _show_table
        if self.get_table_visible() and winpos != self._old_win_position:
            self.hide_table()
            GLib.timeout_add(300, self._show_table)

    def _window_hidden(self, window: Gtk.Widget) -> None:
        """
        Window changed hid
        """
        self.hide_table()

    def _row_activated(
        self, treeview: Gtk.TreeView, path: ty.Any, col: ty.Any
    ) -> None:
        obj = self.get_current()
        self.emit("activate", obj)

    def _cursor_changed(self, treeview: Gtk.TreeView) -> None:
        path, _col = treeview.get_cursor()
        match = self.model.get_object(path)
        self._set_match(match)

    def _set_match(self, rankable: ty.Optional[Rankable] = None) -> None:
        """
        Set the currently selected (represented) object, either as
        @rankable or KupferObject @obj

        Emits cursor-changed
        """
        self.match = rankable.object if rankable else None
        self.emit("cursor-changed", self.match)
        if self.match:
            match_text = rankable.value if rankable else None
            self.match_state = State.MATCH
            pbuf = self.match.get_thumbnail(
                self.icon_size * 4 // 3, self.icon_size
            ) or self.match.get_pixbuf(self.icon_size)
            self.match_view.set_match_state(
                match_text, pbuf, match=self.text, state=self.match_state
            )

    def set_match_plain(self, obj: Rankable) -> None:
        """Set match to object @obj, without search or matches"""
        self.text = None
        self._set_match(obj)
        self.model.add_first(obj)
        self._table_set_cursor_at_row(0)

    def relax_match(self) -> None:
        """Remove match text highlight"""
        self.match_view.set_match_text(None)
        self.text = None

    def has_result(self) -> bool:
        """A search with explicit search term is active"""
        return self._has_search_result

    def is_showing_result(self) -> bool:
        """Showing search result:
        A search with explicit search term is active,
        and the result list is shown.
        """
        return self._has_search_result and self.get_table_visible()

    def update_match(
        self,
        key: str | None,
        matchrankable: ty.Optional[Rankable],
        matches: ty.Iterable[Rankable],
    ) -> None:
        """
        @matchrankable: Rankable first match or None
        @matches: Iterable to rest of matches
        """
        self._has_search_result = bool(key)
        self.model.clear()
        self.text = key
        if not matchrankable:
            self._set_match(None)
            self.handle_no_matches(empty=not key)
            return

        self._set_match(matchrankable)
        self.model.set_base(iter(matches))
        if not self.model and self.get_table_visible():
            self.go_down()

    def reset(self) -> None:
        self._has_search_result = False
        self._initialized = True
        self.model.clear()
        self.setup_empty()

    def setup_empty(self) -> None:
        self.match_state = State.NO_MATCH
        self.match_view.set_match_state("No match", None, state=State.NO_MATCH)
        self.relax_match()

    def populate(self, num: int) -> ty.Optional[KupferObject]:
        """populate model with num items"""
        return self.model.populate(num)

    def handle_no_matches(self, empty: bool = False) -> None:
        """if @empty, there were no matches to find"""
        assert hasattr(self, "get_nomatch_name_icon")
        name, icon = self.get_nomatch_name_icon(  # pylint: disable=no-member
            empty=empty
        )
        self.match_state = State.NO_MATCH
        self.match_view.set_match_state(name, icon, state=State.NO_MATCH)


# Take care of GObject things to set up the Search class
GObject.type_register(Search)
GObject.signal_new(
    "activate",
    Search,
    GObject.SignalFlags.RUN_LAST,
    GObject.TYPE_BOOLEAN,
    (GObject.TYPE_PYOBJECT,),
)
GObject.signal_new(
    "cursor-changed",
    Search,
    GObject.SignalFlags.RUN_LAST,
    GObject.TYPE_BOOLEAN,
    (GObject.TYPE_PYOBJECT,),
)


class LeafSearch(Search):
    """
    Customize for leaves search
    """

    def get_aux_info(self, leaf: KupferObject) -> str:
        if hasattr(leaf, "has_content") and leaf.has_content():  # type: ignore
            if text_direction_is_ltr():
                return "\N{BLACK RIGHT-POINTING SMALL TRIANGLE} "

            return "\N{BLACK LEFT-POINTING SMALL TRIANGLE} "

        return ""

    def _get_pbuf(self, src: AnySource) -> ty.Optional[GdkPixbuf]:
        return src.get_thumbnail(
            self.icon_size * 4 // 3, self.icon_size
        ) or src.get_pixbuf(self.icon_size)

    def get_nomatch_name_icon(self, empty: bool) -> tuple[str, GdkPixbuf]:
        if empty and self.source:
            return (
                f"<i>{_escape_markup_str(self.source.get_empty_text())}</i>",
                self._get_pbuf(self.source),
            )

        if self.source:
            assert self.text
            return (
                _('No matches in %(src)s for "%(query)s"')
                % {
                    "src": f"<i>{_escape_markup_str(str(self.source))}</i>",
                    "query": _escape_markup_str(self.text),
                },
                self._get_pbuf(self.source),
            )

        return _("No matches"), icons.get_icon_for_name(
            "kupfer-object", self.icon_size
        )

    def setup_empty(self) -> None:
        if self.source:
            icon = self._get_pbuf(self.source)
            msg = self.source.get_search_text()
        else:
            icon = None
            msg = _("Type to search")

        title = f"<i>{msg}</i>"

        self._set_match(None)
        self.match_state = State.WAIT
        self.match_view.set_match_state(title, icon, state=State.WAIT)


def _accel_for_action(
    action: Action, action_accel_config: actionaccel.AccelConfig
) -> ty.Optional[str]:
    if action_accel_config is None:
        return None

    if (config_accel := action_accel_config.get(action)) is not None:
        return config_accel

    return action.action_accelerator


class ActionSearch(Search):
    """
    Customization for Actions

    Attributes:

    accel_modifier
    """

    def __init__(self) -> None:
        super().__init__()
        self.action_accel_config: ty.Optional[actionaccel.AccelConfig] = None
        self.accel_modifier: Gdk.ModifierType = Gdk.ModifierType.MOD1_MASK

    def lazy_setup(self) -> None:
        setctl = settings.GetSettingsController()
        setctl.connect(
            "value-changed::kupfer.action_accelerator_modifer",
            self._on_modifier_changed,
        )
        self._read_accel_modifer(setctl.get_action_accelerator_modifer())

    def _on_modifier_changed(
        self,
        setctl: settings.SettingsController,
        section: ty.Any,
        key: ty.Any,
        value: str,
    ) -> None:
        self._read_accel_modifer(value)

    def _read_accel_modifer(self, value: str) -> None:
        if value == "alt":
            self.accel_modifier = Gdk.ModifierType.MOD1_MASK
        elif value == "ctrl":
            self.accel_modifier = Gdk.ModifierType.CONTROL_MASK
        else:
            pretty.print_error("Unknown modifier key", value)

    def get_aux_info(self, leaf: Action) -> str:
        if not self.action_accel_config:
            return ""

        accel = _accel_for_action(leaf, self.action_accel_config)
        if accel:
            keyv, mods = Gtk.accelerator_parse(accel)
            if mods != 0:
                self.output_error("Ignoring action accelerator mod", mods)

            return Gtk.accelerator_get_label(
                keyv, self.accel_modifier
            )  # type:ignore

        return ""

    def get_nomatch_name_icon(
        self, empty: bool = False
    ) -> tuple[str, GdkPixbuf]:
        # don't look up icons too early
        if not self._initialized:
            return ("", None)

        if self.text:
            msg = _('No action matches "%s"') % _escape_markup_str(self.text)
            title = f"<i>{msg}</i>"
        else:
            title = ""

        return title, icons.get_icon_for_name("kupfer-execute", self.icon_size)

    def setup_empty(self) -> None:
        self.handle_no_matches()
        self.hide_table()

    def select_action(self, accel: str) -> tuple[bool, bool]:
        """
        Find and select the next action with accelerator key @accel

        Return pair of bool success, can activate
        """
        assert self.action_accel_config

        if self.get_match_state() == State.NO_MATCH:
            return False, False

        i = self._table_current_row() or 0
        self.populate(1)
        if not self.model:
            return False, False

        start_row = i
        while True:
            cur = self.model.get_object((i,))
            self.output_debug("Looking at action", repr(cur.object))
            action = cur.object

            if _accel_for_action(action, self.action_accel_config) == accel:
                self._table_set_cursor_at_row(i)
                return True, not action.requires_object()

            self.populate(1)
            i += 1
            i %= len(self.model)
            if i == start_row:
                break

        return False, False


def _trunc_long_str(instr: ty.Any) -> str:
    "truncate long object names"
    ustr = str(instr)
    return ustr[:25] + "…" if len(ustr) > 27 else ustr


_SLOW_INPUT_INTERVAL = 2
_KEY_PRESS_INTERVAL = 0.3
_KEY_PRESS_REPEAT_THRESHOLD = 0.02


class Interface(GObject.GObject, pretty.OutputMixin):
    """
    Controller object that controls the input and
    the state (current active) search object/widget

    Signals:
    * cancelled: def callback(controller)
        escape was typed
    """

    __gtype_name__ = "Interface"

    def __init__(
        self, controller: data.DataController, window: Gtk.Window
    ) -> None:
        """
        @controller: DataController
        @window: toplevel window
        """
        GObject.GObject.__init__(self)

        self.search = LeafSearch()
        self.action = ActionSearch()
        self.third = LeafSearch()
        self.entry = Gtk.Entry()
        self.label = Gtk.Label()
        self.preedit = Gtk.Entry()
        self.search.set_name("kupfer-object-pane")
        self.action.set_name("kupfer-action-pane")
        self.third.set_name("kupfer-indirect-object-pane")
        ## make sure we lose the preedit focus ring
        self.preedit.set_name("kupfer-preedit")

        self.current: ty.Optional[Search] = None

        self._widget: ty.Optional[Gtk.Widget] = None
        self._ui_transition_timer = scheduler.Timer()
        self._pane_three_is_visible = False
        self._is_text_mode = False
        self._latest_input_timer = scheduler.Timer()
        # self._key_press_time = None
        # self._key_repeat_key = None  # TODO: check; not set
        # self._key_repeat_active = False  # TODO: check: not set
        self._reset_to_toplevel = False
        self._reset_when_back = False
        self.entry.connect("realize", self._entry_realized)
        self.preedit.set_has_frame(False)
        self.preedit.set_width_chars(0)
        self.preedit.set_alignment(1)
        self._preedit_text = ""

        self.label.set_width_chars(50)
        self.label.set_max_width_chars(50)
        self.label.set_single_line_mode(True)
        self.label.set_ellipsize(ELLIPSIZE_MIDDLE)
        self.label.set_name("kupfer-description")

        self.switch_to_source_init()
        self.entry.connect("changed", self._changed)
        self.preedit.connect("insert-text", self._preedit_insert_text)
        self.preedit.connect("draw", self._preedit_draw)
        self.preedit.connect("preedit-changed", self._preedit_im_changed)
        for widget in (self.entry, self.preedit):
            widget.connect("activate", self._activate, None)
            widget.connect("key-press-event", self._entry_key_press)
            widget.connect("key-release-event", self._entry_key_release)
            widget.connect("copy-clipboard", self._entry_copy_clipboard)
            widget.connect("cut-clipboard", self._entry_cut_clipboard)
            widget.connect("paste-clipboard", self._entry_paste_clipboard)

        # set up panewidget => self signals
        # as well as window => panewidgets
        for widget_owner in (self.search, self.action, self.third):
            widget = widget_owner.widget()
            widget_owner.connect("activate", self._activate)
            widget_owner.connect("cursor-changed", self._selection_changed)
            widget.connect("button-press-event", self._panewidget_button_press)
            # window signals
            window.connect("configure-event", widget_owner._window_config)
            window.connect("hide", widget_owner._window_hidden)

        self.data_controller = controller
        self.data_controller.connect("search-result", self._search_result)
        self.data_controller.connect("source-changed", self._new_source)
        self.data_controller.connect("pane-reset", self._pane_reset)
        self.data_controller.connect("mode-changed", self._show_hide_third)
        self.data_controller.connect(
            "object-stack-changed", self._object_stack_changed
        )
        self.widget_to_pane = {
            id(self.search): data.PaneSel.SOURCE,
            id(self.action): data.PaneSel.ACTION,
            id(self.third): data.PaneSel.OBJECT,
        }
        self.pane_to_widget = {
            data.PaneSel.SOURCE: self.search,
            data.PaneSel.ACTION: self.action,
            data.PaneSel.OBJECT: self.third,
        }
        # Setup keyval mapping
        keys = (
            "Up",
            "Down",
            "Right",
            "Left",
            "Tab",
            "ISO_Left_Tab",
            "BackSpace",
            "Escape",
            "Delete",
            "space",
            "Page_Up",
            "Page_Down",
            "Home",
            "End",
            "Return",
        )
        self.key_book = {k: Gdk.keyval_from_name(k) for k in keys}
        if not text_direction_is_ltr():
            # for RTL languages, simply swap the meaning of Left and Right
            # (for keybindings!)
            D = self.key_book
            D["Left"], D["Right"] = D["Right"], D["Left"]

        self.keys_sensible = set(self.key_book.values())
        self.action_accel_config = actionaccel.AccelConfig()
        self.search.reset()

    def get_widget(self) -> Gtk.Widget:
        """Return a Widget containing the whole Interface"""
        if self._widget:
            return self._widget

        box = Gtk.HBox()
        box.pack_start(self.search.widget(), True, True, 3)
        box.pack_start(self.action.widget(), True, True, 3)
        box.pack_start(self.third.widget(), True, True, 3)
        vbox = Gtk.VBox()
        vbox.pack_start(box, True, True, 0)

        label_align = Gtk.Alignment.new(0.5, 1, 0, 0)
        label_align.set_property("top-padding", 3)
        label_align.add(self.label)
        vbox.pack_start(label_align, False, False, 0)
        vbox.pack_start(self.entry, False, False, 0)
        vbox.show_all()
        self.third.hide()
        self._widget = vbox
        return vbox

    def lazy_setup(self) -> None:
        def validate(keystr):
            keyv, mod = Gtk.accelerator_parse(keystr)
            return (
                mod == 0
                and keyv != 0
                and Gtk.accelerator_valid(
                    keyv, Gdk.ModifierType.MOD1_MASK
                )  # pylint: disable=no-member
            )

        self.action_accel_config.load(validate)
        self.action.action_accel_config = self.action_accel_config
        self.action.lazy_setup()
        self.output_debug("Finished lazy_setup")

    def save_config(self) -> None:
        self.action_accel_config.store()
        self.output_debug("Finished save_config")

    def _entry_realized(self, widget: Gtk.Widget) -> None:
        self.update_text_mode()

    def _entry_key_release(self, entry, event):
        return
        # check for key repeat activation (disabled)
        # FIXME: check; not used;
        # if self._key_repeat_key == event.keyval:
        #     if self._key_repeat_active:
        #         self.activate()

        #     self._key_repeat_key = None
        #     self._key_repeat_active = False
        #     self._update_active()

    def _entry_key_press(self, entry: Gtk.Entry, event: Gdk.EventKey) -> bool:
        """
        Intercept arrow keys and manipulate table
        without losing focus from entry field
        """
        assert self.current is not None
        direct_text_key = Gdk.keyval_from_name("period")
        init_text_keys = list(
            map(Gdk.keyval_from_name, ("slash", "equal", "question"))
        )
        init_text_keys.append(direct_text_key)
        event_state = event.get_state()
        # translate keys properly
        (
            _was_bound,
            keyv,
            _egroup,
            _level,
            consumed,
        ) = Gdk.Keymap.get_default().translate_keyboard_state(
            event.hardware_keycode, event_state, event.group
        )
        all_modifiers = Gtk.accelerator_get_default_mod_mask()
        shift_mask = (
            event_state & all_modifiers
        ) == Gdk.ModifierType.SHIFT_MASK
        event_state &= all_modifiers & ~consumed

        # curtime = time.time()
        self._reset_input_timer()

        setctl = settings.GetSettingsController()
        # process accelerators
        for action, accel in setctl.get_accelerators().items():
            akeyv, amodf = Gtk.accelerator_parse(accel)
            if akeyv and akeyv == keyv and amodf == event_state:
                if action_method := getattr(self, action, None):
                    action_method()
                else:
                    pretty.print_error(__name__, f"Action invalid '{action}'")

                return True

        # look for action accelerators
        if event_state == self.action.accel_modifier:
            keystr = Gtk.accelerator_name(keyv, 0)
            if self.action_accelerator(keystr):
                return True

        if self._preedit_text:
            return False

        key_book = self.key_book
        use_command_keys = setctl.get_use_command_keys()
        has_selection = self.current.get_match_state() == State.MATCH
        if not self._is_text_mode and use_command_keys:
            # translate extra commands to normal commands here
            # and remember skipped chars
            if keyv == key_book["space"]:
                keyv = key_book["Up" if shift_mask else "Down"]

            elif keyv == ord("/") and has_selection:
                keyv = key_book["Right"]

            elif keyv == ord(",") and has_selection:
                if self.comma_trick():
                    return True

            elif keyv in init_text_keys:
                if self.try_enable_text_mode():
                    # swallow if it is the direct key
                    swallow: bool = keyv == direct_text_key
                    return swallow

        if self._is_text_mode and keyv in (
            key_book["Left"],
            key_book["Right"],
            key_book["Home"],
            key_book["End"],
        ):
            # pass these through in text mode
            # except on → at the end of the input
            cursor_position = self.entry.get_property("cursor-position")
            if (
                keyv != key_book["Right"]
                or cursor_position == 0
                or cursor_position != self.entry.get_text_length()
            ):
                return False

        # disabled  repeat-key activation and shift-to-action selection
        # check for repeated key activation
        # """
        # if ((not text_mode) and self._key_repeat_key == keyv and
        #         keyv not in self.keys_sensible and
        #         curtime - self._key_press_time > _KEY_PRESS_REPEAT_THRESHOLD):
        #     if curtime - self._key_press_time > _KEY_PRESS_INTERVAL:
        #         self._key_repeat_active = True
        #         self._update_active()
        #     return True
        # else:
        #     # cancel repeat key activation if a new key is pressed
        #     self._key_press_time = curtime
        #     self._key_repeat_key = keyv
        #     if self._key_repeat_active:
        #         self._key_repeat_active = False
        #         self._update_active()
        # """

        # """
        #     ## if typing with shift key, switch to action pane
        #     if not text_mode and use_command_keys and shift_mask:
        #         uchar = Gdk.keyval_to_unicode(keyv)
        #         if (uchar and unichr(uchar).isupper() and
        #             self.current == self.search):
        #             self.current.hide_table()
        #             self.switch_current()
        #     return False
        # """
        # exit here if it's not a special key
        if keyv not in self.keys_sensible:
            return False

        self._reset_to_toplevel = False

        if keyv == key_book["Escape"]:
            self._escape_key_press()
            return True

        if keyv == key_book["Up"]:
            self.current.go_up()

        elif keyv == key_book["Page_Up"]:
            self.current.go_page_up()

        elif keyv == key_book["Down"]:
            ## if typing with shift key, switch to action pane
            if shift_mask and self.current == self.search:
                self.current.hide_table()
                self.switch_current()

            if (
                not self.current.get_current()
                and self.current.get_match_state() is State.WAIT
            ):
                self._populate_search()

            self.current.go_down()

        elif keyv == key_book["Page_Down"]:
            if (
                not self.current.get_current()
                and self.current.get_match_state() is State.WAIT
            ):
                self._populate_search()

            self.current.go_page_down()

        elif keyv == key_book["Right"]:
            # MOD1_MASK is alt/option
            mod1_mask = (
                event_state
                == Gdk.ModifierType.MOD1_MASK  # pylint: disable=no-member
            )
            self._browse_down(alternate=mod1_mask)

        elif keyv == key_book["BackSpace"]:
            if not self.entry.get_text():  # not has_input
                self._backspace_key_press()
            elif not self._is_text_mode:
                self.entry.delete_text(self.entry.get_text_length() - 1, -1)
            else:
                return False

        elif keyv == key_book["Left"]:
            self._back_key_press()

        elif keyv in (key_book["Tab"], key_book["ISO_Left_Tab"]):
            self.switch_current(reverse=(keyv == key_book["ISO_Left_Tab"]))

        elif keyv == key_book["Home"]:
            self.current.go_first()

        else:
            # cont. processing
            return False

        return True

    def _entry_copy_clipboard(self, entry: Gtk.Entry) -> bool:
        # Copy current selection to clipboard
        # delegate to text entry when in text mode

        if self._is_text_mode:
            return False

        assert self.current
        selection = self.current.get_current()
        if selection is None:
            return False

        clip = Gtk.Clipboard.get_for_display(
            entry.get_display(), Gdk.SELECTION_CLIPBOARD
        )

        return interface.copy_to_clipboard(selection, clip)

    def _entry_cut_clipboard(self, entry: Gtk.Entry) -> bool:
        if not self._entry_copy_clipboard(entry):
            return False

        self.reset_current()
        self.reset()
        return False  # TODO: check, was no return

    def _entry_paste_data_received(
        self,
        clipboard: Gtk.Clipboard,
        targets: ty.Iterable[str],
        _extra: ty.Any,
        entry: Gtk.Widget,
    ) -> None:
        uri_target = Gdk.Atom.intern("text/uri-list", False)
        ### check if we can insert files
        if uri_target in targets:
            # paste as files
            sdata = clipboard.wait_for_contents(uri_target)
            self.reset_current()
            self.reset()
            self.put_files(sdata.get_uris(), paths=False)
            ## done
        else:
            # enable text mode and reemit to paste text
            self.try_enable_text_mode()
            if self._is_text_mode:
                entry.emit("paste-clipboard")

    def _entry_paste_clipboard(self, entry: Gtk.Widget) -> None:
        if not self._is_text_mode:
            self.reset()
            ## when not in text mode,
            ## stop signal emission so we can handle it
            clipboard = Gtk.Clipboard.get_for_display(
                entry.get_display(), Gdk.SELECTION_CLIPBOARD
            )
            clipboard.request_targets(self._entry_paste_data_received, entry)
            entry.emit_stop_by_name("paste-clipboard")

    def reset_text(self) -> None:
        self.entry.set_text("")

    def reset(self) -> None:
        self.reset_text()
        assert self.current
        self.current.hide_table()

    def reset_current(self, populate: bool = False) -> None:
        """
        Reset the source or action view

        Corresponds to backspace
        """
        assert self.current
        if self.current.get_match_state() is State.WAIT:
            self.toggle_text_mode(False)

        if self.current is self.action or populate:
            self._populate_search()
        else:
            self.current.reset()

    def reset_all(self) -> None:
        """Reset all panes and focus the first"""
        self.switch_to_source()
        while self._browse_up():
            pass

        self.toggle_text_mode(False)
        self.data_controller.object_stack_clear_all()
        self.reset_current()
        self.reset()

    def _populate_search(self) -> None:
        """Do a blanket search/empty search to populate current pane"""
        pane = self._pane_for_widget(self.current)
        self.data_controller.search(pane, interactive=True)

    def soft_reset(self, pane: ty.Optional[int] = None) -> None:
        """Reset @pane or current pane context/source
        softly (without visible update), and unset _reset_to_toplevel marker.
        """
        pane = pane or self._pane_for_widget(self.current)
        assert pane is not None
        if newsrc := self.data_controller.soft_reset(pane):
            assert self.current
            self.current.set_source(newsrc)

        self._reset_to_toplevel = False

    def _escape_key_press(self) -> None:
        """Handle escape if first pane is reset, cancel (put away) self."""
        assert self.current

        if self.current.has_result():
            if self.current.is_showing_result():
                self.reset_current(populate=True)
            else:
                self.reset_current()
        else:
            if self._is_text_mode:
                self.toggle_text_mode(False)
            elif not self.current.get_table_visible():
                pane = self._pane_for_widget(self.current)
                self.data_controller.object_stack_clear(pane)
                self.emit("cancelled")

            self._reset_to_toplevel = True
            self.current.hide_table()

        self.reset_text()

    def _backspace_key_press(self) -> None:
        # backspace: delete from stack
        pane = self._pane_for_widget(self.current)
        if self.data_controller.get_object_stack(pane):
            self.data_controller.object_stack_pop(pane)
            self.reset_text()
            return

        self._back_key_press()

    def _back_key_press(self) -> None:
        # leftarrow (or backspace without object stack)
        # delete/go up through stource stack
        assert self.current

        if self.current.is_showing_result():
            self.reset_current(populate=True)
        elif not self._browse_up():
            self.reset()
            self.reset_current()
            self._reset_to_toplevel = True

        self.reset_text()

    def _relax_search_terms(self) -> None:
        if self._is_text_mode:
            return

        assert self.current
        self.reset_text()
        self.current.relax_match()

    def get_can_enter_text_mode(self) -> bool:
        """We can enter text mode if the data backend allows,
        and the text entry is ready for input (empty)
        """
        pane = self._pane_for_widget(self.current)
        val = self.data_controller.get_can_enter_text_mode(pane)
        entry_text = self.entry.get_text()
        return val and not entry_text

    def try_enable_text_mode(self) -> bool:
        """Perform a soft reset if possible and then try enabling text mode"""
        if self._reset_to_toplevel:
            self.soft_reset()

        if self.get_can_enter_text_mode():
            return self.toggle_text_mode(True)

        return False

    def toggle_text_mode(self, val: bool) -> bool:
        """Toggle text mode on/off per @val,
        and return the subsequent on/off state.
        """
        val = val and self.get_can_enter_text_mode()
        self._is_text_mode = val
        self.update_text_mode()
        self.reset()
        return val

    def toggle_text_mode_quick(self) -> None:
        """Toggle text mode or not, if we can or not, without reset"""
        self._is_text_mode = not self._is_text_mode
        self.update_text_mode()

    def disable_text_mode_quick(self) -> None:
        """Toggle text mode or not, if we can or not, without reset"""
        if self._is_text_mode:
            self._is_text_mode = False
            self.update_text_mode()

    def update_text_mode(self) -> None:
        """update appearance to whether text mode enabled or not"""
        if self._is_text_mode:
            self.entry.show()
            self.entry.grab_focus()
            self.entry.set_position(-1)
            self.preedit.hide()
            self.preedit.set_width_chars(0)
        else:
            self.entry.hide()

        self._update_active()

    def switch_to_source_init(self) -> None:
        # Initial switch to source
        self.current = self.search
        self._update_active()
        if self._is_text_mode:
            self.toggle_text_mode_quick()

    def switch_to_source(self) -> None:
        self.switch_current_to(0)

    def switch_to_2(self) -> None:
        self.switch_current_to(1)

    def switch_to_3(self) -> None:
        self.switch_current_to(2)

    def focus(self) -> None:
        """called when the interface is focus (after being away)"""
        if self._reset_when_back:
            self._reset_when_back = False
            self.toggle_text_mode(False)
        # preserve text mode, but switch to source if we are not in it
        if not self._is_text_mode:
            self.switch_to_source()
        # Check that items are still valid when "coming back"
        self.data_controller.validate()

    def did_launch(self) -> None:
        "called to notify that 'activate' was successful"
        self._reset_when_back = True

    def did_get_result(self) -> None:
        "called when a command result has come in"
        self._reset_when_back = False

    def put_away(self) -> None:
        """Called when the interface is hidden"""
        self._relax_search_terms()
        self._reset_to_toplevel = True
        # no hide / show pane three on put away -> focus anymore

    def select_selected_file(self) -> None:
        # Add optional lookup data to narrow the search
        self.data_controller.find_object("qpfer:selectedfile#any.FileLeaf")

    def select_clipboard_file(self) -> None:
        # Add optional lookup data to narrow the search
        self.data_controller.find_object("qpfer:clipboardfile#any.FileLeaf")

    def select_selected_text(self) -> None:
        self.data_controller.find_object("qpfer:selectedtext#any.TextLeaf")

    def select_clipboard_text(self) -> None:
        # Add optional lookup data to narrow the search
        self.data_controller.find_object("qpfer:clipboardtext#any.FileLeaf")

    def select_quit(self) -> None:
        self.data_controller.find_object("qpfer:quit")

    def show_help(self) -> None:
        kupferui.show_help(self._make_gui_ctx())
        self.emit("launched-action")

    def show_preferences(self) -> None:
        kupferui.show_preferences(self._make_gui_ctx())
        self.emit("launched-action")

    def compose_action(self) -> None:
        self.data_controller.compose_selection()

    def mark_as_default(self) -> bool:
        if self.action.get_match_state() != State.MATCH:
            return False

        self.data_controller.mark_as_default(data.PaneSel.ACTION)
        return True

    def erase_affinity_for_first_pane(self) -> bool:
        if self.search.get_match_state() != State.MATCH:
            return False

        self.data_controller.erase_object_affinity(data.PaneSel.SOURCE)
        return True

    def comma_trick(self) -> bool:
        assert self.current

        if self.current.get_match_state() != State.MATCH:
            return False

        cur = self.current.get_current()
        curpane = self._pane_for_widget(self.current)
        if self.data_controller.object_stack_push(curpane, cur):
            self._relax_search_terms()
            if self._is_text_mode:
                self.reset_text()

            return True

        return False

    def action_accelerator(self, keystr: str) -> bool:
        """
        keystr: accelerator name string

        Return False if it was not possible to handle or the action was not
        used, return True if it was acted upon
        """
        if self.search.get_match_state() != State.MATCH:
            return False
        self.output_debug("Looking for action accelerator for", keystr)
        success, activate = self.action.select_action(keystr)
        if success:
            if activate:
                self.disable_text_mode_quick()
                self.activate()
            else:
                self.switch_to_3()
        else:
            self.output_debug("No action found for", keystr)
            return False

        return True

    def assign_action_accelerator(self) -> None:
        from kupfer.ui import getkey_dialog

        if self.action.get_match_state() != State.MATCH:
            raise RuntimeError("No Action Selected")

        def is_good_keystr(k: str) -> bool:
            keyv, mods = Gtk.accelerator_parse(k)
            return keyv != 0 and mods in (0, self.action.accel_modifier)

        widget = self.get_widget()
        keystr = getkey_dialog.ask_for_key(
            is_good_keystr,
            screen=widget.get_screen(),
            parent=widget.get_toplevel(),
        )
        if keystr is None:
            # Was cancelled
            return

        action = self.action.get_current()
        # Remove the modifiers
        keyv, _mods = Gtk.accelerator_parse(keystr)
        keystr = Gtk.accelerator_name(keyv, 0)
        self.action_accel_config.set(action, keystr)

    def get_context_actions(self) -> ty.Iterable[tuple[str, AccelFunc]]:
        """
        Get a list of (name, function) currently
        active context actions
        """
        assert self.current

        def get_accel(key: str) -> tuple[str, AccelFunc]:
            """Return name, method pair for @key"""
            if key not in accelerators.ACCELERATOR_NAMES:
                raise RuntimeError(f"Missing accelerator: {key}")

            return (accelerators.ACCELERATOR_NAMES[key], getattr(self, key))

        has_match = self.current.get_match_state() == State.MATCH
        if has_match:
            yield get_accel("compose_action")

        yield get_accel("select_selected_text")

        if self.get_can_enter_text_mode():
            yield get_accel("toggle_text_mode_quick")

        if self.action.get_match_state() == State.MATCH:
            smatch = self.search.get_current()
            amatch = self.action.get_current()

            label = _('Assign Accelerator to "%(action)s"') % {
                "action": _trunc_long_str(amatch)
            }
            w_label = textwrap.wrap(label, width=40, subsequent_indent="    ")
            yield ("\n".join(w_label), self.assign_action_accelerator)

            label = _('Make "%(action)s" Default for "%(object)s"') % {
                "action": _trunc_long_str(amatch),
                "object": _trunc_long_str(smatch),
            }
            w_label = textwrap.wrap(label, width=40, subsequent_indent="    ")
            yield ("\n".join(w_label), self.mark_as_default)

        if has_match:
            if self.data_controller.get_object_has_affinity(
                data.PaneSel.SOURCE
            ):
                # TRANS: Removing learned and/or configured bonus search score
                yield (
                    _('Forget About "%s"')
                    % _trunc_long_str(self.search.get_current()),
                    self.erase_affinity_for_first_pane,
                )

            yield get_accel("reset_all")

    def _pane_reset(
        self, _controller: ty.Any, pane: int, item: Rankable | None
    ) -> None:
        wid = self._widget_for_pane(pane)
        if not item:
            wid.reset()
            return

        wid.set_match_plain(item)
        if wid is self.search:
            self.reset()
            self.toggle_text_mode(False)
            self.switch_to_source()

    def _new_source(
        self, _sender: ty.Any, pane: int, source: AnySource, at_root: bool
    ) -> None:
        """Notification about a new data source,
        (represented object for the self.search object
        """
        wid = self._widget_for_pane(pane)
        wid.set_source(source)
        wid.reset()
        if pane == data.PaneSel.SOURCE:
            self.switch_to_source()
            self.action.reset()

        if wid is self.current:
            self.toggle_text_mode(False)
            self._reset_to_toplevel = False
            if not at_root:
                self.reset_current(populate=True)
                wid.show_table_quirk()

    def update_third(self) -> None:
        if self._pane_three_is_visible:
            self._ui_transition_timer.set_ms(200, self._show_third_pane, True)
        else:
            self._show_third_pane(False)

    def _show_hide_third(
        self, _ctr: ty.Any, mode: int, _ignored: ty.Any
    ) -> None:
        if mode == data.PaneMode.SOURCE_ACTION_OBJECT:
            # use a delay before showing the third pane,
            # but set internal variable to "shown" already now
            self._pane_three_is_visible = True
            self._ui_transition_timer.set_ms(200, self._show_third_pane, True)
        else:
            self._pane_three_is_visible = False
            self._show_third_pane(False)

    def _show_third_pane(self, show: bool) -> None:
        self._ui_transition_timer.invalidate()
        self.third.set_visible(show)

    def _update_active(self) -> None:
        for panewidget in (self.action, self.search, self.third):
            if panewidget is not self.current:
                panewidget.set_state(Gtk.StateType.NORMAL)

            panewidget.match_view.inject_preedit(None)

        assert self.current

        if self._is_text_mode:  # or self._key_repeat_active:
            self.current.set_state(Gtk.StateType.ACTIVE)
        else:
            self.current.set_state(Gtk.StateType.SELECTED)
            self.current.match_view.inject_preedit(self.preedit)

        self._description_changed()

    def switch_current(self, reverse: bool = False) -> None:
        # Only allow switch if we have match
        if self._pane_three_is_visible:
            curidx = (self.search, self.action, self.third).index(self.current)
            newidx = (curidx - 1 if reverse else curidx + 1) % 3
        else:
            # for 2 panels simple switch to other one
            newidx = 0 if self.current == self.action else 1

        self.switch_current_to(newidx)

    def switch_current_to(self, index: int) -> bool:
        """
        Switch selected pane

        index: index (0, 1, or 2) of the pane to select.
        """
        assert index in (0, 1, 2)
        assert self.current

        if self._pane_three_is_visible:
            order = (self.search, self.action, self.third)
        else:
            order = (self.search, self.action)  # type: ignore

        if index >= len(order):
            return False

        pane_before = order[max(index - 1, 0)]
        new_focus = order[index]
        no_match_ok = index == 0
        # Only allow switch if we have match in the pane before
        if (
            no_match_ok or pane_before.get_match_state() is State.MATCH
        ) and new_focus is not self.current:
            self.current.hide_table()
            self.current = new_focus
            # Use toggle_text_mode to reset
            self.toggle_text_mode(False)
            pane = self._pane_for_widget(new_focus)
            self._update_active()
            if self.data_controller.get_should_enter_text_mode(pane):
                self.toggle_text_mode_quick()

        return True

    def _browse_up(self) -> bool:
        pane = self._pane_for_widget(self.current)
        return self.data_controller.browse_up(pane)

    def _browse_down(self, alternate: bool = False) -> None:
        pane = self._pane_for_widget(self.current)
        assert pane is not None
        self.data_controller.browse_down(pane, alternate=alternate)

    def _make_gui_ctx(self) -> uievents.GUIEnvironmentContext:
        event_time = Gtk.get_current_event_time()
        return uievents.gui_context_from_widget(event_time, self._widget)

    def _activate(self, _pane_owner: ty.Any, _current: ty.Any) -> None:
        self.data_controller.activate(ui_ctx=self._make_gui_ctx())

    def activate(self) -> None:
        """Activate current selection (Run action)"""
        self._activate(None, None)

    def execute_file(
        self,
        filepath: ty.Iterable[str],
        display: str,
        event_time: float,
    ) -> None:
        """Execute a .kfcom file"""

        def _handle_error(exc_info):
            from kupfer import uiutils

            _etype, exc, _tb = exc_info
            if not uiutils.show_notification(str(exc), icon_name="kupfer"):
                raise exc

        ctxenv = uievents.gui_context_from_keyevent(event_time, display)
        self.data_controller.execute_file(filepath, ctxenv, _handle_error)

    def _search_result(
        self,
        _sender: ty.Any,
        pane: int,
        matchrankable: Rankable | None,
        matches: ty.Iterable[Rankable],
        key: str | None,
    ) -> None:
        # NOTE: "Always-matching" search.
        # If we receive an empty match, we ignore it, to retain the previous
        # results. The user is not served by being met by empty results.
        if key and len(key) > 1 and matchrankable is None:
            # with typos or so, reset quicker
            self._latest_input_timer.set(
                _SLOW_INPUT_INTERVAL / 2, self._relax_search_terms
            )
            return

        wid = self._widget_for_pane(pane)
        wid.update_match(key, matchrankable, matches)

    def _widget_for_pane(self, pane: int) -> Search:
        return self.pane_to_widget[pane]

    def _pane_for_widget(self, widget: GObject.GObject) -> int | None:
        return self.widget_to_pane[id(widget)]

    def _object_stack_changed(
        self, controller: data.DataController, pane: int
    ) -> None:
        """
        Stack of objects (for comma trick) changed in @pane
        """
        wid = self._widget_for_pane(pane)
        wid.set_object_stack(controller.get_object_stack(pane))

    def _panewidget_button_press(
        self, widget: Gtk.Widget, event: Gdk.EventButton
    ) -> bool:
        "mouse clicked on a pane widget"
        # activate on double-click
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            self.activate()
            return True

        return False

    def _selection_changed(
        self, pane_owner: Search, match: Rankable | None
    ) -> None:
        pane = self._pane_for_widget(pane_owner)
        self.data_controller.select(pane, match)
        if pane_owner is not self.current:
            return

        self._description_changed()

    def _description_changed(self) -> None:
        assert self.current
        match = self.current.get_current()
        # Use invisible WORD JOINER instead of empty, to maintain vertical size
        desc = match and match.get_description() or "\N{WORD JOINER}"
        markup = f"<small>{_escape_markup_str(desc)}</small>"
        self.label.set_markup(markup)

    def put_text(self, text: str) -> None:
        """
        Put @text into the interface to search, to use
        for "queries" from other sources
        """
        self.try_enable_text_mode()
        self.entry.set_text(text)
        self.entry.set_position(-1)

    def put_files(
        self, fileuris: ty.Iterable[str], paths: ty.Iterable[str]
    ) -> None:
        # don't consume iterable
        # self.output_debug("put-files:", list(fileuris))
        if paths:
            objs = (Gio.File.new_for_path(U).get_path() for U in fileuris)
        else:
            objs = (Gio.File.new_for_uri(U).get_path() for U in fileuris)

        leaves = list(map(FileLeaf, filter(None, objs)))
        if leaves:
            self.data_controller.insert_objects(data.PaneSel.SOURCE, leaves)

    def _reset_input_timer(self) -> None:
        # if input is slow/new, we reset
        self._latest_input_timer.set(
            _SLOW_INPUT_INTERVAL, self._relax_search_terms
        )

    def _preedit_im_changed(
        self, _editable: ty.Any, preedit_string: str
    ) -> None:
        """
        This is called whenever the input method changes its own preedit box.
        We take this opportunity to expand it.
        """
        if preedit_string:
            assert self.current
            self.current.match_view.expand_preedit(self.preedit)
            self._reset_input_timer()

        self._preedit_text = preedit_string

    def _preedit_insert_text(
        self, editable: Gtk.Entry, text: str, byte_length: int, position: int
    ) -> bool:
        # New text about to be inserted in preedit
        if text:
            self.entry.insert_text(text, -1)
            self.entry.set_position(-1)
            self._reset_input_timer()
            self._update_active()

        GObject.signal_stop_emission_by_name(editable, "insert-text")
        return False

    def _preedit_draw(self, widget: Gtk.Widget, _cr: ty.Any) -> bool:
        # draw nothing if hidden
        return widget.get_width_chars() == 0  # type: ignore

    def _changed(self, editable: Gtk.Entry) -> None:
        """
        The entry changed callback: Here we have to be sure to use
        **UNICODE** (unicode()) for the entered text
        """
        # @text is UTF-8
        text = editable.get_text()

        # draw character count as icon
        editable.set_icon_from_pixbuf(Gtk.EntryIconPosition.SECONDARY, None)

        # cancel search and return if empty
        if not text:
            self.data_controller.cancel_search()
            # See if it was a deleting key press
            curev = Gtk.get_current_event()
            if (
                curev
                and curev.type == Gdk.EventType.KEY_PRESS
                and curev.keyval
                in (self.key_book["Delete"], self.key_book["BackSpace"])
            ):
                self._backspace_key_press()

            return

        # start search for updated query
        pane = self._pane_for_widget(self.current)
        if not self._is_text_mode and self._reset_to_toplevel:
            self.soft_reset(pane)

        self.data_controller.search(
            pane, key=text, context=text, text_mode=self._is_text_mode
        )


GObject.type_register(Interface)
GObject.signal_new(
    "cancelled",
    Interface,
    GObject.SignalFlags.RUN_LAST,
    GObject.TYPE_BOOLEAN,
    (),
)
# Send only when the interface itself launched an action directly
GObject.signal_new(
    "launched-action",
    Interface,
    GObject.SignalFlags.RUN_LAST,
    GObject.TYPE_BOOLEAN,
    (),
)

PREEDIT_HIDDEN_CLASS = "hidden"

KUPFER_CSS = b"""
#kupfer {
}

.matchview {
    border-radius: 0.6em;
}

#kupfer-preedit {
    padding: 0 0 0 0;
}

#kupfer-preedit.hidden {
    border-width: 0 0 0 0;
    padding: 0 0 0 0 ;
    margin: 0 0 0 0;
    outline-width: 0;
    min-height: 0;
    min-width: 0;
}

#kupfer-object-pane {
}

#kupfer-action-pane {
}

#kupfer-indirect-object-pane {
}

#kupfer-list {
}

#kupfer-list-view {
}

*:selected.matchview {
    background: alpha(@theme_selected_bg_color, 0.5);
    border: 2px solid alpha(black, 0.3)
}
"""

WINDOW_BORDER_WIDTH = 8


class WindowController(pretty.OutputMixin):
    """
    This is the fundamental Window (and App) Controller
    """

    def __init__(self):
        self.window: Gtk.Window = None
        self.current_screen_handler = 0
        self.current_screen = None
        self.interface: Interface = None  # type: ignore
        self._statusicon = None
        self._statusicon_ai = None
        self._window_hide_timer = scheduler.Timer()

    def initialize(self, data_controller: data.DataController) -> None:
        self.window = Gtk.Window(
            type=Gtk.WindowType.TOPLEVEL,
            border_width=WINDOW_BORDER_WIDTH,
            decorated=False,
            name="kupfer",
        )
        self.window.connect("realize", self._on_window_realize)
        self.window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)

        data_controller.connect("launched-action", self.launch_callback)
        data_controller.connect("command-result", self.result_callback)

        self.interface = Interface(data_controller, self.window)
        self.interface.connect("launched-action", self.launch_callback)
        self.interface.connect("cancelled", self._cancelled)
        self.window.connect("map-event", self._on_window_map_event)
        self._setup_window()

        # Accept drops
        self.window.drag_dest_set(
            Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY
        )
        self.window.drag_dest_add_uri_targets()
        self.window.drag_dest_add_text_targets()
        self.window.connect("drag-data-received", self._on_drag_data_received)

    def _on_window_map_event(self, *_args: ty.Any) -> None:
        self.interface.update_third()

    def _on_window_realize(self, widget: Gtk.Widget) -> None:
        # Load css
        style_provider = Gtk.CssProvider()
        style_provider.load_from_data(KUPFER_CSS)

        Gtk.StyleContext.add_provider_for_screen(
            widget.get_screen(),
            style_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def show_statusicon(self) -> None:
        if not self._statusicon:
            self._statusicon = self._setup_gtk_status_icon(self._setup_menu())

        with suppress(AttributeError):
            self._statusicon.set_visible(True)

    def hide_statusicon(self) -> None:
        if self._statusicon:
            try:
                self._statusicon.set_visible(False)
            except AttributeError:
                self._statusicon = None

    def _showstatusicon_changed(
        self,
        setctl: settings.SettingsController,
        section: str,
        key: str,
        value: ty.Any,
    ) -> None:
        "callback from SettingsController"
        if value:
            self.show_statusicon()
        else:
            self.hide_statusicon()

    def show_statusicon_ai(self) -> None:
        if not self._statusicon_ai:
            self._statusicon_ai = self._setup_appindicator(self._setup_menu())

        if self._statusicon_ai or AppIndicator3 is None:
            return

        self._statusicon_ai.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

    def hide_statusicon_ai(self) -> None:
        if self._statusicon_ai and AppIndicator3 is not None:
            self._statusicon_ai.set_status(
                AppIndicator3.IndicatorStatus.PASSIVE
            )

    def _showstatusicon_ai_changed(
        self,
        setctl: settings.SettingsController,
        section: str,
        key: str,
        value: ty.Any,
    ) -> None:
        if value:
            self.show_statusicon_ai()
        else:
            self.hide_statusicon_ai()

    def _setup_menu(self, context_menu: bool = False) -> Gtk.Menu:
        menu = Gtk.Menu()
        menu.set_name("kupfer-menu")

        def submenu_callback(
            menuitem: Gtk.MenuItem, callback: ty.Callable[[], None]
        ) -> bool:
            callback()
            return True

        def add_menu_item(
            icon: str | None,
            callback: ty.Callable[..., None],
            label: str | None = None,
            with_ctx: bool = True,
        ) -> None:
            def mitem_handler(
                menuitem: Gtk.MenuItem, callback: ty.Callable[..., None]
            ) -> bool:
                if with_ctx:
                    event_time = Gtk.get_current_event_time()
                    ui_ctx = uievents.gui_context_from_widget(
                        event_time, menuitem
                    )
                    callback(ui_ctx)
                else:
                    callback()

                if context_menu:
                    self.put_away()

                return True

            if label and not icon:
                mitem = Gtk.MenuItem(label=label)
            else:
                mitem = Gtk.ImageMenuItem.new_from_stock(icon)

            mitem.connect("activate", mitem_handler, callback)
            menu.append(mitem)

        if context_menu:
            add_menu_item(Gtk.STOCK_CLOSE, self.put_away, with_ctx=False)
        else:
            add_menu_item(None, self.activate, _("Show Main Interface"))

        menu.append(Gtk.SeparatorMenuItem())
        if context_menu:
            for name, func in self.interface.get_context_actions():
                mitem = Gtk.MenuItem(label=name)
                mitem.connect("activate", submenu_callback, func)
                menu.append(mitem)

            menu.append(Gtk.SeparatorMenuItem())

        add_menu_item(Gtk.STOCK_PREFERENCES, kupferui.show_preferences)
        add_menu_item(Gtk.STOCK_HELP, kupferui.show_help)
        add_menu_item(Gtk.STOCK_ABOUT, kupferui.show_about_dialog)
        menu.append(Gtk.SeparatorMenuItem())
        add_menu_item(Gtk.STOCK_QUIT, self.quit, with_ctx=False)
        menu.show_all()

        return menu

    def _setup_gtk_status_icon(self, menu: Gtk.Menu) -> Gtk.StatusIcon:
        status = Gtk.StatusIcon.new_from_icon_name(version.ICON_NAME)
        status.set_tooltip_text(version.PROGRAM_NAME)

        status.connect("popup-menu", self._popup_menu, menu)
        status.connect("activate", self.show_hide)
        return status

    def _setup_appindicator(self, menu: Gtk.Menu) -> ty.Any:
        if AppIndicator3 is None:
            return None

        indicator = AppIndicator3.Indicator.new(
            version.PROGRAM_NAME,
            version.ICON_NAME,
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        indicator.set_menu(menu)
        return indicator

    def _setup_window(self) -> None:
        """
        Returns window
        """

        self.window.connect("delete-event", self._close_window)
        self.window.connect("focus-out-event", self._lost_focus)
        self.window.connect("button-press-event", self._window_frame_clicked)
        widget = self.interface.get_widget()
        widget.show()

        # Build the window frame with its top bar
        topbar = Gtk.HBox()
        vbox = Gtk.VBox()
        vbox.pack_start(topbar, False, False, 0)
        vbox.pack_start(widget, True, True, 0)
        vbox.show()
        self.window.add(vbox)
        title = Gtk.Label.new("")
        button = Gtk.Label.new("")
        l_programname = version.PROGRAM_NAME.lower()
        # The text on the general+context menu button
        btext = f"<b>{l_programname} ⚙</b>"
        button.set_markup(btext)
        button_box = Gtk.EventBox()
        button_box.set_visible_window(False)
        button_adj = Gtk.Alignment.new(0.5, 0.5, 0, 0)
        button_adj.set_padding(0, 2, 0, 3)
        button_adj.add(button)
        button_box.add(button_adj)
        button_box.connect("button-press-event", self._context_clicked)
        button_box.connect(
            "enter-notify-event", self._button_enter, button, btext
        )
        button_box.connect(
            "leave-notify-event", self._button_leave, button, btext
        )
        button.set_name("kupfer-menu-button")
        title_align = Gtk.Alignment.new(0, 0.5, 0, 0)
        title_align.add(title)
        topbar.pack_start(title_align, True, True, 0)
        topbar.pack_start(button_box, False, False, 0)
        topbar.show_all()

        self.window.set_title(version.PROGRAM_NAME)
        self.window.set_icon_name(version.ICON_NAME)
        self.window.set_type_hint(self._window_type_hint())
        self.window.set_property("skip-taskbar-hint", True)
        self.window.set_keep_above(True)
        pos = self._window_position()
        if pos != Gtk.WindowPosition.NONE:
            self.window.set_position(pos)

        if not text_direction_is_ltr():
            self.window.set_gravity(Gdk.GRAVITY_NORTH_EAST)
        # Setting not resizable changes from utility window
        # on metacity
        self.window.set_resizable(False)

    def _window_type_hint(self) -> Gdk.WindowTypeHint:
        type_hint = Gdk.WindowTypeHint.UTILITY
        hint_name = kupfer.config.get_kupfer_env("WINDOW_TYPE_HINT").upper()
        if hint_name:
            if hint_enum := getattr(Gdk.WindowTypeHint, hint_name, None):
                type_hint = hint_enum
            else:
                self.output_error("No such Window Type Hint", hint_name)
                self.output_error("Existing type hints:")
                for name in dir(Gdk.WindowTypeHint):
                    if name.upper() == name:
                        self.output_error(name)

        return type_hint

    def _window_position(self) -> Gtk.WindowPosition:
        value = Gtk.WindowPosition.NONE
        hint_name = kupfer.config.get_kupfer_env("WINDOW_POSITION").upper()
        if hint_name:
            if hint_enum := getattr(Gtk.WindowPosition, hint_name, None):
                value = hint_enum
            else:
                self.output_error("No such Window Position", hint_name)
                self.output_error("Existing values:")
                for name in dir(Gtk.WindowPosition):
                    if name.upper() == name:
                        self.output_error(name)

        return value

    def _window_frame_clicked(
        self, widget: Gtk.Widget, event: Gdk.EventButton
    ) -> None:
        "Start drag when the window is clicked"
        widget.begin_move_drag(
            event.button, int(event.x_root), int(event.y_root), event.time
        )

    def _context_clicked(
        self, widget: Gtk.Widget, event: Gdk.EventButton
    ) -> bool:
        "The context menu label was clicked"
        menu = self._setup_menu(True)
        menu.set_screen(self.window.get_screen())
        menu.popup(None, None, None, None, event.button, event.time)
        return True

    def _button_enter(
        self,
        widget: Gtk.Widget,
        event: Gdk.EventCrossing,
        button: Gtk.Widget,
        udata: str,
    ) -> None:
        "Pointer enters context menu button"
        button.set_markup(f"<u>{udata}</u>")

    def _button_leave(
        self,
        widget: Gtk.Widget,
        event: Gdk.EventCrossing,
        button: Gtk.Widget,
        udata: str,
    ) -> None:
        "Pointer leaves context menu button"
        button.set_markup(udata)

    def _popup_menu(
        self,
        status_icon: Gtk.StatusIcon,
        button: Gtk.Widget,
        activate_time: float,
        menu: Gtk.Menu,
    ) -> None:
        """
        When the StatusIcon is right-clicked
        """
        menu.popup(
            None,
            None,
            Gtk.StatusIcon.position_menu,
            status_icon,
            button,
            activate_time,
        )

    def launch_callback(self, sender: ty.Any) -> None:
        # Separate window hide from the action being
        # done. This is to solve a window focus bug when
        # we switch windows using an action
        self.interface.did_launch()
        self._window_hide_timer.set_ms(100, self.put_away)

    def result_callback(
        self,
        sender: data.DataController,
        _result_type: ty.Any,
        ui_ctx: uievents.GUIEnvironmentContext,
    ) -> None:
        self.interface.did_get_result()
        if ui_ctx:
            self.on_present(
                sender, ui_ctx.get_display(), ui_ctx.get_timestamp()
            )
        else:
            self.on_present(sender, "", Gtk.get_current_event_time())

    def _lost_focus(self, window: Gtk.Window, event: Gdk.EventFocus) -> None:
        if not kupfer.config.has_capability("HIDE_ON_FOCUS_OUT"):
            return
        # Close at unfocus.
        # Since focus-out-event is triggered even
        # when we click inside the window, we'll
        # do some additional math to make sure that
        # that window won't close if the mouse pointer
        # is over it.
        _gdkwindow, x, y, _mods = (
            window.get_screen().get_root_window().get_pointer()
        )
        w_x, w_y = window.get_position()
        w_w, w_h = window.get_size()
        if x not in range(w_x, w_x + w_w) or y not in range(w_y, w_y + w_h):
            self._window_hide_timer.set_ms(50, self.put_away)

    def _monitors_changed(self, *_ignored: ty.Any) -> None:
        self._center_window()

    def is_current_display(self, displayname: str) -> bool:
        def norm_name(name):
            "Make :0.0 out of :0"
            # TODO: change
            if name[-2] == ":":
                return name + ".0"

            return name

        if not self.window.has_screen():
            return False

        cur_disp = self.window.get_screen().get_display().get_name()
        return norm_name(cur_disp) == norm_name(displayname)

    def _window_put_on_screen(self, screen: Gdk.Screen) -> None:
        if self.current_screen_handler:
            scr = self.window.get_screen()
            scr.disconnect(self.current_screen_handler)

        self.window.set_screen(screen)
        self.current_screen_handler = screen.connect(
            "monitors-changed", self._monitors_changed
        )
        self.current_screen = screen

    def _center_window(self, displayname: str | None = None) -> None:
        """Center Window on the monitor the pointer is currently on"""

        def norm_name(name):
            "Make :0.0 out of :0"
            # TODO: remove duplicate
            if name[-2] == ":":
                return name + ".0"

            return name

        if not displayname and self.window.has_screen():
            display = self.window.get_display()
        else:
            display = uievents.GUIEnvironmentContext.ensure_display_open(
                displayname
            )

        screen, x, y, modifiers = display.get_pointer()
        self._window_put_on_screen(screen)
        monitor_nr = screen.get_monitor_at_point(x, y)
        geo = screen.get_monitor_geometry(monitor_nr)
        wid, hei = self.window.get_size()
        midx = geo.x + geo.width / 2 - wid / 2
        midy = geo.y + geo.height / 2 - hei / 2
        self.window.move(midx, midy)
        uievents.GUIEnvironmentContext.try_close_unused_displays(screen)

    def _should_recenter_window(self) -> bool:
        """Return True if the mouse pointer and the window
        are on different monitors.
        """
        # Check if the GtkWindow was realized yet
        if not self.window.get_realized():
            return True

        display = self.window.get_screen().get_display()
        screen, x, y, modifiers = display.get_pointer()
        mon_cur = screen.get_monitor_at_point(x, y)
        mon_win = screen.get_monitor_at_window(self.window.get_window())
        return mon_cur != mon_win

    def activate(self, sender: Gtk.Widget | None = None) -> None:
        dispname = self.window.get_screen().make_display_name()
        self.on_present(sender, dispname, Gtk.get_current_event_time())

    def on_present(
        self, sender: ty.Any, display: str | None, timestamp: float
    ) -> None:
        """Present on @display, where None means default display"""
        self._window_hide_timer.invalidate()
        if not display:
            display = Gdk.Display.get_default().get_name()

        # Center window before first show
        if not self.window.get_realized():
            self._center_window(display)

        self.window.stick()
        self.window.present_with_time(timestamp)
        self.window.get_window().focus(timestamp=timestamp)
        self.interface.focus()

        # Center after present if we are moving between monitors
        if self._should_recenter_window():
            self._center_window(display)

    def put_away(self) -> None:
        self.interface.put_away()
        self.window.hide()

    def _cancelled(self, _obj: Interface) -> None:
        self.put_away()

    def on_show_hide(
        self, sender: ty.Any, display: str, timestamp: float
    ) -> None:
        """
        Toggle activate/put-away
        """
        if self.window.get_property("visible"):
            self.put_away()
        else:
            self.on_present(sender, display, timestamp)

    def show_hide(self, sender: Gtk.Widget) -> None:
        "GtkStatusIcon callback"
        self.on_show_hide(sender, "", Gtk.get_current_event_time())

    def _key_binding(
        self,
        keyobj: keybindings.KeyboundObject,
        keybinding_number: int,
        display: str,
        timestamp: float,
    ) -> None:
        """Keybinding activation callback"""
        if keybinding_number == keybindings.KEYBINDING_DEFAULT:
            self.on_show_hide(keyobj, display, timestamp)

        elif keybinding_number == keybindings.KEYBINDING_MAGIC:
            self.on_present(keyobj, display, timestamp)
            self.interface.select_selected_text()
            self.interface.select_selected_file()

    def _on_drag_data_received(
        self,
        widget: Gtk.Widget,
        context: ty.Any,
        x: int,
        y: int,
        data,
        info,
        time,
    ) -> None:
        ic(vars())
        uris = data.get_uris()
        if uris:
            self.interface.put_files(uris, paths=False)
        else:
            self.interface.put_text(data.get_text())

    def on_put_text(
        self, sender: Gtk.Widget, text: str, display: str, timestamp: float
    ) -> None:
        """We got a search text from dbus"""
        self.on_present(sender, display, timestamp)
        self.interface.put_text(text)

    def on_put_files(
        self,
        sender: ty.any,
        fileuris: ty.Iterable[str],
        display: str,
        timestamp: float,
    ) -> None:
        self.on_present(sender, display, timestamp)
        self.interface.put_files(fileuris, paths=True)

    def on_execute_file(
        self,
        sender: ty.Any,
        filepath: ty.Iterable[str],
        display: str,
        timestamp: float,
    ) -> None:
        self.interface.execute_file(filepath, display, timestamp)

    def _close_window(self, window: Gtk.Widget, event) -> bool:
        ic(vars())
        self.put_away()
        return True

    def _destroy(self, widget: Gtk.Widget, _data: ty.Any = None) -> None:
        self.quit()

    def _sigterm(self, signal: int, _frame: ty.Any) -> None:
        self.output_info("Caught signal", signal, "exiting..")
        self.quit()

    def _on_early_interrupt(self, signal: int, _frame: ty.Any) -> None:
        sys.exit(1)

    def save_data(self) -> None:
        """Save state before quit"""
        sch = scheduler.get_scheduler()
        sch.finish()
        self.interface.save_config()

    def quit(self, sender: Gtk.Widget | None = None) -> None:
        Gtk.main_quit()

    def quit_now(self) -> None:
        """Quit immediately (state save should already be done)"""
        raise SystemExit

    def _session_save(self, *_args: ty.Any) -> bool:
        """Old-style session save callback.
        ret True on successful
        """
        # No quit, only save
        self.output_info("Saving for logout...")
        self.save_data()
        return True

    def _session_die(self, *_args: ty.Any) -> None:
        """Session callback on session end
        quit now, without saving, since we already do that on
        Session save!
        """
        self.quit_now()

    def lazy_setup(self) -> None:
        """Do all setup that can be done after showing main interface.
        Connect to desktop services (keybinding callback, session logout
        callbacks etc).
        """
        from kupfer.ui import session

        self.output_debug("in lazy_setup")

        setctl = settings.GetSettingsController()
        if setctl.get_show_status_icon():
            self.show_statusicon()

        if setctl.get_show_status_icon_ai():
            self.show_statusicon_ai()

        setctl.connect(
            "value-changed::kupfer.showstatusicon",
            self._showstatusicon_changed,
        )
        setctl.connect(
            "value-changed::kupfer.showstatusicon_ai",
            self._showstatusicon_ai_changed,
        )

        if keystr := setctl.get_keybinding():
            succ = keybindings.bind_key(keystr)
            self.output_info(
                f"Trying to register {keystr} to spawn kupfer.. "
                + ("success" if succ else "failed")
            )

        if magickeystr := setctl.get_magic_keybinding():
            succ = keybindings.bind_key(
                magickeystr, keybindings.KEYBINDING_MAGIC
            )
            self.output_debug(
                f"Trying to register {magickeystr} to spawn kupfer.. "
                + ("success" if succ else "failed")
            )

        keyobj = keybindings.GetKeyboundObject()
        keyobj.connect("keybinding", self._key_binding)

        signal.signal(signal.SIGINT, self._sigterm)
        signal.signal(signal.SIGTERM, self._sigterm)
        signal.signal(signal.SIGHUP, self._sigterm)

        client = session.SessionClient()
        client.connect("save-yourself", self._session_save)
        client.connect("die", self._session_die)
        self.interface.lazy_setup()

        self.output_debug("finished lazy_setup")

    def main(self, quiet: bool = False) -> None:
        """Start WindowController, present its window (if not @quiet)"""
        signal.signal(signal.SIGINT, self._on_early_interrupt)

        try:
            # NOTE: For a *very short* time we will use both APIs
            kserv1 = listen.Service()
            kserv2 = listen.ServiceNew()
        except listen.AlreadyRunningError:
            self.output_info("An instance is already running, exiting...")
            self.quit_now()
        except listen.NoConnectionError:
            kserv1 = None
            kserv2 = None
        else:
            keyobj = keybindings.GetKeyboundObject()
            keyobj.connect(
                "bound-key-changed",
                lambda x, y, z: kserv1.BoundKeyChanged(y, z),
            )
            kserv1.connect("relay-keys", keyobj.relayed_keys)

        # Load data
        data_controller = data.DataController()
        sch = scheduler.get_scheduler()
        sch.load()
        # Now create UI and display
        self.initialize(data_controller)
        sch.display()

        if kserv1:
            kserv1.connect("present", self.on_present)
            kserv1.connect("show-hide", self.on_show_hide)
            kserv1.connect("put-text", self.on_put_text)
            kserv1.connect("put-files", self.on_put_files)
            kserv1.connect("execute-file", self.on_execute_file)
            kserv1.connect("quit", self.quit)

        if kserv2:
            kserv2.connect("present", self.on_present)
            kserv2.connect("show-hide", self.on_show_hide)
            kserv2.connect("put-text", self.on_put_text)
            kserv2.connect("put-files", self.on_put_files)
            kserv2.connect("execute-file", self.on_execute_file)
            kserv2.connect("quit", self.quit)

        if not quiet:
            self.activate()

        GLib.idle_add(self.lazy_setup)

        def do_main_iterations(max_events=0):
            # use sentinel form of iter
            for idx, _pending in enumerate(iter(Gtk.events_pending, False)):
                if max_events and idx > max_events:
                    break

                Gtk.main_iteration()

        try:
            Gtk.main()
            # put away window *before exiting further*
            self.put_away()
            do_main_iterations(10)
        finally:
            self.save_data()

        # tear down but keep hanging
        if kserv1:
            kserv1.unregister()

        if kserv2:
            kserv2.unregister()

        keybindings.bind_key(None, keybindings.KEYBINDING_DEFAULT)
        keybindings.bind_key(None, keybindings.KEYBINDING_MAGIC)

        do_main_iterations(100)
        # if we are still waiting, print a message
        if Gtk.events_pending():
            self.output_info("Waiting for tasks to finish...")
            do_main_iterations()

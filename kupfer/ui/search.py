#! /usr/bin/env python3
from __future__ import annotations

import itertools
import enum
import typing as ty

from gi.repository import Gtk, Gdk, GObject
from gi.repository import GLib, Pango
from gi.repository import GdkPixbuf


from kupfer.core import relevance, learn
from kupfer.core.search import Rankable
from kupfer.core import settings, actionaccel
from kupfer.obj.base import Leaf, Action, KupferObject, AnySource
from kupfer import icons
from kupfer import pretty
import kupfer.config
import kupfer.environment

from .support import escape_markup_str, text_direction_is_ltr

ELLIPSIZE_MIDDLE = Pango.EllipsizeMode.MIDDLE
PREEDIT_HIDDEN_CLASS = "hidden"
WINDOW_BORDER_WIDTH = 8

if ty.TYPE_CHECKING:
    _ = str


def _format_match(match: str) -> str:
    return f"<u><b>{escape_markup_str(match)}</b></u>"


# State Constants
class State(enum.IntEnum):
    WAIT = 1
    MATCH = 2
    NO_MATCH = 3


_ICON_COL = 1
_NAME_COL = 2
_FAV_COL = 3
_INFO_COL = 4
_RANK_COL = 5


class LeafModel:
    """A base for a tree view
    With a magic load-on-demand feature.

    self.set_base will set its base iterator
    and self.populate(num) will load @num items into
    the model

    Attributes:
    icon_size
    """

    def __init__(
        self, aux_info_callback: ty.Callable[[Leaf | Action], str]
    ) -> None:
        """
        First column is always the object -- returned by get_object
        it needs not be specified in columns
        """
        columns = (GObject.TYPE_OBJECT, str, str, str, str)
        self.store = Gtk.ListStore(GObject.TYPE_PYOBJECT, *columns)
        self.object_column = 0
        self.base: ty.Iterator[Rankable] | None = None
        self._setup_columns()
        self.icon_size = 32
        self.aux_info_callback = aux_info_callback

    def __len__(self) -> int:
        return len(self.store)

    def _setup_columns(self):
        # only show in debug mode
        show_rank_col = pretty.DEBUG

        # Name and description column
        # Expands to the rest of the space
        name_cell = Gtk.CellRendererText()
        name_cell.set_property("ellipsize", ELLIPSIZE_MIDDLE)
        name_col = Gtk.TreeViewColumn("item", name_cell)
        name_col.set_expand(True)
        name_col.add_attribute(name_cell, "markup", _NAME_COL)

        fav_cell = Gtk.CellRendererText()
        fav_col = Gtk.TreeViewColumn("fav", fav_cell)
        fav_col.add_attribute(fav_cell, "text", _FAV_COL)

        info_cell = Gtk.CellRendererText()
        info_col = Gtk.TreeViewColumn("info", info_cell)
        info_col.add_attribute(info_cell, "text", _INFO_COL)

        nbr_cell = Gtk.CellRendererText()
        nbr_col = Gtk.TreeViewColumn("rank", nbr_cell)
        nbr_cell.set_property("width-chars", 3)
        nbr_col.add_attribute(nbr_cell, "text", _RANK_COL)

        icon_cell = Gtk.CellRendererPixbuf()
        # icon_cell.set_property("height", 32)
        # icon_cell.set_property("width", 32)
        # icon_cell.set_property("stock-size", Gtk.IconSize.LARGE_TOOLBAR)

        icon_col = Gtk.TreeViewColumn("icon", icon_cell)
        icon_col.add_attribute(icon_cell, "pixbuf", _ICON_COL)

        self.columns = [icon_col, name_col, fav_col, info_col]
        if show_rank_col:
            self.columns.append(nbr_col)

    def _get_column(self, treepath: ty.Iterable[int], col: int) -> ty.Any:
        store_iter = self.store.get_iter(treepath)
        return self.store.get_value(store_iter, col)

    def get_object(self, path: ty.Iterable[int] | None) -> ty.Any:
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

    def populate(self, num: int | None = None) -> KupferObject | None:
        """
        populate model with num items from its base
        and return first item inserted
        if num is none, insert everything

        """
        if not self.base:
            return None

        # FIXME: there is now path for num=None, added this; check
        iterator: ty.Iterator[Rankable] = self.base
        if num:
            iterator = itertools.islice(self.base, num)

        try:
            first_rank = next(iterator)
            self.add(first_rank)
            first = first_rank.object
        except StopIteration:
            return None

        for item in iterator:
            self.add(item)

        # first.object is a leaf
        return first

    def _get_row(
        self, rankable: Rankable
    ) -> tuple[Rankable, GdkPixbuf.Pixbuf | None, str, str, str, str]:
        """Use the UI description functions get_*
        to initialize @rankable into the model
        """
        leaf, rank = rankable.object, rankable.rank
        assert isinstance(leaf, (Leaf, Action))
        icon = self._get_icon(leaf)
        markup = self._get_label_markup(rankable)
        fav = self._get_fav(leaf)
        info = self._get_aux_info(leaf)
        rank_str = self._get_rank_str(rank)
        return (rankable, icon, markup, fav, info, rank_str)

    def add(self, rankable: Rankable) -> None:
        self.store.append(self._get_row(rankable))

    def add_first(self, rankable: Rankable) -> None:
        self.store.prepend(self._get_row(rankable))

    def _get_icon(self, leaf: KupferObject) -> GdkPixbuf.Pixbuf | None:
        if (size := self.icon_size) > 8:
            return leaf.get_thumbnail(size, size) or leaf.get_pixbuf(size)

        return None

    def _get_label_markup(self, rankable: Rankable) -> str:
        leaf = rankable.object
        # Here we use the items real name
        # Previously we used the alias that was matched,
        # but it can be too confusing or ugly
        name = escape_markup_str(str(leaf))
        if desc := escape_markup_str(leaf.get_description() or ""):
            return f"{name}\n<small>{desc}</small>"

        return f"{name}"

    def _get_fav(self, leaf: KupferObject) -> str:
        # fav: display star if it's a favourite
        if learn.is_favorite(leaf):
            return "\N{BLACK STAR}"

        return ""

    def _get_aux_info(self, leaf: Leaf | Action) -> str:
        # For objects: Show arrow if it has content
        # For actions: Show accelerator
        #
        if self.aux_info_callback is not None:
            return self.aux_info_callback(leaf)

        return ""

    def _get_rank_str(self, rank: ty.Optional[float]) -> str:
        # Display rank empty instead of 0 since it looks better
        return str(int(rank)) if rank else ""


def _dim_icon(icon: GdkPixbuf.Pixbuf | None) -> GdkPixbuf.Pixbuf | None:
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


_LABEL_CHAR_WIDTH = 25
_PREEDIT_CHAR_WIDTH = 5


class MatchViewOwner(pretty.OutputMixin):
    """
    Owner of the widget for displaying name, icon and name underlining (if
    applicable) of the current match.
    """

    def __init__(self):
        # object attributes
        self.match_state: State = State.WAIT

        self.object_stack = []

        # finally build widget
        self._build_widget()
        self.cur_icon: ty.Optional[GdkPixbuf.Pixbuf] = None

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

    def _build_widget(self) -> None:
        """
        Core initalization method that builds the widget
        """
        self.label = Gtk.Label.new("<match>")
        self.label.set_single_line_mode(True)
        self.label.set_width_chars(_LABEL_CHAR_WIDTH)
        self.label.set_max_width_chars(_LABEL_CHAR_WIDTH)
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
        self,
        base: GdkPixbuf.Pixbuf,
        pixbufs: list[GdkPixbuf.Pixbuf],
        small_size: int,
    ) -> GdkPixbuf.Pixbuf:
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
        markup = relevance.formatCommonSubstrings(
            str(self.cur_text),  # text
            str(self.cur_match).lower(),  # key,
            format_clean=escape_markup_str,
            format_match=_format_match,
        )
        self.label.set_markup(markup)

    def set_object(
        self,
        text: ty.Optional[str],
        icon: GdkPixbuf.Pixbuf | None,
        update: bool = True,
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
        icon: GdkPixbuf.Pixbuf | None,
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
        new_label_width = _LABEL_CHAR_WIDTH - _PREEDIT_CHAR_WIDTH
        self.label.set_width_chars(new_label_width)
        preedit.set_width_chars(_PREEDIT_CHAR_WIDTH)
        preedit.get_style_context().remove_class(PREEDIT_HIDDEN_CLASS)

    def shrink_preedit(self, preedit: Gtk.Entry) -> None:
        self.label.set_width_chars(_LABEL_CHAR_WIDTH)
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

        self.label.set_width_chars(_LABEL_CHAR_WIDTH)
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
        self.model = LeafModel(self._get_aux_info)
        self.match = None
        self.match_state = State.WAIT
        self.text: ty.Optional[str] = ""
        self.source: ty.Optional[AnySource] = None
        self._old_win_position = None
        self._has_search_result = False
        self._initialized = False
        # finally build widget
        self._build_widget()
        self._icon_size: int = 0
        self._icon_size_small: int = 0
        self._read_icon_size()
        self._setup_empty()

    def _get_aux_info(self, leaf: KupferObject) -> str:
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

    def _build_widget(self) -> None:
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

        # k: not in use: sub_x = pos_x
        sub_y = pos_y + win_height
        # to stop a warning
        _dummy_sr = self.table.size_request()

        # FIXME: Adapt list length
        subwin_height = list_maxheight
        subwin_width = self_width * 2 + parent_padding_x
        # k: not in use:
        # if not text_direction_is_ltr():
        #    sub_x += win_width - subwin_width + self_x

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
            self._populate(_SHOW_MORE)

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
            self._populate(_SHOW_MORE)

        if len(self.model) >= 1:
            path, _col = self.table.get_cursor()
            if path:
                row = path[0]
                if len(self.model) - rows_count <= row:
                    self._populate(_SHOW_MORE)
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
            self._handle_no_matches(empty=not key)
            return

        self._set_match(matchrankable)
        self.model.set_base(iter(matches))
        if not self.model and self.get_table_visible():
            self.go_down()

    def reset(self) -> None:
        self._has_search_result = False
        self._initialized = True
        self.model.clear()
        self._setup_empty()

    def _setup_empty(self) -> None:
        self.match_state = State.NO_MATCH
        self.match_view.set_match_state("No match", None, state=State.NO_MATCH)
        self.relax_match()

    def _populate(self, num: int) -> ty.Optional[KupferObject]:
        """populate model with num items"""
        return self.model.populate(num)

    def _handle_no_matches(self, empty: bool = False) -> None:
        """if @empty, there were no matches to find"""
        assert hasattr(self, "_get_nomatch_name_icon")
        name, icon = self._get_nomatch_name_icon(  # pylint: disable=no-member
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

    def _get_aux_info(self, leaf: KupferObject) -> str:
        if hasattr(leaf, "has_content") and leaf.has_content():  # type: ignore
            if text_direction_is_ltr():
                return "\N{BLACK RIGHT-POINTING SMALL TRIANGLE} "

            return "\N{BLACK LEFT-POINTING SMALL TRIANGLE} "

        return ""

    def _get_pbuf(self, src: AnySource) -> GdkPixbuf.Pixbuf:
        return src.get_thumbnail(
            self.icon_size * 4 // 3, self.icon_size
        ) or src.get_pixbuf(self.icon_size)

    def _get_nomatch_name_icon(
        self, empty: bool
    ) -> tuple[str, GdkPixbuf.Pixbuf]:
        if empty and self.source:
            return (
                f"<i>{escape_markup_str(self.source.get_empty_text())}</i>",
                self._get_pbuf(self.source),
            )

        if self.source:
            assert self.text
            return (
                _('No matches in %(src)s for "%(query)s"')
                % {
                    "src": f"<i>{escape_markup_str(str(self.source))}</i>",
                    "query": escape_markup_str(self.text),
                },
                self._get_pbuf(self.source),
            )

        return _("No matches"), icons.get_icon_for_name(
            "kupfer-object", self.icon_size
        )

    def _setup_empty(self) -> None:
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
) -> str | None:
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
        self.action_accel_config: actionaccel.AccelConfig | None = None
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

    def _get_aux_info(self, leaf: Action) -> str:
        if not self.action_accel_config:
            return ""

        accel = _accel_for_action(leaf, self.action_accel_config)
        if accel:
            keyv, mods = Gtk.accelerator_parse(accel)
            if mods != 0:
                self.output_error("Ignoring action accelerator mod", mods)

            return Gtk.accelerator_get_label(  # type:ignore
                keyv, self.accel_modifier
            )

        return ""

    def _get_nomatch_name_icon(
        self, empty: bool = False
    ) -> tuple[str, GdkPixbuf.Pixbuf | None]:
        # don't look up icons too early
        if not self._initialized:
            return ("", None)

        if self.text:
            msg = _('No action matches "%s"') % escape_markup_str(self.text)
            title = f"<i>{msg}</i>"
        else:
            title = ""

        return title, icons.get_icon_for_name("kupfer-execute", self.icon_size)

    def _setup_empty(self) -> None:
        self._handle_no_matches()
        self.hide_table()

    def select_action(self, accel: str) -> tuple[bool, bool]:
        """
        Find and select the next action with accelerator key @accel

        Return pair of bool success, can activate
        """
        assert self.action_accel_config

        if self.get_match_state() == State.NO_MATCH:
            return False, False

        idx = self._table_current_row() or 0
        self._populate(1)
        if not self.model:
            return False, False

        start_row = idx
        model_len = len(self.model)
        while True:
            cur = self.model.get_object((idx,))
            self.output_debug("Looking at action", repr(cur.object))
            action = cur.object

            if _accel_for_action(action, self.action_accel_config) == accel:
                self._table_set_cursor_at_row(idx)
                return True, not action.requires_object()

            self._populate(1)
            idx = (idx + 1) % model_len
            if idx == start_row:
                break

        return False, False

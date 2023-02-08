from __future__ import annotations
import itertools
import operator
import os
import sys
from contextlib import suppress
import typing as ty
from enum import IntEnum

from gi.repository import GLib, GObject

from kupfer.obj import base, sources, compose
from kupfer.obj.base import (
    Action,
    Leaf,
    Source,
    AnySource,
    TextSource,
    KupferObject,
    ActionGenerator,
)
from kupfer import pretty, scheduler
from kupfer import datatools
from kupfer.core import actioncompat
from kupfer.core import commandexec
from kupfer.core import execfile
from kupfer.core import pluginload
from kupfer.core import qfurl
from kupfer.core import search, learn
from kupfer.core import settings
from kupfer.core.search import Rankable
from kupfer.core.sources import GetSourceController
from kupfer.ui.uievents import GUIEnvironmentContext

DATA_SAVE_INTERVAL_S = 3660

# "Enums"
# Which pane
class PaneSel(IntEnum):
    SOURCE = 1
    ACTION = 2
    OBJECT = 3


# In two-pane or three-pane mode
class PaneMode(IntEnum):
    SOURCE_ACTION = 1
    SOURCE_ACTION_OBJECT = 2


ItemCheckFunc = ty.Callable[
    [ty.Iterable[KupferObject]], ty.Iterable[KupferObject]
]
DecoratorFunc = ty.Callable[[ty.Iterable[Rankable]], ty.Iterable[Rankable]]


def _identity(x: ty.Any) -> ty.Any:
    return x


def _is_iterable(obj: ty.Any) -> bool:
    return hasattr(obj, "__iter__")


def _dress_leaves(
    seq: ty.Iterable[Rankable], action: ty.Optional[Action]
) -> ty.Iterable[Rankable]:
    """yield items of @seq "dressed" by the source controller"""
    sctr = GetSourceController()
    for itm in seq:
        sctr.decorate_object(itm.object, action=action)
        yield itm


def _peekfirst(
    seq: ty.Iterable[Rankable],
) -> tuple[ty.Optional[Rankable], ty.Iterable[Rankable]]:
    """This function will return (firstitem, iter)
    where firstitem is the first item of @seq or None if empty,
    and iter an equivalent copy of @seq
    """
    seq = iter(seq)
    for itm in seq:
        old_iter = itertools.chain((itm,), seq)
        return (itm, old_iter)

    return (None, seq)


def _as_set_iter(seq: ty.Iterable[Rankable]) -> ty.Iterable[Rankable]:
    key = operator.attrgetter("object")
    return datatools.UniqueIterator(seq, key=key)


def _valid_check(seq: ty.Iterable[Rankable]) -> ty.Iterable[Rankable]:
    """yield items of @seq that are valid"""
    for itm in seq:
        obj = itm.object
        if (not hasattr(obj, "is_valid")) or obj.is_valid():
            yield itm


class Searcher:
    """
    This class searches KupferObjects efficiently, and
    stores searches in a cache for a very limited time (*)

    (*) As of this writing, the cache is used when the old key
    is a prefix of the search key.
    """

    def __init__(self):
        self._source_cache = {}
        self._old_key: str | None = None

    def search(
        self,
        sources_: ty.Iterable[Source | TextSource | ty.Iterable[KupferObject]],
        key: str,
        score: bool = True,
        item_check: ItemCheckFunc | None = None,
        decorator: DecoratorFunc | None = None,
    ) -> tuple[Rankable | None, ty.Iterable[Rankable]]:
        """
        @sources is a sequence listing the inputs, which should be
        Sources, TextSources or sequences of KupferObjects

        If @score, sort by rank.
        filters (with _identity() as default):
            @item_check: Check items before adding to search pool
            @decorator: Decorate items before access

        Return (first, match_iter), where first is the first match,
        and match_iter an iterator to all matches, including the first match.
        """
        if not self._old_key or not key.startswith(self._old_key):
            self._source_cache.clear()

        self._old_key = key

        # General strategy: Extract a `list` from each source,
        # and perform ranking as in place operations on lists
        item_check = item_check or _identity
        decorator = decorator or _identity
        start_time = pretty.timing_start()
        match_lists: list[list[Rankable]] = []
        for src in sources_:
            fixedrank = 0
            can_cache = True
            rankables = None
            if hasattr(src, "__iter__"):
                items = src
                can_cache = False
            else:
                # Look in source cache for stored rankables
                try:
                    rankables = self._source_cache[src]
                except KeyError:
                    try:
                        # TextSources
                        items = src.get_text_items(key)  # type: ignore
                        fixedrank = src.get_rank()  # type: ignore
                        can_cache = False
                    except AttributeError:
                        # Source
                        items = src.get_leaves()  # type: ignore

            if rankables is None:
                rankables = search.make_rankables(item_check(items))  # type: ignore

            assert rankables is not None

            if score:
                if fixedrank:
                    rankables = search.add_rank_objects(rankables, fixedrank)
                elif key:
                    rankables = search.score_objects(rankables, key)
                    rankables = search.bonus_objects(rankables, key)

                if can_cache:
                    rankables = list(rankables)
                    self._source_cache[src] = rankables

            match_lists.append(list(rankables))

        if score:
            matches = search.find_best_sort(match_lists)
        else:
            matches = itertools.chain(*match_lists)

        # Check if the items are valid as the search
        # results are accessed through the iterators
        unique_matches = _as_set_iter(matches)
        match, match_iter = _peekfirst(decorator(_valid_check(unique_matches)))
        pretty.timing_step(__name__, start_time, "ranked")
        return match, match_iter

    def rank_actions(
        self,
        objects: ty.Iterable[KupferObject],
        key: str,
        leaf: Leaf | None,
        item_check: ItemCheckFunc | None = None,
        decorator: DecoratorFunc | None = None,
    ) -> tuple[Rankable | None, ty.Iterable[Rankable]]:
        """
        rank @objects, which should be a sequence of KupferObjects,
        for @key, with the action ranker algorithm.

        @leaf is the Leaf the action is going to be invoked on

        Filters and return value like .score().
        """
        item_check = item_check or _identity
        decorator = decorator or _identity

        rankables = search.make_rankables(item_check(objects))
        if key:
            rankables = search.score_objects(rankables, key)
            matches = search.bonus_actions(rankables, key)
        else:
            matches = search.score_actions(rankables, leaf)

        sorted_matches = sorted(
            matches, key=operator.attrgetter("rank"), reverse=True
        )

        match, match_iter = _peekfirst(decorator(sorted_matches))
        return match, match_iter


WrapContext = tuple[int, ty.Any]


class Pane(GObject.GObject):
    """
    signals:
        search-result (match, match_iter, context)
    """

    __gtype_name__ = "Pane"

    def __init__(self):
        super().__init__()
        self.selection: KupferObject | None = None
        self.latest_key: str | None = None
        self.outstanding_search: int = -1
        self.outstanding_search_id: int = -1
        self.searcher = Searcher()

    def select(self, item: KupferObject | None) -> None:
        self.selection = item

    def get_selection(self) -> KupferObject | None:
        return self.selection

    def reset(self) -> None:
        self.selection = None

    def get_latest_key(self) -> str | None:
        return self.latest_key

    def get_can_enter_text_mode(self) -> bool:
        return False

    def get_should_enter_text_mode(self) -> bool:
        return False

    def emit_search_result(
        self,
        match: Rankable | None,
        match_iter: ty.Iterable[Rankable],
        context: WrapContext | None,
    ) -> None:
        self.emit("search-result", match, match_iter, context)


GObject.signal_new(
    "search-result",
    Pane,
    GObject.SignalFlags.RUN_LAST,
    GObject.TYPE_BOOLEAN,
    (GObject.TYPE_PYOBJECT, GObject.TYPE_PYOBJECT, GObject.TYPE_PYOBJECT),
)


class LeafPane(Pane, pretty.OutputMixin):
    __gtype_name__ = "LeafPane"

    def __init__(self):
        super().__init__()
        self.source_stack: list[AnySource] = []
        self.source: AnySource | None = None
        self.object_stack: list[KupferObject] = []

    def select(self, item: Leaf | None) -> None:
        assert item is None or isinstance(
            item, Leaf
        ), "New selection for object pane is not a Leaf!"
        super().select(item)

    def _load_source(self, src: AnySource) -> AnySource:
        """Try to get a source from the SourceController,
        if it is already loaded we get it from there, else
        returns @src"""
        sctr = GetSourceController()
        return sctr.get_canonical_source(src)

    def get_source(self) -> AnySource | None:
        return self.source

    def source_rebase(self, src: AnySource) -> None:
        self.source_stack = []
        self.source = self._load_source(src)
        self.refresh_data()

    def push_source(self, src: AnySource) -> None:
        self.source_stack.append(self.source)
        self.source = self._load_source(src)
        self.refresh_data()

    def pop_source(self) -> bool:
        """Return True if succeeded"""
        if self.source_stack:
            self.source = self.source_stack.pop()
            return True

        return False

    def is_at_source_root(self) -> bool:
        """Return True if we have no source stack"""
        return not self.source_stack

    def object_stack_push(self, obj: KupferObject) -> None:
        self.object_stack.append(obj)

    def object_stack_pop(self) -> KupferObject:
        return self.object_stack.pop()

    def get_can_enter_text_mode(self) -> bool:
        return self.is_at_source_root()

    def get_should_enter_text_mode(self) -> bool:
        return False

    def refresh_data(self) -> None:
        self.emit("new-source", self.source)

    def browse_up(self) -> bool:
        """Try to browse up to previous sources, from current
        source"""
        succ = bool(self.pop_source())
        if not succ:
            assert self.source
            if self.source.has_parent():
                self.source_rebase(self.source.get_parent())
                succ = True

        if succ:
            self.refresh_data()

        return succ

    def browse_down(self, alternate: bool = False) -> bool:
        """Browse into @leaf if it's possible
        and save away the previous sources in the stack
        if @alternate, use the Source's alternate method"""
        leaf: Leaf = self.get_selection()  # type: ignore
        if leaf and leaf.has_content():
            self.push_source(leaf.content_source(alternate=alternate))
            return True

        return False

    def reset(self) -> None:
        """Pop all sources and go back to top level"""
        Pane.reset(self)
        while self.pop_source():
            pass

        self.refresh_data()

    def soft_reset(self) -> ty.Optional[AnySource]:
        Pane.reset(self)
        while self.pop_source():
            pass

        return self.source

    def search(
        self,
        key: str = "",
        context: WrapContext | None = None,
        text_mode: bool = False,
    ) -> None:
        """
        filter for action @item
        """
        self.latest_key = key
        sources_: ty.Iterable[AnySource] = (
            (self.get_source(),) if not text_mode else ()
        )
        if key and self.is_at_source_root():
            # Only use text sources when we are at root catalog
            sctr = GetSourceController()
            textsrcs = sctr.get_text_sources()
            sources_ = itertools.chain(sources_, textsrcs)

        def decorator(seq):
            return _dress_leaves(seq, action=None)

        match, match_iter = self.searcher.search(
            sources_, key, score=bool(key), decorator=decorator
        )
        self.emit_search_result(match, match_iter, context)


GObject.signal_new(
    "new-source",
    LeafPane,
    GObject.SignalFlags.RUN_LAST,
    GObject.TYPE_BOOLEAN,
    (GObject.TYPE_PYOBJECT,),
)


class PrimaryActionPane(Pane):
    def __init__(self):
        super().__init__()
        self._action_valid_cache = {}
        self.set_item(None)

    def select(self, item: Action | None) -> None:
        assert not item or isinstance(
            item, base.Action
        ), "Selection in action pane is not an Action!"
        super().select(item)

    def set_item(self, item: Leaf | None) -> None:
        """Set which @item we are currently listing actions for"""
        self.current_item = item
        self._action_valid_cache.clear()

    def search(
        self,
        key: str = "",
        context: WrapContext | None = None,
        text_mode: bool = False,
    ) -> None:
        """Search: Register the search method in the event loop

        using @key, promising to return
        @context in the notification about the result, having selected
        @item in PaneSel.SOURCE

        If we already have a call to search, we remove the "source"
        so that we always use the most recently requested search."""

        leaf = self.current_item
        if not leaf:
            return

        self.latest_key = key
        actions = actioncompat.actions_for_item(leaf, GetSourceController())
        cache = self._action_valid_cache

        def is_valid_cached(action: Action) -> bool:
            """Check if @action is valid for current item"""
            valid = cache.get(action)
            if valid is None:
                valid = actioncompat.action_valid_for_item(action, leaf)
                cache[action] = valid

            return valid

        def valid_decorator(seq):
            """Check if actions are valid before access"""
            return (obj for obj in seq if is_valid_cached(obj.object))

        match, match_iter = self.searcher.rank_actions(
            actions, key, leaf, decorator=valid_decorator
        )
        self.emit_search_result(match, match_iter, context)


class SecondaryObjectPane(LeafPane):
    __gtype_name__ = "SecondaryObjectPane"

    def __init__(self):
        LeafPane.__init__(self)
        self.current_item: Leaf | None = None
        self.current_action: Action | None = None

    def reset(self) -> None:
        LeafPane.reset(self)
        self.searcher = Searcher()

    def set_item_and_action(
        self, item: Leaf | None, act: Action | None
    ) -> None:
        self.current_item = item
        self.current_action = act
        if item and act:
            ownsrc, use_catalog = actioncompat.iobject_source_for_action(
                act, item
            )
            if ownsrc and not use_catalog:
                self.source_rebase(ownsrc)
            else:
                extra_sources = [ownsrc] if ownsrc else None
                sctr = GetSourceController()
                self.source_rebase(
                    sctr.root_for_types(act.object_types(), extra_sources)
                )
        else:
            self.reset()

    def get_can_enter_text_mode(self) -> bool:
        """Check if there are any reasonable text sources for this action"""
        assert self.current_action
        atroot = self.is_at_source_root()
        types = tuple(self.current_action.object_types())
        sctr = GetSourceController()
        textsrcs = sctr.get_text_sources()
        return atroot and any(
            sctr.good_source_for_types(s, types) for s in textsrcs
        )

    def get_should_enter_text_mode(self):
        return self.is_at_source_root() and hasattr(
            self.get_source(), "get_text_items"
        )

    def search(
        self,
        key: str = "",
        context: WrapContext | None = None,
        text_mode: bool = False,
    ) -> None:
        """
        filter for action @item
        """
        assert self.current_action

        self.latest_key = key
        sources_: ty.Iterable[AnySource] = []
        if not text_mode or hasattr(self.get_source(), "get_text_items"):
            if srcs := self.get_source():
                sources_ = itertools.chain(sources_, (srcs,))

        if key and self.is_at_source_root():
            # Only use text sources when we are at root catalog
            sctr = GetSourceController()
            if textsrcs := sctr.get_text_sources():
                sources_ = itertools.chain(sources_, textsrcs)

        item_check = actioncompat.iobjects_valid_for_action(
            self.current_action, self.current_item
        )

        def decorator(seq):
            return _dress_leaves(seq, action=self.current_action)

        match, match_iter = self.searcher.search(
            sources_,
            key,
            score=True,
            item_check=item_check,
            decorator=decorator,
        )
        self.emit_search_result(match, match_iter, context)


class DataController(GObject.GObject, pretty.OutputMixin):
    """
    Sources <-> Actions controller

    The data controller must be created before main program commences,
    so it can register itself at the scheduler correctly.
    """

    __gtype_name__ = "DataController"

    def __init__(self):
        super().__init__()

        self.source_pane = LeafPane()
        self.object_pane = SecondaryObjectPane()
        self.source_pane.connect("new-source", self._new_source)
        self.object_pane.connect("new-source", self._new_source)
        self.action_pane = PrimaryActionPane()
        self._panectl_table: ty.Dict[PaneSel, LeafPane] = {
            PaneSel.SOURCE: self.source_pane,
            PaneSel.ACTION: self.action_pane,
            PaneSel.OBJECT: self.object_pane,
        }
        for pane, ctl in list(self._panectl_table.items()):
            ctl.connect("search-result", self._pane_search_result, pane)

        self.mode: PaneMode | None = None
        self._search_ids = itertools.count(1)
        self._latest_interaction = -1
        self._execution_context = (
            commandexec.default_action_execution_context()
        )
        self._execution_context.connect(
            "command-result", self._command_execution_result
        )
        self._execution_context.connect(
            "late-command-result", self._late_command_execution_result
        )

        self._save_data_timer = scheduler.Timer()

        sch = scheduler.get_scheduler()
        sch.connect("load", self._load)
        sch.connect("display", self._display)
        sch.connect("finish", self._finish)

    def register_text_sources(
        self, plugin_id: str, srcs: ty.Iterable[TextSource]
    ) -> None:
        """Pass in text sources as @srcs

        we register text sources"""
        sctr = GetSourceController()
        sctr.add_text_sources(plugin_id, srcs)

    def register_action_decorators(
        self, plugin_id: str, actions: list[Action]
    ) -> None:
        # Keep a mapping: Decorated Leaf Type -> List of actions
        decorate_types: ty.Dict[ty.Any, list[Action]] = {}
        for action in actions:
            for appl_type in action.item_types():
                decorate_types.setdefault(appl_type, []).append(action)

        if not decorate_types:
            return

        sctr = GetSourceController()
        sctr.add_action_decorators(plugin_id, decorate_types)

    def register_content_decorators(
        self, plugin_id: str, contents: ty.Collection[Source]
    ) -> None:
        """
        Register the sequence of classes @contents as
        potential content decorators. Classes not conforming to
        the decoration protocol (most importantly, ``.decorates_type()``)
        will be skipped
        """
        # Keep a mapping:
        # Decorated Leaf Type -> Set of content decorator types
        decorate_item_types: ty.Dict[ty.Any, set[Source]] = {}
        for content in contents:
            with suppress(AttributeError):
                applies = content.decorates_type()  # type: ignore
                decorate_item_types.setdefault(applies, set()).add(content)

        if not decorate_item_types:
            return

        sctr = GetSourceController()
        sctr.add_content_decorators(plugin_id, decorate_item_types)

    def register_action_generators(
        self, plugin_id: str, generators: ty.Iterable[ActionGenerator]
    ) -> None:
        sctr = GetSourceController()
        for generator in generators:
            sctr.add_action_generator(plugin_id, generator)

    def _load(self, _sched: ty.Any) -> None:
        """Begin Data Controller work when we get application 'load' signal

        Load the data model from saved configuration and caches
        """
        setctl = settings.GetSettingsController()
        setctl.connect("plugin-enabled-changed", self._plugin_enabled)
        setctl.connect("plugin-toplevel-changed", self._plugin_catalog_changed)

        self._load_all_plugins()
        dir_src, indir_src = self._get_directory_sources()
        sctr = GetSourceController()
        sctr.add(None, dir_src, toplevel=True)
        sctr.add(None, indir_src, toplevel=False)
        sctr.initialize()
        learn.load()

    def _display(self, _sched: ty.Any) -> None:
        self._reload_source_root()
        self._save_data_timer.set(DATA_SAVE_INTERVAL_S, self._save_data)

    def _get_directory_sources(
        self,
    ) -> tuple[
        ty.Iterator[sources.DirectorySource],
        ty.Iterator[sources.DirectorySource],
    ]:
        """
        Return a tuple of dir_sources, indir_sources for
        directory sources directly included and for
        catalog inclusion respectively
        """
        setctl = settings.GetSettingsController()
        source_config = setctl.get_config

        def file_source(opt, depth=1):
            abs_path = os.path.abspath(os.path.expanduser(opt))
            return sources.FileSource([abs_path], depth)

        indir_sources: ty.Iterator[sources.DirectorySource] = (
            sources.DirectorySource(item)
            for item in setctl.get_directories(False)
            if os.path.isdir(item)
        )

        dir_sources: ty.Iterator[sources.DirectorySource] = (
            sources.DirectorySource(item)
            for item in setctl.get_directories(True)
            if os.path.isdir(item)
        )

        dir_depth = source_config("DeepDirectories", "Depth")

        indir_sources = itertools.chain(
            indir_sources,
            (
                file_source(item, dir_depth)
                for item in source_config("DeepDirectories", "Catalog")
            ),
        )

        dir_sources = itertools.chain(
            dir_sources,
            (
                file_source(item, dir_depth)
                for item in source_config("DeepDirectories", "Direct")
            ),
        )

        return dir_sources, indir_sources

    def _load_all_plugins(self):
        """
        Insert all plugin sources into the catalog
        """
        from kupfer.core import plugins

        setctl = settings.GetSettingsController()
        for item in sorted(plugins.get_plugin_ids()):
            if setctl.get_plugin_enabled(item):
                sources_ = self._load_plugin(item)
                self._insert_sources(item, sources_, initialize=False)

    def _load_plugin(self, plugin_id: str) -> ty.Set[AnySource]:
        """
        Load @plugin_id, register all its Actions, Content and TextSources.
        Return its sources.
        """
        with pluginload.exception_guard(plugin_id):
            plugin = pluginload.load_plugin(plugin_id)
            self.register_text_sources(plugin_id, plugin.text_sources)
            self.register_action_decorators(
                plugin_id, plugin.action_decorators
            )
            self.register_content_decorators(
                plugin_id, plugin.content_decorators
            )
            self.register_action_generators(
                plugin_id, plugin.action_generators
            )
            return set(plugin.sources)

        return set()

    def _plugin_enabled(
        self, _setctl: ty.Any, plugin_id: str, enabled: bool | int
    ) -> None:
        from kupfer.core import plugins

        if enabled and not plugins.is_plugin_loaded(plugin_id):
            srcs = self._load_plugin(plugin_id)
            self._insert_sources(plugin_id, srcs, initialize=True)
        elif not enabled:
            self._remove_plugin(plugin_id)

    def _remove_plugin(self, plugin_id: str) -> None:
        sctl = GetSourceController()
        if sctl.remove_objects_for_plugin_id(plugin_id):
            self._reload_source_root()

        pluginload.remove_plugin(plugin_id)

    def _reload_source_root(self) -> None:
        self.output_debug("Reloading source root")
        sctl = GetSourceController()
        self.source_pane.source_rebase(sctl.root)

    def _plugin_catalog_changed(
        self, _setctl: ty.Any, _plugin_id: str, _toplevel: ty.Any
    ) -> None:
        self._reload_source_root()

    def _insert_sources(
        self,
        plugin_id: str,
        sources_: ty.Collection[AnySource],
        initialize: bool = True,
    ) -> None:
        if not sources_:
            return

        sctl = GetSourceController()
        setctl = settings.GetSettingsController()
        for src in sources_:
            is_toplevel = setctl.get_source_is_toplevel(plugin_id, src)
            sctl.add(
                plugin_id, (src,), toplevel=is_toplevel, initialize=initialize
            )

        if initialize:
            self._reload_source_root()

    def _finish(self, _sched: ty.Any) -> None:
        "Close down the data model, save user data, and write caches to disk"
        GetSourceController().finalize()
        self._save_data(final_invocation=True)
        self.output_info("Saving cache...")
        GetSourceController().save_cache()

    def _save_data(self, final_invocation: bool = False) -> None:
        """Save Learning data and User's configuration data in sources
        (Recurring timer)
        """
        self.output_info("Saving data...")
        learn.save()
        GetSourceController().save_data()
        if not final_invocation:
            self._save_data_timer.set(DATA_SAVE_INTERVAL_S, self._save_data)

    def _new_source(self, ctr: LeafPane, src: AnySource) -> None:
        if ctr is self.source_pane:
            pane = PaneSel.SOURCE
        elif ctr is self.object_pane:
            pane = PaneSel.OBJECT

        root = ctr.is_at_source_root()
        self.emit("source-changed", pane, src, root)

    def reset(self) -> None:
        self.source_pane.reset()
        self.action_pane.reset()

    def soft_reset(self, pane: PaneSel) -> ty.Optional[AnySource]:
        if pane == PaneSel.ACTION:
            return None

        panectl: LeafPane = self._panectl_table[pane]
        return panectl.soft_reset()

    def cancel_search(self, pane: PaneSel | None = None) -> None:
        """Cancel any outstanding search, or the search for @pane"""
        panes = (
            (pane,)
            if pane
            else (PaneSel.SOURCE, PaneSel.ACTION, PaneSel.OBJECT)
        )
        for pane_ in panes:
            ctl = self._panectl_table[pane_]
            if ctl.outstanding_search > 0:
                GLib.source_remove(ctl.outstanding_search)
                ctl.outstanding_search = -1

    def search(
        self,
        pane: PaneSel,
        key: str = "",
        context: str | None = None,
        interactive: bool = False,
        lazy: bool = False,
        text_mode: bool = False,
    ) -> None:
        """Search: Register the search method in the event loop

        Will search in @pane's base using @key, promising to return
        @context in the notification about the result.

        if @interactive, the search result will return immediately
        if @lazy, will slow down search result reporting
        """
        self.cancel_search(pane)
        self._latest_interaction = self._execution_context.last_command_id
        ctl = self._panectl_table[pane]
        ctl.outstanding_search_id = next(self._search_ids)
        wrapcontext = (ctl.outstanding_search_id, context)
        if interactive:
            ctl.search(key, wrapcontext, text_mode)
            return

        timeout = 300 if lazy else 0 if not key else 50 // len(key)

        def ctl_search(*args):
            ctl.outstanding_search = -1
            return ctl.search(*args)

        ctl.outstanding_search = GLib.timeout_add(
            timeout, ctl_search, key, wrapcontext, text_mode
        )

    def _pane_search_result(
        self,
        panectl: Pane,
        match: Rankable | None,
        match_iter: ty.Iterable[Rankable],
        wrapcontext: WrapContext,
        pane: PaneSel,
    ) -> bool:
        search_id, context = wrapcontext
        if search_id == panectl.outstanding_search_id:
            self.emit("search-result", pane, match, match_iter, context)
            return False

        self.output_debug("Skipping late search", match, context)
        return True

    def select(self, pane: PaneSel, item: KupferObject | None) -> None:
        """Select @item in @pane to self-update
        relevant places"""
        # If already selected, do nothing
        panectl = self._panectl_table[pane]
        if item == panectl.get_selection():
            return

        self.cancel_search()
        panectl.select(item)  # type: ignore
        if pane == PaneSel.SOURCE:
            # populate actions
            citem = self._get_pane_object_composed(self.source_pane)
            self.action_pane.set_item(citem)
            self.search(PaneSel.ACTION, interactive=True)
            if self.mode == PaneMode.SOURCE_ACTION_OBJECT:
                self.object_stack_clear(PaneSel.OBJECT)
                self._populate_third_pane()

        elif pane == PaneSel.ACTION:
            assert item is None or isinstance(item, Action), str(type(item))
            self.object_stack_clear(PaneSel.OBJECT)
            if item and item.requires_object():
                newmode = PaneMode.SOURCE_ACTION_OBJECT
            else:
                newmode = PaneMode.SOURCE_ACTION

            if newmode != self.mode:
                self.mode = newmode
                self.emit("mode-changed", self.mode, item)

            if self.mode == PaneMode.SOURCE_ACTION_OBJECT:
                self._populate_third_pane()

    def _populate_third_pane(self) -> None:
        citem = self._get_pane_object_composed(self.source_pane)
        action = self.action_pane.get_selection()
        assert isinstance(action, Action)
        self.object_pane.set_item_and_action(citem, action)
        self.search(PaneSel.OBJECT, lazy=True)

    def get_can_enter_text_mode(self, pane: PaneSel) -> bool:
        panectl = self._panectl_table[pane]
        return panectl.get_can_enter_text_mode()

    def get_should_enter_text_mode(self, pane: PaneSel) -> bool:
        panectl = self._panectl_table[pane]
        return panectl.get_should_enter_text_mode()

    def validate(self) -> None:
        """Check if all selected items are still valid
        (for example after being spawned again, old item
        still focused)

        This will trigger .select() with None if items
        are not valid..
        """

        def valid_check(obj):
            return not (hasattr(obj, "is_valid") and not obj.is_valid())

        for pane, panectl in self._panectl_table.items():
            sel = panectl.get_selection()
            if not valid_check(sel):
                self.emit("pane-reset", pane, None)
                self.select(pane, None)

            if self._has_object_stack(pane):
                new_stack = [o for o in panectl.object_stack if valid_check(o)]
                if new_stack != panectl.object_stack:
                    self._set_object_stack(pane, new_stack)

    def browse_up(self, pane: PaneSel) -> bool:
        """Try to browse up to previous sources, from current
        source"""
        if pane == PaneSel.SOURCE:
            return self.source_pane.browse_up()

        if pane == PaneSel.OBJECT:
            return self.object_pane.browse_up()

        return False

    def browse_down(self, pane: PaneSel, alternate: bool = False) -> None:
        """Browse into @leaf if it's possible
        and save away the previous sources in the stack
        if @alternate, use the Source's alternate method"""
        if pane == PaneSel.ACTION:
            return

        # record used object if we browse down
        panectl = self._panectl_table[pane]
        sel, key = panectl.get_selection(), panectl.get_latest_key()
        if panectl.browse_down(alternate=alternate):
            learn.record_search_hit(sel, key)

    def activate(self, ui_ctx: GUIEnvironmentContext) -> None:
        """
        Activate current selection

        @ui_ctx: GUI environment context object
        """
        leaf, action, sobject = self._get_current_command_objects()

        # register search to learning database
        learn.record_search_hit(leaf, self.source_pane.get_latest_key())
        learn.record_search_hit(action, self.action_pane.get_latest_key())
        if sobject and self.mode == PaneMode.SOURCE_ACTION_OBJECT:
            learn.record_search_hit(sobject, self.object_pane.get_latest_key())

        if not leaf or not action:
            return

        try:
            ctx = self._execution_context
            res, _ret = ctx.run(leaf, action, sobject, ui_ctx=ui_ctx)
        except commandexec.ActionExecutionError:
            self.output_exc()
            return

        if not res.is_sync:
            self.emit("launched-action")

    def execute_file(
        self,
        filepath: ty.Iterable[str],
        ui_ctx: GUIEnvironmentContext,
        on_error: ty.Callable[[commandexec.ExecInfo], None],
    ) -> bool:
        # TODO: check: this was not supported by file path may be [str]
        # so probably this should be run in loop
        assert isinstance(filepath, (list, tuple))
        ctx = self._execution_context
        try:
            for sfp in filepath:
                cmd_objs = execfile.parse_kfcom_file(sfp)
                ctx.run(*cmd_objs, ui_ctx=ui_ctx)

            return True
        except commandexec.ActionExecutionError:
            self.output_exc()
        except execfile.ExecutionError:
            on_error(sys.exc_info())

        return False

    def _insert_object(self, pane: PaneSel, obj: KupferObject) -> None:
        "Insert @obj in @pane: prepare the object, then emit pane-reset"
        self._decorate_object(obj)
        self.emit("pane-reset", pane, search.wrap_rankable(obj))

    def _decorate_object(self, *objects: KupferObject) -> None:
        sctl = GetSourceController()
        for obj in objects:
            sctl.decorate_object(obj)

    def insert_objects(self, pane: PaneSel, objects: list[Leaf]) -> None:
        "Select @objects in @pane"
        if pane != PaneSel.SOURCE:
            raise ValueError("Can only insert in first pane")

        ic(objects)
        # FIXME: !!check; added * before objects
        self._decorate_object(*objects[:-1])
        self._set_object_stack(pane, objects[:-1])
        self._insert_object(pane, objects[-1])

    def _command_execution_result(
        self,
        ctx: commandexec.ActionExecutionContext,
        result_type: commandexec.ExecResult | int,
        ret: ty.Any,
        uictx: GUIEnvironmentContext,
    ) -> None:
        result_type = commandexec.ExecResult(result_type)
        if result_type == commandexec.ExecResult.SOURCE:
            self.object_stack_clear_all()
            self.source_pane.push_source(ret)
        elif result_type == commandexec.ExecResult.OBJECT:
            self.object_stack_clear_all()
            self._insert_object(PaneSel.SOURCE, ret)
        else:
            return

        self.emit("command-result", result_type, uictx)

    def _late_command_execution_result(
        self,
        ctx: commandexec.ActionExecutionContext,
        id_: int,
        result_type: commandexec.ExecResult | int,
        ret: ty.Any,
        uictx: GUIEnvironmentContext,
    ) -> None:
        "Receive late command result"
        if self._latest_interaction < id_:
            self._command_execution_result(ctx, result_type, ret, uictx)

    def find_object(self, url: str) -> None:
        """Find object with URI @url and select it in the first pane"""
        sc = GetSourceController()
        qf = qfurl.qfurl(url=url)
        found = qf.resolve_in_catalog(sc.sources)
        if found and not found == self.source_pane.get_selection():
            self._insert_object(PaneSel.SOURCE, found)

    def mark_as_default(self, pane: PaneSel) -> None:
        """
        Make the object selected on @pane as default
        for the selection in previous pane.
        """
        if pane in (PaneSel.SOURCE, PaneSel.OBJECT):
            raise RuntimeError("Setting default on pane 1 or 3 not supported")

        obj = self.source_pane.get_selection()
        act = self.action_pane.get_selection()
        assert obj and act
        learn.set_correlation(act, obj)

    def get_object_has_affinity(self, pane: PaneSel) -> bool:
        """
        Return ``True`` if we have any recorded affinity
        for the object selected in @pane
        """
        panectl = self._panectl_table[pane]
        if selection := panectl.get_selection():
            return learn.get_object_has_affinity(selection)

        return False

    def erase_object_affinity(self, pane: PaneSel) -> None:
        """
        Erase all learned and configured affinity for
        the selection of @pane
        """
        panectl = self._panectl_table[pane]
        if selection := panectl.get_selection():
            learn.erase_object_affinity(selection)

    def compose_selection(self) -> None:
        leaf, action, iobj = self._get_current_command_objects()
        if leaf is None:
            return

        self.object_stack_clear_all()
        obj = compose.ComposedLeaf(leaf, action, iobj)
        self._insert_object(PaneSel.SOURCE, obj)

    def _get_pane_object_composed(self, pane):
        objects = list(pane.object_stack)
        sel = pane.get_selection()
        if sel and sel not in objects:
            objects.append(sel)

        if not objects:
            return None

        if len(objects) == 1:
            return objects[0]

        return compose.MultipleLeaf(objects)

    def _get_current_command_objects(self):
        """
        Return a tuple of current (obj, action, iobj)
        """
        objects = self._get_pane_object_composed(self.source_pane)
        action = self.action_pane.get_selection()
        if objects is None or action is None:
            return (None, None, None)

        iobjects = self._get_pane_object_composed(self.object_pane)
        if self.mode == PaneMode.SOURCE_ACTION_OBJECT:
            if not iobjects:
                return (None, None, None)

        else:
            iobjects = None

        return (objects, action, iobjects)

    def _has_object_stack(self, pane):
        return pane in (PaneSel.SOURCE, PaneSel.OBJECT)

    def _set_object_stack(self, pane, newstack):
        panectl = self._panectl_table[pane]
        panectl.object_stack[:] = list(newstack)
        self.emit("object-stack-changed", pane)

    def object_stack_push(self, pane, object_):
        """
        Push @object_ onto the stack
        """
        if not self._has_object_stack(pane):
            return

        panectl = self._panectl_table[pane]
        if object_ not in panectl.object_stack:
            panectl.object_stack_push(object_)
            self.emit("object-stack-changed", pane)

        return True

    def object_stack_pop(self, pane):
        if not self._has_object_stack(pane):
            return

        panectl = self._panectl_table[pane]
        obj = panectl.object_stack_pop()
        self._insert_object(pane, obj)
        self.emit("object-stack-changed", pane)
        return True

    def object_stack_clear(self, pane):
        if not self._has_object_stack(pane):
            return

        panectl = self._panectl_table[pane]
        panectl.object_stack[:] = []
        self.emit("object-stack-changed", pane)

    def object_stack_clear_all(self):
        """
        Clear the object stack for all panes
        """
        for pane in self._panectl_table:
            self.object_stack_clear(pane)

    def get_object_stack(self, pane):
        if not self._has_object_stack(pane):
            return ()

        panectl = self._panectl_table[pane]
        return panectl.object_stack


# pane cleared or set with new item
# pane, item
GObject.signal_new(
    "pane-reset",
    DataController,
    GObject.SignalFlags.RUN_LAST,
    GObject.TYPE_BOOLEAN,
    (
        GObject.TYPE_INT,
        GObject.TYPE_PYOBJECT,
    ),
)

# pane, match, iter to matches, context
GObject.signal_new(
    "search-result",
    DataController,
    GObject.SignalFlags.RUN_LAST,
    GObject.TYPE_BOOLEAN,
    (
        GObject.TYPE_INT,
        GObject.TYPE_PYOBJECT,
        GObject.TYPE_PYOBJECT,
        GObject.TYPE_PYOBJECT,
    ),
)

GObject.signal_new(
    "source-changed",
    DataController,
    GObject.SignalFlags.RUN_LAST,
    GObject.TYPE_BOOLEAN,
    (int, object, bool),
)

# mode, None(?)
GObject.signal_new(
    "mode-changed",
    DataController,
    GObject.SignalFlags.RUN_LAST,
    GObject.TYPE_BOOLEAN,
    (
        GObject.TYPE_INT,
        GObject.TYPE_PYOBJECT,
    ),
)

# object stack update signal
# arguments: pane
GObject.signal_new(
    "object-stack-changed",
    DataController,
    GObject.SignalFlags.RUN_LAST,
    GObject.TYPE_BOOLEAN,
    (GObject.TYPE_INT,),
)
# when an command returned a result
# arguments: result type, gui_context
GObject.signal_new(
    "command-result",
    DataController,
    GObject.SignalFlags.RUN_LAST,
    GObject.TYPE_BOOLEAN,
    (GObject.TYPE_INT, GObject.TYPE_PYOBJECT),
)

# when an action was launched
# arguments: none
GObject.signal_new(
    "launched-action",
    DataController,
    GObject.SignalFlags.RUN_LAST,
    GObject.TYPE_BOOLEAN,
    (),
)

from __future__ import annotations

import itertools
import operator
import typing as ty

from gi.repository import GObject

from kupfer.obj import base
from kupfer.obj.base import (
    Action,
    AnySource,
    KupferObject,
    Leaf,
    Source,
    TextSource,
)
from kupfer.support import datatools, pretty
from kupfer.support.itertools import peekfirst

from . import actioncompat, search
from .search import Rankable
from .sources import get_source_controller


T = ty.TypeVar("T")

ItemCheckFunc = ty.Callable[[ty.Iterable[T]], ty.Iterable[T]]
DecoratorFunc = ty.Callable[[ty.Iterable[Rankable]], ty.Iterable[Rankable]]


def _identity(x: ty.Any) -> ty.Any:
    return x


def _dress_leaves(
    seq: ty.Iterable[Rankable], action: Action | None
) -> ty.Iterable[Rankable]:
    """yield items of @seq "dressed" by the source controller"""
    sctr = get_source_controller()
    decorate_object = sctr.decorate_object
    for itm in seq:
        decorate_object(itm.object, action=action)  # type:ignore
        yield itm


def _as_set_iter(seq: ty.Iterable[Rankable]) -> ty.Iterable[Rankable]:
    key = operator.attrgetter("object")
    return datatools.unique_iterator(seq, key=key)


def _valid_check(seq: ty.Iterable[Rankable]) -> ty.Iterable[Rankable]:
    """yield items of @seq that are valid"""
    for itm in seq:
        obj = itm.object
        if (not hasattr(obj, "is_valid")) or obj.is_valid():  # type:ignore
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

    # pylint: disable=too-many-locals,too-many-branches
    def search(
        self,
        sources_: ty.Iterable[Source | TextSource | ty.Iterable[KupferObject]],
        key: str,
        score: bool = True,
        item_check: ItemCheckFunc[ty.Any] | None = None,
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
        key = key.lower()

        if not self._old_key or not key.startswith(self._old_key):
            self._source_cache.clear()

        self._old_key = key

        # General strategy: Extract a `list` from each source,
        # and perform ranking as in place operations on lists
        item_check = item_check or _identity
        decorator = decorator or _identity
        start_time = pretty.timing_start()
        match_lists: list[Rankable] = []
        for src in sources_:
            fixedrank = 0
            can_cache = True
            src_hash = None
            if hasattr(src, "__iter__"):
                rankables = search.make_rankables(item_check(src))  # type: ignore
                can_cache = False
            else:
                src_hash = hash(src)
                # Look in source cache for stored rankables
                try:
                    rankables = self._source_cache[src_hash]
                except KeyError:
                    try:
                        # TextSources
                        items = src.get_text_items(key)  # type: ignore
                        fixedrank = src.get_rank()  # type: ignore
                        can_cache = False
                    except AttributeError:
                        # Source
                        items = src.get_leaves()  # type: ignore

                    rankables = search.make_rankables(item_check(items))

            assert rankables is not None

            if score:
                if fixedrank:
                    rankables = search.add_rank_objects(rankables, fixedrank)
                elif key:
                    rankables = search.bonus_objects(
                        search.score_objects(rankables, key), key
                    )

                if can_cache:
                    rankables = tuple(rankables)
                    self._source_cache[src_hash] = rankables

            match_lists.extend(rankables)

        matches = search.find_best_sort(match_lists) if score else match_lists

        # Check if the items are valid as the search
        # results are accessed through the iterators
        unique_matches = _as_set_iter(matches)
        match, match_iter = peekfirst(decorator(_valid_check(unique_matches)))
        pretty.timing_step(__name__, start_time, "ranked")
        return match, match_iter

    def rank_actions(
        self,
        objects: ty.Iterable[KupferObject],
        key: str,
        leaf: Leaf | None,
        item_check: ItemCheckFunc[KupferObject] | None = None,
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
        key = key.lower()

        rankables = search.make_rankables(item_check(objects))
        if key:
            rankables = search.score_objects(rankables, key)
            matches = search.bonus_actions(rankables, key)
        else:
            matches = search.score_actions(rankables, leaf)

        sorted_matches = sorted(
            matches, key=operator.attrgetter("rank"), reverse=True
        )

        match, match_iter = peekfirst(decorator(sorted_matches))
        return match, match_iter


WrapContext = tuple[int, ty.Any]


class Pane(GObject.GObject):  # type:ignore
    """
    signals:
        search-result (match, match_iter, context)
    """

    __gtype_name__ = "Pane"

    def __init__(self):
        super().__init__()
        self.selection: Leaf | None = None
        self.latest_key: str | None = None
        self.outstanding_search: int = -1
        self.outstanding_search_id: int = -1
        self.searcher = Searcher()

    def select(self, item: Leaf | None) -> None:
        self.selection = item

    def get_selection(self) -> Leaf | None:
        return self.selection

    def reset(self) -> None:
        self.selection = None
        self.latest_key = None

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
        # source_stack keep track on history selected sources and leaves
        self._source_stack: list[tuple[AnySource, Leaf | None]] = []
        self._source: AnySource | None = None
        self.object_stack: list[KupferObject] = []

    def select(self, item: KupferObject | None) -> None:
        assert item is None or isinstance(
            item, Leaf
        ), "New selection for object pane is not a Leaf!"
        super().select(item)

    def _load_source(self, src: AnySource) -> AnySource:
        """Try to get a source from the SourceController,
        if it is already loaded we get it from there, else
        returns @src"""
        sctr = get_source_controller()
        return sctr.get_canonical_source(src)

    def get_source(self) -> AnySource | None:
        return self._source

    def source_rebase(self, src: AnySource) -> None:
        self._source_stack.clear()
        self._source = self._load_source(src)
        self.refresh_data()

    def push_source(self, src: AnySource) -> None:
        if self._source:
            self._source_stack.append((self._source, self.selection))

        self._source = self._load_source(src)
        self.refresh_data()

    def _pop_source(self) -> bool:
        """Return True if succeeded"""
        if self._source_stack:
            self._source, self.selection = self._source_stack.pop()
            return True

        return False

    def is_at_source_root(self) -> bool:
        """Return True if we have no source stack"""
        return not self._source_stack

    def object_stack_push(self, obj: KupferObject) -> None:
        self.object_stack.append(obj)

    def object_stack_pop(self) -> KupferObject:
        return self.object_stack.pop()

    def get_can_enter_text_mode(self) -> bool:
        return self.is_at_source_root()

    def get_should_enter_text_mode(self) -> bool:
        return False

    def refresh_data(self, select: ty.Any = None) -> None:
        self.emit("new-source", self._source, select)

    def browse_up(self) -> bool:
        """Try to browse up to previous sources, from current
        source"""
        succ = self._pop_source()
        if not succ:
            assert self._source
            if self._source.has_parent():
                self.source_rebase(self._source.get_parent())  # type:ignore
                succ = True

        if succ:
            self.refresh_data(select=self.selection)

        return succ

    def browse_down(self, alternate: bool = False) -> bool:
        """Browse into @leaf if it's possible
        and save away the previous sources in the stack
        if @alternate, use the Source's alternate method"""
        leaf: Leaf = self.get_selection()  # type: ignore
        if leaf and leaf.has_content():
            if csrc := leaf.content_source(alternate=alternate):
                self.push_source(csrc)
                return True

        return False

    def reset(self) -> None:
        """Pop all sources and go back to top level"""
        Pane.reset(self)
        while self._pop_source():
            pass

        self.refresh_data()

    def soft_reset(self) -> ty.Optional[AnySource]:
        Pane.reset(self)
        while self._pop_source():
            pass

        return self._source

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
        sources_: ty.Iterable[AnySource] = ()
        if not text_mode:
            if srcs := self.get_source():
                sources_ = (srcs,)

        if key and self.is_at_source_root():
            # Only use text sources when we are at root catalog
            sctr = get_source_controller()
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
    (GObject.TYPE_PYOBJECT, GObject.TYPE_PYOBJECT),
)


class PrimaryActionPane(Pane):
    def __init__(self):
        super().__init__()
        self._action_valid_cache: dict[int, bool] = {}
        self.set_item(None)

    def select(self, item: Leaf | None) -> None:
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
        actions = actioncompat.actions_for_item(leaf, get_source_controller())
        cache = self._action_valid_cache

        def valid_decorator(seq):
            """Check if actions are valid before access"""
            for obj in seq:
                action = obj.object
                action_hash = hash(action)
                valid = cache.get(action_hash)
                if valid is None:
                    valid = actioncompat.action_valid_for_item(action, leaf)  # type: ignore
                    cache[action_hash] = valid

                if valid:
                    yield obj

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
                sctr = get_source_controller()
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
        sctr = get_source_controller()
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
        if not self.current_item:
            return

        self.latest_key = key
        sources_: ty.Iterable[AnySource] = []
        if not text_mode or hasattr(self.get_source(), "get_text_items"):
            if srcs := self.get_source():
                sources_ = itertools.chain(sources_, (srcs,))

        if key and self.is_at_source_root():
            # Only use text sources when we are at root catalog
            sctr = get_source_controller()
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

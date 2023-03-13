from __future__ import annotations

import operator
import typing as ty

from kupfer.obj.base import KupferObject, Leaf, Source, TextSource
from kupfer.support import datatools, pretty
from kupfer.support.itertools import peekfirst

from . import search
from .search import Rankable

T = ty.TypeVar("T")
ItemCheckFunc = ty.Callable[[ty.Iterable[T]], ty.Iterable[T]]

DecoratorFunc = ty.Callable[[ty.Iterable[Rankable]], ty.Iterable[Rankable]]


def _identity(x: ty.Any) -> ty.Any:
    return x


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

    def reset(self):
        self._source_cache.clear()
        self._old_key = None

    # pylint: disable=too-many-locals,too-many-branches
    def search(
        self,
        sources_: ty.Iterable[Source | TextSource | ty.Iterable[KupferObject]],
        key: str,
        score: bool = True,
        item_check: ItemCheckFunc[KupferObject] | None = None,
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

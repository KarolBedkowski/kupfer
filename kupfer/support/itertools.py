#! /usr/bin/env python3
# Distributed under terms of the GPLv3 license.
"""
Support function for iterators
"""

from __future__ import annotations

import itertools
import typing as ty


def two_part_mapper(instr: str, repfunc: ty.Callable[[str], str | None]) -> str:
    """
    Scan @instr two characters at a time and replace using @repfunc.
    If @repfunc return not None - use origin character.
    """
    if not instr:
        return instr

    def _inner():
        sit = zip(instr, instr[1:])
        for cur, nex in sit:
            key = cur + nex
            if (rep := repfunc(key)) is not None:
                yield rep
                # skip a step in the iter
                try:
                    next(sit)
                except StopIteration:
                    return

            else:
                yield cur

        yield instr[-1]

    return "".join(_inner())


T = ty.TypeVar("T")


def peekfirst(
    seq: ty.Iterable[T],
) -> tuple[T | None, ty.Iterable[T]]:
    """This function will return (firstitem, iter) where firstitem is the first
    item of `seq` or None if empty, and iter an equivalent copy of `seq`
    """
    seq = iter(seq)
    for itm in seq:
        old_iter = itertools.chain((itm,), seq)
        return (itm, old_iter)

    return (None, seq)

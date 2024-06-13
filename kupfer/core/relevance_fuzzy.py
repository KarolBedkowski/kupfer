"""
Scoring using fuzzing string matching.
Require rapidfuzz library.

Distributed under terms of the GPLv3 license.
"""

import typing as ty

from rapidfuzz import fuzz

from kupfer.support import kupferstring, pretty


def _score(string: str, query: str) -> float:
    string = string.lower()
    if string == query:
        return 1.0

    return fuzz.ratio(query, string, processor=kupferstring.tofolded) / 100.0  # type: ignore


def _score_partial(string: str, query: str) -> float:
    string = string.lower()
    if string == query:
        return 1.0

    return (  # type: ignore
        fuzz.partial_ratio(query, string, processor=kupferstring.tofolded)
        / 100.0
    )


def _score_token_set(string: str, query: str) -> float:
    string = string.lower()
    if string == query:
        return 1.0

    return (  # type: ignore
        fuzz.token_set_ratio(query, string, processor=kupferstring.tofolded)
        / 100.0
    )


def _score_partial_token_set(string: str, query: str) -> float:
    string = string.lower()
    if string == query:
        return 1.0

    return (  # type: ignore
        fuzz.partial_token_set_ratio(
            query, string, processor=kupferstring.tofolded
        )
        / 100.0
    )


def _score_token(string: str, query: str) -> float:
    string = string.lower()
    if string == query:
        return 1.0

    return (  # type: ignore
        fuzz.token_ratio(query, string, processor=kupferstring.tofolded)
        / 100.0
    )


def _score_partial_token(string: str, query: str) -> float:
    string = string.lower()
    if string == query:
        return 1.0

    return (  # type: ignore
        fuzz.partial_token_ratio(
            query, string, processor=kupferstring.tofolded
        )
        / 100.0
    )


class ScoreFunction(ty.Protocol):
    def __call__(self, string: str, query: str) -> float: ...


def get_score_function(method: str) -> ScoreFunction:
    if method in ("indel", "standard"):
        return _score

    if method == "token_set":
        return _score_token_set

    if method == "partial_token_set":
        return _score_partial_token_set

    if method == "token":
        return _score_token

    if method == "partial_token":
        return _score_partial_token

    if method in ("partial", ""):
        return _score_partial

    pretty.print_error(
        __name__,
        f"unknown fuzzy method '{method}'; fallback to 'partial', "
        f"see {__file__} for available options",
    )

    return _score_partial

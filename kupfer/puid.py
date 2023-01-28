"""
Persistent Globally Unique Indentifiers for KupferObjects.

Some objects are assigned identifiers by reference, some are assigned
identifiers containing the whole object data (SerializedObject).

SerializedObject is a saved representation of a KupferObject, i.e. a
data model user-level object.

We unpickle SerializedObjects in an especially conservative way: new
module loading is always refused; this way, we avoid loading parts of
the program that we didn't wish to activate.
"""

import contextlib
import pickle
import typing as ty

from kupfer import pretty
from kupfer.core import actioncompat
from kupfer.core import qfurl
from kupfer.obj.base import Source, Leaf, Action, AnySource
from kupfer.core.sources import GetSourceController
from kupfer.conspickle import ConservativeUnpickler

__all__ = [
    "SerializedObject",
    "SERIALIZABLE_ATTRIBUTE",
    "resolve_unique_id",
    "resolve_action_id",
    "get_unique_id",
    "is_reference",
]


SERIALIZABLE_ATTRIBUTE = "serializable"


class SerializedObject:
    # treat the serializable attribute as a version number, defined on the class
    def __init__(self, obj: Leaf) -> None:
        self.version = getattr(obj, SERIALIZABLE_ATTRIBUTE)
        self.data = pickle.dumps(obj, pickle.HIGHEST_PROTOCOL)

    def __hash__(self) -> int:
        return hash(self.data)

    def __eq__(self, other: ty.Any) -> bool:
        return (
            isinstance(other, type(self))
            and self.data == other.data
            and self.version == other.version
        )

    def reconstruct(self) -> Leaf:
        obj = ConservativeUnpickler.loads(self.data)
        if self.version != getattr(obj, SERIALIZABLE_ATTRIBUTE):
            raise ValueError(f"Version mismatch for reconstructed {obj}")

        return obj  # type: ignore


PuID = ty.Union[str, SerializedObject]


def get_unique_id(obj: ty.Any) -> ty.Optional[PuID]:
    if obj is None:
        return None

    if hasattr(obj, "qf_id"):
        return str(qfurl.qfurl(obj))

    if getattr(obj, SERIALIZABLE_ATTRIBUTE, None) is not None:
        try:
            return SerializedObject(obj)
        except pickle.PicklingError as exc:
            pretty.print_error(__name__, type(exc).__name__, exc)
            return None

    return repr(obj)


def is_reference(puid: ty.Any) -> bool:
    "Return True if @puid is a reference-type ID"
    return not isinstance(puid, SerializedObject)


# A Context manager to block recursion when seeking inside a
# catalog; we have a stack (@_EXCLUDING) of the sources we
# are visiting, and nested context with the _exclusion
# context manager

_EXCLUDING: list[AnySource] = []


@contextlib.contextmanager
def _exclusion(src: AnySource) -> ty.Iterator[None]:
    try:
        _EXCLUDING.append(src)
        yield
    finally:
        _EXCLUDING.pop()


def _is_currently_excluding(src: ty.Any) -> bool:
    return src is not None and src in _EXCLUDING


def _find_obj_in_catalog(
    puid: str, catalog: ty.Collection[AnySource]
) -> ty.Optional[Leaf]:
    if puid.startswith(qfurl.QFURL_SCHEME):
        qfu = qfurl.qfurl(url=puid)
        return qfu.resolve_in_catalog(catalog)

    for src in catalog:
        if _is_currently_excluding(src):
            continue

        with _exclusion(src):
            for obj in src.get_leaves() or []:
                if repr(obj) == puid:
                    return obj

    return None


def resolve_unique_id(
    puid: ty.Any, excluding: ty.Optional[AnySource] = None
) -> ty.Optional[Leaf]:
    """
    Resolve unique id @puid

    The caller (if a Source) should pass itself as @excluding,
    so that recursion into itself is avoided.
    """
    if excluding is not None:
        with _exclusion(excluding):
            return resolve_unique_id(puid, None)

    if puid is None:
        return None

    if isinstance(puid, SerializedObject):
        try:
            return puid.reconstruct()
        except Exception as exc:
            pretty.print_debug(__name__, type(exc).__name__, exc)
            return None

    sctl = GetSourceController()
    if (obj := _find_obj_in_catalog(puid, sctl.firstlevel)) is not None:
        return obj

    other_sources = set(sctl.sources) - set(sctl.firstlevel)
    return _find_obj_in_catalog(puid, other_sources)


def resolve_action_id(
    puid: ty.Any, for_item: ty.Optional[Leaf] = None
) -> ty.Optional[Action]:
    if puid is None:
        return None

    if isinstance(puid, SerializedObject):
        return resolve_unique_id(puid)  # type: ignore

    sctr = GetSourceController()
    if for_item is not None:
        for action in actioncompat.actions_for_item(for_item, sctr):
            if get_unique_id(action) == puid:
                return action

    get_action_id = repr
    for actions in sctr.action_decorators.values():
        for action in actions:
            if get_action_id(action) == puid:
                return action

    pretty.print_debug(__name__, f"Unable to resolve {puid} ({for_item})")
    return None

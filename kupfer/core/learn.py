import pickle
import os
from pathlib import Path
import random
import typing as ty

from kupfer import config
from kupfer import conspickle
from kupfer import pretty

_MNEMONICS_FILENAME = "mnemonics.pickle"
_CORRELATION_KEY = "kupfer.bonus.correlation"

## this is a harmless default
_DEFAULT_ACTIONS = {
    "<builtin.AppLeaf gnome-terminal>": "<builtin.LaunchAgain>",
    "<builtin.AppLeaf xfce4-terminal>": "<builtin.LaunchAgain>",
}
_FAVORITES = set()


class Mnemonics:
    """
    Class to describe a collection of mnemonics
    as well as the total count
    """

    def __init__(self):
        self.mnemonics: ty.Dict[str, int] = {}
        self.count: int = 0

    def __repr__(self):
        mnm = "".join(f"{m}: {c}, " for m, c in self.mnemonics.items())
        return f"<{self.__class__.__name__} {self.count} {mnm}>"

    def increment(self, mnemonic=None):
        if mnemonic:
            mcount = self.mnemonics.get(mnemonic, 0)
            self.mnemonics[mnemonic] = mcount + 1

        self.count += 1

    def decrement(self):
        """Decrement total count and the least mnemonic"""
        if self.mnemonics:
            key = min(self.mnemonics, key=lambda k: self.mnemonics[k])
            if self.mnemonics[key] <= 1:
                del self.mnemonics[key]
            else:
                self.mnemonics[key] -= 1

        self.count = max(self.count - 1, 0)

    def __bool__(self):
        return self.count > 0

    def get_count(self) -> int:
        return self.count

    def get_mnemonics(self) -> ty.Dict[str, int]:
        return self.mnemonics


class Learning:
    @classmethod
    def _unpickle_register(cls, pickle_file):
        try:
            pfile = Path(pickle_file).read_bytes()
            data = conspickle.ConservativeUnpickler.loads(pfile)
            assert isinstance(data, dict), "Stored object not a dict"
            pretty.print_debug(__name__, f"Reading from {pickle_file}")
        except OSError:
            return None
        except (pickle.PickleError, Exception) as exc:
            data = None
            pretty.print_error(__name__, f"Error loading {pickle_file}: {exc}")

        return data

    @classmethod
    def _pickle_register(cls, reg, pickle_file):
        ## Write to tmp then rename over for atomicity
        tmp_pickle_file = f"{pickle_file}.{os.getpid()}"
        pretty.print_debug(__name__, f"Saving to {pickle_file}")
        Path(tmp_pickle_file).write_bytes(
            pickle.dumps(reg, pickle.HIGHEST_PROTOCOL)
        )
        os.rename(tmp_pickle_file, pickle_file)
        return True


_REGISTER: ty.Dict[str, Mnemonics] = {}


def record_search_hit(obj: ty.Any, key: str = "") -> None:
    """
    Record that KupferObject @obj was used, with the optional
    search term @key recording
    """
    name = repr(obj)
    if name not in _REGISTER:
        _REGISTER[name] = Mnemonics()

    _REGISTER[name].increment(key)


def get_record_score(obj: ty.Any, key: str = "") -> float:
    """
    Get total score for KupferObject @obj,
    bonus score is given for @key matches
    """
    name = repr(obj)
    fav = 7 * (name in _FAVORITES)
    if name not in _REGISTER:
        return fav

    mns = _REGISTER[name]
    if not key:
        cnt = mns.get_count()
        return fav + 50 * (1 - 1.0 / (cnt + 1))

    stats = mns.get_mnemonics()
    closescr = sum(stats[m] for m in stats if m.startswith(key))
    mnscore = 30 * (1 - 1.0 / (closescr + 1))
    exact = stats.get(key, 0)
    mnscore += 50 * (1 - 1.0 / (exact + 1))
    return fav + mnscore


def get_correlation_bonus(obj, for_leaf):
    """
    Get the bonus rank for @obj when used with @for_leaf
    """
    if _REGISTER.setdefault(_CORRELATION_KEY, {}).get(repr(for_leaf)) == repr(
        obj
    ):
        return 50

    return 0


def set_correlation(obj, for_leaf):
    """
    Register @obj to get a bonus when used with @for_leaf
    """
    _REGISTER.setdefault(_CORRELATION_KEY, {})[repr(for_leaf)] = repr(obj)


def _get_mnemonic_items(in_register):
    return [(k, v) for k, v in in_register.items() if k != _CORRELATION_KEY]


def get_object_has_affinity(obj):
    """
    Return if @obj has any positive score in the register
    """
    return bool(
        _REGISTER.get(repr(obj))
        or _REGISTER.get(_CORRELATION_KEY, {}).get(repr(obj))
    )


def erase_object_affinity(obj):
    """
    Remove all track of affinity for @obj
    """
    _REGISTER.pop(repr(obj), None)
    _REGISTER.get(_CORRELATION_KEY, {}).pop(repr(obj), None)


def _prune_register():
    """
    Remove items with chance (len/25000)

    Assuming homogenous records (all with score one) we keep:
    x_n+1 := x_n * (1 - chance)

    To this we have to add the expected number of added mnemonics per
    invocation, est. 10, and we can estimate a target number of saved mnemonics.
    """
    random.seed()
    rand = random.random

    goalitems = 500
    flux = 2.0
    alpha = flux / goalitems**2

    chance = min(0.1, len(_REGISTER) * alpha)
    for leaf, mn in _get_mnemonic_items(_REGISTER):
        if rand() > chance:
            continue

        mn.decrement()
        if not mn:
            _REGISTER.pop(leaf)

    pretty.print_debug(
        __name__, f"Pruned register ({len(_REGISTER)} mnemonics)"
    )


def load():
    """
    Load learning database
    """
    global _REGISTER

    filepath = config.get_config_file(_MNEMONICS_FILENAME)
    if filepath:
        _REGISTER = Learning._unpickle_register(filepath)

    if not _REGISTER:
        _REGISTER = {}

    if _CORRELATION_KEY not in _REGISTER:
        _REGISTER[_CORRELATION_KEY] = _DEFAULT_ACTIONS


def save():
    """
    Save the learning record
    """
    if not _REGISTER:
        pretty.print_debug(__name__, "Not writing empty register")
        return

    if len(_REGISTER) > 100:
        _prune_register()

    filepath = config.save_config_file(_MNEMONICS_FILENAME)
    Learning._pickle_register(_REGISTER, filepath)


def add_favorite(obj):
    _FAVORITES.add(repr(obj))


def remove_favorite(obj):
    _FAVORITES.discard(repr(obj))


def is_favorite(obj):
    return repr(obj) in _FAVORITES

"""
Module for confiugration and misc things
"""
import os
from pathlib import Path
import typing as ty

import xdg.BaseDirectory as base

PACKAGE_NAME = "kupfer"


class ResourceLookupError(Exception):
    pass


def has_capability(cap: str) -> bool:
    return not bool(os.getenv(f"KUPFER_NO_{cap}"))


def get_kupfer_env(name: str, default: str = "") -> str:
    return os.getenv(f"KUPFER_{name}", default)


def get_cache_home() -> ty.Optional[str]:
    """
    Directory where cache files should be put
    Guaranteed to exist
    """
    cache_home = base.xdg_cache_home or os.path.expanduser("~/.cache")
    cache_dir = Path(cache_home, PACKAGE_NAME)
    if not cache_dir.exists():
        try:
            cache_dir.mkdir(mode=0o700)
        except OSError as exc:
            print(exc)
            return None

    return str(cache_dir)


def get_cache_file(path: ty.Tuple[str, ...] = ()) -> ty.Optional[str]:
    cache_home = base.xdg_cache_home or os.path.expanduser("~/.cache")
    cache_dir = Path(cache_home, *path)
    if not cache_dir.exists():
        return None

    return str(cache_dir)


def get_data_file(filename: str, package: str = PACKAGE_NAME) -> str:
    """
    Return path to @filename if it exists
    anywhere in the data paths, else raise ResourceLookupError.
    """
    try:
        from . import version_subst
    except ImportError:
        first_datadir = "./data"
    else:
        first_datadir = os.path.join(version_subst.DATADIR, package)

    file_path = Path(first_datadir, filename)
    if file_path.exists():
        return str(file_path)

    for data_path in base.load_data_paths(package):
        file_path = Path(data_path, filename)
        if file_path.exists():
            return str(file_path)

    if package == PACKAGE_NAME:
        raise ResourceLookupError(f"Resource {filename} not found")

    raise ResourceLookupError(
        f"Resource {filename} in package {package} not found"
    )


def save_data_file(filename: str) -> ty.Optional[str]:
    """
    Return filename in the XDG data home directory, where the
    directory is guaranteed to exist
    """
    direc = base.save_data_path(PACKAGE_NAME)
    if not direc:
        return None

    return os.path.join(direc, filename)


def get_data_home() -> str:
    """
    Directory where data is to be saved
    Guaranteed to exist
    """
    return base.save_data_path(PACKAGE_NAME)  # type: ignore


def get_data_dirs(
    name: str = "", package: str = PACKAGE_NAME
) -> ty.Iterable[str]:
    """
    Iterate over all data dirs of @name that exist
    """
    return base.load_data_paths(os.path.join(package, name))  # type: ignore


def get_config_file(
    filename: str, package: str = PACKAGE_NAME
) -> ty.Optional[str]:
    """
    Return path to @package/@filename if it exists anywhere in the config
    paths, else return None
    """
    return base.load_first_config(package, filename)  # type: ignore


def get_config_files(filename: str) -> ty.Iterable[str]:
    """
    Iterator to @filename in all
    config paths, with most important (takes precendence)
    files first
    """
    return base.load_config_paths(PACKAGE_NAME, filename) or ()


def save_config_file(filename: str) -> ty.Optional[str]:
    """
    Return filename in the XDG data home directory, where the
    directory is guaranteed to exist
    """
    direc = base.save_config_path(PACKAGE_NAME)
    if not direc:
        return None

    return os.path.join(direc, filename)

import contextlib
import typing as ty

from kupfer import pretty
from kupfer.obj.base import Source, ActionGenerator, Action, AnySource
from kupfer.core import plugins
from kupfer.core.plugins import (
    PluginAttr,
    load_plugin_objects,
    initialize_plugin,
)


class PluginDescription:
    text_sources: list[AnySource] = []
    action_decorators: list[Action] = []
    content_decorators: list[Source] = []
    action_generators: list[ActionGenerator] = []
    sources: list[Source] = []


def load_plugin(plugin_id: str) -> PluginDescription:
    """
    @S_sources are to be included directly in the catalog,
    @s_souces as just as subitems
    """
    sources: list[Source] = []
    text_sources: list[AnySource] = []
    action_decorators: list[Action] = []
    content_decorators: list[Source] = []
    action_generators: list[ActionGenerator] = []

    item = plugin_id

    initialize_plugin(item)
    if not plugins.is_plugin_loaded(item):
        return PluginDescription()

    text_sources.extend(load_plugin_objects(item, PluginAttr.TEXT_SOURCES))
    action_decorators.extend(
        load_plugin_objects(item, PluginAttr.ACTION_DECORATORS)  # type: ignore
    )
    action_generators.extend(
        load_plugin_objects(item, PluginAttr.ACTION_GENERATORS)  # type: ignore
    )

    # Register all Sources as (potential) content decorators
    content_decorators.extend(
        load_plugin_objects(item, PluginAttr.SOURCES, instantiate=False)  # type: ignore
    )
    content_decorators.extend(
        load_plugin_objects(  # type: ignore
            item, PluginAttr.CONTENT_DECORATORS, instantiate=False
        )
    )
    sources.extend(load_plugin_objects(item))  # type: ignore

    desc = PluginDescription()

    desc.text_sources = text_sources
    desc.action_decorators = action_decorators
    desc.content_decorators = content_decorators
    desc.sources = sources
    desc.action_generators = action_generators
    return desc


@contextlib.contextmanager
def exception_guard(name, *args, callback=None, **kwargs):
    "Guard for exceptions, print traceback and call @callback if any is raised"
    try:
        yield
    except Exception:
        import traceback

        pretty.print_error(__name__, f"Loading {name} raised an exception:")
        traceback.print_exc()
        pretty.print_error(__name__, "This error is probably a bug in", name)
        pretty.print_error(__name__, "Please file a bug report")
        if callback is not None:
            callback(*args, **kwargs)


def remove_plugin(plugin_id: str) -> None:
    plugins.unimport_plugin(plugin_id)

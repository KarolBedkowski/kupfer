from __future__ import annotations

import gzip
import hashlib
import itertools
import pickle
import os
import threading
import time
import weakref
from pathlib import Path
import typing as ty

from kupfer import config, pretty, scheduler
from kupfer import conspickle
from kupfer.obj import base, sources
from kupfer.obj.base import Source, Action, ActionGenerator, Leaf, TextSource, AnySource
from kupfer.core import pluginload


class InternalError(Exception):
    pass


class PeriodicRescanner(pretty.OutputMixin):
    """
    Periodically rescan a @catalog of sources

    Do first rescan after @startup seconds, then
    followup with rescans in @period.

    Each campaign of rescans is separarated by @campaign
    seconds
    """

    def __init__(
        self, period: int = 5, startup: int = 10, campaign: int = 3600
    ) -> None:
        self.startup = startup
        self.period = period
        self.campaign = campaign
        self.timer = scheduler.Timer()
        # Source -> time mapping
        self.latest_rescan_time: weakref.WeakKeyDictionary[
            Source, float
        ] = weakref.WeakKeyDictionary()
        self._min_rescan_interval = campaign // 4

    def set_catalog(self, catalog: ty.Iterable[Source]) -> None:
        self.catalog = catalog
        self.cur = iter(self.catalog)
        self.output_debug(f"Registering new campaign, in {self.startup} s")
        self.timer.set(self.startup, self._new_campaign)

    def _new_campaign(self) -> None:
        self.output_info(f"Starting new campaign, interval {self.period} s")
        self.cur = iter(self.catalog)
        self.timer.set(self.period, self._periodic_rescan_helper)

    def _periodic_rescan_helper(self) -> None:
        # Advance until we find a source that was not recently rescanned
        for next in self.cur:
            oldtime = self.latest_rescan_time.get(next, 0)
            if (time.time() - oldtime) > self._min_rescan_interval:
                self.timer.set(self.period, self._periodic_rescan_helper)
                self._start_source_rescan(next)
                return

        # No source to scan found
        self.output_info(f"Campaign finished, pausing {self.campaign} s")
        self.timer.set(self.campaign, self._new_campaign)

    def rescan_now(self, source: Source, force_update: bool = False) -> None:
        "Rescan @source immediately"
        if force_update:
            # if forced update, we know that it was brought up to date
            self.latest_rescan_time[source] = time.time()

        self.rescan_source(source, force_update=force_update)

    def _start_source_rescan(self, source: Source) -> None:
        self.latest_rescan_time[source] = time.time()
        if not source.is_dynamic():
            thread = threading.Thread(
                target=self.rescan_source, args=(source,)
            )
            thread.daemon = True
            thread.start()

    def rescan_source(self, source: Source, force_update: bool = True) -> None:
        list(source.get_leaves(force_update=force_update))


class SourcePickler(pretty.OutputMixin):
    """
    Takes care of pickling and unpickling Kupfer Sources.
    """

    format_version = 5
    name_template = "k%s-v%d.pickle.gz"

    def __init__(self) -> None:
        self.open = lambda f, mode: gzip.open(f, mode, compresslevel=3)

    @classmethod
    def should_use_cache(cls) -> bool:
        return config.has_capability("CACHE")  # type: ignore

    @classmethod
    def should_use_cache_for_source(cls, source: Source) -> bool:
        return cls.should_use_cache() and source.source_use_cache

    def rm_old_cachefiles(self) -> None:
        """Checks if there are old cachefiles from last version,
        and deletes those
        """
        for dpath, _dirs, files in os.walk(config.get_cache_home()):
            # Look for files matching beginning and end of
            # name_template, with the previous file version
            chead, ctail = self.name_template.split("%s")
            ctail = ctail % ((self.format_version - 1),)
            obsolete_files = []
            for cfile in files:
                if cfile.startswith(chead) and cfile.endswith(ctail):
                    cfullpath = os.path.join(dpath, cfile)
                    obsolete_files.append(cfullpath)

        if obsolete_files:
            self.output_info(
                "Removing obsolete cache files:", sep="\n", *obsolete_files
            )
            for fpath in obsolete_files:
                # be overly careful
                assert fpath.startswith(config.get_cache_home())
                assert "kupfer" in fpath
                Path(fpath).unlink()

    def get_filename(self, source: Source) -> str:
        """Return cache filename for @source"""
        # make sure we take the source name into account
        # so that we get a "break" when locale changes
        source_id = f"{source!r}{source}{source.version}"
        hash_str = hashlib.md5(source_id.encode("utf-8")).hexdigest()
        filename = self.name_template % (hash_str, self.format_version)
        return os.path.join(config.get_cache_home(), filename)

    def unpickle_source(self, source: Source) -> ty.Any:
        if not self.should_use_cache_for_source(source):
            return None

        if cached := self._unpickle_source(self.get_filename(source)):
            # check consistency
            if source == cached:
                return cached

            self.output_debug("Cached version mismatches", source)

        return None

    def _unpickle_source(self, pickle_file: str) -> ty.Any:
        try:
            pfile = Path(pickle_file).read_bytes()
            source = pickle.loads(pfile)
            assert isinstance(
                source, base.Source
            ), "Stored object not a Source"
            sname = os.path.basename
            self.output_debug("Loading", source, "from", sname(pickle_file))
            return source
        except OSError:
            return None
        except (pickle.PickleError, Exception) as exc:
            self.output_info(f"Error loading {pickle_file}: {exc}")

        return None

    def pickle_source(self, source: Source) -> bool:
        if self.should_use_cache_for_source(source):
            return self._pickle_source(self.get_filename(source), source)

        return False

    def _pickle_source(self, pickle_file: str, source: Source) -> bool:
        """
        When writing to a file, use pickle.dumps()
        and then write the file in one go --
        if the file is a gzip file, pickler's thousands
        of small writes are very slow
        """
        sname = os.path.basename
        self.output_debug("Storing", source, "as", sname(pickle_file))
        Path(pickle_file).write_bytes(
            pickle.dumps(source, pickle.HIGHEST_PROTOCOL)
        )
        return True


class SourceDataPickler(pretty.OutputMixin):
    """Takes care of pickling and unpickling Kupfer Sources' configuration
    or data.

    The SourceDataPickler requires a protocol of three methods:

    config_save_name()
      Return an ascii name to be used as a token/key for the configuration

    config_save()
      Return an object to be saved as configuration

    config_restore(obj)
      Receive the configuration object `obj' to load
    """

    format_version = 2
    name_template = "config-%s-v%d.pickle"

    def __init__(self) -> None:
        self.open = open

    @classmethod
    def get_filename(cls, source: Source) -> str:
        """Return filename for @source"""
        name = source.config_save_name()
        filename = cls.name_template % (name, cls.format_version)
        return config.save_config_file(filename)

    @classmethod
    def source_has_config(cls, source: Source) -> ty.Any:
        return getattr(source, "config_save_name", None)

    def load_source(self, source: Source) -> None:
        if data := self._load_data(self.get_filename(source)):
            source.config_restore(data)  # type: ignore

    def _load_data(self, pickle_file: str) -> ty.Any:
        try:
            pfile = Path(pickle_file).read_bytes()
            data = conspickle.BasicUnpickler.loads(pfile)
            sname = os.path.basename(pickle_file)
            self.output_debug("Loaded configuration from", sname)
            return data
            # self.output_debug(data)
        except OSError:
            return None
        except (pickle.PickleError, Exception) as exc:
            self.output_error(f"Loading {pickle_file}: {exc}")

        return None

    def save_source(self, source: Source) -> bool:
        return self._save_data(self.get_filename(source), source)

    def _save_data(self, pickle_file: str, source: Source) -> bool:
        sname = os.path.basename(pickle_file)
        obj = source.config_save()  # type: ignore
        try:
            data = pickle.dumps(obj, pickle.HIGHEST_PROTOCOL)
        except pickle.PickleError:
            import traceback

            self.output_error("Unable to save configuration for", source)
            self.output_error("Saving configuration raised an exception:")
            traceback.print_exc()
            self.output_error("Please file a bug report")
            data = None

        if data:
            self.output_debug("Storing configuration for", source, "as", sname)
            ## Write to temporary and rename into place
            tmp_pickle_file = f"{pickle_file}.{os.getpid()}"
            Path(tmp_pickle_file).write_bytes(data)
            os.rename(tmp_pickle_file, pickle_file)

        return True


class SourceController(pretty.OutputMixin):
    """Control sources; loading, pickling, rescanning

    Call .add() to add sources.
    Call .initialize() before use commences.
    """

    _instance = None

    @classmethod
    def instance(cls) -> SourceController:
        """Get instance of SourceController (singleton)"""
        if cls._instance is None:
            cls._instance = SourceController()

        return cls._instance

    def __init__(self):
        self.rescanner = PeriodicRescanner(period=3)
        self.sources: ty.Set[Source] = set()
        self.toplevel_sources: ty.Set[Source] = set()
        self.text_sources: ty.Set[TextSource] = set()
        self.content_decorators: ty.Dict[ty.Any, ty.Set[Source]] = {}
        self.action_decorators: ty.Dict[ty.Any, ty.Set[Action]] = {}
        self.action_generators: ty.List[ActionGenerator] = []
        self.plugin_object_map: weakref.WeakKeyDictionary[
            ty.Any, str
        ] = weakref.WeakKeyDictionary()
        self.loaded_successfully = False
        self.did_finalize_sources = False
        self._pre_root: ty.Optional[ty.List[Source]] = None

    def add(
        self,
        plugin_id: ty.Optional[str],
        srcs: ty.Iterable[Source],
        toplevel: bool = False,
        initialize: bool = False,
    ) -> None:
        self._invalidate_root()
        new_srcs = set(self._try_restore(srcs))
        new_srcs.update(srcs)

        self.sources.update(new_srcs)
        if toplevel:
            self.toplevel_sources.update(new_srcs)

        if initialize:
            self._initialize_sources(new_srcs)
            self._cache_sources(new_srcs)
            self.rescanner.set_catalog(self.sources)

        if plugin_id:
            self._register_plugin_objects(plugin_id, *new_srcs)

    def set_toplevel(self, src: AnySource, toplevel: bool) -> None:
        assert src in self.sources, "Source is not tracked in SourceController"
        self._invalidate_root()
        if toplevel:
            self.toplevel_sources.add(src)
        else:
            self.toplevel_sources.discard(src)

    def _register_plugin_objects(
        self, plugin_id: str, *objects: ty.Any
    ) -> None:
        "Register a plugin id mapping for @objects"
        for obj in objects:
            self.plugin_object_map[obj] = plugin_id
            pretty.print_debug(__name__, "Add", repr(obj))

    def _remove(self, src: AnySource) -> None:
        self._invalidate_root()
        self.toplevel_sources.discard(src)
        self.sources.discard(src)
        self.rescanner.set_catalog(self.sources)
        self._finalize_source(src)
        pretty.print_debug(__name__, "Remove", repr(src))

    def get_plugin_id_for_object(self, obj: ty.Any) -> ty.Optional[str]:
        id_ = self.plugin_object_map.get(obj)
        # self.output_debug("Object", repr(obj), "has id", id_, id(obj))
        return id_

    def remove_objects_for_plugin_id(self, plugin_id: str) -> bool:
        """Remove all objects for @plugin_id

        Return True if the catalog configuration changed
        """
        removed_source = False
        self.output_debug("Removing objects for plugin:", plugin_id)

        # sources
        for src in list(self.sources):
            if self.get_plugin_id_for_object(src) == plugin_id:
                self._remove(src)
                removed_source = True

        # all other objects
        def remove_matching_objects(collection, plugin_id):
            for obj in list(collection):
                if self.get_plugin_id_for_object(obj) == plugin_id:
                    collection.remove(obj)
                    pretty.print_debug(__name__, "Remove", repr(obj))

        remove_matching_objects(self.text_sources, plugin_id)

        for typ_v in self.content_decorators.values():
            remove_matching_objects(typ_v, plugin_id)

        for a_typ_v in self.action_decorators.values():
            remove_matching_objects(a_typ_v, plugin_id)

        remove_matching_objects(self.action_generators, plugin_id)

        return removed_source

    def get_sources(self) -> ty.Set[Source]:
        return self.sources

    def add_text_sources(self, plugin_id, srcs):
        self.text_sources.update(srcs)
        self._register_plugin_objects(plugin_id, *srcs)

    def get_text_sources(self) -> ty.Set[TextSource]:
        return self.text_sources

    def add_content_decorators(
        self, plugin_id: str, decos: ty.Dict[ty.Any, ty.Set[Source|Leaf]]
    ) -> None:
        for typ, val in decos.items():
            self.content_decorators.setdefault(typ, set()).update(val)
            self._register_plugin_objects(plugin_id, *val)

    def add_action_decorators(
        self, plugin_id: str, decos: dict[ty.Any, ty.Collection[Action]]
    ) -> None:
        for typ, val in decos.items():
            self.action_decorators.setdefault(typ, set()).update(val)
            self._register_plugin_objects(plugin_id, *val)

        for typ_v in self.action_decorators.values():
            self._disambiguate_actions(typ_v)

    def add_action_generator(
        self, plugin_id: str, agenerator: ActionGenerator
    ) -> None:
        self.action_generators.append(agenerator)
        self._register_plugin_objects(plugin_id, agenerator)

    def _disambiguate_actions(self, actions: ty.Iterable[Action]) -> None:
        """Rename actions by the same name (adding a suffix)"""
        # FIXME: Disambiguate by plugin name, not python module name
        names: ty.Dict[str, Action] = {}
        renames = set()
        for action in actions:
            name = str(action)
            if name in names:
                renames.add(names[name])
                renames.add(action)
            else:
                names[name] = action

        for action in renames:
            self.output_debug(f"Disambiguate Action {action}")
            plugin_suffix = f" ({type(action).__module__.split('.')[-1]})"
            if not action.name.endswith(plugin_suffix):
                action.name += plugin_suffix

    def __contains__(self, src: AnySource) -> bool:
        return src in self.sources

    def __getitem__(self, src: AnySource) -> AnySource:
        # TODO: ???
        if not src in self:
            raise KeyError

        self.output_debug(f"__getitem__ {src!r}")

        for s in self.sources:
            if s == src:
                return s

        raise KeyError

    @property
    def root(self) -> ty.Optional[Source]:
        """Get the root source of catalog"""
        if len(self.sources) == 1:
            (root_catalog,) = self.sources
        elif len(self.sources) > 1:
            firstlevel = self.firstlevel
            root_catalog = sources.MultiSource(firstlevel)
        else:
            root_catalog = None

        return root_catalog

    def _invalidate_root(self) -> None:
        "The source root needs to be recalculated"
        self._pre_root = None

    @property
    def firstlevel(self) -> ty.List[Source]:
        if self._pre_root:
            return self._pre_root

        sourceindex = set(self.sources)
        kupfer_sources = sources.SourcesSource(self.sources)
        sourceindex.add(kupfer_sources)
        # Make sure firstlevel is ordered
        # So that it keeps the ordering.. SourcesSource first
        firstlevel: ty.List[Source] = []
        firstlevel.append(sources.SourcesSource(sourceindex))
        firstlevel.extend(self.toplevel_sources)
        self._pre_root = firstlevel
        return firstlevel

    @classmethod
    def good_source_for_types(
        cls, source: Source, types: ty.Tuple[ty.Any, ...]
    ) -> bool:
        """return whether @s provides good leaves for @types"""
        if provides := list(source.provides()):
            return any(issubclass(t, types) for t in provides)

        return True

    def root_for_types(
        self,
        types: ty.Iterable[ty.Any],
        extra_sources: ty.Optional[ty.Iterable[Source]] = None,
    ) -> sources.MultiSource:
        """
        Get root for a flat catalog of all catalogs
        providing at least Leaves of @types

        types: Iterable of classes
        extra_sources: Sources to include

        Take all sources which:
            Provide a type T so that it is a subclass
            to one in the set of types we want
        """
        ttypes: ty.Tuple[ty.Any, ...] = tuple(types)
        firstlevel = set(extra_sources or [])
        # include the Catalog index since we want to include
        # the top of the catalogs (like $HOME)
        catalog_index = (sources.SourcesSource(self.sources),)
        firstlevel.update(
            s
            for s in itertools.chain(self.sources, catalog_index)
            if self.good_source_for_types(s, ttypes)
        )

        return sources.MultiSource(firstlevel)

    def get_canonical_source(self, source: AnySource) -> AnySource:
        "Return the canonical instance for @source"
        # check if we already have source, then return that
        if source in self:
            return self[source]

        source.initialize()
        return source

    def get_contents_for_leaf(
        self, leaf: Leaf, types: ty.Optional[ty.Tuple[ty.Any, ...]] = None
    ) -> ty.Iterator[Source]:
        """Iterator of content sources for @leaf,
        providing @types (or None for all)"""
        for typ, val in self.content_decorators.items():
            if not isinstance(leaf, typ):
                continue

            for content in list(val):
                with pluginload.exception_guard(
                    content, self._remove_source, content, is_decorator=True
                ):
                    dsrc = content.decorate_item(leaf)  # type: ignore

                if dsrc:
                    if types and not self.good_source_for_types(dsrc, types):
                        continue

                    yield self.get_canonical_source(dsrc)

    def get_actions_for_leaf(self, leaf: Leaf) -> ty.Iterator[Action]:
        for typ, val in self.action_decorators.items():
            if isinstance(leaf, typ):
                yield from val

        for agenerator in self.action_generators:
            yield from agenerator.get_actions_for_leaf(leaf)

    def decorate_object(
        self, obj: Leaf, action: ty.Optional[Action] = None
    ) -> None:
        if hasattr(obj, "has_content") and not obj.has_content():
            types = tuple(action.object_types()) if action else ()
            contents = list(self.get_contents_for_leaf(obj, types))
            content = contents[0] if contents else None
            if len(contents) > 1:
                content = sources.SourcesSource(
                    contents, name=str(obj), use_reprs=False
                )

            obj.add_content(content)

    def finalize(self) -> None:
        "Finalize all sources, equivalent to deactivating all sources"
        for src in self.sources:
            src.finalize()

        self.did_finalize_sources = True

    def save_cache(self) -> None:
        "Save all caches (non-important data)"
        if not self.did_finalize_sources:
            raise InternalError("Called save_cache without finalize!")

        if self.loaded_successfully:
            self._pickle_sources(self.sources)
        else:
            self.output_debug("Not writing cache on failed load")

    def save_data(self)->None:
        "Save (important) user data/configuration"
        if not self.loaded_successfully:
            self.output_info("Not writing configuration on failed load")
            return

        configsaver = SourceDataPickler()
        for source in self.sources:
            if configsaver.source_has_config(source):
                self._save_source(source, pickler=configsaver)

    @classmethod
    def _save_source(cls, source: Source, pickler: ty.Any =None) -> None:
        configsaver = pickler or SourceDataPickler()
        configsaver.save_source(source)

    def _finalize_source(self, source: Source) -> None:
        "Either save config, or save cache for @source"
        source.finalize()
        if SourceDataPickler.source_has_config(source):
            self._save_source(source)
        elif not source.is_dynamic():
            self._pickle_source(source)

    def _pickle_sources(self, sources: ty.Iterable[Source])->None:
        sourcepickler = SourcePickler()
        sourcepickler.rm_old_cachefiles()
        for source in sources:
            if source.is_dynamic() or SourceDataPickler.source_has_config(
                source
            ):
                continue

            self._pickle_source(source, pickler=sourcepickler)

    @classmethod
    def _pickle_source(cls, source: Source, pickler: ty.Any=None)->None:
        sourcepickler = pickler or SourcePickler()
        sourcepickler.pickle_source(source)

    def _try_restore(self, srcs: ty.Iterable[Source])->ty.Iterator[Source]:
        """
        Try to restore the source that is equivalent to the
        "dummy" instance @source, from cache, or from saved configuration.
        yield the instances that succeed.
        """
        sourcepickler = SourcePickler()
        configsaver = SourceDataPickler()
        for source in srcs:
            if configsaver.source_has_config(source):
                configsaver.load_source(source)
            else:
                source = sourcepickler.unpickle_source(source)

            if source:
                yield source

    def _remove_source(self, source: Source, is_decorator:bool=False)->None:
        "Oust @source from catalog if any exception is raised"
        if not is_decorator:
            self.sources.discard(source)
            self.toplevel_sources.discard(source)
            source_type = type(source)
        else:
            source_type = source  # type: ignore

        for cdv in self.content_decorators.values():
            cdv.discard(source_type)  # type: ignore

    def initialize(self) -> None:
        "Initialize all sources and cache toplevel sources"
        self._initialize_sources(self.sources)
        self.rescanner.set_catalog(self.sources)
        self._cache_sources(self.toplevel_sources)
        self.loaded_successfully = True

    def _initialize_sources(self, srcs: ty.Iterable[Source]) -> None:
        for src in srcs:
            with pluginload.exception_guard(src, self._remove_source, src):
                src.initialize()

    def _cache_sources(self, srcs: ty.Iterable[Source]) -> None:
        # Make sure that the toplevel sources are chached
        # either newly rescanned or the cache is fully loaded
        for src in srcs:
            with pluginload.exception_guard(src, self._remove_source, src):
                self.rescanner.rescan_now(src, force_update=False)


def GetSourceController() -> SourceController:
    return SourceController.instance()

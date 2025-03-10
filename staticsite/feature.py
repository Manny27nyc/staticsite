from __future__ import annotations
from typing import Dict, Callable, Set, List, Type
from collections import defaultdict
import logging
import sys
from .page import Page
from . import contents
from . import site
from . import toposort

log = logging.getLogger("feature")


class Feature:
    """
    Base class for implementing a staticsite feature.

    It contains dependencies on other features, and hooks called in various
    stages of site processing.
    """
    # Name with which the feature class was loaded
    NAME: str

    # List names of features that should run after us.
    # The dependency order is taken into account when calling try_load_page and
    # finalize.
    RUN_BEFORE: List[str] = []

    # List names of features that should run before us.
    # The dependency order is taken into account when calling try_load_page and
    # finalize.
    RUN_AFTER: List[str] = []

    def __init__(self, name: str, site: "site.Site"):
        # Feature name
        self.name = name
        # Site object
        self.site = site
        # Feature-provided jinja2 globals
        self.j2_globals: Dict[str, Callable] = {}
        # Feature-provided jinja2 filters
        self.j2_filters: Dict[str, Callable] = {}

    def __str__(self):
        return self.name

    def __repr__(self):
        return "{}({})".format(self.__class__.__name__, self.name)

    def get_short_description(self):
        """
        Get a short description from this feature's docstring
        """
        if not self.__doc__:
            return ""
        else:
            return self.__doc__.lstrip().splitlines()[0].strip()

    def load_dir_meta(self, sitedir: "contents.ContentDir"):
        """
        Hook to load extra directory metadata for the given sitedir.

        sitedir will be already populated with directory contents, but not yet
        loaded.
        """
        # Do nothing by default
        pass

    def load_dir(self, sitedir: "contents.ContentDir") -> List[Page]:
        """
        Load pages from the given Dir.

        Remove from dir the filenames that have been loaded.

        Return the list of loaded pages.
        """
        return []

    def try_load_archetype(self, archetypes, relpath, name):
        """
        Try loading an archetype page.

        Returns None if this path is not handled by this feature
        """
        return None

    def finalize(self):
        """
        Hook called after all the pages have been loaded
        """
        pass

    def add_site_commands(self, subparsers):
        """
        Add commands to `ssite site --cmd …` command line parser
        """
        pass


class Features:
    def __init__(self, site: site.Site):
        self.site = site

        # Registry of feature classes by name, built during loading
        self.feature_classes: Dict[str, Type[Feature]] = {}

        # Feature implementation registry
        self.features: Dict[str, Feature] = {}

        # Features sorted by topological order
        self.sorted = None

    def ordered(self):
        return self.sorted

    def __getitem__(self, key):
        return self.features[key]

    def get(self, key, default=None):
        return self.features.get(key, default)

    def _sort_features(self, features: Dict[str, Feature]):
        graph: Dict[str, Set[str]] = defaultdict(set)

        # Add well-known synchronization points
        graph["autogenerated_pages"]

        # Add feature dependencies
        for feature in features.values():
            # Make sure that each feature is in the graph
            graph[feature.NAME] = set()

            for name in feature.RUN_AFTER:
                if name not in features and name not in graph:
                    log.warn("feature %s: ignoring RUN_AFTER relation on %s which is not available",
                             feature, name)
                    continue
                graph[feature.NAME].add(name)

            for name in feature.RUN_BEFORE:
                if name not in features and name not in graph:
                    log.warn("feature %s: ignoring RUN_BEFORE relation on %s which is not available",
                             feature, name)
                    continue
                graph[name].add(feature.NAME)

        # Build the sorted list of features
        sorted_names = toposort.sort(graph)
        log.debug("Feature run order: %r", sorted_names)
        sorted_features = []
        for name in sorted_names:
            f = features.get(name)
            # Skip names that are not features, like well-known synchronization
            # points
            if f is None:
                continue
            sorted_features.append(f)
        return sorted_features

    def commit(self):
        """
        Finalize feature loading, instantiating and initializing all the
        features that have been collected.
        """
        self.sorted = []

        # Instantiate the feature classes in dependency order
        for cls in self._sort_features(self.feature_classes):
            if cls.NAME in self.features:
                continue
            feature = cls(cls.NAME, self.site)
            self.features[cls.NAME] = feature
            self.sorted.append(feature)

        log.debug("sorted feature list: %r", [x.name for x in self.sorted])

    def load_default_features(self):
        """
        Load features packaged with staticsite
        """
        from . import features
        self.load_feature_dir(features.__path__)

    def load_feature_dir(self, paths, namespace="staticsite.features"):
        """
        Load all features found in the given directory.

        Feature classes are instantiate in dependency order.
        """
        import pkgutil
        import importlib

        for module_finder, name, ispkg in pkgutil.iter_modules(paths):
            full_name = namespace + "." + name
            mod = sys.modules.get(full_name)
            if not mod:
                try:
                    spec = module_finder.find_spec(name)
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                except Exception:
                    log.exception("%r: failed to load feature module", name)
                    continue
                sys.modules[full_name] = mod

            features = getattr(mod, "FEATURES", None)
            if features is None:
                log.warn("%r: feature module did not define a FEATURES dict", name)
                continue

            # Register features with site
            for name, cls in features.items():
                cls.NAME = name
                old = self.feature_classes.get(name)
                if old is not None:
                    # Allows replacing features: see #28
                    log.info("%s: replacing feature %s with %s", name, old, cls)
                self.feature_classes[name] = cls

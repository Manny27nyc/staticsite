from __future__ import annotations
from typing import Optional, Dict
import os
import sys
import pytz
import datetime
from .settings import Settings
from .page import Page
import logging

log = logging.getLogger()


class Site:
    def __init__(self, settings: Optional[Settings] = None):
        from .feature import Features

        # Site settings
        if settings is None:
            settings = Settings()
        self.settings: Settings = settings

        # Site pages
        self.pages: Dict[str, Page] = {}

        # Site time zone
        self.timezone = pytz.timezone(settings.TIMEZONE)

        # Current datetime
        self.generation_time = pytz.utc.localize(datetime.datetime.utcnow()).astimezone(self.timezone)

        # Theme used to render pages
        self.theme = None

        # Feature implementation registry
        self.features = Features(self)

    def load_features(self):
        # Load default features
        from . import features
        self.load_feature_dir(features.__path__)

    def load_feature_dir(self, paths, namespace="staticsite.features"):
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
                self.features.add(name, cls)

    def find_theme_root(self) -> str:
        """
        Choose a theme root from the ones listed in the configuration
        """
        # Pick the first valid theme directory
        candidate_themes = self.settings.THEME
        if isinstance(candidate_themes, str):
            candidate_themes = (candidate_themes,)

        for theme_root in candidate_themes:
            theme_root = os.path.join(self.settings.PROJECT_ROOT, theme_root)
            if os.path.isdir(theme_root):
                return theme_root

        raise RuntimeError(
                "None of the configured theme directories ({}) seem to exist".format(
                    ", ".join(self.settings.THEME)))

    def load_theme(self):
        """
        Load a theme from the given directory.

        This needs to be called once (and only once) before analyze() is
        called.
        """
        if self.theme is not None:
            raise RuntimeError(
                    F"load_theme called while a theme was already loaded from {self.theme.root}")

        theme_root = self.find_theme_root()

        from .theme import Theme
        self.theme = Theme(self, theme_root)

        theme_static = os.path.join(theme_root, "static")
        if os.path.isdir(theme_static):
            self.read_asset_tree(theme_static)

        for name in self.settings.SYSTEM_ASSETS:
            root = os.path.join("/usr/share/javascript", name)
            if not os.path.isdir(root):
                log.warning("%s: system asset directory not found", root)
                continue
            self.read_asset_tree("/usr/share/javascript", name)

    def load_content(self, content_root=None):
        """
        Load site page and assets from the given directory.

        Can be called multiple times.

        :arg content_root: path to read contents from. If missing,
                           settings.CONTENT is used.
        """
        if content_root is None:
            content_root = os.path.join(self.settings.PROJECT_ROOT, self.settings.CONTENT)
        self.read_contents_tree(content_root)

    def load(self):
        """
        Load all site components
        """
        self.load_features()
        self.load_theme()
        self.load_content()

    def add_page(self, page: Page):
        """
        Add a Page object to the site.

        Use this only when the normal Site content loading functions are not
        enough. This is exported as a public function mainly for the benefit of
        unit tests.
        """
        ts = page.meta.get("date", None)
        if not self.settings.DRAFT_MODE and ts is not None and ts > self.generation_time:
            log.info("Ignoring page %s with date %s in the future", page.src_relpath, ts - self.generation_time)
            return
        self.pages[page.src_linkpath] = page

        # Run feature metadata hooks for the given page, if any
        trigger_features = set()
        for name, features in self.features.metadata_hooks.items():
            if name in page.meta:
                for feature in features:
                    trigger_features.add(feature)
        for feature in trigger_features:
            feature.add_page(page)

    def add_test_page(self, feature: str, **kw) -> Page:
        """
        Add a page instantiated using the given feature for the purpose of unit
        testing.

        :return: the Page added
        """
        page = self.features[feature].build_test_page(**kw)
        self.add_page(page)
        return page

    def read_contents_tree(self, tree_root):
        """
        Read static assets and pages from a directory and all its subdirectories
        """
        from .asset import Asset

        log.info("Loading pages from %s", tree_root)

        for root, dnames, fnames in os.walk(tree_root, followlinks=True):
            for i, d in enumerate(dnames):
                if d.startswith("."):
                    del dnames[i]

            for f in fnames:
                if f.startswith("."):
                    continue

                page_abspath = os.path.join(root, f)
                page_relpath = os.path.relpath(page_abspath, tree_root)

                for handler in self.features.ordered():
                    p = handler.try_load_page(tree_root, page_relpath)
                    if p is not None:
                        self.add_page(p)
                        break
                else:
                    if os.path.isfile(page_abspath):
                        log.debug("Loading static file %s", page_relpath)
                        p = Asset(self, tree_root, page_relpath)
                        self.add_page(p)

    def read_asset_tree(self, tree_root, subdir=None):
        """
        Read static assets from a directory and all its subdirectories
        """
        from .asset import Asset

        if subdir is None:
            search_root = tree_root
        else:
            search_root = os.path.join(tree_root, subdir)

        log.info("Loading assets from %s", search_root)

        for root, dnames, fnames in os.walk(search_root, followlinks=True):
            for f in fnames:
                if f.startswith("."):
                    continue

                page_abspath = os.path.join(root, f)
                if not os.path.isfile(page_abspath):
                    continue

                page_relpath = os.path.relpath(page_abspath, tree_root)
                log.debug("Loading static file %s", page_relpath)
                p = Asset(self, tree_root, page_relpath)
                self.add_page(p)

    def analyze(self):
        """
        Iterate through all Pages in the site to build aggregated content like
        taxonomies and directory indices.

        Call this after all Pages have been added to the site.
        """
        # Call finalize hook on features
        for feature in self.features.ordered():
            feature.finalize()

    def slugify(self, text):
        """
        Return the slug version of an arbitrary string, that can be used as an
        url component or file name.
        """
        from slugify import slugify
        return slugify(text)

    def get_archetypes(self):
        from .archetypes import Archetypes
        return Archetypes(self, os.path.join(self.settings.PROJECT_ROOT, "archetypes"))

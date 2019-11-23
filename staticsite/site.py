from __future__ import annotations
from typing import Optional, Dict
import os
import sys
import pytz
import datetime
import stat
from .settings import Settings
from .page import Page
from .render import File
from .cache import Caches, DisabledCaches
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
        if settings.TIMEZONE is None:
            from dateutil.tz import tzlocal
            self.timezone = tzlocal()
        else:
            self.timezone = pytz.timezone(settings.TIMEZONE)

        # Current datetime
        self.generation_time = pytz.utc.localize(datetime.datetime.utcnow()).astimezone(self.timezone)

        # Theme used to render pages
        self.theme = None

        # Feature implementation registry
        self.features = Features(self)

        # Build cache repository
        if self.settings.CACHE_REBUILDS:
            self.caches = Caches(os.path.join(self.settings.PROJECT_ROOT, ".cache"))
        else:
            self.caches = DisabledCaches()

        # Pick an initial site name from settings
        self.site_name = self.settings.SITE_NAME

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
        self.features.load_default_features()
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
            log.info("Ignoring page %s with date %s in the future", page.src.relpath, ts - self.generation_time)
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

        if not os.path.exists(tree_root):
            log.info("%s: content tree does not exist", tree_root)
            return
        else:
            log.info("Loading pages from %s", tree_root)

        for f in File.scan(tree_root, follow_symlinks=True, ignore_hidden=True):
            for handler in self.features.ordered():
                p = handler.try_load_page(f)
                if p is not None:
                    self.add_page(p)
                    break
            else:
                if stat.S_ISREG(f.stat.st_mode):
                    log.debug("Loading static file %s", f.relpath)
                    p = Asset(self, f)
                    self.add_page(p)

    def read_asset_tree(self, tree_root, subdir=None):
        """
        Read static assets from a directory and all its subdirectories
        """
        from .asset import Asset

        if subdir:
            log.info("Loading assets from %s / %s", tree_root, subdir)
        else:
            log.info("Loading assets from %s", tree_root)

        for f in File.scan(tree_root, relpath=subdir, follow_symlinks=True, ignore_hidden=True):
            if not stat.S_ISREG(f.stat.st_mode):
                continue
            log.debug("Loading static file %s", f.relpath)
            p = Asset(self, f)
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

        # Set a default SITE_NAME if none was provided
        if self.site_name is None:
            toplevel_index = self.pages.get("")
            if toplevel_index is not None:
                self.site_name = toplevel_index.meta.get("title")

        # Fallback site name for sites without a toplevel index
        if self.site_name is None:
            self.site_name = "/"

    def slugify(self, text):
        """
        Return the slug version of an arbitrary string, that can be used as an
        url component or file name.
        """
        from slugify import slugify
        return slugify(text)

    def localized_timestamp(self, ts):
        return pytz.utc.localize(
                        datetime.datetime.utcfromtimestamp(ts)
                    ).astimezone(self.timezone)

    def get_archetypes(self):
        from .archetypes import Archetypes
        return Archetypes(self, os.path.join(self.settings.PROJECT_ROOT, "archetypes"))

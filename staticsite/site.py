from __future__ import annotations
from typing import Optional, Dict
import os
import pytz
import datetime
from collections import defaultdict
from .settings import Settings
from .page import Page
from .cache import Caches, DisabledCaches
from .utils import lazy, open_dir_fd
import logging

log = logging.getLogger("site")


class Site:
    """
    A staticsite site.

    This class tracks all resources associated with a site.
    """

    def __init__(self, settings: Optional[Settings] = None):
        from .feature import Features

        # Site settings
        if settings is None:
            settings = Settings()
        self.settings: Settings = settings

        # Fill in settings left empty that have meaningful defaults
        if self.settings.PROJECT_ROOT is None:
            self.settings.PROJECT_ROOT = os.getcwd()
        if self.settings.CONTENT is None:
            self.settings.CONTENT = "."

        # Site pages
        self.pages: Dict[str, Page] = {}

        # Metadata for which we add pages to pages_by_metadata
        self.tracked_metadata = set(settings.TAXONOMIES)

        # Site pages that have the given metadata
        self.pages_by_metadata = defaultdict(list)

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
            if os.access(self.settings.PROJECT_ROOT, os.W_OK):
                self.caches = Caches(os.path.join(self.settings.PROJECT_ROOT, ".staticsite-cache"))
            else:
                log.warn("%s: directory not writable: disabling caching", self.settings.PROJECT_ROOT)
                self.caches = DisabledCaches()
        else:
            self.caches = DisabledCaches()

        # Content root for the website
        self.content_root = os.path.join(self.settings.PROJECT_ROOT, self.settings.CONTENT)

        # Pick an initial site name from settings
        self.site_name = self.settings.SITE_NAME

    @lazy
    def archetypes(self) -> "archetypes.Archetypes":
        """
        Archetypes defined in the site
        """
        from .archetypes import Archetypes
        return Archetypes(self, os.path.join(self.settings.PROJECT_ROOT, "archetypes"))

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
        self.theme.load_assets()

    def load_content(self, content_root=None):
        """
        Load site page and assets from the given directory.

        Can be called multiple times.

        :arg content_root: path to read contents from. If missing,
                           settings.CONTENT is used.
        """
        from .contents import ContentDir
        if content_root is None:
            content_root = self.content_root

        if not os.path.exists(content_root):
            log.info("%s: content tree does not exist", content_root)
            return
        else:
            log.info("Loading pages from %s", content_root)

        with open_dir_fd(content_root) as dir_fd:
            root = ContentDir(self, content_root, "", dir_fd)
            root.load()

    def load(self, content_root=None):
        """
        Load all site components
        """
        self.features.load_default_features()
        self.load_theme()
        self.load_content(content_root=content_root)

    def add_page(self, page: Page):
        """
        Add a Page object to the site.

        Use this only when the normal Site content loading functions are not
        enough. This is exported as a public function mainly for the benefit of
        unit tests.
        """
        self.pages[page.src_linkpath] = page

        # Also group pages by tracked metadata
        for tracked in page.meta.keys() & self.tracked_metadata:
            self.pages_by_metadata[tracked].append(page)

    def add_test_page(self, feature: str, *args, **kw) -> Page:
        """
        Add a page instantiated using the given feature for the purpose of unit
        testing.

        :return: the Page added
        """
        page = self.features[feature].build_test_page(*args, **kw)
        if not page.is_valid():
            raise RuntimeError("Tried to add an invalid test page")
        self.add_page(page)
        return page

    def read_asset_tree(self, tree_root, subdir=None):
        """
        Read static assets from a directory and all its subdirectories
        """
        from .contents import AssetDir

        if subdir:
            log.info("Loading assets from %s / %s", tree_root, subdir)
            with open_dir_fd(os.path.join(tree_root, subdir)) as dir_fd:
                root = AssetDir(self, tree_root, subdir, dir_fd, dest_subdir="static")
                root.load()
        else:
            log.info("Loading assets from %s", tree_root)
            with open_dir_fd(tree_root) as dir_fd:
                root = AssetDir(self, tree_root, "", dir_fd, dest_subdir="static")
                root.load()

    def analyze(self):
        """
        Iterate through all Pages in the site to build aggregated content like
        taxonomies and directory indices.

        Call this after all Pages have been added to the site.
        """
        # Add missing pages_by_metadata entries in case no matching page were
        # found for some of them
        for key in self.tracked_metadata:
            if key not in self.pages_by_metadata:
                self.pages_by_metadata[key] = []

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

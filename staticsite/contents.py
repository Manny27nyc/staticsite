from __future__ import annotations
from typing import Dict, List, Any, Tuple
from .utils import front_matter, open_dir_fd
from .utils.typing import Meta
from .page_filter import compile_page_match
from . import site
from . import file
import stat
import os
import re
import logging

log = logging.getLogger("contents")


class Dir:
    """
    Base class for content loaders
    """
    def __init__(
            self, site: "site.Site", src: file.File, meta: Dict[str, Any]):
        self.site = site
        # File object for the source directory in the filesystem
        self.src = src
        # Subdirectory of this directory
        self.subdirs: List["ContentDir"] = []
        # Files found in this directory
        self.files: Dict[str, file.File] = {}
        self.meta: Dict[str, Any] = meta
        # Rules for assigning metadata to subdirectories
        self.dir_rules: List[Tuple[re.Pattern, Meta]] = []
        # Rules for assigning metadata to files
        self.file_rules: List[Tuple[re.Pattern, Meta]] = []
        # Computed metadata for files and subdirectories
        self.file_meta: Dict[str, Meta] = {}

    @classmethod
    def create(cls, site: "site.Site", src: file.File, meta: Dict[str, Any]):
        # Check whether to load subdirectories as asset trees
        if meta.get("asset"):
            return AssetDir(site, src, meta)
        else:
            return ContentDir(site, src, meta)

    def add_dir_config(self, meta: Meta):
        """
        Acquire directory configuration from a page metadata
        """
        # Compile directory matching rules
        dir_meta = meta.pop("dirs", None)
        if dir_meta is None:
            dir_meta = {}
        self.dir_rules.extend((compile_page_match(k), v) for k, v in dir_meta.items())

        # Compute file matching rules
        file_meta = meta.pop("files", None)
        if file_meta is None:
            file_meta = {}
        self.file_rules.extend((compile_page_match(k), v) for k, v in file_meta.items())

        # Merge in metadata
        for name in self.site.metadata.keys() & meta.keys():
            metadata = self.site.metadata[name]
            if metadata.inherited:
                self.meta[name] = meta[name]

        # Default site name to the root page title, if site name has not been
        # set yet
        # TODO: template_title is not supported (yet)
        title = meta.get("title")
        if title is not None:
            self.meta.setdefault("site_name", title)

    def meta_file(self, fname: str):
        # TODO: deprecate, and just use self.file_meta[fname]
        return self.file_meta[fname]

    def scan(self, dir_fd: int):
        # Scan directory contents
        subdirs = {}
        with os.scandir(dir_fd) as entries:
            for entry in entries:
                # Note: is_dir, is_file, and stat, follow symlinks by default
                if entry.is_dir():
                    if entry.name.startswith("."):
                        # Skip hidden directories
                        continue
                    # Take note of directories
                    subdirs[entry.name] = file.File(
                            relpath=os.path.join(self.src.relpath, entry.name),
                            abspath=os.path.join(self.src.abspath, entry.name),
                            stat=entry.stat())
                elif entry.name.startswith(".") and entry.name != ".staticsite":
                    # Skip hidden files
                    continue
                else:
                    # Take note of files
                    self.files[entry.name] = file.File(
                            relpath=os.path.join(self.src.relpath, entry.name),
                            abspath=os.path.join(self.src.abspath, entry.name),
                            stat=entry.stat())

        # Load dir metadata from .staticsite, if present
        # TODO: move this to a feature implementing just load_dir_meta?
        dircfg = self.files.pop(".staticsite", None)
        if dircfg is not None:
            config: Dict[str, Any] = {}

            # Load .staticsite if found
            def _file_opener(path, flags):
                return os.open(path, flags, dir_fd=dir_fd)
            with open(dircfg.abspath, "rt", opener=_file_opener) as fd:
                lines = [line.rstrip() for line in fd]
                fmt, config = front_matter.parse(lines)

            self.add_dir_config(config)

        # Lead features add to directory metadata
        for feature in self.site.features.ordered():
            feature.load_dir_meta(self)

        # If site_name is not defined, use the content directory name
        if "site_name" not in self.meta:
            self.meta["site_name"] = os.path.basename(self.src.abspath)

        # Store directory metadata
        self.site.dir_meta[self.meta["site_path"]] = self.meta

        # Compute metadata for directories
        for dname in subdirs:
            res: Meta = dict(self.meta)
            res["site_path"] = os.path.join(res["site_path"], dname)
            for pattern, meta in self.dir_rules:
                if pattern.match(dname):
                    res.update(meta)
            self.file_meta[dname] = res

        # Compute metadata for files
        for fname in self.files.keys():
            res: Meta = dict(self.meta)
            for pattern, meta in self.file_rules:
                if pattern.match(fname):
                    res.update(meta)
            self.file_meta[fname] = res

        # Scan subdirectories
        for name, f in subdirs.items():
            subdir = Dir.create(
                        self.site, f,
                        meta=self.file_meta[name])
            with open_dir_fd(name, dir_fd=dir_fd) as subdir_fd:
                subdir.scan(subdir_fd)
            self.subdirs.append(subdir)


class ContentDir(Dir):
    """
    Content path which uses features for content loading
    """
    def load(self, dir_fd: int):
        """
        Read static assets and pages from this directory and all its subdirectories

        Load files through features by default
        """
        from .asset import Asset

        site_path = self.meta["site_path"]

        log.debug("Loading pages from %s as %s", self.src.abspath, site_path)

        # Handle files marked as assets in their metadata
        taken = []
        for fname, f in self.files.items():
            meta = self.file_meta[fname]
            if meta and meta.get("asset"):
                meta["site_path"] = os.path.join(meta["site_path"], fname)
                p = Asset(self, f, meta=meta)
                if not p.is_valid():
                    continue
                self.site.add_page(p)
                taken.append(fname)
        for fname in taken:
            del self.files[fname]

        # Let features pick their files
        for handler in self.site.features.ordered():
            for page in handler.load_dir(self):
                self.site.add_page(page)
            if not self.files:
                break

        # Use everything else as an asset
        # TODO: move into an asset feature?
        site_path = self.meta["site_path"]
        for fname, f in self.files.items():
            if stat.S_ISREG(f.stat.st_mode):
                log.debug("Loading static file %s", f.relpath)
                meta = self.file_meta[fname]
                meta["site_path"] = os.path.join(meta["site_path"], fname)
                p = Asset(self, f,
                          meta=self.file_meta[fname])
                if not p.is_valid():
                    continue
                self.site.add_page(p)

        # TODO: warn of contents not loaded at this point?

        # Load subdirectories
        for subdir in self.subdirs:
            with open_dir_fd(os.path.basename(subdir.src.relpath), dir_fd=dir_fd) as subdir_fd:
                subdir.load(subdir_fd)


class AssetDir(ContentDir):
    """
    Content path which loads everything as assets
    """
    def load(self, dir_fd: int):
        """
        Read static assets from this directory and all its subdirectories

        Loader load assets directly without consulting features
        """
        from .asset import Asset

        site_path = self.meta["site_path"]

        log.debug("Loading pages from %s as %s", self.src.abspath, site_path)

        # Load every file as an asset
        for fname, f in self.files.items():
            if stat.S_ISREG(f.stat.st_mode):
                log.debug("Loading static file %s", f.relpath)
                meta = self.file_meta[fname]
                meta["site_path"] = os.path.join(meta["site_path"], fname)
                p = Asset(self, f,
                          meta=meta)
                if not p.is_valid():
                    continue
                self.site.add_page(p)

        # Load subdirectories
        for subdir in self.subdirs:
            # TODO: prevent loops with a set of seen directory devs/inodes
            with open_dir_fd(os.path.basename(subdir.src.relpath), dir_fd=dir_fd) as subdir_fd:
                subdir.load(subdir_fd)

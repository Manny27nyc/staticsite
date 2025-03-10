from __future__ import annotations
from typing import NamedTuple, Optional
import os
import logging

log = logging.getLogger("contents")


class File(NamedTuple):
    """
    Information about a file in the file system.

    If stat is not None, the file exists and these are its stats.
    """
    # Relative path to root
    relpath: str
    # Absolute path to the file
    abspath: Optional[str] = None
    # File stats if the file exists, else None
    stat: Optional[os.stat_result] = None

    def __str__(self):
        if self.abspath:
            return self.abspath
        else:
            return self.relpath

    @classmethod
    def from_abspath(cls, tree_root: str, abspath: str) -> "File":
        return cls(
                relpath=os.path.relpath(abspath, tree_root),
                abspath=abspath)

    @classmethod
    def from_dir_entry(cls, dir: "File", entry: os.DirEntry) -> "File":
        try:
            st = entry.stat()
        except FileNotFoundError:
            log.warning("%s: cannot stat() file: broken symlink?",
                      os.path.join(dir.abspath, entry.name))
            st = None

        return cls(
                relpath=os.path.join(dir.relpath, entry.name),
                abspath=os.path.join(dir.abspath, entry.name),
                stat=st)

    @classmethod
    def with_stat(cls, relpath: str, abspath: str):
        return cls(relpath, abspath, os.stat(abspath))

    @classmethod
    def scan(cls, abspath, follow_symlinks=False, ignore_hidden=False):
        """
        Scan tree_root, generating relative paths based on it
        """
        for root, dnames, fnames, dirfd in os.fwalk(abspath, follow_symlinks=follow_symlinks):
            if ignore_hidden:
                # Ignore hidden directories
                filtered = [d for d in dnames if not d.startswith(".")]
                if len(filtered) != len(dnames):
                    dnames[::] = filtered

            for f in fnames:
                # Ignore hidden files
                if ignore_hidden and f.startswith("."):
                    continue

                try:
                    st = os.stat(f, dir_fd=dirfd)
                except FileNotFoundError:
                    # Skip broken links
                    continue
                relpath = os.path.join(root, f)
                yield cls(
                        relpath=relpath,
                        abspath=os.path.join(abspath, relpath),
                        stat=st)

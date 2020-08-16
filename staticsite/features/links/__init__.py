from __future__ import annotations
from typing import TYPE_CHECKING, List
import jinja2
import os
import logging
from collections import defaultdict
from staticsite.metadata import Metadata
from staticsite.feature import Feature
from staticsite.features.data import DataPage
from staticsite.features.links.data import Link, LinkCollection
from staticsite.features.links.index import LinkIndexPage
from staticsite.page_filter import PageFilter

if TYPE_CHECKING:
    from staticsite import Page
    from staticsite.contents import ContentDir

log = logging.getLogger("links")


class LinksPage(DataPage):
    """
    Page with a link collection posted as metadata only.
    """
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.links = LinkCollection([Link(link, page=self) for link in self.meta["links"]])


class Links(Feature):
    """
    Collect links and link metadata from page metadata.

    Optionally generate link collections.
    """
    RUN_AFTER = ["data"]
    RUN_BEFORE = ["dirs"]

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.j2_globals["links_merged"] = self.links_merged
        self.j2_globals["links_tag_index_url"] = self.links_tag_index_url

        # Shortcut to access the Data feature
        self.data = self.site.features["data"]

        # Let the Data feature render link collections from pure yaml files,
        # when they have a `data_type: links` metadata
        self.data.register_page_class("links", LinksPage)

        # Collect 'links' metadata
        self.site.tracked_metadata.add("links")
        self.site.register_metadata(Metadata("links", doc="""
Extra metadata for external links.

It is a list of dicts of metadata, one for each link. In each dict, these keys are recognised:

* `title`: str: short title for the link
* `url`: str: external URL
* `abstract`: str: long description or abstract for the link
* `archive`: str: URL to an archived version of the site
* `tags`: List[str]: tags for this link
* `related`: List[Dict[str, str]]: other related links, as a list of dicts with
  `title` and `url` keys
"""))

        # Pages for .links files found in the site
        self.indices: List[LinkIndexPage] = []

    def load_dir(self, sitedir: ContentDir) -> List[Page]:
        """
        Handle .links pages that generate the browseable archive of annotated
        external links
        """
        taken: List[str] = []
        pages: List[Page] = []
        for fname, src in sitedir.files.items():
            if not fname.endswith(".links"):
                continue
            taken.append(fname)

            name = fname[:-6]

            meta = sitedir.meta_file(fname)
            meta["site_path"] = os.path.join(sitedir.meta["site_path"], name)

            try:
                fm_meta = self.load_file_meta(sitedir, src, fname)
            except Exception:
                log.exception("%s: cannot parse taxonomy information", src.relpath)
                continue
            meta.update(fm_meta)

            page = LinkIndexPage(self.site, src, meta=meta, name=name, dir=sitedir, links=self)
            pages.append(page)

            self.indices.append(page)

        for fname in taken:
            del sitedir.files[fname]

        return pages

    def load_file_meta(self, sitedir, src, fname):
        """
        Parse the links file to read its description
        """
        from staticsite.utils import front_matter
        with sitedir.open(fname, src, "rt") as fd:
            fmt, meta = front_matter.read_whole(fd)
        if meta is None:
            meta = {}
        return meta

    @jinja2.contextfunction
    def links_merged(self, context, path=None, limit=None, sort=None, link_tags=None, **kw):
        page_filter = PageFilter(self.site, path=path, limit=limit, sort=sort, **kw)

        # Build link tag filter
        if link_tags is not None:
            if isinstance(link_tags, str):
                link_tags = {link_tags}
            else:
                link_tags = set(link_tags)

        links = LinkCollection()
        if link_tags is None:
            for page in page_filter.filter(self.data.by_type.get("links", ())):
                links.merge(page.links)
        else:
            for page in page_filter.filter(self.data.by_type.get("links", ())):
                for link in page.links:
                    if link_tags is not None and not link_tags.issubset(link.tags):
                        continue
                    links.append(link)

        return links

    @jinja2.contextfunction
    def links_tag_index_url(self, context, tag):
        return os.path.join(self.site.settings.SITE_ROOT, "links", tag + "-links")

    def finalize(self):
        # Index links by tag
        self.by_tag = defaultdict(LinkCollection)
        for page in self.site.pages_by_metadata["links"]:
            for link in page.links:
                for tag in link.tags:
                    self.by_tag[tag].append(link)

        # Call finalize on all .links pages, to populate them
        for index in self.indices:
            index.finalize()

    def add_site_commands(self, subparsers):
        super().add_site_commands(subparsers)
        from staticsite.features.links.cmdline import LinkLint
        LinkLint.make_subparser(subparsers)


FEATURES = {
    "links": Links,
}

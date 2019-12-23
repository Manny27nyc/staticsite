from __future__ import annotations
from typing import Dict, Any
from staticsite.feature import Feature
from staticsite.theme import PageFilter
from staticsite import Page, Site
from staticsite.metadata import Metadata
import os
import logging

log = logging.getLogger("syndication")


class SyndicationFeature(Feature):
    """
    Build syndication feeds for groups of pages.

    One page is used to define the syndication, using "syndication_*" tags.

    Use a data page without type to define a contentless syndication page
    """
    # syndication requires page.meta.pages prefilled by pages and taxonomy features
    RUN_AFTER = ["pages", "taxonomy"]
    RUN_BEFORE = ["autogenerated_pages"]

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.site.tracked_metadata.add("syndication")
        self.site.features["rst"].yaml_tags.add("syndication")
        self.site.register_metadata(Metadata("syndication", inherited=False, structure=True, doc=f"""
Defines syndication for the contents of this page.

It is a structure which can contain various fields:

* `add_to`: chooses which pages will include a link to the RSS/Atom feeds
* `filter`: chooses which pages are shown in the RSS/Atom feeds

Any other metadata found in the structure are used when generating pages for
the RSS/Atom feeds, so you can use `title`, `template_title`, `description`,
and so on, to personalize the feeds.

`filter` and `add_to` are dictionaries that select pages in the site, similar
to the `site_pages` function in [templates](templates.md). See
[Selecting pages](page-filter.md) for details.

`filter` is optional, and if missing, `page.meta.pages` is used. This way,
[using the `pages` metadata](pages.md), you can define a single expression for
both syndication and page listing.
"""))
        self.syndications = []

    def finalize(self):
        # Build syndications from pages with a 'syndication' metadata
        for page in self.site.pages_by_metadata["syndication"]:
            syndication_meta = page.meta.get("syndication")
            if syndication_meta is None:
                continue

            # Make a shallow copy to prevent undesired side effects if multiple
            # pages share the same syndication dict, as may be the case with
            # taxonomies
            syndication_meta = dict(syndication_meta)
            syndication_meta["site_path"] = page.meta["site_path"]
            page.meta["syndication"] = syndication_meta

            # Index page for the syndication
            syndication_meta["index"] = page

            # Pages in the syndication
            select = syndication_meta.get("filter")
            if select:
                f = PageFilter(self.site, **select)
                pages = f.filter(self.site.pages.values())
            else:
                pages = page.meta.get("pages", [])
            syndication_meta["pages"] = pages

            # RSS feed
            rss_page = RSSPage(page.parent, syndication_meta)
            if rss_page.is_valid():
                syndication_meta["rss_page"] = rss_page
                self.site.add_page(rss_page)
                log.debug("%s: adding syndication page for %s", rss_page, page)

            # Atom feed
            atom_page = AtomPage(page.parent, syndication_meta)
            if atom_page.is_valid():
                syndication_meta["atom_page"] = atom_page
                self.site.add_page(atom_page)
                log.debug("%s: adding syndication page for %s", rss_page, page)

            # Add a link to the syndication to the pages listed in add_to
            add_to = syndication_meta.get("add_to")
            if add_to:
                f = PageFilter(self.site, **add_to)
                for dest in f.filter(self.site.pages.values()):
                    old = dest.meta.get("syndication")
                    if old is not None:
                        log.warn("%s: attempted to add meta.syndication from %s, but it already has it from %s",
                                 dest, page, old["index"])
                    dest.meta["syndication"] = syndication_meta


class SyndicationPage(Page):
    """
    Base class for syndication pages
    """
    # Default template to use for this type of page
    TEMPLATE: str

    def __init__(self, parent: Page, meta: Dict[str, Any]):
        index = meta["index"]
        meta = dict(meta)
        meta["site_path"] = os.path.join(meta["site_path"], f"index.{self.TYPE}")

        super().__init__(
            parent=parent,
            src=None,
            dst_relpath=meta["site_path"],
            meta=meta)
        self.meta.setdefault("template", self.TEMPLATE)
        if self.meta["pages"]:
            self.meta["date"] = max(p.meta["date"] for p in self.meta["pages"])
        else:
            self.meta["date"] = self.site.generation_time

        # Copy well known keys from index page
        for key in "site_root", "site_url", "author", "site_name":
            self.meta.setdefault(key, index.meta.get(key))


class RSSPage(SyndicationPage):
    """
    A RSS syndication page
    """
    TYPE = "rss"
    TEMPLATE = "syndication.rss"


class AtomPage(SyndicationPage):
    """
    An Atom syndication page
    """
    TYPE = "atom"
    TEMPLATE = "syndication.atom"


FEATURES = {
    "syndication": SyndicationFeature,
}

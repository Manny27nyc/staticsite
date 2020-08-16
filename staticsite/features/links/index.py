from __future__ import annotations
from typing import TYPE_CHECKING
import os
import logging
from staticsite import Page
from .data import Link, LinkCollection

if TYPE_CHECKING:
    from . import Links

log = logging.getLogger("links")


class LinkIndexPage(Page):
    """
    Root page for the browseable archive of annotated external links in the
    site
    """
    TYPE = "links_index"

    def __init__(self, *args, name: str, links: Links, **kw):
        super().__init__(*args, **kw)
        # Reference to the Feature with the aggregated link collection
        self.feature_links = links

        self.meta["build_path"] = os.path.join(self.meta["site_path"], "index.html")
        self.meta.setdefault("template", "data-links.html")
        self.meta.setdefault("nav_title", name.capitalize())
        self.meta.setdefault("title", "All links shared in the site")

#    def to_dict(self):
#        from staticsite.utils import dump_meta
#        res = super().to_dict()
#        res["name"] = self.name
#        res["categories"] = dump_meta(self.categories)
#        res["category_meta"] = dump_meta(self.category_meta)
#        return res

    def finalize(self):
        pages = []
        all_links = LinkCollection()
        for tag, links in self.feature_links.by_tag.items():
            all_links.merge(links)
            meta = dict(self.meta)
            meta["site_path"] = os.path.join(meta["site_path"], tag + "-links")
            if meta["site_path"] in self.site.pages:
                continue
            meta["data_type"] = "links"
            meta["title"] = f"{tag} links"
            meta["links"] = links
            page = LinksTagPage.create_from(self, meta, links=links)
            self.site.add_page(page)
            pages.append(page)

        # Set self.meta.pages to the sorted list of categories
        pages.sort(key=lambda x: x.meta["title"])
        self.meta["pages"] = pages
        self.links = all_links


class LinksTagPage(Page):
    """
    Page with an autogenerated link collection from a link tag.
    """
    TYPE = "links_tag"

    def __init__(self, *args, **kw):
        links = kw.pop("links", None)
        super().__init__(*args, **kw)
        self.meta["build_path"] = os.path.join(self.meta["site_path"], "index.html")
        self.meta["syndicated"] = False
        if links is None:
            self.links = LinkCollection([Link(link) for link in self.meta["links"]])
        else:
            self.links = links

    @property
    def src_abspath(self):
        return None

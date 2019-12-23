from __future__ import annotations
from typing import List, Dict, Iterable, Optional
from staticsite.page import Page
from staticsite.feature import Feature
from staticsite.file import File
from staticsite.contents import ContentDir
from staticsite.metadata import Metadata
from staticsite.utils.typing import Meta
from collections import defaultdict
import functools
import os
import logging

log = logging.getLogger("taxonomy")


class TaxonomyFeature(Feature):
    """
    Tag pages using one or more taxonomies.

    See doc/taxonomies.md for details.
    """
    RUN_BEFORE = ["autogenerated_pages"]

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.known_taxonomies = set()

        # All TaxonomyPages found
        self.taxonomies: Dict[str, TaxonomyPage] = {}

        self.j2_globals["taxonomies"] = self.jinja2_taxonomies
        self.j2_globals["taxonomy"] = self.jinja2_taxonomy

    def register_taxonomy_name(self, name):
        self.known_taxonomies.add(name)
        self.site.tracked_metadata.add(name)
        # Note that if we want to make the tags inheritable, we need to
        # interface with 'rst' (or 'rst' to interface with us) because rst
        # needs to know which metadata items are taxonomies in order to parse
        # them.
        # Instead of making tags inheritable from normal metadata, we can offer
        # them to be added by 'files' or 'dirs' directives.
        self.site.register_metadata(Metadata(name, inherited=False, structure=True, doc=f"""
List of categories for the `{name}` taxonomy.

Setting this as a simple string is the same as setting it as a list of one
element.
"""))

    def load_dir_meta(self, sitedir: ContentDir):
        for fname in sitedir.files.keys():
            if not fname.endswith(".taxonomy"):
                continue
            self.register_taxonomy_name(fname[:-9])

    def load_dir(self, sitedir: ContentDir) -> List[Page]:
        taken: List[str] = []
        pages: List[Page] = []
        for fname, src in sitedir.files.items():
            if not fname.endswith(".taxonomy"):
                continue

            name = fname[:-9]

            meta = sitedir.meta_file(fname)
            meta["site_path"] = os.path.join(meta["site_path"], name)

            page = TaxonomyPage(self.site, src, name, meta=meta)
            if not page.is_valid():
                continue
            self.taxonomies[page.name] = page
            taken.append(fname)
            pages.append(page)

        for fname in taken:
            del sitedir.files[fname]

        return pages

    def build_test_page(self, relpath: str, meta: Optional[Meta] = None) -> Page:
        page = TestTaxonomyPage(
                self.site,
                File(relpath=relpath + ".taxonomy", abspath="/" + relpath + ".taxonomy"),
                os.path.basename(relpath), meta=meta)
        self.taxonomies[page.name] = page
        return page

    def jinja2_taxonomies(self) -> Iterable["TaxonomyPage"]:
        return self.taxonomies.values()

    def jinja2_taxonomy(self, name) -> Optional["TaxonomyPage"]:
        return self.taxonomies.get(name)

    def finalize(self):
        # Call finalize on all taxonomy pages, to populate them by scanning
        # site pages
        for taxonomy in self.taxonomies.values():
            taxonomy.finalize()


class TaxonomyPage(Page):
    """
    Root page for one taxonomy defined in the site
    """
    TYPE = "taxonomy"

    def __init__(self, site, src, name, meta: Meta):
        super().__init__(
            site=site,
            src=src,
            site_path=meta["site_path"],
            dst_relpath=os.path.join(meta["site_path"], "index.html"),
            meta=meta)

        self.meta.setdefault("template", "taxonomy/taxonomy.html")

        # Taxonomy name (e.g. "tags")
        self.name = name

        # Map all possible values for this taxonomy to the pages that reference
        # them
        self.categories: Dict[str, CategoryPage] = {}

        # Read taxonomy information
        self._read_taxonomy_description()

        # Metadata for category pages
        self.category_meta = self.meta.get("category", {})
        self.category_meta.setdefault("template", "taxonomy/category.html")
        self.category_meta.setdefault("template_title", "{{page.name}}")
        self.category_meta.setdefault("syndication", {})

        # Metadata for archive pages
        self.archive_meta = self.meta.get("archive", {})
        self.archive_meta.setdefault("template", "taxonomy/archive.html")
        self.archive_meta.setdefault("template_title", "{{page.name}} archive")

        # Copy well known meta keys
        for name, metadata in self.site.metadata.items():
            if not metadata.inherited:
                continue
            val = self.meta.get(name)
            self.category_meta.setdefault(name, val)
            self.archive_meta.setdefault(name, val)

    def to_dict(self):
        from staticsite.utils import dump_meta
        res = super().to_dict()
        res["name"] = self.name
        res["categories"] = dump_meta(self.categories)
        res["category_meta"] = dump_meta(self.category_meta)
        res["archive_meta"] = dump_meta(self.archive_meta)
        return res

    def _read_taxonomy_description(self):
        """
        Parse the taxonomy file to read its description
        """
        from staticsite.utils import front_matter
        with open(self.src.abspath, "rt") as fd:
            lines = [x.rstrip() for x in fd]
        try:
            style, meta = front_matter.parse(lines)
            self.meta.update(**meta)
        except Exception:
            log.exception("%s: cannot parse taxonomy information", self.src.relpath)

    def __getitem__(self, name):
        return self.categories[name]

    def finalize(self):
        # Group pages by category
        by_category = defaultdict(list)
        for page in self.site.pages_by_metadata[self.name]:
            categories = page.meta.get(self.name)
            if not categories:
                continue
            # Make sure page.meta.$category is a list
            if isinstance(categories, str):
                categories = page.meta[self.name] = (categories,)
            # File the page in its category lists
            for category in categories:
                by_category[category].append(page)

        # Create category pages
        for category, pages in by_category.items():
            # Sort pages by date, used by series sequencing
            pages.sort(key=lambda p: p.meta["date"])

            # Create category page
            category_meta = dict(self.category_meta)
            category_meta["taxonomy"] = self
            category_meta["pages"] = pages
            category_meta["date"] = pages[-1].meta["date"]
            category_meta["site_path"] = os.path.join(category_meta["site_path"], category)
            category_page = CategoryPage(self, category, meta=category_meta)
            if not category_page.is_valid():
                log.error("%s: unexpectedly reported page not valid, but we have to add it anyway", category_page)
            self.categories[category] = category_page
            self.site.add_page(category_page)

            # Create archive page
            archive_meta = dict(self.archive_meta)
            archive_meta["taxonomy"] = self
            archive_meta["pages"] = pages
            archive_meta["category"] = category_page
            archive_meta["date"] = category_meta["date"]
            archive_meta["site_path"] = os.path.join(archive_meta["site_path"], category, "archive")
            archive_page = CategoryArchivePage(meta=archive_meta)
            if not archive_page.is_valid():
                log.error("%s: unexpectedly reported page not valid, but we have to add it anyway", archive_page)
            category_page.meta["archive"] = archive_page
            self.site.add_page(archive_page)

        # Replace category names with category pages in each categorized page
        for page in self.site.pages_by_metadata[self.name]:
            categories = page.meta.get(self.name)
            if not categories:
                continue
            page.meta[self.name] = [self.categories[c] for c in categories]

        # Sort categories dict by category name
        self.categories = {k: v for k, v in sorted(self.categories.items())}

        # Set self.meta.pages to the sorted list of categories
        self.meta["pages"] = list(self.categories.values())


@functools.total_ordering
class CategoryPage(Page):
    """
    Index page showing all the pages tagged with a given taxonomy item
    """
    TYPE = "category"

    def __init__(self, taxonomy, name, meta):
        super().__init__(
            site=taxonomy.site,
            src=None,
            site_path=meta["site_path"],
            dst_relpath=os.path.join(meta["site_path"], "index.html"),
            meta=meta)
        # Category name
        self.name = name
        # Index of each page in the category sequence
        self.page_index: Dict[Page, int] = {page.site_path: idx for idx, page in enumerate(self.meta["pages"])}

    def to_dict(self):
        res = super().to_dict()
        res["name"] = self.name
        return res

    def __lt__(self, o):
        o_taxonomy = getattr(o, "taxonomy", None)
        if o_taxonomy is None:
            return NotImplemented

        o_name = getattr(o, "name", None)
        if o_name is None:
            return NotImplemented

        return (self.taxonomy.name, self.name) < (o_taxonomy.name, o_name)

    def __eq__(self, o):
        o_taxonomy = getattr(o, "taxonomy", None)
        if o_taxonomy is None:
            return NotImplemented

        o_name = getattr(o, "name", None)
        if o_name is None:
            return NotImplemented

        return (self.taxonomy.name, self.name) == (o_taxonomy.name, o_name)

    def sequence(self, page):
        idx = self.page_index.get(page.site_path)
        if idx is None:
            return None

        # Compute a series title for this page.
        # Look for the last defined series title, defaulting to the title of
        # the first page in the series.
        pages = self.meta["pages"]
        series_title = pages[0].meta["title"]
        for p in pages:
            title = p.meta.get("series_title")
            if title is not None:
                series_title = title
            if p == page:
                break

        return {
            # Array with all the pages in the series
            "pages": pages,
            # Assign series_prev and series_next metadata elements to pages
            "index": idx + 1,
            "length": len(pages),
            "first": pages[0],
            "last": pages[-1],
            "prev": pages[idx - 1] if idx > 0 else None,
            "next": pages[idx + 1] if idx < len(pages) - 1 else None,
            "title": series_title,
        }


class CategoryArchivePage(Page):
    """
    Index page showing the archive page for a CategoryPage
    """
    TYPE = "category_archive"

    def __init__(self, meta):
        category_page = meta["category"]
        super().__init__(
            site=category_page.site,
            src=None,
            site_path=meta["site_path"],
            dst_relpath=os.path.join(meta["site_path"], "index.html"),
            meta=meta)

        # Category name
        self.name = category_page.name

    def to_dict(self):
        res = super().to_dict()
        res["name"] = self.name
        return res


class TestTaxonomyPage(TaxonomyPage):
    def _read_taxonomy_description(self):
        pass


FEATURES = {
    "taxonomy": TaxonomyFeature,
}

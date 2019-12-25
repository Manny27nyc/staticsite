from __future__ import annotations
from typing import Dict, Any, Optional, Union
import os
import logging
from urllib.parse import urlparse, urlunparse
from .utils import lazy
from .utils.typing import Meta
from .render import RenderedString
import jinja2

log = logging.getLogger("page")


class PageNotFoundError(Exception):
    pass


class Page:
    """
    A source page in the site.

    This can be a static asset, a file to be rendered, a taxonomy, a
    directory listing, or anything else.
    """
    # Page type
    TYPE: str

    def __init__(
            self,
            parent: "Page",
            src: Optional["staticsite.File"],
            dst_relpath: str,
            meta: Meta):
        # Site for this page
        self.site = parent.site if parent else None
        # Parent page in the directory hierarchy
        self.parent = parent
        # File object for this page on disk, or None if this is an autogenerated page
        self.src = src
        # Relative path in the build directory for the file that will be written
        # when this page gets rendered. For example, `blog/2016/example.md`
        # generates `blog/2016/example/index.html`.
        self.dst_relpath = dst_relpath
        # A dictionary with the page metadata. See the README for documentation
        # about its contents.
        self.meta: Meta = meta

    def is_valid(self) -> bool:
        """
        Enforce common meta invariants.

        Performs validation and completion of metadata.

        :return: True if the page is valid and ready to be added to the site,
                 False if it should be discarded
        """
        # Run metadata on load functions
        for f in self.site.metadata_on_load_functions:
            f(self)

        # TODO: move more of this to on_load functions

        # template must exist, and defaults to page.html
        self.meta.setdefault("template", "page.html")

        # Render the metadata entres generated that are templates for other
        # entries
        self.site.theme.render_metadata_templates(self)

        # title must exist
        if "title" not in self.meta:
            self.meta["title"] = self.meta["site_name"]

        # Check draft status
        if self.site.settings.DRAFT_MODE:
            return True
        if self.draft:
            log.info("%s: still a draft", self.src.relpath)
            return False

        # Check the existance of other mandatory fields
        if "site_url" not in self.meta:
            log.warn("%s: missing meta.site_url", self)
            return False

        # Make sure site_path exists and is relative
        site_path = self.meta.get("site_path")
        if site_path is None:
            log.warn("%s: missing meta.site_path", self)
            return False
        if site_path.startswith("/"):
            self.meta["site_path"] = site_path.lstrip("/")

        return True

    @property
    def draft(self):
        """
        Return True if this page is still a draft (i.e. its date is in the future)
        """
        ts = self.meta.get("date", None)
        if ts is None:
            return False
        if ts <= self.site.generation_time:
            return False
        return True

    @lazy
    def page_template(self):
        template = self.meta["template"]
        if isinstance(template, jinja2.Template):
            return template
        return self.site.theme.jinja2.get_template(template)

    @lazy
    def redirect_template(self):
        return self.site.theme.jinja2.get_template("redirect.html")

    @property
    def date_as_iso8601(self):
        from dateutil.tz import tzlocal
        ts = self.meta.get("date", None)
        if ts is None:
            return None
        # TODO: Take timezone from config instead of tzlocal()
        tz = tzlocal()
        ts = ts.astimezone(tz)
        offset = tz.utcoffset(ts)
        offset_sec = (offset.days * 24 * 3600 + offset.seconds)
        offset_hrs = offset_sec // 3600
        offset_min = offset_sec % 3600
        if offset:
            tz_str = '{0:+03d}:{1:02d}'.format(offset_hrs, offset_min // 60)
        else:
            tz_str = 'Z'
        return ts.strftime("%Y-%m-%d %H:%M:%S") + tz_str

    def resolve_path(self, target: str) -> "Page":
        """
        Return a Page from the site, given a source or site path relative to
        this page.

        The path is resolved relative to this page, and if not found, relative
        to the parent page, and so on until the top.
        """
        # Absolute URLs are resolved as is
        if target.startswith("/"):
            if target == "/":
                target_relpath = ""
            else:
                target_relpath = os.path.normpath(target.lstrip("/"))

            # Try by source path
            res = self.site.pages_by_src_relpath.get(target_relpath)
            if res is not None:
                return res

            # Try by site path
            res = self.site.pages.get(target_relpath)
            if res is not None:
                return res

            # Try adding /static as a compatibility with old links
            target_relpath = "static/" + target_relpath

            # Try by source path
            res = self.site.pages_by_src_relpath.get(target_relpath)
            if res is not None:
                log.warn("%s+%s: please use /static/%s instead of %s", self, target, target)
                return res

            raise PageNotFoundError(f"cannot resolve absolute path {target}")

        # Relative urls are tried based on all path components of this page,
        # from the bottom up

        # First using the source paths
        if self.src is not None:
            root = os.path.dirname(self.src.relpath)
            while True:
                target_relpath = os.path.normpath(os.path.join(root, target))
                if target_relpath == ".":
                    target_relpath = ""

                res = self.site.pages_by_src_relpath.get(target_relpath)
                if res is not None:
                    return res

                if not root:
                    break

                root = os.path.dirname(root)

        # The using the site paths
        root = self.meta["site_path"]
        while True:
            target_relpath = os.path.normpath(os.path.join(root, target))
            if target_relpath == ".":
                target_relpath = ""

            res = self.site.pages.get(target_relpath)
            if res is not None:
                return res

            if not root:
                break

            root = os.path.dirname(root)

        raise PageNotFoundError(f"cannot resolve `{target!r}` relative to `{self!r}`")

    def resolve_url(self, url: str) -> str:
        """
        Resolve internal URLs.

        Returns the argument itself if the URL does not need changing, else
        returns the new URL.

        To check for a noop, check like ``if page.resolve_url(url) is url``

        This is used by url resolver postprocessors, like in markdown or
        restructured text pages.

        For resolving urls in templates, see Theme.jinja2_url_for().
        """
        parsed = urlparse(url)
        if parsed.scheme or parsed.netloc:
            return url
        if not parsed.path:
            return url

        try:
            dest = self.url_for(parsed.path)
        except PageNotFoundError as e:
            log.warn("%s", e)
            return url

        dest = urlparse(dest)

        return urlunparse(
            (dest.scheme, dest.netloc, dest.path,
             parsed.params, parsed.query, parsed.fragment)
        )

    def url_for(self, arg: Union[str, "Page"], absolute=False) -> str:
        """
        Generate a URL for a page, specified by path or with the page itself
        """
        page: "Page"

        if isinstance(arg, str):
            page = self.resolve_path(arg)
        else:
            page = arg

        # If the destination has a different site_url, generate an absolute url
        if self.meta["site_url"] != page.meta["site_url"]:
            absolute = True

        if absolute:
            site_url = page.meta["site_url"].rstrip("/")
            return f"{site_url}/{page.meta['site_path']}"
        else:
            return "/" + page.meta["site_path"]

    def check(self, checker):
        pass

    def target_relpaths(self):
        res = [self.dst_relpath]
        for relpath in self.meta.get("aliases", ()):
            res.append(os.path.join(relpath, "index.html"))
        return res

    def __str__(self):
        return self.meta["site_path"]

    def __repr__(self):
        return "{}:{}".format(self.TYPE, self.src.relpath)

    @lazy
    def content(self):
        """
        Return only the rendered content of the page, without headers, footers,
        and navigation.
        """
        template = self.page_template
        template_content = template.blocks.get("page_content")
        block_name = "page_content"
        if template_content is None:
            template_content = template.blocks.get("content")
            block_name = "content"
            if template_content is None:
                log.warn("%s: `page_content` and `content` not found in template %s", self, template.name)
                return ""

        try:
            return jinja2.Markup("".join(template_content(template.new_context({"page": self}))))
        except jinja2.TemplateError as e:
            log.error("%s: failed to render %s.%s: %s", template.filename, self.src.relpath, block_name, e)
            log.debug("%s: failed to render %s.%s: %s", template.filename, self.src.relpath, block_name, e, exc_info=True)
            # TODO: return a "render error" page? But that risks silent errors
            return ""

    def to_dict(self):
        from .utils import dump_meta
        res = {
            "src": {
                "relpath": str(self.src.relpath),
                "abspath": str(self.src.abspath),
            },
            "dst_relpath": str(self.dst_relpath),
            "meta": dump_meta(self.meta),
        }
        return res

    def render(self):
        res = {
            self.dst_relpath: RenderedString(self.render_template(self.page_template)),
        }

        aliases = self.meta.get("aliases", ())
        if aliases:
            for relpath in aliases:
                html = self.render_template(self.redirect_template)
                res[os.path.join(relpath, "index.html")] = RenderedString(html)

        return res

    def render_template(self, template: jinja2.Template, template_args: Dict[Any, Any] = None) -> str:
        """
        Render a jinja2 template, logging things if something goes wrong
        """
        if template_args is None:
            template_args = {}
        template_args.setdefault("page", self)
        try:
            return template.render(**template_args)
        except jinja2.TemplateError as e:
            log.error("%s: failed to render %s: %s", template.filename, self.src.relpath, e)
            log.debug("%s: failed to render %s: %s", template.filename, self.src.relpath, e, exc_info=True)
            # TODO: return a "render error" page? But that risks silent errors
            return None

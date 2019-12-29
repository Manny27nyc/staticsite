from __future__ import annotations
from typing import List, Tuple
from staticsite import Page, Feature, File
from staticsite.utils import front_matter
from staticsite.archetypes import Archetype
from staticsite.contents import ContentDir, Dir
from staticsite.utils import lazy
from staticsite.utils.typing import Meta
import jinja2
import os
import io
import markdown
import logging

log = logging.getLogger("markdown")


class LinkResolver(markdown.treeprocessors.Treeprocessor):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.page = None
        self.substituted = {}

    def set_page(self, page):
        self.page = page
        self.substituted = {}

    def run(self, root):
        for a in root.iter("a"):
            new_url = self.resolve_url(a.attrib.get("href", None))
            if new_url is not None:
                a.attrib["href"] = new_url

        for img in root.iter("img"):
            new_url = self.resolve_url(img.attrib.get("src", None))
            if new_url is not None:
                img.attrib["src"] = new_url

    def resolve_url(self, url):
        """
        Resolve internal URLs.

        Returns None if the URL does not need changing, else returns the new URL.
        """
        new_url = self.substituted.get(url)
        if new_url is not None:
            return new_url

        from markdown.util import AMP_SUBSTITUTE
        if url.startswith(AMP_SUBSTITUTE):
            # Possibly an overencoded mailto: link.
            # see https://bugs.debian.org/816218
            #
            # Markdown then further escapes & with utils.AMP_SUBSTITUTE, so
            # we look for it here.
            return None

        new_url = self.page.resolve_url(url)
        if new_url is url:
            return None

        self.substituted[url] = new_url
        return new_url


class StaticSiteExtension(markdown.extensions.Extension):
    def extendMarkdown(self, md, md_globals):
        self.link_resolver = LinkResolver(md)
        # Insert instance of 'mypattern' before 'references' pattern
        md.treeprocessors.add('staticsite', self.link_resolver, '_end')
        md.registerExtension(self)

    def reset(self):
        pass

    def set_page(self, page):
        self.link_resolver.set_page(page)


class MarkdownPages(Feature):
    """
    Render ``.md`` markdown pages, with front matter.

    See doc/reference/markdown.md for details.
    """
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        md_staticsite = StaticSiteExtension()
        self.markdown = markdown.Markdown(
            extensions=self.site.settings.MARKDOWN_EXTENSIONS + [
                md_staticsite,
            ],
            extension_configs=self.site.settings.MARKDOWN_EXTENSION_CONFIGS,
            output_format="html5",
        )
        self.link_resolver = md_staticsite.link_resolver

        self.j2_filters["markdown"] = self.jinja2_markdown

        self.render_cache = self.site.caches.get("markdown")

    @jinja2.contextfilter
    def jinja2_markdown(self, context, mdtext):
        return jinja2.Markup(self.render_snippet(context.parent["page"], mdtext))

    def render_page(self, page):
        """
        Render markdown in the context of the given page.
        """
        self.link_resolver.set_page(page)

        cached = self.render_cache.get(page.src.relpath)
        if cached and cached["mtime"] != page.src.stat.st_mtime:
            cached = None
        if cached:
            for src, dest in cached["paths"]:
                if self.link_resolver.resolve_url(src) != dest:
                    cached = None
                    break
        if cached:
            # log.info("%s: markdown cache hit", page.src.relpath)
            return cached["rendered"]

        content = page.get_content()
        self.markdown.reset()
        rendered = self.markdown.convert(content)

        self.render_cache.put(page.src.relpath, {
            "mtime": page.src.stat.st_mtime,
            "rendered": rendered,
            "paths": list(self.link_resolver.substituted.items()),
        })

        return rendered

    def render_snippet(self, page, content):
        """
        Render markdown in the context of the given page.

        It renders the page content by default, unless `content` is set to a
        different markdown string.
        """
        if content is None:
            content = page.get_content()
        self.link_resolver.set_page(page)
        self.markdown.reset()
        return self.markdown.convert(content)

    def load_dir(self, sitedir: ContentDir) -> List[Page]:
        taken: List[str] = []
        pages: List[Page] = []
        for fname, src in sitedir.files.items():
            if not fname.endswith(".md"):
                continue
            taken.append(fname)

            meta = sitedir.meta_file(fname)
            if fname not in ("index.md", "README.md"):
                meta["site_path"] = os.path.join(meta["site_path"], fname[:-3])

            try:
                fm_meta, body = self.load_file_meta(sitedir, src, fname)
            except Exception as e:
                log.warn("%s: Failed to parse markdown page front matter (%s): skipped", src, e)
                log.debug("%s: Failed to parse markdown page front matter: skipped", src, exc_info=e)
                continue

            meta.update(fm_meta)

            page = MarkdownPage(self, src, meta=meta, dir=sitedir, body=body)
            pages.append(page)

        for fname in taken:
            del sitedir.files[fname]

        return pages

    def load_dir_meta(self, sitedir: ContentDir):
        # Load front matter from index.md
        # Do not try to load front matter from README.md, as one wouldn't
        # clutter a repo README with staticsite front matter
        index = sitedir.files.get("index.md")
        if index is None:
            return

        try:
            with open(index.abspath, "rb") as fd:
                fmt, meta = front_matter.read_partial(fd)
        except Exception as e:
            log.debug("%s: failed to parse front matter", index.relpath, exc_info=e)
            log.warn("%s: failed to parse front matter", index.relpath)
        else:
            sitedir.add_dir_config(meta)

    def load_file_meta(self, sitedir, src, fname) -> Tuple[Meta, List[str]]:
        """
        Load metadata for a file.

        Returns the metadata and the markdown lines for the rest of the file
        """
        # Read the contents

        # Parse separating front matter and markdown content
        with sitedir.open(fname, src, "rb") as fd:
            fmt, meta = front_matter.read_partial(fd)

            body = []
            for line in fd:
                body.append(line.strip().decode())

            # Remove leading empty lines
            while body and not body[0]:
                body.pop(0)

            # Read title from first # title if not specified in metadata
            if not meta.get("title", ""):
                if body and body[0].startswith("# "):
                    meta["title"] = body.pop(0)[2:].strip()

                # Remove leading empty lines again
                while body and not body[0]:
                    body.pop(0)

        return meta, body

    def try_load_archetype(self, archetypes, relpath, name):
        if os.path.basename(relpath) != name + ".md":
            return None
        return MarkdownArchetype(archetypes, relpath, self)


def parse_markdown_with_front_matter(fd):
    """
    Parse lines of markdown with front matter.

    Returns two arrays: one with the lines of front matter and one with the
    lines of markdown.
    """
    fmatter = []
    body = []

    front_matter_end = None
    in_front_matter = True

    for lineno, line in enumerate(fd, 1):
        line = line.rstrip()
        if lineno == 1:
            if line == "{":
                front_matter_end = "}"
            elif line == "---":
                front_matter_end = "---"
            elif line == "+++":
                front_matter_end = "+++"
            else:
                in_front_matter = False

        if in_front_matter:
            fmatter.append(line)
            if lineno > 1 and line == front_matter_end:
                in_front_matter = False
        else:
            body.append(line)

    return fmatter, body


class MarkdownArchetype(Archetype):
    def __init__(self, archetypes, relpath, mdpages):
        super().__init__(archetypes, relpath)
        self.mdpages = mdpages

    def render(self, **kw):
        meta, rendered = super().render(**kw)

        # Reparse the rendered version
        with io.StringIO(rendered) as fd:
            # Reparse it separating front matter and markdown content
            fmatter, body = parse_markdown_with_front_matter(fd)
        try:
            style, meta = front_matter.parse(fmatter)
        except Exception as e:
            log.debug("archetype %s: failed to parse front matter", self.relpath, exc_info=e)
            log.warn("archetype %s: failed to parse front matter", self.relpath)

        # Make a copy of the full parsed metadata
        archetype_meta = dict(meta)

        # Remove the path entry
        meta.pop("path", None)

        # Reserialize the page with the edited metadata
        fmatter = front_matter.write(meta, style)
        with io.StringIO() as fd:
            fd.write(fmatter)
            print(file=fd)
            for line in body:
                print(line, file=fd)
            post_body = fd.getvalue()

        return archetype_meta, post_body


class MarkdownPage(Page):
    TYPE = "markdown"

    def __init__(
            self, mdpages: MarkdownPages, src: File, meta: Meta, dir: Dir, body: List[str]):
        super().__init__(site=mdpages.site, src=src, meta=meta, dir=dir)

        self.meta["build_path"] = os.path.join(meta["site_path"], "index.html")

        # Indexed by default
        self.meta.setdefault("indexed", True)

        # Shared markdown environment
        self.mdpages = mdpages

        # Sequence of lines found in the body
        self.body = body

    def get_content(self):
        return "\n".join(self.body)

    def check(self, checker):
        self.mdpages.render_page(self)

    @lazy
    def content(self):
        return self.mdpages.render_page(self)


FEATURES = {
    "md": MarkdownPages,
}

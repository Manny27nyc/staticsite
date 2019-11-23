from staticsite import Site
from staticsite.settings import Settings
from staticsite.utils import timings
import sys
import os
import logging

log = logging.getLogger()


class CmdlineError(RuntimeError):
    pass


class Command:
    # Command name (as used in command line)
    # Defaults to the lowercased class name
    NAME = None

    # Command description (as used in command line help)
    # Defaults to the strip()ped class docstring.
    DESC = None

    def __init__(self, args):
        self.args = args
        self.setup_logging()
        self.settings = Settings()

    def setup_logging(self):
        FORMAT = "%(asctime)-15s %(levelname)s %(message)s"
        if self.args.debug:
            logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, format=FORMAT)
        elif self.args.verbose:
            logging.basicConfig(level=logging.INFO, stream=sys.stderr, format=FORMAT)
        else:
            logging.basicConfig(level=logging.WARN, stream=sys.stderr, format=FORMAT)

    def load_site(self):
        # Instantiate site
        site = Site(settings=self.settings)
        with timings("Loaded site in %fs"):
            site.load()
        with timings("Analysed site tree in %fs"):
            site.analyze()
        return site

    @classmethod
    def make_subparser(cls, subparsers):
        name = cls.NAME
        if name is None:
            name = cls.__name__.lower()

        desc = cls.DESC
        if desc is None:
            desc = cls.__doc__.strip()

        parser = subparsers.add_parser(name, help=desc)
        parser.set_defaults(handler=cls)
        parser.add_argument("-v", "--verbose", action="store_true", help="verbose output")
        parser.add_argument("--debug", action="store_true", help="verbose output")
        return parser


class SiteCommand(Command):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)

        # Look for extra settings
        settings_files = ['settings.py', '.staticsite.py']
        if self.args.project:
            if os.path.isfile(self.args.project):
                # If a project file is mentioned, take its directory as default
                # project root
                settings_file = os.path.abspath(self.args.project)
                settings_dir, settings_file = os.path.split(settings_file)
                if not self.args.project.endswith(".py"):
                    log.warn("%s: project settings does not end in `.py`: contents ignored", settings_file)
                else:
                    settings_files = [settings_file]
            else:
                # If a project directory is mentioned, take it as default
                # project root
                settings_dir = os.path.abspath(self.args.project)
        else:
            settings_dir = os.getcwd()

        # Load the first settings file found (if any)
        for relpath in settings_files:
            abspath = os.path.join(settings_dir, relpath)
            if os.path.isfile(abspath):
                log.info("%s: loading settings", abspath)
                self.settings.load(abspath)
                break

        # Set default project root if undefined
        if self.settings.PROJECT_ROOT is None:
            self.settings.PROJECT_ROOT = settings_dir

        # Command line overrides for settings
        if self.args.theme:
            self.settings.THEME = (os.path.abspath(self.args.theme),)
        if self.args.content:
            self.settings.CONTENT = os.path.abspath(self.args.content)
        if self.args.archetypes:
            self.settings.ARCHETYPES = os.path.abspath(self.args.archetypes)
        if self.args.output:
            self.settings.OUTPUT = os.path.abspath(self.args.output)
        if self.args.draft:
            self.settings.DRAFT_MODE = True

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)

        parser.add_argument("project", nargs="?",
                            help="project directory or .py configuration file (default: the current directory)")
        parser.add_argument("--theme", help="theme directory location. Overrides settings.THEME")
        parser.add_argument("--content", help="content directory location. Overrides settings.CONTENT")
        parser.add_argument("--archetypes", help="archetypes directory location. Override settings.ARCHETYPES")
        parser.add_argument("-o", "--output", help="output directory location. Override settings.OUTPUT")
        parser.add_argument("--draft", action="store_true", help="do not ignore pages with date in the future")

        return parser

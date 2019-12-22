from __future__ import annotations
from typing import Any
import inspect
from . import site


class Metadata:
    """
    Declarative description of a metadata used by staticsite
    """

    def __init__(self, name, inherited: bool = False, structure: bool = False, doc: str = ""):
        """
        :arg name: name of this metadata element
        :arg inherited: set to True if this metadata, when present in a
                        directory index, should be inherited by other files in
                        directories and subdirectories. False if it stays local
                        to the file.
        :arg structure: set to True if this element is a structured value. Set
                        to false if it is a simple value like an integer or
                        string
        :arg doc: documentation for this metadata element
        """
        self.site: "site.Site" = None
        self.name: str = name
        self.inherited: bool = inherited
        self.structure: bool = structure
        self.doc = inspect.cleandoc(doc)

    def clean_value(self, val: Any) -> Any:
        """
        Return a validated and cleaned version of this metadata
        """
        return val

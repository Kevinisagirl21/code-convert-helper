"""Sphinx configuration for the py2rust documentation."""

from __future__ import annotations

import os
import sys

# Two separate path entries are needed here, not one:
#   - "../src" lets autodoc import the tool's own modules the same flat
#     way the tests and pipeline.py do (`import ir`, `import codegen`,
#     `import cli`, ...) -- there is no `py2rust` wrapper package.
#   - ".." (the repo root) lets *this file* import `src` as a package,
#     purely to read `__version__` below -- `src/` only exists as a
#     traversable package from one level up, not from inside itself.
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("../src"))

project = "py2rust"
copyright = "2026, py2rust contributors"
author = "py2rust contributors"

from src import __version__ as release  # noqa: E402

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_autodoc_typehints",
]

napoleon_google_docstring = True
napoleon_numpy_docstring = True

autodoc_member_order = "bysource"
autodoc_typehints = "description"

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "furo"
html_static_path = ["_static"]

"""Sphinx configuration for the pyrite documentation."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath("../src"))

project = "pyrite"
copyright = "2026, pyrite contributors"
author = "pyrite contributors"

from pyrite import __version__ as release  # noqa: E402

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

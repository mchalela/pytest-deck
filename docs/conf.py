"""Sphinx configuration for the pytest-deck documentation.

Built with MyST (Markdown sources) + autodoc. See docs/index.md for the
top-level table of contents. Published on ReadTheDocs via .readthedocs.yaml.
"""

from importlib import metadata

# -- Project information -----------------------------------------------------

project = "pytest-deck"
author = "Martin Chalela"
copyright = "2026, Martin Chalela"

# Single-source the version from the installed package metadata so the docs
# never drift from pyproject.toml. Falls back gracefully if not yet installed.
try:
    release = metadata.version("pytest-deck")
except metadata.PackageNotFoundError:  # pragma: no cover - docs-only fallback
    release = "0.1.0"
version = ".".join(release.split(".")[:2])

# -- General configuration ---------------------------------------------------

extensions = [
    "myst_parser",  # Markdown source support
    "sphinx.ext.autodoc",  # pull docstrings from pytest_deck
    "sphinx.ext.autosummary",  # generate API summary tables
    "sphinx.ext.napoleon",  # tolerate Google/NumPy sections if added later
    "sphinx.ext.intersphinx",  # cross-link to Python / pytest docs
    "sphinx.ext.viewcode",  # [source] links next to autodoc entries
    "sphinx_copybutton",  # copy button on code blocks
    "sphinx_design",  # grid/card components for the landing page
]

autosummary_generate = True

# The package imports a few pytest internals at module load (rootdir.py pulls
# from _pytest.config.findpaths; import_paths.py calls inspect.signature() on
# _pytest.pathlib.resolve_pkg_root_and_module_name at import time). pytest is a
# hard runtime dependency, so _pytest is always importable wherever the docs
# build runs (RTD installs `.[docs]`, which brings pytest in). So we deliberately
# do not mock it: a MagicMock stand-in has no real __signature__, and that
# import-time inspect.signature() call would raise TypeError under autodoc and
# fail the build.
autodoc_mock_imports = []

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
}
# Leading-underscore modules and functions are internal machinery; autodoc's
# defaults keep them out of the rendered API (the naming already separates
# public from private).
autodoc_member_order = "bysource"

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pytest": ("https://docs.pytest.org/en/stable", None),
}

templates_path = ["_templates"]
# ARCHITECTURE.md and INVARIANTS.md are internal contributor design docs that
# live alongside these sources; they are adapted into the public "How It Works"
# section, not published verbatim, so keep them out of the Sphinx build.
exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
    "ARCHITECTURE.md",
    "INVARIANTS.md",
]

# -- MyST configuration ------------------------------------------------------

myst_enable_extensions = [
    "colon_fence",  # ::: fenced directives
    "deflist",  # definition lists
    "linkify",  # bare URLs become links
    "substitution",  # {{ }} substitutions
]
myst_heading_anchors = 3  # auto header anchors up to <h3>

# Substitutions usable as {{ name }} in Markdown sources.
myst_substitutions = {
    "release": release,
}

# -- HTML output -------------------------------------------------------------

html_theme = "furo"
html_title = f"pytest-deck {release}"
html_static_path = ["_static"]
html_css_files = ["custom.css"]

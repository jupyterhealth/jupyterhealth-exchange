# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys
from datetime import datetime

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'JupyterHealth'
copyright = f'{datetime.now().year}, JupyterHealth Team'
author = 'JupyterHealth Team'
release = '1.0.0'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
    'sphinxcontrib.httpdomain',
    'sphinx_copybutton',
    'sphinx_design',
    'myst_parser',
]

# Add support for both RST and Markdown
source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

templates_path = ['_templates']
exclude_patterns = []

# The master toctree document
master_doc = 'index'

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'pydata_sphinx_theme'
html_static_path = ['_static']
html_logo = "_static/images/logo/logo.png"
html_title = ''  # Remove text title since logo contains the name
html_css_files = ['custom.css']

# PyData theme options
html_theme_options = {
    "github_url": "https://github.com/jupyterhealth/jupyterhealth-exchange",
    "navbar_end": ["theme-switcher", "navbar-icon-links"],
    "show_nav_level": 1,
    "navigation_depth": 2,
    "show_toc_level": 2,
    "header_links_before_dropdown": 5,
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/jupyterhealth/jupyterhealth-exchange",
            "icon": "fa-brands fa-github",
        },
    ],
    "use_edit_page_button": True,
    "show_version_warning_banner": False,
    "navbar_align": "left",
    "primary_sidebar_end": [],
    "secondary_sidebar_items": ["page-toc", "edit-this-page"],
    "show_prev_next": False,
    "nosidebar": False,
    "sidebar_includehidden": True,
    "collapse_navbar": False,
    "pygment_light_style": "default",
    "pygment_dark_style": "monokai",
}

# Edit on GitHub button
html_context = {
    "github_user": "jupyterhealth",
    "github_repo": "jupyterhealth-exchange",
    "github_version": "main",
    "doc_path": "docs/source",
}

# -- Options for MyST Markdown parser ----------------------------------------
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "html_image",
]

# -- Options for intersphinx extension ---------------------------------------
intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
    'django': ('https://docs.djangoproject.com/en/stable/', 'https://docs.djangoproject.com/en/stable/_objects/'),
    'jupyterhub': ('https://jupyterhub.readthedocs.io/en/stable/', None),
}

# -- Options for copy button -------------------------------------------------
copybutton_prompt_text = r">>> |\.\.\. |\$ |In \[\d*\]: | {2,5}\.\.\.: | {5,8}: "
copybutton_prompt_is_regexp = True

# -- Options for HTTP domain -------------------------------------------------
http_headers_ignore_prefixes = ['X-']

# -- Options for the opengraph extension -------------------------------------
# ref: https://github.com/wpilibsuite/sphinxext-opengraph#options
#
# OpenGraph extension not installed - commenting out OGP settings
# ogp_site_url is set automatically by RTD
# ogp_image = "_static/images/logo/logo.png"
# ogp_use_first_image = True
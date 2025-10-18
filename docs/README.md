# JupyterHealth Exchange Documentation

This directory contains the source for JupyterHealth Exchange documentation, built with Sphinx and the PyData theme to match the Jupyter ecosystem standards.

## Quick Start

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Build Documentation

```bash
make html
```

### View Documentation

Open `build/html/index.html` in your browser, or use the preview script:

```bash
./preview.sh
```

### Live Development Server

For development with auto-reload:

```bash
make livehtml
```

This will start a server at http://localhost:8000 that automatically rebuilds when you save changes.

## Documentation Structure

```
source/
├── index.rst              # Home page
├── quickstart.rst         # Quick start guide
├── installation/          # Installation guides
│   ├── index.rst
│   ├── zero-to-prod.rst   # Complete setup guide
│   ├── docker.rst         # Docker deployment
│   └── cloud.rst          # Cloud deployment
├── configuration/         # Configuration guides
│   ├── index.rst
│   ├── environment.rst    # Environment variables
│   ├── authentication.rst # Auth setup
│   ├── database.rst       # Database config
│   └── security.rst       # Security settings
├── api/                   # API documentation
│   ├── index.rst
│   ├── rest.rst          # REST API
│   ├── fhir.rst          # FHIR API
│   ├── oauth.rst         # OAuth flows
│   └── examples.rst      # Usage examples
└── _static/              # Custom CSS/JS
```

## Adding New Pages

1. Create a new `.rst` file in the appropriate directory
2. Add it to the relevant `index.rst` toctree
3. Follow the existing format for consistency

## Writing Documentation

We use both reStructuredText (.rst) and Markdown (.md) files:

- **RST files**: Primary documentation format for Sphinx
- **Markdown files**: Supported via MyST parser

### RST Quick Reference

```rst
================
Chapter Title
================

Section Title
-------------

Subsection Title
~~~~~~~~~~~~~~~~

**Bold text**, *italic text*, ``code``

.. code-block:: python

   def hello():
       print("Hello, JHE!")

.. note::

   This is an important note.

.. warning::

   This is a warning message.

:doc:`link-to-other-doc`
`External Link <https://example.com>`_
```

## Building for Production

### ReadTheDocs

This documentation is designed to be hosted on ReadTheDocs. To deploy:

1. Create account at https://readthedocs.org
2. Import the jupyterhealth-exchange repository
3. Documentation will build automatically on commits

### Manual Build

For PDF or other formats:

```bash
make latexpdf  # Requires LaTeX
make epub      # EPUB format
```

## Theme Customization

The PyData Sphinx Theme is configured in `source/conf.py`. Key options:

- Navigation depth
- GitHub links
- Theme switcher (light/dark)
- Logo and branding

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test locally with `make html`
5. Submit a pull request

## Troubleshooting

**Missing module errors**: Install all requirements
```bash
pip install -r requirements.txt
```

**Build warnings**: Check for:
- Missing toctree entries
- Broken internal links
- Missing files

**Theme issues**: Clear cache and rebuild
```bash
make clean
make html
```

## Resources

- [Sphinx Documentation](https://www.sphinx-doc.org/)
- [PyData Theme Docs](https://pydata-sphinx-theme.readthedocs.io/)
- [MyST Parser](https://myst-parser.readthedocs.io/)
- [reStructuredText Primer](https://www.sphinx-doc.org/en/master/usage/restructuredtext/basics.html)
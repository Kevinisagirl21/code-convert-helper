code-convert-helper
=======

A Python-to-Rust conversion assistant that preserves comments and never
silently resolves a judgment call.

This is the API reference for the **v1 core-subset prototype**, now with
Milestone 2's ``#!`` ownership directives, import recursion, and
Milestone 3's clippy-clean codegen. For the project's design rationale,
see ``PROJECT_OVERVIEW.md``, ``ARCHITECTURE.md``, ``PLUGIN_API.md``, and
``ROADMAP.md`` alongside the repository.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   pipeline
   preflight
   ir
   typing_inference
   ambiguity
   directives
   ownership
   imports
   codegen
   plugins
   report
   cli

Quick start
-----------

.. code-block:: bash

   pip install -e ".[dev]"
   code-convert-helper preflight examples/sample.py
   code-convert-helper convert examples/sample.py --out output

That last command also writes ``output/ownership_log.{json,md}`` and, by
default, follows every import reachable from ``examples/sample.py`` (up
to ``--import-depth``, default 5), converting each one under
``output/ir/_imports/``. Generated Rust for the core subset is written to
be clippy-clean (Milestone 3) -- see ``verification/README.md`` for how
to check this with ``cargo clippy``.

Indices
-------

* :ref:`genindex`
* :ref:`modindex`

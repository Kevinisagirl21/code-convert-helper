pyrite
======

A Python-to-Rust conversion assistant that preserves comments and never
silently resolves a judgment call.

This is the API reference for the **v1 core-subset prototype**. For the
project's design rationale, see ``PROJECT_OVERVIEW.md``,
``ARCHITECTURE.md``, and ``PLUGIN_API.md`` alongside the repository.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   pipeline
   preflight
   ir
   typing_inference
   ambiguity
   codegen
   plugins
   report
   cli

Quick start
-----------

.. code-block:: bash

   pip install -e ".[dev]"
   pyrite preflight examples/sample.py
   pyrite convert examples/sample.py --out output

Indices
-------

* :ref:`genindex`
* :ref:`modindex`

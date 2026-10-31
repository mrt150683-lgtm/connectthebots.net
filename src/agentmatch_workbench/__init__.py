"""Local collaboration workbench: a fictional prototype on top of the R0 core.

This package demonstrates one complete collaboration journey through a local,
simulated discovery board: brief -> exact publication approval -> bounded
discovery -> explained shortlist -> bilateral introduction -> first step.

It is a fictional prototype. It does not complete the R1 secure-owner gate
(no encrypted storage, no authentication) or the R2 networking gates (no real
board service, no peer transport). The demo owner switch is a perspective
selector, not authentication.

It is a separate top-level package that imports the R0 ``agentmatch``
package and never modifies it, so the R0 package fingerprint and the
recorded R0 evidence in ``examples/r0/`` are unchanged by its presence.

Layers, each importing only from the ones above it in this list:

- ``domain``: pure rules. Briefs, the brief/notice -> R0 assessment mapping,
  publication approval and grants, board visibility, introductions and first
  steps. No clocks are read and no I/O happens here.
- ``application``: use cases (``WorkbenchService``), ports for storage and
  search, and allowlisted view records returned to the browser.
- ``adapters``: the in-memory store, the in-memory lexical search index, the
  fictional seed loader and the loopback HTTP adapter.

Standard library only, like the rest of the R0 package.
"""

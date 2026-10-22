# Third-party material

se2-modkit bundles no third-party code or assets.

Runtime dependency: [NumPy](https://numpy.org/) (BSD-3-Clause), installed from PyPI.

`se2modkit.diag` talks to an in-game bridge plugin (a Pulsar plugin that runs an MCP server inside the game) over
loopback HTTP. No plugin code is bundled; the user installs the plugin separately under its own license.

Space Engineers 2 and its SDK are products of Keen Software House. No Keen code, assets or type data is part of this repository; the tool reads them from the user's own installation at run time.

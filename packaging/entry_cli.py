"""PyInstaller entry: mmlcc.exe (console CLI)."""

import sys

from mml_cloud_courier.cli.__main__ import main

if __name__ == "__main__":
    sys.exit(main())

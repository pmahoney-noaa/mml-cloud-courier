"""Console-mode entry point: python -m mml_cloud_courier.service"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from mml_cloud_courier.service.config import load_config
from mml_cloud_courier.service.host import run_console


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mml_cloud_courier.service")
    parser.add_argument("--data-dir", default=None,
                        help="Data directory (default: %%ProgramData%%\\MML Cloud Transfer,"
                             " or MMLCT_DATA_DIR)")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    run_console(load_config(args.data_dir, port=args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

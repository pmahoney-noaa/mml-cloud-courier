"""Write packaging/_version_info.txt (PyInstaller VSVersionInfo) from
pyproject.toml's [project] version — the single version source.

Run by build_release.ps1 before PyInstaller:

    .venv\\Scripts\\python packaging\\version_info.py
"""

from __future__ import annotations

import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

TEMPLATE = """\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({numbers}),
    prodvers=({numbers}),
    mask=0x3F,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'NOAA Fisheries Marine Mammal Laboratory'),
        StringStruct('FileDescription', 'MML Cloud Courier'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('ProductName', 'MML Cloud Courier'),
        StringStruct('ProductVersion', '{version}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
"""


def main() -> int:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    version = pyproject["project"]["version"]
    parts = [int(p) for p in version.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    numbers = ", ".join(str(p) for p in [*parts, 0])
    out = HERE / "_version_info.txt"
    out.write_text(
        TEMPLATE.format(numbers=numbers, version=version), encoding="utf-8"
    )
    print(f"wrote {out} for version {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

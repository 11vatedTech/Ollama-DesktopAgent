# PyInstaller spec: one runtime folder, two executables.
#   11vated-Intelligence.exe  windowed desktop shell
#   11vated.exe               console CLI (profile, qualify, agent, accept, doctor, ...)
# Build with:  pyinstaller --noconfirm --clean packaging/11vated.spec
# ruff: noqa
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

datas, binaries, hiddenimports = [], [], []
for package in ("PySide6", "shiboken6", "jsonschema", "jsonschema_specifications"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden
hiddenimports += collect_submodules("ollama_workstation")
datas += [(str(ROOT / "LICENSE"), "."), (str(ROOT / "SECURITY.md"), ".")]

common = dict(pathex=[str(SRC)], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
              excludes=["tkinter", "pytest", "ruff", "mypy"], noarchive=False)

gui = Analysis([str(SRC / "ollama_workstation" / "__main__.py")], **common)
cli = Analysis([str(ROOT / "packaging" / "cli_entry.py")], **common)

gui_pyz = PYZ(gui.pure)
cli_pyz = PYZ(cli.pure)

gui_exe = EXE(gui_pyz, gui.scripts, [], exclude_binaries=True, name="11vated-Intelligence",
              console=False, disable_windowed_traceback=False, upx=False)
cli_exe = EXE(cli_pyz, cli.scripts, [], exclude_binaries=True, name="11vated",
              console=True, upx=False)

COLLECT(gui_exe, gui.binaries, gui.datas, cli_exe, cli.binaries, cli.datas,
        strip=False, upx=False, name="11vated-Intelligence")

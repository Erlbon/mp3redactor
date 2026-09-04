# PyInstaller spec file for The ɯP3 Redactor.
#
# Build on Windows with: pyinstaller mp3redactor.spec
# (PyInstaller builds for whatever platform it runs ON -- cannot be
# cross-compiled from Linux/macOS to produce a Windows .exe.)
#
# IMPORTANT: mp3val.exe and keyfinder-cli.exe are NOT bundled by this
# spec -- they're external binaries the app shells out to (or finds
# bundled next to the .exe at RUNTIME via core.tool_locator.find_tool(),
# which checks tools/<exe> before falling back to PATH). build_exe.bat
# copies a local tools/ folder into dist/tools as a separate step after
# this build, since --add-data content unpacks to PyInstaller's onefile
# temp extraction dir at runtime, not next to the actual .exe, and
# tool_locator specifically looks next to the exe itself.
#
# redactor_common is not listed in datas/hiddenimports -- it's a plain
# importable Python package sitting next to main.py, same as core/ and
# gui/, so PyInstaller's static import analysis picks it up
# automatically without any special-casing.

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("assets/icon.ico", "assets"),
        ("README.md", "."),
        ("CHANGELOG.md", "."),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="mp3redactor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed app -- no console popup
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

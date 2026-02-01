# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

os_name = os.environ.get('MATRIX_OS', 'unknown')
upx_enabled = 'windows' in os_name
upx_directory = 'C:\\upx' if upx_enabled else None

print('Building on', os_name, 'with upx enabled:', upx_enabled, 'upx directory:', upx_directory)

hidden_imports = [
        'utils.buff_helper',
        'utils.uu_helper',
        'utils.ApiCrypt',
        'utils.BuffApiCrypt',
        'PyC5Game',
        'PyECOsteam',
        'uuyoupinapi',
        'BuffApi'
    ]
if os.path.exists('requirements.txt'):
    with open('requirements.txt') as f:
        for line in f:
            hidden_imports.append(line.strip())

datas = [('plugins', 'plugins')]
binaries= []

apprise = collect_all('apprise')
datas += apprise[0]
binaries += apprise[1]

a = Analysis(
    ['Steamauto.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Steamauto-' + os_name,
    onefile=True,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=upx_enabled,
    console=True,
    disable_windowed_traceback=False,
)
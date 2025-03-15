# -*- mode: python ; coding: utf-8 -*-
import os

os_name = os.environ.get('MATRIX_OS', 'unknown')
upx_enabled = 'windows' in os_name
upx_directory = 'C:\\upx' if upx_enabled else None

print('Building on', os_name, 'with upx enabled:', upx_enabled, 'upx directory:', upx_directory)

a = Analysis(
    ['Steamauto.py'],  # 主脚本
    pathex=[],
    binaries=[],
    datas=[('plugins', 'plugins')],  # 数据文件
    hiddenimports=[
        'utils.buff_helper',
        'utils.uu_helper',
        'utils.ApiCrypt',
        'PyC5Game',
        'PyECOsteam',
        'uuyoupinapi',
        'BuffApi',
    ],
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
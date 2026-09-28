# -*- mode: python ; coding: utf-8 -*-
# 使用PyInstaller将JavSP打包为单个可执行文件: poetry run pyinstaller JavSP.spec
# 打包结果位于 dist/JavSP(.exe)，首次运行时会在其旁边生成默认的config.yml
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

datas = [
    ('config.yml', '.'),
    ('data', 'data'),
    ('image', 'image'),
]
# cloudscraper运行时需要读取浏览器UA等数据文件
datas += collect_data_files('cloudscraper')
# 检查更新时通过importlib.metadata读取javsp的版本号
datas += copy_metadata('javsp')

# 抓取器是按配置文件中的名称动态导入的，PyInstaller无法自动发现。
# 不能用collect_submodules：它需要实际导入模块，而抓取器在导入时就会读取配置，
# 在打包环境中导入失败会被静默跳过，因此直接按文件名列出
hiddenimports = ['javsp.web.' + p.stem for p in Path('javsp/web').glob('*.py')
                 if p.stem != '__init__']

a = Analysis(
    ['javsp/__main__.py'],
    pathex=[],
    binaries=collect_dynamic_libs('slimeface'),
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 项目中的unittest目录会遮蔽同名标准库，且运行时并不需要
    excludes=['unittest', 'pytest', '_pytest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    # 启用UTF-8模式，避免在Windows上输出被重定向时按本地编码打印中文而报错
    [('X utf8', None, 'OPTION')],
    a.binaries,
    a.datas,
    [],
    name='JavSP',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    icon='image/JavSP.ico',
)

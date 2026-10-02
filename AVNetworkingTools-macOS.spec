# -*- mode: python ; coding: utf-8 -*-
import os

# Build natively for arm64 or x86_64. A universal2 build is allowed only when
# every collected native dependency contains both slices.
target_arch = os.environ.get('AVNETWORKINGTOOLS_TARGET_ARCH') or None
if target_arch not in (None, 'arm64', 'x86_64', 'universal2'):
    raise ValueError('Unsupported target architecture')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('templates', 'templates'), ('static', 'static'),
           ('manufacturer_data', 'manufacturer_data'), ('LICENSE', '.')],
    hiddenimports=['CoreWLAN', 'CoreLocation'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['win32com', 'win32timezone', 'win32api'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name='AVNetworkingTools', console=False,
          target_arch=target_arch, argv_emulation=False,
          codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False,
               upx=False, name='AVNetworkingTools')
app = BUNDLE(coll, name='AVNetworkingTools.app',
             icon='AVNetworkingTools.icns',
             bundle_identifier='com.jclaerebout.AVNetworkingTools',
             info_plist={'CFBundleShortVersionString': '2.0.0',
                         'CFBundleVersion': '2.0.0',
                         'NSLocationWhenInUseUsageDescription': 'Wi-Fi network details require Location Services.'})

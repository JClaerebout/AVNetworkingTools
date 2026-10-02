#!/bin/zsh
set -e
python3 -m PyInstaller --noconfirm --clean AVNetworkingTools-macOS.spec
stage_dir=$(mktemp -d /private/tmp/avnetworkingtools-build.XXXXXX)
trap 'rm -rf "$stage_dir"' EXIT
ditto --noextattr --norsrc dist/AVNetworkingTools.app "$stage_dir/AVNetworkingTools.app"
codesign --verify --deep --strict "$stage_dir/AVNetworkingTools.app"
hdiutil create -volname AVNetworkingTools -srcfolder "$stage_dir/AVNetworkingTools.app" -ov -format UDZO dist/AVNetworkingTools-macOS-arm64.dmg
print 'Built and verified dist/AVNetworkingTools-macOS-arm64.dmg'

# Building AVNetworkingTools 2.0.0

## Local development

Windows x64: install 64-bit Python and Node.js. Run `python -m pip install -r requirements.txt`, `check.bat`, and `python app.py` from an Administrator terminal. `build.bat` keeps the existing pinned Windows build and PyInstaller spec. Run `smoke-packaged.ps1` after building. Windows runtime regression testing is still required on Windows.

macOS arm64 or Intel: use a native Python 3.12+ installation for that architecture. In a virtual environment, run `python3 -m pip install -r requirements.txt`, then `python3 app.py`. Use `./build-macos.sh` for `dist/AVNetworkingTools.app`. The app stores data in `~/Library/Application Support/AVNetworkingTools/`. No Apple certificate is needed for local builds.

macOS network configuration uses `networksetup` with the signed-in user’s rights first. If macOS rejects a change for insufficient privileges, the app requests administrator authorization for the IP and DNS commands together. It never launches the whole app as root. Service order replaces Windows interface metric. Confirm the selected service and settings before changing them. Capture uses `tcpdump` and requires BPF device access. Apple documents running `sudo tcpdump` for packet capture. This local build does not elevate its capture subprocess. For local capture, Wireshark documents installing its official ChmodBPF package, which grants BPF access to the user after a new login. This is a system-level permission change; review it before installing. Do not run the entire app as root or make `/dev/bpf` world-writable. A scoped privileged capture helper is still needed for general distribution. CoreWLAN requires Location Services authorization to show nearby networks and identifiers. The Wi-Fi Scan button requests access when needed. If access was denied, enable AVNetworkingTools in System Settings > Privacy & Security > Location Services. The app does not disable macOS privacy or security controls.

The macOS build is native arm64 or x86_64. `AVNETWORKINGTOOLS_TARGET_ARCH=universal2 ./build-macos.sh` is supported only if the Python interpreter and every native wheel contains both slices. The local arm64 dependency set includes arm64-only wheels, so separate architecture builds are the reliable option at present. Build Intel on an Intel Python environment and test on a real Intel Mac.

A DMG can be made locally with `hdiutil create -volname AVNetworkingTools -srcfolder dist/AVNetworkingTools.app -ov -format UDZO dist/AVNetworkingTools-macOS-arm64.dmg` (change the architecture name for an Intel build). The app's updater checks GitHub Releases and opens a verified DMG for manual installation. It does not replace a running `.app`.

## Public release preparation

Build Windows and each macOS architecture from the same versioned source, then attach the Windows EXE and Mac DMG assets to one release. No release workflow exists in this repository yet; create and validate one separately before publishing. The Windows updater accepts `AVNetworkingTools.exe` or `AVNetworkingTools-Windows-x64.exe`; macOS accepts `AVNetworkingTools-macOS-universal.dmg` or the matching `arm64`/`x64` DMG. Include GitHub SHA-256 asset digests for the updater.

For distribution, sign the `.app` with an Apple Developer ID Application certificate, use hardened runtime where required, notarize the archive/DMG with Apple notary service, and staple the ticket. Store certificate and notary credentials outside the repository. Test signed builds on clean macOS installations, including Location Services and capture permissions.

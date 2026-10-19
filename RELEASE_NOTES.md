# V2.0.0 — 2026-10-19

- Native macOS arm64 support alongside Windows x64, including adapter configuration, service order, Wi-Fi inspection, and packet capture.
- Quick scans up to /16, including direct ARP discovery of APIPA devices without changing the adapter's static IP, with bounded scheduling, cancellation, progress, and CSV export.
- Local manufacturer lookup during quick scans.
- Persistent drafts, scan results, and tool logs across restarts. Restored tasks stay stopped; passwords are excluded.
- Page keyboard shortcuts: Enter applies or starts the relevant action, and Escape stops or disconnects.
- Automatic free loopback port selection and an independent single-instance lock to avoid conflicts with other desktop applications.
- More reliable handling of tentative Windows IP addresses before reporting a conflict.
- Corrected platform assumptions in automated tests, including a Windows Wi-Fi test that could hang on macOS.

## Packages and validation

Windows x64: `AVNetworkingTools.exe`. macOS Apple Silicon: `AVNetworkingTools-macOS-arm64.dmg`. No Intel Mac package is included.

Both packages passed packaged startup, page navigation, cancelled Save As, and shutdown smoke checks. The Windows Python suite and JavaScript checks passed. The macOS Python suite passed with six Windows network API tests skipped; macOS backend tests ran. Desktop and network validation on both platforms was confirmed by the maintainer.

The macOS package is ad-hoc signed, without Apple Developer ID signing or notarization, and Gatekeeper may block it. Packet capture and direct ARP need BPF access on macOS; direct ARP needs Npcap on Windows. Wi-Fi details on macOS require Location Services permission. See BUILDING.md for setup and distribution requirements.

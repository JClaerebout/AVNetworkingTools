# V2.2.0 — 2026-11-22

- Add and remove temporary secondary IPv4 addresses on Windows and macOS, with subnet previews and source selection for scans and ping.
- Reorder NIC cards by dragging their handles or using arrow keys. Card order is saved per workspace.
- Import saved scripts and Connect setups from other workspaces, with a new name when needed. Explicit library saves remain available after switching workspaces.
- Send multiple selected IP Scan devices to a new or existing script block without leaving the scan.
- Save HTTP and WebSocket connection options, completed results and logs with workspaces. Restored connections stay stopped; request contents and credentials are excluded from field drafts.
- Refresh the NIC card layout and icons.

## Packages

Windows x64: `AVNetworkingTools.exe`. macOS Apple Silicon: `AVNetworkingTools-macOS-arm64.dmg`. No Intel Mac package is included.

The macOS package is ad-hoc signed, without Apple Developer ID signing or notarization, and Gatekeeper may block it. Packet capture and direct ARP need BPF access on macOS; direct ARP needs Npcap on Windows. Wi-Fi details on macOS require Location Services permission. See BUILDING.md for setup and distribution requirements.

# V2.0.0 — 2026-10-19

- Native macOS arm64 support alongside Windows x64, including adapter configuration, service order, Wi-Fi inspection, and packet capture.
- Quick scans up to /16, including direct ARP discovery of APIPA devices without changing the adapter's static IP, with bounded scheduling, cancellation, progress, and CSV export.
- Local manufacturer lookup during quick scans.
- Persistent drafts, scan results, and tool logs across restarts. Restored tasks stay stopped; passwords are excluded.
- Page keyboard shortcuts: Enter applies or starts the relevant action, and Escape stops or disconnects.
- Automatic free loopback port selection and an independent single-instance lock to avoid conflicts with other desktop applications.
- More reliable handling of tentative Windows IP addresses before reporting a conflict.

## Packages

Windows x64: `AVNetworkingTools.exe`. macOS Apple Silicon: `AVNetworkingTools-macOS-arm64.dmg`. No Intel Mac package is included.

The macOS package is ad-hoc signed, without Apple Developer ID signing or notarization, and Gatekeeper may block it. Packet capture and direct ARP need BPF access on macOS; direct ARP needs Npcap on Windows. Wi-Fi details on macOS require Location Services permission. See BUILDING.md for setup and distribution requirements.

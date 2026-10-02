# AVNetworkingTools

A Windows and macOS desktop toolbox for AV commissioning: configure adapters, discover devices, test connections, and inspect network traffic.

## Quick start

On Windows, download `AVNetworkingTools.exe` from the [latest release](https://github.com/JClaerebout/AVNetworkingTools/releases), place it in a convenient folder, and run it. Allow Windows administrator access when prompted; adapter changes and packet capture need it. The app opens a desktop window and serves its interface locally at `http://127.0.0.1:49780`. The packaged app does not require a Python installation.

## What it does

- Configure static IPv4, DHCP, DNS, and adapter priority; reuse saved settings.
- Scan IPv4 networks, identify devices and manufacturers, monitor hosts, and export results.
- Run ping, Wi-Fi inspection, command-line diagnostics, and TCP/UDP/SSH/serial connection tests.
- Build and save multi-device command scripts.
- Use **AV Network Health Check** to inspect visible multicast, IGMP, PTP, and possible RTP traffic, then export a report.
- See active tools in the header and return to them from any page.

## Common workflows

### Configure an adapter

On the home page, select a network adapter and choose DHCP or enter a static IPv4 address, subnet mask, gateway, and DNS servers. Check the result message after applying a change. If needed, **Restore previous** reapplies the primary IPv4 settings captured before the most recent change in this app session. You can also set the IPv4 interface metric to influence adapter priority.

### Find and test devices

In **IP Scan**, choose an adapter and start a quick scan for reachable addresses and MAC data. Use **Lookup details** after the scan to add hostnames, manufacturers, and web services without repeating the address sweep. A complete scan performs those lookups automatically. You can monitor devices for changes and export scan results to CSV.

On macOS, the home page shows network service order instead of a Windows metric. Changing network settings requests administrator authorization for that operation.

Use **Ping** for reachability and **Connect** for TCP, UDP, SSH, or serial tests. **Scripts** can send command sequences to multiple targets. Saved connection presets and scripts do not retain passwords.

### Check AV traffic

Open **More > AV Network Health Check**, select the relevant adapter, and capture for at least 130 seconds. The overview shows an overall assessment and actionable findings. Expand stream groups or Technical Details for IGMP, PTP, RTP, and QoS observations. **Download Report** includes both the summary and detailed measurements.

## Practical limits

- Scans accept IPv4 ranges of up to 1,024 usable hosts. On macOS, resolved neighbors from the selected interface’s ARP cache supplement ICMP replies, so counts can vary between scans as that cache changes. Reachable devices can appear without a MAC address.
- **Restore previous** uses the primary IPv4 settings captured during the current app session. It does not restore secondary addresses or advanced routes.
- Health Check describes traffic visible to the selected interface. It cannot see traffic withheld by the switch, VLAN tags, switch queues, or link-layer PTP. RTP loss and jitter are capture estimates. Npcap is not required.
- Health findings are commissioning prompts. An inconclusive result means the capture was too short, empty, or failed; it does not confirm that the network is healthy.
- Saved histories are stored in `%APPDATA%\AVNetworkingTools` on Windows or `~/Library/Application Support/AVNetworkingTools/` on macOS and keep backup copies.

## Updates and data

The packaged app can check for GitHub releases. Windows can install a verified EXE update; macOS opens a verified DMG for manual installation. Saved histories, presets, scripts, and the local manufacturer database live in the platform application data folder. Export actions use a Windows Save As dialog, so cancelling an export leaves the destination untouched.

## Run from source

On Windows, open a terminal as Administrator:

```powershell
python -m pip install -r requirements.txt
python app.py
```

On macOS, install `requirements.txt` in a native Python environment and run `python3 app.py`. Build with `./build-macos.sh`; see [BUILDING.md](BUILDING.md) for permissions and signing.

Run all Windows checks with `check.bat`. Build the Windows executable with `build.bat`; see [BUILDING.md](BUILDING.md) for the packaged smoke check. The application version is defined in [version.py](version.py).

## License

MIT. See [LICENSE](LICENSE).

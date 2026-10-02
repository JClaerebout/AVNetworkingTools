"""CoreWLAN scan adapter with foreground Location Services authorization."""
import time
import logging

_location_manager = None


def _request_location_on_main_thread():
    global _location_manager
    import CoreLocation
    _location_manager = CoreLocation.CLLocationManager.alloc().init()
    _location_manager.requestWhenInUseAuthorization()


def _check_location_permission():
    try:
        import CoreLocation
        from PyObjCTools import AppHelper
    except ImportError as exc:
        raise RuntimeError("CoreLocation support is missing. Install the macOS requirements.") from exc

    status = CoreLocation.CLLocationManager.authorizationStatus()
    if status == CoreLocation.kCLAuthorizationStatusNotDetermined:
        # The scan was requested from Flask's worker thread. Core Location's
        # manager and authorization prompt belong on the Cocoa main thread.
        AppHelper.callAfter(_request_location_on_main_thread)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            time.sleep(0.25)
            status = CoreLocation.CLLocationManager.authorizationStatus()
            if status != CoreLocation.kCLAuthorizationStatusNotDetermined:
                break
    if status not in (
        CoreLocation.kCLAuthorizationStatusAuthorizedWhenInUse,
        CoreLocation.kCLAuthorizationStatusAuthorizedAlways,
    ):
        raise RuntimeError(
            "Wi-Fi scan needs Location Services. In System Settings > Privacy & Security > "
            "Location Services, allow AVNetworkingTools, then retry."
        )

def scan():
    try:
        import CoreWLAN
        _check_location_permission()
        client = CoreWLAN.CWWiFiClient.sharedWiFiClient()
        interface = client.interface()
        if interface is None:
            raise RuntimeError("No Wi-Fi interface is available.")
        networks, error = interface.scanForNetworksWithName_error_(None, None)
        cached = False
        if error:
            logging.getLogger(__name__).warning("CoreWLAN scan failed: %s", error)
            networks = interface.cachedScanResults()
            if not networks:
                raise RuntimeError(
                    "macOS could not refresh Wi-Fi networks. Keep the app in the foreground and retry. "
                    "If this persists, check Location Services for AVNetworkingTools."
                )
            cached = True
        items = []
        for network in networks or []:
            channel = network.wlanChannel()
            number = str(channel.channelNumber()) if channel else ""
            rssi = int(network.rssiValue())
            band = "2.4GHz" if number.isdigit() and int(number) <= 14 else "5GHz" if number else "unknown"
            items.append({"ssid": network.ssid() or "Hidden network", "bssid": network.bssid() or "",
                          "authentication": "Secure" if network.supportsSecurity_(CoreWLAN.kCWSecurityWPAPersonal) else "Unknown",
                          "encryption": "", "signal_percent": max(0, min(100, 2 * (rssi + 100))),
                          "signal_dbm": rssi, "channel": number, "band": band,
                          "radio_type": "", "channel_width": "", "channel_utilization_percent": None,
                          "connected_stations": None, "medium_available_capacity": ""})
        if not items and not interface.ssid():
            raise RuntimeError("No Wi-Fi results are available. Check Wi-Fi and Location Services permissions.")
        return items, cached
    except ImportError as exc:
        raise RuntimeError("CoreWLAN support is missing. Install the macOS requirements.") from exc

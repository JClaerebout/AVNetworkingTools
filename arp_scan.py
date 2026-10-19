"""Interface-bound ARP discovery, including link-local targets outside the IP subnet."""
import ctypes as c
import os
import socket
import struct
import sys
import time


def request_frame(source_mac, source_ip, target_ip):
    mac = bytes.fromhex(source_mac.replace(':', '').replace('-', ''))
    if len(mac) != 6:
        raise ValueError('Selected NIC has no valid Ethernet MAC address.')
    return (b'\xff' * 6 + mac + b'\x08\x06' +
            struct.pack('!HHBBH', 1, 0x0800, 6, 4, 1) + mac +
            socket.inet_aton(source_ip) + b'\0' * 6 + socket.inet_aton(target_ip)).ljust(60, b'\0')


def reply_address(frame, source_ip, source_mac, network):
    offset = 14
    if len(frame) < 14:
        return None
    kind = frame[12:14]
    while kind in (b'\x81\x00', b'\x88\xa8') and len(frame) >= offset + 4:
        kind = frame[offset + 2:offset + 4]
        offset += 4
    arp = frame[offset:]
    if kind != b'\x08\x06' or len(arp) < 28 or arp[:8] != struct.pack('!HHBBH', 1, 0x0800, 6, 4, 2):
        return None
    if arp[24:28] != socket.inet_aton(source_ip) or arp[18:24] != bytes.fromhex(source_mac.replace(':', '').replace('-', '')):
        return None
    ip = socket.inet_ntoa(arp[14:18])
    import ipaddress
    if ipaddress.ip_address(ip) not in network or ip == source_ip:
        return None
    return ip, ':'.join(f'{byte:02X}' for byte in arp[8:14])


class ArpCapture:
    def __init__(self, nic):
        self.handle = None
        try:
            if sys.platform == 'win32':
                directory = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'Npcap')
                self.dll_directory = os.add_dll_directory(directory)
                self.lib = c.CDLL(os.path.join(directory, 'wpcap.dll'))
                device = r'\Device\NPF_' + nic['guid']
            else:
                self.lib = c.CDLL('/usr/lib/libpcap.A.dylib')
                device = nic['device']
            signatures = {
                'pcap_open_live': ([c.c_char_p, c.c_int, c.c_int, c.c_int, c.c_char_p], c.c_void_p),
                'pcap_setnonblock': ([c.c_void_p, c.c_int, c.c_char_p], c.c_int),
                'pcap_datalink': ([c.c_void_p], c.c_int),
                'pcap_sendpacket': ([c.c_void_p, c.c_char_p, c.c_int], c.c_int),
                'pcap_next_ex': ([c.c_void_p, c.POINTER(c.c_void_p), c.POINTER(c.c_void_p)], c.c_int),
                'pcap_close': ([c.c_void_p], None),
            }
            for name, (args, result) in signatures.items():
                fn = getattr(self.lib, name)
                fn.argtypes, fn.restype = args, result
            error = c.create_string_buffer(256)
            self.handle = self.lib.pcap_open_live(device.encode(), 96, 0, 20, error)
            if not self.handle:
                raise OSError(error.value.decode(errors='replace'))
            if self.lib.pcap_datalink(self.handle) != 1:
                raise OSError('Selected interface does not provide Ethernet frames.')
            if self.lib.pcap_setnonblock(self.handle, 1, error) != 0:
                raise OSError(error.value.decode(errors='replace'))
        except Exception as exc:
            self.close()
            raise RuntimeError('APIPA discovery needs packet access on the selected NIC (Npcap on Windows; BPF access on macOS). ' + str(exc)) from exc

    def send(self, frame):
        if self.lib.pcap_sendpacket(self.handle, frame, len(frame)) != 0:
            raise OSError('Could not send ARP request on selected NIC.')

    def receive(self):
        header, data = c.c_void_p(), c.c_void_p()
        result = self.lib.pcap_next_ex(self.handle, c.byref(header), c.byref(data))
        if result < 0:
            raise OSError('ARP capture stopped unexpectedly.')
        if result == 0:
            return None
        # pcap_pkthdr: timeval (two C longs), captured length, original length.
        length = c.c_uint32.from_address(header.value + 2 * c.sizeof(c.c_long)).value
        return c.string_at(data, min(length, 96))

    def close(self):
        if self.handle:
            self.lib.pcap_close(self.handle)
            self.handle = None
        directory = getattr(self, 'dll_directory', None)
        if directory:
            directory.close()
            self.dll_directory = None


def sweep(nic, network, stop, progress, found, capture_factory=ArpCapture):
    """Two paced passes; capture and cancellation run between every small batch."""
    capture = capture_factory(nic)
    try:
        for pass_number in range(2):
            for number, target in enumerate(network.hosts(), 1):
                if stop.is_set():
                    return
                if str(target) != nic['ip']:
                    capture.send(request_frame(nic['mac'], nic['ip'], str(target)))
                if str(target) != nic['ip']:
                    progress()
                # Bound work even on a busy network, and pace at ~500 requests/s.
                for _ in range(64):
                    frame = capture.receive()
                    if frame is None:
                        break
                    reply = reply_address(frame, nic['ip'], nic['mac'], network)
                    if reply:
                        found(*reply)
                if stop.wait(0.002):
                    return
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and not stop.is_set():
                frame = capture.receive()
                if frame:
                    reply = reply_address(frame, nic['ip'], nic['mac'], network)
                    if reply:
                        found(*reply)
                else:
                    stop.wait(0.01)
    finally:
        capture.close()

"""Read IPv4 frames from tcpdump's pcap stream without duplicating packet analysis."""
import struct
import subprocess


def _read_exact(stream, count):
    data = bytearray()
    while len(data) < count:
        chunk = stream.read(count - len(data))
        if not chunk:
            raise EOFError("Packet capture stopped.")
        data.extend(chunk)
    return bytes(data)


def _ipv4_frame(frame, link_type):
    if link_type == 1:  # Ethernet, possibly VLAN tagged
        offset = 12
        if len(frame) < 14:
            return None
        ether_type = int.from_bytes(frame[offset:offset + 2], "big")
        offset = 14
        while ether_type in (0x8100, 0x88a8) and len(frame) >= offset + 4:
            ether_type = int.from_bytes(frame[offset + 2:offset + 4], "big")
            offset += 4
        return frame[offset:] if ether_type == 0x0800 else None
    if link_type == 0:  # BSD loopback
        return frame[4:] if len(frame) >= 4 and int.from_bytes(frame[:4], "little") == 2 else None
    if link_type in (12, 101):  # raw IPv4
        return frame if frame and frame[0] >> 4 == 4 else None
    return None


class TcpdumpCapture:
    def __init__(self, device):
        self.process = subprocess.Popen(
            ["/usr/sbin/tcpdump", "-i", device, "-s", "0", "-U", "-w", "-", "ip"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
        )
        self.stdout = self.process.stdout
        try:
            header = _read_exact(self.stdout, 24)
            magic = header[:4]
            if magic == b"\xd4\xc3\xb2\xa1":
                self.endian = "<"
            elif magic == b"\xa1\xb2\xc3\xd4":
                self.endian = ">"
            else:
                raise RuntimeError("tcpdump did not provide a pcap stream. Check capture permission for /dev/bpf devices.")
            self.link_type = struct.unpack(self.endian + "I", header[20:24])[0]
            if self.link_type not in (0, 1, 12, 101):
                raise RuntimeError("The selected interface uses an unsupported capture link type.")
        except EOFError as exc:
            error = self.process.stderr.read().decode("utf-8", errors="replace") if self.process.stderr else ""
            self.close()
            if "Permission" in error or "Operation not permitted" in error:
                raise PermissionError("Packet capture requires BPF device access.") from exc
            raise OSError(error.strip() or "tcpdump stopped before capture began.") from exc
        except Exception:
            self.close()
            raise

    def recvfrom(self, _size):
        while True:
            header = _read_exact(self.stdout, 16)
            length = struct.unpack(self.endian + "I", header[8:12])[0]
            if length > 1024 * 1024:
                raise RuntimeError("Invalid packet length in capture stream.")
            frame = _read_exact(self.stdout, length)
            packet = _ipv4_frame(frame, self.link_type)
            if packet is not None:
                return packet, None

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        if self.stdout:
            self.stdout.close()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        if self.process.stderr:
            self.process.stderr.close()

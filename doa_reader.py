"""
Direction of arrival (DOA) from the reSpeaker XVF3800.

Preferred source ("usb"), the approach from the teammate's usher-audio branch: read
DOA_VALUE = [angle 0-359, speech 0/1] straight over USB with pyusb. The speech flag is
the chip's own voice detector, so we only trust angles taken while someone is talking.
Same control request as Seeed's python_control/xvf_host.py, minus its prints and its
libusb_package dependency. Much cheaper than spawning `sudo xvf_host` several times a second.

Fallback source ("xvf_host"): the vendor binary's AEC_AZIMUTH_VALUES (auto-selected beam).
It has no speech flag, so every reading counts as speech.

USB access without sudo needs a udev rule (see CLAUDE.md, "Setup on the Pi").
The two sources may not share the same zero angle: recalibrate (doa_calibrate.py) after switching.
"""

import re
import subprocess
import time

VID, PID = 0x2886, 0x001A
DOA_RESID, DOA_CMDID = 20, 18      # DOA_VALUE in Seeed's PARAMETERS table: 2 x uint16
STATUS_OK, STATUS_RETRY = 0, 64
USB_TIMEOUT_MS = 500


class UsbDoa:
    name = "usb DOA_VALUE (angle + on-chip speech flag)"

    def __init__(self):
        import usb.core
        import usb.util

        self._util = usb.util
        self.dev = usb.core.find(idVendor=VID, idProduct=PID)
        if self.dev is None:
            raise RuntimeError("XVF3800 not found on USB")
        self.read()  # fail now (e.g. permissions) rather than in the poll loop

    def read(self):
        """Return (raw_angle_deg, speech) or None."""
        u = self._util
        request = u.CTRL_IN | u.CTRL_TYPE_VENDOR | u.CTRL_RECIPIENT_DEVICE
        for _ in range(20):
            r = self.dev.ctrl_transfer(request, 0, 0x80 | DOA_CMDID, DOA_RESID, 5, USB_TIMEOUT_MS)
            if r[0] == STATUS_OK:
                angle = r[1] | (r[2] << 8)
                speech = r[3] | (r[4] << 8)
                return float(angle % 360), bool(speech)
            if r[0] != STATUS_RETRY:
                raise RuntimeError(f"DOA_VALUE returned status {r[0]}")
            time.sleep(0.005)
        return None

    def close(self):
        self._util.dispose_resources(self.dev)


class XvfHostDoa:
    name = "xvf_host AEC_AZIMUTH_VALUES (no speech flag)"

    def __init__(self, path):
        self.path = path

    def read(self):
        try:
            out = subprocess.run(["sudo", self.path, "AEC_AZIMUTH_VALUES"],
                                 capture_output=True, text=True, timeout=2).stdout
        except subprocess.TimeoutExpired:
            return None
        found = re.findall(r"\((\d+\.\d+) deg\)", out)
        return (float(found[-1]), True) if found else None  # last = auto-selected beam

    def close(self):
        pass


def open_doa(source, xvf_host_path):
    """source: 'usb', 'xvf_host', or 'auto' (usb, falling back to xvf_host)."""
    if source in ("usb", "auto"):
        try:
            return UsbDoa()
        except Exception as exc:
            if source == "usb":
                raise
            print(f"WARNING doa: USB DOA unavailable ({exc!r}); falling back to xvf_host")
    return XvfHostDoa(xvf_host_path)

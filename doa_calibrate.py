"""
Find the mic array's left/right flip and rotation offset for server.py.

Run on the Pi (inside the venv, so pyusb is available):
    python doa_calibrate.py

Uses the same direction source as server.py (DOA_SOURCE: usb by default, falling back
to xvf_host), and only counts readings taken while the chip hears speech. It asks you to
talk from the front, right, back and left of the WEARER (in that order), then prints the
DOA_FLIP_LEFT_RIGHT and DOA_OFFSET_DEG values to put in .env. Re-run it after switching
DOA_SOURCE or remounting the mic. Don't run it while server.py is running.
"""

import math
import os
import time

import doa_reader

XVF_HOST = os.environ.get(
    "XVF_HOST",
    os.path.expanduser("~/Documents/reSpeaker_XVF3800_USB_4MIC_ARRAY/host_control/rpi_64bit/xvf_host"),
)
DOA_SOURCE = os.environ.get("DOA_SOURCE", "auto").strip().lower()
SECONDS = 6.0
POSITIONS = (("FRONT", 0), ("RIGHT", 90), ("BACK", 180), ("LEFT", 270))


def circular_mean(angles):
    x = sum(math.cos(math.radians(a)) for a in angles) / len(angles)
    y = sum(math.sin(math.radians(a)) for a in angles) / len(angles)
    return math.degrees(math.atan2(y, x)) % 360, math.hypot(x, y)


def diff(a, b):
    return (a - b + 180) % 360 - 180


reader = doa_reader.open_doa(DOA_SOURCE, XVF_HOST)
print(f"Direction source: {reader.name}")

means = []
for name, _ in POSITIONS:
    input(f"\nStand at the wearer's {name}, about 1-2 m away. Press Enter, then keep talking for {SECONDS:.0f} s...")
    angles = []
    end = time.monotonic() + SECONDS
    while time.monotonic() < end:
        sample = reader.read()
        if sample and sample[1]:  # only readings taken during speech
            angles.append(sample[0])
        time.sleep(0.05)
    if not angles:
        raise SystemExit("No speech readings. Check the mic is plugged in, talk louder/closer, "
                         "and (xvf_host source) that sudo works without a password.")
    mean, strength = circular_mean(angles)
    print(f"  {name:5}: {mean:6.1f} deg  (consistency {strength:.2f}, {len(angles)} readings)")
    if strength < 0.6:
        print("  ^ readings were scattered; consider re-running in a quieter spot")
    means.append(mean)

reader.close()

best = None
for flip in (False, True):
    logical = [(360 - m) % 360 if flip else m for m in means]
    offset, _ = circular_mean([diff(l, t) % 360 for l, (_, t) in zip(logical, POSITIONS)])
    errors = [abs(diff((l - offset) % 360, t)) for l, (_, t) in zip(logical, POSITIONS)]
    if best is None or sum(errors) < sum(best[2]):
        best = (flip, offset, errors)

flip, offset, errors = best
print("\nPut these in .env:")
print(f"  DOA_FLIP_LEFT_RIGHT={1 if flip else 0}")
print(f"  DOA_OFFSET_DEG={offset:.0f}")
print(f"Remaining error per position (front/right/back/left): {', '.join(f'{e:.0f}' for e in errors)} deg")
if max(errors) > 40:
    print("WARNING: large errors. Check you followed front/right/back/left order and re-run.")

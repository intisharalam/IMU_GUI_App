"""
T16 - BLE Packet Decode Round-Trip Test
========================================
Requirement : FR-02
Pass criterion: Decoded quaternion norm in [0.9999, 1.0001] for all 1000 packets;
                zero decode exceptions.

What this tests
---------------
The firmware packs each quaternion sample as four consecutive 32-bit little-endian
IEEE-754 floats in the order w, x, y, z:

    uint8_t buf[16];
    memcpy(buf,      &w, 4);   // bytes  0-3
    memcpy(buf + 4,  &x, 4);   // bytes  4-7
    memcpy(buf + 8,  &y, 4);   // bytes  8-11
    memcpy(buf + 12, &z, 4);   // bytes 12-15
    ble.send(buf, 16);

The companion app decodes with:
    w, x, y, z = struct.unpack_from("<ffff", raw)

This test replicates that exact encode -> decode pipeline using 1000 synthetic unit
quaternions (random orientations, normalised), verifies no information is lost, and
checks that the decoded norm is within floating-point tolerance of 1.0.

No hardware, no BLE, no imports from the companion app required.
"""

import struct
import math
import random
import sys

# ── Configuration ──────────────────────────────────────────────────────────────
NUM_PACKETS      = 1000
NORM_LOWER       = 0.9999
NORM_UPPER       = 1.0001
RANDOM_SEED      = 42          # fixed seed for reproducibility

# ── Helpers ────────────────────────────────────────────────────────────────────

def random_unit_quaternion(rng: random.Random) -> tuple:
    """Return a random unit quaternion (w, x, y, z) using Shoemake's method."""
    u1, u2, u3 = rng.random(), rng.random(), rng.random()
    w = math.sqrt(1 - u1) * math.sin(2 * math.pi * u2)
    x = math.sqrt(1 - u1) * math.cos(2 * math.pi * u2)
    y = math.sqrt(u1)      * math.sin(2 * math.pi * u3)
    z = math.sqrt(u1)      * math.cos(2 * math.pi * u3)
    return (w, x, y, z)


def firmware_pack(w: float, x: float, y: float, z: float) -> bytearray:
    """
    Replicate the firmware memcpy packing:
        memcpy(buf,      &w, 4)
        memcpy(buf + 4,  &x, 4)
        memcpy(buf + 8,  &y, 4)
        memcpy(buf + 12, &z, 4)
    """
    buf = bytearray(16)
    struct.pack_into("<f", buf, 0,  w)
    struct.pack_into("<f", buf, 4,  x)
    struct.pack_into("<f", buf, 8,  y)
    struct.pack_into("<f", buf, 12, z)
    return buf


def companion_decode(raw: bytearray) -> tuple:
    """
    Replicate the companion app decode path:
        w, x, y, z = struct.unpack_from("<ffff", raw)
    """
    w, x, y, z = struct.unpack_from("<ffff", raw)
    return (w, x, y, z)


def quaternion_norm(w, x, y, z) -> float:
    return math.sqrt(w*w + x*x + y*y + z*z)


# ── Test ───────────────────────────────────────────────────────────────────────

def run_test() -> bool:
    print("=" * 60)
    print("T16 - BLE Packet Decode Round-Trip")
    print("=" * 60)
    print(f"  Packets       : {NUM_PACKETS}")
    print(f"  Norm range    : [{NORM_LOWER}, {NORM_UPPER}]")
    print(f"  Random seed   : {RANDOM_SEED}")
    print()

    rng = random.Random(RANDOM_SEED)

    decode_exceptions  = 0
    norm_failures      = 0
    component_failures = 0

    norm_min  =  float("inf")
    norm_max  = -float("inf")

    worst_norm_delta = 0.0
    worst_packet_idx = -1

    for i in range(NUM_PACKETS):
        w_orig, x_orig, y_orig, z_orig = random_unit_quaternion(rng)

        # --- Encode (firmware side) ---
        try:
            raw = firmware_pack(w_orig, x_orig, y_orig, z_orig)
        except Exception as e:
            print(f"  [FAIL] Packet {i}: pack exception: {e}")
            decode_exceptions += 1
            continue

        # Sanity: raw payload must be exactly 16 bytes
        if len(raw) != 16:
            print(f"  [FAIL] Packet {i}: packed length {len(raw)} != 16")
            decode_exceptions += 1
            continue

        # --- Decode (companion app side) ---
        try:
            w_dec, x_dec, y_dec, z_dec = companion_decode(raw)
        except Exception as e:
            print(f"  [FAIL] Packet {i}: decode exception: {e}")
            decode_exceptions += 1
            continue

        # --- Check norm ---
        norm = quaternion_norm(w_dec, x_dec, y_dec, z_dec)
        norm_min = min(norm_min, norm)
        norm_max = max(norm_max, norm)

        delta = abs(norm - 1.0)
        if delta > worst_norm_delta:
            worst_norm_delta = delta
            worst_packet_idx = i

        if not (NORM_LOWER <= norm <= NORM_UPPER):
            norm_failures += 1
            print(f"  [FAIL] Packet {i}: norm {norm:.8f} outside [{NORM_LOWER}, {NORM_UPPER}]")

        # --- Check round-trip fidelity (single-precision float tolerance) ---
        # struct.pack/unpack of a 32-bit float is lossless; components should
        # be bit-identical after round-trip.
        tol = 1e-7
        if (abs(w_dec - w_orig) > tol or abs(x_dec - x_orig) > tol or
                abs(y_dec - y_orig) > tol or abs(z_dec - z_orig) > tol):
            component_failures += 1
            print(f"  [FAIL] Packet {i}: component mismatch")
            print(f"         orig  ({w_orig:.8f}, {x_orig:.8f}, {y_orig:.8f}, {z_orig:.8f})")
            print(f"         decoded ({w_dec:.8f}, {x_dec:.8f}, {y_dec:.8f}, {z_dec:.8f})")

    # ── Summary ────────────────────────────────────────────────────────────────
    total_failures = decode_exceptions + norm_failures + component_failures
    passed = total_failures == 0

    print(f"  Packets tested    : {NUM_PACKETS}")
    print(f"  Decode exceptions : {decode_exceptions}")
    print(f"  Norm failures     : {norm_failures}")
    print(f"  Component failures: {component_failures}")
    print()
    print(f"  Norm min  : {norm_min:.10f}")
    print(f"  Norm max  : {norm_max:.10f}")
    print(f"  Worst |norm - 1|  : {worst_norm_delta:.2e}  (packet {worst_packet_idx})")
    print()

    if passed:
        print("  RESULT: PASS")
    else:
        print(f"  RESULT: FAIL  ({total_failures} failure(s))")

    print("=" * 60)
    return passed


if __name__ == "__main__":
    passed = run_test()
    sys.exit(0 if passed else 1)
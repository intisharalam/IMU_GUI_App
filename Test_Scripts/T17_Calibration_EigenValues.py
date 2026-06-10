"""
T17 - Calibration Eigenvalue Averaging Test
=============================================
Requirement : FR-14
Pass criterion: Angular error between computed average quaternion and ground
                truth <= 0.1 degrees, across all sub-tests.

What this tests
---------------
The calibration module (_average_quaternions in calc/calibration.py) computes a
neutral-pose reference quaternion by averaging N samples collected over a 3-second
window. Because quaternions live on the unit 4-sphere (and q and -q represent the
same rotation), a naive component-wise mean is wrong. The correct approach is the
eigenvalue method:

    1. Flip any sample whose dot product with the first sample is negative
       (brings all samples to the same hemisphere).
    2. Form the 4x4 accumulation matrix  M = Q^T Q  (where Q is the Nx4 sample
       matrix, rows = quaternion samples).
    3. The optimal L2 average is the eigenvector of M corresponding to its
       largest eigenvalue.
    4. Normalise and enforce positive-w convention.

Sub-tests
---------
T17a  Tight cluster (150 samples, tiny noise)
      Quaternions drawn from a narrow von-Mises-Fisher-like cluster around a
      known ground truth. Expected result: very close to ground truth.

T17b  50 sign-flipped duplicates mixed in
      The test specification explicitly requires this: 50 of the 150 samples
      have their sign flipped (i.e. -q instead of q). The algorithm must handle
      this correctly via the hemisphere-alignment step.

T17c  Moderate noise (realistic sensor jitter)
      150 samples with per-component noise matching a typical stationary BNO085
      (SD ~0.002 per component). Error should still be well within 0.1 degrees.

T17d  Multiple known ground truths
      Run T17a/T17b at 5 distinct orientations spanning different parts of the
      rotation space to confirm the algorithm is not tuned to one pose.

T17e  Single-sample edge case
      Feed exactly 1 sample. The function should return it unchanged (or its
      positive-w form) without exceptions.

T17f  Sign-flip edge case: all samples negated
      Every sample is -q_gt. After hemisphere alignment all should flip back,
      and the result should match q_gt.
"""

import sys
import math
import random
import numpy as np

# ── Copy of _average_quaternions from calc/calibration.py ─────────────────────
# Reproduced verbatim so this script runs stand-alone.

def _average_quaternions(quats_wxyz: list) -> tuple:
    if not quats_wxyz:
        return (1.0, 0.0, 0.0, 0.0)
    arr = np.array(quats_wxyz, dtype=float)
    ref = arr[0]
    for i in range(1, len(arr)):
        if np.dot(arr[i], ref) < 0.0:
            arr[i] = -arr[i]
    M = arr.T @ arr
    eigenvalues, eigenvectors = np.linalg.eigh(M)
    avg = eigenvectors[:, np.argmax(eigenvalues)]
    avg /= np.linalg.norm(avg)
    if avg[0] < 0:
        avg = -avg
    return tuple(float(v) for v in avg)


# ── Helpers ────────────────────────────────────────────────────────────────────

PASS_THRESHOLD_DEG = 0.1


def angular_error_deg(q1: tuple, q2: tuple) -> float:
    """
    Geodesic angle between two unit quaternions in degrees.
    Uses  angle = 2 * arccos(|q1 . q2|)  (absolute dot handles double-cover).
    """
    dot = abs(sum(a * b for a, b in zip(q1, q2)))
    dot = min(dot, 1.0)          # clamp for numerical safety
    return math.degrees(2.0 * math.acos(dot))


def normalise(q: tuple) -> tuple:
    n = math.sqrt(sum(v * v for v in q))
    return tuple(v / n for v in q)


def positive_w(q: tuple) -> tuple:
    """Enforce positive-w convention."""
    if q[0] < 0:
        return tuple(-v for v in q)
    return q


def random_unit_quaternion(rng: random.Random) -> tuple:
    """Uniformly random unit quaternion (Shoemake's method)."""
    u1, u2, u3 = rng.random(), rng.random(), rng.random()
    w = math.sqrt(1 - u1) * math.sin(2 * math.pi * u2)
    x = math.sqrt(1 - u1) * math.cos(2 * math.pi * u3)
    y = math.sqrt(u1)      * math.sin(2 * math.pi * u2)
    z = math.sqrt(u1)      * math.cos(2 * math.pi * u3)
    return normalise((w, x, y, z))


def perturb_quaternion(q: tuple, noise_sd: float, rng: random.Random) -> tuple:
    """
    Add Gaussian noise to each component then renormalise.
    This approximates the small-angle jitter seen from a stationary IMU.
    """
    noisy = tuple(v + rng.gauss(0, noise_sd) for v in q)
    return normalise(noisy)


def make_cluster(ground_truth: tuple, n: int, noise_sd: float,
                 n_flipped: int, rng: random.Random) -> list:
    """
    Generate n noisy samples around ground_truth.
    n_flipped of them will have their sign negated before being added to the list.
    """
    samples = []
    flip_indices = set(rng.sample(range(n), k=min(n_flipped, n)))
    for i in range(n):
        q = perturb_quaternion(ground_truth, noise_sd, rng)
        if i in flip_indices:
            q = tuple(-v for v in q)    # intentional sign flip
        samples.append(q)
    return samples


# ── Individual sub-tests ───────────────────────────────────────────────────────

def run_subtest(name: str, ground_truth: tuple, samples: list,
                extra_info: str = "") -> tuple:
    """
    Run _average_quaternions on samples, compare to ground_truth.
    Returns (passed: bool, error_deg: float).
    """
    gt = positive_w(normalise(ground_truth))

    try:
        result = _average_quaternions(samples)
    except Exception as e:
        print(f"  [{name}] EXCEPTION: {e}")
        return False, float("inf")

    # Enforce positive-w for comparison
    result = positive_w(result)

    err = angular_error_deg(result, gt)
    passed = err <= PASS_THRESHOLD_DEG

    status = "PASS" if passed else "FAIL"
    print(f"  [{name}] {status}  |  angular error = {err:.4f} deg  "
          f"(threshold {PASS_THRESHOLD_DEG} deg)"
          + (f"  |  {extra_info}" if extra_info else ""))

    if not passed:
        print(f"    ground truth : ({gt[0]:.6f}, {gt[1]:.6f}, {gt[2]:.6f}, {gt[3]:.6f})")
        print(f"    computed avg : ({result[0]:.6f}, {result[1]:.6f}, "
              f"{result[2]:.6f}, {result[3]:.6f})")

    return passed, err


# ── Main test ──────────────────────────────────────────────────────────────────

def run_test() -> bool:
    print("=" * 65)
    print("T17 - Calibration Eigenvalue Averaging")
    print("=" * 65)
    print(f"  Pass threshold  : {PASS_THRESHOLD_DEG} degrees angular error")
    print()

    rng = random.Random(42)
    results = []

    # ── T17a: Tight cluster, no sign flips ────────────────────────────────────
    print("T17a  Tight cluster (150 samples, tiny noise, 0 sign flips)")
    gt = random_unit_quaternion(rng)
    samples = make_cluster(gt, n=150, noise_sd=0.001, n_flipped=0, rng=rng)
    passed, err = run_subtest("T17a", gt, samples,
                              "150 samples, noise_sd=0.001, 0 flipped")
    results.append(passed)
    print()

    # ── T17b: 50 sign-flipped duplicates (explicit test spec requirement) ─────
    print("T17b  50 sign-flipped duplicates mixed in (test spec requirement)")
    gt = random_unit_quaternion(rng)
    samples = make_cluster(gt, n=150, noise_sd=0.001, n_flipped=50, rng=rng)
    passed, err = run_subtest("T17b", gt, samples,
                              "150 samples, noise_sd=0.001, 50 flipped")
    results.append(passed)
    print()

    # ── T17c: Realistic BNO085 sensor noise ───────────────────────────────────
    print("T17c  Realistic sensor noise (noise_sd=0.002, 0 sign flips)")
    gt = random_unit_quaternion(rng)
    samples = make_cluster(gt, n=150, noise_sd=0.002, n_flipped=0, rng=rng)
    passed, err = run_subtest("T17c", gt, samples,
                              "150 samples, noise_sd=0.002, 0 flipped")
    results.append(passed)
    print()

    # ── T17d: 5 distinct orientations, each with 50 sign flips ───────────────
    print("T17d  5 distinct orientations, each 150 samples, 50 sign flips")
    all_pass = True
    worst_err = 0.0
    for i in range(5):
        gt = random_unit_quaternion(rng)
        samples = make_cluster(gt, n=150, noise_sd=0.001, n_flipped=50, rng=rng)
        p, e = run_subtest(f"T17d-{i+1}", gt, samples,
                           f"orientation {i+1}/5")
        if not p:
            all_pass = False
        worst_err = max(worst_err, e)
    results.append(all_pass)
    print(f"  [T17d] worst error across 5 orientations: {worst_err:.4f} deg")
    print()

    # ── T17e: Single-sample edge case ─────────────────────────────────────────
    print("T17e  Single-sample edge case (n=1)")
    gt = random_unit_quaternion(rng)
    # Positive-w form of gt is what the function should return
    gt_pw = positive_w(gt)
    samples = [gt]
    passed, err = run_subtest("T17e", gt, samples,
                              "1 sample, no noise")
    results.append(passed)
    print()

    # ── T17f: All samples negated ─────────────────────────────────────────────
    print("T17f  All 150 samples negated (-q for every sample)")
    gt = random_unit_quaternion(rng)
    # Generate clean cluster then negate every sample
    base_samples = make_cluster(gt, n=150, noise_sd=0.001, n_flipped=0, rng=rng)
    negated_samples = [tuple(-v for v in s) for s in base_samples]
    passed, err = run_subtest("T17f", gt, negated_samples,
                              "150 samples all negated")
    results.append(passed)
    print()

    # ── Summary ───────────────────────────────────────────────────────────────
    total   = len(results)
    n_pass  = sum(results)
    n_fail  = total - n_pass
    overall = n_fail == 0

    print("-" * 65)
    print(f"  Sub-tests passed  : {n_pass} / {total}")
    if n_fail:
        print(f"  Sub-tests failed  : {n_fail}")
    print()
    print(f"  RESULT: {'PASS' if overall else 'FAIL'}")
    print("=" * 65)

    return overall


if __name__ == "__main__":
    passed = run_test()
    sys.exit(0 if passed else 1)
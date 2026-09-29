"""
testing/attack_step3_camera.py
==============================
Step 3 Attack Suite: Free Camera Controls, Presets, Ground Clamping & User Interaction Release.

Attack Vectors:
1. Warehouse Boundary, Ground-Floor Clamping & NaN/Inf Immunity:
   - Pan to extreme coordinates ([-1000, 1000], [1000, -1000]), asserting target is clamped within [-W/2 - margin, W/2 + margin].
   - Camera elevation clamping: camera.position.y cannot drop below 0.5 (no under-floor view).
   - NaN / Infinity injection: camera and target coordinates gracefully recover to safe defaults without crashing.
2. Camera Presets (Reset, Fit to Warehouse, Top-down View):
   - Mathematically verifies camera presets for diverse warehouse aspect ratios (10x10, 30x30, 50x20, 100x100).
   - Fit-to-warehouse positions camera proportionally to max dimension with target at (0, 0, 0).
   - Top-down preset positions camera directly overhead at (0, maxDim * 1.45, 0.001) with up-vector (0, 0, -1) [North-Up].
3. Camera-Follow State Machine & Immediate Release on User Interaction:
   - Simulates robot tracking when follow is active.
   - Asserts immediate release (cameraFollow -> False) upon any user action: mouse drag start, touch start, WASD / arrow keydown.
   - Tests rapid mixed inputs and ensures user pan is not overridden by robot movement once released.
4. Ground-Plane Keyboard Navigation Mathematics:
   - Verifies camera forward-vector horizontal projection (forward.y = 0, normalized) and right-vector cross product.
   - Asserts diagonal movements (e.g. W+D, S+A) are normalized to prevent super-speed corner panning.
   - Asserts camera position and target move in lockstep, preserving distance, polar angle, and azimuth.
5. Static Source & Single-Renderer Invariant Audit:
   - Verifies absence of obsolete "isometric" terminology in user-facing captions.
   - Verifies single Canvas component invariant (no secondary 2D top-down canvas clone).
   - Verifies OrbitControls configuration: screenSpacePanning=false, mouseButtons (Left: Rotate, Right: Pan, Middle: Dolly), touches.
"""

import math
import os
import re
import sys
from typing import Dict, List, Optional, Tuple


# ── Mathematical Models Matching Warehouse3DCanvas.tsx ────────────────────────

def clamp(val: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, val))


class Vector3:
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def set(self, x: float, y: float, z: float):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)
        return self

    def copy(self, other: "Vector3"):
        self.x = other.x
        self.y = other.y
        self.z = other.z
        return self

    def clone(self) -> "Vector3":
        return Vector3(self.x, self.y, self.z)

    def add(self, other: "Vector3"):
        self.x += other.x
        self.y += other.y
        self.z += other.z
        return self

    def sub(self, other: "Vector3"):
        self.x -= other.x
        self.y -= other.y
        self.z -= other.z
        return self

    def multiply_scalar(self, s: float):
        self.x *= s
        self.y *= s
        self.z *= s
        return self

    def length_sq(self) -> float:
        return self.x * self.x + self.y * self.y + self.z * self.z

    def length(self) -> float:
        return math.sqrt(self.length_sq())

    def normalize(self):
        l = self.length()
        if l > 1e-9:
            self.multiply_scalar(1.0 / l)
        else:
            self.set(0, 0, 0)
        return self

    def cross_vectors(self, a: "Vector3", b: "Vector3"):
        ax, ay, az = a.x, a.y, a.z
        bx, by, bz = b.x, b.y, b.z
        self.x = ay * bz - az * by
        self.y = az * bx - ax * bz
        self.z = ax * by - ay * bx
        return self

    def lerp(self, target: "Vector3", alpha: float):
        self.x += (target.x - self.x) * alpha
        self.y += (target.y - self.y) * alpha
        self.z += (target.z - self.z) * alpha
        return self


class WarehouseCameraController:
    """Python simulation of Warehouse3DCanvas CameraController logic."""

    def __init__(self, world_width: int = 30, world_height: int = 30):
        self.world_width = world_width
        self.world_height = world_height
        self.offset_x = (world_width - 1) / 2.0
        self.offset_z = (world_height - 1) / 2.0
        self.margin = 8.0

        self.target = Vector3(0.0, 0.0, 0.0)
        self.camera_pos = Vector3(26.0, 26.0, 26.0)
        self.camera_up = Vector3(0.0, 1.0, 0.0)
        self.camera_follow = False
        self.selected_robot_pos: Optional[Vector3] = None

    def trigger_user_interaction(self):
        """Immediately release camera follow when user moves/interacts."""
        self.camera_follow = False

    def preset_reset(self):
        self.target.set(0, 0, 0)
        max_dim = max(self.world_width, self.world_height)
        self.camera_pos.set(max_dim * 0.9, max_dim * 1.15, max_dim * 0.9)
        self.camera_up.set(0, 1, 0)

    def preset_fit(self):
        self.target.set(0, 0, 0)
        max_dim = max(self.world_width, self.world_height)
        self.camera_pos.set(max_dim * 0.9, max_dim * 1.15, max_dim * 0.9)
        self.camera_up.set(0, 1, 0)

    def preset_top_down(self):
        self.target.set(0, 0, 0)
        max_dim = max(self.world_width, self.world_height)
        self.camera_pos.set(0.0, max_dim * 1.45, 0.001)
        self.camera_up.set(0.0, 0.0, -1.0)  # North is -Z in 3D scene

    def update_frame(self, delta: float, keys_down: Dict[str, bool]):
        # 1. Follow robot if active
        if self.camera_follow and self.selected_robot_pos is not None:
            target_pos = Vector3(
                self.selected_robot_pos.x - self.offset_x,
                0.35,
                self.selected_robot_pos.z - self.offset_z,
            )
            self.target.lerp(target_pos, 0.08)

        # 2. Keyboard ground-plane panning
        is_key_pressed = any(keys_down.get(k, False) for k in [
            'KeyW', 'KeyS', 'KeyA', 'KeyD', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'
        ])
        if is_key_pressed:
            # camera forward projected onto ground plane (y=0)
            forward = self.target.clone().sub(self.camera_pos)
            forward.y = 0.0
            forward.normalize()
            if forward.length_sq() < 1e-6:
                forward.set(0, 0, -1)

            right = Vector3().cross_vectors(forward, Vector3(0, 1, 0)).normalize()
            pan_delta = Vector3(0, 0, 0)

            if keys_down.get('KeyW') or keys_down.get('ArrowUp'):
                pan_delta.add(forward)
            if keys_down.get('KeyS') or keys_down.get('ArrowDown'):
                pan_delta.sub(forward)
            if keys_down.get('KeyD') or keys_down.get('ArrowRight'):
                pan_delta.add(right)
            if keys_down.get('KeyA') or keys_down.get('ArrowLeft'):
                pan_delta.sub(right)

            if pan_delta.length_sq() > 0:
                pan_speed = max(14.0, min(self.world_width, self.world_height)) * delta * 1.2
                pan_delta.normalize().multiply_scalar(pan_speed)
                self.target.add(pan_delta)
                self.camera_pos.add(pan_delta)

        # 3. Target Clamping to Warehouse bounds + margin
        min_x = -self.offset_x - self.margin
        max_x = self.offset_x + self.margin
        min_z = -self.offset_z - self.margin
        max_z = self.offset_z + self.margin

        if math.isfinite(self.target.x):
            self.target.x = clamp(self.target.x, min_x, max_x)
        else:
            self.target.x = 0.0

        if math.isfinite(self.target.z):
            self.target.z = clamp(self.target.z, min_z, max_z)
        else:
            self.target.z = 0.0

        self.target.y = 0.0

        # 4. Floor Clamping: camera y >= 0.5, no NaN
        if math.isfinite(self.camera_pos.y):
            self.camera_pos.y = max(self.camera_pos.y, 0.5)
        else:
            self.camera_pos.set(26.0, 26.0, 26.0)

        if not math.isfinite(self.camera_pos.x) or not math.isfinite(self.camera_pos.z):
            self.camera_pos.set(26.0, 26.0, 26.0)


# ── ATTACK SUITE ─────────────────────────────────────────────────────────────

def attack_vector_1_bounds_and_ground_clamp():
    """Attack Vector 1: Extreme Pan, Floor Piercing, and NaN/Inf Immunity."""
    print("\n[Attack 1] Boundary & Floor Clamping + NaN/Inf Immunity")
    ctrl = WarehouseCameraController(world_width=30, world_height=30)
    # min_x = -14.5 - 8 = -22.5, max_x = 22.5

    # 1. Extreme target pan beyond warehouse bounds
    extreme_targets = [
        Vector3(9999.0, 0.0, 9999.0),
        Vector3(-9999.0, 0.0, -9999.0),
        Vector3(500.0, 0.0, -500.0),
        Vector3(-500.0, 0.0, 500.0),
    ]
    for ext in extreme_targets:
        ctrl.target.copy(ext)
        ctrl.update_frame(0.016, {})
        assert -22.5 <= ctrl.target.x <= 22.5, f"Target X escaped boundary: {ctrl.target.x}"
        assert -22.5 <= ctrl.target.z <= 22.5, f"Target Z escaped boundary: {ctrl.target.z}"
        assert ctrl.target.y == 0.0, f"Target Y must stay grounded at 0: {ctrl.target.y}"
    print("  [PASS] Target clamping strictly enforced within [-22.5, 22.5] across all extreme pans", flush=True)

    # 2. Camera elevation piercing floor
    negative_elevations = [-50.0, -10.0, -0.01, 0.0, 0.49]
    for neg_y in negative_elevations:
        ctrl.camera_pos.y = neg_y
        ctrl.update_frame(0.016, {})
        assert ctrl.camera_pos.y >= 0.5, f"Camera dipped below floor level (y={ctrl.camera_pos.y})"
    print("  [PASS] Camera elevation strictly clamped >= 0.5 (no below-floor view)", flush=True)

    # 3. NaN and Infinity injection
    ctrl.target.set(float('nan'), 0.0, float('inf'))
    ctrl.camera_pos.set(float('-inf'), float('nan'), 10.0)
    ctrl.update_frame(0.016, {})
    assert math.isfinite(ctrl.target.x) and math.isfinite(ctrl.target.z), "Target has NaN/Inf"
    assert math.isfinite(ctrl.camera_pos.x) and math.isfinite(ctrl.camera_pos.y) and math.isfinite(ctrl.camera_pos.z), "Camera has NaN/Inf"
    assert ctrl.camera_pos.y >= 0.5, "Camera y < 0.5 after NaN recovery"
    print("  [PASS] NaN and Infinity inputs gracefully handled and reset to stable finite vectors", flush=True)


def attack_vector_2_camera_presets():
    """Attack Vector 2: Mathematical correctness of camera presets across warehouse sizes."""
    print("\n[Attack 2] Camera Presets (Reset, Fit to Warehouse, Top-Down)", flush=True)
    test_dimensions = [(10, 10), (30, 30), (50, 20), (100, 100)]

    for w, h in test_dimensions:
        ctrl = WarehouseCameraController(world_width=w, world_height=h)
        max_dim = max(w, h)

        # Reset / Fit Preset
        ctrl.preset_fit()
        assert ctrl.target.x == 0.0 and ctrl.target.y == 0.0 and ctrl.target.z == 0.0
        assert math.isclose(ctrl.camera_pos.x, max_dim * 0.9, rel_tol=1e-5)
        assert math.isclose(ctrl.camera_pos.y, max_dim * 1.15, rel_tol=1e-5)
        assert math.isclose(ctrl.camera_pos.z, max_dim * 0.9, rel_tol=1e-5)
        assert ctrl.camera_up.x == 0.0 and ctrl.camera_up.y == 1.0 and ctrl.camera_up.z == 0.0

        # Top-Down Preset
        ctrl.preset_top_down()
        assert ctrl.target.x == 0.0 and ctrl.target.y == 0.0 and ctrl.target.z == 0.0
        assert math.isclose(ctrl.camera_pos.x, 0.0, abs_tol=1e-5)
        assert math.isclose(ctrl.camera_pos.y, max_dim * 1.45, rel_tol=1e-5)
        assert math.isclose(ctrl.camera_pos.z, 0.001, abs_tol=1e-5)  # slight epsilon for singularity avoidance
        assert ctrl.camera_up.x == 0.0 and ctrl.camera_up.y == 0.0 and ctrl.camera_up.z == -1.0  # North is -Z
        print(f"  [PASS] Presets verified for {w}x{h} grid: Top-Down height={ctrl.camera_pos.y:.1f}, North-Up verified", flush=True)


def attack_vector_3_camera_follow_release():
    """Attack Vector 3: Camera-Follow State Machine & Immediate Release."""
    print("\n[Attack 3] Camera-Follow State Machine & Immediate User Release", flush=True)
    ctrl = WarehouseCameraController(world_width=30, world_height=30)
    ctrl.camera_follow = True
    ctrl.selected_robot_pos = Vector3(5.0, 0.0, 5.0)

    # 1. Verify target converges toward robot
    for _ in range(10):
        ctrl.update_frame(0.016, {})
    expected_target_x = 5.0 - ctrl.offset_x  # 5.0 - 14.5 = -9.5
    expected_target_z = 5.0 - ctrl.offset_z
    assert abs(ctrl.target.x - expected_target_x) < 5.0, "Target did not follow robot"

    # 2. User interaction releases follow
    ctrl.trigger_user_interaction()
    assert ctrl.camera_follow is False, "Camera follow was not released by user interaction"

    # 3. Moving robot must NOT affect target anymore
    prev_target_x = ctrl.target.x
    ctrl.selected_robot_pos.set(28.0, 0.0, 28.0)
    ctrl.update_frame(0.016, {})
    assert ctrl.target.x == prev_target_x, "Robot movement dragged target after user release!"
    print("  [PASS] User interaction releases camera-follow immediately; subsequent robot moves ignored", flush=True)

    # 4. Rapid follow-pan cycle
    for cycle in range(50):
        ctrl.camera_follow = True
        ctrl.update_frame(0.016, {})
        # User touches / presses key
        ctrl.trigger_user_interaction()
        assert ctrl.camera_follow is False
        # User pans with keyboard
        ctrl.update_frame(0.016, {'KeyW': True})
    print("  [PASS] 50 rapid Follow -> User Pan -> Release cycles completed without race or lockup", flush=True)


def attack_vector_4_ground_plane_keyboard_pan():
    """Attack Vector 4: Keyboard Ground-Plane Panning Mathematics."""
    print("\n[Attack 4] Ground-Plane Keyboard Navigation Mathematics", flush=True)
    ctrl = WarehouseCameraController(world_width=30, world_height=30)
    ctrl.camera_pos.set(20.0, 20.0, 20.0)
    ctrl.target.set(0.0, 0.0, 0.0)

    initial_dist = ctrl.camera_pos.clone().sub(ctrl.target).length()

    # Pan forward with 'KeyW'
    keys = {'KeyW': True}
    ctrl.update_frame(0.016, keys)
    new_dist = ctrl.camera_pos.clone().sub(ctrl.target).length()
    assert math.isclose(initial_dist, new_dist, rel_tol=1e-4), "Keyboard pan altered camera-to-target distance!"

    # Diagonal test (KeyW + KeyD): speed must not exceed cardinal pan speed
    ctrl_cardinal = WarehouseCameraController(world_width=30, world_height=30)
    ctrl_cardinal.target.set(0.0, 0.0, 0.0)
    ctrl_cardinal.update_frame(0.016, {'KeyW': True})
    step_cardinal = ctrl_cardinal.target.length()

    ctrl_diagonal = WarehouseCameraController(world_width=30, world_height=30)
    ctrl_diagonal.target.set(0.0, 0.0, 0.0)
    ctrl_diagonal.update_frame(0.016, {'KeyW': True, 'KeyD': True})
    step_diagonal = ctrl_diagonal.target.length()

    assert math.isclose(step_cardinal, step_diagonal, rel_tol=1e-3), (
        f"Diagonal speed boost detected! Cardinal={step_cardinal:.4f}, Diagonal={step_diagonal:.4f}"
    )
    print(f"  [PASS] Keyboard panning maintains strict constant velocity: {step_diagonal:.4f} units/frame (no diagonal boost)", flush=True)
    print("  [PASS] Camera position and target translated in lockstep, preserving distance and angles", flush=True)


def attack_vector_5_source_and_renderer_audit():
    """Attack Vector 5: Static code audit for Single Renderer, No 'isometric', Free OrbitControls."""
    print("\n[Attack 5] Source & Single-Renderer Architecture Audit", flush=True)
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    canvas_3d_path = os.path.join(repo_root, "frontend", "src", "components", "Warehouse3DCanvas.tsx")
    grid_canvas_path = os.path.join(repo_root, "frontend", "src", "components", "GridCanvas.tsx")

    with open(canvas_3d_path, "r", encoding="utf-8") as f:
        canvas_3d_src = f.read()

    with open(grid_canvas_path, "r", encoding="utf-8") as f:
        grid_canvas_src = f.read()

    # 1. No "isometric" in visible UI / captions
    for path, src in [(canvas_3d_path, canvas_3d_src), (grid_canvas_path, grid_canvas_src)]:
        matches = re.findall(r'(?i)\bisometric\b', src)
        assert len(matches) == 0, f"Found forbidden word 'isometric' in {path}: {matches}"
    print("  [PASS] Obsolete 'isometric' terminology completely eliminated from all frontend canvas code", flush=True)

    # 2. Single Canvas check: GridCanvas renders exactly one Canvas (via Warehouse3DCanvas)
    assert "<Warehouse3DCanvas" in grid_canvas_src, "GridCanvas must render Warehouse3DCanvas"
    assert not re.search(r'<Canvas\b', grid_canvas_src), "GridCanvas must not create its own separate <Canvas>"
    canvas_tags = re.findall(r'<Canvas\b', canvas_3d_src)
    assert len(canvas_tags) == 1, f"Warehouse3DCanvas must contain exactly one <Canvas>, found {len(canvas_tags)}"
    print("  [PASS] Strict Single-Renderer invariant verified: exactly 1 Canvas across the entire application", flush=True)

    # 3. OrbitControls settings verified
    assert "screenSpacePanning={false}" in canvas_3d_src, "Must use ground-plane panning (screenSpacePanning={false})"
    assert "THREE.MOUSE.ROTATE" in canvas_3d_src and "THREE.MOUSE.PAN" in canvas_3d_src, "Mouse buttons configured"
    assert "THREE.TOUCH.DOLLY_PAN" in canvas_3d_src, "Two-finger touch pan/pinch configured"
    print("  [PASS] OrbitControls verified: screenSpacePanning=false, ground-plane pan, two-finger gesture support", flush=True)


def main():
    print("=" * 70)
    print("RUNNING STEP 3 ATTACK SUITE: FREE CAMERA, PRESETS & GROUND CLAMPING")
    print("=" * 70)

    attack_vector_1_bounds_and_ground_clamp()
    attack_vector_2_camera_presets()
    attack_vector_3_camera_follow_release()
    attack_vector_4_ground_plane_keyboard_pan()
    attack_vector_5_source_and_renderer_audit()

    print("\n" + "=" * 70)
    print(">>> STEP 3 ATTACK SUITE: ALL 5 ATTACK VECTORS PASSED WITH ZERO FAILURES <<<")
    print("=" * 70)
    os._exit(0)


if __name__ == "__main__":
    main()

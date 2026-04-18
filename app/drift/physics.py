import math
import random
from dataclasses import dataclass

from app.models.drift import PhysicsProfile

# ── Default constants (BALANCED profile) ──
MAX_VELOCITY = 2.0
DAMPING = 0.95
ATTRACTION_STRENGTH = 0.15
REPULSION_STRENGTH = 50.0
REPULSION_RADIUS = 60.0
JITTER = 0.3
CANVAS_BOUNDS = 1000.0


@dataclass(frozen=True)
class PhysicsParams:
    max_velocity: float = MAX_VELOCITY
    damping: float = DAMPING
    attraction_strength: float = ATTRACTION_STRENGTH
    repulsion_strength: float = REPULSION_STRENGTH
    repulsion_radius: float = REPULSION_RADIUS
    jitter: float = JITTER
    canvas_bounds: float = CANVAS_BOUNDS


PROFILE_PARAMS: dict[PhysicsProfile, PhysicsParams] = {
    PhysicsProfile.GENTLE: PhysicsParams(
        max_velocity=1.2,
        damping=0.90,
        attraction_strength=0.10,
        jitter=0.12,
    ),
    PhysicsProfile.BALANCED: PhysicsParams(),
    PhysicsProfile.ENERGETIC: PhysicsParams(
        max_velocity=3.0,
        damping=0.96,
        attraction_strength=0.22,
        jitter=0.5,
    ),
    PhysicsProfile.CHAOTIC: PhysicsParams(
        max_velocity=4.0,
        damping=0.98,
        attraction_strength=0.30,
        jitter=1.2,
    ),
}


def get_params(profile: PhysicsProfile | None = None) -> PhysicsParams:
    return PROFILE_PARAMS.get(profile or PhysicsProfile.BALANCED, PhysicsParams())


def compute_attraction(
    x1: float, y1: float,
    x2: float, y2: float,
    similarity: float,
    params: PhysicsParams | None = None,
) -> tuple[float, float]:
    p = params or PhysicsParams()
    dx = x2 - x1
    dy = y2 - y1
    dist = math.sqrt(dx * dx + dy * dy) + 1e-6
    nx, ny = dx / dist, dy / dist
    force = p.attraction_strength * similarity / max(dist, 1.0)
    return nx * force, ny * force


def compute_repulsion(
    x1: float, y1: float,
    x2: float, y2: float,
    params: PhysicsParams | None = None,
) -> tuple[float, float]:
    p = params or PhysicsParams()
    dx = x1 - x2
    dy = y1 - y2
    dist = math.sqrt(dx * dx + dy * dy) + 1e-6
    if dist > p.repulsion_radius:
        return 0.0, 0.0
    nx, ny = dx / dist, dy / dist
    force = p.repulsion_strength / (dist * dist)
    return nx * force, ny * force


def apply_drift(
    x: float, y: float,
    vx: float, vy: float,
    fx: float, fy: float,
    params: PhysicsParams | None = None,
) -> tuple[float, float, float, float]:
    p = params or PhysicsParams()
    fx += random.uniform(-p.jitter, p.jitter)
    fy += random.uniform(-p.jitter, p.jitter)

    vx = (vx + fx) * p.damping
    vy = (vy + fy) * p.damping

    speed = math.sqrt(vx * vx + vy * vy)
    if speed > p.max_velocity:
        scale = p.max_velocity / speed
        vx *= scale
        vy *= scale

    new_x = x + vx
    new_y = y + vy

    if abs(new_x) > p.canvas_bounds:
        new_x = math.copysign(p.canvas_bounds, new_x)
        vx *= -0.5
    if abs(new_y) > p.canvas_bounds:
        new_y = math.copysign(p.canvas_bounds, new_y)
        vy *= -0.5

    return new_x, new_y, vx, vy

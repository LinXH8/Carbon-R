/* Small shared pieces for the scene modules: materials, placement, a seeded generator,
 * the per-frame motion uniforms, and an even-spaced lookup table along a curve.
 */

import * as THREE from 'three';

export const lambert = (color, extra = {}) =>
  new THREE.MeshLambertMaterial({ color, flatShading: true, ...extra });

export function place(geometry, material, x, y, z, { cast = true, receive = false } = {}) {
  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.set(x, y, z);
  mesh.castShadow = cast;
  mesh.receiveShadow = receive;
  return mesh;
}

export function mulberry32(seed) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* Vertex-shader motion shared by the trees and the water: one time uniform and the wind,
   updated once a frame. Flat shading derives normals from screen-space derivatives, so a
   displaced vertex is lit correctly without recomputing anything. */
export const motion = { uTime: { value: 0 }, uWind: { value: new THREE.Vector2() } };

const _p = new THREE.Vector3();
const _t = new THREE.Vector3();

/**
 * A curve sampled once into `samples` even-spaced points and unit tangents. sample(u)
 * interpolates linearly and writes into the caller's vectors, so the per-frame path
 * allocates nothing. A closed table wraps u; an open one clamps it to its two ends.
 */
export function curveTable(curve, samples, closed = true) {
  const pos = new Float32Array(samples * 3);
  const tan = new Float32Array(samples * 3);
  const step = closed ? samples : samples - 1;
  for (let i = 0; i < samples; i++) {
    curve.getPointAt(i / step, _p);
    curve.getTangentAt(i / step, _t);
    pos[i * 3] = _p.x; pos[i * 3 + 1] = _p.y; pos[i * 3 + 2] = _p.z;
    tan[i * 3] = _t.x; tan[i * 3 + 1] = _t.y; tan[i * 3 + 2] = _t.z;
  }

  function sample(u, outPos, outTan) {
    const f = closed ? (((u % 1) + 1) % 1) * samples : Math.min(Math.max(u, 0), 1) * step;
    const i0 = Math.min(Math.floor(f), samples - 1);
    const k = f - i0;
    const a = i0 * 3;
    const b = (closed ? (i0 + 1) % samples : Math.min(i0 + 1, samples - 1)) * 3;
    outPos.set(
      pos[a] + (pos[b] - pos[a]) * k,
      pos[a + 1] + (pos[b + 1] - pos[a + 1]) * k,
      pos[a + 2] + (pos[b + 2] - pos[a + 2]) * k,
    );
    outTan.set(
      tan[a] + (tan[b] - tan[a]) * k,
      tan[a + 1] + (tan[b + 1] - tan[a + 1]) * k,
      tan[a + 2] + (tan[b + 2] - tan[a + 2]) * k,
    ).normalize();
  }

  return { pos, tan, samples, length: curve.getLength(), sample };
}

export const ease = (t) => t * t * (3 - 2 * t);

/** A lookup table along a polyline: straight between two points, smooth through more. */
export function pathTable(points, samples = 96) {
  const v = points.map((p) => new THREE.Vector3(p[0], p[1] ?? 0, p[2] ?? 0));
  const curve = v.length === 2 ? new THREE.LineCurve3(v[0], v[1]) : new THREE.CatmullRomCurve3(v, false, 'centripetal');
  return curveTable(curve, samples, false);
}

/**
 * Point A to point B and back: ease out of A, travel, ease into B, wait `dwell` seconds
 * while turning round, and return. `travel` is stretched so the peak speed is `speed`.
 * Writes into `out`: u along the path (0 = A, 1 = B), dir (+1 outbound, -1 inbound), turn
 * (0..1 while turning round at either end) and moving.
 */
export function shuttle({ length, speed, dwell, offset = 0 }) {
  const travel = (1.5 * length) / speed;
  const period = 2 * (travel + dwell);
  return (time, out) => {
    const t = (((time + offset) % period) + period) % period;
    out.turn = 0;
    if (t < travel) {
      out.u = ease(t / travel); out.dir = 1; out.moving = true;
    } else if (t < travel + dwell) {
      out.u = 1; out.dir = 1; out.moving = false; out.turn = ease((t - travel) / dwell);
    } else if (t < 2 * travel + dwell) {
      out.u = 1 - ease((t - travel - dwell) / travel); out.dir = -1; out.moving = true;
    } else {
      out.u = 0; out.dir = -1; out.moving = false; out.turn = ease((t - 2 * travel - dwell) / dwell);
    }
    return out;
  };
}

/** Heading for a mover on a shuttle: along the tangent, turning half a circle at each end. */
export const shuttleYaw = (tan, s) => Math.atan2(tan.x * s.dir, tan.z * s.dir) + Math.PI * s.turn;

/**
 * A flat strip `half` either side of a table's centre line, `lift` above its points,
 * shifted `offset` sideways along each sample's own normal (for a rail beside a track).
 */
export function ribbonGeometry(table, half, lift = 0, offset = 0) {
  const { pos, tan, samples } = table;
  const vertices = new Float32Array(samples * 2 * 3);
  const index = [];
  for (let i = 0; i < samples; i++) {
    const j = i * 3;
    const nx = -tan[j + 2];
    const nz = tan[j];
    const cx = pos[j] + nx * offset;
    const cz = pos[j + 2] + nz * offset;
    vertices.set([cx + nx * half, pos[j + 1] + lift, cz + nz * half], i * 6);
    vertices.set([cx - nx * half, pos[j + 1] + lift, cz - nz * half], i * 6 + 3);
    if (i < samples - 1) {
      const a = i * 2;
      index.push(a, a + 2, a + 1, a + 1, a + 2, a + 3);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(vertices, 3));
  geometry.setIndex(index);
  geometry.computeVertexNormals();
  return geometry;
}

/** Shortest distance from (x, z) to any sample of the given tables. */
export function distanceToTables(tables, x, z) {
  let best = Infinity;
  for (const { pos, samples } of tables) {
    for (let i = 0; i < samples; i++) {
      const dx = pos[i * 3] - x;
      const dz = pos[i * 3 + 2] - z;
      best = Math.min(best, dx * dx + dz * dz);
    }
  }
  return Math.sqrt(best);
}

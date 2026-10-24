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
 * allocates nothing.
 */
export function curveTable(curve, samples) {
  const pos = new Float32Array(samples * 3);
  const tan = new Float32Array(samples * 3);
  for (let i = 0; i < samples; i++) {
    curve.getPointAt(i / samples, _p);
    curve.getTangentAt(i / samples, _t);
    pos[i * 3] = _p.x; pos[i * 3 + 1] = _p.y; pos[i * 3 + 2] = _p.z;
    tan[i * 3] = _t.x; tan[i * 3 + 1] = _t.y; tan[i * 3 + 2] = _t.z;
  }

  function sample(u, outPos, outTan) {
    const f = (((u % 1) + 1) % 1) * samples;
    const i0 = Math.floor(f);
    const k = f - i0;
    const a = i0 * 3;
    const b = ((i0 + 1) % samples) * 3;
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

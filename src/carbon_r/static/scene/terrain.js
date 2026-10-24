/* The island's shape: an ellipse with a wandering coastline. One function, written once in
 * JS and once in GLSL from the same constants, so the land mesh, the water's shoreline
 * taper and the placement of every object agree about where the coast is.
 */

import * as THREE from 'three';
import { place } from './kit.js';

export const COAST = { rx: 18, rz: 12 };
// [amplitude, frequency, phase] terms added to the unit ellipse radius.
const TERMS = [[0.06, 3, 0.5], [0.04, 5, 1.7], [0.025, 8, 4.1]];
const f = (n) => n.toFixed(4);

/** Coast radius factor at angle t (1 = the plain ellipse). */
export function coastFactor(t) {
  let r = 1;
  for (let i = 0; i < TERMS.length; i++) r += TERMS[i][0] * Math.sin(TERMS[i][1] * t + TERMS[i][2]);
  return r;
}

/** Approximate distance to the coast: negative inland, positive at sea. */
export function seaDistance(x, z) {
  const t = Math.atan2(z, x);
  return (Math.hypot(x / COAST.rx, z / COAST.rz) - coastFactor(t)) * COAST.rz;
}

export function coastPoint(t, out = new THREE.Vector3()) {
  const r = coastFactor(t);
  return out.set(Math.cos(t) * COAST.rx * r, 0, Math.sin(t) * COAST.rz * r);
}

/** The same distance in GLSL, for the water's vertex shader. */
export const SEA_DISTANCE_GLSL = /* glsl */ `
float seaDistance(vec2 p) {
  float t = atan(p.y, p.x);
  float r = 1.0${TERMS.map(([a, k, ph]) => ` + ${f(a)} * sin(${f(k)} * t + ${f(ph)})`).join('')};
  return (length(p / vec2(${f(COAST.rx)}, ${f(COAST.rz)})) - r) * ${f(COAST.rz)};
}`;

/** The land: the coastline extruded down 1.2 units, grass on top and earth on the sides. */
export function buildLand(grass, earth, samples = 180) {
  const points = [];
  const p = new THREE.Vector3();
  for (let i = 0; i < samples; i++) {
    coastPoint((i / samples) * Math.PI * 2, p);
    points.push(new THREE.Vector2(p.x, -p.z)); // shape y becomes world -z after the rotation
  }
  const geometry = new THREE.ExtrudeGeometry(new THREE.Shape(points), { depth: 1.2, bevelEnabled: false, curveSegments: 1 })
    .rotateX(-Math.PI / 2)
    .translate(0, -1.2, 0);
  return place(geometry, [grass, earth], 0, 0, 0, { cast: false, receive: true });
}

/* The sea around the island: a vertex-displaced ring, and a JS copy of the same height
 * function so ships float on the surface the viewer actually sees.
 *
 * Four directional waves of different length, direction and speed, summed (Gerstner
 * style): each lifts the surface and pushes it a little sideways toward its crest. The
 * sideways chop is held at CHOP (15%) of the vertical amplitude, so the height at a world
 * point stays close to the plain sum the JS sampler computes. The swell grows with the
 * wind. Crests are lit a little lighter, troughs darker, and a pale band runs along the
 * shore. Everything tapers to still water at the outer wall and at the island.
 */

import * as THREE from 'three';
import { lambert, motion, place } from './kit.js';

export const WATER = { inner: 13.4, outer: 20.5, level: -0.45 };

// [direction (rad), wavelength, amplitude, angular speed (rad/s)]
const WAVES = [
  [0.4, 6.5, 0.07, 0.9],
  [1.9, 4.4, 0.05, 1.15],
  [-0.8, 3.4, 0.035, 1.4],
  [2.8, 2.9, 0.025, 1.7],
];
const CHOP = 0.15;
const AMPLITUDE_SUM = WAVES.reduce((n, w) => n + w[2], 0);
const SHORE = 13.7; // where the island's side meets the water
const f = (n) => n.toFixed(4);

const waveTerms = WAVES.map(([dir, length, amp, speed]) => /* glsl */ `
  {
    vec2 d = vec2(${f(Math.cos(dir))}, ${f(Math.sin(dir))});
    float ph = ${f((2 * Math.PI) / length)} * dot(d, position.xz) - ${f(speed)} * uTime;
    h += ${f(amp)} * sin(ph);
    shift += d * (${f(CHOP * amp)} * cos(ph));
  }`).join('');

const VERTEX = /* glsl */ `#include <begin_vertex>
  float r = length(position.xz);
  float taper = smoothstep(${f(WATER.inner)}, ${f(WATER.inner + 1.2)}, r)
    * (1.0 - smoothstep(${f(WATER.outer - 1.0)}, ${f(WATER.outer)}, r));
  float swell = (0.6 + 0.8 * length(uWind)) * taper;
  float h = 0.0;
  vec2 shift = vec2(0.0);
  ${waveTerms}
  transformed.y += h * swell;
  transformed.xz += shift * swell;
  vWave = clamp(0.5 + h / ${f(2 * AMPLITUDE_SUM)}, 0.0, 1.0);
  vRadius = r;`;

const FRAGMENT = /* glsl */ `#include <color_fragment>
  diffuseColor.rgb *= 0.82 + 0.36 * vWave;
  float foam = 1.0 - smoothstep(${f(SHORE + 0.15)}, ${f(SHORE + 0.9)}, vRadius);
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.55, 0.64, 0.67), foam * 0.35);`;

const smooth = (a, b, x) => {
  const t = Math.min(Math.max((x - a) / (b - a), 0), 1);
  return t * t * (3 - 2 * t);
};

/** Surface height at (x, z): the vertex shader's sum, without its small sideways chop. */
export function waveHeight(x, z, time, windStrength) {
  const r = Math.hypot(x, z);
  const taper = smooth(WATER.inner, WATER.inner + 1.2, r) * (1 - smooth(WATER.outer - 1, WATER.outer, r));
  let h = 0;
  for (let i = 0; i < WAVES.length; i++) {
    const w = WAVES[i];
    const ph = ((2 * Math.PI) / w[1]) * (Math.cos(w[0]) * x + Math.sin(w[0]) * z) - w[3] * time;
    h += w[2] * Math.sin(ph);
  }
  return WATER.level + h * (0.6 + 0.8 * windStrength) * taper;
}

export function buildWater(colour, sideColour) {
  const material = lambert(colour);
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, motion);
    shader.vertexShader = 'uniform float uTime;\nuniform vec2 uWind;\nvarying float vWave;\nvarying float vRadius;\n' +
      shader.vertexShader.replace('#include <begin_vertex>', VERTEX);
    shader.fragmentShader = 'varying float vWave;\nvarying float vRadius;\n' +
      shader.fragmentShader.replace('#include <color_fragment>', FRAGMENT);
  };
  const surface = place(
    new THREE.RingGeometry(WATER.inner, WATER.outer, 96, 18).rotateX(-Math.PI / 2),
    material, 0, WATER.level, 0, { cast: false, receive: true },
  );
  const side = place(
    new THREE.CylinderGeometry(WATER.outer, WATER.outer, 0.9, 96, 1, true),
    lambert(sideColour), 0, WATER.level - 0.45, 0, { cast: false },
  );
  return [surface, side];
}

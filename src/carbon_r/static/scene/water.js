/* The sea: one large plane reaching past the edges of the frame, so there is no rim to see;
 * the far side fades into the page through the scene's fog. A JS copy of the height
 * function lets boats float on the surface the viewer actually sees.
 *
 * Four directional waves of different length, direction and speed, summed (Gerstner
 * style): each lifts the surface and pushes it a little sideways toward its crest. The
 * sideways chop is held at CHOP (15%) of the vertical amplitude, so the height at a world
 * point stays close to the plain sum the JS sampler computes. The swell grows with the
 * wind and dies away at the coast, where a pale band marks the shore. Crests are lit a
 * little lighter, troughs darker.
 */

import * as THREE from 'three';
import { lambert, motion, place } from './kit.js';
import { SEA_DISTANCE_GLSL, seaDistance } from './terrain.js';

export const WATER = { level: -0.45, size: 160 };

// [direction (rad), wavelength, amplitude, angular speed (rad/s)]
const WAVES = [
  [0.4, 6.5, 0.07, 0.9],
  [1.9, 4.4, 0.05, 1.15],
  [-0.8, 3.4, 0.035, 1.4],
  [2.8, 2.9, 0.025, 1.7],
];
const CHOP = 0.15;
const AMPLITUDE_SUM = WAVES.reduce((n, w) => n + w[2], 0);
const f = (n) => n.toFixed(4);

const waveTerms = WAVES.map(([dir, length, amp, speed]) => /* glsl */ `
  {
    vec2 d = vec2(${f(Math.cos(dir))}, ${f(Math.sin(dir))});
    float ph = ${f((2 * Math.PI) / length)} * dot(d, position.xz) - ${f(speed)} * uTime;
    h += ${f(amp)} * sin(ph);
    shift += d * (${f(CHOP * amp)} * cos(ph));
  }`).join('');

const VERTEX = /* glsl */ `#include <begin_vertex>
  float shore = seaDistance(position.xz);
  float swell = (0.6 + 0.8 * length(uWind)) * smoothstep(0.0, 1.4, shore);
  float h = 0.0;
  vec2 shift = vec2(0.0);
  ${waveTerms}
  transformed.y += h * swell;
  transformed.xz += shift * swell;
  vWave = clamp(0.5 + h / ${f(2 * AMPLITUDE_SUM)}, 0.0, 1.0);
  vShore = shore;`;

const FRAGMENT = /* glsl */ `#include <color_fragment>
  diffuseColor.rgb *= 0.82 + 0.36 * vWave;
  float foam = 1.0 - smoothstep(0.1, 0.9, vShore);
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.55, 0.64, 0.67), foam * 0.35);`;

const smooth = (a, b, x) => {
  const t = Math.min(Math.max((x - a) / (b - a), 0), 1);
  return t * t * (3 - 2 * t);
};

/** Surface height at (x, z): the vertex shader's sum, without its small sideways chop. */
export function waveHeight(x, z, time, windStrength) {
  let h = 0;
  for (let i = 0; i < WAVES.length; i++) {
    const w = WAVES[i];
    const ph = ((2 * Math.PI) / w[1]) * (Math.cos(w[0]) * x + Math.sin(w[0]) * z) - w[3] * time;
    h += w[2] * Math.sin(ph);
  }
  return WATER.level + h * (0.6 + 0.8 * windStrength) * smooth(0, 1.4, seaDistance(x, z));
}

export function buildWater(colour) {
  const material = lambert(colour);
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, motion);
    shader.vertexShader = `uniform float uTime;\nuniform vec2 uWind;\nvarying float vWave;\nvarying float vShore;\n${SEA_DISTANCE_GLSL}\n` +
      shader.vertexShader.replace('#include <begin_vertex>', VERTEX);
    shader.fragmentShader = 'varying float vWave;\nvarying float vShore;\n' +
      shader.fragmentShader.replace('#include <color_fragment>', FRAGMENT);
  };
  const geometry = new THREE.PlaneGeometry(WATER.size, WATER.size, 140, 140).rotateX(-Math.PI / 2);
  return place(geometry, material, 0, WATER.level, 0, { cast: false, receive: true });
}

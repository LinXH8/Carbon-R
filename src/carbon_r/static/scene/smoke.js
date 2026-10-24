/* Smoke, steam and exhaust: one fixed pool of soft points, drawn in a single call.
 *
 * Everything is allocated in the constructor. Each emitter owns a fixed slice of the pool
 * and recycles it as a ring buffer, emitting at count / mean life per second, so a slice
 * is exactly full at steady state and never asks for more. The per-frame work is typed
 * array writes and one upload; nothing in update() allocates.
 *
 * The disc is drawn in the fragment shader rather than from a texture. Tints are stored
 * as raw sRGB and written straight out, because a ShaderMaterial does no colour-space
 * conversion of its own.
 */

import * as THREE from 'three';

const VERTEX = /* glsl */ `
attribute float aSize;
attribute float aAlpha;
attribute vec3 aTint;
uniform float uScale;
varying float vAlpha;
varying vec3 vTint;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = aSize * uScale / -mv.z;
  vAlpha = aAlpha;
  vTint = aTint;
}`;

const FRAGMENT = /* glsl */ `
varying float vAlpha;
varying vec3 vTint;
void main() {
  float d = length(gl_PointCoord - 0.5);
  float a = (1.0 - smoothstep(0.04, 0.5, d)) * vAlpha;
  if (a < 0.004) discard;
  gl_FragColor = vec4(vTint, a);
}`;

const lerp = (a, b, t) => a + (b - a) * t;

export class Smoke {
  constructor(capacity) {
    this.capacity = capacity;
    this.used = 0;
    this.emitters = [];

    this.position = new Float32Array(capacity * 3);
    this.velocity = new Float32Array(capacity * 3);
    this.tint = new Float32Array(capacity * 3);
    this.size = new Float32Array(capacity);
    this.alpha = new Float32Array(capacity);
    this.age = new Float32Array(capacity);
    this.life = new Float32Array(capacity); // 0 = dead
    this.size0 = new Float32Array(capacity);
    this.size1 = new Float32Array(capacity);
    this.alphaMax = new Float32Array(capacity);
    this.rise = new Float32Array(capacity);
    this.lift = new Float32Array(capacity);
    this.drift = new Float32Array(capacity);

    const geometry = new THREE.BufferGeometry();
    this.attrs = {
      position: new THREE.BufferAttribute(this.position, 3).setUsage(THREE.DynamicDrawUsage),
      aSize: new THREE.BufferAttribute(this.size, 1).setUsage(THREE.DynamicDrawUsage),
      aAlpha: new THREE.BufferAttribute(this.alpha, 1).setUsage(THREE.DynamicDrawUsage),
      aTint: new THREE.BufferAttribute(this.tint, 3),
    };
    for (const [name, attr] of Object.entries(this.attrs)) geometry.setAttribute(name, attr);

    this.material = new THREE.ShaderMaterial({
      uniforms: { uScale: { value: 1 } },
      vertexShader: VERTEX,
      fragmentShader: FRAGMENT,
      transparent: true,
      depthWrite: false,
    });
    this.points = new THREE.Points(geometry, this.material);
    this.points.frustumCulled = false;
    this.points.renderOrder = 1;
  }

  /** World units to pixels at unit depth, for the current drawing buffer and fov. */
  setViewport(bufferHeight, fovDegrees) {
    this.material.uniforms.uScale.value =
      bufferHeight / (2 * Math.tan(THREE.MathUtils.degToRad(fovDegrees) / 2));
  }

  /**
   * Reserve a slice of the pool. `origin` is a Vector3 the caller may move every frame.
   * life, size: [min, max] seconds and [start, end] world-unit diameters. `lift` is the
   * vertical speed left once buoyancy has decayed: 0 keeps a wake on the water. Set
   * `enabled` on the returned emitter to pause spawning; live particles still finish.
   */
  addEmitter({ count, origin, life, size, rise, spread, alpha, drift, tints, lift = 0.05 }) {
    const start = this.used;
    count = Math.min(count, this.capacity - this.used);
    this.used += count;
    for (let k = 0; k < count; k++) {
      const hex = tints[k % tints.length];
      const i = (start + k) * 3;
      this.tint[i] = ((hex >> 16) & 255) / 255;
      this.tint[i + 1] = ((hex >> 8) & 255) / 255;
      this.tint[i + 2] = (hex & 255) / 255;
    }
    this.attrs.aTint.needsUpdate = true;
    const emitter = {
      start, count, cursor: 0, carry: 0, origin, life, size, rise, spread, alpha, drift, lift,
      enabled: true,
      rate: count / ((life[0] + life[1]) / 2),
    };
    this.emitters.push(emitter);
    return emitter;
  }

  spawn(e) {
    const i = e.start + e.cursor;
    e.cursor = (e.cursor + 1) % e.count;
    const p = i * 3;
    const angle = Math.random() * Math.PI * 2;
    const r = Math.sqrt(Math.random()) * e.spread;
    this.position[p] = e.origin.x + Math.cos(angle) * r;
    this.position[p + 1] = e.origin.y;
    this.position[p + 2] = e.origin.z + Math.sin(angle) * r;
    this.velocity[p] = (Math.random() - 0.5) * 0.12;
    this.velocity[p + 2] = (Math.random() - 0.5) * 0.12;
    this.age[i] = 0;
    this.life[i] = lerp(e.life[0], e.life[1], Math.random());
    const jitter = 0.85 + Math.random() * 0.3;
    this.size0[i] = e.size[0] * jitter;
    this.size1[i] = e.size[1] * jitter;
    this.alphaMax[i] = e.alpha * (0.8 + Math.random() * 0.2);
    this.rise[i] = e.rise * (0.85 + Math.random() * 0.3);
    this.drift[i] = e.drift;
    this.lift[i] = e.lift;
  }

  /** Advance every live particle. `wind` is {x, z} in world units per second. */
  update(dt, wind) {
    for (let k = 0; k < this.emitters.length; k++) {
      const e = this.emitters[k];
      if (!e.enabled) {
        e.carry = 0;
        continue;
      }
      e.carry += e.rate * dt;
      while (e.carry >= 1) {
        e.carry -= 1;
        this.spawn(e);
      }
    }

    const { position, velocity, size, alpha, age, life } = this;
    for (let i = 0; i < this.used; i++) {
      if (life[i] === 0) continue;
      age[i] += dt;
      const t = age[i] / life[i];
      if (t >= 1) {
        life[i] = 0;
        size[i] = 0;
        alpha[i] = 0;
        continue;
      }
      const p = i * 3;
      // Buoyant at the mouth, then carried: lift decays, the wind's share grows with age
      // and a little with height, and the particle's own spread is damped away.
      const lift = this.rise[i] * Math.exp(-age[i] * 0.35) + this.lift[i];
      const carried = Math.min(age[i] * 0.6, 1) * this.drift[i] * (0.6 + position[p + 1] * 0.08);
      const damp = 1 - Math.min(dt * 0.6, 1);
      velocity[p] *= damp;
      velocity[p + 2] *= damp;
      position[p] += (velocity[p] + wind.x * carried) * dt;
      position[p + 1] += lift * dt;
      position[p + 2] += (velocity[p + 2] + wind.z * carried) * dt;

      const grow = 1 - (1 - t) * (1 - t);
      size[i] = lerp(this.size0[i], this.size1[i], grow);
      alpha[i] = this.alphaMax[i] * Math.min(t / 0.08, 1) * Math.pow(1 - t, 1.25);
    }

    this.attrs.position.needsUpdate = true;
    this.attrs.aSize.needsUpdate = true;
    this.attrs.aAlpha.needsUpdate = true;
  }
}

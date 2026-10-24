/* The diorama: an island with an irregular coast, in a sea that runs past the edges of
 * the frame. Industry, harbour, airfield and farm each live in their own module and are
 * joined by roads and a rail line; this file adds the light, the land, the sea, the trees
 * and the clouds, and merges what never moves into a few draw calls.
 *
 * Procedural geometry only - no models, no textures, no signage, no brands. Layout is fixed
 * by hand and by a seeded generator, so every visit sees the same world. update() runs
 * every frame and allocates nothing.
 */

import * as THREE from 'three';
import { distanceToTables, lambert, motion, mulberry32 } from './kit.js';
import { mergeStatic } from './merge.js';
import { buildLand, COAST, seaDistance } from './terrain.js';
import { buildWater } from './water.js';
import { buildRoads } from './roads.js';
import { buildIndustry } from './industry.js';
import { buildHarbour } from './harbour.js';
import { buildAirfield } from './airfield.js';
import { buildFarm } from './farm.js';

const COLOR = {
  grass: 0x5f7a52,
  earth: 0x4b3d30,
  water: 0x3a5868,
  trunk: 0x5b4532,
  leaves: [0x4e6b43, 0x5d7a4a, 0x44613d, 0x667f4c],
  cloud: 0xd6dade,
};

const SUN_PERIOD = 120; // s
const CLOUD_BOUNDS = { x: 34, z: 26 };

const _m = new THREE.Matrix4();
const _p = new THREE.Vector3();
const _s = new THREE.Vector3();
const _q = new THREE.Quaternion();

// Trees bend from the ground up, the tip most, each on its own phase. Local space is world
// space here: tree instances are translated and scaled, never rotated.
const SWAY = /* glsl */ `#include <begin_vertex>
  float bend = max(position.y, 0.0);
  transformed.xz += uWind * bend * bend * 0.032 * (0.6 + 0.4 * sin(uTime * 1.7 + aPhase));`;

function sway(material) {
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, motion);
    shader.vertexShader = 'uniform float uTime;\nuniform vec2 uWind;\nattribute float aPhase;\n' +
      shader.vertexShader.replace('#include <begin_vertex>', SWAY);
  };
  return material;
}

export function buildWorld({ smoke, small, shadows }) {
  const root = new THREE.Group();
  const statics = new THREE.Group(); // harbour, airfield and farm parts that never move
  root.add(statics);
  const rand = mulberry32(20261025);
  const hoverTargets = [];
  const tag = (object, kind) => {
    object.traverse((o) => { o.userData.kind = kind; });
    hoverTargets.push(object);
  };

  // Light. The sun swings from morning to afternoon and back, never setting, so the
  // scene's contrast never collapses; its colour warms as it drops.
  const hemi = new THREE.HemisphereLight(0xdfe7f0, 0x3a3226, 1.25);
  const sun = new THREE.DirectionalLight(0xfff1e0, 2.4);
  const sunLow = new THREE.Color(0xffc995);
  const sunHigh = new THREE.Color(0xfff4e6);
  sun.castShadow = shadows;
  if (shadows) {
    // Fitted to the island and the quay, which reaches out to x = -19.6.
    sun.shadow.mapSize.set(2048, 2048);
    Object.assign(sun.shadow.camera, { left: -22, right: 22, top: 22, bottom: -22, near: 5, far: 80 });
    sun.shadow.bias = -0.0005;
    sun.shadow.normalBias = 0.03;
  }
  root.add(hemi, sun, sun.target);

  // Land and sea.
  root.add(buildLand(lambert(COLOR.grass), lambert(COLOR.earth)), buildWater(COLOR.water));

  // The working parts.
  const share = { smoke, small, statics, root, tag };
  const roads = buildRoads(share);
  const industry = buildIndustry(share);
  const harbour = buildHarbour(share);
  const airfield = buildAirfield(share);
  const farm = buildFarm(share);

  // Trees fill what is left: inland, clear of roads, rail, buildings, fields and runway.
  const keepOut = [...industry.keepOut, ...harbour.keepOut, ...airfield.keepOut, ...farm.keepOut];
  const rects = [airfield.keepOutRect, ...farm.keepOutRects];
  const lines = [...roads.tables, ...industry.tables];
  const treeCount = small ? 45 : 90;
  const trees = [];
  for (let attempts = 0; trees.length < treeCount && attempts < 12000; attempts++) {
    const x = (rand() * 2 - 1) * COAST.rx * 1.1;
    const z = (rand() * 2 - 1) * COAST.rz * 1.1;
    if (seaDistance(x, z) > -0.9) continue;
    if (rects.some(([x0, x1, z0, z1]) => x > x0 && x < x1 && z > z0 && z < z1)) continue;
    if (keepOut.some(([kx, kz, kr]) => Math.hypot(x - kx, z - kz) < kr)) continue;
    if (trees.some((t) => Math.hypot(x - t.x, z - t.z) < 0.85)) continue;
    if (distanceToTables(lines, x, z) < 0.9) continue;
    trees.push({ x, z, s: 0.7 + rand() * 0.6, phase: rand() * Math.PI * 2 });
  }
  const trunkGeo = new THREE.CylinderGeometry(0.08, 0.11, 0.5, 5).translate(0, 0.25, 0);
  const coneGeo = new THREE.ConeGeometry(0.45, 1.2, 6).translate(0, 1.1, 0);
  coneGeo.setAttribute('aPhase', new THREE.InstancedBufferAttribute(new Float32Array(trees.map((t) => t.phase)), 1));
  const trunks = new THREE.InstancedMesh(trunkGeo, lambert(COLOR.trunk), trees.length);
  const cones = new THREE.InstancedMesh(coneGeo, sway(lambert(0xffffff)), trees.length);
  // The shadow sways with the tree.
  cones.customDepthMaterial = sway(new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking }));
  trees.forEach((t, i) => {
    _m.compose(_p.set(t.x, 0, t.z), _q.identity(), _s.setScalar(t.s));
    trunks.setMatrixAt(i, _m);
    cones.setMatrixAt(i, _m);
    cones.setColorAt(i, new THREE.Color(COLOR.leaves[i % COLOR.leaves.length]));
  });
  for (const mesh of [trunks, cones]) {
    mesh.castShadow = true;
    mesh.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 1, 0), COAST.rx + 2);
  }
  root.add(trunks, cones);

  // Clouds: a few blobs each, drifting with the wind across a box wider than the frame and
  // shrinking away near its edges before they wrap, so nothing pops.
  const cloudCount = small ? 2 : 4;
  const blobs = [[-0.9, 0, 0, 0.75], [0, 0.25, 0, 1], [0.9, 0.05, 0.1, 0.8], [0.25, 0.1, -0.55, 0.7]];
  const sky = mulberry32(11); // clouds start spread across the box, none parked over the plant
  const clouds = Array.from({ length: cloudCount }, (_, i) => ({
    x: ((i + sky()) / cloudCount * 2 - 1) * CLOUD_BOUNDS.x, z: (sky() * 2 - 1) * CLOUD_BOUNDS.z,
    y: 13 + sky() * 2, s: 0.45 + sky() * 0.25, phase: sky() * 6.28,
  }));
  const cloudMesh = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1, 0), lambert(COLOR.cloud), cloudCount * blobs.length);
  cloudMesh.castShadow = shadows;
  cloudMesh.frustumCulled = false;
  cloudMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  root.add(cloudMesh);

  mergeStatic(statics);

  function update(time, dt, wind) {
    // Light: one slow arc, a full swing every SUN_PERIOD seconds.
    const day = Math.sin((time / SUN_PERIOD) * Math.PI * 2);
    const height = 0.5 + 0.5 * Math.cos((time / SUN_PERIOD) * Math.PI * 4); // high at mid-swing
    const elevation = 0.5 + 0.6 * height;
    const azimuth = 0.9 + 0.8 * day;
    sun.position.set(
      35 * Math.cos(elevation) * Math.cos(azimuth),
      35 * Math.sin(elevation),
      35 * Math.cos(elevation) * Math.sin(azimuth),
    );
    sun.color.lerpColors(sunLow, sunHigh, height);
    sun.intensity = 2.1 + 0.5 * height;
    hemi.intensity = 1.15 + 0.2 * height;

    // Trees and water move in their vertex shaders; they only need the clock and the wind.
    motion.uTime.value = time;
    motion.uWind.value.set(wind.x, wind.z);

    roads.update(time, dt, wind);
    industry.update(time, dt, wind);
    harbour.update(time, dt, wind);
    airfield.update(time, dt, wind);
    farm.update(time, dt, wind);

    let k = 0;
    for (let i = 0; i < cloudCount; i++) {
      const c = clouds[i];
      c.x += wind.x * 0.5 * dt;
      c.z += wind.z * 0.5 * dt;
      if (c.x > CLOUD_BOUNDS.x) c.x -= 2 * CLOUD_BOUNDS.x;
      if (c.x < -CLOUD_BOUNDS.x) c.x += 2 * CLOUD_BOUNDS.x;
      if (c.z > CLOUD_BOUNDS.z) c.z -= 2 * CLOUD_BOUNDS.z;
      if (c.z < -CLOUD_BOUNDS.z) c.z += 2 * CLOUD_BOUNDS.z;
      const edge = Math.min(CLOUD_BOUNDS.x - Math.abs(c.x), CLOUD_BOUNDS.z - Math.abs(c.z));
      const fade = THREE.MathUtils.smoothstep(edge, 0, 4) * c.s;
      const bob = Math.sin(time * 0.3 + c.phase) * 0.08;
      for (let b = 0; b < blobs.length; b++) {
        const blob = blobs[b];
        const bs = blob[3] * fade;
        _p.set(c.x + blob[0] * c.s, c.y + blob[1] + bob, c.z + blob[2] * c.s);
        _s.set(bs, bs * 0.7, bs);
        _q.identity();
        _m.compose(_p, _q, _s);
        cloudMesh.setMatrixAt(k++, _m);
      }
    }
    cloudMesh.instanceMatrix.needsUpdate = true;
  }

  // For ?stats measurements only.
  const debug = { plane: airfield.plane, ship: harbour.ship };
  return { root, update, hoverTargets, debug };
}

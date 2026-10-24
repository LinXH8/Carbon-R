/* The diorama: an island with a power plant, a factory, a road loop with traffic, trees
 * and clouds. Procedural geometry only - no models, no textures, no signage, no brands.
 *
 * Layout is fixed by hand and by a seeded generator, so every visit sees the same world.
 * update() runs every frame and allocates nothing: it writes into scratch objects declared
 * once at module level.
 */

import * as THREE from 'three';

const COLOR = {
  grass: 0x5f7a52,
  earth: 0x4b3d30,
  water: 0x3a5868,
  waterSide: 0x223540,
  road: 0x3b3e41,
  concrete: 0xb7b1a5,
  roof: 0x7b7f84,
  tower: 0xd0cabe,
  stack: 0xc4bfb5,
  band: 0x9c4a3c,
  brick: 0x9a6a54,
  metal: 0x6d7175,
  trunk: 0x5b4532,
  leaves: [0x4e6b43, 0x5d7a4a, 0x44613d, 0x667f4c],
  cloud: 0xd6dade,
  cabin: 0x2a3034,
  cars: [0xb4574a, 0xd6c79f, 0x5a7a97, 0xdedede, 0x7c8b6a],
  van: 0xeeede8,
};

// Muted greys, never black: the plant's flue gas, the tower's steam, tailpipes.
const SMOKE_TINTS = [0x8e9196, 0x9ea1a5, 0xadb0b4, 0xbcbfc3, 0xc9ccd0];
const STEAM_TINTS = [0xdcdfe2, 0xe4e6e8, 0xeceef0];
const EXHAUST_TINTS = [0x8e9196, 0x9a9da2, 0xa6a9ad];

const UP = new THREE.Vector3(0, 1, 0);
const _m = new THREE.Matrix4();
const _m2 = new THREE.Matrix4();
const _p = new THREE.Vector3();
const _t = new THREE.Vector3();
const _s = new THREE.Vector3();
const _q = new THREE.Quaternion();

function mulberry32(seed) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const lambert = (color) => new THREE.MeshLambertMaterial({ color, flatShading: true });

/* Vertex-shader motion shared by the trees and the water: one time uniform and the wind,
   updated once a frame. Flat shading derives normals from screen-space derivatives, so a
   displaced vertex is lit correctly without recomputing anything. */
const motion = { uTime: { value: 0 }, uWind: { value: new THREE.Vector2() } };

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

// Water: a slow swell outward from the island and a cross-chop, faceted by flat shading.
const RIPPLE = /* glsl */ `#include <begin_vertex>
  float r = length(position.xz);
  float rim = smoothstep(12.2, 11.3, r); // still at the outer wall, so the edge never lifts off it
  transformed.y += rim * (0.08 * sin(r * 1.7 - uTime * 1.1) + 0.05 * sin(position.x * 0.9 + position.z * 0.6 + uTime * 0.8));`;

function ripple(material) {
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, motion);
    shader.vertexShader = 'uniform float uTime;\nuniform vec2 uWind;\n' +
      shader.vertexShader.replace('#include <begin_vertex>', RIPPLE);
  };
  return material;
}

function place(geometry, material, x, y, z, { cast = true, receive = false } = {}) {
  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.set(x, y, z);
  mesh.castShadow = cast;
  mesh.receiveShadow = receive;
  return mesh;
}

/* --- the road: a closed curve sampled once into an even-spaced lookup table ----------- */

const ROAD_SAMPLES = 240;
const SUN_PERIOD = 120; // s

function buildRoad() {
  const points = [];
  const n = 10;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    const wobble = 1 + 0.06 * Math.sin(a * 3 + 0.7);
    points.push(new THREE.Vector3(Math.cos(a) * 7.2 * wobble, 0.03, 3 + Math.sin(a) * 3.6 * wobble));
  }
  const curve = new THREE.CatmullRomCurve3(points, true, 'centripetal');
  const length = curve.getLength();
  const pos = new Float32Array(ROAD_SAMPLES * 3);
  const tan = new Float32Array(ROAD_SAMPLES * 3);
  for (let i = 0; i < ROAD_SAMPLES; i++) {
    const u = i / ROAD_SAMPLES;
    curve.getPointAt(u, _p);
    curve.getTangentAt(u, _t);
    pos.set([_p.x, _p.y, _p.z], i * 3);
    tan.set([_t.x, _t.y, _t.z], i * 3);
  }

  // A flat ribbon either side of the centre line.
  const half = 0.48;
  const vertices = new Float32Array((ROAD_SAMPLES + 1) * 2 * 3);
  const index = [];
  for (let i = 0; i <= ROAD_SAMPLES; i++) {
    const j = (i % ROAD_SAMPLES) * 3;
    const nx = -tan[j + 2];
    const nz = tan[j];
    vertices.set([pos[j] + nx * half, pos[j + 1], pos[j + 2] + nz * half], i * 6);
    vertices.set([pos[j] - nx * half, pos[j + 1], pos[j + 2] - nz * half], i * 6 + 3);
    if (i < ROAD_SAMPLES) {
      const a = i * 2;
      index.push(a, a + 2, a + 1, a + 1, a + 2, a + 3);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(vertices, 3));
  geometry.setIndex(index);
  geometry.computeVertexNormals();
  const mesh = new THREE.Mesh(geometry, new THREE.MeshLambertMaterial({ color: COLOR.road }));
  mesh.receiveShadow = true;

  /** Position and unit tangent at u in [0, 1), linearly interpolated. Writes into outs. */
  function sample(u, outPos, outTan) {
    const f = (((u % 1) + 1) % 1) * ROAD_SAMPLES;
    const i0 = Math.floor(f);
    const i1 = (i0 + 1) % ROAD_SAMPLES;
    const k = f - i0;
    const a = i0 * 3;
    const b = i1 * 3;
    outPos.set(
      pos[a] + (pos[b] - pos[a]) * k,
      pos[a + 1] + (pos[b + 1] - pos[a + 1]) * k,
      pos[a + 2] + (pos[b + 2] - pos[a + 2]) * k,
    );
    outTan.set(
      tan[a] + (tan[b] - tan[a]) * k,
      0,
      tan[a + 2] + (tan[b + 2] - tan[a + 2]) * k,
    ).normalize();
  }

  function distanceTo(x, z) {
    let best = Infinity;
    for (let i = 0; i < ROAD_SAMPLES; i++) {
      const dx = pos[i * 3] - x;
      const dz = pos[i * 3 + 2] - z;
      best = Math.min(best, dx * dx + dz * dz);
    }
    return Math.sqrt(best);
  }

  return { mesh, length, sample, distanceTo };
}

/* --- the world ---------------------------------------------------------------------- */

export function buildWorld({ smoke, small, shadows }) {
  const root = new THREE.Group();
  const rand = mulberry32(20261024);
  const hoverTargets = [];
  const tag = (object, kind) => {
    object.traverse((o) => { o.userData.kind = kind; });
    hoverTargets.push(object);
  };

  // Light. The sun swings from morning to afternoon and back, never setting, so the
  // scene's contrast never collapses; its colour warms as it drops.
  const hemi = new THREE.HemisphereLight(0xdfe7f0, 0x3a3226, 1.25);
  const sun = new THREE.DirectionalLight(0xfff1e0, 2.4);
  sun.position.set(9, 16, 7);
  const sunLow = new THREE.Color(0xffc995);
  const sunHigh = new THREE.Color(0xfff4e6);
  sun.castShadow = shadows;
  if (shadows) {
    sun.shadow.mapSize.set(1024, 1024);
    Object.assign(sun.shadow.camera, { left: -14, right: 14, top: 14, bottom: -14, near: 1, far: 50 });
    sun.shadow.bias = -0.0005;
    sun.shadow.normalBias = 0.03;
  }
  root.add(hemi, sun, sun.target);

  // Ground and water.
  const earth = lambert(COLOR.earth);
  const island = place(
    new THREE.CylinderGeometry(10, 9.3, 1.2, 28),
    [earth, lambert(COLOR.grass), earth],
    0, -0.6, 0, { cast: false, receive: true },
  );
  const waterSurface = place(
    new THREE.RingGeometry(9.4, 12.2, 56, 8).rotateX(-Math.PI / 2),
    ripple(lambert(COLOR.water)),
    0, -0.45, 0, { cast: false, receive: true },
  );
  const waterSide = place(
    new THREE.CylinderGeometry(12.2, 12.2, 0.9, 56, 1, true),
    lambert(COLOR.waterSide),
    0, -0.9, 0, { cast: false },
  );
  root.add(island, waterSurface, waterSide);

  // Power plant: a hall, two stacks, a cooling tower.
  const plant = new THREE.Group();
  const concrete = lambert(COLOR.concrete);
  plant.add(place(new THREE.BoxGeometry(3.4, 1.6, 2.2), concrete, -4.6, 0.8, -2.6, { receive: true }));
  plant.add(place(new THREE.BoxGeometry(3.6, 0.15, 2.4), lambert(COLOR.roof), -4.6, 1.675, -2.6));
  const stackGeo = new THREE.CylinderGeometry(0.22, 0.32, 4.6, 8);
  const bandGeo = new THREE.CylinderGeometry(0.235, 0.24, 0.3, 8);
  const stackMat = lambert(COLOR.stack);
  const bandMat = lambert(COLOR.band);
  const redLight = new THREE.MeshBasicMaterial({ color: 0xff5a4a });
  const lightGeo = new THREE.SphereGeometry(0.08, 6, 4);
  const stackX = [-6.0, -5.2];
  for (const x of stackX) {
    plant.add(place(stackGeo, stackMat, x, 2.3, -3.6));
    plant.add(place(bandGeo, bandMat, x, 4.3, -3.6));
    plant.add(place(lightGeo, redLight, x, 4.68, -3.6, { cast: false }));
  }
  const towerProfile = [[1.6, 0], [1.35, 0.8], [1.05, 2.0], [1.0, 2.6], [1.12, 3.4]]
    .map(([r, y]) => new THREE.Vector2(r, y));
  const towerMat = new THREE.MeshLambertMaterial({ color: COLOR.tower, flatShading: true, side: THREE.DoubleSide });
  plant.add(place(new THREE.LatheGeometry(towerProfile, 20), towerMat, -1.4, 0, -4.8));
  tag(plant, 'plant');
  root.add(plant);

  // Factory: a sawtooth-roofed shed, a chimney, an annex with a turning roof vent.
  const factory = new THREE.Group();
  factory.add(place(new THREE.BoxGeometry(3.8, 1.3, 2.6), lambert(COLOR.brick), 4.2, 0.65, -2.6, { receive: true }));
  const tooth = new THREE.Shape([new THREE.Vector2(0, 0), new THREE.Vector2(1.267, 0), new THREE.Vector2(0, 0.62)]);
  const toothGeo = new THREE.ExtrudeGeometry(tooth, { depth: 2.6, bevelEnabled: false }).translate(0, 0, -1.3);
  const metal = lambert(COLOR.metal);
  for (let i = 0; i < 3; i++) factory.add(place(toothGeo, metal, 2.3 + i * 1.267, 1.3, -2.6));
  factory.add(place(new THREE.CylinderGeometry(0.2, 0.28, 3.2, 8), lambert(COLOR.brick), 5.6, 1.6, -4.1));
  const amberLight = new THREE.MeshBasicMaterial({ color: 0xffb340 });
  factory.add(place(lightGeo, amberLight, 5.6, 3.28, -4.1, { cast: false }));
  factory.add(place(new THREE.BoxGeometry(1.6, 0.8, 1.2), concrete, 6.6, 0.4, -1.2, { receive: true }));
  factory.add(place(new THREE.CylinderGeometry(0.34, 0.34, 0.22, 10), metal, 6.6, 0.91, -1.2));
  const vent = new THREE.Group();
  vent.position.set(6.6, 1.06, -1.2);
  const bladeGeo = new THREE.BoxGeometry(0.62, 0.03, 0.11);
  const bladeMat = lambert(COLOR.roof);
  for (let i = 0; i < 4; i++) {
    const blade = place(bladeGeo, bladeMat, 0, 0, 0);
    blade.rotation.y = (i * Math.PI) / 4;
    vent.add(blade);
  }
  factory.add(vent);
  tag(factory, 'factory');
  root.add(factory);

  // Road and traffic.
  const road = buildRoad();
  root.add(road.mesh);

  const lanes = small ? [1, 1, -1] : [1, 1, 1, -1, -1];
  const carCount = lanes.length;
  const bodyGeo = new THREE.BoxGeometry(0.38, 0.26, 0.72).translate(0, 0.21, 0);
  const cabinGeo = new THREE.BoxGeometry(0.34, 0.2, 0.38).translate(0, 0.44, -0.06);
  const bodies = new THREE.InstancedMesh(bodyGeo, lambert(0xffffff), carCount);
  const cabins = new THREE.InstancedMesh(cabinGeo, lambert(COLOR.cabin), carCount);
  const cars = [];
  for (let i = 0; i < carCount; i++) {
    const dir = lanes[i];
    const sameLane = lanes.filter((d) => d === dir).length;
    const slot = lanes.slice(0, i).filter((d) => d === dir).length;
    const isVan = i === 1;
    const scale = isVan ? new THREE.Vector3(1.08, 1.35, 1.3) : new THREE.Vector3(1, 1, 1);
    // The van's cabin is stretched into a box body; a car's sits behind its bonnet.
    const cabinLocal = isVan
      ? new THREE.Matrix4().compose(new THREE.Vector3(0, 0.02, 0.06), new THREE.Quaternion(), new THREE.Vector3(1.04, 1.05, 1.85))
      : new THREE.Matrix4();
    bodies.setColorAt(i, new THREE.Color(isVan ? COLOR.van : COLOR.cars[i % COLOR.cars.length]));
    const exhaust = new THREE.Vector3();
    cars.push({
      dir,
      u0: slot / sameLane + (dir < 0 ? 0.13 : 0),
      speed: 1.5 + (dir < 0 ? 0.15 : 0),
      scale,
      cabinLocal,
      exhaust,
    });
    smoke.addEmitter({
      count: small ? 12 : 16, origin: exhaust, life: [0.8, 1.2], size: [0.1, 0.42],
      rise: 0.25, spread: 0.03, alpha: 0.45, drift: 0.4, tints: EXHAUST_TINTS,
    });
  }
  for (const mesh of [bodies, cabins]) {
    mesh.castShadow = true;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    // Cars move along the whole loop, so cull and raycast against the loop, not a pose.
    mesh.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 3), 9);
  }
  tag(bodies, 'vehicle');
  tag(cabins, 'vehicle');
  root.add(bodies, cabins);

  // Smoke and steam. Slices of one pool; see smoke.js for the budget.
  const share = small ? 0.5 : 1;
  for (const x of stackX) {
    smoke.addEmitter({
      count: Math.round(110 * share), origin: new THREE.Vector3(x, 4.75, -3.6),
      life: [6, 8], size: [0.5, 2.6], rise: 0.9, spread: 0.12, alpha: 0.55, drift: 1, tints: SMOKE_TINTS,
    });
  }
  smoke.addEmitter({
    count: Math.round(200 * share), origin: new THREE.Vector3(-1.4, 3.45, -4.8),
    life: [8, 10], size: [1.0, 3.8], rise: 0.7, spread: 0.75, alpha: 0.42, drift: 0.8, tints: STEAM_TINTS,
  });
  smoke.addEmitter({
    count: Math.round(110 * share), origin: new THREE.Vector3(5.6, 3.32, -4.1),
    life: [6, 8], size: [0.45, 2.4], rise: 0.85, spread: 0.1, alpha: 0.5, drift: 1, tints: SMOKE_TINTS,
  });

  // Trees, kept clear of the road, the buildings and the shore.
  const keepOut = [[-4.6, -2.6, 2.6], [-5.6, -3.6, 1.0], [-1.4, -4.8, 2.0], [4.2, -2.6, 2.6], [5.6, -4.1, 0.8], [6.6, -1.2, 1.3]];
  const treeCount = small ? 20 : 36;
  const trees = [];
  for (let attempts = 0; trees.length < treeCount && attempts < 4000; attempts++) {
    const r = Math.sqrt(rand()) * 9.1;
    const a = rand() * Math.PI * 2;
    const x = Math.cos(a) * r;
    const z = Math.sin(a) * r;
    if (road.distanceTo(x, z) < 1.0) continue;
    if (keepOut.some(([kx, kz, kr]) => Math.hypot(x - kx, z - kz) < kr)) continue;
    if (trees.some((t) => Math.hypot(x - t.x, z - t.z) < 0.85)) continue;
    trees.push({ x, z, s: 0.7 + rand() * 0.6, phase: rand() * Math.PI * 2 });
  }
  const trunkGeo = new THREE.CylinderGeometry(0.08, 0.11, 0.5, 5).translate(0, 0.25, 0);
  const coneGeo = new THREE.ConeGeometry(0.45, 1.2, 6).translate(0, 1.1, 0);
  const phases = new Float32Array(trees.map((t) => t.phase));
  coneGeo.setAttribute('aPhase', new THREE.InstancedBufferAttribute(phases, 1));
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
    mesh.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 1, 0), 11);
  }
  root.add(trunks, cones);

  // Clouds: a few blobs each, drifting with the wind and shrinking away at the edge of
  // the world before they wrap, so nothing pops.
  const cloudCount = small ? 3 : 5;
  const blobs = [[-0.9, 0, 0, 0.75], [0, 0.25, 0, 1], [0.9, 0.05, 0.1, 0.8], [0.25, 0.1, -0.55, 0.7]];
  const clouds = [];
  for (let i = 0; i < cloudCount; i++) {
    const a = (i / cloudCount) * Math.PI * 2 + rand();
    const r = 5 + rand() * 8;
    clouds.push({ x: Math.cos(a) * r, z: Math.sin(a) * r, y: 9.2 + rand() * 1.4, s: 0.6 + rand() * 0.35, phase: rand() * 6.28 });
  }
  const cloudMesh = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1, 0), lambert(COLOR.cloud), cloudCount * blobs.length);
  cloudMesh.castShadow = shadows;
  cloudMesh.frustumCulled = false;
  cloudMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  root.add(cloudMesh);
  const CLOUD_EDGE = 15;

  const redOn = new THREE.Color(0xff5a4a);
  const redOff = new THREE.Color(0x3a1a17);
  const amberOn = new THREE.Color(0xffb340);
  const amberOff = new THREE.Color(0x3a2a12);

  function update(time, dt, wind) {
    // Light: one slow arc, a full swing every SUN_PERIOD seconds.
    const day = Math.sin((time / SUN_PERIOD) * Math.PI * 2);
    const height = 0.5 + 0.5 * Math.cos((time / SUN_PERIOD) * Math.PI * 4); // high at mid-swing
    const elevation = 0.5 + 0.6 * height;
    const azimuth = 0.9 + 0.8 * day;
    sun.position.set(
      20 * Math.cos(elevation) * Math.cos(azimuth),
      20 * Math.sin(elevation),
      20 * Math.cos(elevation) * Math.sin(azimuth),
    );
    sun.color.lerpColors(sunLow, sunHigh, height);
    sun.intensity = 2.1 + 0.5 * height;
    hemi.intensity = 1.15 + 0.2 * height;

    // Beacons: short flashes, the two stacks in step, the factory's on its own beat.
    redLight.color.lerpColors(redOff, redOn, Math.sin(time * 2.2) > 0.55 ? 1 : 0.15);
    amberLight.color.lerpColors(amberOff, amberOn, Math.sin(time * 3.1 + 1) > 0.3 ? 1 : 0.1);
    vent.rotation.y += dt * 2.4;

    // Traffic.
    for (let i = 0; i < carCount; i++) {
      const c = cars[i];
      road.sample(c.u0 + (c.dir * c.speed * time) / road.length, _p, _t);
      // Right-hand lane offset; the forward vector is the tangent times the direction.
      _p.x += -_t.z * 0.22 * c.dir;
      _p.z += _t.x * 0.22 * c.dir;
      _t.multiplyScalar(c.dir);
      _q.setFromAxisAngle(UP, Math.atan2(_t.x, _t.z));
      _m.compose(_p, _q, c.scale);
      bodies.setMatrixAt(i, _m);
      _m2.multiplyMatrices(_m, c.cabinLocal);
      cabins.setMatrixAt(i, _m2);
      const back = 0.38 * c.scale.z;
      c.exhaust.set(_p.x - _t.x * back, 0.14, _p.z - _t.z * back);
    }
    bodies.instanceMatrix.needsUpdate = true;
    cabins.instanceMatrix.needsUpdate = true;

    // Trees and water move in their vertex shaders; they only need the clock and the wind.
    motion.uTime.value = time;
    motion.uWind.value.set(wind.x, wind.z);

    // Clouds.
    let k = 0;
    for (let i = 0; i < cloudCount; i++) {
      const c = clouds[i];
      c.x += wind.x * 0.5 * dt;
      c.z += wind.z * 0.5 * dt;
      let r = Math.hypot(c.x, c.z);
      if (r > CLOUD_EDGE) {
        // Wrap to the opposite edge, where it is still shrunk to nothing.
        c.x *= -(CLOUD_EDGE - 0.05) / r;
        c.z *= -(CLOUD_EDGE - 0.05) / r;
        r = CLOUD_EDGE - 0.05;
      }
      const fade = THREE.MathUtils.smoothstep(CLOUD_EDGE - r, 0, 2.5) * c.s;
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

  return { root, update, hoverTargets };
}

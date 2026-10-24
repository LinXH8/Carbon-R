/* The farm in the south: fields with furrows, a barn and a silo, a tractor ploughing its
 * field row by row (and back), cows wandering their pasture, and wind turbines that turn
 * to face the wind and spin with it.
 */

import * as THREE from 'three';
import { lambert, pathTable, place, shuttle, shuttleYaw } from './kit.js';
import { mergeStatic } from './merge.js';

const COLOR = {
  fields: [0x6b5236, 0xb8a35a, 0x6f8a4a],
  furrow: [0x55412b, 0x9c8a4a, 0x5d7640],
  barn: 0x8e4a3c,
  barnRoof: 0x4f4a43,
  silo: 0xc9c4b8,
  tractor: 0x9c4a3c,
  tyre: 0x2a2c2e,
  cow: 0xe6e2d6,
  turbine: 0xe4e2dc,
};
const EXHAUST_TINTS = [0x8e9196, 0x9a9da2];
const DUST_TINTS = [0x8a7458, 0x9a8466, 0x7a6650];

// Fields as [xMin, xMax, zMin, zMax]; the first is the one being ploughed.
const FIELDS = [[-10, -6.6, 5.0, 8.3], [-6.2, -2.7, 5.0, 8.3], [-2.3, 1.2, 5.0, 8.3]];
const PASTURE = [2.0, 5.4, 5.8, 8.2];
const TURBINES = [[6.0, 8.6], [9.0, 7.8], [12.0, 6.6]];

const UP = new THREE.Vector3(0, 1, 0);
const _p = new THREE.Vector3();
const _t = new THREE.Vector3();
const _q = new THREE.Quaternion();
const _m = new THREE.Matrix4();
const _s = new THREE.Vector3(1, 1, 1);

export function buildFarm({ smoke, small, statics, root, tag }) {
  // Fields and their furrows.
  FIELDS.forEach(([x0, x1, z0, z1], i) => {
    statics.add(place(new THREE.BoxGeometry(x1 - x0, 0.03, z1 - z0), lambert(COLOR.fields[i]), (x0 + x1) / 2, 0.015, (z0 + z1) / 2, { cast: false, receive: true }));
    for (let z = z0 + 0.25; z < z1 - 0.1; z += 0.35) {
      statics.add(place(new THREE.BoxGeometry(x1 - x0 - 0.2, 0.02, 0.07), lambert(COLOR.furrow[i]), (x0 + x1) / 2, 0.04, z, { cast: false }));
    }
  });

  // Barn and silo.
  statics.add(place(new THREE.BoxGeometry(1.6, 0.9, 1.2), lambert(COLOR.barn), -12.2, 0.45, 4.4, { receive: true }));
  const gable = new THREE.Shape([new THREE.Vector2(-0.7, 0), new THREE.Vector2(0.7, 0), new THREE.Vector2(0, 0.5)]);
  statics.add(place(new THREE.ExtrudeGeometry(gable, { depth: 1.7, bevelEnabled: false }).rotateY(Math.PI / 2).translate(-0.85, 0, 0), lambert(COLOR.barnRoof), -12.2, 0.9, 4.4));
  statics.add(place(new THREE.CylinderGeometry(0.4, 0.4, 1.8, 10).translate(0, 0.9, 0), lambert(COLOR.silo), -12.9, 0, 5.7));
  statics.add(place(new THREE.SphereGeometry(0.4, 10, 5, 0, Math.PI * 2, 0, Math.PI / 2), lambert(COLOR.silo), -12.9, 1.8, 5.7));

  // Tractor: up and down the first field's rows, then back the way it came.
  const [fx0, fx1, fz0, fz1] = FIELDS[0];
  const rows = [];
  for (let z = fz0 + 0.4, flip = false; z < fz1 - 0.2; z += 0.7, flip = !flip) {
    rows.push(flip ? [fx1 - 0.4, 0, z] : [fx0 + 0.4, 0, z], flip ? [fx0 + 0.4, 0, z] : [fx1 - 0.4, 0, z]);
  }
  const plough = pathTable(rows, 240);
  const tractor = new THREE.Group();
  tractor.add(place(new THREE.BoxGeometry(0.26, 0.18, 0.46).translate(0, 0.2, 0.04), lambert(COLOR.tractor), 0, 0, 0));
  tractor.add(place(new THREE.BoxGeometry(0.24, 0.22, 0.22).translate(0, 0.4, -0.08), lambert(COLOR.tyre), 0, 0, 0));
  const tyre = lambert(COLOR.tyre);
  for (const side of [-1, 1]) {
    tractor.add(place(new THREE.CylinderGeometry(0.14, 0.14, 0.08, 10).rotateZ(Math.PI / 2), tyre, side * 0.16, 0.14, -0.1));
    tractor.add(place(new THREE.CylinderGeometry(0.08, 0.08, 0.06, 8).rotateZ(Math.PI / 2), tyre, side * 0.14, 0.08, 0.2));
  }
  mergeStatic(tractor);
  tag(tractor, 'vehicle');
  root.add(tractor);
  const tractorAt = shuttle({ length: plough.length, speed: 0.6, dwell: 4, offset: 0 });
  const tractorState = { u: 0, dir: 1, turn: 0, moving: false };
  const stack = new THREE.Vector3();
  const dust = new THREE.Vector3();
  const stackEmitter = smoke.addEmitter({
    count: small ? 12 : 20, origin: stack, life: [1, 1.5], size: [0.1, 0.4],
    rise: 0.35, spread: 0.02, alpha: 0.4, drift: 0.4, tints: EXHAUST_TINTS,
  });
  const dustEmitter = smoke.addEmitter({
    count: small ? 25 : 50, origin: dust, life: [1.6, 2.4], size: [0.2, 0.8],
    rise: 0.05, lift: 0.02, spread: 0.15, alpha: 0.35, drift: 0.6, tints: DUST_TINTS,
  });

  // Cows: each wanders its own slow loop around the pasture.
  const cowCount = small ? 4 : 7;
  const cowGeo = new THREE.BoxGeometry(0.16, 0.16, 0.34).translate(0, 0.16, 0);
  const cows = new THREE.InstancedMesh(cowGeo, lambert(COLOR.cow), cowCount);
  cows.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  cows.frustumCulled = false;
  root.add(cows);
  const [px0, px1, pz0, pz1] = PASTURE;
  const herd = Array.from({ length: cowCount }, (_, i) => ({
    cx: px0 + 0.6 + ((i * 0.37) % 1) * (px1 - px0 - 1.2),
    cz: pz0 + 0.5 + ((i * 0.61) % 1) * (pz1 - pz0 - 1.0),
    w1: 0.05 + i * 0.007, w2: 0.04 + i * 0.006, p: i * 1.7,
  }));

  // Wind turbines: towers are static; each head turns to the wind and its rotor spins.
  const heads = [];
  const turbineMat = lambert(COLOR.turbine);
  for (const [x, z] of TURBINES) {
    statics.add(place(new THREE.CylinderGeometry(0.06, 0.1, 3.2, 8).translate(0, 1.6, 0), turbineMat, x, 0, z));
    const head = new THREE.Group();
    head.userData.dynamic = true;
    head.position.set(x, 3.2, z);
    head.add(place(new THREE.BoxGeometry(0.16, 0.16, 0.4), turbineMat, 0, 0, -0.05));
    const rotor = new THREE.Group();
    rotor.userData.dynamic = true;
    rotor.position.set(0, 0, 0.2);
    for (let b = 0; b < 3; b++) {
      const blade = place(new THREE.BoxGeometry(0.12, 1.3, 0.03).translate(0, 0.65, 0), turbineMat, 0, 0, 0);
      blade.rotation.z = (b * Math.PI * 2) / 3;
      rotor.add(blade);
    }
    head.add(rotor);
    mergeStatic(rotor);
    mergeStatic(head);
    root.add(head);
    heads.push({ head, rotor, phase: x });
  }

  function update(time, dt, wind) {
    const s = tractorAt(time, tractorState);
    plough.sample(s.u, _p, _t);
    const yaw = shuttleYaw(_t, s);
    tractor.position.copy(_p);
    tractor.rotation.y = yaw;
    stack.set(_p.x + Math.sin(yaw) * 0.12, 0.5, _p.z + Math.cos(yaw) * 0.12);
    dust.set(_p.x - Math.sin(yaw) * 0.35, 0.08, _p.z - Math.cos(yaw) * 0.35);
    stackEmitter.enabled = true;
    dustEmitter.enabled = s.moving;

    for (let i = 0; i < cowCount; i++) {
      const c = herd[i];
      const x = c.cx + 0.6 * Math.sin(time * c.w1 + c.p);
      const z = c.cz + 0.45 * Math.sin(time * c.w2 + c.p * 1.3);
      const dx = 0.6 * c.w1 * Math.cos(time * c.w1 + c.p);
      const dz = 0.45 * c.w2 * Math.cos(time * c.w2 + c.p * 1.3);
      _p.set(x, 0, z);
      _q.setFromAxisAngle(UP, Math.atan2(dx, dz));
      _m.compose(_p, _q, _s);
      cows.setMatrixAt(i, _m);
    }
    cows.instanceMatrix.needsUpdate = true;

    // Turbines face into the wind and spin faster as it rises.
    const strength = Math.hypot(wind.x, wind.z);
    const facing = Math.atan2(-wind.x, -wind.z);
    for (let i = 0; i < heads.length; i++) {
      heads[i].head.rotation.y = facing;
      heads[i].rotor.rotation.z += dt * (0.8 + 2.2 * strength);
    }
  }

  const keepOut = [[-12.2, 4.4, 1.3], [-12.9, 5.7, 0.7], ...TURBINES.map(([x, z]) => [x, z, 0.5])];
  const keepOutRects = [...FIELDS, PASTURE].map(([x0, x1, z0, z1]) => [x0 - 0.2, x1 + 0.2, z0 - 0.2, z1 + 0.2]);
  return { update, keepOut, keepOutRects };
}

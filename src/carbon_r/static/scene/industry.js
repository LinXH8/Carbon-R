/* Industry: the power plant (fed by a freight train from the harbour), and the factory
 * (a conveyor carrying crates out to its loading dock, a forklift taking them to the yard,
 * a turning roof vent, a chimney). Every moving part works between two places.
 */

import * as THREE from 'three';
import { lambert, pathTable, place, ribbonGeometry, shuttle, shuttleYaw } from './kit.js';
import { mergeStatic } from './merge.js';

const COLOR = {
  concrete: 0xb7b1a5,
  roof: 0x7b7f84,
  tower: 0xd0cabe,
  stack: 0xc4bfb5,
  band: 0x9c4a3c,
  brick: 0x9a6a54,
  metal: 0x6d7175,
  belt: 0x2f3234,
  crate: 0xb39f78,
  coal: 0x2c2b2a,
  ballast: 0x5a5550,
  rail: 0x9a9da0,
  loco: 0x5d6b5a,
  wagon: 0x4f4a43,
  forklift: 0xc9a23f,
  dark: 0x2a2c2e,
};
const SMOKE_TINTS = [0x8e9196, 0x9ea1a5, 0xadb0b4, 0xbcbfc3, 0xc9ccd0];
const STEAM_TINTS = [0xdcdfe2, 0xe4e6e8, 0xeceef0];
const EXHAUST_TINTS = [0x8e9196, 0x9a9da2, 0xa6a9ad];

const UP = new THREE.Vector3(0, 1, 0);
const _p = new THREE.Vector3();
const _t = new THREE.Vector3();
const _q = new THREE.Quaternion();
const _m = new THREE.Matrix4();
const _s = new THREE.Vector3();

/* A forklift facing +z with a fork that rises and falls and a crate it can carry. */
export function makeForklift() {
  const group = new THREE.Group();
  group.add(place(new THREE.BoxGeometry(0.3, 0.22, 0.42).translate(0, 0.15, -0.05), lambert(COLOR.forklift), 0, 0, 0));
  group.add(place(new THREE.BoxGeometry(0.26, 0.2, 0.18).translate(0, 0.36, -0.12), lambert(COLOR.dark), 0, 0, 0));
  group.add(place(new THREE.BoxGeometry(0.04, 0.6, 0.04).translate(0, 0.3, 0.19), lambert(COLOR.dark), 0.1, 0, 0));
  group.add(place(new THREE.BoxGeometry(0.04, 0.6, 0.04).translate(0, 0.3, 0.19), lambert(COLOR.dark), -0.1, 0, 0));
  const fork = new THREE.Group();
  fork.userData.dynamic = true;
  fork.add(place(new THREE.BoxGeometry(0.26, 0.03, 0.26).translate(0, 0, 0.33), lambert(COLOR.dark), 0, 0, 0, { cast: false }));
  const load = place(new THREE.BoxGeometry(0.24, 0.2, 0.24).translate(0, 0.115, 0.33), lambert(COLOR.crate), 0, 0, 0, { cast: false });
  load.userData.dynamic = true; // shown and hidden, so never merged into the fork
  fork.add(load);
  group.add(fork);
  mergeStatic(group);
  mergeStatic(fork);
  return { group, fork, load };
}

/**
 * Drive a forklift between two points: it carries a load out, lifts it off at B, drives
 * back empty and lowers its fork at A to pick the next one up.
 */
export function forkliftJob(lift, from, to, { speed = 0.9, dwell = 3, offset = 0 } = {}) {
  const table = pathTable([[from[0], 0, from[1]], [to[0], 0, to[1]]], 24);
  const at = shuttle({ length: table.length, speed, dwell, offset });
  const state = { u: 0, dir: 1, turn: 0, moving: false };
  return (time) => {
    const s = at(time, state);
    table.sample(s.u, _p, _t);
    lift.group.position.copy(_p);
    lift.group.rotation.y = shuttleYaw(_t, s);
    // Fork up while stopped (unloading at B, loading at A); loaded on the way out.
    lift.fork.position.y = s.moving ? 0.12 : 0.12 + 0.35 * Math.sin(Math.PI * s.turn);
    lift.load.visible = s.dir > 0 ? s.turn < 0.5 : s.turn > 0.5;
  };
}

export function buildIndustry({ smoke, small, root, tag }) {
  const share = small ? 0.5 : 1;

  // Power plant: a hall, two stacks, a cooling tower, a coal pile at the rail head.
  const plant = new THREE.Group();
  const concrete = lambert(COLOR.concrete);
  plant.add(place(new THREE.BoxGeometry(3.4, 1.6, 2.2), concrete, -6, 0.8, -6.5, { receive: true }));
  plant.add(place(new THREE.BoxGeometry(3.6, 0.15, 2.4), lambert(COLOR.roof), -6, 1.675, -6.5));
  const stackGeo = new THREE.CylinderGeometry(0.22, 0.32, 4.6, 8);
  const bandGeo = new THREE.CylinderGeometry(0.235, 0.24, 0.3, 8);
  const redLight = new THREE.MeshBasicMaterial({ color: 0xff5a4a });
  const lightGeo = new THREE.SphereGeometry(0.08, 6, 4);
  const stackX = [-7.4, -6.6];
  for (const x of stackX) {
    plant.add(place(stackGeo, lambert(COLOR.stack), x, 2.3, -7.8));
    plant.add(place(bandGeo, lambert(COLOR.band), x, 4.3, -7.8));
    plant.add(place(lightGeo, redLight, x, 4.68, -7.8, { cast: false }));
  }
  const towerProfile = [[1.6, 0], [1.35, 0.8], [1.05, 2.0], [1.0, 2.6], [1.12, 3.4]].map(([r, y]) => new THREE.Vector2(r, y));
  plant.add(place(new THREE.LatheGeometry(towerProfile, 20), lambert(COLOR.tower, { side: THREE.DoubleSide }), -2.8, 0, -8.2));
  plant.add(place(new THREE.ConeGeometry(0.9, 0.5, 7), lambert(COLOR.coal), -3.2, 0.25, -5.6));
  tag(plant, 'plant');
  mergeStatic(plant);
  root.add(plant);
  for (const x of stackX) {
    smoke.addEmitter({
      count: Math.round(110 * share), origin: new THREE.Vector3(x, 4.75, -7.8),
      life: [6, 8], size: [0.5, 2.6], rise: 0.9, spread: 0.12, alpha: 0.55, drift: 1, tints: SMOKE_TINTS,
    });
  }
  smoke.addEmitter({
    count: Math.round(200 * share), origin: new THREE.Vector3(-2.8, 3.45, -8.2),
    life: [8, 10], size: [1.0, 3.8], rise: 0.7, spread: 0.75, alpha: 0.42, drift: 0.8, tints: STEAM_TINTS,
  });

  // Rail line from the harbour yard to the plant's coal pile, and the train that runs it.
  const railTable = pathTable([[-14.2, 0.02, -4.2], [-10.5, 0.02, -4.4], [-7.2, 0.02, -4.8], [-4.0, 0.02, -5.0]], 96);
  const statics = new THREE.Group();
  statics.add(place(ribbonGeometry(railTable, 0.32), lambert(COLOR.ballast), 0, 0, 0, { cast: false, receive: true }));
  const railMat = lambert(COLOR.rail);
  for (const side of [-0.13, 0.13]) {
    statics.add(place(ribbonGeometry(railTable, 0.025, 0.02, side), railMat, 0, 0, 0, { cast: false }));
  }
  const cars = 4;
  const train = new THREE.InstancedMesh(new THREE.BoxGeometry(0.34, 0.3, 0.8).translate(0, 0.25, 0), lambert(0xffffff), cars);
  for (let i = 0; i < cars; i++) train.setColorAt(i, new THREE.Color(i === 0 ? COLOR.loco : COLOR.wagon));
  train.castShadow = true;
  train.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  train.boundingSphere = new THREE.Sphere(new THREE.Vector3(-9, 0, -4.6), 7);
  tag(train, 'freight');
  root.add(train);
  const trainAt = shuttle({ length: railTable.length - 3.2, speed: 1.3, dwell: 7, offset: 3 });
  const trainState = { u: 0, dir: 1, turn: 0, moving: false };
  const locoExhaust = new THREE.Vector3();
  const locoEmitter = smoke.addEmitter({
    count: small ? 20 : 30, origin: locoExhaust, life: [1.2, 1.8], size: [0.15, 0.6],
    rise: 0.4, spread: 0.03, alpha: 0.45, drift: 0.5, tints: EXHAUST_TINTS,
  });

  // Factory: sawtooth shed, chimney, annex with a turning vent.
  const factory = new THREE.Group();
  factory.add(place(new THREE.BoxGeometry(3.8, 1.3, 2.6), lambert(COLOR.brick), 4.5, 0.65, -6.2, { receive: true }));
  const tooth = new THREE.Shape([new THREE.Vector2(0, 0), new THREE.Vector2(1.267, 0), new THREE.Vector2(0, 0.62)]);
  const toothGeo = new THREE.ExtrudeGeometry(tooth, { depth: 2.6, bevelEnabled: false }).translate(0, 0, -1.3);
  const metal = lambert(COLOR.metal);
  for (let i = 0; i < 3; i++) factory.add(place(toothGeo, metal, 2.6 + i * 1.267, 1.3, -6.2));
  factory.add(place(new THREE.CylinderGeometry(0.2, 0.28, 3.2, 8), lambert(COLOR.brick), 6.2, 1.6, -7.8));
  const amberLight = new THREE.MeshBasicMaterial({ color: 0xffb340 });
  factory.add(place(lightGeo, amberLight, 6.2, 3.28, -7.8, { cast: false }));
  factory.add(place(new THREE.BoxGeometry(1.4, 0.8, 1.2), concrete, 1.8, 0.4, -6.0, { receive: true }));
  factory.add(place(new THREE.CylinderGeometry(0.34, 0.34, 0.22, 10), metal, 1.8, 0.91, -6.0));
  const vent = new THREE.Group();
  vent.userData.dynamic = true;
  vent.position.set(1.8, 1.06, -6.0);
  for (let i = 0; i < 4; i++) {
    const blade = place(new THREE.BoxGeometry(0.62, 0.03, 0.11), lambert(COLOR.roof), 0, 0, 0, { cast: false });
    blade.rotation.y = (i * Math.PI) / 4;
    vent.add(blade);
  }
  factory.add(vent);

  // Conveyor from the factory's east wall out to the loading dock.
  const beltFrom = new THREE.Vector3(6.5, 0.5, -5.4);
  const beltTo = new THREE.Vector3(9.2, 0.5, -4.3);
  const beltDir = beltTo.clone().sub(beltFrom);
  const beltLength = beltDir.length();
  beltDir.normalize();
  const belt = place(new THREE.BoxGeometry(0.34, 0.06, beltLength), lambert(COLOR.belt), 0, 0, 0);
  belt.position.copy(beltFrom).lerp(beltTo, 0.5);
  belt.rotation.y = Math.atan2(beltDir.x, beltDir.z);
  factory.add(belt);
  for (const k of [0.15, 0.5, 0.85]) {
    const leg = place(new THREE.BoxGeometry(0.05, 0.5, 0.05), lambert(COLOR.dark), 0, 0, 0);
    leg.position.copy(beltFrom).lerp(beltTo, k).setY(0.25);
    factory.add(leg);
  }
  factory.add(place(new THREE.BoxGeometry(1.2, 0.3, 1.0), concrete, 9.7, 0.15, -4.0, { receive: true }));
  tag(factory, 'factory');
  mergeStatic(factory);
  mergeStatic(vent);
  root.add(factory);
  smoke.addEmitter({
    count: Math.round(110 * share), origin: new THREE.Vector3(6.2, 3.32, -7.8),
    life: [6, 8], size: [0.45, 2.4], rise: 0.85, spread: 0.1, alpha: 0.5, drift: 1, tints: SMOKE_TINTS,
  });

  const crateCount = 6;
  const crates = new THREE.InstancedMesh(new THREE.BoxGeometry(0.22, 0.18, 0.22), lambert(COLOR.crate), crateCount);
  crates.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  crates.frustumCulled = false;
  root.add(crates);

  // Forklift: dock to yard. A few pallets wait in the yard.
  const lift = makeForklift();
  tag(lift.group, 'vehicle');
  root.add(lift.group);
  const runLift = forkliftJob(lift, [9.9, -3.2], [11.6, -2.4], { offset: 2 });
  for (let i = 0; i < 4; i++) statics.add(place(new THREE.BoxGeometry(0.24, 0.2, 0.24), lambert(COLOR.crate), 12.0 + (i % 2) * 0.3, 0.1 + Math.floor(i / 2) * 0.2, -2.0));
  mergeStatic(statics);
  root.add(statics);

  const redOn = new THREE.Color(0xff5a4a);
  const redOff = new THREE.Color(0x3a1a17);
  const amberOn = new THREE.Color(0xffb340);
  const amberOff = new THREE.Color(0x3a2a12);

  function update(time, dt) {
    redLight.color.lerpColors(redOff, redOn, Math.sin(time * 2.2) > 0.55 ? 1 : 0.15);
    amberLight.color.lerpColors(amberOff, amberOn, Math.sin(time * 3.1 + 1) > 0.3 ? 1 : 0.1);
    vent.rotation.y += dt * 2.4;

    // Crates ride the belt and drop off the end, evenly spaced.
    for (let i = 0; i < crateCount; i++) {
      const k = ((time * 0.35) / beltLength + i / crateCount) % 1;
      _p.copy(beltFrom).lerp(beltTo, k);
      _p.y = 0.62;
      _m.compose(_p, _q.setFromAxisAngle(UP, belt.rotation.y), _s.setScalar(1));
      crates.setMatrixAt(i, _m);
    }
    crates.instanceMatrix.needsUpdate = true;

    runLift(time);

    // Train: the locomotive leads out of the harbour and backs its wagons home.
    const s = trainAt(time, trainState);
    const usable = railTable.length - 3.2;
    for (let i = 0; i < cars; i++) {
      const along = (3.2 + s.u * usable - i * 0.95) / railTable.length;
      railTable.sample(along, _p, _t);
      _q.setFromAxisAngle(UP, Math.atan2(_t.x, _t.z));
      _m.compose(_p, _q, _s.setScalar(1));
      train.setMatrixAt(i, _m);
      if (i === 0) locoExhaust.set(_p.x + _t.x * 0.25, 0.62, _p.z + _t.z * 0.25);
    }
    train.instanceMatrix.needsUpdate = true;
    locoEmitter.enabled = s.moving;
  }

  const keepOut = [[-6, -6.5, 2.6], [-7, -7.8, 1.0], [-2.8, -8.2, 2.0], [-3.2, -5.6, 1.1], [4.5, -6.2, 2.7], [6.2, -7.8, 0.8], [1.8, -6.0, 1.1], [8, -4.8, 1.2], [10.5, -3.2, 1.8]];
  return { update, keepOut, tables: [railTable] };
}

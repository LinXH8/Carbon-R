/* Roads between the places that need them - harbour, factory, power plant, farm and
 * airfield - and the vehicles that use them. Every vehicle has a job: it drives from A
 * to B, stops, turns round and comes back. Nobody circles.
 *
 * Bodies and cabins are two instanced meshes shared by every vehicle; a truck is a car
 * with a long body and its cabin moved to the front. update() allocates nothing.
 */

import * as THREE from 'three';
import { lambert, pathTable, place, ribbonGeometry, shuttle, shuttleYaw } from './kit.js';
import { mergeStatic } from './merge.js';

const COLOR = {
  road: 0x3b3e41,
  cabin: 0x2a3034,
  cars: [0xb4574a, 0x5a7a97],
  van: 0xeeede8,
  truck: [0x8d5a43, 0x5c6f7d],
  tanker: 0xd0cdc5,
  grain: 0xb39f78,
};
const EXHAUST_TINTS = [0x8e9196, 0x9a9da2, 0xa6a9ad];

// Where the roads meet, and where they go.
export const NODE = {
  harbour: [-12, 0.8], junctionW: [-4.5, 0.6], junctionE: [3, 0.4],
  factory: [5.5, -3.3], plant: [-5.5, -3.6], airfield: [10.4, 0.2], farm: [-8, 3.2],
};
const BEND = {
  'harbour-junctionW': [-8, 1.1], 'junctionW-junctionE': [-0.5, 0.2], 'junctionE-factory': [4.6, -1.5],
  'junctionW-plant': [-5.2, -1.5], 'junctionE-airfield': [7, 0], 'junctionW-farm': [-6.3, 2.2],
};

/** Points along a chain of nodes, with each road's bend between them. */
function route(...names) {
  const points = [];
  for (let i = 0; i < names.length; i++) {
    if (i > 0) {
      const bend = BEND[`${names[i - 1]}-${names[i]}`] || BEND[`${names[i]}-${names[i - 1]}`];
      points.push([bend[0], 0.03, bend[1]]);
    }
    points.push([NODE[names[i]][0], 0.03, NODE[names[i]][1]]);
  }
  return points;
}

// Each vehicle's job: kind, route, peak speed, wait at each end, phase offset.
const JOBS = [
  { kind: 'truck', colour: COLOR.truck[0], via: ['harbour', 'junctionW', 'junctionE', 'factory'], speed: 1.5, dwell: 5, offset: 0 },
  { kind: 'tanker', colour: COLOR.tanker, via: ['harbour', 'junctionW', 'junctionE', 'airfield'], speed: 1.4, dwell: 6, offset: 9 },
  { kind: 'truck', colour: COLOR.grain, via: ['farm', 'junctionW', 'harbour'], speed: 1.4, dwell: 5, offset: 4 },
  { kind: 'car', colour: COLOR.cars[0], via: ['farm', 'junctionW', 'junctionE', 'airfield'], speed: 2.0, dwell: 4, offset: 13 },
  { kind: 'van', colour: COLOR.van, via: ['plant', 'junctionW', 'junctionE', 'factory'], speed: 1.8, dwell: 4, offset: 6 },
  { kind: 'car', colour: COLOR.cars[1], via: ['factory', 'junctionE', 'junctionW', 'plant'], speed: 2.0, dwell: 3, offset: 20 },
];

// Body scale, and the cabin's offset and scale applied to the shared cabin geometry, both
// relative to the unscaled vehicle. Vehicles face +z. A truck's cabin sits ahead of its
// load; a van's is stretched into a box body.
const SHAPE = {
  car: { scale: [1, 1, 1], cabin: [[0, 0, 0], [1, 1, 1]], back: 0.38 },
  van: { scale: [1.08, 1.35, 1.3], cabin: [[0, 0.027, 0.078], [1.12, 1.42, 2.4]], back: 0.48 },
  truck: { scale: [1.15, 1.5, 2.1], cabin: [[0, -0.634, 1.004], [1.15, 2.1, 0.9]], back: 0.78 },
  tanker: { scale: [1.1, 1.35, 2.0], cabin: [[0, -0.566, 0.969], [1.1, 1.9, 0.9]], back: 0.74 },
};
const UNIT = new THREE.Vector3(1, 1, 1);

const _p = new THREE.Vector3();
const _t = new THREE.Vector3();
const _q = new THREE.Quaternion();
const _m = new THREE.Matrix4();
const _m2 = new THREE.Matrix4();
const _m3 = new THREE.Matrix4();
const UP = new THREE.Vector3(0, 1, 0);

export function buildRoads({ smoke, small, root, tag }) {
  // The roads themselves, one ribbon per stretch, merged into one draw call.
  const roadMat = lambert(COLOR.road);
  const network = new THREE.Group();
  const tables = [];
  for (const key of Object.keys(BEND)) {
    const [a, b] = key.split('-');
    const table = pathTable(route(a, b), 48);
    tables.push(table);
    network.add(place(ribbonGeometry(table, 0.48), roadMat, 0, 0, 0, { cast: false, receive: true }));
  }
  mergeStatic(network);
  root.add(network);

  const jobs = small ? [JOBS[0], JOBS[2], JOBS[3]] : JOBS;
  const bodyGeo = new THREE.BoxGeometry(0.38, 0.26, 0.72).translate(0, 0.21, 0);
  const cabinGeo = new THREE.BoxGeometry(0.34, 0.2, 0.38).translate(0, 0.44, -0.06);
  const bodies = new THREE.InstancedMesh(bodyGeo, lambert(0xffffff), jobs.length);
  const cabins = new THREE.InstancedMesh(cabinGeo, lambert(COLOR.cabin), jobs.length);
  const vehicles = jobs.map((job, i) => {
    const shape = SHAPE[job.kind];
    const table = pathTable(route(...job.via), 160);
    bodies.setColorAt(i, new THREE.Color(job.colour));
    const exhaust = new THREE.Vector3();
    const emitter = smoke.addEmitter({
      count: small ? 10 : 14, origin: exhaust, life: [0.8, 1.2], size: [0.1, 0.42],
      rise: 0.25, spread: 0.03, alpha: 0.45, drift: 0.4, tints: EXHAUST_TINTS,
    });
    return {
      table,
      at: shuttle({ length: table.length, speed: job.speed, dwell: job.dwell, offset: job.offset }),
      state: { u: 0, dir: 1, turn: 0, moving: false },
      scale: new THREE.Vector3(...shape.scale),
      cabinLocal: new THREE.Matrix4().compose(new THREE.Vector3(...shape.cabin[0]), new THREE.Quaternion(), new THREE.Vector3(...shape.cabin[1])),
      back: shape.back,
      exhaust,
      emitter,
    };
  });
  for (const mesh of [bodies, cabins]) {
    mesh.castShadow = true;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    // Vehicles roam the whole network, so cull and raycast against all of it.
    mesh.boundingSphere = new THREE.Sphere(new THREE.Vector3(-1, 0, 0), 14);
  }
  tag(bodies, 'vehicle');
  tag(cabins, 'vehicle');
  root.add(bodies, cabins);

  function update(time) {
    for (let i = 0; i < vehicles.length; i++) {
      const v = vehicles[i];
      const s = v.at(time, v.state);
      v.table.sample(s.u, _p, _t);
      _t.y = 0;
      _t.normalize();
      // Keep right; while turning round at a stop, cross to the other side of the road.
      const lane = 0.22 * s.dir * (1 - 2 * s.turn);
      _p.x += -_t.z * lane;
      _p.z += _t.x * lane;
      const yaw = shuttleYaw(_t, s);
      _q.setFromAxisAngle(UP, yaw);
      _m.compose(_p, _q, v.scale);
      bodies.setMatrixAt(i, _m);
      _m3.compose(_p, _q, UNIT);
      _m2.multiplyMatrices(_m3, v.cabinLocal);
      cabins.setMatrixAt(i, _m2);
      v.exhaust.set(_p.x - Math.sin(yaw) * v.back, 0.14, _p.z - Math.cos(yaw) * v.back);
      v.emitter.enabled = s.moving;
    }
    bodies.instanceMatrix.needsUpdate = true;
    cabins.instanceMatrix.needsUpdate = true;
  }

  return { update, tables };
}

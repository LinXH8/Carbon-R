/* The airfield on the east side: a short runway, an apron, a small hangar, a control
 * tower with a beacon, a windsock, one plane flying circuits and one parked. Generic
 * shapes: no names, liveries or logos.
 *
 * The circuit is one closed 3D curve sampled into a lookup table - takeoff roll, climb,
 * a wide loop round the world, descent, touchdown - so the plane never needs a state
 * machine and update() allocates nothing.
 */

import * as THREE from 'three';
import { curveTable, lambert, place } from './kit.js';
import { mergeStatic } from './merge.js';

const COLOR = {
  runway: 0x3a3d40,
  marking: 0xd9d9d4,
  apron: 0x5a5d60,
  hangar: 0x8a8f94,
  hangarFloor: 0x2a2c2e,
  tower: 0xb7b1a5,
  glass: 0x2a3034,
  sock: 0xc8743e,
  pole: 0x8a8f94,
  planes: [0xe4e2dc, 0xd8c99a],
  prop: 0x2a2c2e,
};

const EXHAUST_TINTS = [0x9a9da2, 0xa6a9ad, 0xb2b5b8];
const RUNWAY = { x: 11, half: 5.2, width: 1.3 };
const CIRCUIT_SPEED = 3; // world units per second
const CIRCUIT = [
  [11, 0.12, 4.5], [11, 0.12, 0], [11, 0.16, -4], [11, 1.3, -8], [7, 3.2, -11.5], [0, 4.8, -12.5],
  [-7.5, 5.5, -10], [-11, 5.5, -3], [-10.5, 5, 5.5], [-5, 4.3, 11], [4, 3.2, 12.5], [10, 1.7, 10.5], [11, 0.5, 7.5],
];

const _p = new THREE.Vector3();
const _t = new THREE.Vector3();
const _e = new THREE.Euler(0, 0, 0, 'YXZ');

/* A small high-wing prop plane facing +z. The propeller is its own dynamic mesh. */
function makePlane(colour) {
  const plane = new THREE.Group();
  const body = lambert(colour);
  plane.add(place(new THREE.BoxGeometry(0.24, 0.24, 1.4), body, 0, 0, 0));
  plane.add(place(new THREE.BoxGeometry(1.9, 0.04, 0.34), body, 0, 0.14, 0.12));
  plane.add(place(new THREE.BoxGeometry(0.7, 0.03, 0.22), body, 0, 0.02, -0.6));
  plane.add(place(new THREE.BoxGeometry(0.03, 0.3, 0.26), body, 0, 0.17, -0.6));
  plane.add(place(new THREE.BoxGeometry(0.18, 0.16, 0.36), lambert(COLOR.glass), 0, 0.1, 0.28));
  const prop = place(new THREE.BoxGeometry(0.56, 0.05, 0.02), lambert(COLOR.prop), 0, 0, 0.72);
  prop.userData.dynamic = true;
  plane.add(prop);
  mergeStatic(plane);
  plane.rotation.order = 'YXZ';
  return { plane, prop };
}

export function buildAirfield({ smoke, small, statics, root, tag }) {
  // Runway, markings, apron.
  statics.add(place(new THREE.BoxGeometry(RUNWAY.width, 0.04, RUNWAY.half * 2), lambert(COLOR.runway), RUNWAY.x, 0.02, 0, { cast: false, receive: true }));
  const marking = lambert(COLOR.marking);
  const dashGeo = new THREE.BoxGeometry(0.08, 0.01, 0.55);
  for (let i = 0; i < 7; i++) statics.add(place(dashGeo, marking, RUNWAY.x, 0.045, -3.6 + i * 1.2, { cast: false }));
  const barGeo = new THREE.BoxGeometry(0.1, 0.01, 0.42);
  for (const end of [-1, 1]) {
    for (let i = 0; i < 4; i++) {
      statics.add(place(barGeo, marking, RUNWAY.x - 0.45 + i * 0.3, 0.045, end * (RUNWAY.half - 0.35), { cast: false }));
    }
  }
  statics.add(place(new THREE.BoxGeometry(1.9, 0.03, 2.3), lambert(COLOR.apron), 9.4, 0.015, 5.7, { cast: false, receive: true }));

  // Hangar: a half-cylinder vault along x, open toward the runway, closed at the back.
  const vault = new THREE.CylinderGeometry(0.95, 0.95, 2.0, 14, 1, true, 0, Math.PI).rotateZ(Math.PI / 2);
  const hangarMat = lambert(COLOR.hangar, { side: THREE.DoubleSide });
  statics.add(place(vault, hangarMat, 9.3, 0, 3.4));
  const backWall = new THREE.CircleGeometry(0.95, 14, 0, Math.PI).rotateY(-Math.PI / 2);
  statics.add(place(backWall, hangarMat, 8.3, 0, 3.4));
  statics.add(place(new THREE.BoxGeometry(2.0, 0.02, 1.9), lambert(COLOR.hangarFloor), 9.3, 0.01, 3.4, { cast: false }));

  // Control tower with a beacon.
  const towerMat = lambert(COLOR.tower);
  statics.add(place(new THREE.BoxGeometry(0.46, 1.2, 0.46), towerMat, 9.4, 0.6, -1.6));
  statics.add(place(new THREE.BoxGeometry(0.7, 0.36, 0.7), lambert(COLOR.glass), 9.4, 1.38, -1.6));
  statics.add(place(new THREE.BoxGeometry(0.78, 0.06, 0.78), towerMat, 9.4, 1.59, -1.6));
  const beacon = new THREE.MeshBasicMaterial({ color: 0xffffff });
  statics.add(place(new THREE.SphereGeometry(0.07, 6, 4), beacon, 9.4, 1.7, -1.6, { cast: false }));

  // Windsock on a pole; the sock turns with the wind.
  statics.add(place(new THREE.CylinderGeometry(0.025, 0.03, 1.0, 5).translate(0, 0.5, 0), lambert(COLOR.pole), 10.0, 0, -5.6));
  const sock = new THREE.Group();
  sock.userData.dynamic = true;
  sock.position.set(10.0, 0.98, -5.6);
  sock.add(place(new THREE.ConeGeometry(0.08, 0.5, 8, 1, true).rotateX(-Math.PI / 2).translate(0, 0, 0.25), lambert(COLOR.sock, { side: THREE.DoubleSide }), 0, 0, 0));
  root.add(sock);

  // The circuit plane.
  const circuit = curveTable(new THREE.CatmullRomCurve3(CIRCUIT.map((c) => new THREE.Vector3(...c)), true, 'centripetal'), 400);
  const flyer = makePlane(COLOR.planes[0]);
  tag(flyer.plane, 'plane');
  root.add(flyer.plane);
  const exhaust = new THREE.Vector3();
  const exhaustEmitter = smoke.addEmitter({
    count: small ? 30 : 40, origin: exhaust, life: [0.6, 0.9], size: [0.12, 0.45],
    rise: 0.1, spread: 0.03, alpha: 0.4, drift: 0.3, tints: EXHAUST_TINTS,
  });

  // The parked plane, nose out of the apron.
  let parked = null;
  if (!small) {
    parked = makePlane(COLOR.planes[1]);
    parked.plane.position.set(9.4, 0.2, 5.8);
    parked.plane.rotation.y = Math.PI / 2;
    tag(parked.plane, 'plane');
    root.add(parked.plane);
  }

  let lastYaw = 0;
  let bank = 0;
  const beaconOn = new THREE.Color(0xffffff);
  const beaconOff = new THREE.Color(0x3a3a3a);

  function update(time, dt, wind) {
    beacon.color.lerpColors(beaconOff, beaconOn, Math.sin(time * 4.2) > 0.85 ? 1 : 0);
    sock.rotation.y = Math.atan2(wind.x, wind.z);

    circuit.sample((time * CIRCUIT_SPEED) / circuit.length, _p, _t);
    flyer.plane.position.copy(_p);
    flyer.plane.position.y += 0.2;
    // Bank into turns: roll follows the rate of change of heading, smoothed, and levels
    // out near the ground. A left turn (yaw increasing) lowers the left wing.
    const yaw = Math.atan2(_t.x, _t.z);
    const turn = Math.atan2(Math.sin(yaw - lastYaw), Math.cos(yaw - lastYaw));
    lastYaw = yaw;
    const target = _p.y > 0.8 && dt > 0 ? Math.max(-0.6, Math.min(0.6, (-turn / dt) * 1.2)) : 0;
    bank += (target - bank) * Math.min(1, dt * 2.5);
    _e.set(-Math.asin(_t.y), yaw, bank);
    flyer.plane.quaternion.setFromEuler(_e);
    flyer.prop.rotation.z += dt * 30;
    exhaustEmitter.enabled = _p.y > 0.4;
    exhaust.set(_p.x + _t.x * 0.3, _p.y + 0.12, _p.z + _t.z * 0.3);

    if (parked) parked.prop.rotation.z += dt * 6;
  }

  const keepOut = [[9.3, 3.4, 1.6], [9.4, 5.7, 1.5], [9.4, -1.6, 0.8], [10, -5.6, 0.6]];
  // The runway strip, as [xMin, xMax, zMin, zMax], with a margin.
  const keepOutRect = [RUNWAY.x - 1.2, RUNWAY.x + 1.2, -RUNWAY.half - 0.8, RUNWAY.half + 0.8];
  return { update, keepOut, keepOutRect, plane: flyer.plane };
}

/* The airfield in the east: a runway running out toward the sea, an apron, a small hangar,
 * a control tower with a beacon and a windsock. One plane works a trip: it taxis out,
 * lines up, takes off east and leaves the frame; later it comes back in from the east,
 * lands, taxis to the apron and parks with its engine off before the next trip. A second
 * plane stays parked. Generic shapes: no names, liveries or logos.
 */

import * as THREE from 'three';
import { ease, lambert, pathTable, place } from './kit.js';
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
const RUNWAY = { x0: 6.5, x1: 15, z: 3.8, width: 1.2 };
const PARK = [10.2, 1.8];
const WHEELS = 0.2; // the plane's origin sits this far above the ground
const TAXI_SPEED = 0.9;

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
  const prop = place(new THREE.BoxGeometry(0.56, 0.05, 0.02), lambert(COLOR.prop), 0, 0, 0.72, { cast: false });
  prop.userData.dynamic = true;
  plane.add(prop);
  mergeStatic(plane);
  plane.rotation.order = 'YXZ';
  return { plane, prop };
}

export function buildAirfield({ smoke, small, statics, root, tag }) {
  // Runway, markings, apron.
  const runwayLength = RUNWAY.x1 - RUNWAY.x0;
  const runwayX = (RUNWAY.x0 + RUNWAY.x1) / 2;
  statics.add(place(new THREE.BoxGeometry(runwayLength, 0.04, RUNWAY.width), lambert(COLOR.runway), runwayX, 0.02, RUNWAY.z, { cast: false, receive: true }));
  const marking = lambert(COLOR.marking);
  for (let i = 0; i < 6; i++) statics.add(place(new THREE.BoxGeometry(0.55, 0.01, 0.08), marking, RUNWAY.x0 + 1.6 + i * 1.1, 0.045, RUNWAY.z, { cast: false }));
  for (const end of [RUNWAY.x0 + 0.35, RUNWAY.x1 - 0.35]) {
    for (let i = 0; i < 4; i++) statics.add(place(new THREE.BoxGeometry(0.4, 0.01, 0.1), marking, end, 0.045, RUNWAY.z - 0.42 + i * 0.28, { cast: false }));
  }
  statics.add(place(new THREE.BoxGeometry(3.0, 0.03, 1.7), lambert(COLOR.apron), 11.0, 0.015, 1.8, { cast: false, receive: true }));
  statics.add(place(new THREE.BoxGeometry(0.9, 0.03, 1.4), lambert(COLOR.apron), PARK[0], 0.015, 2.9, { cast: false, receive: true }));

  // Hangar: a half-cylinder vault with its open end toward the apron.
  const vault = new THREE.CylinderGeometry(0.95, 0.95, 2.0, 14, 1, true, 0, Math.PI).rotateZ(Math.PI / 2).rotateY(Math.PI / 2);
  const hangarMat = lambert(COLOR.hangar, { side: THREE.DoubleSide });
  statics.add(place(vault, hangarMat, 8.4, 0, 1.2));
  statics.add(place(new THREE.CircleGeometry(0.95, 14, 0, Math.PI), hangarMat, 8.4, 0, 0.2));
  statics.add(place(new THREE.BoxGeometry(1.9, 0.02, 2.0), lambert(COLOR.hangarFloor), 8.4, 0.01, 1.2, { cast: false }));

  // Control tower with a beacon.
  const towerMat = lambert(COLOR.tower);
  statics.add(place(new THREE.BoxGeometry(0.46, 1.2, 0.46), towerMat, 13.8, 0.6, 1.2));
  statics.add(place(new THREE.BoxGeometry(0.7, 0.36, 0.7), lambert(COLOR.glass), 13.8, 1.38, 1.2));
  statics.add(place(new THREE.BoxGeometry(0.78, 0.06, 0.78), towerMat, 13.8, 1.59, 1.2));
  const beacon = new THREE.MeshBasicMaterial({ color: 0xffffff });
  statics.add(place(new THREE.SphereGeometry(0.07, 6, 4), beacon, 13.8, 1.7, 1.2, { cast: false }));

  // Windsock: the sock turns with the wind.
  statics.add(place(new THREE.CylinderGeometry(0.025, 0.03, 1.0, 5).translate(0, 0.5, 0), lambert(COLOR.pole), 15.2, 0, 2.5));
  const sock = new THREE.Group();
  sock.userData.dynamic = true;
  sock.position.set(15.2, 0.98, 2.5);
  sock.add(place(new THREE.ConeGeometry(0.08, 0.5, 8, 1, true).rotateX(-Math.PI / 2).translate(0, 0, 0.25), lambert(COLOR.sock, { side: THREE.DoubleSide }), 0, 0, 0, { cast: false }));
  root.add(sock);

  // The working plane's trip, phase by phase.
  const taxiOut = pathTable([[PARK[0], 0, PARK[1]], [PARK[0], 0, 2.9], [9.0, 0, RUNWAY.z], [RUNWAY.x0 + 0.6, 0, RUNWAY.z]], 48);
  const takeoff = pathTable([[RUNWAY.x0 + 0.6, 0, RUNWAY.z], [13.8, 0, RUNWAY.z], [18, 1.4, RUNWAY.z], [28, 4.8, 3.4], [46, 10, 2.6]], 160);
  const arrival = pathTable([[46, 9, 5.2], [30, 4.6, 4.4], [18.5, 1.2, 3.9], [15, 0, RUNWAY.z], [8.2, 0, RUNWAY.z]], 160);
  const taxiIn = pathTable([[8.2, 0, RUNWAY.z], [9.4, 0, 3.5], [PARK[0], 0, 2.8], [PARK[0], 0, PARK[1]]], 48);
  const taxiTime = (table) => (1.5 * table.length) / TAXI_SPEED;
  const PHASES = [
    { name: 'taxiOut', length: taxiTime(taxiOut) },
    { name: 'lineUp', length: 3 },
    { name: 'takeoff', length: 9 },
    { name: 'away', length: 12 },
    { name: 'arrival', length: 11 },
    { name: 'taxiIn', length: taxiTime(taxiIn) },
    { name: 'parked', length: 10 },
  ];
  const TRIP = PHASES.reduce((n, p) => n + p.length, 0);

  const flyer = makePlane(COLOR.planes[0]);
  tag(flyer.plane, 'plane');
  root.add(flyer.plane);
  const exhaust = new THREE.Vector3();
  const exhaustEmitter = smoke.addEmitter({
    count: small ? 30 : 40, origin: exhaust, life: [0.6, 0.9], size: [0.12, 0.45],
    rise: 0.1, spread: 0.03, alpha: 0.4, drift: 0.3, tints: EXHAUST_TINTS,
  });

  let parked = null;
  if (!small) {
    parked = makePlane(COLOR.planes[1]);
    parked.plane.position.set(12.0, WHEELS, 1.8);
    parked.plane.rotation.y = Math.PI;
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

    // Find the phase.
    let t = (time + 30) % TRIP;
    let i = 0;
    while (t >= PHASES[i].length) t -= PHASES[i++].length;
    const k = t / PHASES[i].length;
    let yaw;
    let spin = 30;
    let running = true;
    flyer.plane.visible = true;
    _t.set(0, 0, 1);
    switch (PHASES[i].name) {
      case 'taxiOut': taxiOut.sample(ease(k), _p, _t); yaw = Math.atan2(_t.x, _t.z); spin = 14; break;
      case 'lineUp': _p.set(RUNWAY.x0 + 0.6, 0, RUNWAY.z); yaw = -Math.PI / 2 + Math.PI * ease(k); spin = 14; break;
      case 'takeoff': takeoff.sample(k * k, _p, _t); yaw = Math.atan2(_t.x, _t.z); break; // accelerating
      case 'away': _p.set(46, 10, 2.6); yaw = Math.PI / 2; flyer.plane.visible = false; running = false; break;
      case 'arrival': arrival.sample(1 - (1 - k) ** 2, _p, _t); yaw = Math.atan2(_t.x, _t.z); break; // slowing
      case 'taxiIn': taxiIn.sample(ease(k), _p, _t); yaw = Math.atan2(_t.x, _t.z); spin = 14; break;
      default: // parked, engine off; turned round for the next trip in its last few seconds
        _p.set(PARK[0], 0, PARK[1]);
        yaw = Math.PI + Math.PI * ease(Math.max(0, (k - 0.7) / 0.3));
        spin = k > 0.7 ? 10 : 0;
        running = k > 0.7;
    }
    flyer.plane.position.set(_p.x, _p.y + WHEELS, _p.z);

    // Bank into turns while airborne: roll follows the rate of change of heading, smoothed.
    // A left turn (yaw increasing) lowers the left wing.
    const turn = Math.atan2(Math.sin(yaw - lastYaw), Math.cos(yaw - lastYaw));
    lastYaw = yaw;
    const airborne = _p.y > 0.6;
    const target = airborne && dt > 0 ? Math.max(-0.6, Math.min(0.6, (-turn / dt) * 1.2)) : 0;
    bank += (target - bank) * Math.min(1, dt * 2.5);
    _e.set(-Math.asin(Math.max(-1, Math.min(1, _t.y))), yaw, bank);
    flyer.plane.quaternion.setFromEuler(_e);
    flyer.prop.rotation.z += dt * spin;
    exhaustEmitter.enabled = running && flyer.plane.visible;
    exhaust.set(_p.x + Math.sin(yaw) * 0.3, _p.y + WHEELS + 0.1, _p.z + Math.cos(yaw) * 0.3);
    if (parked) parked.prop.rotation.z += dt * 2;
  }

  const keepOut = [[8.4, 1.2, 1.5], [11.0, 1.8, 1.9], [13.8, 1.2, 0.8], [15.2, 2.5, 0.6], [PARK[0], 2.9, 1.0]];
  // The runway strip, as [xMin, xMax, zMin, zMax], with a margin.
  const keepOutRect = [RUNWAY.x0 - 0.8, RUNWAY.x1 + 0.8, RUNWAY.z - 1.0, RUNWAY.z + 1.0];
  return { update, keepOut, keepOutRect, plane: flyer.plane };
}

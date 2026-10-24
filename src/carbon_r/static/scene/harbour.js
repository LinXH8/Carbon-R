/* The harbour on the west coast. A container ship sails in from the open sea, docks while
 * two gantry cranes work it, backs off the berth, turns and sails away; a while later the
 * next one comes in. A second ship crosses far out, two fishing boats go out to their
 * grounds and back, and a forklift runs between the quay and the container yard.
 * Box-built, no names, no markings.
 */

import * as THREE from 'three';
import { ease, lambert, mulberry32, pathTable, place, shuttle, shuttleYaw } from './kit.js';
import { waveHeight } from './water.js';
import { mergeStatic } from './merge.js';
import { forkliftJob, makeForklift } from './industry.js';

const COLOR = {
  quay: 0x8e8a82,
  pile: 0x4f4a43,
  wall: 0xa59f92,
  roof: 0x6f7276,
  crane: 0xb08a3e,
  cable: 0x2a2c2e,
  hulls: [0x34465a, 0x5b3a34],
  boat: [0xd8d4ca, 0x4e6a7a],
  deck: 0x6b6e70,
  bridge: 0xe2e0da,
  funnel: 0x3a3d40,
  boxes: [0x8d5a43, 0x5c6f7d, 0xb39f78, 0x6e7f63, 0x9a9da2],
};
const SMOKE_TINTS = [0x8e9196, 0x9ea1a5, 0xadb0b4];
const WAKE_TINTS = [0xd4dcdf, 0xdfe6e8, 0xe9eef0];
const EXHAUST_TINTS = [0x9a9da2, 0xa6a9ad];

const QUAY = { x0: -15.4, x1: -19.6, z: 1.0, width: 1.4, top: 0.12 };
const BERTH = { x: -18.6, z: 2.55 };
const TURN_AT = { x: -25, z: 2.55 };
const CRANE_PERIOD = 18; // s

// The ship's visit, in seconds: in, alongside, astern off the berth, turn, out, away.
const VISIT = { arrive: 26, docked: 28, astern: 8, pivot: 7, depart: 22, away: 12 };
const VISIT_LENGTH = Object.values(VISIT).reduce((a, b) => a + b, 0);

const dip = (t) => ease(Math.min(1, Math.max(0, t < 0.5 ? t / 0.4 : (1 - t) / 0.4)));

const _p = new THREE.Vector3();
const _t = new THREE.Vector3();
const _e = new THREE.Euler(0, 0, 0, 'YXZ');

/* A cargo ship facing +z: hull with a pointed bow, deck, containers, bridge and funnel aft. */
function makeShip(hullColour, rand) {
  const ship = new THREE.Group();
  const plan = new THREE.Shape([
    new THREE.Vector2(-0.4, -1.5), new THREE.Vector2(0.4, -1.5), new THREE.Vector2(0.4, 0.85),
    new THREE.Vector2(0, 1.55), new THREE.Vector2(-0.4, 0.85),
  ]);
  const hullGeo = new THREE.ExtrudeGeometry(plan, { depth: 0.45, bevelEnabled: false }).rotateX(Math.PI / 2).translate(0, 0.25, 0);
  ship.add(place(hullGeo, lambert(hullColour), 0, 0, 0));
  ship.add(place(new THREE.BoxGeometry(0.74, 0.04, 2.3), lambert(COLOR.deck), 0, 0.27, -0.25));
  const boxGeo = new THREE.BoxGeometry(0.3, 0.24, 0.55);
  for (let row = 0; row < 3; row++) {
    for (let col = 0; col < 2; col++) {
      ship.add(place(boxGeo, lambert(COLOR.boxes[Math.floor(rand() * COLOR.boxes.length)]), col * 0.32 - 0.16, 0.41, row * 0.58 - 0.45));
    }
  }
  ship.add(place(new THREE.BoxGeometry(0.66, 0.42, 0.42), lambert(COLOR.bridge), 0, 0.5, -1.15));
  ship.add(place(new THREE.CylinderGeometry(0.09, 0.11, 0.34, 8), lambert(COLOR.funnel), 0, 0.88, -1.25));
  mergeStatic(ship);
  ship.rotation.order = 'YXZ';
  return ship;
}

/* A small fishing boat facing +z. */
function makeBoat(colour) {
  const boat = new THREE.Group();
  const plan = new THREE.Shape([
    new THREE.Vector2(-0.16, -0.42), new THREE.Vector2(0.16, -0.42), new THREE.Vector2(0.16, 0.2),
    new THREE.Vector2(0, 0.46), new THREE.Vector2(-0.16, 0.2),
  ]);
  boat.add(place(new THREE.ExtrudeGeometry(plan, { depth: 0.18, bevelEnabled: false }).rotateX(Math.PI / 2).translate(0, 0.1, 0), lambert(colour), 0, 0, 0));
  boat.add(place(new THREE.BoxGeometry(0.22, 0.16, 0.22), lambert(COLOR.bridge), 0, 0.18, -0.12));
  mergeStatic(boat);
  boat.rotation.order = 'YXZ';
  return boat;
}

/* Sit a vessel on the sea: height from the surface under it, pitch from bow against stern,
   roll from side against side. Forward is (sin yaw, cos yaw). */
function float(vessel, x, z, yaw, time, strength, half, beam) {
  const fx = Math.sin(yaw);
  const fz = Math.cos(yaw);
  const bow = waveHeight(x + fx * half, z + fz * half, time, strength);
  const stern = waveHeight(x - fx * half, z - fz * half, time, strength);
  const left = waveHeight(x + fz * beam, z - fx * beam, time, strength);
  const right = waveHeight(x - fz * beam, z + fx * beam, time, strength);
  vessel.position.set(x, (bow + stern + left + right) / 4, z);
  _e.set(-Math.atan2(bow - stern, 2 * half), yaw, Math.atan2(left - right, 2 * beam));
  vessel.quaternion.setFromEuler(_e);
}

export function buildHarbour({ smoke, small, statics, root, tag }) {
  const rand = mulberry32(7);

  // Quay on piles.
  const length = QUAY.x0 - QUAY.x1;
  statics.add(place(new THREE.BoxGeometry(length, 0.3, QUAY.width), lambert(COLOR.quay), (QUAY.x0 + QUAY.x1) / 2, QUAY.top - 0.15, QUAY.z, { receive: true }));
  const pileGeo = new THREE.CylinderGeometry(0.07, 0.07, 1.2, 6);
  for (let i = 0; i < 4; i++) {
    for (const dz of [-0.55, 0.55]) statics.add(place(pileGeo, lambert(COLOR.pile), QUAY.x0 - 1.0 - i * 1.0, -0.6, QUAY.z + dz, { cast: false }));
  }

  // Warehouse with a pitched roof.
  statics.add(place(new THREE.BoxGeometry(2.4, 1.1, 1.6), lambert(COLOR.wall), -13.4, 0.55, -1.3, { receive: true }));
  const gable = new THREE.Shape([new THREE.Vector2(-0.9, 0), new THREE.Vector2(0.9, 0), new THREE.Vector2(0, 0.5)]);
  statics.add(place(new THREE.ExtrudeGeometry(gable, { depth: 2.5, bevelEnabled: false }).rotateY(Math.PI / 2).translate(-1.25, 0, 0), lambert(COLOR.roof), -13.4, 1.1, -1.3));

  // Container yard.
  const slots = [];
  for (let col = 0; col < 3; col++) {
    for (let row = 0; row < 5; row++) {
      const layers = small ? 1 : 1 + Math.floor(rand() * 2.6);
      for (let layer = 0; layer < layers; layer++) slots.push([-14.6 + col * 0.7, 0.14 + layer * 0.27, 1.9 + row * 0.33]);
    }
  }
  const containers = new THREE.InstancedMesh(new THREE.BoxGeometry(0.62, 0.26, 0.28), lambert(0xffffff), slots.length);
  const m = new THREE.Matrix4();
  slots.forEach(([x, y, z], i) => {
    containers.setMatrixAt(i, m.makeTranslation(x, y, z));
    containers.setColorAt(i, new THREE.Color(COLOR.boxes[Math.floor(rand() * COLOR.boxes.length)]));
  });
  containers.castShadow = true;
  containers.receiveShadow = true;
  root.add(containers);

  // Two gantry cranes on the quay, booms out over the berth.
  const craneMat = lambert(COLOR.crane);
  const cranes = [-17.3, -19.0].map((x, i) => {
    for (const dx of [-0.3, 0.3]) {
      for (const dz of [-0.55, 0.55]) statics.add(place(new THREE.BoxGeometry(0.1, 2.1, 0.1), craneMat, x + dx, QUAY.top + 1.05, QUAY.z + dz));
    }
    const z0 = QUAY.z - 0.8;
    const z1 = BERTH.z + 1.0;
    statics.add(place(new THREE.BoxGeometry(0.75, 0.16, z1 - z0), craneMat, x, QUAY.top + 2.15, (z0 + z1) / 2));
    const trolley = new THREE.Group();
    trolley.userData.dynamic = true;
    trolley.position.set(x, QUAY.top + 2.0, QUAY.z);
    trolley.add(place(new THREE.BoxGeometry(0.5, 0.14, 0.3), craneMat, 0, 0, 0));
    const cable = place(new THREE.BoxGeometry(0.02, 1, 0.02).translate(0, -0.5, 0), lambert(COLOR.cable), 0, 0, 0, { cast: false });
    const spreader = place(new THREE.BoxGeometry(0.66, 0.06, 0.3), craneMat, 0, -0.5, 0, { cast: false });
    cable.scale.y = 0.5;
    trolley.add(cable, spreader);
    root.add(trolley);
    return { trolley, cable, spreader, offset: i * 7 };
  });

  // Yard forklift: quay to stacks.
  const lift = makeForklift();
  tag(lift.group, 'vehicle');
  root.add(lift.group);
  const runLift = forkliftJob(lift, [-15.1, 0.95], [-13.6, 1.5], { offset: 5, dwell: 2.5 });

  // The visiting ship and its route.
  const ship = makeShip(COLOR.hulls[0], rand);
  tag(ship, 'ship');
  root.add(ship);
  const arrive = pathTable([[-62, 0, 24], [-42, 0, 14], [-28, 0, 5], [-21.5, 0, 2.6], [BERTH.x, 0, BERTH.z]], 160);
  const astern = pathTable([[BERTH.x, 0, BERTH.z], [TURN_AT.x, 0, TURN_AT.z]], 32);
  const depart = pathTable([[TURN_AT.x, 0, TURN_AT.z], [-36, 0, -2], [-62, 0, -14]], 120);
  const funnel = new THREE.Vector3();
  const stern = new THREE.Vector3();
  const funnelEmitter = smoke.addEmitter({
    count: small ? 30 : 50, origin: funnel, life: [4, 5.5], size: [0.2, 1.1],
    rise: 0.45, spread: 0.04, alpha: 0.4, drift: 0.9, tints: SMOKE_TINTS,
  });
  const wake = smoke.addEmitter({
    count: small ? 60 : 120, origin: stern, life: [2.5, 3.5], size: [0.25, 1.0],
    rise: 0, lift: 0, spread: 0.15, alpha: 0.5, drift: 0.15, tints: WAKE_TINTS,
  });

  // A ship crossing far out, and two fishing boats going out to their grounds and back.
  let crossing = null;
  const boats = [];
  if (!small) {
    crossing = makeShip(COLOR.hulls[1], rand);
    tag(crossing, 'ship');
    root.add(crossing);
    for (const [i, [gx, gz]] of [[-23, -7], [-22.5, 8.5]].entries()) {
      const boat = makeBoat(COLOR.boat[i]);
      root.add(boat);
      const table = pathTable([[-17.4, 0, -0.6], [(-17.4 + gx) / 2, 0, gz * 0.4 - 0.6], [gx, 0, gz]], 64);
      const exhaust = new THREE.Vector3();
      const emitter = smoke.addEmitter({
        count: 10, origin: exhaust, life: [0.8, 1.2], size: [0.08, 0.3],
        rise: 0.2, spread: 0.02, alpha: 0.35, drift: 0.4, tints: EXHAUST_TINTS,
      });
      boats.push({
        boat, table, exhaust, emitter,
        at: shuttle({ length: table.length, speed: 1.1, dwell: 9, offset: i * 17 }),
        state: { u: 0, dir: 1, turn: 0, moving: false },
      });
    }
  }

  function update(time, dt, wind) {
    const strength = Math.hypot(wind.x, wind.z);

    // Where is the ship in its visit?
    let t = (time + 10) % VISIT_LENGTH;
    let x;
    let z;
    let yaw;
    let moving = true;
    let docked = -1;
    ship.visible = true;
    if (t < VISIT.arrive) {
      arrive.sample(1 - (1 - t / VISIT.arrive) ** 2, _p, _t); // slowing as it comes in
      x = _p.x; z = _p.z; yaw = Math.atan2(_t.x, _t.z);
    } else if ((t -= VISIT.arrive) < VISIT.docked) {
      x = BERTH.x; z = BERTH.z; yaw = Math.PI / 2; moving = false; docked = t;
    } else if ((t -= VISIT.docked) < VISIT.astern) {
      astern.sample(ease(t / VISIT.astern), _p, _t);
      x = _p.x; z = _p.z; yaw = Math.PI / 2;
    } else if ((t -= VISIT.astern) < VISIT.pivot) {
      x = TURN_AT.x; z = TURN_AT.z; yaw = Math.PI / 2 + Math.PI * ease(t / VISIT.pivot); moving = false;
    } else if ((t -= VISIT.pivot) < VISIT.depart) {
      depart.sample((t / VISIT.depart) ** 2, _p, _t); // gathering way
      x = _p.x; z = _p.z; yaw = Math.atan2(_t.x, _t.z);
    } else {
      x = -62; z = -14; yaw = 0; moving = false; ship.visible = false;
    }
    float(ship, x, z, yaw, time, strength, 1.3, 0.4);
    _p.set(0, 1.05, -1.25).applyQuaternion(ship.quaternion);
    funnel.set(x + _p.x, ship.position.y + _p.y, z + _p.z);
    stern.set(x - Math.sin(yaw) * 1.55, ship.position.y + 0.03, z - Math.cos(yaw) * 1.55);
    funnelEmitter.enabled = ship.visible;
    wake.enabled = moving;

    // Cranes work only while the ship is alongside: out, down, up, back, down, up.
    for (let i = 0; i < cranes.length; i++) {
      const c = cranes[i];
      let along = 0;
      let drop = 0.5;
      if (docked >= 0) {
        const k = ((docked + c.offset) % CRANE_PERIOD) / CRANE_PERIOD;
        const toShip = c.trolley.position.y - (ship.position.y + 0.56);
        const toQuay = c.trolley.position.y - (QUAY.top + 0.3);
        if (k < 0.2) along = ease(k / 0.2);
        else if (k < 0.5) { along = 1; drop = 0.5 + (toShip - 0.5) * dip((k - 0.2) / 0.3); }
        else if (k < 0.7) along = 1 - ease((k - 0.5) / 0.2);
        else drop = 0.5 + (toQuay - 0.5) * dip((k - 0.7) / 0.3);
      }
      c.trolley.position.z = QUAY.z + (BERTH.z - QUAY.z) * along;
      c.spreader.position.y = -drop;
      c.cable.scale.y = drop;
    }

    runLift(time);

    if (crossing) {
      const cx = -80 + ((time * 1.3 + 40) % 160);
      float(crossing, cx, -36, Math.PI / 2, time, strength, 1.3, 0.4);
    }
    for (let i = 0; i < boats.length; i++) {
      const b = boats[i];
      const s = b.at(time, b.state);
      b.table.sample(s.u, _p, _t);
      const by = shuttleYaw(_t, s);
      float(b.boat, _p.x, _p.z, by, time, strength, 0.4, 0.15);
      b.exhaust.set(_p.x - Math.sin(by) * 0.4, b.boat.position.y + 0.25, _p.z - Math.cos(by) * 0.4);
      b.emitter.enabled = s.moving;
    }
  }

  const keepOut = [[-13.4, -1.3, 1.8], [-13.9, 2.6, 1.8], [-15, 1, 1.0]];
  return { update, keepOut, ship };
}

/* The harbour on the west shore: a quay on piles, a warehouse, a container yard, a gantry
 * crane, a ship moored at the quay and one sailing round the island. Box-built, no names,
 * no markings.
 *
 * Static pieces go into `statics`, which world.js merges into a couple of draw calls.
 * Moving pieces are marked userData.dynamic and update() moves them without allocating.
 */

import * as THREE from 'three';
import { lambert, mulberry32, place } from './kit.js';
import { waveHeight } from './water.js';
import { mergeStatic } from './merge.js';

const COLOR = {
  quay: 0x8e8a82,
  pile: 0x4f4a43,
  wall: 0xa59f92,
  roof: 0x6f7276,
  crane: 0xb08a3e,
  cable: 0x2a2c2e,
  hulls: [0x34465a, 0x5b3a34],
  deck: 0x6b6e70,
  bridge: 0xe2e0da,
  funnel: 0x3a3d40,
  boxes: [0x8d5a43, 0x5c6f7d, 0xb39f78, 0x6e7f63, 0x9a9da2],
};

const SMOKE_TINTS = [0x8e9196, 0x9ea1a5, 0xadb0b4];
const WAKE_TINTS = [0xd4dcdf, 0xdfe6e8, 0xe9eef0];

const QUAY = { x0: -13.2, x1: -17.6, z: 1.7, width: 1.4, top: 0.12 };
const MOORED = { x: -15.6, z: 3.05 };
const SAIL_RADIUS = 18.8;
const SAIL_SPEED = 0.8; // world units per second

const CRANE_PERIOD = 18; // s

const ease = (t) => t * t * (3 - 2 * t);
// Down and back up over one span, pausing briefly at the bottom.
const dip = (t) => ease(Math.min(1, Math.max(0, t < 0.5 ? t / 0.4 : (1 - t) / 0.4)));

const _p = new THREE.Vector3();
const _e = new THREE.Euler(0, 0, 0, 'YXZ');

/* A ship facing +z: hull with a pointed bow, a deck, cargo, a bridge and a funnel aft. */
function makeShip(hullColour, rand) {
  const ship = new THREE.Group();
  const plan = new THREE.Shape([
    new THREE.Vector2(-0.4, -1.5), new THREE.Vector2(0.4, -1.5), new THREE.Vector2(0.4, 0.85),
    new THREE.Vector2(0, 1.55), new THREE.Vector2(-0.4, 0.85),
  ]);
  // Extruded upward from the plan; the waterline sits a fifth of the way up the hull.
  const hullGeo = new THREE.ExtrudeGeometry(plan, { depth: 0.45, bevelEnabled: false })
    .rotateX(Math.PI / 2).translate(0, 0.25, 0);
  ship.add(place(hullGeo, lambert(hullColour), 0, 0, 0));
  ship.add(place(new THREE.BoxGeometry(0.74, 0.04, 2.3), lambert(COLOR.deck), 0, 0.27, -0.25));
  const boxGeo = new THREE.BoxGeometry(0.3, 0.24, 0.55);
  for (let row = 0; row < 3; row++) {
    for (let col = 0; col < 2; col++) {
      const tint = COLOR.boxes[Math.floor(rand() * COLOR.boxes.length)];
      ship.add(place(boxGeo, lambert(tint), col * 0.32 - 0.16, 0.41, row * 0.58 - 0.45));
    }
  }
  ship.add(place(new THREE.BoxGeometry(0.66, 0.42, 0.42), lambert(COLOR.bridge), 0, 0.5, -1.15));
  ship.add(place(new THREE.CylinderGeometry(0.09, 0.11, 0.34, 8), lambert(COLOR.funnel), 0, 0.88, -1.25));
  mergeStatic(ship);
  return ship;
}

export function buildHarbour({ smoke, small, statics, root, tag }) {
  const rand = mulberry32(7);

  // Quay on piles, reaching from the shore into the water.
  const length = QUAY.x0 - QUAY.x1;
  const quayX = (QUAY.x0 + QUAY.x1) / 2;
  statics.add(place(new THREE.BoxGeometry(length, 0.3, QUAY.width), lambert(COLOR.quay), quayX, QUAY.top - 0.15, QUAY.z, { receive: true }));
  const pileGeo = new THREE.CylinderGeometry(0.07, 0.07, 1.2, 6);
  const pileMat = lambert(COLOR.pile);
  for (let i = 0; i < 4; i++) {
    for (const dz of [-0.55, 0.55]) {
      statics.add(place(pileGeo, pileMat, QUAY.x0 - 1.1 - i * 1.05, -0.6, QUAY.z + dz, { cast: false }));
    }
  }

  // Warehouse with a pitched roof.
  statics.add(place(new THREE.BoxGeometry(2.6, 1.1, 1.6), lambert(COLOR.wall), -11.3, 0.55, 0.0, { receive: true }));
  const gable = new THREE.Shape([new THREE.Vector2(-0.9, 0), new THREE.Vector2(0.9, 0), new THREE.Vector2(0, 0.5)]);
  const roofGeo = new THREE.ExtrudeGeometry(gable, { depth: 2.7, bevelEnabled: false })
    .rotateY(Math.PI / 2).translate(-1.35, 0, 0);
  statics.add(place(roofGeo, lambert(COLOR.roof), -11.3, 1.1, 0.0));

  // Container yard: one instanced mesh, static.
  const slots = [];
  for (let col = 0; col < 4; col++) {
    for (let row = 0; row < 5; row++) {
      const layers = small ? 1 : 1 + Math.floor(rand() * 2.6);
      for (let layer = 0; layer < layers; layer++) slots.push([-12.7 + col * 0.7, 0.14 + layer * 0.27, 2.25 + row * 0.33]);
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

  // Gantry crane: legs and boom are static; the trolley and its hook move.
  const craneX = -16.2;
  const craneMat = lambert(COLOR.crane);
  const legGeo = new THREE.BoxGeometry(0.1, 2.1, 0.1);
  for (const dx of [-0.3, 0.3]) {
    for (const dz of [-0.55, 0.55]) statics.add(place(legGeo, craneMat, craneX + dx, QUAY.top + 1.05, QUAY.z + dz));
  }
  const boomZ0 = QUAY.z - 0.8;
  const boomZ1 = MOORED.z + 1.0;
  statics.add(place(new THREE.BoxGeometry(0.75, 0.16, boomZ1 - boomZ0), craneMat, craneX, QUAY.top + 2.15, (boomZ0 + boomZ1) / 2));
  const trolley = new THREE.Group();
  trolley.userData.dynamic = true;
  trolley.position.set(craneX, QUAY.top + 2.0, QUAY.z);
  trolley.add(place(new THREE.BoxGeometry(0.5, 0.14, 0.3), craneMat, 0, 0, 0));
  const cable = place(new THREE.BoxGeometry(0.02, 1, 0.02).translate(0, -0.5, 0), lambert(COLOR.cable), 0, 0, 0, { cast: false });
  const spreader = place(new THREE.BoxGeometry(0.66, 0.06, 0.3), craneMat, 0, -1, 0);
  trolley.add(cable, spreader);
  root.add(trolley);

  // Ships.
  const moored = makeShip(COLOR.hulls[0], rand);
  moored.rotation.order = 'YXZ';
  tag(moored, 'ship');
  root.add(moored);
  const mooredFunnel = new THREE.Vector3(MOORED.x + 1.25, 0.6, MOORED.z);
  smoke.addEmitter({
    count: small ? 30 : 40, origin: mooredFunnel, life: [4, 5.5], size: [0.2, 1.0],
    rise: 0.45, spread: 0.04, alpha: 0.35, drift: 0.9, tints: SMOKE_TINTS,
  });

  let sailing = null;
  const sailFunnel = new THREE.Vector3();
  const sailStern = new THREE.Vector3();
  if (!small) {
    sailing = makeShip(COLOR.hulls[1], rand);
    tag(sailing, 'ship');
    root.add(sailing);
    smoke.addEmitter({
      count: 60, origin: sailFunnel, life: [4, 5.5], size: [0.2, 1.1],
      rise: 0.45, spread: 0.04, alpha: 0.4, drift: 0.9, tints: SMOKE_TINTS,
    });
    // The wake: white water that stays on the surface, spreading and fading behind.
    smoke.addEmitter({
      count: 120, origin: sailStern, life: [2.5, 3.5], size: [0.25, 1.0],
      rise: 0, lift: 0, spread: 0.15, alpha: 0.5, drift: 0.15, tints: WAKE_TINTS,
    });
  }

  /* Sit a ship on the sea: height from the wave surface under it, pitch from bow against
     stern, roll from one side against the other. Forward is (sin yaw, cos yaw). */
  function float(ship, x, z, yaw, time, strength) {
    const fx = Math.sin(yaw);
    const fz = Math.cos(yaw);
    const bow = waveHeight(x + fx * 1.3, z + fz * 1.3, time, strength);
    const stern = waveHeight(x - fx * 1.3, z - fz * 1.3, time, strength);
    const left = waveHeight(x + fz * 0.4, z - fx * 0.4, time, strength);
    const right = waveHeight(x - fz * 0.4, z + fx * 0.4, time, strength);
    ship.position.set(x, (bow + stern + left + right) / 4, z);
    _e.set(-Math.atan2(bow - stern, 2.6), yaw, Math.atan2(left - right, 0.8));
    ship.quaternion.setFromEuler(_e);
  }

  function update(time, dt, wind) {
    const strength = Math.hypot(wind.x, wind.z);
    // Moored ship, bow out to sea.
    float(moored, MOORED.x, MOORED.z, -Math.PI / 2, time, strength);
    mooredFunnel.set(MOORED.x + 1.25, moored.position.y + 1.05, MOORED.z);

    // Crane: travel out over the ship, lower, lift, travel back, lower onto the quay, lift.
    const c = (time % CRANE_PERIOD) / CRANE_PERIOD;
    const up = 0.5;
    const toShip = trolley.position.y - (moored.position.y + 0.56);
    const toQuay = trolley.position.y - (QUAY.top + 0.3);
    let along;
    let drop;
    if (c < 0.2) { along = ease(c / 0.2); drop = up; }
    else if (c < 0.5) { along = 1; drop = up + (toShip - up) * dip((c - 0.2) / 0.3); }
    else if (c < 0.7) { along = 1 - ease((c - 0.5) / 0.2); drop = up; }
    else { along = 0; drop = up + (toQuay - up) * dip((c - 0.7) / 0.3); }
    trolley.position.z = QUAY.z + (MOORED.z - QUAY.z) * along;
    spreader.position.y = -drop;
    cable.scale.y = drop;

    if (sailing) {
      const angle = 2.1 + (time * SAIL_SPEED) / SAIL_RADIUS;
      const x = SAIL_RADIUS * Math.cos(angle);
      const z = SAIL_RADIUS * Math.sin(angle);
      const yaw = Math.atan2(-Math.sin(angle), Math.cos(angle));
      float(sailing, x, z, yaw, time, strength);
      _p.set(0, 1.05, -1.25).applyQuaternion(sailing.quaternion);
      sailFunnel.set(x + _p.x, sailing.position.y + _p.y, z + _p.z);
      sailStern.set(x - Math.sin(yaw) * 1.55, sailing.position.y + 0.03, z - Math.cos(yaw) * 1.55);
    }
  }

  const keepOut = [[-11.3, 0.0, 1.9], [-11.6, 2.9, 1.7], [-13.6, 1.7, 1.0]];
  return { update, keepOut, moored };
}


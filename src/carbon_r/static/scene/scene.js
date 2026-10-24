/* The landing scene: renderer, camera, loop and lifecycle around world.js and smoke.js.
 *
 * The loop runs only while the tab is visible and the scene is on screen. With
 * prefers-reduced-motion it never runs: the world is simulated forward a few seconds in
 * fixed steps and rendered once, so the smoke is already there in the still.
 *
 * The camera is fixed: only the world moves. It is placed once per size so the whole
 * island fits the frame - turned a quarter so the island's long side runs up a portrait
 * screen - and fog the colour of the page fades the far sea into it, so the water has no
 * edge to see. No controls and no wheel handling, so scrolling the page never fights it.
 */

import * as THREE from 'three';
import { Smoke } from './smoke.js';
import { buildWorld } from './world.js';
import { coastPoint } from './terrain.js';

// Kept exactly this careful: the plant's own emissions are its Scope 1; they become
// Scope 2 only for the business that buys its electricity.
export const LABELS = {
  vehicle: 'Scope 1: fuel burned in vehicles you own',
  factory: 'Scope 1: fuel burned on site',
  plant: 'Scope 2 for the businesses that buy its electricity',
  // A carrier's fuel is its operator's Scope 1; for a business that uses it, Scope 3.
  ship: 'Scope 1 for the operator; Scope 3 for businesses that ship goods on it',
  plane: 'Scope 1 for the operator; Scope 3 for business travel on it',
  freight: 'Scope 1 for the operator; Scope 3 for businesses that ship goods on it',
};

const FOV = 30;
const AZIMUTH = 0.25;           // rad: the view from a little east of south
const ELEVATION = 0.62;        // rad above the horizon; steeper on a portrait screen
const ELEVATION_PORTRAIT = 0.86;
const FIT = { x: 0.97, y: 0.88 }; // how much of the frame the island may fill
const MAX_DT = 1 / 20;         // s: clamp a dropped frame or a resume from pause
const PREWARM = 8;             // s of simulation before the first frame
const TARGET = new THREE.Vector3(0, 0.8, 0.6);
const PAGE = 0x0e0f10;         // the page's ground colour, which the fog fades to
const WATER_LEVEL = -0.45;

// Points that must stay in frame: the coast all round, the quay's end, the tallest things.
const FIT_POINTS = [
  ...Array.from({ length: 36 }, (_, i) => coastPoint((i / 36) * Math.PI * 2)),
  new THREE.Vector3(-19.6, 0, 1), new THREE.Vector3(-7, 5, -7.8), new THREE.Vector3(-2.8, 3.6, -8.2),
  new THREE.Vector3(6, 4.6, 8.6), new THREE.Vector3(12, 4.6, 6.6),
];

/* Wind: one slowly turning vector shared by smoke, clouds, trees and turbines. It blows
   across the frame and a little away from the viewer: wind toward the camera would
   foreshorten every plume into a blob. Screen-right for a camera at azimuth a is -a. */
function windAt(time, azimuth, out) {
  const heading = -azimuth - 0.35 + 0.25 * Math.sin(time * 0.05) + 0.1 * Math.sin(time * 0.13 + 1.3);
  const strength = 0.55 + 0.15 * Math.sin(time * 0.09 + 0.4);
  out.x = Math.cos(heading) * strength;
  out.z = Math.sin(heading) * strength;
  return out;
}

export function mountScene(container, { small = false, showStats = false } = {}) {
  const canvas = document.createElement('canvas');
  container.prepend(canvas);
  const label = container.querySelector('.scene-label');

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);
  renderer.shadowMap.enabled = !small;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  // Reset by hand before each frame: three's own reset comes after the shadow pass, which
  // would leave the shadow draws out of the reported count.
  renderer.info.autoReset = false;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(FOV, 1, 1, 300);
  const smoke = new Smoke(small ? 500 : 1000);
  const world = buildWorld({ smoke, small, shadows: !small });
  scene.add(world.root, smoke.points);

  scene.fog = new THREE.Fog(PAGE, 60, 120);

  const wind = { x: 0, z: 0 };
  let azimuth = AZIMUTH;
  let time = 0;

  function advance(dt) {
    time += dt;
    windAt(time, azimuth, wind);
    world.update(time, dt, wind);
    smoke.update(dt, wind);
  }

  /* Place the camera at `distance` along the current view direction. */
  function aim(distance, elevation) {
    camera.position.set(
      TARGET.x + distance * Math.cos(elevation) * Math.sin(azimuth),
      TARGET.y + distance * Math.sin(elevation),
      TARGET.z + distance * Math.cos(elevation) * Math.cos(azimuth),
    );
    camera.lookAt(TARGET);
    camera.updateMatrixWorld();
  }

  /* The nearest distance at which every fit point lands inside the frame. */
  function fit(elevation) {
    const v = new THREE.Vector3();
    let near = 5;
    let far = 300;
    for (let i = 0; i < 30; i++) {
      const mid = (near + far) / 2;
      aim(mid, elevation);
      const inside = FIT_POINTS.every((p) => {
        v.copy(p).project(camera);
        return Math.abs(v.x) <= FIT.x && Math.abs(v.y) <= FIT.y;
      });
      if (inside) far = mid; else near = mid;
    }
    return far;
  }

  /* --- size --------------------------------------------------------------------------- */

  function resize() {
    const { width, height } = container.getBoundingClientRect();
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    const portrait = camera.aspect < 1;
    azimuth = portrait ? AZIMUTH + Math.PI / 2 : AZIMUTH;
    const elevation = portrait ? ELEVATION_PORTRAIT : ELEVATION;
    const distance = fit(elevation);
    aim(distance, elevation);
    // Fog: clear out to the island's farthest point, fully faded where the top edge of the
    // frame meets the sea, so the water dissolves into the page instead of ending.
    let farthest = 0;
    for (const p of FIT_POINTS) farthest = Math.max(farthest, camera.position.distanceTo(p));
    const below = elevation - THREE.MathUtils.degToRad(FOV) / 2; // the top edge's angle down
    const horizon = below > 0.02 ? (camera.position.y - WATER_LEVEL) / Math.sin(below) : farthest * 2;
    scene.fog.near = farthest * 1.02;
    scene.fog.far = Math.max(horizon, farthest * 1.1);
    smoke.setViewport(renderer.getDrawingBufferSize(new THREE.Vector2()).y, FOV);
    if (!running) draw();
  }

  /* --- stats (?stats): fps, frame time, draw calls ---------------------------------- */

  const WINDOW = 120;
  const frameTimes = new Float32Array(WINDOW);
  const workTimes = new Float32Array(WINDOW);
  const sorted = new Float32Array(WINDOW);
  let frames = 0;
  let statsEl = null;
  const stats = { frames: 0, fps: 0, avgMs: 0, p95Ms: 0, workAvgMs: 0, workP95Ms: 0, calls: 0, triangles: 0, points: smoke.capacity };
  if (showStats) {
    statsEl = document.createElement('div');
    statsEl.style.cssText = 'position:fixed;left:8px;bottom:8px;z-index:9;padding:6px 8px;background:#16181a;color:#e8e9ea;font:12px/1.4 ui-sans-serif,system-ui;font-variant-numeric:tabular-nums;border-radius:4px;white-space:pre;pointer-events:none';
    document.body.append(statsEl);
    window.__sceneStats = stats;
    window.__sceneDebug = { camera, ...world.debug };
  }
  function record(ms, workMs) {
    frameTimes[frames % WINDOW] = ms;
    workTimes[frames % WINDOW] = workMs;
    frames++;
    stats.frames = frames;
    stats.calls = renderer.info.render.calls;
    stats.triangles = renderer.info.render.triangles;
    if (frames % 30 || frames < WINDOW) return;
    sorted.set(frameTimes);
    sorted.sort();
    let sum = 0;
    for (let i = 0; i < WINDOW; i++) sum += frameTimes[i];
    stats.avgMs = sum / WINDOW;
    stats.p95Ms = sorted[Math.floor(WINDOW * 0.95)];
    stats.fps = 1000 / stats.avgMs;
    sorted.set(workTimes);
    sorted.sort();
    sum = 0;
    for (let i = 0; i < WINDOW; i++) sum += workTimes[i];
    stats.workAvgMs = sum / WINDOW;
    stats.workP95Ms = sorted[Math.floor(WINDOW * 0.95)];
    if (statsEl) {
      statsEl.textContent =
        `${stats.fps.toFixed(1)} fps   frame avg ${stats.avgMs.toFixed(2)} ms   p95 ${stats.p95Ms.toFixed(2)} ms\n` +
        `work avg ${stats.workAvgMs.toFixed(2)} ms   p95 ${stats.workP95Ms.toFixed(2)} ms\n` +
        `${stats.calls} draw calls   ${stats.triangles} triangles   ${stats.points} particles`;
    }
  }

  /* --- the loop ----------------------------------------------------------------------- */

  let running = false;
  let last = 0;

  function draw() {
    renderer.info.reset();
    renderer.render(scene, camera);
    if (!('ready' in container.dataset)) container.dataset.ready = '';
  }

  function tick(now) {
    const ms = now - last;
    last = now;
    const begin = performance.now();
    advance(Math.min(ms / 1000, MAX_DT));
    draw();
    record(ms, performance.now() - begin);
  }

  const reduceQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  let visible = !document.hidden;
  let onscreen = true;

  function sync() {
    const want = visible && onscreen && !reduceQuery.matches;
    if (want === running) return;
    running = want;
    if (running) {
      last = performance.now();
      renderer.setAnimationLoop(tick);
    } else {
      renderer.setAnimationLoop(null);
    }
  }

  const onVisibility = () => { visible = !document.hidden; sync(); };
  const onReduce = () => { sync(); if (!running) draw(); };
  const io = new IntersectionObserver(([entry]) => { onscreen = entry.isIntersecting; sync(); });
  const ro = new ResizeObserver(resize);

  /* --- pointer: hover labels (fine pointers only) ----------------------------------- */

  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  const hits = [];
  let lastHover = 0;

  function hideLabel() { delete label.dataset.show; }

  function onPointerMove(event) {
    if (event.pointerType !== 'mouse' || !finePointer.matches || event.timeStamp - lastHover < 50) return;
    lastHover = event.timeStamp;

    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (x < 0 || y < 0 || x > rect.width || y > rect.height) return hideLabel();
    ndc.set((x / rect.width) * 2 - 1, -(y / rect.height) * 2 + 1);
    raycaster.setFromCamera(ndc, camera);
    hits.length = 0;
    raycaster.intersectObjects(world.hoverTargets, true, hits);
    const kind = hits.length ? hits[0].object.userData.kind : null;
    if (!kind) return hideLabel();
    label.textContent = LABELS[kind];
    const left = Math.min(x + 14, rect.width - label.offsetWidth - 8);
    label.style.transform = `translate(${Math.max(8, left)}px, ${Math.max(8, y - 38)}px)`;
    label.dataset.show = '';
  }

  /* --- start ------------------------------------------------------------------------ */

  // Size first (it places the camera, which sets the wind's heading), then simulate forward
  // in fixed steps so the first frame already has smoke in the air.
  resize();
  for (let t = 0; t < PREWARM; t += 1 / 30) advance(1 / 30);
  draw();

  document.addEventListener('visibilitychange', onVisibility);
  reduceQuery.addEventListener('change', onReduce);
  window.addEventListener('pointermove', onPointerMove, { passive: true });
  container.addEventListener('pointerleave', hideLabel);
  io.observe(container);
  ro.observe(container);
  sync();

  return {
    dispose() {
      renderer.setAnimationLoop(null);
      running = false;
      io.disconnect();
      ro.disconnect();
      document.removeEventListener('visibilitychange', onVisibility);
      reduceQuery.removeEventListener('change', onReduce);
      window.removeEventListener('pointermove', onPointerMove);
      container.removeEventListener('pointerleave', hideLabel);
      scene.traverse((object) => {
        object.geometry?.dispose();
        const materials = Array.isArray(object.material) ? object.material : [object.material];
        for (const material of materials) material?.dispose();
        object.dispose?.();
      });
      renderer.dispose();
      renderer.forceContextLoss();
      canvas.remove();
      statsEl?.remove();
      hideLabel();
      delete container.dataset.ready;
    },
  };
}

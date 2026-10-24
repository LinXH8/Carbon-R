/* The landing scene: renderer, camera, loop and lifecycle around world.js and smoke.js.
 *
 * The loop runs only while the tab is visible and the scene is on screen. With
 * prefers-reduced-motion it never runs: the world is simulated forward a few seconds in
 * fixed steps and rendered once, so the smoke is already there in the still.
 *
 * The camera orbits on its own and leans a little toward the pointer through a critically
 * damped spring (Apple's response/damping mapping: stiffness = (2PI / duration)^2,
 * damping = 4PI * ratio / duration), so the parallax has weight instead of snapping. There
 * are no orbit controls and no wheel handling, so scrolling the page never fights it.
 */

import * as THREE from 'three';
import { Smoke } from './smoke.js';
import { buildWorld } from './world.js';

// Kept exactly this careful: the plant's own emissions are its Scope 1; they become
// Scope 2 only for the business that buys its electricity.
export const LABELS = {
  vehicle: 'Scope 1: fuel burned in vehicles you own',
  factory: 'Scope 1: fuel burned on site',
  plant: 'Scope 2 for the businesses that buy its electricity',
};

const FOV = 30;
const FRAME_RADIUS = 10.5;     // world units kept in view vertically
const FRAME_WIDTH = 12.6;      // and horizontally: the whole water ring
const ELEVATION = 0.5;         // rad above the horizon; steeper on a portrait screen,
const ELEVATION_PORTRAIT = 0.78; // where a tall frame suits a more top-down view
const ORBIT_SPEED = 0.03;      // rad/s
const PARALLAX = { x: 0.16, y: 0.05 };
const MAX_DT = 1 / 20;         // s: clamp a dropped frame or a resume from pause
const PREWARM = 8;             // s of simulation before the first frame
const TARGET = new THREE.Vector3(0, 1.1, 0.4);

function spring(duration, ratio = 1) {
  return {
    value: 0, velocity: 0, target: 0,
    stiffness: ((2 * Math.PI) / duration) ** 2,
    damping: (4 * Math.PI * ratio) / duration,
  };
}
function stepSpring(s, dt) {
  const accel = -s.stiffness * (s.value - s.target) - s.damping * s.velocity;
  s.velocity += accel * dt;
  s.value += s.velocity * dt;
}

/* Wind: one slowly turning vector shared by smoke, clouds and trees. It blows across the
   frame and a little away from the viewer, turning with the orbit: wind toward the camera
   foreshortens every plume into a blob. Screen-right for a camera at azimuth a is -a. */
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
  const camera = new THREE.PerspectiveCamera(FOV, 1, 1, 200);
  const smoke = new Smoke(small ? 320 : 640);
  const world = buildWorld({ smoke, small, shadows: !small });
  scene.add(world.root, smoke.points);

  const wind = { x: 0, z: 0 };
  const lean = { x: spring(1.4), y: spring(1.4) };
  let distance = 40;
  let baseElevation = ELEVATION;
  let time = 0;

  function advance(dt) {
    time += dt;
    stepSpring(lean.x, dt);
    stepSpring(lean.y, dt);
    const azimuth = 0.35 + time * ORBIT_SPEED + lean.x.value;
    windAt(time, azimuth, wind);
    world.update(time, dt, wind);
    smoke.update(dt, wind);
    const elevation = baseElevation + lean.y.value;
    camera.position.set(
      TARGET.x + distance * Math.cos(elevation) * Math.sin(azimuth),
      TARGET.y + distance * Math.sin(elevation),
      TARGET.z + distance * Math.cos(elevation) * Math.cos(azimuth),
    );
    camera.lookAt(TARGET);
  }

  /* --- size --------------------------------------------------------------------------- */

  function resize() {
    const { width, height } = container.getBoundingClientRect();
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    const vfov = THREE.MathUtils.degToRad(FOV);
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
    distance = Math.max(FRAME_RADIUS / Math.sin(vfov / 2), FRAME_WIDTH / Math.sin(hfov / 2));
    baseElevation = camera.aspect < 1 ? ELEVATION_PORTRAIT : ELEVATION;
    camera.updateProjectionMatrix();
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

  /* --- pointer: parallax and hover labels (fine pointers only) ---------------------- */

  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  const hits = [];
  let lastHover = 0;

  function hideLabel() { delete label.dataset.show; }

  function onPointerMove(event) {
    if (event.pointerType !== 'mouse') return;
    lean.x.target = -((event.clientX / window.innerWidth) * 2 - 1) * PARALLAX.x;
    lean.y.target = ((event.clientY / window.innerHeight) * 2 - 1) * PARALLAX.y;
    if (!finePointer.matches || event.timeStamp - lastHover < 50) return;
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

  // Simulate forward in fixed steps so the first frame already has smoke in the air.
  for (let t = 0; t < PREWARM; t += 1 / 30) advance(1 / 30);
  resize();
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

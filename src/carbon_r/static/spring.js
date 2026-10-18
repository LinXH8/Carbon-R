/* A small spring, because the audit sheet must be grabbable mid-flight.
 *
 * CSS transitions and @keyframes cannot be interrupted smoothly: they interpolate from
 * where they were told to start, so grabbing a closing sheet makes it jump. A spring
 * always animates from its *current* value and can carry its current velocity through a
 * re-target, which is exactly what an interruptible gesture needs.
 *
 * The parameters are Apple's two, not the physics triplet:
 *
 *   bounce    0 is critically damped (no overshoot); ~0.2 is a slight, physical overshoot.
 *             Damping ratio is 1 - bounce.
 *   duration  how quickly the value reaches the target, in seconds. This is NOT a
 *             duration in the CSS sense - a spring has no fixed end; settle time emerges.
 *
 * Mapped to a unit-mass spring the way SwiftUI's Spring(response:dampingRatio:) does:
 *   stiffness = (2PI / duration)^2        damping = 4PI * dampingRatio / duration
 */

const REDUCE_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)');

const REST_VALUE = 0.4;      // px: closer than the eye resolves on a 1x display
const REST_VELOCITY = 12;    // px/s
const MAX_STEP = 1 / 30;     // s: clamp dt so a dropped frame cannot explode the integrator

export class Spring {
  constructor({ value = 0, bounce = 0, duration = 0.35, onUpdate, onRest } = {}) {
    this.value = value;
    this.velocity = 0;
    this.target = value;
    this.onUpdate = onUpdate || (() => {});
    this.onRest = onRest || (() => {});
    this._setParams(bounce, duration);
    this._frame = null;
    this._last = 0;
  }

  _setParams(bounce, duration) {
    const ratio = Math.max(0, Math.min(1, 1 - bounce));
    this.stiffness = (2 * Math.PI / duration) ** 2;
    this.damping = 4 * Math.PI * ratio / duration;
  }

  /* Jump. Used for layout sync, and for every move under prefers-reduced-motion. */
  set(value) {
    this.stop();
    this.value = value;
    this.velocity = 0;
    this.target = value;
    this.onUpdate(value);
    this.onRest(value);
  }

  /* Animate to a new target FROM THE CURRENT VALUE, optionally inheriting a velocity.
   *
   * Velocity is assigned rather than replaced-by-zero on purpose: a gesture that reverses
   * mid-flight should keep its momentum through the re-target. Hard-cutting it to zero is
   * the "brick wall" that makes a reversal feel like hitting something. */
  retarget(target, { velocity, bounce, duration } = {}) {
    if (bounce !== undefined || duration !== undefined) {
      this._setParams(bounce ?? 0, duration ?? 0.35);
    }
    this.target = target;
    if (velocity !== undefined) this.velocity = velocity;

    if (REDUCE_MOTION.matches) {
      this.set(target);     // a gentler, non-vestibular equivalent: no travel at all
      return;
    }
    this._start();
  }

  /* Hand the spring a value from a drag: position is authoritative, velocity comes from
   * the pointer, and no integration happens while the finger is down. */
  track(value, velocity = 0) {
    this.stop();
    this.value = value;
    this.velocity = velocity;
    this.onUpdate(value);
  }

  stop() {
    if (this._frame !== null) cancelAnimationFrame(this._frame);
    this._frame = null;
  }

  get animating() {
    return this._frame !== null;
  }

  _start() {
    if (this._frame !== null) return;        // already running; the new target is enough
    this._last = performance.now();
    this._frame = requestAnimationFrame(this._tick);
  }

  _tick = (now) => {
    const dt = Math.min((now - this._last) / 1000, MAX_STEP);
    this._last = now;

    // Semi-implicit Euler: stable at these stiffnesses and trivial to read.
    const displacement = this.value - this.target;
    const acceleration = -this.stiffness * displacement - this.damping * this.velocity;
    this.velocity += acceleration * dt;
    this.value += this.velocity * dt;

    if (Math.abs(this.value - this.target) < REST_VALUE && Math.abs(this.velocity) < REST_VELOCITY) {
      this.value = this.target;
      this.velocity = 0;
      this._frame = null;
      this.onUpdate(this.value);
      this.onRest(this.value);
      return;
    }

    this.onUpdate(this.value);
    this._frame = requestAnimationFrame(this._tick);
  };
}

/* Where a flick would come to rest, so a sheet snaps to where the gesture was GOING
 * rather than to whatever happened to be nearest when the finger lifted.
 *
 * This is Apple's exponential-decay projection from the Designing Fluid Interfaces sample
 * code - not the textbook v^2/(2a), which lands somewhere else entirely. */
export function project(velocity, decelerationRate = 0.998) {
  return (velocity / 1000) * decelerationRate / (1 - decelerationRate);
}

/* Progressive resistance past a boundary. A hard stop reads as frozen; resistance reads
 * as "you can keep pulling, but there is nothing more here". */
export function rubberband(overshoot, dimension, constant = 0.55) {
  return (overshoot * dimension * constant) / (dimension + constant * Math.abs(overshoot));
}

/* The nearest snap point to a projected landing position. */
export function nearest(value, points) {
  return points.reduce((best, p) => (Math.abs(p - value) < Math.abs(best - value) ? p : best));
}

/* A few pointermoves of history is enough for a release velocity, and far steadier than
 * the last single delta - which is often zero because the finger paused before lifting. */
export class VelocityTracker {
  constructor(window = 100) {
    this.window = window;
    this.samples = [];
  }

  add(value) {
    const now = performance.now();
    this.samples.push({ value, at: now });
    while (this.samples.length > 2 && now - this.samples[0].at > this.window) {
      this.samples.shift();
    }
  }

  get velocity() {
    if (this.samples.length < 2) return 0;
    const first = this.samples[0];
    const last = this.samples[this.samples.length - 1];
    const seconds = (last.at - first.at) / 1000;
    return seconds > 0 ? (last.value - first.value) / seconds : 0;
  }

  reset() {
    this.samples = [];
  }
}

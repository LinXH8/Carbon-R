/* Bake a group's static meshes into as few draw calls as possible.
 *
 * Every plain Mesh under `group` is merged unless it, or an ancestor below `group`, is
 * marked `userData.dynamic` (it moves on its own), it is instanced, or it has several
 * materials. Flat Lambert meshes - nearly everything here - are merged across colours: each
 * one's colour is written into a vertex colour attribute and they share one vertex-coloured
 * material per (side, shadow flags). Anything else (the blinking beacons) is merged per
 * material and keeps that material, so changing its colour still works.
 *
 * Geometries are normalised before merging: non-indexed, the mesh's transform relative to
 * the group baked in, `uv` dropped (nothing here is textured), a colour attribute added
 * where needed. After that every geometry in a bucket must carry exactly the same
 * attributes with the same item sizes; if one does not, this throws rather than merging
 * something that would render wrongly.
 *
 * Runs once, before the first frame, so nothing has been uploaded to the GPU yet and the
 * replaced geometries are simply dropped.
 */

import * as THREE from 'three';

const _rel = new THREE.Matrix4();
const _inv = new THREE.Matrix4();

function isStatic(mesh, group) {
  for (let o = mesh; o && o !== group; o = o.parent) if (o.userData.dynamic) return false;
  return true;
}

function normalise(mesh, group, vertexColour) {
  let geometry = mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry.clone();
  geometry.deleteAttribute('uv');
  geometry.deleteAttribute('uv1');
  if (!geometry.getAttribute('normal')) geometry.computeVertexNormals();
  _rel.multiplyMatrices(_inv, mesh.matrixWorld);
  geometry.applyMatrix4(_rel);
  if (vertexColour) {
    const { r, g, b } = mesh.material.color; // linear, which is what vertex colours expect
    const count = geometry.getAttribute('position').count;
    const colours = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) colours.set([r, g, b], i * 3);
    geometry.setAttribute('color', new THREE.BufferAttribute(colours, 3));
  }
  return geometry;
}

function concatenate(entries) {
  const [first] = entries;
  const names = Object.keys(first.geometry.attributes).sort();
  for (const { geometry, mesh } of entries) {
    const own = Object.keys(geometry.attributes).sort();
    const label = mesh.name || `${mesh.geometry.type} under ${mesh.parent?.name || mesh.parent?.type}`;
    if (own.join() !== names.join()) {
      const missing = names.filter((n) => !own.includes(n));
      const extra = own.filter((n) => !names.includes(n));
      throw new Error(
        `merge.js: ${label} does not match its bucket's attributes ` +
        `(missing: ${missing.join(', ') || 'none'}; extra: ${extra.join(', ') || 'none'})`,
      );
    }
    for (const name of names) {
      if (geometry.attributes[name].itemSize !== first.geometry.attributes[name].itemSize) {
        throw new Error(`merge.js: ${label} has attribute "${name}" with a different item size`);
      }
    }
  }
  const merged = new THREE.BufferGeometry();
  for (const name of names) {
    const itemSize = first.geometry.attributes[name].itemSize;
    const total = entries.reduce((n, e) => n + e.geometry.attributes[name].count, 0);
    const array = new Float32Array(total * itemSize);
    let offset = 0;
    for (const { geometry } of entries) {
      array.set(geometry.attributes[name].array, offset);
      offset += geometry.attributes[name].array.length;
    }
    merged.setAttribute(name, new THREE.BufferAttribute(array, itemSize));
  }
  merged.computeBoundingSphere();
  return merged;
}

/** Merge `group`'s static meshes in place. Returns how many meshes became how many. */
export function mergeStatic(group) {
  group.updateMatrixWorld(true);
  _inv.copy(group.matrixWorld).invert();

  const buckets = new Map();
  const sources = [];
  group.traverse((o) => {
    if (!o.isMesh || o.isInstancedMesh || Array.isArray(o.material) || !isStatic(o, group)) return;
    sources.push(o);
  });

  for (const mesh of sources) {
    const m = mesh.material;
    // A material with its own shader patch keeps its own material.
    const patched = m.onBeforeCompile !== THREE.Material.prototype.onBeforeCompile;
    const vertexColour = m.isMeshLambertMaterial && !m.vertexColors && !patched;
    const shadow = `${mesh.castShadow}|${mesh.receiveShadow}|${mesh.userData.kind || ''}`;
    const key = vertexColour ? `lambert|${m.side}|${shadow}` : `${m.uuid}|${shadow}`;
    if (!buckets.has(key)) buckets.set(key, { vertexColour, material: m, side: m.side, mesh, entries: [] });
    buckets.get(key).entries.push({ mesh, geometry: normalise(mesh, group, vertexColour) });
  }

  for (const bucket of buckets.values()) {
    const material = bucket.vertexColour
      ? new THREE.MeshLambertMaterial({ vertexColors: true, flatShading: true, side: bucket.side })
      : bucket.material;
    const merged = new THREE.Mesh(concatenate(bucket.entries), material);
    merged.castShadow = bucket.mesh.castShadow;
    merged.receiveShadow = bucket.mesh.receiveShadow;
    if (bucket.mesh.userData.kind) merged.userData.kind = bucket.mesh.userData.kind;
    group.add(merged);
  }
  for (const mesh of sources) mesh.removeFromParent();
  return { from: sources.length, to: buckets.size };
}

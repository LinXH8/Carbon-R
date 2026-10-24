/* Carbon R - landing page bootstrap.
 *
 * The scene is decoration, so nothing here may break the page. three needs WebGL2; if it
 * is missing, or the CDN import fails, or the renderer throws, the page keeps its flat
 * fallback field and the Start link works exactly the same.
 */

const container = document.getElementById('scene');
const showStats = new URLSearchParams(location.search).has('stats');
let handle = null;

function hasWebGL2() {
  try {
    const gl = document.createElement('canvas').getContext('webgl2');
    gl?.getExtension('WEBGL_lose_context')?.loseContext();
    return Boolean(gl);
  } catch {
    return false;
  }
}

function fallback() {
  document.documentElement.classList.add('no-webgl');
}

async function start() {
  if (handle) return;
  if (!hasWebGL2()) return fallback();
  try {
    const { mountScene } = await import('/static/scene/scene.js');
    handle = mountScene(container, {
      small: window.matchMedia('(max-width: 767px)').matches,
      showStats,
    });
  } catch (error) {
    console.warn('Carbon R: scene unavailable, showing the flat fallback.', error);
    fallback();
  }
}

// pagehide rather than unload, so the page stays eligible for the back/forward cache; a
// restored page gets a fresh scene, since the old context was released.
window.addEventListener('pagehide', () => {
  handle?.dispose();
  handle = null;
});
window.addEventListener('pageshow', (event) => {
  if (event.persisted) start();
});

start();

/* Light/dark theme toggle.
 *
 * The inline script in base.html sets data-bs-theme on <html> before first paint.
 * This file wires up the navbar button, saves the choice in localStorage, follows the
 * OS setting until the user picks one, and recolours Chart.js charts to match.
 */
(() => {
  const root = document.documentElement;
  const KEY = 'theme';
  const store = {
    get() { try { return localStorage.getItem(KEY); } catch (e) { return null; } },
    set(v) { try { localStorage.setItem(KEY, v); } catch (e) { /* private mode: theme just won't persist */ } },
  };
  const systemDark = matchMedia('(prefers-color-scheme: dark)');
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const button = document.getElementById('theme-toggle');
  const isDark = () => root.getAttribute('data-bs-theme') === 'dark';

  // Sets obj.a.b.c = value, creating missing levels.
  const setPath = (obj, path, value) => {
    let o = obj;
    path.slice(0, -1).forEach(k => { o = o[k] ??= {}; });
    o[path[path.length - 1]] = value;
  };

  function styleCharts() {
    if (!window.Chart) return;
    const dark = isDark();
    const text = dark ? '#94a3b8' : '#64748b';
    const line = dark ? 'rgba(148, 163, 184, .16)' : 'rgba(15, 23, 42, .08)';
    // Defaults cover charts created later on this page...
    Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
    Chart.defaults.color = text;
    Chart.defaults.borderColor = line;
    // ...but existing charts cache resolved defaults, so write the colours into their own options.
    Object.values(Chart.instances).forEach(chart => {
      const opts = chart.config.options;
      Object.keys(chart.scales).forEach(id => {
        setPath(opts, ['scales', id, 'ticks', 'color'], text);
        setPath(opts, ['scales', id, 'title', 'color'], text);
        setPath(opts, ['scales', id, 'grid', 'color'], line);
        setPath(opts, ['scales', id, 'border', 'color'], line);
      });
      setPath(opts, ['plugins', 'legend', 'labels', 'color'], text);
      chart.update('none');
    });
  }

  function syncButton() {
    if (!button) return;
    const dark = isDark();
    button.setAttribute('aria-pressed', String(dark));
    button.title = dark ? 'Switch to light mode' : 'Switch to dark mode';
  }

  let fadeTimer;
  function apply(theme, animate) {
    if (animate && !reduceMotion.matches) {
      root.classList.add('theme-fade');
      clearTimeout(fadeTimer);
      fadeTimer = setTimeout(() => root.classList.remove('theme-fade'), 400);
    }
    root.setAttribute('data-bs-theme', theme);
    syncButton();
    styleCharts();
  }

  button?.addEventListener('click', () => {
    const next = isDark() ? 'light' : 'dark';
    store.set(next);
    apply(next, true);
    // Small GSAP spin on top of the CSS sun/moon morph.
    if (window.gsap && !reduceMotion.matches) {
      gsap.fromTo(button.querySelector('.theme-icon'), { rotation: next === 'dark' ? -60 : 60 },
        { rotation: 0, duration: 0.5, ease: 'back.out(2)', transformOrigin: '50% 50%', overwrite: true });
    }
  });

  // Follow OS changes only while the user hasn't chosen a theme themselves.
  systemDark.addEventListener('change', e => { if (!store.get()) apply(e.matches ? 'dark' : 'light', true); });

  syncButton();
  styleCharts();   // runs before page scripts create their charts, so they start with the right colours
})();

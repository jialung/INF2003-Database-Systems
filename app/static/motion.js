/* Page motion with GSAP + ScrollTrigger.
 *
 * - Intro timeline: brand mark draws itself, nav items and above-the-fold content stagger in.
 * - Content below the fold is revealed with ScrollTrigger.batch() as it scrolls into view.
 * - Desktop nav gets a sliding indicator driven by gsap.quickTo().
 * - Everything runs inside gsap.matchMedia(), so prefers-reduced-motion users get a static page.
 *
 * Exposes window.Motion.rows(elements) so pages that build rows in JS (Nearby) can animate them.
 */
(() => {
  const root = document.documentElement;
  const reveal = () => root.classList.remove('motion');
  const Motion = { rows: () => {} };
  window.Motion = Motion;

  if (!window.gsap) { reveal(); return; }
  const hasST = !!window.ScrollTrigger;
  if (hasST) gsap.registerPlugin(ScrollTrigger);
  gsap.defaults({ duration: 0.6, ease: 'power3.out' });

  const main = document.querySelector('main');
  // Blocks that animate as one unit. Nested matches are skipped so a card moves as a whole.
  const UNIT = 'h1, h2, h3, p, form, ul, .card, .map, canvas, table, .alert, .btn';
  const MAX_ROWS = 150;   // keep the number of row tweens / triggers bounded on long route tables

  const mm = gsap.matchMedia();

  mm.add('(prefers-reduced-motion: no-preference)', () => {
    const units = gsap.utils.toArray(main.querySelectorAll(UNIT))
      .filter(el => !el.parentElement.closest(UNIT));
    const rows = gsap.utils.toArray(main.querySelectorAll('table > tbody > tr')).slice(0, MAX_ROWS);

    // Read every position first, then write (avoids layout thrashing).
    const fold = window.innerHeight;
    const inView = el => el.getBoundingClientRect().top < fold;
    const unitsNow = units.filter(inView), unitsLater = units.filter(el => !inView(el));
    const rowsNow = rows.filter(inView).slice(0, 25), rowsLater = rows.filter(el => !rowsNow.includes(el));

    const tl = gsap.timeline();

    const route = document.querySelector('.brand-route');
    if (route) {
      const len = route.getTotalLength();
      gsap.set(route, { strokeDasharray: len });
      tl.from(route, { strokeDashoffset: len, duration: 0.9, ease: 'power2.inOut' }, 0)
        .from('.brand-stop', { scale: 0, transformOrigin: '50% 50%', stagger: 0.15, duration: 0.4, ease: 'back.out(2.5)' }, 0.15);
    }
    tl.from('.site-nav .nav-item', { y: -10, autoAlpha: 0, stagger: 0.04, duration: 0.4 }, 0.1);
    if (unitsNow.length) {
      tl.from(unitsNow, { y: 24, autoAlpha: 0, stagger: 0.07, clearProps: 'transform' }, 0.15);
    }
    if (rowsNow.length) {
      tl.from(rowsNow, { y: 10, autoAlpha: 0, stagger: 0.03, duration: 0.4, clearProps: 'transform' }, 0.35);
    }

    if (hasST) {
      const batchReveal = (targets, y, stagger, batchMax) => {
        if (!targets.length) return;
        gsap.set(targets, { autoAlpha: 0, y });
        ScrollTrigger.batch(targets, {
          start: 'clamp(top 90%)',   // clamp: elements at the very bottom still get revealed
          once: true,
          batchMax,
          onEnter: batch => gsap.to(batch, { autoAlpha: 1, y: 0, stagger, overwrite: true, clearProps: 'transform' }),
        });
      };
      batchReveal(unitsLater, 28, 0.08, 6);
      batchReveal(rowsLater, 10, 0.03, 12);
    }

    reveal();   // start states are applied, safe to show the page

    // "Show queries" panel: stagger the cards in as it opens.
    const queries = document.getElementById('queries');
    const onShow = () => queries.querySelector('.query-card') && gsap.fromTo(queries.querySelectorAll('.query-card'),
      { y: 12, autoAlpha: 0 }, { y: 0, autoAlpha: 1, stagger: 0.04, duration: 0.4, overwrite: true, clearProps: 'transform' });
    queries?.addEventListener('show.bs.collapse', onShow);

    Motion.rows = els => els.length && gsap.fromTo(els, { x: -12, autoAlpha: 0 },
      { x: 0, autoAlpha: 1, stagger: 0.03, duration: 0.35, overwrite: true, clearProps: 'transform' });

    return () => {
      queries?.removeEventListener('show.bs.collapse', onShow);
      Motion.rows = () => {};
    };
  });

  // Sliding underline under the main nav links (desktop only).
  mm.add('(min-width: 992px)', () => {
    const nav = document.querySelector('.nav-main');
    const bar = nav?.querySelector('.nav-indicator');
    if (!bar) return;
    const links = gsap.utils.toArray(nav.querySelectorAll(':scope > .nav-item > .nav-link'));
    const active = links.find(a => a.classList.contains('active'));
    const BASE = 100;   // CSS width of .nav-indicator; scaleX stretches it to the link width
    const PAD = 8;      // Bootstrap .nav-link horizontal padding
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches;

    const measure = link => {
      const n = nav.getBoundingClientRect(), r = link.getBoundingClientRect();
      return { x: r.left - n.left + PAD, scaleX: (r.width - PAD * 2) / BASE };
    };

    if (active) gsap.set(bar, { ...measure(active), autoAlpha: 1 });
    else gsap.set(bar, { autoAlpha: 0 });

    const dur = still ? 0.01 : 0.4;
    const xTo = gsap.quickTo(bar, 'x', { duration: dur, ease: 'power3' });
    const sTo = gsap.quickTo(bar, 'scaleX', { duration: dur, ease: 'power3' });
    const moveTo = link => {
      const m = measure(link);
      if (gsap.getProperty(bar, 'opacity') < 0.01) gsap.set(bar, m);   // appear in place, don't slide in from 0
      xTo(m.x); sTo(m.scaleX);
      gsap.to(bar, { autoAlpha: 1, duration: 0.2, overwrite: 'auto' });
    };
    const home = () => active ? moveTo(active) : gsap.to(bar, { autoAlpha: 0, duration: 0.2, overwrite: 'auto' });

    const handlers = links.map(link => {
      const fn = () => moveTo(link);
      link.addEventListener('mouseenter', fn);
      link.addEventListener('focus', fn);
      return [link, fn];
    });
    nav.addEventListener('mouseleave', home);
    nav.addEventListener('focusout', home);
    const onResize = () => active && gsap.set(bar, measure(active));
    window.addEventListener('resize', onResize);
    document.fonts?.ready.then(onResize);   // Inter changes link widths once it loads

    return () => {
      handlers.forEach(([link, fn]) => { link.removeEventListener('mouseenter', fn); link.removeEventListener('focus', fn); });
      nav.removeEventListener('mouseleave', home);
      nav.removeEventListener('focusout', home);
      window.removeEventListener('resize', onResize);
    };
  });

  if (hasST) {
    // Layout changes that the automatic resize refresh does not catch.
    document.fonts?.ready.then(() => ScrollTrigger.refresh());
    const queries = document.getElementById('queries');
    queries?.addEventListener('shown.bs.collapse', () => ScrollTrigger.refresh());
    queries?.addEventListener('hidden.bs.collapse', () => ScrollTrigger.refresh());
  }

  reveal();   // reduced-motion path, or no matchMedia handler hid anything
})();

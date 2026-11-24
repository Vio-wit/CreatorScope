(() => {
  const groups = [...document.querySelectorAll('.nav-group')];
  if (!groups.length) return;

  const STORAGE_KEY = 'creatorscope-nav-open';
  let pinnedLink = null;
  let pinnedAt = 0;

  function readOpenState() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; } catch (_) { return {}; }
  }

  function writeOpenState(state) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (_) { /* 隐私模式下忽略持久化 */ }
  }

  function currentGroup() {
    if (location.pathname === '/ai') return 'smart';
    if (location.pathname !== '/') return '';
    if (location.hash === '#creators') return 'creators';
    if (location.hash === '#collection' || location.hash === '#records') return 'collection';
    return 'analytics';
  }

  function persistOpen(group, open) {
    const state = readOpenState();
    state[group.dataset.navGroup] = open;
    writeOpenState(state);
  }

  function setOpen(group, open) {
    group.querySelector('.section-nav').hidden = !open;
    const button = group.querySelector('.nav-expand');
    button.setAttribute('aria-expanded', String(open));
    const title = group.querySelector('[data-primary-section]').textContent.trim();
    button.setAttribute('aria-label', `${open ? '收起' : '展开'}${title}目录`);
    persistOpen(group, open);
  }

  function syncGroups() {
    const current = currentGroup();
    const saved = readOpenState();
    groups.forEach((group) => {
      if (group.dataset.navGroup === current) {
        setOpen(group, true);
      } else {
        // 恢复上次持久化的展开状态；无记录时保持收起，不强制展开其余分组。
        setOpen(group, saved[group.dataset.navGroup] === true);
      }
    });
    updateProgress();
  }

  function targetFor(link) {
    return document.querySelector(link.dataset.target);
  }

  function matchesCurrentPage(link) {
    if (link.dataset.page === 'ai') return location.pathname === '/ai';
    return location.pathname === '/' && link.dataset.view === currentGroup();
  }

  function positionProgress(group, link) {
    const nav = group.querySelector('.section-nav');
    const span = group.querySelector('.section-nav-progress');
    if (!nav || !span) return;
    const navHeight = nav.offsetHeight || 1;
    const center = link.offsetTop + link.offsetHeight / 2;
    const ratio = Math.max(0, Math.min(1, center / navHeight));
    span.style.top = `${ratio * 100}%`;
  }

  function updateProgress() {
    const name = currentGroup();
    const group = groups.find((item) => item.dataset.navGroup === name);
    if (!group) return;
    const links = [...group.querySelectorAll('.section-nav a')];
    const entries = [];
    links.forEach((link) => {
      const samePage = matchesCurrentPage(link);
      const target = samePage ? targetFor(link) : null;
      // Cross-page links remain available; absent same-page sections are omitted.
      link.hidden = samePage && !target;
      link.classList.remove('is-current');
      link.removeAttribute('aria-current');
      if (target && target.getClientRects().length) entries.push({ link, target });
    });

    const pinValid = pinnedLink && !pinnedLink.hidden && matchesCurrentPage(pinnedLink)
      && (Date.now() - pinnedAt < 400);
    let current = null;
    if (pinValid) {
      current = { link: pinnedLink };
    } else if (entries.length) {
      const viewportLine = Math.min(150, window.innerHeight * 0.25);
      current = entries[0];
      entries.forEach((entry) => {
        if (entry.target.getBoundingClientRect().top <= viewportLine) current = entry;
      });
      const sections = location.pathname === '/ai'
        ? [...document.querySelectorAll('main > section')].filter((node) => node.getClientRects().length)
        : [...document.querySelectorAll(`[data-primary-view="${name}"]`)].filter((node) => node.getClientRects().length);
      if (sections.length && window.scrollY + window.innerHeight >= sections[sections.length - 1].getBoundingClientRect().bottom + window.scrollY - 10) {
        current = entries[entries.length - 1];
      }
    }
    if (!current) return;
    current.link.classList.add('is-current');
    current.link.setAttribute('aria-current', 'location');
    positionProgress(group, current.link);
  }

  groups.forEach((group) => {
    group.querySelector('.nav-expand').addEventListener('click', () => {
      setOpen(group, group.querySelector('.section-nav').hidden);
      updateProgress();
    });
    group.querySelector('[data-primary-section]').addEventListener('click', (event) => {
      if (group.dataset.navGroup !== currentGroup()) return;
      if (group.dataset.navGroup === 'smart' && location.pathname !== '/ai') return;
      // On the current main page the label also opens or closes its directory.
      event.preventDefault();
      setOpen(group, group.querySelector('.section-nav').hidden);
      updateProgress();
    });
    group.querySelectorAll('.section-nav a').forEach((link) => {
      link.addEventListener('click', (event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        if (!matchesCurrentPage(link)) {
          // Remember the intended section when another main view or page opens.
          try { sessionStorage.setItem('creatorscope-nav-target', link.dataset.target); } catch (_) { /* Navigation still works without storage. */ }
          if (location.pathname === '/' && link.dataset.view) {
            event.preventDefault();
            location.hash = `#${link.dataset.view}`;
          }
          return;
        }
        const target = targetFor(link);
        if (!target) return;
        event.preventDefault();
        pinnedLink = link;
        pinnedAt = Date.now();
        updateProgress();
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });
  });
  let pending = false;
  window.addEventListener('scroll', () => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; updateProgress(); });
  }, { passive: true });
  window.addEventListener('resize', updateProgress);
  function followPendingTarget() {
    let selector;
    try { selector = sessionStorage.getItem('creatorscope-nav-target'); } catch (_) { return; }
    if (!selector) return;
    const target = document.querySelector(selector);
    if (!target || !target.getClientRects().length) return;
    try { sessionStorage.removeItem('creatorscope-nav-target'); } catch (_) { /* No stored state to clear. */ }
    target.scrollIntoView({ block: 'start' });
  }
  window.addEventListener('hashchange', () => requestAnimationFrame(() => { syncGroups(); followPendingTarget(); }));
  syncGroups();
  requestAnimationFrame(followPendingTarget);
})();

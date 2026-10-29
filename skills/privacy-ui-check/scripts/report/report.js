(() => {
  const pages = [...document.querySelectorAll('section.page')];
  const order = pages.map(p => p.id);
  const show = () => {
    const id = order.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'overview';
    pages.forEach(p => p.classList.toggle('show', p.id === id));
    document.querySelectorAll('.toc a').forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#' + id));
    setTimeout(() => window.scrollTo(0, 0));  // 브라우저의 앵커 이동 뒤에 맨 위로
  };
  const go = step => {
    const cur = order.indexOf(location.hash.slice(1));
    const next = order[Math.min(order.length - 1, Math.max(0, (cur < 0 ? 0 : cur) + step))];
    location.hash = '#' + next;
  };
  addEventListener('hashchange', show);
  addEventListener('keydown', ev => {
    if (ev.key === 'Escape') { document.getElementById('zoom').hidden = true; return; }
    if (ev.target.closest && ev.target.closest('input, textarea')) return;
    if (ev.key === 'ArrowRight') go(1);
    if (ev.key === 'ArrowLeft') go(-1);
  });
  document.querySelectorAll('#ov-list tbody tr').forEach(tr => tr.addEventListener('click', () => { location.hash = tr.dataset.href; }));
  const zoom = document.getElementById('zoom');
  document.querySelectorAll('figure img').forEach(img => img.addEventListener('click', () => {
    zoom.querySelector('img').src = img.src; zoom.hidden = false;
  }));
  zoom.addEventListener('click', () => { zoom.hidden = true; });
  show();
})();

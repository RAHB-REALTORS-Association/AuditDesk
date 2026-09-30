const menu = document.querySelector('.menu-toggle');
const close = document.querySelector('.menu-close');
const backdrop = document.querySelector('.menu-backdrop');
function setMenu(open) {
  document.body.classList.toggle('menu-open', open);
  menu.setAttribute('aria-expanded', String(open));
  backdrop.hidden = !open;
  (open ? close : menu).focus();
}
menu.addEventListener('click', () => setMenu(!document.body.classList.contains('menu-open')));
close.addEventListener('click', () => setMenu(false));
backdrop.addEventListener('click', () => setMenu(false));
document.addEventListener('keydown', event => { if (event.key === 'Escape' && document.body.classList.contains('menu-open')) setMenu(false); });
document.querySelectorAll('.sidebar a').forEach(link => link.addEventListener('click', () => { if (document.body.classList.contains('menu-open')) setMenu(false); }));

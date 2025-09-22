// Общий скрипт для страниц драфтов без спецфункций
// Автоприменение серверных фильтров
document.addEventListener('DOMContentLoaded', function () {
  const form = document.getElementById('filters-form');
  if (form) {
    form.querySelectorAll('select').forEach(sel => {
      sel.addEventListener('change', () => form.submit());
    });
  }

  // FP 2024/25 sorting (data already in table)
  const sortBtn = document.getElementById('fp-sort-btn');
  const sortArrow = document.getElementById('fp-sort-arrow');

  function sortByFp(dir) {
    const body = document.querySelector('#players tbody');
    if (!body) return;
    const rows = Array.from(body.querySelectorAll('tr'));
    rows.sort((a, b) => {
      const afp = Number(a.querySelector('.fp-cell')?.getAttribute('data-fp') || '0');
      const bfp = Number(b.querySelector('.fp-cell')?.getAttribute('data-fp') || '0');
      return dir === 'asc' ? (afp - bfp) : (bfp - afp);
    });
    rows.forEach(r => body.appendChild(r));
  }

  sortBtn?.addEventListener('click', () => {
    const cur = sortBtn.getAttribute('data-dir') || 'desc';
    const next = cur === 'desc' ? 'asc' : 'desc';
    sortBtn.setAttribute('data-dir', next);
    if (sortArrow) sortArrow.textContent = next === 'desc' ? '↓' : '↑';
    sortByFp(next);
  });

  // FP 2025/26 sorting (current season) for UCL
  const sortCurBtn = document.getElementById('fp-cur-sort-btn');
  const sortCurArrow = document.getElementById('fp-cur-sort-arrow');

  function sortByFpCur(dir) {
    const body = document.querySelector('#players tbody');
    if (!body) return;
    const rows = Array.from(body.querySelectorAll('tr'));
    rows.sort((a, b) => {
      const afp = Number(a.querySelector('.fp-cur-cell')?.getAttribute('data-fp') || '0');
      const bfp = Number(b.querySelector('.fp-cur-cell')?.getAttribute('data-fp') || '0');
      return dir === 'asc' ? (afp - bfp) : (bfp - afp);
    });
    rows.forEach(r => body.appendChild(r));
  }

  sortCurBtn?.addEventListener('click', () => {
    const cur = sortCurBtn.getAttribute('data-dir') || 'desc';
    const next = cur === 'desc' ? 'asc' : 'desc';
    sortCurBtn.setAttribute('data-dir', next);
    if (sortCurArrow) sortCurArrow.textContent = next === 'desc' ? '↓' : '↑';
    sortByFpCur(next);
  });
});

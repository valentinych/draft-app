// Автоприменение серверных фильтров
document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('filters-form');
    if (form) {
      form.querySelectorAll('select').forEach(sel => {
        sel.addEventListener('change', () => form.submit());
      });
    }
  
    // -------- Попап статистики --------
    const modal = document.getElementById('stat-modal');
    const tbody = document.getElementById('stat-tbody');
    const loading = document.getElementById('stat-loading');
    const empty = document.getElementById('stat-empty');
    const wrap = document.getElementById('stat-table-wrap');
    const title = document.getElementById('stat-title');
    const nameEl = document.getElementById('stat-name');
    const photoEl = document.getElementById('stat-photo');
  
    function openModal() {
      modal.setAttribute('aria-hidden', 'false');
      document.addEventListener('keydown', onEsc);
    }
    function closeModal() {
      modal.setAttribute('aria-hidden', 'true');
      document.removeEventListener('keydown', onEsc);
    }
    function onEsc(e) { if (e.key === 'Escape') closeModal(); }
    modal.addEventListener('click', (e) => { if (e.target.dataset.close === '1') closeModal(); });
  
    document.querySelectorAll('.link-stat').forEach(btn => {
      btn.addEventListener('click', async () => {
        const pid = btn.getAttribute('data-pid');
        const name = btn.getAttribute('data-name') || 'Статистика';
        title.textContent = 'Статистика';
        nameEl.textContent = name;
        photoEl.style.display = 'none';
  
        loading.style.display = '';
        empty.style.display = 'none';
        wrap.style.display = 'none';
        tbody.innerHTML = '';
        openModal();
  
        try {
          const res = await fetch(`/epl/api/player/${pid}/stats`, { credentials: 'same-origin' });
          if (!res.ok) throw new Error('load failed');
          const data = await res.json();
  
          if (data.photo_url) {
            photoEl.src = data.photo_url;
            photoEl.style.display = '';
          }
  
          const hist = Array.isArray(data.history) ? data.history : [];
          loading.style.display = 'none';
          if (!hist.length) { empty.style.display = ''; return; }
  
          const rows = hist.map(r => {
            const td = (v, cls) => `<td class="${cls||''}">${v ?? ''}</td>`;
            return `<tr>
              ${td(r.season)}
              ${td(r.minutes, 'num')}
              ${td(r.goals, 'num')}
              ${td(r.assists, 'num')}
              ${td(r.cs, 'num')}
              ${td(r.total_points, 'num')}
            </tr>`;
          }).join('');
          tbody.innerHTML = rows;
          wrap.style.display = '';
        } catch (e) {
          loading.style.display = 'none';
          empty.style.display = '';
        }
      });
    });

    // -------- FP 2025/26: сортировка --------
    const fpCurCells = Array.from(document.querySelectorAll('.fp-cur-cell'));
    const sortCurBtn = document.getElementById('fp-cur-sort-btn');
    const sortCurArrow = document.getElementById('fp-cur-sort-arrow');

    function sortByFpCur(dir) {
      const body = document.querySelector('#players tbody');
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
      sortCurArrow.textContent = next === 'desc' ? '↓' : '↑';
      sortByFpCur(next);
    });

    // -------- FP 2024/25: подгрузка + сортировка --------
    const fpCells = Array.from(document.querySelectorAll('.fp-cell'));
    const sortBtn = document.getElementById('fp-sort-btn');
    const sortArrow = document.getElementById('fp-sort-arrow');
  
    async function loadFpBatch() {
      if (!fpCells.length) return;
      const ids = fpCells.map(el => el.getAttribute('data-pid')).filter(Boolean);
      const chunkSize = 50;
      for (let i = 0; i < ids.length; i += chunkSize) {
        const chunk = ids.slice(i, i + chunkSize);
        try {
          const res = await fetch(`/epl/api/fp_last?ids=${chunk.join(',')}`, { credentials: 'same-origin' });
          if (!res.ok) continue;
          const data = await res.json();
          const fp = data.fp || {};
          chunk.forEach(id => {
            const val = fp[id];
            const cell = document.querySelector(`.fp-cell[data-pid="${id}"]`);
            if (cell) {
              const shown = (val === null || val === undefined) ? 0 : Number(val);
              cell.textContent = String(shown);
              cell.setAttribute('data-fp', String(shown));
            }
          });
        } catch(e) { /* ignore */ }
      }
    }
  
    function sortByFp(dir) {
      const body = document.querySelector('#players tbody');
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
      sortArrow.textContent = next === 'desc' ? '↓' : '↑';
      sortByFp(next);
    });
  
    loadFpBatch();
  });
  
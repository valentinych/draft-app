(function () {
    // -------- helpers ----------
    function qs(sel, root) { return (root || document).querySelector(sel); }
    function qsa(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }
  
    function getCtx() {
      const table = qs('#players');
      return {
        league: table?.dataset?.league || 'epl',
        manager: table?.dataset?.manager || '',
      };
    }
  
    // batch changes to server
    let batch = { add: new Set(), remove: new Set() };
    let timer = null;
    const DEBOUNCE_MS = 250;
  
    async function fetchWishlist() {
      const { league } = getCtx();
      const res = await fetch(`/${league}/api/wishlist`, { credentials: 'same-origin' });
      if (!res.ok) throw new Error('Wishlist load failed');
      const data = await res.json();
      return Array.isArray(data.ids) ? new Set(data.ids.map(Number)) : new Set();
    }
  
    async function pushBatch() {
      if (!batch.add.size && !batch.remove.size) return;
      const payload = {
        add: Array.from(batch.add),
        remove: Array.from(batch.remove),
      };
      // reset batch before sending to avoid double-send on quick toggles
      batch.add.clear(); batch.remove.clear();
  
      try {
        const { league } = getCtx();
        const res = await fetch(`/${league}/api/wishlist`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error('Wishlist save failed');
        const data = await res.json();
        applyWishlistStyles(new Set(data.ids.map(Number)));
        updateBadge(data.ids.length);
        applyFilters();
      } catch (e) {
        console.error(e);
      }
    }
  
    function enqueueChange(pid, checked) {
      if (checked) {
        batch.add.add(pid);
        batch.remove.delete(pid);
      } else {
        batch.remove.add(pid);
        batch.add.delete(pid);
      }
      if (timer) clearTimeout(timer);
      timer = setTimeout(pushBatch, DEBOUNCE_MS);
    }
  
    function updateBadge(count) {
      const el = qs('#wishlist-count');
      if (el) el.textContent = String(count || 0);
    }
  
    function applyWishlistStyles(set) {
      qsa('#players tbody tr').forEach(tr => {
        const pid = Number(tr.getAttribute('data-player-id'));
        const checked = set.has(pid);
        tr.classList.toggle('is-wishlist', checked);
        const box = qs('input.wishlist-toggle', tr);
        if (box) box.checked = checked;
      });
    }
  
    function applyFilters() {
      const wlOnly = qs('#wishlist-only-toggle')?.checked;
      const canPickOnly = qs('#can-pick-toggle')?.checked;
      const hideTransfers = qs('#hide-transfers-toggle')?.checked;
      const hideReds = qs('#hide-reds-toggle')?.checked;
      qsa('#players tbody tr').forEach(tr => {
        const inWishlist = tr.classList.contains('is-wishlist');
        const canPick = tr.getAttribute('data-can-pick') === '1';
        const pickBtn = qs('form button[type="submit"]', tr);
        if (pickBtn) {
          // For TOP4 transfers, always enable buttons for current manager
          const isTop4Transfer = window.location.pathname.includes('/top4') && 
                                document.querySelector('[data-transfer-window-active="true"]');
          const isCurrentManager = document.querySelector('[data-is-current-manager="true"]');
          
          if (isTop4Transfer && isCurrentManager) {
            pickBtn.disabled = false;
          } else {
            pickBtn.disabled = !canPick;
          }
        }
        const status = tr.getAttribute('data-status') || '';
        const chance = Number(tr.getAttribute('data-chance') || '0');
        const news = (tr.getAttribute('data-news') || '').toLowerCase();
        const isRed = status && status !== 'a' && chance < 50;
        const isTransfer = isRed && (news.includes('joined') || news.includes('loan') || news.includes('departed'));
        let visible = true;
        if (wlOnly) visible = visible && inWishlist;
        if (canPickOnly) visible = visible && canPick;
        if (hideReds && isRed) visible = false;
        if (hideTransfers && isTransfer) visible = false;
        tr.style.display = visible ? '' : 'none';
      });
    }
  
    async function init() {
      const table = qs('#players');
      if (!table) return;
  
      // загрузка серверного списка
      let serverSet = new Set();
      try {
        serverSet = await fetchWishlist();
      } catch (e) {
        console.error(e);
      }
  
      updateBadge(serverSet.size);
      applyWishlistStyles(serverSet);
  
      // чекбоксы строк
      qsa('input.wishlist-toggle').forEach(chk => {
        const pid = Number(chk.getAttribute('data-player-id'));
        chk.checked = serverSet.has(pid);
        chk.addEventListener('change', () => {
          enqueueChange(pid, chk.checked);
        });
      });
  
      // фильтры
      const wlOnly = qs('#wishlist-only-toggle');
      const canPickOnly = qs('#can-pick-toggle');
      const hideTransfers = qs('#hide-transfers-toggle');
      const hideReds = qs('#hide-reds-toggle');
      wlOnly && wlOnly.addEventListener('change', applyFilters);
      canPickOnly && canPickOnly.addEventListener('change', applyFilters);
      hideTransfers && hideTransfers.addEventListener('change', applyFilters);
      hideReds && hideReds.addEventListener('change', applyFilters);
      applyFilters();
  
      // выгрузить хвост при уходе
      window.addEventListener('beforeunload', () => { if (timer) { clearTimeout(timer); pushBatch(); } });
    }
  
    document.addEventListener('DOMContentLoaded', init);
  })();
  
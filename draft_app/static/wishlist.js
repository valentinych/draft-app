(function(){
    const tableEl = document.getElementById('players');
    const onlyToggle = document.getElementById('wishlist-only-toggle');
    const canPickToggle = document.getElementById('can-pick-toggle');
    const countEl = document.getElementById('wishlist-count');
  
    if (!tableEl) return;
  
    const league = (tableEl.dataset.league || 'epl').toLowerCase();
    const manager = (tableEl.dataset.manager || '').trim();
  
    const rows = Array.from(tableEl.querySelectorAll('tbody tr'));
    const rowsById = new Map(rows.map(tr => [String(tr.dataset.playerId), tr]));
    const boxes = Array.from(tableEl.querySelectorAll('input.wishlist-toggle'));
  
    let wishlist = new Set();
  
    function setCount(n){ if (countEl) countEl.textContent = String(n || 0); }
    function markRow(pid, on){
      const tr = rowsById.get(String(pid));
      if (tr) tr.classList.toggle('is-wishlist', !!on);
    }
  
    function rowPassesFilters(tr){
      // wishlist-only
      const needWL = !!(onlyToggle && onlyToggle.checked);
      if (needWL && !tr.classList.contains('is-wishlist')) return false;
  
      // can-pick
      const needPickable = !!(canPickToggle && canPickToggle.checked);
      if (needPickable && tr.getAttribute('data-can-pick') !== '1') return false;
  
      return true;
    }
  
    function refreshFilter(){
      rows.forEach(tr => {
        tr.style.display = rowPassesFilters(tr) ? '' : 'none';
      });
    }
  
    async function apiGet(){
      if (!manager) return new Set();
      const params = new URLSearchParams({ manager, league });
      const r = await fetch(`/api/wishlist?${params.toString()}`, { credentials: 'same-origin' });
      if (!r.ok) return new Set();
      const data = await r.json();
      return new Set((data && data.ids) || []);
    }
  
    async function apiPut(addIds, removeIds){
      if (!manager) return;
      const r = await fetch('/api/wishlist', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ manager, league, add: addIds, remove: removeIds })
      });
      if (!r.ok) throw new Error('wishlist update failed');
      const data = await r.json();
      wishlist = new Set((data && data.ids) || []);
      // repaint to be sure
      boxes.forEach(cb => {
        const pid = String(cb.dataset.playerId);
        const on = wishlist.has(pid);
        cb.checked = on; markRow(pid, on);
      });
      setCount(wishlist.size);
      refreshFilter();
    }
  
    function paint(){
      boxes.forEach(cb => {
        const pid = String(cb.dataset.playerId);
        const on = wishlist.has(pid);
        cb.checked = on; markRow(pid, on);
      });
      setCount(wishlist.size);
      refreshFilter();
    }
  
    boxes.forEach(cb => {
      cb.addEventListener('change', () => {
        const pid = String(cb.dataset.playerId);
        const add = [], remove = [];
        if (cb.checked) { wishlist.add(pid); add.push(pid); }
        else { wishlist.delete(pid); remove.push(pid); }
        markRow(pid, cb.checked);
        setCount(wishlist.size);
        refreshFilter();
        apiPut(add, remove).catch(()=>{/* optimistic */});
      });
    });
  
    if (onlyToggle) onlyToggle.addEventListener('change', refreshFilter);
    if (canPickToggle) canPickToggle.addEventListener('change', refreshFilter);
  
    if (!manager){
      boxes.forEach(cb => { cb.disabled = true; });
      if (onlyToggle) onlyToggle.disabled = true;
      if (canPickToggle) canPickToggle.disabled = true;
      setCount(0);
      return;
    }
  
    apiGet().then(set => { wishlist = set; paint(); }).catch(() => { paint(); });
  })();
  
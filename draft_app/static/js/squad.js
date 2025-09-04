function formationCounts(fmt){
  const parts = fmt.split('-').map(x=>parseInt(x,10));
  if(parts.length!==3||parts.some(isNaN)) return {GK:1,DEF:4,MID:4,FWD:2};
  return {GK:1,DEF:parts[0],MID:parts[1],FWD:parts[2]};
}

const POSITIONS=['GK','DEF','MID','FWD'];
const ALL_POSITIONS=[...POSITIONS,'BENCH'];

function buildSlots(){
  const fmt=document.getElementById('formation').value;
  const counts=formationCounts(fmt);
  POSITIONS.forEach(pos=>{
    const wrap=document.getElementById('slot-'+pos);
    wrap.innerHTML='';
    wrap.dataset.max=counts[pos];
  });
}

function placePreset(){
  const data=document.getElementById('lineup-data');
  const benchData=document.getElementById('bench-data');
  try{
    const ids=JSON.parse(data?.textContent||'[]');
    ids.forEach(pid=>{
      const el=document.querySelector(`#roster .player[data-id='${pid}']`);
      if(el){
        const pos=el.dataset.pos;
        const target=document.getElementById('slot-'+pos);
        if(target) target.appendChild(el);
      }
    });
    const bIds=JSON.parse(benchData?.textContent||'[]');
    bIds.forEach(pid=>{
      const el=document.querySelector(`#roster .player[data-id='${pid}']`);
      if(el){
        const target=document.getElementById('slot-BENCH');
        if(target) target.appendChild(el);
      }
    });
  }catch(e){}
}

function placeInRoster(el){
  const line=document.getElementById('roster-'+(el.dataset.pos||''));
  if(line) line.appendChild(el); else document.getElementById('roster').appendChild(el);
}

function handlePlayerClick(el){
  const parentId=el.parentElement.id;
  if(parentId.startsWith('slot-')){
    placeInRoster(el);
    return;
  }
  const pos=el.dataset.pos;
  const wrap=document.getElementById('slot-'+pos);
  const max=parseInt(wrap.dataset.max||'0',10);
  if(!max || wrap.children.length<max){
    wrap.appendChild(el);
  }else{
    document.getElementById('slot-BENCH').appendChild(el);
  }
}

function serialize(){
  const ids=[];
  POSITIONS.forEach(pos=>{
    const wrap=document.getElementById('slot-'+pos);
    wrap.querySelectorAll('.player').forEach(p=>ids.push(p.dataset.id));
  });
  document.getElementById('player_ids').value=ids.join(',');
  const benchIds=[];
  const benchWrap=document.getElementById('slot-BENCH');
  benchWrap.querySelectorAll('.player').forEach(p=>benchIds.push(p.dataset.id));
  document.getElementById('bench_ids').value=benchIds.join(',');
}


function initPlayerModal(){
  const modal=document.getElementById('player-modal');
  if(!modal) return null;
  const nameEl=document.getElementById('pm-name');
  const photoEl=document.getElementById('pm-photo');
  const newsEl=document.getElementById('pm-news');
  const statsEl=document.getElementById('pm-stats');
  function open(){ modal.setAttribute('aria-hidden','false'); document.addEventListener('keydown',esc); }
  function close(){ modal.setAttribute('aria-hidden','true'); document.removeEventListener('keydown',esc); }
  function esc(e){ if(e.key==='Escape') close(); }
  modal.addEventListener('click',e=>{ if(e.target.dataset.close==='1') close(); });
  function show(playerEl){
    const name=playerEl.dataset.name||'';
    const news=playerEl.dataset.news||'';
    let stats={};
    try{ stats=JSON.parse(playerEl.dataset.stats||'{}'); }catch(e){}
    nameEl.textContent=name;
    const img=playerEl.querySelector('img');
    if(img){ photoEl.src=img.src; photoEl.style.display=''; } else { photoEl.style.display='none'; }
    newsEl.textContent=news;
    const rows=[
      ['Minutes',stats.minutes],
      ['Goals',stats.goals],
      ['Assists',stats.assists],
      ['CS',stats.cs],
      ['Points',stats.points],
    ].map(r=>`<tr><td>${r[0]}</td><td class="num">${r[1]??''}</td></tr>`).join('');
    statsEl.innerHTML=`<table class="tbl"><tbody>${rows}</tbody></table>`;
    open();
  }
  return {show};
}

document.addEventListener('DOMContentLoaded',()=>{
  const editable=document.getElementById('lineup').dataset.editable==='1';
  buildSlots();
  placePreset();
  const modal=initPlayerModal();

  const viewBtn=document.getElementById('view-toggle');
  let listMode=false;
  function setViewMode(list){
    const players=document.querySelectorAll('.player');
    players.forEach(p=>{
      if(!p.dataset.orig){
        p.dataset.orig=p.innerHTML;
        const fx=p.dataset.fixture?` ${p.dataset.fixture}`:'';
        p.dataset.list=`${p.dataset.pos} ${p.dataset.name}${fx}`;
      }
      p.innerHTML=list?p.dataset.list:p.dataset.orig;
    });
    document.getElementById('roster').classList.toggle('list-mode',list);
    document.getElementById('lineup').classList.toggle('list-mode',list);
    if(viewBtn) viewBtn.textContent=list?'Фото':'Список';
  }
  if(viewBtn){
    viewBtn.addEventListener('click',()=>{ listMode=!listMode; setViewMode(listMode); });
  }

  function iconHover(e){
    const icon=e.target.closest('.flag, .info-icon');
    if(icon && modal){
      const pl=icon.closest('.player');
      if(pl) modal.show(pl);
    }
  }
  function iconClick(e){
    const icon=e.target.closest('.flag, .info-icon');
    if(icon){
      e.stopPropagation();
      if(modal){
        const pl=icon.closest('.player');
        if(pl) modal.show(pl);
      }
    }
  }

  const roster=document.getElementById('roster');
  roster.addEventListener('mouseover',iconHover);
  roster.addEventListener('click',iconClick);

  if(editable){
    roster.addEventListener('click',e=>{
      const p=e.target.closest('.player');
      if(p) handlePlayerClick(p);
    });
    ALL_POSITIONS.forEach(pos=>{
      const wrap=document.getElementById('slot-'+pos);
      wrap.addEventListener('mouseover',iconHover);
      wrap.addEventListener('click',iconClick);
      wrap.addEventListener('click',e=>{
        const p=e.target.closest('.player');
        if(p) handlePlayerClick(p);
      });
    });
    document.getElementById('formation').addEventListener('change',()=>{
      ALL_POSITIONS.forEach(pos=>{
        const wrap=document.getElementById('slot-'+pos);
        if(pos!=='BENCH'){
          Array.from(wrap.children).forEach(p=>{
            placeInRoster(p);
          });
        }
      });
      buildSlots();
    });
    document.getElementById('lineup-form').addEventListener('submit',serialize);
  }else{
    ALL_POSITIONS.forEach(pos=>{
      const wrap=document.getElementById('slot-'+pos);
      wrap.addEventListener('mouseover',iconHover);
      wrap.addEventListener('click',iconClick);
    });
  }
});

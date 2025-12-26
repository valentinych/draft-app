const FORMATIONS=JSON.parse(document.getElementById('formation-list')?.textContent||'[]');
const POSITIONS=['GK','DEF','MID','FWD'];
const ALL_POSITIONS=[...POSITIONS,'BENCH'];

function parseFormation(fmt){
  const parts=fmt.split('-').map(x=>parseInt(x,10));
  if(parts.length!==3||parts.some(isNaN)) return {DEF:4,MID:4,FWD:2};
  return {DEF:parts[0],MID:parts[1],FWD:parts[2]};
}

const MAX_COUNTS=FORMATIONS.reduce((acc,f)=>{
  const c=parseFormation(f);
  acc.DEF=Math.max(acc.DEF,c.DEF);
  acc.MID=Math.max(acc.MID,c.MID);
  acc.FWD=Math.max(acc.FWD,c.FWD);
  return acc;
},{GK:1,DEF:0,MID:0,FWD:0});

function formationCounts(fmt){
  if(fmt==='auto') return {...MAX_COUNTS};
  const c=parseFormation(fmt);
  return {GK:1,DEF:c.DEF,MID:c.MID,FWD:c.FWD};
}

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

function canAddToLineup(pos){
  const counts={GK:0,DEF:0,MID:0,FWD:0};
  POSITIONS.forEach(p=>{ counts[p]=document.getElementById('slot-'+p).children.length; });
  counts[pos]+=1;
  if(counts.GK>1) return false;
  const total=counts.GK+counts.DEF+counts.MID+counts.FWD;
  if(total>11) return false;
  return FORMATIONS.some(f=>{
    const c=parseFormation(f);
    return counts.DEF<=c.DEF && counts.MID<=c.MID && counts.FWD<=c.FWD;
  });
}

function handlePlayerClick(el){
  const parentId=el.parentElement.id;
  if(parentId.startsWith('slot-')){
    placeInRoster(el);
    return;
  }
  const pos=el.dataset.pos;
  const fmt=document.getElementById('formation').value;
  if(fmt==='auto'){
    if(canAddToLineup(pos)){
      document.getElementById('slot-'+pos).appendChild(el);
    }else{
      document.getElementById('slot-BENCH').appendChild(el);
    }
    return;
  }
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
    if(img){ 
      photoEl.src=img.src; 
      photoEl.style.display=''; 
      // Добавляем обработчик ошибок для модального окна
      photoEl.onerror = function() {
        this.onerror = null;
        this.src = 'https://static.wikitide.net/rytpwiki/thumb/2/20/%D0%A1%D0%B2%D0%B8%D0%B4%D0%B5%D1%82%D0%B5%D0%BB%D1%8C_%D0%B8%D0%B7_%D0%A4%D1%80%D1%8F%D0%B7%D0%B8%D0%BD%D0%BE.png/250px-%D0%A1%D0%B2%D0%B8%D0%B4%D0%B5%D1%82%D0%B5%D0%BB%D1%8C_%D0%B8%D0%B7_%D0%A4%D1%80%D1%8F%D0%B7%D0%B8%D0%BD%D0%BE.png';
      };
    } else { 
      photoEl.style.display='none'; 
    }
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
  const roster=document.getElementById('roster');
  const rosterTable=document.getElementById('roster-table-wrap');
  const lineup=document.getElementById('lineup');
  let listMode=false;
  function setViewMode(list){
    roster.style.display=list?'none':'';
    if(rosterTable) rosterTable.style.display=list?'':'none';
    lineup.classList.toggle('list-mode',list);
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

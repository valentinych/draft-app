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

function sortRoster(){
  const roster=document.getElementById('roster');
  const order={GK:0,DEF:1,MID:2,FWD:3};
  Array.from(roster.children)
    .sort((a,b)=>order[a.dataset.pos]-order[b.dataset.pos])
    .forEach(el=>roster.appendChild(el));
}

function initDrag(){
  Sortable.create(document.getElementById('roster'), {
    group:'players', animation:150, sort:false,
    onAdd: sortRoster
  });
  ALL_POSITIONS.forEach(pos=>{
    Sortable.create(document.getElementById('slot-'+pos), {
      group:'players', animation:150, sort: pos==='BENCH',
      onAdd: evt => {
        const itemPos=evt.item.dataset.pos;
        if(pos!=='BENCH' && itemPos!==pos){
          evt.from.insertBefore(evt.item, evt.from.children[evt.oldIndex]);
          return;
        }
        const max=parseInt(evt.to.dataset.max||'0',10);
        if(max && evt.to.children.length>max){
          evt.from.insertBefore(evt.item, evt.from.children[evt.oldIndex]);
        }
      }
    });
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

document.addEventListener('DOMContentLoaded', ()=>{
  const editable=document.getElementById('lineup').dataset.editable==='1';
  buildSlots();
  if(editable){
    initDrag();
  }
  placePreset();
  sortRoster();
  if(editable){
    const roster=document.getElementById('roster');
    document.getElementById('formation').addEventListener('change', ()=>{
      POSITIONS.forEach(pos=>{
        const wrap=document.getElementById('slot-'+pos);
        wrap.querySelectorAll('.player').forEach(p=>roster.appendChild(p));
      });
      buildSlots();
      initDrag();
    });
    document.getElementById('lineup-form').addEventListener('submit', serialize);
  }
});

function formationCounts(fmt){
  const parts = fmt.split('-').map(x=>parseInt(x,10));
  if(parts.length!==3||parts.some(isNaN)) return {GK:1,DEF:4,MID:4,FWD:2};
  return {GK:1,DEF:parts[0],MID:parts[1],FWD:parts[2]};
}

function buildSlots(){
  const fmt=document.getElementById('formation').value;
  const counts=formationCounts(fmt);
  ['GK','DEF','MID','FWD'].forEach(pos=>{
    const wrap=document.getElementById('slot-'+pos);
    wrap.innerHTML='';
    wrap.dataset.max=counts[pos];
  });
}

function initDrag(){
  Sortable.create(document.getElementById('roster'), {group:'players', animation:150, sort:false});
  ['GK','DEF','MID','FWD'].forEach(pos=>{
    Sortable.create(document.getElementById('slot-'+pos), {
      group:'players', animation:150,
      onAdd: evt => {
        const max=parseInt(evt.to.dataset.max||'0',10);
        if(evt.to.children.length>max){
          evt.from.insertBefore(evt.item, evt.from.children[evt.oldIndex]);
        }
      }
    });
  });
}

function placePreset(){
  const data=document.getElementById('lineup-data');
  if(!data) return;
  try{
    const ids=JSON.parse(data.textContent||'[]');
    ids.forEach(pid=>{
      const el=document.querySelector(`#roster .player[data-id='${pid}']`);
      if(el){
        const pos=el.dataset.pos;
        const target=document.getElementById('slot-'+pos);
        if(target) target.appendChild(el);
      }
    });
  }catch(e){}
}

function serialize(){
  const ids=[];
  ['GK','DEF','MID','FWD'].forEach(pos=>{
    const wrap=document.getElementById('slot-'+pos);
    wrap.querySelectorAll('.player').forEach(p=>ids.push(p.dataset.id));
  });
  document.getElementById('player_ids').value=ids.join(',');
}

document.addEventListener('DOMContentLoaded', ()=>{
  const editable=document.getElementById('lineup').dataset.editable==='1';
  buildSlots();
  if(editable){
    initDrag();
  }
  placePreset();
  if(editable){
    const roster=document.getElementById('roster');
    document.getElementById('formation').addEventListener('change', ()=>{
      ['GK','DEF','MID','FWD'].forEach(pos=>{
        const wrap=document.getElementById('slot-'+pos);
        wrap.querySelectorAll('.player').forEach(p=>roster.appendChild(p));
      });
      buildSlots();
      initDrag();
    });
    document.getElementById('lineup-form').addEventListener('submit', serialize);
  }
});

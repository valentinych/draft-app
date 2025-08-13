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

document.addEventListener('DOMContentLoaded',()=>{
  const editable=document.getElementById('lineup').dataset.editable==='1';
  buildSlots();
  placePreset();
  if(editable){
    const roster=document.getElementById('roster');
    roster.addEventListener('click',e=>{
      const p=e.target.closest('.player');
      if(p) handlePlayerClick(p);
    });
    ALL_POSITIONS.forEach(pos=>{
      const wrap=document.getElementById('slot-'+pos);
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
  }
});

const maxStationPhotos = 24;
const by = id => document.getElementById(id);
const makeStation = (name='', files=[]) => ({id:crypto.randomUUID(), name, files, source:null, result:null, useOriginals:false, spherical:false, exits:new Map(), suggestions:[]});
let stations = [makeStation()], pending = false;
let project = crypto.randomUUID(), parent = null, entry = '', projectNotes = {};
let routeChanges = [];
let compiledVenue, compiledNavigation;
const taskQuery = new URLSearchParams(location.search);
const taskPage = taskQuery.get('task') === '1';
let taskDirty = false, savedGrade, previousRevision, focusedDirection, confirmation, confirmationIssue;
const exactConfirmation = taskPage && taskQuery.get('confirm') === '1' &&
  (taskQuery.has('from_id') || taskQuery.has('to_id'));
function activeFrame(s) { return s.useOriginals ? s.result?.photo_revision : s.result?.view_revision; }
function setProject(key=crypto.randomUUID(), revision=null) {
  project=key; parent=revision;
  by('project-key').value=revision ? key : '';
  by('project-version').value=revision || '';
}
const error = by('error'), preview = by('preview');
by('toolbar').style.display = 'none';

function drawInputs() {
  by('inputs').replaceChildren();
  if(!stations.some(s=>s.id===entry)) entry='';
  const refreshEntries=()=>{
    by('entry').replaceChildren(new Option('路线第一站',''),
      ...stations.map((s,i)=>new Option(s.name.trim() || '点位 '+(i+1),s.id)));
    by('entry').value=entry;
  };
  refreshEntries();
  stations.forEach((s, i) => {
    const row = document.createElement('fieldset');
    row.innerHTML = '<legend></legend><label>点位名称 <input type="text" maxlength="70"></label><label>照片（拍了一圈可一起选拍摄记录 JSON） <input type="file" accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp,.json" multiple></label><small class="files"></small><div class="row"><button type="button" data-up>上移</button><button type="button" data-down>下移</button><button type="button" data-remove>移除</button></div>';
    row.querySelector('legend').textContent = (entry || stations[0].id) === s.id ? '第 '+(i+1)+' 站 · 入口' : '第 ' + (i+1) + ' 站';
    const name = row.querySelector('[type=text]');
    name.value=s.name; name.placeholder='例如：前台';
    name.oninput=()=>{s.name=name.value;refreshEntries();changed()};
    const picker=row.querySelector('[type=file]'), summary=row.querySelector('.files');
    const mode = document.createElement('label');
    const sphere = document.createElement('input');
    sphere.type = 'checkbox';
    sphere.checked = s.spherical;
    sphere.onchange = () => {
      s.spherical = sphere.checked;
      s.result = null;
      s.useOriginals = false;
      s.exits.clear();
      changed();
    };
    mode.append(sphere, ' 使用一张已有的 360° 球面图');
    summary.after(mode);
    const describe=()=>{
      const guided=s.files.some(isCapture) || !!s.source?.capture;
      sphere.disabled=guided;
      if(guided){sphere.checked=false;s.spherical=false;}
      summary.textContent=s.source ? s.source.files.map(f=>f.split('/').pop()).join('、') : !s.files.length?'尚未选择照片':guided?`已选择 ${s.files.filter(f=>!isCapture(f)).length} 张照片及拍摄记录`:s.files.map(f=>f.name).join('、');
    };
    picker.onchange=()=>{if(!picker.files.length)return;setFiles(s,[...picker.files]);describe();};
    describe();
    const move=(offset)=>{[stations[i],stations[i+offset]]=[stations[i+offset],stations[i]];drawInputs();changed()};
    row.querySelector('[data-up]').disabled=i===0;
    row.querySelector('[data-down]').disabled=i===stations.length-1;
    row.querySelector('[data-remove]').disabled=stations.length===1;
    row.querySelector('[data-up]').onclick=()=>move(-1);
    row.querySelector('[data-down]').onclick=()=>move(1);
    row.querySelector('[data-remove]').onclick=()=>{
      stations.forEach(from=>from.exits.forEach((_exit,to)=>{
        if(from===s || to===s) routeChanges.push({from_id:from.id,to_id:to.id,reason:'用户在制作面板移除此站'});
      }));
      stations.splice(i,1);stations.forEach(other=>other.exits.delete(s));drawInputs();changed();
    };
    by('inputs').append(row);
  });
  by('add').disabled=stations.length>=32;
}
function setFiles(station, files) {
  station.files=files;
  station.source=null;
  station.result=null;
  station.useOriginals=false;
  station.exits.clear();
  changed();
}
function changed() {
  if(window.activeTour) {
    window.activeTour.destroy(); window.activeTour=null;
    preview.replaceChildren(); by('review').replaceChildren(); by('hotspot-editor').hidden=true; by('toolbar').style.display='none'; by('empty').hidden=false;
  }
  error.textContent='';
}
function setBusy(busy) {
  pending=busy;
  by('builder').inert=busy;
  by('review').inert=busy;
  by('visitor-task').inert=busy;
  preview.inert=busy;
  by('hotspot-editor').inert=busy;
  for(const id of ['sample-phone','sample-cave','sample-street','export','open-project']) by(id).disabled=busy;
}
function targetsFor(i) {
  const connections = projectNotes.venue?.connections;
  if (!projectNotes.venue) return projectNotes.forward_only ? [i+1] : [i-1,i+1];
  return (connections || []).filter(c => c.from === stations[i].id && !c.pending)
    .map(c => stations.findIndex(s => s.id === c.to)).filter(to => to >= 0);
}
function taskMode() { return projectNotes.visitor_route || projectNotes.venue; }
function linksFor(i) {
  const station = stations[i];
  return targetsFor(i).flatMap(to => (station.exits.get(stations[to]) || [])
    .filter(anchor => (!projectNotes.venue && anchor.cut) || (!anchor.cut &&
      (!projectNotes.venue || anchor.photo === undefined) &&
      anchor.confirmed_by && anchor.evidence && anchor.view_revision === activeFrame(station)))
    .map(({view_revision, confirmed_by, evidence, ...anchor}) => ({...anchor,to})));
}
function dataForExport() {
  return {title:by('title').value.trim()||'空间预览', visitor_route:projectNotes.visitor_route, fixed_route:projectNotes.fixed_route,
    venue:compiledVenue, navigation_map:compiledNavigation,
    first_look:projectNotes.first_look, forward_only:projectNotes.forward_only, grade:taskDirty ? null : savedGrade,
    entry:Math.max(0,stations.findIndex(s=>s.id===entry)), stations:stations.map((s,i)=>{
    const item={...s.result,name:s.name.trim()||'点位 '+(i+1),links:linksFor(i)};
    if(projectNotes.fixed_route) item.opening_photo=s.openingPhoto;
    if(taskMode()) item.opening=s.opening?.view_revision===activeFrame(s) ? s.opening : null;
    if(s.useOriginals && item.panorama) Object.assign(item,{panorama:null,kind:'photos',reason:'本点位展示原图。'});
    return item;
  })};
}
function redrawPreview(stationIndex=null) {
  if(window.activeTour) window.activeTour.destroy();
  window.activeTour=mountTour(preview, dataForExport(), {
    initialStation: stationIndex,
    onStationChange:()=>{
      if(focusedDirection && focusedDirection.from!==window.activeTour?.station()) focusedDirection=null;
      drawHotspotEditor();
    },
    onPlace(i, {to, ...anchor}) {
      const station = stations[i], target = stations[to];
      if(confirmationIssue || (taskPage && !focusedDirection) ||
          (focusedDirection && (focusedDirection.from!==i || focusedDirection.to!==to))) return;
      if (projectNotes.venue && (anchor.photo !== undefined || !targetsFor(i).includes(to))) return;
      if(projectNotes.fixed_route) {
        if(focusedDirection?.from!==i || focusedDirection?.to!==to || !walkCandidate(station,target)) return;
        station.walk[target.id]={...station.walk[target.id],frame:station.result.walk_frames[target.id],
          confirmed_by:'现场操作者',
          evidence:`操作者在「${station.name}」实拍上点选通向「${target.name}」的通道，并确认本段走法`};
      }
      const remaining = (station.exits.get(target) || []).filter(point => !point.cut && point.photo !== anchor.photo);
      const prior=(station.exits.get(target) || []).find(point=>point.photo===anchor.photo && !point.cut);
      station.exits.set(target, [...remaining,{...prior,...anchor,view_revision:activeFrame(station),
        confirmed_by: projectNotes.venue && prior?.confirmed_by ||'工作台操作者',
        evidence:projectNotes.visitor_route ? `操作者在「${station.name}」源站实景点选通往「${target.name}」的可通行位置` :
          prior?.evidence || '操作者在源站实景画面确认该方向的位置'}]);
      if(projectNotes.next_action?.kind==='confirm' &&
        projectNotes.next_action.from_id===station.id && projectNotes.next_action.to_id===target.id) {
        delete projectNotes.next_action;
        station.suggestions=station.suggestions.filter(s=>s?.to_id!==target.id);
        confirmation=null;
      }
      focusedDirection=null;
      markTaskDirty();
      window.activeTour.links(i).splice(0, Infinity, ...linksFor(i));
      drawHotspotEditor(projectNotes.fixed_route ? '这段方向已确认，保存后可试走新版。' : '箭头已放好，可以在画面里点它试走。');
    }
  });
  drawHotspotEditor();
}
function drawHotspotEditor(message = '') {
  drawTask(message);
  const panel = by('hotspot-editor');
  panel.hidden = stations.length < 2 || Boolean(projectNotes.first_look || projectNotes.fixed_route ||
    focusedDirection || confirmationIssue);
  if (panel.hidden) return;
  const current = window.activeTour.station();
  const station = stations[current], actions = by('hotspot-actions'), instruction = by('hotspot-status');
  actions.replaceChildren();
  let missing = 0;
  const missingLinks = [];
  for (const [from, source] of stations.entries()) {
    for (const to of targetsFor(from)) {
      if (stations[to] && !linksFor(from).some(link => link.to === to && !link.cut && (source.result.panorama && !source.useOriginals ? link.photo === undefined : link.photo === 0))) missing++;
      if (stations[to] && !linksFor(from).some(link => link.to === to)) missingLinks.push([from,to]);
    }
  }
  by('hotspot-progress').textContent = projectNotes.venue ?
    (missing ? `有 ${missing} 个已登记方向需要绑定源站全景；未核对通道不会显示箭头。` : '已登记且可确认的方向已标记。') : missing ?
    `还有 ${missing} 个方向未标到默认画面。如果画面里没有对应的路口或门口，可在该站原地转向补拍。访客也可以用画面上方的路线按钮切站。` :
    '相邻站的往返方向均已标在画面中。';
  instruction.textContent = message;
  if (missingLinks.length && !projectNotes.venue) {
    const fill = document.createElement('button');
    fill.type = 'button';
    fill.textContent = `给 ${missingLinks.length} 个未连方向加切换按钮`;
    fill.onclick = () => {
      for (const [from,to] of missingLinks) {
        const points = stations[from].exits.get(stations[to]) || [];
        stations[from].exits.set(stations[to], [...points, {cut:true}]);
      }
      markTaskDirty(); redrawPreview(current);
    };
    actions.append(fill);
  }
  for (const to of targetsFor(current)) {
    const target = stations[to];
    if (!target) continue;
    const name = target.name.trim() || '点位 '+(to+1);
    const photo = window.activeTour.photoView();
    const placed = linksFor(current).some(link => link.to === to && !link.cut && link.photo === photo);
    const cut = linksFor(current).some(link => link.to === to && link.cut);
    if (!placed && !projectNotes.venue) {
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.textContent = cut ? '取消直接切换到 ' + name : '仅标注切换到 ' + name;
      toggle.onclick = () => {
        const anchors = (station.exits.get(target) || []).filter(point => !point.cut);
        station.exits.set(target, cut ? anchors : [{cut:true}]);
        markTaskDirty(); redrawPreview(current);
      };
      actions.append(toggle);
    }
    const set = document.createElement('button');
    set.type = 'button';
    set.disabled = Boolean(projectNotes.venue && (!station.result.panorama || photo !== undefined));
    set.textContent = (placed ? '调整指向 ' : '放置去 ') + name + ' 的箭头';
    set.onclick = () => {
      if(taskPage) focusedDirection={from:current,to};
      window.activeTour.setPlacement(to);
      const message='请在画面里点击通向「'+name+'」的路面或门口。先拖动找方向也可以。';
      if(taskPage) drawHotspotEditor(message);
      else instruction.textContent=message;
      preview.querySelector('#scene').scrollIntoView({block:'nearest'});
    };
    actions.append(set);
    if (placed) {
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '删除指向 '+name+' 的箭头';
      remove.onclick = () => {
        routeChanges.push({from_id:station.id,to_id:target.id,reason:'用户在画面中删除此箭头'});
        station.exits.set(target, (station.exits.get(target) || []).filter(point => point.photo !== photo));
        markTaskDirty();
        window.activeTour.setPlacement(null);
        window.activeTour.links(current).splice(0, Infinity, ...linksFor(current));
        window.activeTour.drawLinks();
        drawHotspotEditor();
      };
      actions.append(remove);
    }
  }
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.textContent = '取消放置';
  cancel.onclick = () => {window.activeTour.setPlacement(null);instruction.textContent = '';};
  actions.append(cancel);
}
function showReview() {
  const review=by('review');review.replaceChildren();
  const lead=document.createElement('h2');lead.textContent='确认每站画面';review.append(lead);
  stations.forEach((s,i)=>{
    const p=document.createElement('p'), item=s.result;
    let detail=item.kind==='panorama' ? `${item.reason ? item.reason.replace(/。?$/, '。') : ''}已用 ${item.matched.length}/${item.originals.length} 张。请检查门框、桌沿等是否接顺。` : item.reason;
    if(item.panorama && !s.useOriginals) {
      const {haov,vaov,width,height}=item.panorama;
      detail += ` 实拍范围 ${Math.round(haov)}° × ${Math.round(vaov)}°${width && height ? '，'+width+'×'+height+' 像素' : ''}。`;
    }
    if(item.panorama && item.matched.length < item.originals.length) {
      const unused=item.originals.filter((_,n)=>!item.matched.includes(n)).map(f=>f.name);
      detail += ' 未参与拼接：'+unused.join('、')+'。';
    }
    p.textContent=`${s.name.trim()||'点位 '+(i+1)}：${detail}`;
    review.append(p);
    if(item.followup){
      const hint=document.createElement('p');
      hint.textContent=item.followup;
      review.append(hint);
      if(!s.source?.capture && !s.files.some(isCapture) && (s.source?.files.length ?? s.files.length)<maxStationPhotos){
        const label=document.createElement('label'), input=document.createElement('input');
        input.type='file';
        input.accept='image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp';
        input.multiple=true;
        label.append(`给「${s.name.trim()||'点位 '+(i+1)}」补照片 `, input);
        input.onchange=async()=>{
          if(pending || !input.files.length)return;
          const files=s.source ? [...input.files] : [...s.files,...input.files];
          if(files.length+(s.source?.files.length || 0)>maxStationPhotos){
            hint.textContent=`这站合计最多 ${maxStationPhotos} 张照片，请少选几张，或在上方重新选择。`;
            input.value='';
            return;
          }
          if(photosTooLarge(files)){
            hint.textContent='单张最多 32 MB，每站合计最多 35 MB。请压缩后再补。';
            input.value='';
            return;
          }
          setBusy(true);
          hint.textContent='正在更新这个点位…';
          try {
            const updated=await buildStation(s,files);
            const cuts = new Map([...s.exits].map(([target, anchors]) =>
              [target, anchors.filter(anchor => anchor.cut)]));
            setFiles(s,files);
            s.exits=cuts;
            s.result=updated; s.source=updated.source;
            drawInputs();
            showResult();
          } catch(ex) {
            hint.textContent=ex.message;
            input.value='';
          } finally {
            setBusy(false);
          }
        };
        review.append(label);
      }
    }
    if(item.panorama){
      const label=document.createElement('label'), input=document.createElement('input');
      input.type='checkbox';input.checked=s.useOriginals;
      input.onchange=()=>{s.useOriginals=input.checked;redrawPreview()};
      label.append(input,`「${s.name.trim()||'点位 '+(i+1)}」成品用原图`);review.append(label);
    }
  });
}
async function post(url, data) {
  let res;
  try {res=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});}
  catch {throw Error('未连上本机工作台。恢复服务后打开同一任务，查看是否已经保存。');}
  if(!res.ok) {
    const msg=await res.json().catch(()=>null);
    throw Error(msg?.error || (res.status>=500
      ? '本机工作台暂未响应。请打开同一任务核对保存状态，再重试。' : '无法处理，请核对项目或照片后重试。'));
  }
  return res;
}
function encoded(f) {
  return new Promise((resolve,reject)=>{
    const reader=new FileReader();
    reader.onload=()=>resolve({name:f.name,data:reader.result.split(',')[1]});
    reader.onerror=()=>reject(Error('无法读取 '+f.name));reader.readAsDataURL(f);
  });
}
function isCapture(file) { return /\.json$/i.test(file.name); }
function photosTooLarge(photos) {
  return photos.some(f=>f.size>32*1024*1024) ||
    photos.reduce((sum,f)=>sum+f.size,0)>35*1024*1024;
}
async function buildStation(station, inputs=station.files, replace=false) {
  const record=inputs.find(isCapture);
  let capture;
  if(record) {
    try {
      capture=JSON.parse(await record.text());
      if(!capture || !Array.isArray(capture.pictures))throw Error();
    } catch {
      throw Error(`第 ${stations.indexOf(station)+1} 站的拍摄记录无法读取。`);
    }
  }
  const files=await Promise.all(inputs.filter(f=>!isCapture(f)).map(encoded));
  const result=await (await post('/api/station',{project,name:station.name,spherical:station.spherical,capture,
    capture_name:record?.name, files,sources:replace ? undefined : station.source?.files,
    capture_source:replace ? undefined : station.source?.capture})).json();
  if(!station.source && station.exits.size) {
    for(const [target, anchors] of station.exits) station.exits.set(target,anchors.map(anchor =>
      anchor.cut || anchor.view_revision ? anchor : {...anchor,view_revision:result.view_revision,
        confirmed_by:'前期样片',evidence:'归档洞穴路线锚点'}));
  }
  return result;
}
function showResult() {
  const index=taskPage ? stations.findIndex(s=>s.id===taskQuery.get('station')) : -1;
  const source=taskPage && !confirmationIssue ? currentConfirmation()?.from : null;
  redrawPreview(index < 0 ? source ?? null : index);
  showReview();
  by('empty').hidden=true;
  by('toolbar').style.display=taskMode() ? 'none' : 'flex';
  error.textContent='预览已生成。';
}
function markTaskDirty() {
  if(taskMode()) taskDirty=true;
}
function walkCandidate(source,target) {
  const direction=source.walk?.[target.id];
  return Boolean(direction?.see && direction.action && direction.arrive);
}
function firstPending() {
  if(projectNotes.first_look) return null;
  if(projectNotes.fixed_route) {
    if(savedGrade?.blocked) return null;
    for(let from=0;from<stations.length-1;from++) {
      const to=from+1, source=stations[from], target=stations[to];
      const step=source.walk?.[target.id];
      if(!walkCandidate(source,target) || !step.confirmed_by || !step.evidence ||
          (projectNotes.fixed_route.public_photos && !step.evidence_url) ||
          step.frame!==source.result.walk_frames[target.id]) return {from,to};
    }
    return null;
  }
  for(const [from, station] of stations.entries()) {
    for(const to of (projectNotes.venue ? targetsFor(from) : projectNotes.forward_only ? [from+1] : [from+1,from-1])) {
      if(projectNotes.venue && (!station.result.panorama || station.useOriginals)) continue;
      if(stations[to] && !linksFor(from).some(link=>link.to===to && !link.cut &&
          (station.result.panorama ? link.photo===undefined : link.photo===0))) return {from,to};
    }
  }
  return null;
}
function currentConfirmation() {
  if(confirmationIssue) return null;
  if(confirmation) return confirmation;
  if(exactConfirmation || projectNotes.next_action?.kind==='confirm') return null;
  return firstPending();
}
function versionPreview(revision) {
  return '/?'+new URLSearchParams({preview:'1',project,revision,
    station:stations[window.activeTour.station()].id});
}
function taskReview(key, revision) {
  return '/?'+new URLSearchParams({history:'1',project:key,...(revision ? {revision} : {})});
}
function drawTask(message = '') {
  const panel=by('visitor-task');
  panel.hidden=!taskMode() || !parent;
  by('task-heading').hidden=panel.hidden;
  const routeTools=by('task-route-tools'), arrows=by('hotspot-editor');
  routeTools.hidden=panel.hidden || Boolean(projectNotes.first_look || projectNotes.fixed_route);
  if(!panel.hidden) routeTools.append(arrows);
  else if(arrows.parentElement===routeTools) by('review').before(arrows);
  if(panel.hidden) return;
  const gap=focusedDirection || currentConfirmation(), confirm=by('task-confirm'), open=by('task-opening');
  const current=window.activeTour.station();
  const atSource=gap?.from===current;
  const atEntry=stations[current].id===entry;
  const session=projectNotes.workflow?.session;
  const chat=typeof session==='string' && /^agent:space-preview:[a-z0-9:_-]+$/i.test(session)
    ? 'http://127.0.0.1:18820/chat?'+new URLSearchParams({session}) : '';
  const photo=projectNotes.next_action?.kind==='photo' ? projectNotes.next_action : null;
  drawPhotoIssue(photo);
  const canConfirm=gap && !(projectNotes.fixed_route &&
    (projectNotes.fixed_route.public_photos || !walkCandidate(stations[gap.from],stations[gap.to])));
  by('task-title').textContent=by('title').value;
  const inputCount=stations.reduce((sum,s)=>sum+(s.source?.files?.length || 0)+(s.source?.alternates?.length || 0),0);
  by('task-current').textContent=`${inputCount} 张照片 · ${current+1} / ${stations.length} · ${stations[current].name}`;
  by('task-version').textContent=photo && !taskDirty ? photo.title || '待补一张全景' : `保存版 ${parent.slice(0,8)}`;
  const route=projectNotes.first_look ? '照片编号供核对。可调整本站开场，或回原聊天确认入口、顺序和通路。' :
    gap ? `从「${stations[gap.from].name}」去「${stations[gap.to].name}」` :
    projectNotes.venue ? savedGrade?.reasons?.[0] || '查看分层总览' :
    savedGrade?.route_ready || taskDirty ? '相邻通道已标记' : '查看试走版中的交付检查';
  const gradeLabel=projectNotes.first_look ? '实景草稿' : savedGrade?.kind==='venue-preview' ? '分层预览' :
    projectNotes.archive_notice ? '档案试走' :
    savedGrade?.kind==='limited' && stations.every(s=>s.result.panorama) ? '路线预览' : savedGrade?.label;
  const note=gap && (projectNotes.fixed_route ? stations[gap.from].walk?.[stations[gap.to].id]?.pending
    : stations[gap.from].suggestions.find(s=>s?.to_id===stations[gap.to].id && typeof s.pending==='string')?.pending);
  by('task-grade').textContent=taskDirty?'有未保存修改':gradeLabel || '路线预览';
  by('task-grade').classList.toggle('is-preview',!savedGrade?.route_ready || Boolean(projectNotes.archive_notice));
  by('task-next').textContent=confirmationIssue ? '这条确认链接已失效' :
    taskDirty ? '保存本次调整' : focusedDirection ? '在画面上确认通道' :
    photo ? photo.title || '补一张全景' : canConfirm ? '核对一处通道' :
    projectNotes.first_look ? '核对照片与开场' : '预览交付效果';
  by('task-state').textContent=message || confirmationIssue || (photo && !focusedDirection ? photo.note || '已有照片和草稿会保留。' :
    gap && !atSource ? `当前画面是「${stations[current].name}」。待确认的是${route}；先打开源站，再点通道位置。` :
    route+(note ? '。'+note : '')+(focusedDirection ? '。请在画面上点通往目标的门洞或走道。' : ''));
  confirm.hidden=!canConfirm || Boolean(photo || focusedDirection || taskDirty);
  confirm.textContent=atSource ? '在当前画面标通道' : `打开「${stations[gap?.from]?.name || '源站'}」核对`;
  by('task-photo').hidden=!photo || !chat || taskDirty || Boolean(focusedDirection);
  if(chat) {
    by('task-photo').href=chat;
    by('task-chat').href=chat;
  }
  by('task-chat').hidden=!chat;
  by('task-skip').hidden=!focusedDirection;
  open.textContent='保存当前画面为本站开场';
  if(projectNotes.fixed_route) {
    if(!stations[current].result.panorama) open.textContent=atEntry ? '将当前照片设为开场' : '保存本站初始照片';
    open.disabled=Boolean(focusedDirection);
  } else open.disabled=!stations[current].result.panorama || stations[current].useOriginals || Boolean(focusedDirection);
  open.disabled ||= taskDirty || Boolean(confirmationIssue);
  by('task-save').hidden=!taskDirty || Boolean(confirmationIssue);
  const refresh=by('task-refresh');
  refresh.hidden=!confirmationIssue;
  refresh.href='/?'+new URLSearchParams({task:'1',project,station:stations[current].id});
  by('task-cancel').hidden=!taskDirty;
  by('task-entry').hidden=atEntry || Boolean(projectNotes.first_look || projectNotes.fixed_route || projectNotes.venue || focusedDirection || confirmationIssue);
  by('task-download').hidden=Boolean(projectNotes.first_look || projectNotes.archive_notice) || savedGrade?.kind==='venue-preview';
  by('task-download').disabled=taskDirty || !savedGrade?.route_ready || Boolean(projectNotes.archive_notice) ||
    Boolean(projectNotes.workflow && !savedGrade?.panorama_ready);
  by('task-latest').href=taskReview(project);
  by('task-preview').href=versionPreview(parent);
  by('task-preview').textContent=taskDirty ? '试走修改前的保存版' : '试走保存版';
  by('task-preview').classList.toggle('primary-action',!taskDirty && !focusedDirection && !canConfirm && !confirmationIssue && (!photo || !chat));
  by('task-previous').hidden=!previousRevision;
  if(previousRevision) by('task-previous').href=taskReview(project,previousRevision);
  const files=(projectNotes.workflow?.batches || []).flatMap(batch=>batch.files);
  by('task-intake').hidden=!files.length;
  by('task-intake-files').replaceChildren(...files.map(file=>{
    const item=document.createElement('li');
    item.textContent=file.name+' · '+(file.status==='received'?'原件已入任务':file.reason || '未收到原件');
    return item;
  }));
}
function drawPhotoIssue(photo) {
  let card=by('task-photo-issue');
  if(!card){card=document.createElement('section');card.id='task-photo-issue';by('task-state').after(card);}
  card.replaceChildren();card.hidden=!photo;
  if(!photo)return;
  const station=stations.find(s=>s.id===photo.station_id);
  const note=document.createElement('p');
  if(!station){
    note.textContent=photo.station_id ? '这条补图说明还没有绑定具体点位，请回原聊天确认要补哪一站；已有照片会保留。' :
      '这是尚未加入任务的新点位。请点“补这一张”回原聊天上传；已有照片和已确认方向会保留。';
    card.append(note);return;
  }
  const heading=document.createElement('h3');heading.textContent='只替换「'+station.name+'」';card.append(heading);
  for(const original of station.result.originals || []){
    const figure=document.createElement('figure'),image=document.createElement('img'),caption=document.createElement('figcaption');
    image.src=original.image;image.alt=station.name+' · '+original.name;
    caption.textContent=original.name;figure.append(image,caption);card.append(figure);
  }
  note.textContent=photo.note || station.result.followup || station.result.reason || '请补一张同机位完整全景。';card.append(note);
  const label=document.createElement('label'),input=document.createElement('input');
  input.type='file';input.accept='image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp';
  input.disabled=pending || taskDirty || Boolean(confirmationIssue);
  label.append('选择这站的新原图（原保存版仍保留）',input);card.append(label);
  const status=document.createElement('p');status.setAttribute('role','status');card.append(status);
  input.onchange=async()=>{
    if(pending || !input.files.length)return;
    const files=[...input.files];
    if(photosTooLarge(files)){status.textContent='单张最多 32 MB。请选择同机位的合格原图；不必重传其他站。';return;}
    setBusy(true);status.textContent='正在检查并替换这一个点位…';
    try{
      const oldFrame=activeFrame(station),updated=await buildStation(station,files,true);
      if(oldFrame!==updated.view_revision){
        for(const [target,anchors]of station.exits){
          if(anchors.some(a=>!a.cut))routeChanges.push({from_id:station.id,to_id:target.id,reason:'用户替换本站原图，原方向须在新画面重新确认'});
          station.exits.set(target,anchors.filter(a=>a.cut));
        }
      }
      station.files=[];station.result=updated;station.source=updated.source;station.useOriginals=false;
      delete projectNotes.next_action;markTaskDirty();
      redrawPreview(stations.indexOf(station));showReview();
      drawTask('已替换这一站，其他点位和方向保留。请保存；本站画面改变的方向需要重新核对。');
    }catch(ex){status.textContent=ex.message+'；其他站未改变，只需重新选择这一张。';input.value='';}
    finally{setBusy(false);}
  };
}
by('task-confirm').onclick=()=>{
  const gap=currentConfirmation();
  if(!gap || (projectNotes.fixed_route &&
    (projectNotes.fixed_route.public_photos || !walkCandidate(stations[gap.from],stations[gap.to])))) return;
  focusedDirection=gap;
  window.activeTour.goTo(gap.from);
  window.activeTour.setPlacement(gap.to);
  const planned=stations[gap.from].walk?.[stations[gap.to].id];
  drawHotspotEditor(`从「${stations[gap.from].name}」去「${stations[gap.to].name}」。请在画面中点通行的门洞或走道。` +
    (planned ? ` 只在现场确认「${planned.action}」正确后点选。` : ' 也可以暂时跳过。'));
  preview.querySelector('#scene').scrollIntoView({block:'nearest'});
};
by('task-skip').onclick=()=>{
  focusedDirection=null; window.activeTour.setPlacement(null);
  drawHotspotEditor('已跳过，待确认的通道仍保留为站点浏览。');
};
by('task-cancel').onclick=()=>location.assign('/?'+new URLSearchParams({task:'1',project,revision:parent,
  station:stations[window.activeTour.station()].id}));
by('task-entry').onclick=()=>{
  entry=stations[window.activeTour.station()].id;
  markTaskDirty();
  drawTask('路线入口将改为当前站。各站开场画面保持原样。');
};
by('task-opening').onclick=async()=>{
  const current=window.activeTour.station();
  const view=window.activeTour.view(), station=stations[current];
  if(projectNotes.fixed_route && !station.result.panorama) {
    station.openingPhoto=window.activeTour.photoView();
    markTaskDirty();
    await saveProject(false, '本站开场已保存。画面已回到本次保存的开场。');
    return;
  }
  if(!view?.isLoaded()) {
    by('task-state').textContent='请先打开全景，再把当前视角设为开场。';
    return;
  }
  station.opening={yaw:Math.round(view.getYaw()*100)/100,pitch:Math.round(view.getPitch()*100)/100,
    hfov:Math.round(view.getHfov()*100)/100,
    view_revision:activeFrame(station)};
  markTaskDirty();
  await saveProject(false, '本站开场已保存。画面已回到本次保存的开场。');
};
by('task-download').onclick=async()=>{
  if(taskDirty || pending) return;
  setBusy(true);
  try {
    const receipt=await (await post('/api/project/download',{project,revision:parent,
      require_panorama:Boolean(projectNotes.workflow)})).json();
    if(!receipt.download) throw Error(receipt.download_error);
    const link=document.createElement('a');
    link.href=receipt.download; link.click();
  }catch(ex){by('task-state').textContent=ex.message;}finally{setBusy(false);}
};
by('builder').onsubmit=async e=>{
  e.preventDefault();if(pending)return;
  const bad=stations.findIndex(s=>{
    if(s.source) return false;
    const records=s.files.filter(isCapture);
    const photos=s.files.filter(f=>!isCapture(f));
    return records.length>1 || (records.length && records[0].size>256*1024) ||
      photos.length<(records.length?3:1) || photos.length>(records.length?96:maxStationPhotos) ||
      photosTooLarge(photos);
  });
  if(bad!==-1){error.textContent=`第 ${bad+1} 站：普通照片 1–${maxStationPhotos} 张；环拍 3–96 张并附一份拍摄记录。单张最多 32 MB，每站合计最多 35 MB。`;return;}
  changed();setBusy(true);
  try{
    for(const [i,s] of stations.entries()){
      error.textContent=`正在处理第 ${i+1} / ${stations.length} 站…`;
      if(!s.result) { s.result=await buildStation(s,s.source ? [] : s.files); s.source=s.result.source; }
    }
    showResult();
  } catch(ex) {error.textContent=ex.message;} finally {setBusy(false);}
};
by('title').oninput=changed;
by('add').onclick=()=>{if(stations.length<32){stations.push(makeStation());drawInputs();changed()}};
by('entry').onchange=()=>{entry=by('entry').value;drawInputs();if(window.activeTour)redrawPreview();};
by('open-project').onclick=async()=>{
  if(pending)return;
  const key=by('project-key').value.trim(), revision=by('project-version').value.trim();
  setBusy(true);
  if(taskPage) {
    by('task-load-status').textContent='正在加载保存版的画面…';
    by('task-retry').hidden=true;
  }
  try {
    const {manifest,tour,confirmation:candidate,latest_revision}=await (await post('/api/project/load',{project:key,revision})).json();
    if(manifest.venue && !manifest.stations.length) {
      location.assign(`/api/history/${encodeURIComponent(key)}/${manifest.revision}/tour.html`);
      return;
    }
    changed(); setProject(key,manifest.revision); entry=manifest.entry;
    const {title,stations:specs,project:_,revision:_rev,parent:_p,entry:_e,...notes}=manifest;
    projectNotes=notes; compiledVenue=tour.venue; compiledNavigation=tour.navigation_map; routeChanges=[];
    taskDirty=false; savedGrade=tour.grade; previousRevision=manifest.parent; focusedDirection=null;
    stations=specs.map((spec,i)=>({...makeStation(spec.name),id:spec.id,result:tour.stations[i],
      source:{files:spec.files,capture:spec.capture,spherical:spec.spherical,source_archive:spec.source_archive,
        alternates:spec.alternates},spherical:spec.spherical===true,opening:spec.opening,landmark:spec.landmark,
      rawSpec:spec, walk:spec.walk || {}, openingPhoto:spec.opening_photo || 0,
      useOriginals:spec.use_originals===true,suggestions:Array.isArray(spec.suggestions)?spec.suggestions:[]}));
    confirmation=candidate ? {from:specs.findIndex(s=>s.id===candidate.from_id),to:specs.findIndex(s=>s.id===candidate.to_id)} : null;
    confirmationIssue=null;
    if(exactConfirmation) {
      const source=taskQuery.get('from_id');
      if(latest_revision!==manifest.revision) confirmationIssue='这个任务已有更新。仍可查看指定保存版，请主动打开最新版再继续确认。';
      else if(!taskQuery.get('revision') || !candidate || source!==candidate.from_id || taskQuery.get('to_id')!==candidate.to_id)
        confirmationIssue='指定的通道已不在本版待确认项里。可以查看本版，或打开最新版继续。';
    }
    specs.forEach((spec,i)=>(spec.links||[]).forEach(({to_id,...anchor})=>{
      const target=stations.find(s=>s.id===to_id);
      if(target) stations[i].exits.set(target,[...(stations[i].exits.get(target)||[]),anchor]);
    }));
    by('title').value=title;drawInputs();showResult();
    if(exactConfirmation) {
      const from=stations.findIndex(s=>s.id===taskQuery.get('from_id'));
      if(from>=0 && window.activeTour.station()!==from) window.activeTour.goTo(from);
    }
    by('task-loading').hidden=true;
    if(taskPage && !taskMode()) document.body.classList.remove('task-page');
    if(taskQuery.get('project')===project && taskMode()) {
      document.body.classList.add('task-page');
      if(taskQuery.get('confirm')==='1' && !confirmationIssue) by('task-confirm').click();
    }
  }catch(ex){
    error.textContent=ex.message;
    if(taskPage) {
      by('task-load-status').textContent=ex.message;
      by('task-retry').hidden=false;
    }
  }finally{setBusy(false);}
};
by('task-retry').onclick=()=>by('open-project').click();
async function saveProject(openPreview=false, savedMessage='') {
  if(pending || confirmationIssue)return;setBusy(true);
  const current=window.activeTour?.station() ?? null;
  try{
    const tour=dataForExport();
    const manifest={...projectNotes,project,revision:parent,route_changes:routeChanges,title:tour.title,entry:stations[tour.entry].id,
      stations:stations.map(s=>({...s.rawSpec,...(projectNotes.fixed_route ? {walk:s.walk,opening_photo:s.openingPhoto} : {}),
        ...s.source,id:s.id,name:s.name,spherical:s.spherical,
        use_originals:s.useOriginals,suggestions:s.suggestions,opening:s.opening,landmark:s.landmark,links:[...s.exits].flatMap(([target,points])=>
          points.map(point=>({...point,to_id:target.id})))}))};
    const receipt=await (await post('/api/project/save',{project,parent,manifest})).json();
    routeChanges=[];
    previousRevision=parent; setProject(project,receipt.revision);
    if(taskMode()) {
      savedGrade=receipt.grade; taskDirty=false; focusedDirection=null;
      if(taskPage) {
        const url=new URL(receipt.task_url);
        if(current!==null) url.searchParams.set('station',stations[current].id);
        history.replaceState(null,'',url.pathname+url.search);
      }
      redrawPreview(current);
      if(savedMessage) drawTask(savedMessage);
      if(openPreview===true) location.assign(versionPreview(parent));
      return;
    }
    if(receipt.download){
      const a=document.createElement('a');a.href=receipt.download;a.download=tour.title.replace(/[\\/:*?"<>|]/g,'-')+'.html';a.click();
    }
    const outcome=receipt.grade?.label || '预览';
    const status=receipt.grade?.route_ready ?
      (projectNotes.fixed_route ? '已保存路线，请下载后断网核对各站照片和走法。' : '已保存导览，请断网检查各站和画面内箭头。') :
      '已保存为预览或对照，导览尚未完成。';
    error.textContent='「'+outcome+'」'+status+' '+(receipt.download ? '' : receipt.output);
  }catch(ex){
    if(taskMode()) {
      if(exactConfirmation && ex.message.includes('这个任务已有更新')) {
        confirmationIssue='这个任务已有更新。仍可查看指定保存版，请主动打开最新版再继续确认。';
        focusedDirection=null;
        window.activeTour.setPlacement(null);
        drawHotspotEditor();
      } else drawTask(ex.message+'。可重试保存，或试走修改前的保存版。');
    }
    else error.textContent=ex.message;
  }finally{setBusy(false);}
}
by('export').onclick=saveProject;
by('task-save').onclick=()=>saveProject(taskPage);
async function sample(kind) {
  if(pending)return;
  const sphere = kind === 'street';
  const config=kind==='phone' ? [['室内',[1,2].map(i=>`/sample/phone-${i}.jpg`)]] :
    sphere ? ['起点','游道一','游道二','游道三','游道四'].map((name,i)=>[name,[`/sample/sphere-${i+1}.jpg`]]) :
      ['室外入口','入口游道','洞口游道'].map((n,j)=>[n,[1,2,3].map(i=>`/sample/cave-${j+1}-${i}.jpg`)]);
  changed();setBusy(true);error.textContent='正在装入示例照片…';
  try {
    const loaded=[];
    for(const [name,paths] of config){
      const files=[];
      for(const path of paths){
        const res=await fetch(path);if(!res.ok)throw Error('样本文件未找到');
        files.push(new File([await res.blob()],path.split('/').pop(),{type:'image/jpeg'}));
      }
      loaded.push(makeStation(name,files));
    }
    stations=loaded; entry=''; projectNotes={}; setProject();
    if (kind !== 'phone') {
      // Full sphere anchors are from the earlier tour. Partial anchors use the same directions
      // registered to the cropped coverage, with the first marker moved slightly in from the edge.
      const exits = sphere ? [[[1,-.8044,-.1064]], [[2,-.6138,-.0648],[0,.6115,.0720]],
        [[3,1.0128,-.1161],[1,-1.5289,-.0434]], [[4,2.3913,.0151],[2,-1.4958,.0670]],
        [[3,2.2670,.1705]]] : [[[1,-70,-6.08]], [[2,-31.31,-3.68],[0,38.9,4.16]], [[1,-65.72,-2.5]]];
      const units = sphere ? 180/Math.PI : 1;
      for (const [i,station] of stations.entries()) {
        station.spherical = sphere;
        for (const [to,yaw,pitch] of exits[i]) station.exits.set(stations[to],[{yaw:yaw*units,pitch:pitch*units}]);
      }
    }
    by('title').value=kind==='phone'?'室内实拍预览':sphere?'卡尔斯巴德洞穴 · 实景五站':'卡尔斯巴德洞穴 · 三站预览';
    drawInputs();
    error.textContent=kind==='phone'?'两张独立手机原片已加入，可以生成预览。':
      sphere?'五张实拍球面和双向箭头已加入，可以生成预览。':'已加入从真实洞穴全景裁出的九张普通视角图，可用于体验三站流程。';
  }catch(ex){error.textContent=ex.message;}finally{setBusy(false);}
}
by('sample-phone').onclick=()=>sample('phone');
by('sample-cave').onclick=()=>sample('cave');
by('sample-street').onclick=()=>sample('street');
const captureAdvice={
  walk:'从起点实际走到终点，在岔路、转弯和遮挡前后设站。终点拍清正确入口；每段去程、回程各拍可通行的门洞或路口。',
  venue:'先列真实物理楼层，再列各层展区与可自由选择的看点。楼梯、电梯两侧、出口和到达层标牌都要拍；没有连接素材时只能分层看景。',
  campus:'让第一次来的新生能分辨校门、容易走错的岔路和目的地。校名、路牌、楼名与可走路径尽量同框，在转弯后的新视野再设一站。'
};
const captureState = () => document.querySelector('input[name="capture-state"]:checked').value;
const captureFirstStep = {
  new:'先拍入口这一站：站定横拍、转身一圈，相邻约三分之一重叠，门牌和通道清楚。先发一站原图确认效果，不用一次拍完整条路。',
  partial:'先发已有照片，按同一机位分组。先看本人实景草稿，只补缺的站点或方向；不确定的通路不要猜。',
  sphere:'完整球面每站一张原尺寸文件，按地点命名，说明楼层、拍摄日期及已知去回通道。'
};
function updateCapture() {
  const state=captureState(), start=by('capture-start').value.trim(), end=by('capture-end').value.trim();
  for (const key of ['new','partial','sphere']) by('capture-'+key).hidden=key!==state;
  by('capture-scenario').textContent=captureAdvice[by('capture-kind').value];
  const condition={new:'我还没拍照片，只有手机，也不懂拼接。先告诉我入口这一站该怎么拍，试好再规划其他站。',partial:'我只拍了一部分照片。先用现有照片给我看实景草稿，再具体告诉我缺哪一站、站在哪里、朝哪里补拍，不要让我全部重拍。',sphere:'我已有每站一张的完整360°球面原图，帮我按真实地点和已确认通道编排。'}[state];
  by('capture-message').value=`我想做一份${by('capture-kind').selectedOptions[0].textContent}的全景导览，给第一次来的人用${start?'，从'+start+'开始':''}${end?'，希望找到'+end:''}。${condition}最后要能拖着环顾、沿实景箭头逐站往返，下载后离线打开。`;
}
by('capture-kind').onchange=updateCapture;
for (const radio of document.querySelectorAll('input[name="capture-state"]')) radio.onchange=updateCapture;
for (const id of ['capture-start','capture-end']) by(id).oninput=updateCapture;
by('capture-chat').href='http://127.0.0.1:18820/chat?session='+encodeURIComponent('agent:space-preview:dashboard:'+crypto.randomUUID());
by('capture-copy').onclick=async()=>{
  try {await navigator.clipboard.writeText(by('capture-message').value);by('capture-status').textContent='需求已复制。打开制作聊天，粘贴发送即可；已有照片可一起上传。';}
  catch {by('capture-message').focus();by('capture-message').select();by('capture-status').textContent='请复制已选中的需求，再到制作聊天粘贴发送。';}
};
by('capture-kind').onchange();
by('show-capture').onclick=e=>{
  if(by('home').hidden) {location.assign('/#capture-guide');return;}
  e.preventDefault();by('capture-guide').open=true;by('capture-guide').scrollIntoView({block:'start',behavior:'smooth'});
};
if(location.hash==='#capture-guide') by('capture-guide').open=true;
by('start-agent').onclick=e=>{e.preventDefault();by('capture-guide').open=true;by('capture-guide').scrollIntoView({block:'start',behavior:'smooth'});};
by('capture-download').onclick=()=>{
  const kind=by('capture-kind'), start=by('capture-start').value.trim()||'填写起点', end=by('capture-end').value.trim()||'填写目的地';
  const text=`全景导览 · 我的拍摄清单
用途：${kind.selectedOptions[0].textContent}
起点：${start}
目的地/主要看点：${end}

现在先做：${captureFirstStep[captureState()]}

${captureAdvice[kind.value]}

完整全景优先：每站一张完整 2:1 等距柱状球面，建议 6000×3000 或以上，原尺寸 JPG，单张不超过 32 MB。到现场检查前后左右、头顶地面、接缝重影、门牌与曝光；镜头擦净、机位稳定。常规导览每份最多 32 站。
空间覆盖：入口、每条分支、转弯前后、遮挡后的过渡与主要目的地都设站，相邻站分别确认去向和来路。场馆另拍楼梯/电梯两端及层号。
地图可选：附有权使用的平面图/校园图/简图，标同名站点编号和入口、分区、物理楼层，说明每个点的位置依据。没有图时使用非比例通道示意，不根据照片顺序猜地理坐标。

出发前：
□ 画出站点和去回关系；记录每站名称、楼层、地标与拍摄日期
□ 确认允许拍摄与交付，按现场开放通路规划

每一站：
□ 站定同一机位、同一高度，横拍、锁曝光、相邻约三分之一重叠，整圈首尾重叠
□ 门牌/地标清楚，脚下通道与去向同框
□ 下一站回头拍来路；遮挡和转弯加过渡站
□ 楼梯/电梯两侧及到达层标牌齐全
□ 离开前检查模糊、倾斜、漏拍

上传前：
□ 完整球面每站一张原文件；普通照片每站一个文件夹
□ 按 01_入口、02_转弯前、03_转弯后、04_目的地 命名
□ 路线说明写去回关系，如 01 ↔ 02 ↔ 03 ↔ 04，并说明现场依据
□ 上传原图或 ZIP；图片较多时按区域分批发到同一聊天，说明“补充到同一份导览”
□ 先核对自己的实景草稿，缺图只补对应站

可复制到 OpenClaw：
${by('capture-message').value}
`;
  const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));
  const a=document.createElement('a');a.href=url;a.download='全景导览-拍摄与上传清单.txt';a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);by('capture-status').textContent='清单已下载。拍完后在 OpenClaw 上传原图或 ZIP。';
};
drawInputs();
if(taskQuery.has('project') && taskQuery.get('history')!=='1' && taskQuery.get('preview')!=='1') {
  if(taskPage) {
    document.body.classList.add('task-page');
    by('empty').hidden=true;
    by('task-loading').hidden=false;
    by('task-recovery').href=taskReview(taskQuery.get('project'),taskQuery.get('revision'));
    by('task-recovery').hidden=false;
  }
  by('project-key').value=taskQuery.get('project');
  by('project-version').value=taskQuery.get('revision') || '';
  by('open-project').click();
}

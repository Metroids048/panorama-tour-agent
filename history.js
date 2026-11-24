(() => {
  const $ = id => document.getElementById(id);
  const project = $('history-project'), version = $('history-version'), filter = $('history-filter');
  const detail = $('history-detail'), status = $('history-status');
  const query = new URLSearchParams(location.search);
  const producerPreview = query.get('preview') === '1';
  const pinned = (producerPreview || query.get('history') === '1') && query.get('project');
  const pinnedRevision = query.get('revision');
  let entries = [], token = 0;
  const name = (tag, value, cls) => {
    const el = document.createElement(tag);
    el.textContent = value;
    if (cls) el.className = cls;
    return el;
  };
  const url = item => '/api/history/'+encodeURIComponent(item.project)+'/'+encodeURIComponent(item.revision);
  const review = (key, revision) => '/?'+new URLSearchParams({
    history:'1', project:key, ...(revision ? {revision} : {})
  });
  const matches = item => filter.value === 'all' ||
    (filter.value === 'fixed' ? item.grade.kind === 'fixed' :
      item.grade.kind !== 'fixed' && (filter.value === 'full' ? item.grade.route_ready : !item.grade.route_ready));
  const link = (label, href) => {
    const a = name('a', label);
    a.href=href;
    a.target='_blank';
    a.rel='noopener';
    return a;
  };
  const count = item => `${item.photos} 张 · ${item.stations} 站`;
  const stamp = t => new Date(t*1000).toLocaleString('zh-CN', {hour12:false});
  const taskURL = (item, station) => '/?'+new URLSearchParams({
    task:'1', project:item.project, revision:item.revision, ...(station ? {station} : {})
  });
  const readyToSend = item => item.grade.route_ready && !item.grade.blocked && !item.archive_notice &&
    item.fulfillment?.complete !== false && (!item.native || item.grade.panorama_ready);
  async function fetchJSON(path) {
    const response = await fetch(path);
    if (!response.ok) throw new Error(response.status === 404 ? '没有找到这份已保存的导览。请核对任务和版本。' : '暂时无法读取保存版。');
    return response.json();
  }
  function render(item) {
    const base=url(item);
    const main = name('section','', 'history-output');
    if (!producerPreview) main.append(name(pinned ? 'h1' : 'h2', item.title));
    const summary = item.first_look ? '照片可环顾 · 实地通路待确认' : item.grade.kind === 'fixed'
      ? (item.grade.route_ready ? (item.grade.reverse ? '去程和回程已确认' : '单向路线指引') : '待核对的步行路线')
      : item.grade.kind==='venue-preview' && Number.isInteger(item.grade.arrows)
        ? `${item.grade.arrows} 个已标注实景方向`
        : Number.isInteger(item.grade.arrows) ? `${item.grade.arrows}/${item.grade.required} 个实景箭头` : '查看场馆楼层和实景';
    const ready=readyToSend(item);
    const layeredPreview=!item.first_look && item.grade.kind==='venue-preview' && item.stations>0 && !item.grade.blocked;
    const archivePreview=Boolean(item.archive_notice) && !item.grade.blocked;
    const previewType=(item.fulfillment?.arrows === 0 || item.grade.arrows === 0) ? '看景草稿' :
      item.fulfillment?.state === 'partial' ? (layeredPreview ? '分层预览' : '局部实景试走') :
      layeredPreview ? '分层预览' : archivePreview ? '档案试走' :
      item.grade.label.includes('路线预览') ? '路线预览' : '预览';
    const meta=name('div','','history-summary');
    meta.append(name('span',item.first_look ? '实景草稿 · 核对照片' : ready ? '可发 · '+item.grade.label : '仅供核对 · '+(previewType !== '预览' ? previewType : item.grade.label),'history-badge'+(ready?'':' is-preview')),
                name('span',`${count(item)} · ${summary}`,'history-meta muted'));
    const controls=name('div','','row');
    if (producerPreview) {
      controls.classList.add('preview-tools');
      controls.setAttribute('role','group');
      controls.setAttribute('aria-label','制作工具 · '+item.title);
      controls.append(meta.firstElementChild);
    } else main.append(meta);
    const preview=link(item.first_look ? '单独打开实景草稿' : '单独打开访客页面',base+'/tour.html');
    const save=link(ready ? (pinned ? '下载离线导览' : '下载该版离线网页') :
      (previewType !== '预览' ? '下载'+previewType : '下载该版预览'),base+'/tour.html');
    save.download=item.title.replace(/[\\/:*?"<>|]/g,'-')+' - '+(ready ? '离线导览' : previewType)+'.html';
    save.removeAttribute('target');
    save.className=ready ? 'action primary-action' : 'action';
    if (producerPreview) {
      if (query.get('station') || !item.first_look || !item.session) {
        const back=name('a',query.get('station') ? '← 返回制作' : '继续修改这版','action');
        back.href=item.editable ? taskURL(item,query.get('station')) : review(item.project,item.revision);
        controls.append(back);
      }
      if (item.session) {
        const chat=link('回到聊天','http://127.0.0.1:18820/chat?'+new URLSearchParams({session:item.session}));
        chat.className=item.first_look ? 'action primary-action' : 'action';
        controls.append(chat);
      }
      if (item.first_look) {
        const latest=name('a','查看任务最新保存版');
        latest.href=review(item.project);
        controls.append(latest);
      }
    }
    if(ready || layeredPreview || archivePreview || (!item.first_look && item.grade.kind!=='fixed' && !item.grade.blocked)) controls.append(save);
    if (!producerPreview || !item.first_look) controls.append(preview);
    if (pinned && item.editable && !producerPreview) {
      const edit=link('继续修改这版',taskURL(item));
      controls.append(edit);
    }
    const frame=document.createElement('iframe');
    frame.title=item.title+' · 冻结输出';
    frame.setAttribute('sandbox','allow-scripts allow-downloads');
    frame.allow='fullscreen';
    frame.src=base+'/tour.html';
    main.append(controls);
    if (producerPreview) {
      const player = name('div','','preview-player');
      const loading = name('p','正在打开实景…','preview-loading');
      loading.setAttribute('role','status');
      frame.addEventListener('load', () => { loading.hidden=true; }, {once:true});
      player.append(frame,loading);
      main.append(player);
    }
    if (item.first_look && !producerPreview) main.append(name('p','先核对收到的实景，再确认入口、开场和通路。可回原聊天说要改哪张照片。','history-note muted'));
    if (ready) main.append(name('p','保存 HTML 后用浏览器打开，即可断网试走。请把下载的文件发给访客。','history-note muted'));
    if (layeredPreview) main.append(name('p','可离线选层或分区看景。沿实景箭头试走，直接换景不代表现场有通路。','history-note muted'));
    if (archivePreview) main.append(name('p',item.archive_notice,'history-note muted'));
    if (!producerPreview) main.append(frame);
    if (producerPreview) {
      const versions=name('p','','row history-note');
      versions.append(name('span','冻结版本 '+item.revision.slice(0,8),'version'));
      const historyLink=name('a','保存记录');
      historyLink.href=review(item.project,item.revision);
      versions.append(historyLink);
      if (!item.first_look) {
        const latest=name('a','查找本任务最新保存版');
        latest.href=review(item.project);
        versions.append(latest);
      }
      main.append(versions);
      detail.replaceChildren(main);
      return;
    }
    const inputs = name('section','', 'history-input');
    inputs.classList.add('panel');
    inputs.append(name('h2','保存记录'),name('p',`${stamp(item.time)} · ${item.revision.slice(0,8)}`,'version'));
    const versions=name('div','','row');
    if (item.parent) versions.append(link('回看上一版',review(item.project,item.parent)));
    if (pinnedRevision) versions.append(link('查找本任务最新保存版',review(item.project)));
    if (versions.childElementCount) inputs.append(versions);
    inputs.append(name('div','','utility'));
    inputs.append(name('h2','该版的输入照片'));
    if (item.attribution) inputs.append(name('p', item.attribution,'history-meta muted'));
    if (item.plans?.length) {
      const plans = name('ul','');
      for (const plan of item.plans) {
        const row = name('li','');
        row.append(link('查看平面：'+plan.name,plan.url), name('p',plan.source,'muted'));
        plans.append(row);
      }
      inputs.append(name('h3','该版的输入平面'),plans);
    }
    if (item.grade.reasons.length) {
      const box = name('details','', 'history-notes');
      box.append(name('summary','这份结果为什么归为对照'));
      const list = name('ul','');
      item.grade.reasons.forEach(reason => list.append(name('li',reason)));
      box.append(list);
      inputs.append(box);
    }
    item.inputs.forEach((s,i) => {
      const group=name('details','', 'history-station');
      const p=s.view;
      const size=p.width ? ` · ${p.width}×${p.height} 像素` : '';
      const kind=p.haov ? `${Math.round(p.haov)}° × ${Math.round(p.vaov)}°${size}` : '原照';
      const entry=s.id===item.entry ? ' · 入口' : '';
      group.append(name('summary',`${i+1}. ${s.name}${entry} · ${s.count} 张`),
                   name('p',`${s.spherical ? '已有球面图' : s.capture ? '带方向记录的普通照片' : '普通照片'} → ${kind}`,'muted'));
      if (s.note) group.append(name('p',s.note,'muted'));
      if (s.source && typeof s.source==='string' && /^https:\/\//.test(s.source)) group.append(link('素材来源',s.source));
      if (s.source_archive) {
        const archive=link('下载未处理的相机原片',base+'/source-archive/'+i);
        archive.download=s.id+'-originals.zip';
        group.append(name('p','下方照片为同一批相机原片的显色及镜头校正版。','muted'),archive);
      }
      group.addEventListener('toggle', () => {
        if (!group.open || group.querySelector('.history-photos')) return;
        const grid=name('div','','history-photos');
        s.files.forEach((f,j) => {
          const asset=base+`/photo/${i}/${j}`;
          const a=link('',asset), image=document.createElement('img');
          image.src=asset; image.loading='lazy'; image.decoding='async'; image.alt=f.name;
          a.append(image,name('span',f.name));
          grid.append(a);
        });
        group.append(grid);
      });
      inputs.append(group);
    });
    detail.replaceChildren(main, inputs);
  }
  async function open() {
    const serial=++token;
    const item=pinned ? {project:pinned,revision:pinnedRevision || 'latest'} :
      entries.find(p=>p.latest===project.value)?.versions.find(v=>v.revision===version.value);
    detail.replaceChildren();
    if (!item) return;
    $('history-retry').hidden=true;
    status.textContent='正在打开这版的原片和网页…';
    try {
      const result=await fetchJSON(url(item));
      if (serial!==token) return;
      render(result);
      status.textContent=pinned ? '' : '当前为历史回看。';
      $('history-feedback').hidden=Boolean(pinned);
    } catch (e) {
      if (serial!==token) return;
      status.textContent=e.message === 'Failed to fetch' ? '未能连接本机工作台。连接恢复后可重试。' : e.message;
      $('history-retry').hidden=false;
      $('history-browse').hidden=!pinned;
    }
  }
  function pickVersion(keep='') {
    const record=entries.find(p=>p.latest===project.value);
    const list=record?.versions.filter(matches) || [];
    version.replaceChildren(...list.map(v=>new Option(`${stamp(v.time)} · ${v.grade.label} · ${v.revision.slice(0,8)}`,v.revision)));
    if (list.some(v=>v.revision===keep)) version.value=keep;
    open();
  }
  function pickProject() {
    const oldProject=project.value, oldVersion=version.value;
    const list=entries.filter(p=>p.versions.some(matches));
    project.replaceChildren(...list.map(p=>{
      const v=p.versions.find(matches);
      return new Option(`${v.title} · ${count(v)}`,p.latest);
    }));
    if (list.some(p=>p.latest===oldProject)) project.value=oldProject;
    pickVersion(oldVersion);
    $('history-count').textContent=`${list.length} / ${entries.length} 个项目`;
    if (!list.length) status.textContent='这个分类暂无记录。';
  }
  async function refresh() {
    const serial=++token;
    $('history-feedback').hidden=false;
    $('history-retry').hidden=true;
    status.textContent='正在读取历史记录…';
    try {
      const next=await fetchJSON('/api/history');
      if (serial!==token) return;
      entries=next;
      pickProject();
    } catch (e) {
      if (serial===token) {
        status.textContent=e.message;
        $('history-retry').hidden=false;
      }
    }
  }
  async function showRecent() {
    const recent=$('recent-tasks');
    try {
      const records=await fetchJSON('/api/history');
      const rows=records.slice(0,3).map(record=>
        record.versions.find(v=>v.revision===record.latest) || record.versions[0]);
      recent.replaceChildren(...rows.map((item,i)=>{
        const row=name('div','','recent-task');
        const about=name('div','');
        const scene=item.grade.kind === 'fixed' ? '固定路线' : item.grade.kind.startsWith('venue') ? '场馆导览' : '空间导览';
        about.append(name('strong',item.title),name('p',`${scene} · ${count(item)} · ${stamp(item.time)}`,'muted'));
        const state=item.next_action?.kind === 'photo' ? '待补图' : readyToSend(item) ? '可发' :
          item.grade.kind==='venue-preview' ? '分层预览' : item.archive_notice ? '档案试走' : item.grade.label;
        const next=name('a',item.editable ? '继续制作' : '查看保存版','action');
        next.href=item.editable ? taskURL(item) : review(item.project,item.revision);
        row.append(name('span',String(i+1).padStart(2,'0'),'version'),about,
          name('span',state,'history-badge'+(readyToSend(item)?'':' is-preview')),next);
        return row;
      }));
      if (!rows.length) recent.append(name('p','保存导览后，可在这里继续制作。','muted'));
    } catch (error) {
      recent.replaceChildren(name('p','暂时无法读取任务。','muted'));
      const retry=name('button','重试读取'); retry.type='button'; retry.onclick=showRecent;
      recent.append(retry);
    }
  }
  $('show-history').onclick=()=>location.assign(query.get('history')==='1' && !pinned ? '/' : '/?history=1');
  $('history-refresh').onclick=refresh;
  project.onchange=()=>pickVersion();
  version.onchange=open;
  filter.onchange=()=>{if(entries.length) pickProject(); else refresh();};
  $('history-retry').onclick=()=>pinned ? open() : refresh();
  if (pinned) {
    if (producerPreview) document.body.classList.add('producer-preview');
    $('history-picker').hidden=true;
    $('history-feedback').hidden=false;
    $('history').hidden=false;
    $('editor').hidden=true;
    $('show-history').onclick=()=>location.assign('/?history=1');
    open();
  } else if (query.get('history') === '1' || producerPreview) {
    $('history').hidden=false;
    $('editor').hidden=true;
    $('show-history').textContent='返回首页';
    refresh();
  } else if (query.get('editor') === '1') {
    $('show-history').onclick=()=>{
      const opening=$('history').hidden;
      $('history').hidden=!opening;
      $('editor').hidden=opening;
      $('show-history').textContent=opening?'返回制作':'历史任务';
      if(opening) refresh();
      else {++token; detail.replaceChildren();}
    };
  } else if (!query.has('project')) {
    $('editor').hidden=true;
    $('home').hidden=false;
    showRecent();
  }
})();

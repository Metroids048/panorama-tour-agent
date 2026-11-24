function mountTour(root, tour, editor) {
  root.innerHTML = `<div class="shell">
    <div class="bar"><div><div class="eyebrow" data-count></div><h2></h2></div>
      <div class="viewer-controls"><button type="button" data-original></button><button type="button" data-reset>回到初始视角</button></div></div>
    <nav class="nav" aria-label="导览路线">
      <button type="button" data-prev></button><button type="button" data-next></button>
    </nav>
    <p class="view-hint" data-mode></p>
    <div class="stage"><div class="frame"><div id="scene" tabindex="0" aria-label="空间画面"></div></div></div>
    <p class="quality" role="status"></p><div class="thumbs"></div>
  </div>`;
  const pick = (selector) => root.querySelector(selector);
  const scene = pick('#scene'), quality = pick('.quality'), thumbs = pick('.thumbs');
  let station = editor?.initialStation ?? tour.entry ?? 0, photo = 0, originals = false, viewer = null, load = 0, placement = null;
  const fixed = tour.fixed_route;
  if (tour.historical_visit || fixed?.visit_mode === 'virtual') {
    root.classList.add('has-history');
    const scope = document.createElement('p');
    scope.className = 'view-hint';
    scope.textContent = '历史虚拟参观 · ' + (tour.historical_visit?.captured_on || fixed.captured_on) + ' 拍摄，不代表今天现场开放或通行。';
    pick('.bar').after(scope);
  }
  root.classList.toggle('fixed-route', Boolean(fixed));
  const entryPhoto = i => fixed ? (tour.stations[i].opening_photo || 0) : 0;
  photo = entryPhoto(station);
  let lastFrame, lastViewer, lastStation, viewerStation, textureLimit, photoFailure, originalDialog;
  const decoded = new Map(), prepared = new Map();
  const compactViewers = new WeakSet(), lostSurfaces = new WeakSet(), textureFailures = new WeakMap();
  let stopped = false, prefetchTimer, warmScene, backScene;
  function decodeSphere(url) {
    if (decoded.has(url)) return decoded.get(url).promise;
    const image = new Image();
    image.src = url;
    const entry = {image, promise:image.decode().then(() => image)};
    decoded.set(url, entry);
    entry.promise.catch(() => decoded.delete(url));
    while (decoded.size > (tour.stations[station]?.panorama?.width > 10000 ? 2 : 4)) decoded.delete(decoded.keys().next().value);
    return entry.promise;
  }
  function prefetchNext(item) {
    clearTimeout(prefetchTimer);
    prefetchTimer = setTimeout(() => {
      const next = sceneLinks(item).filter(l => !l.cut && l.yaw !== undefined);
      const edge = next.find(l => l.to !== transitionOrigin) || next[0];
      const pano = edge && tour.stations[edge.to].panorama;
      if (!stopped && pano) {
        const target = edge.to;
        if (warmScene?.index === target || backScene?.index === target) return;
        discardWarm();
        const surface = panoramaSurface();surface.classList.add('scene-preload');
        surface.inert = true;surface.setAttribute('aria-hidden','true');
        scene.parentElement.append(surface);
        const warm = {index:target,surface,viewer:null};warmScene=warm;
        const reduced = exceedsTextureLimit(pano);
        warm.ready=prepareSphere(pano,reduced).then(texture=>new Promise((resolve,reject)=>{
          if(warm.cancelled || (warmScene!==warm && station!==target) || stopped){surface.remove();resolve(null);return;}
          try {
            warm.viewer=pannellum.viewer(surface,{...panoramaOptions(tour.stations[target],Math.sign(target-station)),panorama:texture});
            if (reduced) compactViewers.add(warm.viewer);
            warm.viewer.on('load',()=>resolve(warm.viewer));warm.viewer.on('error',reject);
            textureFailures.set(surface,reject);
          } catch(error){reject(error);}
        }));
        warm.ready.catch(()=>{if(warmScene===warm)discardWarm();});
      }
    }, 350);
  }
  let transitionOrigin = null, arrivedFrom = null;
  const localDay = () => {
    const values = Object.fromEntries(new Intl.DateTimeFormat('en', {timeZone:'Asia/Shanghai',
      year:'numeric', month:'2-digit', day:'2-digit'}).formatToParts(new Date()).map(({type,value}) => [type,value]));
    return values.year + '-' + values.month + '-' + values.day;
  };
  const blocked = () => fixed && (tour.grade?.blocked ||
    (fixed.valid_from && localDay() < fixed.valid_from) || (fixed.valid_until && localDay() > fixed.valid_until));
  const mayWalk = to => Boolean(fixed && !blocked() && tour.stations[station].walk?.[to]?.confirmed &&
    (to > station || tour.grade?.reverse));
  const mayInspect = to => Boolean(!blocked() && tour.stations[to]?.panorama);
  let fixedAction, fixedPhotoCredit;
  const guided = tour.visitor_route === true && !tour.first_look;
  const visitorRoute = guided && !editor;
  const detailLimited = tour.grade?.detail_limited || [];
  if (visitorRoute && detailLimited.length) pick('.stage').before(quality);
  const forwardOnly = visitorRoute && tour.forward_only;
  const destinationIndex = tour.destination ?? tour.stations.length - 1;
  const mayBrowse = to => Boolean(guided && blocked() && !fixed.public_photos &&
    Math.abs(station - to) === 1 && tour.stations[station].panorama && tour.stations[to]?.panorama);
  const sceneLinks = item => (item.links || []).filter(link => !fixed || editor || mayWalk(link.to));
  const venue = tour.venue;
  const layered = venue && !editor;
  const shell = pick('.shell');
  let tourNavigator;
  if (tour.first_look && !layered) {
    root.classList.add('first-look');
    pick('.stage').after(pick('.nav'));
  }
  let returning = false, browseFrom = null, transition = null, failure = null, firstOpenFallback = false, loadTimeout;
  let guide, routeList, cue, actions, directionWarning, routeExtras, currentLandmark;
  if (guided) {
    root.classList.add('visitor-tour');
    pick('.stage').insertAdjacentHTML('afterend',
      `<section class="orientation-help" aria-label="认路提示">
      <p class="orientation-cue" role="status"></p><div class="orientation-actions"></div></section>`);
    guide = pick('.orientation-help');
    if (visitorRoute) {
      const destination = document.createElement('p');
      destination.className = 'route-destination';
      destination.textContent = fixed ? '目的地 · ' + fixed.destination : (tour.purpose || '');
      pick('h2').after(destination);
      currentLandmark = document.createElement('p');
      currentLandmark.className = 'current-landmark';
      destination.after(currentLandmark);
    }
    if (tour.stations.some(item => item.links?.some(link => link.warning))) {
      directionWarning = document.createElement('p');
      directionWarning.className = 'direction-warning';
      directionWarning.setAttribute('role','status');
      directionWarning.hidden = true;
      pick('.stage').before(directionWarning);
    }
    if (fixed) {
      const fixedMeta = document.createElement('details');
      fixedMeta.className = 'fixed-meta';
      const summary = document.createElement('summary');
      summary.textContent = '适用范围与来源';
      const metadata = document.createElement('p');
      metadata.textContent = [fixed.audience, fixed.coverage, fixed.public_photos ? '公开资料核对，未现场踏勘' : '',
        '核对日期 ' + (fixed.checked_on || '待核对'), fixed.verify_note].filter(Boolean).join(' · ');
      fixedMeta.append(summary, metadata);
      if (tour.grade?.reasons?.length) {
        const notes = document.createElement('p');
        notes.textContent = tour.grade.reasons.join(' ');
        fixedMeta.append(notes);
      }
      if (fixed.verify_url) {
        const a = document.createElement('a');
        a.href = fixed.verify_url;
        a.textContent = '查看官方核实入口（需联网）';
        fixedMeta.append(a);
      }
      fixedAction = document.createElement('p');
      fixedAction.className = 'fixed-action';
      pick('.stage').before(fixedAction);
      fixedPhotoCredit = document.createElement('p');
      fixedPhotoCredit.className = 'fixed-photo-credit';
      guide.before(fixedPhotoCredit);
      shell.append(fixedMeta);
    }
    routeList = document.createElement('ol');
    routeList.className = 'orientation-route';
    routeList.setAttribute('aria-label','站点与地标');
    cue = pick('.orientation-cue');
    actions = pick('.orientation-actions');
    for (const item of tour.stations) {
      const stop = document.createElement('li');
      stop.textContent = item.name;
      routeList.append(stop);
    }
    const brief = document.createElement('details');
    brief.className = 'route-brief';
    const summary = document.createElement('summary');
    summary.textContent = '路线详情';
    routeExtras = document.createElement('div');
    routeExtras.className = 'route-extra-actions';
    brief.append(summary, routeExtras);
    const path = fixed ? tour.stations.map((item, i) => i) : [];
    const headings = tour.entry ? ['站点顺序', '反向浏览'] : ['去程', '回程'];
    const reverse = !forwardOnly && (!fixed || tour.grade?.reverse);
    for (const [index, order] of (reverse ? [path, [...path].reverse()] : [path]).entries()) {
      if (order.length < 2) continue;
      const heading = document.createElement('h3'), list = document.createElement('ol');
      heading.textContent = headings[index];
      order.slice(0, -1).forEach((from, index) => {
        const to = order[index+1], item = tour.stations[from];
        const link = item.links?.find(edge => edge.to === to && !edge.cut &&
          (edge.yaw !== undefined || edge.photo !== undefined));
        const row = document.createElement('li'), title = document.createElement('strong');
        title.textContent = item.name + ' → ' + tour.stations[to].name;
        row.append(title);
        const text = document.createElement('p');
        text.textContent = link ? (link.cue || '在实景里找通道箭头。') : '此段尚不能沿实景箭头走。';
        row.append(text);
        list.append(row);
      });
      if (list.childElementCount) brief.append(heading, list);
    }
    if (visitorRoute && fixed) guide.after(brief);
    else pick('.bar').after(routeList);
  } else root.classList.remove('visitor-tour');
  if (layered) {
    root.classList.add('venue-tour');
    shell.hidden = true;
    shell.insertAdjacentHTML('beforebegin', '<section class="venue-overview" aria-label="场馆总览"></section>');
    pick('.bar').insertAdjacentHTML('beforebegin', '<div class="venue-position"><button type="button" data-overview-back>← 返回总览</button><span data-level role="status"></span></div><nav class="venue-switch" aria-label="换楼层看景"></nav>');
    pick('.stage').insertAdjacentHTML('afterend', '<section class="orientation-help" aria-label="实景通道"><p class="orientation-cue" role="status"></p><div class="orientation-actions"></div></section>');
    pick('.venue-position').append(pick('.viewer-controls'));
    pick('.bar').hidden = true;
    guide = pick('.orientation-help');
    cue = pick('.orientation-cue');
    actions = pick('.orientation-actions');
  }
  const levelOf = (i) => venue.levels.find(l => l.places.some(p => p.station === i));
  const floorSwitches = new Map();
  if (layered) {
    const switcher = pick('.venue-switch');
    const label = document.createElement('span');
    const sharesFloor = level => level.floor && venue.levels.some(other => other !== level && other.floor === level.floor);
    const switchLabel = venue.levels.some(sharesFloor) ? '换楼层或展区看景' : '换楼层看景';
    label.textContent = switchLabel;
    switcher.setAttribute('aria-label', switchLabel);
    switcher.append(label);
    for (const level of venue.levels) {
      const stations = level.places.filter(place => place.station !== undefined).map(place => place.station);
      if (!stations.length) continue;
      const target = venue.recommended.find(i => stations.includes(i)) ?? stations[0];
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = sharesFloor(level) ? level.name : level.floor || level.name;
      button.setAttribute('aria-label', '切换到 ' + level.name);
      button.title = '打开「' + tour.stations[target].name + '」';
      button.onclick = () => goto(target, false, true);
      switcher.append(button);
      floorSwitches.set(level.id, button);
    }
    switcher.hidden = floorSwitches.size < 2;
  }
  function guideButton(text, action, primary = false) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = text;
    if (primary) button.className = 'primary';
    button.onclick = action;
    actions.append(button);
    return button;
  }
  function drawGuide(item, links) {
    if (directionWarning) {
      const warnings = links.filter(link => !link.cut && link.warning)
        .map(link => '去「' + tour.stations[link.to].name + '」 · ' + link.warning);
      directionWarning.hidden = !warnings.length || Boolean(transition || originals);
      directionWarning.textContent = warnings.join('。');
    }
    if (!guide) return;
    [...(routeList?.children || [])].forEach((stop, index) => {
      if (index === station) stop.setAttribute('aria-current','step');
      else stop.removeAttribute('aria-current');
    });
    actions.replaceChildren();
    routeExtras?.replaceChildren();
    if (fixedAction && !transition) {
      const last = tour.stations.length - 1;
      const to = station + (returning || station === last ? -1 : 1);
      fixedAction.textContent = blocked() ? (fixed.public_photos && fixed.access === 'unknown'
        ? '先用首张照片核对起点标牌。' : '')
        : fixed.public_photos && station === last ? '到这里停：' + fixed.destination
        : !station && !returning && fixed.first_action
        ? fixed.first_action : mayWalk(to) ? item.walk[to].action : '';
      fixedAction.hidden = !fixedAction.textContent || browseFrom !== null;
    }
    if (layered) {
      const current = levelOf(transition?.from ?? station);
      if (!transition) pick('[data-level]').textContent = (current.floor ? current.floor + ' · ' : '') + item.name;
      for (const [id, button] of floorSwitches) {
        button.disabled = Boolean(transition) || id === current.id;
        if (id === current.id) button.setAttribute('aria-current', 'location');
        else button.removeAttribute('aria-current');
      }
    }
    if (transition) {
      cue.textContent = '正在打开「' + item.name + '」…';
      return;
    }
    if (failure) {
      cue.textContent = '未能打开「' + tour.stations[failure.to].name + '」，仍在「' + item.name + '」。';
      guideButton(failure.fromArrow ? '重试换站' : '重试打开', () => goto(failure.to, failure.fromArrow, failure.direct), true);
      return;
    }
    if (!editor && item.panorama && !originals && !blocked() && !firstOpenFallback) {
      if (fixedAction) fixedAction.hidden = true;
      const paths = links.filter(link => !link.cut && link.photo === undefined);
      const preferred = fixed ? paths.find(link => link.to === station + (returning ? -1 : 1))
        : paths.find(link => link.to !== arrivedFrom);
      cue.textContent = fixed ? (browseFrom !== null ? '已切换看景 · 可沿已确认通道继续' :
        station === destinationIndex ? '已到达目的地 · 可原路返回' : (returning ? '返程' : '前往目的地') + ' · ' + (station + 1) + ' / ' + (destinationIndex + 1) + ' 处')
        : browseFrom !== null ? '已切换看景 · 可沿已确认通道继续' : '';
      if (paths.length) {
        const directions = document.createElement('select');
        directions.setAttribute('aria-label', '选择通道');
        for (const link of [...paths].sort((a,b) => Number(b === preferred) - Number(a === preferred))) {
          const option = new Option(tour.stations[link.to].name + (link.to === arrivedFrom ? ' · 刚才来路' : ''), link.to);
          option.title = [link.cue, link.warning].filter(Boolean).join(' · ');
          directions.append(option);
        }
        actions.append(directions);
        const details = document.createElement('details');details.className='guidance-detail';
        const summary = document.createElement('summary');summary.textContent='路线提示';
        const description = document.createElement('p');
        const revealPassage = (explicit = false) => {
          if ((!explicit && !details.open) || !viewer?.isLoaded() || transition) return;
          const edge = paths.find(p => p.to === Number(directions.value));
          const arrow = scene.querySelector('[data-destination="' + edge.to + '"] span');
          const rect = arrow?.getBoundingClientRect();
          if (rect && rect.width && rect.left >= 0 && rect.right <= innerWidth &&
              rect.top >= 0 && rect.bottom <= innerHeight &&
              arrow.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2))) return;
          const vfov = 2 * Math.atan(Math.tan(viewer.getHfov() * Math.PI / 360) * scene.clientHeight / scene.clientWidth) * 180 / Math.PI;
          viewer.lookAt(edge.pitch - vfov * .15, edge.yaw, viewer.getHfov(), 300);
        };
        const explain = () => {const edge=paths.find(p=>p.to===Number(directions.value));description.textContent=[edge?.cue,edge?.warning].filter(Boolean).join(' ');revealPassage();};
        const look = guideButton('看向通道', () => revealPassage(true));look.className='passage-look';
        look.disabled = !viewer?.isLoaded();
        const b = guideButton('前往', () => goto(Number(directions.value), true), true);
        const updateLabel = () => { b.textContent = Number(directions.value) === arrivedFrom || fixed && Number(directions.value) < station ? '返回' : '前往'; };
        directions.onchange=()=>{explain();updateLabel();};details.ontoggle=()=>revealPassage();explain();updateLabel();details.append(summary,description);
        b.disabled = !viewer?.isLoaded();
        actions.append(details);
      }
      if (!paths.length) cue.textContent = (browseFrom !== null ? '仅切换看景。' : '') + '当前地点没有已确认通道，可从地图选择其他地点。';
      return;
    }
    if (fixed && !editor) {
      if (blocked()) {
        cue.textContent = tour.grade?.issue || '这条路线不在适用日期内。';
        for (const to of [station - 1, station + 1]) {
          if (!mayBrowse(to)) continue;
          routeExtras.append(guideButton('仅看照片 · ' + tour.stations[to].name, () => goto(to, false, true)));
        }
        return;
      }
      if (photoFailure) {
        cue.textContent = '当前照片未能打开。';
        guideButton('重试照片', () => { photoFailure = false; render(); }, true);
        return;
      }
      if (firstOpenFallback) guideButton('重试打开全景', () => pick('[data-original]').click());
      const last = tour.stations.length - 1;
      const back = returning || station === last;
      const to = station + (back ? -1 : 1);
      const step = item.walk?.[to];
      const say = [];
      const browsing = browseFrom !== null;
      if (browsing) say.push('仅切换看景，未沿路线走到此处。沿画面里的已确认箭头继续。');
      if (!tour.grade?.route_ready) say.push('草稿可试到「' + tour.stations[tour.grade?.through ?? 0].name + '」。');
      else if (!tour.grade.reverse) say.push('单向路线指引');
      if (!browsing && ((station === last && !fixed.public_photos) || (returning && station === 0)))
        say.push('到这里停：' + (item.landmark || item.name));
      if (mayWalk(to)) {
        say.push('看到：' + step.see, '走法：' + step.action, '走对后：' + step.arrive);
        const link = links.find(edge => edge.to === to && edge.yaw !== undefined);
        if (link && !originals) {
          const button = guideButton('朝向 ' + tour.stations[link.to].name, () => {
            if (!viewer?.isLoaded()) return;
            viewer.lookAt(link.pitch, link.yaw, viewer.getHfov(), false);
            scene.querySelector('[data-destination="' + link.to + '"]')?.focus({preventScroll:true});
          }, true);
          button.disabled = !viewer?.isLoaded();
        } else if (!item.panorama) guideButton('下一站 · ' + tour.stations[to].name, () => goto(to, true), true);
      } else if (step && !step.confirmed && !back) say.push(step.pending);
      cue.textContent = say.join('\n');
      return;
    }
    if (firstOpenFallback) {
      cue.textContent = '全景没打开，先显示当前站的原图。';
      guideButton('重试打开全景', () => pick('[data-original]').click(), true);
      return;
    }
    if (layered) {
      if (!item.panorama || originals) {
        cue.textContent = originals && item.panorama ? '正在查看拍摄原图。' : '单视角照片';
        return;
      }
      if (!viewer?.isLoaded()) {
        cue.textContent = '正在打开全景…';
        return;
      }
      const paths = links.filter(link => !link.cut && link.photo === undefined);
      cue.textContent = paths.length ? '沿通道参观时，点画面里的箭头。' : '可环顾实景，也可选楼层或回总览继续参观。';
      for (const link of paths) {
        const name = tour.stations[link.to].name;
        const target = levelOf(link.to);
        const button = guideButton('朝向 ' + name, () => {
          if (!viewer?.isLoaded()) return;
          viewer.lookAt(link.pitch + 5, link.yaw, viewer.getHfov(), false);
          cue.textContent = link.cue || '点画面里的箭头前往「' + name + '」。';
          pick('.venue-position').scrollIntoView({block:'start'});
          scene.querySelector('[data-destination="' + link.to + '"]')?.focus({preventScroll:true});
        });
        button.setAttribute('aria-label', '朝向 ' + name);
        if (target !== levelOf(station)) button.title = target.name;
      }
      return;
    }
    const home = tour.entry ?? 0;
    const last = destinationIndex;
    const back = !forwardOnly && (returning || station === last);
    const next = station + (back ? -1 : 1);
    const visible = links.filter(link => !link.cut && link.photo === undefined);
    const ahead = visible.find(link => link.to === next);
    const browsing = visitorRoute && browseFrom !== null;
    const atStop = visitorRoute && !browsing && !returning && !ahead && station !== last;
    const photoNext = browsing || station === home || forwardOnly ? station + 1 : tour.destination ?? station + 1;
    const arrival = !browsing && station === last ? '终点地标 · ' + (item.landmark || item.name) + '。' :
      station === home && returning ? '已回到「' + item.name + '」。' : '';
    const landmark = tour.stations[next]?.landmark || tour.stations[next]?.name;
    const hint = ahead ? ahead.cue || '沿画面箭头去「' + landmark + '」'
      : editor ? item.pending?.[next] || '' : '';
    if (visitorRoute && originals) {
      cue.textContent = '正在看原图。';
      return;
    }
    const routeHint = hint && !/[。！？]$/.test(hint) ? hint + '。' : hint;
    cue.textContent = browsing ? '正在浏览照片。可返回「' + tour.stations[browseFrom].name + '」。' : atStop
      ? station === home ? '这里可以环顾和看沿途照片，通路待核对。' :
        '这段实景试走到这里。去「' + (tour.stations[photoNext]?.name || tour.stations[last].name) + '」的通路待核对。'
      : arrival + routeHint + (ahead ? '点画面里的箭头继续。' : '');
    if (browsing) guideButton('返回「' + tour.stations[browseFrom].name + '」', () => goto(browseFrom, false, true), true);
    const offerPhoto = (atStop || browsing && station !== last && photoNext !== browseFrom) && tour.stations[photoNext];
    if (offerPhoto) {
      guideButton('仅看照片 · ' + tour.stations[photoNext].name, () => goto(photoNext, false, true), atStop && !visible.length);
    }
    for (const link of visible) {
      if (originals) break;
      const primary = !browsing && (link === ahead || atStop && link.to < station);
      const button = guideButton('朝向 ' + tour.stations[link.to].name, () => {
        if (!viewer?.isLoaded()) return;
        viewer.lookAt(link.pitch, link.yaw, viewer.getHfov(), false);
        cue.textContent = '已对准通道，点画面里的箭头沿路继续。';
        scene.querySelector('[data-destination="' + link.to + '"]')?.focus({preventScroll:true});
      }, primary);
      button.disabled = !viewer?.isLoaded();
      if (visitorRoute && !primary) routeExtras.append(button);
      else if (primary) actions.prepend(button);
    }
    if (visitorRoute) {
      for (const to of forwardOnly ? [station + 1] : [station - 1, station + 1]) {
        if (!tour.stations[to] || visible.some(link => link.to === to) ||
            (offerPhoto && to === photoNext) || to === browseFrom) continue;
        const button = guideButton('仅看照片 · ' + tour.stations[to].name, () => goto(to, false, true));
        routeExtras.append(button);
      }
    }
  }
  function clearFrame(fade = false) {
    const frame = lastFrame, prior = lastViewer, index = lastStation;
    lastFrame = lastViewer = lastStation = null;
    if (!frame) return;
    // Keep only the immediately previous renderer, alongside one forward preload.
    // Re-decoding a cached image still repeats the costly GPU upload on return.
    const retained = !stopped && !editor && prior?.isLoaded() && !lostSurfaces.has(frame) && index !== station
      ? {index, surface:frame, viewer:prior} : null;
    if (retained) { discardBack(); backScene = retained; }
    const remove = () => {
      if (retained && backScene === retained) {
        frame.classList.remove('scene-handoff','is-releasing');
        frame.classList.add('scene-preload');
      } else { prior?.destroy(); frame.remove(); }
    };
    if (fade && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      frame.classList.add('is-releasing');
      const timer = setTimeout(remove, 160);
      if (retained) retained.timer = timer;
    } else remove();
  }
  function holdFrame() {
    if (!viewer?.isLoaded()) return;
    clearFrame();
    viewer.stopMovement();
    lastViewer = viewer;
    lastStation = viewerStation;
    lastFrame = viewer.getRenderer().getCanvas().closest('.scene-surface');
    if (!lastFrame) { lastViewer = null; return; }
    lastFrame.classList.add('scene-handoff');
    lastFrame.inert = true;
    lastFrame.setAttribute('aria-hidden', 'true');
    scene.after(lastFrame);
    viewer = null;
  }
  function maxTexture() {
    if (textureLimit === undefined) {
      const gl = document.createElement('canvas').getContext('webgl', {alpha:false, depth:false});
      textureLimit = gl?.getParameter(gl.MAX_TEXTURE_SIZE) || 0;
      gl?.getExtension('WEBGL_lose_context')?.loseContext();
    }
    return textureLimit;
  }
  function panoramaSurface() {
    const surface = document.createElement('div');
    surface.className = 'scene-surface';
    surface.addEventListener('webglcontextlost', () => {
      lostSurfaces.add(surface);
      textureFailures.get(surface)?.();
    }, true);
    return surface;
  }
  async function smallerSphere(url, height) {
    const img = new Image();
    img.src = url;
    await img.decode();
    const canvas = document.createElement('canvas');
    canvas.width = height * 2;
    canvas.height = height;
    canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
    return canvas;
  }
  function routeButton(link) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'route-arrow';
    if (layered || visitorRoute) button.dataset.destination = link.to;
    const label = '前往 ' + tour.stations[link.to].name;
    button.setAttribute('aria-label', label);
    button.innerHTML = '<span aria-hidden="true">↑</span><b></b>';
    button.querySelector('b').textContent = label;
    button.onclick = event => {
      event.stopPropagation();
      if (placement === null && (!event.detail || !movedSincePress(event))) goto(link.to, true);
    };
    return button;
  }
  function drawLinks() {
    const item = tour.stations[station];
    const links = sceneLinks(item);
    drawNavigation(item, links);
    scene.querySelectorAll('.photo-route-arrow').forEach(el => el.remove());
    if (viewer?.isLoaded()) {
      const targets = venue || tour.route_connections ? tour.stations.map((_, i) => i) : [station - 1, station + 1];
      targets.forEach(to => viewer.removeHotSpot('route-' + to));
      for (const link of links.filter(link => !link.cut && link.photo === undefined)) {
        viewer.addHotSpot({id:'route-' + link.to, yaw:link.yaw, pitch:link.pitch, type:'info',
          cssClass:'tour-arrow-host', createTooltipFunc:host => host.append(routeButton(link))});
      }
      return;
    }
    const surface = scene.querySelector('.photo-surface');
    if (!surface) return;
    for (const link of links.filter(link => !link.cut && link.photo === photo)) {
      const host = document.createElement('div');
      host.className = 'photo-route-arrow';
      host.style.left = (link.x * 100) + '%';
      host.style.top = (link.y * 100) + '%';
      host.append(routeButton(link));
      surface.append(host);
    }
  }
  let placementHint;
  if (editor) {
    placementHint = document.createElement('p');
    placementHint.className = 'placement-hint';
    placementHint.setAttribute('role','status');
    placementHint.hidden = true;
    pick('.stage').before(placementHint);
  }
  function setPlacement(to) {
    placement = to;
    scene.classList.toggle('placing-arrow', to !== null);
    if (placementHint) {
      placementHint.hidden = to === null;
      placementHint.textContent = to === null ? '' :
        '从「' + tour.stations[station].name + '」到「' + tour.stations[to].name + '」。点实拍里的通道。';
    }
  }
  let press;
  const movedSincePress = event => press && Math.hypot(event.clientX - press[0], event.clientY - press[1]) > 6;
  scene.addEventListener('pointerdown', event => { press = [event.clientX, event.clientY]; }, true);
  scene.addEventListener('click', event => {
    if (placement === null || event.target.closest('.pnlm-controls, .pnlm-about-msg')) return;
    if (!press || movedSincePress(event)) return;
    let point;
    if (viewer?.isLoaded()) {
      const [pitch, yaw] = viewer.mouseEventToCoords(event);
      const {haov, vaov, vOffset} = tour.stations[station].panorama;
      if (Math.abs(yaw) <= haov / 2 && Math.abs(pitch - vOffset) <= vaov / 2) point = {pitch, yaw};
    } else {
      const rect = scene.querySelector('.scene-photo')?.getBoundingClientRect();
      if (rect?.width && rect.height) {
        const x = (event.clientX - rect.left) / rect.width, y = (event.clientY - rect.top) / rect.height;
        if (x >= 0 && x <= 1 && y >= 0 && y <= 1) point = {photo, x, y};
      }
    }
    if (!point) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    editor.onPlace(station, {to:placement, ...point});
    setPlacement(null);
    drawLinks();
  }, true);
  function drawNavigation(item, links) {
    const pano = item.panorama, showPano = pano && !originals;
    const hasPictureArrow = links.some(link => !link.cut &&
      (showPano ? link.photo === undefined : link.photo === photo));
    const hint = pick('[data-mode]');
    const dragHint = pano?.vaov >= 40 ? '左右拖动转身，上下拖动抬头低头。' : '左右拖动转身，上下范围有限。';
    const bothDirections = !venue && showPano && links.filter(link => !link.cut && link.photo === undefined).length === 2;
    const routeHint = bothDirections ? '两个去向分别标在实景通道上，转身可找另一处箭头。'
      : hasPictureArrow ? '点门口或通道上的箭头继续。' : '';
    hint.textContent = showPano ? dragHint + routeHint
      : hasPictureArrow ? '点照片里的箭头走到对应站点。' : '';
    hint.hidden = Boolean(layered || visitorRoute || tour.first_look) || !hint.textContent;
    const prev = pick('[data-prev]'), next = pick('[data-next]'), nav = pick('.nav');
    const browse = !editor && (mayBrowse(station - 1) || mayBrowse(station + 1));
    nav.hidden = Boolean(visitorRoute) || !browse && (Boolean(layered || fixed) || tour.stations.length < 2);
    nav.setAttribute('aria-label', browse || tour.first_look ? '仅切换实景' : '导览路线');
    for (const [button, offset, step] of [[prev, -1, '上一站'], [next, 1, '下一站']]) {
      const to = station + offset, target = tour.stations[to];
      button.disabled = !target || (browse && (Boolean(transition) || !mayBrowse(to)));
      const confirmed = !browse && links.some(link => link.to === to && !link.cut);
      const label = tour.first_look ? (offset < 0 ? '上一张照片' : '下一张照片')
        : !target ? step : (confirmed ? step + ' · ' : '浏览 ') + target.name;
      button.textContent = offset < 0 ? '← ' + label : label + ' →';
    }
    const missing = (venue ? (venue.connections || []).filter(c => c.from === station && c.pending).map(c => c.to)
      : forwardOnly ? [station+1] : [station-1,station+1]).filter(to => tour.stations[to] &&
      !links.some(link => link.to === to && !link.cut));
    const coverage = item.reason || (pano && pano.haov < 360 ? `局部实拍，左右视野约 ${Math.round(pano.haov)}°。` : '');
    const lowDetail = visitorRoute && showPano && detailLimited.includes(station);
    quality.textContent = lowDetail ? '标清路线预览 · 本站素材细节有限，放大后会模糊。' : [coverage, editor && !tour.first_look && missing.length ?
      '实景通道待确认：' + missing.map(to => tour.stations[to].name).join('、') + '。' : ''].filter(Boolean).join(' ');
    quality.hidden = !lowDetail && (Boolean(fixed || visitorRoute) || !quality.textContent);
    if (fixed && editor) return;
    drawGuide(item, links);
  }
  function discardWarm() {
    if(!warmScene)return;
    const warm=warmScene;warmScene=null;disposeWarm(warm);
  }
  function disposeWarm(warm) {
    warm.cancelled = true;
    warm.viewer?.destroy();
    warm.surface.remove();
  }
  function discardBack() {
    if (!backScene) return;
    const back = backScene; backScene = null;
    clearTimeout(back.timer); back.viewer.destroy(); back.surface.remove();
  }
  function panoramaOptions(item,direction=0,restored=null) {
    const pano=item.panorama, {haov,vaov,vOffset}=pano;
    const limitedVertical = vaov >= 40 && vaov < 90;
    const radians = Math.PI / 180;
    const aspect = scene.clientWidth / Math.max(1, scene.clientHeight);
    // Leave room to look up and down while keeping the partial panorama in view.
    const verticalFov = Math.min(vaov * (limitedVertical ? .68 : .85), 170);
    const heightLimit = 2 * Math.atan(Math.tan(verticalFov * radians / 2) * aspect) / radians;
    const defaultHfov = Math.max(12, Math.min(layered && aspect < 1 ? 58 : 70, haov * .68, heightLimit));
    const opening = restored || (venue || tour.first_look || editor || (direction >= 0 && guided) ? item.opening : null);
    const hfov = opening?.hfov ?? defaultHfov;
    const vfov = 2 * Math.atan(Math.tan(hfov * radians / 2) / aspect) / radians;
    const links = (item.links || []).filter(link => !link.cut && link.photo === undefined);
    const onward = !venue && links.find(link => link.to === tour.stations.indexOf(item) + (direction || 1));
    const approach = onward || links[0];
    const walkthrough = !editor && tour.grade?.route_ready;
    const lookIntoRoom = !venue && walkthrough && haov === 360 && direction && !onward && approach;
    const yaw = opening?.yaw ?? (lookIntoRoom ? (approach.yaw + 360) % 360 - 180
      : approach ? approach.yaw + (walkthrough && (!guided || direction) ? hfov * .18 * (direction < 0 ? -1 : 1) : 0) : 0);
    return {
      type:'equirectangular', haov, vaov, vOffset, ignoreGPanoXMP:true,
      pitch:opening?.pitch ?? (approach && !lookIntoRoom ? Math.min(vOffset + vaov * .28, approach.pitch + Math.min(9, vfov * .24)) : vOffset),
      yaw, hfov,
      minYaw:-haov/2, maxYaw:haov/2, minPitch:vOffset-vaov/2, maxPitch:vOffset+vaov/2,
      minHfov:Math.min(22, defaultHfov*.65), maxHfov:Math.min(90, haov*.95), avoidShowingBackground:true,
      autoLoad:true, showFullscreenCtrl:true, showZoomCtrl:true, keyboardZoom:true, mouseZoom:false,
      compass:false, friction:.24, backgroundColor:[.04,.10,.08],
      strings:{loadButtonLabel:'打开全景',loadingLabel:'正在打开全景…',bylineLabel:'',zoomInLabel:'放大',zoomOutLabel:'缩小',fullscreenLabel:'全屏'}
    };
  }
  function render(direction = 0, restored = null, retryReduced = false) {
    stopped = false;
    const ticket = ++load;
    clearTimeout(loadTimeout);
    clearTimeout(prefetchTimer);
    const item = tour.stations[station], pano = item.panorama, showPano = pano && !originals;
    tourNavigator?.update(transition?.from ?? station, Boolean(transition));
    if (viewer?.isLoaded() && showPano) holdFrame();
    else if (!(showPano && (failure || (retryReduced && transition)))) clearFrame();
    if (viewer) { viewer.destroy(); viewer = null; }
    if (backScene?.index !== station) discardBack();
    scene.replaceChildren();
    const limitedVertical = showPano && pano.vaov >= 40 && pano.vaov < 90;
    scene.parentElement.classList.toggle('limited-vertical', Boolean(limitedVertical));
    pick('h2').textContent = item.name;
    if (currentLandmark) currentLandmark.textContent = item.landmark && item.landmark !== item.name
      ? '认准 · ' + item.landmark : '';
    pick('[data-count]').textContent = layered ? '实景' :
      `${fixed?.public_photos ? '公开照片 · ' : '站点 '}${station + 1} / ${tour.stations.length}`;
    if (fixedPhotoCredit) {
      fixedPhotoCredit.replaceChildren();
      const credit = item.photo_sources?.[photo];
      fixedPhotoCredit.hidden = !credit || showPano;
      if (credit && !showPano) {
        const source = document.createElement('a'), license = document.createElement('a');
        source.href = credit.url;
        source.textContent = credit.author;
        license.href = credit.license_url;
        license.textContent = credit.license;
        fixedPhotoCredit.append(credit.caption + ' · ', source, ' · ', license);
      }
    }
    drawNavigation(item, sceneLinks(item));
    const original = pick('[data-original]');
    original.hidden = !pano;
    original.textContent = originals ? '返回全景' : (guided || layered) && !editor ? '查看原图' : '看原图';
    pick('[data-reset]').hidden = !showPano;
    thumbs.replaceChildren();
    function picture() {
      scene.replaceChildren();
      const surface = document.createElement('div');
      surface.className = 'photo-surface';
      const img = new Image();
      if (fixed && !editor) img.onerror = () => {
        if (ticket !== load) return;
        photoFailure = true;
        drawGuide(item, item.links || []);
      };
      img.src = item.originals[photo].panorama_source && pano ? pano.image : item.originals[photo].image;
      img.className = 'scene-photo'; img.alt = item.originals[photo].name;
      surface.append(img);
      scene.append(surface);
      if (item.originals.length > 1) {
        item.originals.forEach((asset, i) => {
          const button = document.createElement('button'), preview = new Image();
          button.type = 'button'; button.title = asset.name; button.setAttribute('aria-label', '看原图 ' + asset.name);
          button.setAttribute('aria-pressed', String(i === photo));
          preview.src = asset.image; preview.alt = ''; button.append(preview);
          button.onclick = () => { photo = i; photoFailure = false; setPlacement(null); render(); editor?.onStationChange(); }; thumbs.append(button);
        });
      }
      drawLinks();
    }
    if (!showPano) { picture(); return; }
    const reduced = retryReduced || exceedsTextureLimit(pano);
    let claimedWarm;
    function loadFailed() {
      if (ticket !== load) return;
      if (claimedWarm) { disposeWarm(claimedWarm); claimedWarm = null; }
      if (!reduced && !compactViewers.has(viewer) && reducedSphereHeight(pano)) {
        const pose = viewer?.isLoaded()
          ? {pitch:viewer.getPitch(), yaw:viewer.getYaw(), hfov:viewer.getHfov()} : restored;
        viewer?.destroy();
        viewer = null;
        discardWarm();
        discardBack();
        decoded.delete(pano.image);
        render(direction, pose, true);
        return;
      }
      if (transition) {
        const prior = transition;
        failure = {to:station, fromArrow:prior.fromArrow, direct:prior.direct};
        transition = null;
        station = prior.from;
        originals = prior.originals;
        photo = prior.photo;
        render(0, prior.opening);
      } else {
        originals = true;
        firstOpenFallback = Boolean(guided || layered);
        render();
        if (!guided && !layered) {
          quality.hidden = false;
          quality.textContent = '全景未能打开，已切回原图。可以点「返回全景」重试。';
        }
      }
      editor?.onStationChange();
    }
    // A decode that emits neither load nor error can also happen on first open.
    loadTimeout = setTimeout(loadFailed, 12000);
    function mount(texture, warmed = null) {
      if (ticket !== load) {if (warmed && !warmed.cancelled) disposeWarm(warmed);return;}
      claimedWarm = null;
      try {
        let surface;
        if(warmed){
          surface = warmed.surface;
          clearTimeout(warmed.timer);
          surface.classList.remove('scene-preload','scene-handoff','is-releasing');
          surface.inert = false;
          surface.removeAttribute('aria-hidden');scene.append(surface);viewer=warmed.viewer;
          viewer.off('load');viewer.off('error');viewer.resize();
          const options=panoramaOptions(item,direction,restored);viewer.lookAt(options.pitch,options.yaw,options.hfov,false);
        } else {
          surface=panoramaSurface();scene.append(surface);
          viewer=pannellum.viewer(surface,{...panoramaOptions(item,direction,restored),panorama:texture});
          if (reduced) compactViewers.add(viewer);
        }
        textureFailures.set(surface,loadFailed);
        viewerStation = station;
      } catch {loadFailed();return;}
      const loaded = () => {
        if (ticket !== load) return;
        clearTimeout(loadTimeout);
        const movedFrom = transition?.from ?? null;
        if (transition) {
          finishMove(transition.from, transition.direct);
          transition = null;
        }
        drawLinks();
        tourNavigator?.update(station, false);
        if (layered) drawGuide(item, item.links || []);
        if (venue) editor?.onStationChange();
        transitionOrigin = movedFrom;
        clearFrame(true);
        prefetchNext(item);
      };
      viewer.on('load', loaded);
      viewer.on('error', loadFailed);
      if(warmed && viewer.isLoaded())loaded();
    }
    const mountCold = () => {
      if (ticket === load) prepareSphere(pano,reduced).then(mount,loadFailed);
    };
    if (backScene?.index === station) {
      const back = backScene; backScene = null; discardWarm();
      if (lostSurfaces.has(back.surface)) { clearTimeout(back.timer); disposeWarm(back); mountCold(); }
      else mount(null,back);
    } else if(warmScene?.index===station){
      const warm=warmScene;warmScene=null;claimedWarm=warm;
      const useCold = () => {
        if (!warm.cancelled) disposeWarm(warm);
        claimedWarm = null;
        mountCold();
      };
      warm.ready.then(v=>v && !lostSurfaces.has(warm.surface)?mount(null,warm):useCold(),useCold);
    } else {discardWarm();mountCold();}
  }

  function largeSphere(pano) {
    return pano.width > 4096 && pano.haov === 360 && pano.vaov === 180;
  }
  function exceedsTextureLimit(pano) {
    if (!largeSphere(pano)) return false;
    const limit = maxTexture();
    // Pannellum can split a wide equirectangular image into two textures.
    return limit > 0 && Math.max(pano.width / 2, pano.height) > limit;
  }
  function reducedSphereHeight(pano) {
    if (!largeSphere(pano)) return 0;
    const limit = maxTexture();
    return Math.min(1920, limit ? Math.floor(limit / 2) : 1920);
  }
  function prepareSphere(pano, reduced) {
    if (!reduced) return decodeSphere(pano.image);
    const height = reducedSphereHeight(pano), compact = pano.compact;
    if (compact?.height <= height && compact.width <= height * 2) return decodeSphere(compact.image);
    const key = pano.image;
    if (prepared.has(key)) return prepared.get(key);
    const promise = smallerSphere(compact?.height > height ? compact.image : pano.image,height);
    prepared.set(key,promise);
    promise.catch(()=>prepared.delete(key));
    while(prepared.size > (pano.width>10000?2:4))prepared.delete(prepared.keys().next().value);
    return promise;
  }

  function finishMove(from, direct) {
    arrivedFrom = direct ? null : from;
    if (visitorRoute && direct) {
      browseFrom = station === browseFrom ? null : browseFrom ?? from;
    } else {
      returning = station < from;
      browseFrom = null;
    }
  }
  const goto = async (n, fromArrow = false, direct = false) => {
    if (fixed && !editor && !mayWalk(n) && !mayBrowse(n) && !(direct && mayInspect(n))) return;
    if (transition || n === station || n < 0 || n >= tour.stations.length) return;
    const direction = Math.sign(n - station);
    failure = null;
    firstOpenFallback = false;
    if (fixed && !editor && !tour.stations[n].panorama) {
      const ticket = load, img = new Image();
      transition = {from:station};
      img.src = tour.stations[n].originals[entryPhoto(n)].image;
      drawGuide(tour.stations[n], []);
      try { await img.decode(); } catch {
        if (ticket !== load) return;
        transition = null;
        failure = {to:n, fromArrow, direct};
        drawGuide(tour.stations[station], []);
        return;
      }
      if (ticket !== load) return;
      transition = null;
    }
    photoFailure = false;
    const from = station;
    if ((guided || layered) && tour.stations[n].panorama) transition = {from, originals, photo, fromArrow, direct,
      opening:viewer?.isLoaded() ? {yaw:viewer.getYaw(),pitch:viewer.getPitch(),hfov:viewer.getHfov()} : null};
    station = n;
    if (guided && !transition) finishMove(from, direct);
    photo = entryPhoto(n);
    originals = false;
    setPlacement(null);
    render(visitorRoute && !fixed && direct ? 0 : direction);
    if (fromArrow && fixed && !editor) pick('.bar').scrollIntoView({block:'start'});
    if (fromArrow || direct) scene.focus({preventScroll:true});
    editor?.onStationChange();
  };
  if (!editor && tour.stations.length > 1) {
    tourNavigator = mountTourNavigator(pick('.stage'), tour, (to) => goto(to, false, true),
      (from, link) => !link.cut && (link.yaw !== undefined || link.photo !== undefined) &&
        (!fixed || (!blocked() && tour.stations[from].walk?.[link.to]?.confirmed &&
          (link.to > from || tour.grade?.reverse))),
      to => !fixed || to === station || mayInspect(to) || mayWalk(to) || mayBrowse(to));
  }
  pick('[data-prev]').onclick = () => goto(station-1);
  pick('[data-next]').onclick = () => goto(station+1);
  function showSource(item) {
    viewer?.stopMovement();
    if (!originalDialog) {
      originalDialog = document.createElement('dialog');
      originalDialog.className = 'source-dialog';
      originalDialog.setAttribute('aria-label', '查看原图');
      originalDialog.innerHTML = '<div class="source-dialog-bar"><div><strong></strong><p></p></div><a class="download">下载球面图</a><button type="button" autofocus>返回全景</button></div><img>';
      originalDialog.querySelector('button').onclick = () => originalDialog.close();
      originalDialog.addEventListener('close', () => {
        originalDialog.querySelector('img').removeAttribute('src');
        originalDialog.querySelector('a').removeAttribute('href');
      });
      root.append(originalDialog);
    }
    const pano = item.panorama, source = item.originals[0];
    originalDialog.querySelector('strong').textContent = item.name;
    originalDialog.querySelector('p').textContent = '实拍球面图 · ' + pano.width + ' × ' + pano.height;
    const image = originalDialog.querySelector('img');
    image.alt = item.name + '的完整球面图';
    image.src = pano.image;
    const link = originalDialog.querySelector('a');
    link.href = pano.image;
    const ext = {'image/jpeg':'jpg','image/webp':'webp','image/png':'png'}[pano.image.split(';',1)[0].slice(5)];
    const extension = source.name.split('.').pop().toLowerCase();
    link.download = !ext || extension === ext || (ext === 'jpg' && extension === 'jpeg')
      ? source.name : source.name.replace(/\.[^.]+$/, '') + '.' + ext;
    originalDialog.showModal();
  }
  pick('[data-original]').onclick = () => {
    if (transition) return;
    const item = tour.stations[station];
    if (!editor && !originals && item.originals.length === 1 && item.originals[0].panorama_source) {
      showSource(item);
      return;
    }
    firstOpenFallback = false;
    originals = !originals;
    photo = entryPhoto(station);
    setPlacement(null);
    render();
    editor?.onStationChange();
  };
  pick('[data-reset]').onclick = () => {
    if (transition || !viewer?.isLoaded()) return;
    const opening = panoramaOptions(tour.stations[station]);
    viewer.stopMovement();
    viewer.lookAt(opening.pitch, opening.yaw, opening.hfov, false);
  };
  function stop() {
    stopped = true;
    load++;clearTimeout(loadTimeout);clearTimeout(prefetchTimer);decoded.clear();prepared.clear();discardWarm();discardBack();clearFrame();
    if (viewer) { viewer.destroy(); viewer = null; }
  }
  let closeOverview;
  const backKey = (event) => {
    if (event.key !== 'Escape' || shell.hidden || document.fullscreenElement || originalDialog?.open) return;
    if (pick('.point-drawer:not([hidden]),.tour-minimap:not([hidden])')) return;
    event.preventDefault();
    closeOverview?.();
  };
  if (layered) {
    const overview = pick('.venue-overview');
    const openScene = (i, focus = true) => {
      overview.hidden = true;
      shell.hidden = false;
      root.classList.add('is-exploring');
      station = i;
      photo = 0;
      originals = false;
      firstOpenFallback = false;
      failure = null;
      render();
      pick('.venue-position').scrollIntoView({block:'start'});
      if (focus) pick('[data-overview-back]').focus({preventScroll:true});
    };
    const draw = mountVenueOverview(overview, tour, openScene);
    closeOverview = () => {
      const selected = transition?.from ?? station;
      transition = null;
      stop();
      scene.replaceChildren();
      thumbs.replaceChildren();
      shell.hidden = true;
      overview.hidden = false;
      root.classList.remove('is-exploring');
      draw(levelOf(selected).id, selected, true);
    };
    pick('[data-overview-back]').onclick = closeOverview;
    root.addEventListener('keydown', backKey, true);
    if (!tour.first_look && tour.stations[station].panorama) openScene(station, false);
  } else render();
  return {destroy:()=>{stop();tourNavigator?.destroy();root.removeEventListener('keydown', backKey, true);root.classList.remove('venue-tour','is-exploring');}, view:()=>viewer, station:()=>station, goTo:goto,
    setPlacement, drawLinks, links:index=>tour.stations[index].links,
    photoView:()=>viewer ? undefined : photo};
}

// A topology is laid out by confirmed connections, never by upload order or guessed walls.
function mapTopology(tour, places, isEdge) {
  const ids = new Set(places.map(p => p.station));
  // Include adjoining entrances / stair landings so a small floor is not an isolated dot.
  for (const p of places) for (const edge of tour.stations[p.station].links || [])
    if (isEdge(p.station, edge)) ids.add(edge.to);
  const distance = new Map([[tour.entry ?? 0, 0]]), queue = [tour.entry ?? 0];
  for (const i of queue) for (const e of tour.stations[i].links || [])
    if (isEdge(i,e) && !distance.has(e.to)) {distance.set(e.to,distance.get(i)+1);queue.push(e.to);}
  const ordered = [...ids].sort((a,b) => (distance.get(a) ?? Infinity) - (distance.get(b) ?? Infinity) || a-b);
  const seen = new Set(), nodes = [], roots = [];
  let leaf = 0;
  for (const id of ordered) if (!seen.has(id)) {
    const root = {station:id,depth:0,children:[]}, pending = [root];
    roots.push(root);seen.add(id);
    for (const node of pending) for (const e of tour.stations[node.station].links || []) {
      if (!ids.has(e.to) || seen.has(e.to) || !isEdge(node.station,e)) continue;
      const child = {station:e.to,depth:node.depth+1,children:[]};
      seen.add(e.to);node.children.push(child);pending.push(child);
    }
  }
  function arrange(node) {
    nodes.push(node);
    node.children.forEach(arrange);
    node.row = node.children.length ? node.children.reduce((v,c)=>v+c.row,0)/node.children.length : leaf++;
  }
  for (const root of roots) {arrange(root);leaf+=.5;}
  const depth = Math.max(1,...nodes.map(n=>n.depth));
  const layers=Array.from({length:depth+1},(_,i)=>nodes.filter(n=>n.depth===i).sort((a,b)=>a.row-b.row));
  const position=n=>{const row=layers[n.depth];return (row.indexOf(n)+.5)/row.length;};
  // Reorder each depth by all confirmed neighbours, so loops do not criss-cross
  // a tree chosen from one arbitrary route through the building.
  for(let pass=0;pass<6;pass++)for(const row of pass%2?[...layers].reverse():layers){
    const rank=new Map(row.map(n=>{
      const adjacent=nodes.filter(other=>other!==n&&(tour.stations[n.station].links||[]).some(e=>e.to===other.station&&isEdge(n.station,e)));
      return [n,adjacent.length?adjacent.reduce((v,p)=>v+position(p),0)/adjacent.length:position(n)];
    }));
    row.sort((a,b)=>rank.get(a)-rank.get(b)||a.row-b.row);
  }
  return nodes.map(n=>({station:n.station,layer:n.depth,x:.08+.84*position(n),y:.12+.68*n.depth/depth,
    portal:!places.some(p=>p.station===n.station)}));
}

function placeCompactMapLabels(map, viewport) {
  const bounds = viewport.getBoundingClientRect();
  const pins = [...map.querySelectorAll('.venue-pin:not([hidden])')];
  const markers = pins.map(pin => {
    const frame = pin.getBoundingClientRect();
    if (pin.hasAttribute('aria-current') || pin.classList.contains('map-cluster')) return frame;
    // Labels can occupy the transparent edge of a pin's touch target.
    return {left: frame.left + 5, right: frame.right - 5, top: frame.top + 5, bottom: frame.bottom - 5};
  });
  const labels = [];
  const overlaps = (a,b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
  pins.sort((a,b) => Number(b.hasAttribute('aria-current')) - Number(a.hasAttribute('aria-current')));
  for (const pin of pins) {
    const label = pin.querySelector('.map-place-label');
    if (!label) continue;
    const marker = pin.getBoundingClientRect();
    pin.classList.remove('is-label-crowded');
    let placed = false;
    if (overlaps(marker, bounds)) {
      for (const [above, extra] of [[false, false], [true, false], [false, true], [true, true]]) {
        pin.classList.toggle('is-label-above', above);
        pin.classList.toggle('is-label-offset', extra);
        label.style.marginLeft = '0px';
        const initial = label.getBoundingClientRect();
        const shift = Math.max(bounds.left+4-initial.left, Math.min(0,bounds.right-4-initial.right));
        // The pin's inverse transform already keeps its subtree in screen pixels.
        label.style.marginLeft = shift + 'px';
        const frame = label.getBoundingClientRect();
        if (frame.top < bounds.top+4 || frame.bottom > bounds.bottom-4 ||
            markers.some(other => overlaps(other,frame)) || labels.some(other => overlaps(other,frame))) continue;
        labels.push(frame);
        placed = true;
        break;
      }
    }
    pin.classList.toggle('is-label-crowded', !placed);
  }
}

// Keep source coordinates fixed; scale and pan the viewport instead of displacing places.
function observeMapPoints(map, places, pins, expand) {
  const viewport = map.parentElement, image = map.querySelector('img');
  const diagram = map.classList.contains('route-diagram');
  const layers=Math.max(1,...places.map(p=>p.layer||0));
  let branchGap=1;
  for(const a of places)for(const b of places)if(a!==b && a.layer===b.layer)branchGap=Math.min(branchGap,Math.abs(a.x-b.x));
  const graphHeight=100+layers*(branchGap===1?70:112);
  const graphWidth=Math.max(220,98/Math.max(.01,branchGap));
  let zoom = 1, dx = 0, dy = 0, width = 0, height = 0, aspect = image ? 1 : graphWidth/graphHeight;
  const maxZoom = 64;
  const adjacentTo=station=>new Set([...map.querySelectorAll('svg line')].flatMap(line=>Number(line.dataset.from)===station?[Number(line.dataset.to)]:Number(line.dataset.to)===station?[Number(line.dataset.from)]:[]));
  function neighborhood(p){
    if(!diagram||!p)return p;
    const adjacent=adjacentTo(p.station);
    const nearby=[p,...places.filter(n=>adjacent.has(n.station))],xs=nearby.map(n=>n.x),ys=nearby.map(n=>n.y),lo=Math.min(...ys),hi=Math.max(...ys);
    const left=Math.min(...xs),right=Math.max(...xs),fit=Math.min(1,(viewport.clientWidth-16)/((right-left)*graphWidth+90),(viewport.clientHeight-16)/((hi-lo)*graphHeight+44));
    if(!viewport.closest('dialog')&&fit>=.7)return{x:(left+right)/2,y:(lo+hi)/2,fit};
    if(!viewport.closest('dialog'))return {...p,list:true};
    return (hi-lo)*graphHeight+44<=viewport.clientHeight-16?{x:p.x,y:(lo+hi)/2}:p;
  }
  let minZoom = 1, anchor = diagram ? places.find((p,i)=>pins[i].hasAttribute('aria-current')) || places[0] : null;
  const clustered = () => {
    map.querySelectorAll('.map-cluster').forEach(e=>e.remove());
    const local=diagram&&anchor&&!viewport.closest('dialog')?new Set([anchor.station,...adjacentTo(anchor.station)]):null;
    pins.forEach((p,i)=>p.hidden=!!local&&!local.has(places[i].station));
    let groups=places.map((p,i)=>({x:p.x,y:p.y,indices:[i]})).filter(g=>!local||local.has(places[g.indices[0]].station));
    const gapX=diagram ? 80 : 40, gapY=diagram ? 42 : 40;
    let merged=true;
    while(merged && !diagram){
      merged=false;
      for(let a=0;a<groups.length&&!merged;a++)for(let b=a+1;b<groups.length;b++){
        if(Math.abs((groups[a].x-groups[b].x)*width*zoom)>=gapX || Math.abs((groups[a].y-groups[b].y)*height*zoom)>=gapY)continue;
        const indices=[...groups[a].indices,...groups[b].indices];
        const currentIndex=indices.find(i=>pins[i].hasAttribute('aria-current'));
        groups[a]={indices,x:currentIndex===undefined?indices.reduce((v,i)=>v+places[i].x,0)/indices.length:places[currentIndex].x,y:currentIndex===undefined?indices.reduce((v,i)=>v+places[i].y,0)/indices.length:places[currentIndex].y};
        groups.splice(b,1);merged=true;break;
      }
    }
    for(const group of groups.filter(g=>g.indices.length>1)){
      const currentIndex=group.indices.find(i=>pins[i].hasAttribute('aria-current'));
      if(currentIndex!==undefined){group.x=places[currentIndex].x;group.y=places[currentIndex].y;}
      group.indices.forEach(i=>pins[i].hidden=true);
      const b=document.createElement('button');b.type='button';b.className='venue-pin map-cluster';
      b.style.left=group.x*100+'%';b.style.top=group.y*100+'%';
      b.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10" cy="10" r="6"/><path d="m15 15 5 5M7 10h6M10 7v6"/></svg>';
      b.setAttribute('aria-label','放大查看相邻地点');
      b.dataset.mapStations=group.indices.map(i=>places[i].station).join(',');
      if(currentIndex!==undefined){b.setAttribute('aria-current','location');b.classList.add('is-person');b.innerHTML=mapPersonIcon();const label=pins[currentIndex].querySelector('.map-place-label');if(label)b.append(label.cloneNode(true));b.setAttribute('aria-label',pins[currentIndex].getAttribute('aria-label').replace('地图选点：','当前位置：')+'，放大附近地点');}
      b.onclick=()=>{expand(group.indices.map(i=>places[i].station));requestAnimationFrame(()=>focus(group,Math.min(maxZoom,zoom*2.5)));};
      map.append(b);
    }
    const shown=new Map(groups.flatMap(g=>g.indices.map(i=>[places[i].station,g])));
    for(const line of map.querySelectorAll('svg line')){
      const from=shown.get(Number(line.dataset.from)),to=shown.get(Number(line.dataset.to));
      if(!from||!to){line.style.display='none';continue;}
      line.style.display=from===to?'none':'';
      for(const [key,value]of Object.entries({x1:from.x*1000,y1:from.y*1000,x2:to.x*1000,y2:to.y*1000}))line.setAttribute(key,value);
    }
  };
  function paint(){
    const vw=viewport.clientWidth,vh=viewport.clientHeight;
    if(!vw||!vh)return;
    if(image){width=Math.min(vw-32,(vh-32)*aspect);height=width/aspect;}
    else{width=graphWidth;height=graphHeight;minZoom=Math.min(1,(vw-48)/width,(vh-48)/height);}
    const centered=anchor&&(diagram?neighborhood(anchor):anchor);
    const list=!!centered?.list;
    map.classList.toggle('is-nearby-list',list);viewport.classList.toggle('has-nearby-list',list);
    const caption=viewport.parentElement.querySelector('.minimap-caption');
    if(diagram&&caption)caption.textContent=list?'相邻地点 · 点选查看实景':'通道图 · 非建筑平面';
    // Printed names need a readable nearby view on a small screen. Full-map
    // and manual pan/zoom clear the anchor and keep the user's chosen scale.
    const namedContext=centered&&image&&map.classList.contains('has-map-labels')&&!viewport.closest('dialog');
    zoom=Math.max(minZoom,centered?.fit??zoom,namedContext?420/Math.max(1,width):0);
    if(centered){dx=!diagram||width*zoom>vw ? (.5-centered.x)*width*zoom : 0;dy=!diagram||height*zoom>vh ? (.5-centered.y)*height*zoom : 0;}
    const limitX=Math.max(0,(width*zoom-vw)/2+60),limitY=Math.max(0,(height*zoom-vh)/2+60);
    dx=Math.max(-limitX,Math.min(limitX,dx));dy=Math.max(-limitY,Math.min(limitY,dy));
    Object.assign(map.style,{width:width+'px',height:height+'px',left:(vw-width)/2+dx+'px',top:(vh-height)/2+dy+'px',transform:`scale(${zoom})`});
    map.style.setProperty('--map-zoom',zoom);map.style.setProperty('--map-label-zoom',Math.max(1,zoom));viewport.dataset.zoom=zoom;clustered();
    if (map.classList.contains('is-schematic')) {
      const compact = matchMedia('(max-width: 600px)').matches;
      map.classList.toggle('is-compact-schematic', compact);
      if (compact) {
        placeCompactMapLabels(map, viewport);
        return;
      }
      for (const pin of pins) {
        pin.classList.remove('is-label-crowded', 'is-label-above', 'is-label-offset');
        const label = pin.querySelector('.map-place-label');
        if (label) label.style.marginLeft = '';
      }
    }
    const current=map.querySelector('.venue-pin[aria-current]:not([hidden])'),label=current?.querySelector('.map-place-label');
    if(image&&label){
      current.classList.remove('is-label-above');label.style.marginLeft='0px';
      const v=viewport.getBoundingClientRect(),p=current.getBoundingClientRect();
      if(p.right>v.left&&p.left<v.right&&p.bottom>v.top&&p.top<v.bottom){
        if(label.getBoundingClientRect().bottom>v.bottom-4)current.classList.add('is-label-above');
        const r=label.getBoundingClientRect();
        label.style.marginLeft=Math.max(v.left+4-r.left,Math.min(0,v.right-4-r.right))+'px';
      }
    }
  }
  function focus(p,scale){anchor=p;zoom=scale;paint();}
  function change(factor,point){
    const rect=viewport.getBoundingClientRect(),x=(point?.clientX??rect.left+rect.width/2)-rect.left-rect.width/2,y=(point?.clientY??rect.top+rect.height/2)-rect.top-rect.height/2;
    const next=Math.max(minZoom,Math.min(maxZoom,zoom*factor)),ratio=next/zoom;
    anchor=null;dx=x-(x-dx)*ratio;dy=y-(y-dy)*ratio;zoom=next;paint();
  }
  const observer=new ResizeObserver(paint);observer.observe(viewport);
  if(image)image.decode().then(()=>{aspect=image.naturalWidth/image.naturalHeight;paint();}).catch(()=>{});
  const pointers=new Map();let gesture,moved=false;
  const rebase=()=>{gesture={points:[...pointers.values()].slice(0,2),zoom,dx,dy};};
  const down=e=>{
    if(!pointers.size)moved=false;
    if(e.button!==0||e.target.closest('button')&&e.pointerType!=='touch')return;
    pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});anchor=null;rebase();
    if(pointers.size>1)moved=true;
    if(!e.target.closest('button'))viewport.setPointerCapture(e.pointerId);
  };
  const move=e=>{
    if(!pointers.has(e.pointerId))return;
    pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
    const points=[...pointers.values()].slice(0,2),start=gesture.points;
    moved ||= points.length>1||Math.hypot(points[0].x-start[0].x,points[0].y-start[0].y)>4;
    if(!moved)return;
    for(const id of pointers.keys())if(!viewport.hasPointerCapture(id))viewport.setPointerCapture(id);
    viewport.classList.add('is-panning');
    if(points.length>1){
      const rect=viewport.getBoundingClientRect(),distance=ps=>Math.hypot(ps[1].x-ps[0].x,ps[1].y-ps[0].y),ratio=Math.max(minZoom,Math.min(maxZoom,gesture.zoom*distance(points)/Math.max(1,distance(start))))/gesture.zoom;
      const x=(start[0].x+start[1].x)/2-rect.left-rect.width/2,y=(start[0].y+start[1].y)/2-rect.top-rect.height/2;
      zoom=gesture.zoom*ratio;
      dx=(points[0].x+points[1].x-start[0].x-start[1].x)/2+x-(x-gesture.dx)*ratio;
      dy=(points[0].y+points[1].y-start[0].y-start[1].y)/2+y-(y-gesture.dy)*ratio;
    }else{dx=gesture.dx+points[0].x-start[0].x;dy=gesture.dy+points[0].y-start[0].y;}
    paint();
  };
  const up=e=>{if(!pointers.delete(e.pointerId))return;rebase();if(!pointers.size)viewport.classList.remove('is-panning');};
  const lost=e=>{if(e.target===viewport&&!viewport.hasPointerCapture(e.pointerId))up(e);};
  const click=e=>{if(moved&&e.detail){e.preventDefault();e.stopPropagation();}moved=false;};
  const wheel=e=>{e.preventDefault();const delta=e.deltaY*(e.deltaMode===1?16:e.deltaMode===2?viewport.clientHeight:1);change(Math.exp(-Math.max(-240,Math.min(240,delta))*.002),e);};
  const key=e=>{if(e.target!==viewport)return;if(['+','=','-','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();if(['+','=','-'].includes(e.key))change(e.key==='-'?1/1.5:1.5);else{anchor=null;dx+=e.key==='ArrowLeft'?60:e.key==='ArrowRight'?-60:0;dy+=e.key==='ArrowUp'?60:e.key==='ArrowDown'?-60:0;paint();}}};
  for(const [name,fn]of [['pointerdown',down],['pointermove',move],['pointerup',up],['pointercancel',up],['lostpointercapture',lost],['keydown',key]])viewport.addEventListener(name,fn);
  viewport.addEventListener('click',click,true);
  viewport.addEventListener('wheel',wheel,{passive:false});paint();
  return {change,fit(){anchor=null;zoom=minZoom;dx=dy=0;paint();},focus:station=>{
    const p=places.find(p=>p.station===station);if(!p)return;
    if(map.classList.contains('is-schematic')){anchor=map.classList.contains('has-map-labels')?p:null;zoom=1;dx=dy=0;paint();return;}
    if(image?.naturalWidth){aspect=image.naturalWidth/image.naturalHeight;paint();}
    focus(p,Math.max(zoom,diagram?1:Math.max(2,viewport.clientWidth/Math.max(1,width))));
  },refresh:paint,
    disconnect(){observer.disconnect();viewport.removeEventListener('click',click,true);for(const [name,fn]of [['pointerdown',down],['pointermove',move],['pointerup',up],['pointercancel',up],['lostpointercapture',lost],['keydown',key],['wheel',wheel]])viewport.removeEventListener(name,fn);}};
}

function mapPersonIcon(){return '<svg class="map-person-icon" viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="9" r="4"/><path d="M10 25v-7a6 6 0 0 1 12 0v7M16 18v9"/></svg>';}

function mountTourNavigator(stage, tour, jump, isEdge, mayJump) {
  const make=(tag,text,parent,cls)=>{const e=document.createElement(tag);if(text)e.textContent=text;if(cls)e.className=cls;parent?.append(e);return e;};
  const button=(text,parent,action,cls)=>{const b=make('button',text,parent,cls);b.type='button';b.onclick=action;return b;};
  const groups=tour.venue?.levels || [{id:'all',name:tour.navigation_map?.name || '全部地点',plan:tour.navigation_map?.plan,source:tour.navigation_map?.source,kind:tour.navigation_map?.kind,labels_embedded:tour.navigation_map?.labels_embedded,
    places:tour.navigation_map?.places || tour.stations.map((_,station)=>({station}))}];
  const groupOf=i=>groups.find(g=>g.places.some(p=>p.station===i)) || groups[0];
  const groupLabel=g=>g.floor || g.name.split(' · ')[0];
  let current=tour.entry ?? 0,prior=null,busy=false,selectedGroup=groupOf(current).id,mapView,clusterSelection=null;
  const visited=new Set(),controls=make('nav','',stage,'explore-tools');controls.setAttribute('aria-label','探索导览');
  const drawer=make('section','',stage,'point-drawer');drawer.setAttribute('aria-label','点位目录');drawer.hidden=true;
  const mapPanel=make('section','',stage,'tour-minimap');mapPanel.setAttribute('aria-label','导览地图');mapPanel.hidden=true;
  const mapDialog=make('dialog','',stage,'tour-map-dialog');mapDialog.setAttribute('aria-label','展开导览地图');
  const toggle=(panel,trigger)=>{const open=panel.hidden;drawer.hidden=true;mapPanel.hidden=true;pointToggle.setAttribute('aria-expanded','false');mapToggle.setAttribute('aria-expanded','false');panel.hidden=!open;trigger.setAttribute('aria-expanded',String(open));if(open){mapView?.refresh();if(panel===mapPanel)mapView?.focus(current);}};
  const pointToggle=button('地点 · '+tour.stations.length,controls,()=>{toggle(drawer,pointToggle);if(!drawer.hidden)search.focus({preventScroll:true});});pointToggle.setAttribute('aria-expanded','false');
  const mapToggle=button('地图',controls,()=>toggle(mapPanel,mapToggle));mapToggle.setAttribute('aria-expanded','false');
  if(tour.archive_notice){
    const scope=make('details','',controls,'visit-scope');make('summary','档案说明',scope);make('p',tour.archive_notice,scope);
  }
  const previous=button('返回上个地点',controls,()=>select(prior));previous.hidden=true;
  const entry=button('回到入口',controls,()=>select(tour.entry ?? 0));
  const heading=make('div','',drawer,'explore-heading');make('h3','选择地点',heading);
  const closeDrawer=button('关闭',heading,()=>{toggle(drawer,pointToggle);pointToggle.focus();});
  const searchLabel=make('label','搜索地点',drawer,'point-search');const search=make('input','',searchLabel);search.type='search';search.placeholder='搜索名称';
  const filters=make('div','',drawer,'point-filters');const filterLabel=make('label','区域',filters),filter=make('select','',filterLabel);filter.append(new Option('全部区域',''));
  if(groups.length>1)groups.forEach(g=>filter.append(new Option(groupLabel(g),g.id)));filters.hidden=groups.length<2;
  const count=make('p','',drawer,'point-results');count.setAttribute('role','status');const list=make('div','',drawer,'point-list');
  const mapHeader=make('div','',mapPanel,'explore-heading');make('h3','地图',mapHeader);
  const expandMap=button('展开',mapHeader,()=>openExpanded(),'map-expand');expandMap.setAttribute('aria-label','展开地图选点');
  button('关闭',mapHeader,()=>{if(mapDialog.open)mapDialog.close();else{toggle(mapPanel,mapToggle);mapToggle.focus();}});
  const mapLabel=make('label','楼层 / 区域',mapPanel,'map-group'),mapSelect=make('select','',mapLabel);
  groups.forEach(g=>mapSelect.append(new Option(groupLabel(g),g.id)));mapLabel.hidden=groups.length<2;
  const mapBody=make('div','',mapPanel,'minimap-body');
  const mapFooter=make('div','',mapPanel,'map-footer');const mapCurrent=make('p','',mapFooter,'minimap-current');mapCurrent.setAttribute('role','status');
  const locate=button('定位当前地点',mapFooter,()=>{selectedGroup=groupOf(current).id;drawMap();mapView?.focus(current);},'map-locate');
  const help=make('p','',mapPanel,'minimap-help');
  const hintParent=stage.closest('.shell')?.querySelector('.orientation-help') || stage;
  const hint=make('p','',hintParent,'explore-hint');make('span',tour.stations.some((s,i)=>(s.links || []).some(l=>isEdge(i,l)))?'拖动画面环顾 · 点画面箭头前进':'拖动画面环顾 · 从地点目录选景',hint);
  button('知道了',hint,()=>{hint.hidden=true;});
  const topCard=stage.closest('.shell')?.querySelector('.bar:not([hidden])>div');if(topCard)topCard.append(controls);
  function openExpanded(indices=null){
    clusterSelection=indices;mapPanel.hidden=false;drawer.hidden=false;closeDrawer.hidden=true;expandMap.hidden=true;
    mapDialog.append(drawer,mapPanel);filter.value='';search.value='';drawList();
    if(!mapDialog.open)mapDialog.showModal();mapToggle.setAttribute('aria-expanded','true');pointToggle.setAttribute('aria-expanded','false');
    requestAnimationFrame(()=>{mapView?.refresh();if(!indices)mapView?.fit();});
  }
  mapDialog.addEventListener('close',()=>{stage.append(drawer,mapPanel);drawer.hidden=true;mapPanel.hidden=true;closeDrawer.hidden=false;expandMap.hidden=false;
    clusterSelection=null;filter.value='';drawList();mapToggle.setAttribute('aria-expanded','false');mapToggle.focus({preventScroll:true});});
  function select(to){
    if(to===null||busy||!mayJump(to))return;
    if(mapDialog.open)mapDialog.close();
    drawer.hidden=true;mapPanel.hidden=true;pointToggle.setAttribute('aria-expanded','false');mapToggle.setAttribute('aria-expanded','false');
    hint.hidden=true;jump(to);
  }
  function drawList(){
    const query=search.value.trim().toLocaleLowerCase();list.replaceChildren();
    const indices=tour.stations.map((_,i)=>i);
    const order=tour.fixed_route?indices:[...new Set([...groups.flatMap(g=>mapTopology(tour,g.places,isEdge).filter(p=>!p.portal).map(p=>p.station)),...indices])];
    let shown=0;
    for(const index of order){
      const item=tour.stations[index],group=groups.find(g=>g.places.some(p=>p.station===index));
      if((filter.value&&group?.id!==filter.value)||(query&&!(item.name+' '+(group?.name||'未定位')).toLocaleLowerCase().includes(query)))continue;
      shown++;const b=button('',list,()=>select(index),'point-item');b.setAttribute('aria-label','看实景：'+item.name);
      if(item.preview){const img=make('img','',b);img.src=item.preview;img.alt='';img.loading='lazy';}
      const info=make('span','',b);make('small',String(order.indexOf(index)+1).padStart(2,'0')+' · '+(group?groupLabel(group):'未定位'),info);make('strong',item.name,info);
      make('span',index===current?'当前地点':visited.has(index)?'已看过':'查看实景',info,'point-state');
      b.disabled=busy||!mayJump(index);if(index===current)b.setAttribute('aria-current','location');
      if(clusterSelection?.includes(index))b.classList.add('is-nearby');
    }
    count.textContent=shown?shown+' 个地点':'未找到地点，试试其他名称';
  }
  function drawMap(){
    mapView?.disconnect();mapSelect.value=selectedGroup;
    const group=groups.find(g=>g.id===selectedGroup)||groups[0];mapBody.replaceChildren();mapPanel.querySelectorAll('.minimap-source').forEach(e=>e.remove());
    const mapBar=make('div','',mapBody,'map-bar');
    const caption=make('p',group.plan?(group.kind==='schematic'?'游览示意 · 非比例':'拍摄位置图'):'通道图 · 非建筑平面',mapBar,'minimap-caption');
    const viewport=make('div','',mapBody,'map-viewport');viewport.tabIndex=0;viewport.setAttribute('aria-label','地图，可拖动或使用缩放按钮');
    const map=make('div','',viewport,group.plan?'venue-plan':'route-diagram');
    if(group.plan){map.classList.add('birdseye-map');if(group.kind==='schematic')map.classList.add('is-schematic');if(group.labels_embedded)map.classList.add('has-map-labels');const img=make('img','',map);img.src=group.plan;img.alt=group.name+'点位图';img.draggable=false;img.onerror=()=>{help.textContent='地图无法显示，请从地点目录选景。';};}
    const places=group.places.filter(p=>p.station!==undefined);
    const positioned=group.plan?places.filter(p=>p.x!==undefined):mapTopology(tour,places,isEdge);
    const positions=new Map(positioned.map(p=>[p.station,p]));
    const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 1000 1000');svg.setAttribute('preserveAspectRatio','none');svg.setAttribute('aria-hidden','true');svg.classList.add('map-paths');map.append(svg);
    const drawn=new Set();
    for(const p of group.plan?[]:positioned)for(const link of tour.stations[p.station].links||[]){
      const to=positions.get(link.to);if(!to||!isEdge(p.station,link))continue;
      const key=[p.station,link.to].sort((a,b)=>a-b).join('-');if(drawn.has(key))continue;drawn.add(key);
      const line=document.createElementNS(ns,'line');line.dataset.from=p.station;line.dataset.to=link.to;
      if(p.station===current||link.to===current)line.classList.add('is-current-path');
      for(const [key,value]of Object.entries({x1:p.x*1000,y1:p.y*1000,x2:to.x*1000,y2:to.y*1000}))line.setAttribute(key,value);
      svg.append(line);
    }
    const pins=positioned.map(p=>{const b=button('',map,()=>select(p.station),'venue-pin');const label=tour.stations[p.station].name,area=groupLabel(groupOf(p.station));const name=(p.portal&&!label.startsWith(area)?area+' · ':'')+label;
      if(!group.labels_embedded || group.kind==='schematic')make('span',name,b,'map-place-label');if(p.portal)b.classList.add('is-portal');b.dataset.mapStation=p.station;b.style.left=p.x*100+'%';b.style.top=p.y*100+'%';
      if((tour.stations[current].links||[]).some(l=>l.to===p.station&&isEdge(current,l)))b.classList.add('is-connected');
      b.setAttribute('aria-label','地图选点：'+tour.stations[p.station].name);b.disabled=busy||!mayJump(p.station);
      if(p.station===current){b.setAttribute('aria-current','location');if(group.plan){b.classList.add('is-person');b.insertAdjacentHTML('afterbegin',mapPersonIcon());}}else if(visited.has(p.station))b.classList.add('is-visited');return b;});
    const placesOnly = !group.plan && drawn.size === 0;
    if (placesOnly) {
      mapView = null;
      viewport.classList.add('has-nearby-list');
      viewport.setAttribute('aria-label','拍摄地点');
      map.classList.add('is-nearby-list','is-place-list');
      caption.textContent = '拍摄地点 · 点选实景';
    } else {
      mapView=observeMapPoints(map,positioned,pins,indices=>openExpanded(indices));
      const zoomTools=make('div','',mapBar,'map-zoom');
      const plus=button('+',zoomTools,()=>mapView.change(1.6));plus.setAttribute('aria-label','放大地图');
      const minus=button('−',zoomTools,()=>mapView.change(1/1.6));minus.setAttribute('aria-label','缩小地图');
      button('全图',zoomTools,()=>mapView.fit());
    }
    const currentGroup=groupOf(current),browsing=group.id!==currentGroup.id;
    help.textContent=browsing?'正在查看'+groupLabel(group)+' · 点地点查看实景':group.plan?'人物是当前观看点 · 点蓝点看实景':placesOnly?'蓝色为当前位置 · 通道待确认':'蓝色为当前位置与相邻通道 · 点地点查看实景';
    mapCurrent.textContent=(groups.length>1?groupLabel(currentGroup)+' · ':'')+tour.stations[current].name;
    if(group.source){const source=make('details','',mapPanel,'minimap-source');make('summary','地图来源',source);make('p',group.source,source);}
  }
  search.oninput=()=>{clusterSelection=null;drawList();};filter.onchange=()=>{clusterSelection=null;drawList();if(filter.value){selectedGroup=filter.value;drawMap();}};
  mapSelect.onchange=()=>{selectedGroup=mapSelect.value;clusterSelection=null;if(mapDialog.open){filter.value=selectedGroup;drawList();}drawMap();requestAnimationFrame(()=>mapView?.fit());};
  const keyHost=stage.closest('.shell')||stage;
  const escape=e=>{if(mapDialog.open||e.key!=='Escape'||(drawer.hidden&&mapPanel.hidden))return;e.stopPropagation();e.preventDefault();drawer.hidden=true;mapPanel.hidden=true;pointToggle.setAttribute('aria-expanded','false');mapToggle.setAttribute('aria-expanded','false');mapToggle.focus();};keyHost.addEventListener('keydown',escape);
  return {update(index,loading){if(!loading&&index!==current){prior=current;current=index;hint.hidden=true;}busy=loading;if(!busy)visited.add(current);selectedGroup=groupOf(current).id;
    previous.hidden=prior===null;previous.disabled=busy||!mayJump(prior);entry.hidden=current===(tour.entry??0);entry.disabled=busy||!mayJump(tour.entry??0);drawList();drawMap();},
    destroy(){mapView?.disconnect();if(mapDialog.open)mapDialog.close();mapDialog.remove();controls.remove();keyHost.removeEventListener('keydown',escape);}};
}

function mountVenueOverview(root, tour, openScene) {
  const venue = tour.venue;
  const make = (tag, text, parent, cls) => {
    const el = document.createElement(tag);
    if (text) el.textContent = text;
    if (cls) el.className = cls;
    if (parent) parent.append(el);
    return el;
  };
  const button = (text, parent, action, cls) => {
    const b = make('button', text, parent, cls);
    b.type = 'button';
    b.onclick = action;
    return b;
  };
  const floorOf = (i) => venue.levels.find(l => l.places.some(p => p.station === i));
  const prefixes = new Set(venue.levels.map(l => l.name.includes(' · ') ? l.name.split(' · ')[0] : ''));
  const sharedFloor = prefixes.size === 1 ? [...prefixes][0] : '';
  const shortName = l => sharedFloor ? l.name.slice(sharedFloor.length + 3) : l.name;
  const crossings = new Map();
  for (const c of venue.connections) {
    const from = floorOf(c.from), to = floorOf(c.to);
    if (c.pending || from === to || !from.id || !to.id) continue;
    const direct = from.id + '>' + to.id, reverse = to.id + '>' + from.id;
    if (crossings.has(reverse)) crossings.get(reverse).both = true;
    else if (!crossings.has(direct)) crossings.set(direct, {from, to, both:false});
  }
  const count = tour.stations.length;
  const scope = tour.first_look ? '楼层待确认' : sharedFloor ? sharedFloor + ' · ' + venue.levels.length + ' 个分区'
    : venue.levels.length + ' 个楼层或分区';
  make('p', scope + ' · ' + count + ' 处实景', root, 'venue-scope');
  const gap = venue.overview_note || tour.grade?.reasons[0];
  if (gap) make('p', gap, root, 'venue-scope-note');
  const recommended = make('div', '', root, 'venue-recommended');
  const tabs = make('nav', '', root, 'venue-floors');
  tabs.setAttribute('aria-label', '选择楼层或分区');
  const content = make('div', '', root, 'venue-content');
  const details = make('details', '', root, 'venue-details');
  make('summary', '覆盖范围与通行说明', details);
  make('p', venue.coverage, details);
  for (const reason of tour.grade?.reasons || []) make('p', reason, details);
  const selectedAt = new Map();
  let mapLabels;
  const selectScene = i => {selectedAt.set(floorOf(i).id, i);openScene(i);};
  const floorButtons = new Map();
  if (venue.recommended.length) {
    const [first, ...others] = venue.recommended;
    const firstName = tour.stations[first].name;
    const start = button(firstName.startsWith('先看') ? firstName : '先看 · ' + firstName, recommended, () => selectScene(first));
    start.classList.add('venue-start');
    if (others.length) {
      const more = make('details', '', recommended, 'venue-other-recommendations');
      make('summary', '其他推荐看点', more);
      const choices = make('div', '', more, 'venue-recommendation-list');
      for (const i of others) {
        const b = button(tour.stations[i].name, choices, () => selectScene(i));
        b.setAttribute('aria-label', '推荐看点：' + floorOf(i).name + ' · ' + tour.stations[i].name);
      }
    }
  } else recommended.hidden = true;

  function draw(id, selected = selectedAt.get(id), focus = false) {
    mapLabels?.disconnect();
    const level = venue.levels.find(l => l.id === id);
    selectedAt.set(id, selected);
    for (const [key, b] of floorButtons) b.setAttribute('aria-pressed', String(key === id));
    content.replaceChildren();
    const scenePanel = make('section', '', content);
    const live = level.places.filter(p => p.station !== undefined);
    const areas = level.places.filter(p => p.note);
    const directoryOnly = !live.length && !level.plan;
    content.classList.toggle('venue-directory', directoryOnly);
    const mapPanel = directoryOnly ? null : make('section', '', content, 'venue-map-panel');
    const heading = make('div', '', scenePanel, 'venue-section-title');
    make('h2', shortName(level), heading);
    const position = make('p', selected === undefined ? '点照片进入' : '刚才查看：' + tour.stations[selected].name, heading);
    position.setAttribute('role', 'status');

    const grid = make('div', '', scenePanel, 'venue-places');
    let selectedButton;
    for (const [i, p] of live.entries()) {
      const item = tour.stations[p.station];
      const b = button('', grid, () => selectScene(p.station), 'venue-scene');
      const image = make('img', '', b);
      image.alt = '';
      image.src = item.preview || item.originals[0].image;
      const description = make('span', '', b, 'venue-scene-caption');
      const kind = item.panorama ? item.panorama.haov === 360 ? '360° 实景' : '局部全景' : '单视角照片';
      const entryBadge = !tour.first_look && p.station === tour.entry ? '入口 · ' : '';
      make('small', (p.station+1) + ' · ' + entryBadge + kind, description);
      make('strong', item.name, description);
      if (p.station === selected) {
        b.setAttribute('aria-current', 'location');
        selectedButton = b;
      }
    }
    if (!live.length) {
      position.textContent = directoryOnly ? '仅有资料，无实景和通行指引' : '仅有位置示意，无实景';
      grid.hidden = true;
    }
    if (directoryOnly) {
      const list = make('ul', '', scenePanel, 'venue-area-list');
      for (const area of areas) {
        const row = make('li', '', list);
        make('strong', area.name, row);
        make('p', area.note, row);
      }
      if (!areas.length) make('p', '此处暂未收录地点。', scenePanel);
      if (focus) floorButtons.get(id).focus();
      return;
    }

    const mapHeading = make('div', '', mapPanel, 'venue-map-title');
    make('h2', '楼层位置', mapHeading);
    if (level.plan) make('p', '选择地点进入实景', mapHeading);
    if (level.plan) {
      const viewport = make('div', '', mapPanel, 'map-viewport');
      viewport.style.height='360px';
      const map = make('div', '', viewport, 'venue-plan');
      const image = make('img', '', map);
      image.alt = level.name + '位置示意，对应点位可从照片入口打开';
      image.src = level.plan;
      image.onerror = () => { map.hidden = true; make('p', '平面未能显示，可继续用照片入口浏览。', mapPanel); };
      const positioned=[],pins=[];
      for (const p of level.places) {
        const number = p.station === undefined ? '' : String(p.station+1);
        if (p.x === undefined) continue;
        const dot = number ? button('', map, () => selectScene(p.station), 'venue-pin')
          : make('span', '·', map, 'venue-pin plan-only');
        dot.style.left = p.x * 100 + '%';
        dot.style.top = p.y * 100 + '%';
        if (number) {
          positioned.push(p);pins.push(dot);
          dot.setAttribute('aria-label', '看实景：' + tour.stations[p.station].name);
          dot.title = tour.stations[p.station].name;
          if (p.station === selected) dot.setAttribute('aria-current', 'location');
        } else dot.setAttribute('aria-hidden', 'true');
      }
      if(pins.length)mapLabels=observeMapPoints(map,positioned,pins,()=>{});
    } else make('p', '缺少可用平面，先按照片入口浏览。', mapPanel);

    const directions = venue.connections.filter(c => !c.pending && floorOf(c.from) === level);
    if (crossings.size) {
      make('h3', sharedFloor ? '已核对的跨区方向' : '已核对的楼层或分区连接', mapPanel, 'venue-crossing-title');
      const list = make('ul', '', mapPanel, 'venue-crossings');
      for (const {from, to, both} of crossings.values()) {
        make('li', shortName(from) + (both ? ' ↔ ' : ' → ') + shortName(to), list);
      }
    }
    if (level.source || directions.length || areas.length) {
      const more = make('details', '', mapPanel, 'venue-details');
      make('summary', '位置与通道详情', more);
      if (level.source) make('p', level.source, more);
      for (const p of areas) make('p', p.name + ' · ' + p.note, more);
      const list = make('ul', '', more, 'venue-connections');
      for (const c of directions) {
        const row = make('li', '', list);
        make('strong', tour.stations[c.from].name + ' → ' + tour.stations[c.to].name, row);
        make('p', [c.name, c.access, c.hours, c.audience].filter(Boolean).join(' · '), row);
      }
    }
    if (focus) (selectedButton || floorButtons.get(id)).focus();
  }
  for (const level of venue.levels) {
    const b = button(shortName(level), tabs, () => draw(level.id));
    b.setAttribute('aria-label', level.name);
    floorButtons.set(level.id, b);
  }
  draw(venue.opening_level);
  return draw;
}

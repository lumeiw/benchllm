// ИС ОЗЯМ-ФОП · выгрузка «Библиотеки ЦОК» (academy-content.apkpro.ru) для этапа «цок_корпус».
//
// Как запустить:
//   1. Войти на https://academy-content.apkpro.ru под своей учётной записью (без VPN).
//   2. Открыть каталог нужного предмета и класса, например физика 7 кл.:
//      https://academy-content.apkpro.ru/ru/catalog/03/07?class=07      (03 — физика, 07 — класс)
//   3. F12 → Console → вставить этот файл целиком → Enter.
//   4. Через 2–5 минут скачается цок_<предмет>_<класс>.json. Путь к нему — в конфиге бенчмарка, ключ «цок».
//   История: открыть каталог «История» нужного класса (код предмета — первое число в адресе каталога). Если «История России»
//   и «Всеобщая история» — разные каталоги, выгрузить оба и указать в конфиге списком: "цок": ["…_россия.json", "…_всеобщая.json"].
//
// Что делает: берёт список уроков каталога (первые 100 — из данных страницы каталога, остальные — листая
// страницы кнопкой «Следующая страница» и читая ответ сайта);
// для каждого урока берёт данные страницы урока (/_next/data/<build>/ru/lesson/<uid>.json — тот же запрос, что делает сайт);
// по списку файлов материалов скачивает текстовые: specification.xml (тесты с ключами), content/*/N.xml (страницы),
// structure.json и data.json (интерактивы), *.vtt (субтитры видео) — через тот же адрес хранилища, что использует сайт;
// извлекает из них текст прямо в браузере и сохраняет один JSON. Картинки и видео не скачиваются, только их список.
// Скрипт ничего не меняет на сайте и не обращается к учётным данным.
(async () => {
  const CFG = { download: true, workers: 6, maxPages: 60, pageWaitMs: 2500 };
  const S3 = 'https://ru-edu-prod-ecl-storage.obs.ru-moscow-1.hc.sbercloud.ru/';
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const log = (...a) => console.log('[ЦОК]', ...a);
  const state = (window.__cokExport = { stage: 'каталог', lessons: 0, files: 0, filesDone: 0, done: false, error: null });
  try {
    const m = location.pathname.match(/\/catalog\/(\d+)\/(\d+)/);
    if (!m) throw new Error('Откройте страницу каталога предмета и класса: /ru/catalog/<предмет>/<класс>');
    const [_, subj, cls] = m;
    const BUILD = (window.__NEXT_DATA__ || {}).buildId;
    if (!BUILD) throw new Error('Не найден buildId страницы (window.__NEXT_DATA__) — обновите страницу каталога');

    // ── 1. уроки каталога ──
    // первые до 100 — из данных страницы каталога; остальные — листая страницы каталога кнопкой «Следующая страница»:
    // сайт сам запрашивает следующую страницу, скрипт только читает пришедший ответ.
    const items = [], seen = new Set();
    const add = arr => { for (const it of arr || []) if (it && it.lessonUid && !seen.has(it.lessonUid)) { seen.add(it.lessonUid); items.push(it); } };
    const findLessons = o => {
      if (o && o.lessons && Array.isArray(o.lessons.items)) return o.lessons;
      if (o && typeof o === 'object') for (const v of Object.values(o)) { const r = findLessons(v); if (r) return r; }
      return null;
    };
    const q = `class=${cls}&limit=100&cmsPagePath=ru&cmsPagePath=catalog&cmsPagePath=${subj}&cmsPagePath=${cls}`;
    const L1 = findLessons(await fetch(`/_next/data/${BUILD}/ru/catalog/${subj}/${cls}.json?${q}`).then(r => r.json()));
    if (!L1) throw new Error('В данных каталога нет списка уроков');
    add(L1.items);
    const total = L1.total;
    if (items.length < total) {
      const captured = [];
      const open0 = XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open = function (method, url) {
        if (/\/_system\/ajax\/block\/\d+\/content/.test(String(url))) {
          this.addEventListener('load', () => { try { captured.push(JSON.parse(this.responseText)); } catch (e) { /* не JSON */ } });
        }
        return open0.apply(this, arguments);
      };
      const btn = t => [...document.querySelectorAll('button, a, [role=button]')].find(b => (b.innerText || b.getAttribute('aria-label') || '').trim() === t);
      try {
        for (let p = 0; p < CFG.maxPages && items.length < total; p++) {
          const next = btn('Следующая страница');
          if (!next || next.disabled || next.getAttribute('aria-disabled') === 'true') break;
          const n0 = captured.length;
          next.click();
          for (let w = 0; w < 20 && captured.length === n0; w++) await sleep(CFG.pageWaitMs / 5);
          for (const c of captured.splice(0)) { const L = findLessons(c); if (L) add(L.items); }
          log(`каталог: уроков ${items.length} из ${total}`);
        }
      } finally { XMLHttpRequest.prototype.open = open0; }
    }
    if (items.length < total) log(`ВНИМАНИЕ: собрано ${items.length} уроков из ${total}`);
    state.lessons = items.length;
    log('уроков в каталоге:', items.length, 'из', total);

    // ── 2. данные уроков ──
    state.stage = 'уроки';
    const struct = [];
    for (const it of items) {
      const uid = it.lessonUid;
      const j = await fetch(`/_next/data/${BUILD}/ru/lesson/${uid}.json`).then(r => r.json());
      const L = j.pageProps.page.blocks.find(b => b.response && b.response.lesson).response.lesson;
      const rec = {
        uid, number: it.lessonNumber, name: it.name, author: it.author,
        youWillKnow: L.youWillKnow, youWillLearn: L.youWillLearn,
        keywords: (L.keywords || []).map(k => k.name), s3: L.s3Folder, tasks: [],
      };
      for (const u of L.units || []) for (const md of u.modules || []) for (const t of md.tasks || []) {
        rec.tasks.push({
          uid: t.uid, number: t.number, name: t.name, eom: t.eomType && t.eomType.name,
          play: t.eomType && t.eomType.taskPlaybackMethod && t.eomType.taskPlaybackMethod.code,
          module: md.moduleType && md.moduleType.nameForTeacher, files: t.fileList || [],
        });
      }
      struct.push(rec);
    }

    // ── 3. текстовые файлы материалов ──
    state.stage = 'файлы';
    const want = f => /specification\.xml$|\/\d+\.xml$|structure\.json$|\/data\.json$|\.vtt$/.test(f);
    const list = [];
    struct.forEach(l => l.tasks.forEach(t => t.files.filter(want).forEach(f => list.push(f))));
    state.files = list.length;
    const F = {};
    const queue = list.slice();
    const worker = async () => {
      while (queue.length) {
        const f = queue.shift();
        let txt = null;
        for (let attempt = 0; attempt < 3 && txt === null; attempt++) {
          try {
            const r = await fetch(f.replace(S3, '/api/s3/ru-edu-prod-ecl-storage/'));
            txt = r.ok ? await r.text() : 'ERR ' + r.status;
          } catch (e) { if (attempt === 2) txt = 'ERR ' + e; else await sleep(1000); }
        }
        F[f] = txt; state.filesDone++;
      }
    };
    await Promise.all(Array.from({ length: CFG.workers }, worker));

    // ── 4. извлечение текста ──
    state.stage = 'текст';
    const H = h => {
      if (!h) return '';
      const d = new DOMParser().parseFromString('<div>' + h + '</div>', 'text/html');
      d.querySelectorAll('element-input,input').forEach(e => e.replaceWith(' [___] '));
      d.querySelectorAll('element-maths,.maths').forEach(e => e.replaceWith(' ' + e.textContent.trim() + ' '));
      d.querySelectorAll('b,strong').forEach(e => e.replaceWith('**' + e.textContent + '**'));
      d.querySelectorAll('p,div,li,br,tr,h1,h2,h3').forEach(e => e.append('\n'));
      d.querySelectorAll('td,th').forEach(e => e.append(' | '));
      return d.body.textContent.replace(/[ \t\u00a0]+/g, ' ').replace(/\n\s*\n+/g, '\n').trim();
    };
    const CYR = /[А-Яа-яЁё]/;
    const DROP = new Set(['theme', 'thumbs', 'convertationTasks', 'reference', 'provider', 'contentType', 'size', 'uid', 'id', 'listOrder', 'src', 'origin', 'fullscreen', 'keepSize', 'illustration']);
    const simp = o => {
      if (Array.isArray(o)) { const a = o.map(simp).filter(x => x !== undefined); return a.length ? a : undefined; }
      if (o && typeof o === 'object') {
        const r = {};
        for (const [k, v] of Object.entries(o)) { if (DROP.has(k)) continue; const s = simp(v); if (s !== undefined) r[k] = s; }
        return Object.keys(r).length ? r : undefined;
      }
      if (typeof o === 'string') {
        if (o.startsWith('data:') || /^https?:/.test(o)) return undefined;
        if (/[<&]/.test(o)) o = H(o);
        if (!CYR.test(o) && !/^\$|\\|=|\d/.test(o)) return o.length < 20 ? o : undefined;
        return o.length > 20000 ? o.slice(0, 20000) : o;
      }
      if (typeof o === 'boolean' || typeof o === 'number') return o;
      return undefined;
    };
    const kid = (n, tag) => [...n.children].filter(c => c.tagName === tag);
    const one = (n, tag) => kid(n, tag)[0];
    const htmlOf = n => { if (!n) return ''; let h = one(n, 'Html'); if (h && one(h, 'Html')) h = one(h, 'Html'); return h ? H(h.textContent) : ''; };
    const specTxt = t => {
      const d = new DOMParser().parseFromString(t, 'application/xml');
      const desc = htmlOf(d.querySelector('Specification > Description'));
      const tasks = [...d.querySelectorAll('Tasks > Task')].map(T => ({
        n: one(T, 'Number')?.textContent, mark: one(T, 'MaxMark')?.textContent,
        variants: [...T.querySelectorAll(':scope > Contents')].map(C => {
          const c = one(C, 'Content'); if (!c) return null;
          const els = kid(c, 'Elements').map(E => ({
            type: one(E, 'TypeCode')?.textContent,
            items: kid(E, 'Items').map(I => ({ id: one(I, 'Id')?.textContent, t: htmlOf(I), m: one(I, 'MatchingType')?.textContent || undefined })),
            keys: kid(E, 'Keys').map(K => ({
              sel: kid(K, 'Selects').map(s => one(s, 'ItemId')?.textContent), txt: kid(K, 'Text').map(x => x.textContent),
              match: kid(K, 'Matchings').map(mm => [one(mm, 'SourceId')?.textContent, one(mm, 'TargetId')?.textContent]), mark: one(K, 'Mark')?.textContent,
            })),
          }));
          return { q: htmlOf(c), help: htmlOf(one(c, 'Help')) || undefined, sol: htmlOf(one(c, 'Solution')) || undefined, els };
        }).filter(Boolean),
      }));
      return { desc, tasks };
    };
    const pageTxt = t => {
      const d = new DOMParser().parseFromString(t, 'application/xml');
      const title = d.querySelector('title')?.textContent || '';
      const c = d.querySelector('content');
      d.querySelectorAll('object,figure').forEach(o => { const fl = o.getAttribute && o.getAttribute('file'); if (fl) o.replaceWith(d.createTextNode(' [изображение ' + fl + '] ')); });
      return { title, text: c ? H(new XMLSerializer().serializeToString(c)) : '' };
    };
    const vttTxt = t => t.split('\n').filter(l => l.trim() && !/-->/.test(l) && !/^WEBVTT/.test(l) && !/^\d+$/.test(l.trim())).join(' ').replace(/\s+/g, ' ');
    const byFile = {};
    for (const [f, t] of Object.entries(F)) {
      if (t.startsWith('ERR')) { byFile[f] = { error: t }; continue; }
      try {
        if (f.endsWith('specification.xml')) byFile[f] = { kind: 'test', ...specTxt(t) };
        else if (f.endsWith('structure.json')) byFile[f] = { kind: 'structure', data: simp(JSON.parse(t)) || {} };
        else if (f.endsWith('data.json')) byFile[f] = { kind: 'data', data: simp(JSON.parse(t)) };
        else if (f.endsWith('.vtt')) byFile[f] = { kind: 'subtitles', text: vttTxt(t) };
        else byFile[f] = { kind: 'page', ...pageTxt(t) };
      } catch (e) { byFile[f] = { error: String(e) }; }
    }
    const media = files => files.filter(f => /\.(png|jpe?g|gif|webp|svg|mp4|webm|m3u8|pdf)$/i.test(f) && !/\/(css|fonts?|libs?|js)\//i.test(f) && !/\.ts$/.test(f))
      .filter(f => !/thumb|icon|logo|btn|button|bg|background/i.test(f)).slice(0, 40);
    const lessons = struct.map(l => ({
      ...l, tasks: l.tasks.map(t => {
        const o = { ...t, extracted: t.files.filter(f => byFile[f]).map(f => ({ file: f, ...byFile[f] })),
          images: t.files.filter(f => /\.(png|jpe?g|gif|webp|svg)$/i.test(f)).length, n_files: t.files.length, media: media(t.files) };
        delete o.files;
        return o;
      }),
    }));
    const ds = { source: `Библиотека ЦОК (academy-content.apkpro.ru), предмет ${subj}, класс ${cls}`, saved_at: new Date().toISOString(),
      catalog: location.href, subject: subj, class: cls, lessons };
    const s = JSON.stringify(ds);
    state.result = s; state.stage = 'готово'; state.done = true;
    const errs = Object.values(byFile).filter(x => x.error).length;
    log(`готово: уроков ${lessons.length}, материалов ${lessons.reduce((a, l) => a + l.tasks.length, 0)}, текстовых файлов ${list.length}, ошибок ${errs}, ${Math.round(s.length / 1024)} КБ`);
    if (CFG.download) {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([s], { type: 'application/json' }));
      a.download = `цок_${subj}_${cls}.json`;
      document.body.appendChild(a); a.click();
    }
  } catch (e) { state.error = String(e); console.error('[ЦОК]', e); }
})();

(function(root) {
  'use strict';
  const LIMITS = {name:200, background:2000, topic:1000, glossary:6000};
  let current = {profiles:[], activeId:''};
  let initial = null;
  const listeners = new Set();
  const selectionGuards = new Set();
  function normalize(value) {
    const result = {};
    for (const [key, limit] of Object.entries(LIMITS)) {
      result[key] = typeof value?.[key] === 'string' ? value[key].trim().slice(0, limit) : '';
    }
    return result;
  }
  async function request(url, method = 'GET', data) {
    const response = await root.fetch(url, {method, headers:{'Content-Type':'application/json'},
      cache:'no-store', ...(data === undefined ? {} : {body:JSON.stringify(data)})});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || '课程配置操作失败');
    if (!Array.isArray(value.profiles)) throw new Error('课程配置格式无效');
    current = value;
    for (const listener of listeners) listener(value);
    return value;
  }
  function load() {
    initial = request('/api/courses').catch(error => { initial = null; throw error; });
    return initial;
  }
  function ready() { return initial || load(); }
  async function selectCourse(activeId) {
    for (const guard of selectionGuards) if (!guard()) return false;
    await request('/api/courses/select', 'POST', {activeId});
    return true;
  }
  function getActive() {
    const profile = current.profiles.find(p => p.id === current.activeId);
    return profile ? normalize(profile) : {};
  }
  async function websocketUrl(value) {
    await ready();
    const url = new URL(value, root.location?.href);
    if (url.protocol === 'http:') url.protocol = 'ws:';
    if (url.protocol === 'https:') url.protocol = 'wss:';
    if (!['ws:', 'wss:'].includes(url.protocol)) throw new Error('请填写有效的 WebSocket 地址');
    url.searchParams.set('course_id', current.activeId || '');
    return url.href;
  }
  function recognitionSummary(info) {
    return `本次识别：英语 · ${info.course_name || '通用'} · ${info.hotwords_enabled
      ? `已加载 ${info.hotword_count} 个课程热词` : '未启用课程热词'}${info.warning ? ' · ' + info.warning : ''}`;
  }
  function setRecognitionInfo(info) {
    if (!info?.language) return;
    for (const element of root.document.querySelectorAll('.course-asr-status')) {
      element.hidden = false; element.textContent = recognitionSummary(info);
      element.dataset.error = String(Boolean(info.warning));
    }
  }
  async function send(socket) {
    await ready();
    if (!socket || socket.readyState !== 1) return false;
    socket.send(JSON.stringify({type:'course_profile', data:getActive()}));
    return true;
  }
  function mountPicker(container, {onChange = () => {}, onManage = () => {}} = {}) {
    container.classList.add('course-picker');
    container.innerHTML = `<div class="course-picker-row">
      <label><span class="course-picker-label">课堂课程</span>
        <select aria-label="课堂课程" disabled><option>正在加载课程…</option></select>
      </label>
      <button type="button" class="course-manage" aria-label="编辑课程" title="编辑课程"><svg class="ui-icon" aria-hidden="true"><use href="/static/icons.svg#edit"/></svg></button>
      <span class="language-badge">英语 <span aria-hidden="true">→</span> 中文</span>
    </div><p class="course-picker-status" role="status" aria-live="polite" hidden></p>`;
    const select = container.querySelector('select');
    const status = container.querySelector('.course-picker-status');
    let busy = false;
    function render(value = current) {
      select.replaceChildren();
      for (const item of [{id:'', name:'通用课程'}, ...value.profiles]) {
        const option = root.document.createElement('option');
        option.value = item.id; option.textContent = item.name; select.appendChild(option);
      }
      select.value = value.activeId; select.disabled = busy;
    }
    listeners.add(render);
    container.querySelector('button').addEventListener('click', onManage);
    select.addEventListener('change', async () => {
      if (busy) return;
      const activeId = select.value;
      busy = true; select.disabled = true; status.hidden = true;
      try {
        if (await selectCourse(activeId)) {
          try { await onChange(); }
          catch { throw new Error('课程已保存，当前连接发送失败；重新连接后生效。'); }
        }
      } catch (error) {
        status.hidden = false; status.textContent = error.message || '课程切换失败，请重试。';
      } finally { busy = false; render(); }
    });
    ready().then(render).catch(error => {
      select.replaceChildren();
      const option = root.document.createElement('option'); option.textContent = '课程加载失败'; select.appendChild(option);
      select.disabled = true; status.hidden = false; status.textContent = error.message;
    });
  }
  function mount(container, {onChange = () => {}, expanded = false} = {}) {
    container.classList.add('course-profiles');
    container.innerHTML = `<details ${expanded ? 'open' : ''}>
      <summary>课程配置<span class="course-active"></span></summary>
      <div class="course-body">
        <label>选择课程<select aria-label="选择课程" data-course-select></select></label>
        <label>课程名称<input data-field="name" maxlength="200" placeholder="例如：机器学习" autocomplete="off"></label>
        <label>课程背景<textarea data-field="background" maxlength="2000" placeholder="学科、学生层次、课程涵盖的内容，例如：本科机器学习，使用统计学与优化领域术语。"></textarea></label>
        <label>本节主题<textarea data-field="topic" maxlength="1000" placeholder="例如：梯度下降与反向传播"></textarea></label>
        <label>课程术语表<textarea data-field="glossary" maxlength="6000" placeholder="每行一组，例如：&#10;gradient descent = 梯度下降&#10;backpropagation = 反向传播"></textarea></label>
        <div class="course-actions"><button type="button" data-save class="course-save">保存并使用</button><button type="button" data-new>新建课程</button><button type="button" data-delete>删除</button><button type="button" data-reload>重新加载</button></div>
        <p class="course-status" role="status" aria-live="polite"></p>
        <p class="course-asr-status course-status" role="status" aria-live="polite" hidden></p>
        <p class="course-note">配置保存在项目中，重启或换浏览器后仍可加载。课程术语用于英语识别和翻译。录音中修改或切换课程：翻译从后续请求生效，识别热词在下次开始识别时生效。已有字幕保持原样。</p>
      </div></details>`;
    const select = container.querySelector('[data-course-select]');
    const inputs = Object.fromEntries(Object.keys(LIMITS).map(key => [key, container.querySelector(`[data-field="${key}"]`)]));
    const status = container.querySelector('.course-status');
    const active = container.querySelector('.course-active');
    const deleteButton = container.querySelector('[data-delete]');
    const reloadButton = container.querySelector('[data-reload]');
    let state = current;
    let selectedId = '';
    let editingNew = false;
    let dirty = false;
    let busy = false;
    let available = false;
    function message(text, error = false) { status.textContent = text; status.dataset.error = String(error); }
    function read() { return normalize(Object.fromEntries(Object.entries(inputs).map(([key, input]) => [key, input.value]))); }
    function controls() {
      for (const input of [...Object.values(inputs), select, ...container.querySelectorAll('button')]) input.disabled = busy || !available;
      reloadButton.disabled = busy;
      deleteButton.disabled = busy || !available || !selectedId;
    }
    function render() {
      select.replaceChildren();
      for (const item of [{id:'', name:'通用课程（不使用课程背景与热词）'}, ...state.profiles]) {
        const option = root.document.createElement('option');
        option.value = item.id; option.textContent = item.name; select.appendChild(option);
      }
      if (editingNew) {
        const option = root.document.createElement('option');
        option.value = '__new__'; option.textContent = '新课程（未保存）'; select.appendChild(option);
      }
      select.value = editingNew ? '__new__' : selectedId;
      const profile = state.profiles.find(p => p.id === selectedId) || {};
      for (const [key, input] of Object.entries(inputs)) input.value = profile[key] || '';
      active.textContent = `当前：${state.profiles.find(p => p.id === state.activeId)?.name || '通用课程'}`;
      dirty = false; controls();
    }
    function discard() { return !dirty || root.confirm('尚有未保存的课程修改，是否放弃这些修改？'); }
    selectionGuards.add(() => !busy && discard());
    listeners.add(value => {
      if (busy) return;
      state = value; available = true; selectedId = value.activeId; editingNew = false; render();
    });
    async function change(operation, success = '已保存到项目，将用于后续翻译。') {
      if (busy) return;
      busy = true; controls(); message('正在保存…');
      try {
        state = await operation(); selectedId = state.activeId; editingNew = false; render();
        try { await onChange(); message(success); }
        catch { message('配置已保存；当前连接发送失败，重新连接后自动生效。', true); }
      } catch (error) { select.value = editingNew ? '__new__' : selectedId; message(error.message || '保存失败，修改内容仍保留。', true); }
      finally { busy = false; controls(); }
    }
    async function reload() {
      if (busy || !discard()) return;
      busy = true; controls(); message('正在加载项目中的课程配置…');
      try {
        state = await load(); available = true; selectedId = state.activeId; editingNew = false; render();
        await onChange(); message('已加载项目配置。选择已有课程，或输入信息后保存。');
      } catch (error) { message(error.message || '加载失败，请稍后重新加载。', true); }
      finally { busy = false; controls(); }
    }
    for (const input of Object.values(inputs)) input.addEventListener('input', () => {
      dirty = true; message('有未保存的修改；点击「保存并使用」后生效。');
    });
    select.addEventListener('change', () => {
      if (!discard()) { select.value = editingNew ? '__new__' : selectedId; return; }
      const activeId = select.value;
      if (activeId === '__new__') return;
      change(() => request('/api/courses/select', 'POST', {activeId}), '已切换课程，将用于后续翻译。');
    });
    container.querySelector('[data-new]').addEventListener('click', () => {
      if (!discard()) return;
      selectedId = ''; editingNew = true; render(); inputs.name.focus(); message('填写新课程后点击「保存并使用」；当前生效课程暂不改变。');
    });
    container.querySelector('[data-save]').addEventListener('click', () => {
      const profile = read();
      if (!profile.name) { message('请填写课程名称。', true); inputs.name.focus(); return; }
      change(() => selectedId
        ? request(`/api/courses/${encodeURIComponent(selectedId)}`, 'PUT', profile)
        : request('/api/courses', 'POST', profile));
    });
    deleteButton.addEventListener('click', () => {
      const profile = state.profiles.find(p => p.id === selectedId);
      if (!profile || !root.confirm(`删除课程「${profile.name}」及其配置？`)) return;
      change(() => request(`/api/courses/${encodeURIComponent(profile.id)}`, 'DELETE'), '课程已删除。');
    });
    reloadButton.addEventListener('click', reload);
    reload();
  }
  root.CourseProfiles = {normalize, load, ready, getActive, selectCourse, websocketUrl,
    recognitionSummary, setRecognitionInfo, send, mount, mountPicker};
})(globalThis);

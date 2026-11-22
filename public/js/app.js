import { Display } from './display.js';

const element = (id) => document.getElementById(id);
const display = new Display({
  optics: element('optics'), character: element('character'),
  video: element('clip'), form: element('optics-form'),
});
const chat = element('chat');
const chatStatus = element('chat-status');
const chatInput = element('say');
const memoryDialog = element('memory-dialog');
const growthDialog = element('growth-dialog');
const opticsDialog = element('optics-dialog');
const turns = { center: '居中', left: '向左', right: '向右' };
const cachedMetrics = new WeakMap();
let state;
let renderedScene = '';
let renderedMenuRevision = -1;
let pendingKeys = null;
let submittingChat = false;
let lastReplySource = '';
let memoryType = 'facts';
let searchTimer;
let historyRequest = 0;

async function request(path, { body, timeout = 2400 } = {}) {
  const options = { signal: AbortSignal.timeout(timeout) };
  if (body) {
    options.method = 'POST';
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '本地服务暂不可用');
  return data;
}

function clock(seconds) {
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

function metrics(container, values) {
  const signature = JSON.stringify(values);
  if (cachedMetrics.get(container) === signature) return;
  cachedMetrics.set(container, signature);
  container.replaceChildren(...values.map(({ label, value, target }) => {
    const metric = document.createElement('div');
    metric.className = 'metric';
    const caption = metric.appendChild(document.createElement('span'));
    const number = document.createElement('strong');
    number.textContent = target ? `${Math.min(value, target)}/${target}` : value;
    caption.append(label, number);
    const bar = metric.appendChild(document.createElement('progress'));
    bar.setAttribute('aria-label', label);
    bar.max = target || 100;
    bar.value = Math.min(value, bar.max);
    return metric;
  }));
}

function render(next) {
  if (state && next.serverTime < state.serverTime) return;
  state = next;

  const sceneKey = [next.scene, next.direction, next.scene === 'look' ? next.hardware.event : 0].join('/');
  if (sceneKey !== renderedScene) {
    renderedScene = sceneKey;
    display.show(next.scene, next.direction);
  }
  element('mood').textContent = next.mood;
  const menu = next.menu;
  const line = menu.prompt || next.message;
  element('stage-bubble').hidden = !line;
  const message = element('stage-message');
  if (message.textContent !== line) message.textContent = line;
  if (renderedMenuRevision !== menu.revision) {
    renderedMenuRevision = menu.revision;
    const choices = element('stage-choices');
    choices.hidden = !menu.choices.length;
    element('optics').classList.toggle('choosing', !choices.hidden);
    choices.replaceChildren(...menu.choices.map((label, index) => {
      const choice = document.createElement('li');
      choice.textContent = label;
      if (index === menu.selected) choice.setAttribute('aria-current', 'true');
      return choice;
    }));
  }

  element('open-growth').textContent = `${next.growth.stage} · ${next.personality}`;
  element('bond').textContent = next.bond;
  element('growth').value = next.growth.percent;
  const xp = next.growth.next ? `${next.growth.xp} / ${next.growth.next} 经验` : `${next.growth.xp} 经验 · 已成熟`;
  element('growth').title = xp;
  element('growth-summary').textContent = `${next.growth.stage} · ${xp}`;
  metrics(element('needs'), next.needs);
  metrics(element('traits'), next.traits);
  metrics(element('today'), next.today);

  const focus = next.focus;
  const focusClock = element('focus-clock');
  focusClock.hidden = !focus.phase;
  focusClock.textContent = focus.phase ? `${focus.phase === 'work' ? '自习' : '休息'} ${clock(focus.remaining)}` : '';
  element('focus-minutes').disabled = Boolean(focus.phase);
  element('focus').setAttribute('aria-pressed', String(Boolean(focus.phase)));
  element('focus-label').textContent = focus.phase ? '结束计时' : '一起自习';
  element('sleep-label').textContent = next.mode === 'sleep' ? '叫醒它' : '睡觉';

  const { zone, target, mode, connected, error } = next.hardware;
  const simulated = mode === 'sim';
  element('simulate').hidden = !simulated;
  element('connection').textContent = simulated ? '模拟输入' : connected ? 'ESP32 已连接' : error || '等待 ESP32';
  element('left-light').classList.toggle('on', zone === 'left' || zone === 'both');
  element('right-light').classList.toggle('on', zone === 'right' || zone === 'both');
  element('target').textContent = `目标：${turns[target] || '—'}`;
  if (!submittingChat && !lastReplySource) {
    chatStatus.textContent = next.aiAvailable ? '模型已配置' : '本地对话';
  }
}

function inform(text) {
  element('stage-message').textContent = text;
  element('stage-bubble').hidden = false;
}

function pressKey(key) {
  if (!state) return;
  // Rapid presses from this window follow its last confirmed selection.
  const previous = pendingKeys || Promise.resolve(state.menu.revision);
  const next = previous.then(async (revision) => {
    if (revision === null) return null;
    const result = await request('/api/button', { body: { key, revision } });
    render(result.state);
    return result.accepted ? result.state.menu.revision : null;
  }).catch(() => {
    inform('这次按键没有收到回复，请再试一次。');
    return null;
  });
  pendingKeys = next;
  next.then(() => { if (pendingKeys === next) pendingKeys = null; });
}

async function poll() {
  try {
    render(await request('/api/state'));
  } catch {
    element('connection').textContent = '本地服务未连接';
    if (!state) inform('正在等待本地服务');
  } finally {
    setTimeout(poll, state?.hardware.mode === 'serial' ? 250 : 450);
  }
}

async function action(name) {
  try {
    const minutes = Number(element('focus-minutes').value);
    const result = await request('/api/interact', { body: { action: name, minutes } });
    render(result.state);
  } catch {
    inform('互动暂未完成。请检查本地服务。');
  }
}

async function gesture(zone) {
  if (state?.hardware.mode !== 'sim') return;
  try {
    const result = await request('/api/gesture', { body: { zone } });
    render(result.state);
  } catch {
    inform('模拟输入暂不可用。');
  }
}

chat.addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = chatInput.value.trim();
  if (!text || submittingChat) return;
  submittingChat = true;
  element('send').disabled = true;
  chatStatus.textContent = '光崽正在想…';
  chatInput.value = '';
  try {
    const result = await request('/api/chat', { body: { text }, timeout: 12000 });
    render(result.state);
    lastReplySource = result.source;
    chatStatus.textContent = result.source === 'ai' ? '模型对话' : '本地对话';
    element('chat-reply').textContent = result.reply;
    if (memoryDialog.open) loadHistory();
  } catch (error) {
    if (!chatInput.value) chatInput.value = text;
    chatStatus.textContent = error.name === 'TimeoutError' ? '回复超时，请稍后再试' : '对话暂不可用';
  } finally {
    submittingChat = false;
    element('send').disabled = false;
  }
});

function historyRow(item) {
  const row = document.createElement('li');
  const meta = row.appendChild(document.createElement('time'));
  if (memoryType === 'diary') {
    meta.textContent = item.day;
  } else {
    const date = new Date((item.updated_at || item.time) * 1000);
    meta.dateTime = date.toISOString();
    const formatted = date.toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' });
    meta.textContent = `${formatted} · ${item.label}${item.current === 0 ? ' · 已更新' : ''}`;
  }
  row.append(item.display || item.text);
  if (item.data?.reply) row.appendChild(document.createElement('p')).textContent = `光崽 · ${item.data.reply}`;
  return row;
}

async function loadHistory() {
  const id = ++historyRequest;
  const query = element('memory-search').value.trim();
  const params = new URLSearchParams({ type: memoryType, q: query });
  if (element('include-past').checked) params.set('past', '1');
  const list = element('memory-list');
  try {
    const { items } = await request(`/api/history?${params}`);
    if (historyRequest !== id) return;
    list.replaceChildren(...items.map(historyRow));
    if (!items.length) {
      list.appendChild(document.createElement('li')).textContent =
        query ? '没有找到相关记录。' : '还没有记录。试着告诉光崽一件你喜欢的事。';
    }
  } catch {
    if (historyRequest === id) {
      list.replaceChildren(document.createElement('li'));
      list.firstChild.textContent = '暂时读不到记忆簿。';
    }
  }
}

element('open-memory').addEventListener('click', () => {
  memoryDialog.showModal();
  loadHistory();
});
element('memory-search').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadHistory, 160);
});
element('include-past').addEventListener('change', loadHistory);
const tabs = document.querySelectorAll('[data-memory]');
for (const tab of tabs) {
  tab.addEventListener('click', () => {
    memoryType = tab.dataset.memory;
    element('old-facts').hidden = memoryType !== 'facts';
    element('memory-search').value = '';
    for (const entry of tabs) entry.setAttribute('aria-pressed', String(entry === tab));
    loadHistory();
  });
}
element('export-memory').addEventListener('click', async () => {
  const button = element('export-memory');
  button.disabled = true;
  try {
    const data = await request('/api/export', { timeout: 6000 });
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
    const download = document.createElement('a');
    download.href = url;
    download.download = 'guangzai-memory.json';
    download.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch {
    button.textContent = '重试导出';
  } finally {
    button.disabled = false;
  }
});
element('open-growth').addEventListener('click', () => growthDialog.showModal());
element('open-optics').addEventListener('click', () => opticsDialog.showModal());

function toggleProjection() {
  document.activeElement?.blur();
  document.documentElement.classList.toggle('projection');
}

async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch {
    inform('浏览器全屏可用 F11。');
  }
}
element('fullscreen').addEventListener('click', toggleFullscreen);
element('projection').addEventListener('click', async () => {
  toggleProjection();
  if (!document.fullscreenElement) await toggleFullscreen();
});
element('stage').addEventListener('dblclick', toggleProjection);
for (const button of document.querySelectorAll('[data-action]')) {
  button.addEventListener('click', () => action(button.dataset.action));
}
for (const button of document.querySelectorAll('[data-zone]')) {
  button.addEventListener('click', () => gesture(button.dataset.zone));
}
for (const button of document.querySelectorAll('[data-key]')) {
  button.addEventListener('click', () => pressKey(button.dataset.key));
}
document.addEventListener('keydown', (event) => {
  if (event.isComposing || event.altKey || event.ctrlKey || event.metaKey ||
      event.target.closest?.('input, select, textarea, dialog, [contenteditable]')) return;
  if (event.key === 'Enter' && !state?.menu.choices.length &&
      event.target.closest?.('button:not([data-key]), a')) return;
  const handlers = {
    g: toggleProjection, '1': () => action('pat'), '2': () => action('feed'),
    '3': () => action('play'), '4': () => action('sleep'), '5': () => action('focus'),
    arrowleft: () => pressKey('left'), arrowright: () => pressKey('right'), enter: () => pressKey('center'),
    a: () => gesture('left'), arrowdown: () => gesture('both'), d: () => gesture('right'),
  };
  const handle = handlers[event.key.toLowerCase()];
  if (handle) {
    event.preventDefault();
    if (!event.repeat) handle();
  }
});

display.show('idle');
display.loadClips();
poll();

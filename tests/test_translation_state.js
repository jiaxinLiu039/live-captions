const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
require('../static/translation-state.js');

async function main() {
  for (const file of ['index.html', 'listen.html']) {
    const html = fs.readFileSync(path.join(__dirname, '../static', file), 'utf8');
    assert.ok(html.includes('<script src="/static/translation-state.js"></script>'));
    assert.ok(html.includes('<script src="/static/course-profiles.js"></script>'));
    for (const match of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) {
      new vm.Script(match[1], {filename: file});
    }
  }
  const state = {en: '', zh: '', final: false};
  CaptionState.result(state, {en: 'new', en_final: true, version: 3});
  CaptionState.translation(state, {version: 3, accumulated: '新译文', final: true});
  assert.equal(CaptionState.translation(state, {version: 2, accumulated: '旧译文'}), false);
  assert.equal(CaptionState.result(state, {version: 2, en: 'old'}), false);
  assert.equal(CaptionState.translation(state, {version: 3, accumulated: '晚到的草稿'}), false);
  assert.equal(state.zh, '新译文');
  const ordered = [{sessionOrder: 1, order: [2, 0]}, {sessionOrder: 1, order: [1, 1]},
    {sessionOrder: 1, order: [1, 0]}, {sessionOrder: 2, order: [1, 0]}];
  ordered.sort((a,b) => CaptionState.compare(a,b));
  assert.deepEqual(ordered.map(item => [item.sessionOrder, ...item.order]),
    [[1,1,0], [1,1,1], [1,2,0], [2,1,0]]);
  CaptionState.result(state, {version: 4, en: 'corrected', en_final: true});
  CaptionState.translation(state, {version: 4, state: 'pending', accumulated: ''});
  assert.equal(state.final, false);
  assert.equal(state.zh, '新译文');
  CaptionState.translation(state, {type: 'translation_error', version: 4, msg: '失败'});
  assert.equal(state.translationError, '失败');
  assert.equal(state.zh, '新译文');

  class Socket extends EventTarget {
    readyState = 1;
    sent = [];
    send(data) { this.sent.push(data); }
    close() { this.readyState = 3; this.dispatchEvent(new Event('close')); }
    receive(message) {
      const data = JSON.stringify(message);
      this.onmessage?.({data});
      this.dispatchEvent(new MessageEvent('message', {data}));
    }
  }
  const socket = new Socket();
  const awaitingConfig = CaptionState.waitConfig(socket, 100);
  const messages = [];
  CaptionState.bindSocket(socket, message => messages.push(message), () => true);
  socket.receive({type: 'config', session_id: 'session-A', stop_timeout_ms: 100});
  assert.equal((await awaitingConfig).session_id, 'session-A');
  assert.equal((await CaptionState.waitConfig(socket)).session_id, 'session-A');
  const pending = CaptionState.drainSocket(socket);
  socket.receive({type: 'translation_stream', line_id: 0, version: 1, accumulated: '最后一句', final: true});
  assert.equal(socket.readyState, 1);
  socket.receive({type: 'ready_to_stop', complete: true});
  assert.equal((await pending).complete, true);
  assert.equal(messages[1].line_id, 'session-A:0');
  assert.equal(socket.sent[0].byteLength, 0);

  const timed = new Socket();
  timed.captionStopTimeout = 5;
  assert.equal((await CaptionState.drainSocket(timed)).reason, '等待最后译文超时');
  const disconnected = new Socket();
  const draining = CaptionState.drainSocket(disconnected);
  disconnected.close();
  assert.equal((await draining).reason, '连接提前断开');
  const stale = new Socket();
  CaptionState.bindSocket(stale, () => { throw new Error('stale socket delivered'); }, () => false);
  stale.receive({type: 'result', sentence_id: 0});
  const failedStart = new Socket();
  const failedConfig = CaptionState.waitConfig(failedStart, 100);
  failedStart.receive({type:'error', recoverable:false, msg:'课程不存在'});
  await assert.rejects(failedConfig, /课程不存在/);
  await assert.rejects(CaptionState.waitConfig(new Socket(), 5), /启动超时/);

  // Exercise the actual page's asynchronous stop/new-session functions without
  // media devices or a browser: saving and clearing must happen after the ack.
  const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
  const stopFunction = html.slice(html.indexOf('async function stop(){'), html.indexOf("toggleBtn.addEventListener('click'"));
  const newSessionFunction = html.slice(html.indexOf('async function newSession(){'), html.indexOf('// ===== Sidebar ====='));
  const stoppingSocket = new Socket();
  const saved = [];
  const captions = new Map([['last', {en: 'Last', zh: '草稿', final: false}]]);
  const context = vm.createContext({CaptionState, ws: stoppingSocket, stopping: false,
    stopCompletion: null, intentionalStop: false, recording: true, paused: false,
    toggleBtn: {disabled: false, classList: {remove() {}}}, btnText: {},
    pauseBtn: {style: {}, classList: {remove() {}}}, WF: {stop() {}},
    stopElapsed() {}, releaseWakeLock() {}, clearTimeout, silenceTimer: null,
    workletNode: null, audioCtx: null, mediaStream: null, sentences: captions,
    setStatus() {}, currentSessionId: null, mainTitle: {}, configReadyResolve: null,
    getSessionTitle() { return 'Saved'; }, lastRenderSig: '', render() {}, renderSidebar() {},
    saveCurrentSession() { saved.push([...captions.values()].map(item => ({...item}))); }
  });
  vm.runInContext(stopFunction + newSessionFunction, context);
  CaptionState.bindSocket(stoppingSocket, message => {
    if (message.type === 'translation_stream') CaptionState.translation(captions.get('last'), message);
  }, () => true);
  const stopped = context.stop();
  const newSession = context.newSession();
  assert.equal(saved.length, 0);
  assert.equal(captions.size, 1);
  stoppingSocket.receive({type: 'translation_stream', version: 1, accumulated: '最终译文', final: true});
  stoppingSocket.receive({type: 'ready_to_stop', complete: true});
  await Promise.all([stopped, newSession]);
  assert.equal(saved.length, 1);
  assert.equal(saved[0][0].zh, '最终译文');
  assert.equal(saved[0][0].final, true);
  assert.equal(captions.size, 0);
  assert.equal(context.toggleBtn.disabled, false);
  console.log('Frontend version, error, session, and stop-handshake checks passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });

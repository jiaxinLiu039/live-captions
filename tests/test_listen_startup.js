const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function page() {
  const nodes = new Map();
  const sockets = [];
  const worklets = [];
  const courseSends = [];
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {
      textContent:'', src:'', disabled:false, style:{}, value:'both',
      classList:{add() {}, remove() {}}, addEventListener() {}, setAttribute() {},
      pause() { this.pauses = (this.pauses || 0) + 1; },
      play() { this.plays = (this.plays || 0) + 1; return Promise.resolve(); },
    });
    return nodes.get(id);
  };
  class Socket extends EventTarget {
    static OPEN = 1;
    constructor(url) { super(); this.url = url; this.readyState = 1; this.sent = []; sockets.push(this); }
    receive(message) {
      const event = new Event('message');
      event.data = JSON.stringify(message);
      this.onmessage?.(event);
      this.dispatchEvent(event);
    }
    send(data) {
      this.sent.push(data);
      if (data instanceof ArrayBuffer && data.byteLength === 0) {
        queueMicrotask(() => this.receive({type:'ready_to_stop', complete:true}));
      }
    }
    close() { this.readyState = 3; this.onclose?.(); this.dispatchEvent(new Event('close')); }
  }
  class AudioContext {
    constructor() { this.audioWorklet = {async addModule() {}}; this.destination = {}; }
    createMediaElementSource() { return {connect() {}, disconnect() {}}; }
    async close() { this.closed = true; }
  }
  class AudioWorkletNode {
    constructor() { this.port = {}; worklets.push(this); }
    connect() {}
    disconnect() {}
  }
  const context = vm.createContext({
    document:{getElementById:node, querySelectorAll:() => [], documentElement:{setAttribute() {}}},
    localStorage:{getItem:() => null, setItem() {}},
    location:{protocol:'http:', host:'localhost'}, window:{},
    CourseProfiles:{mount() {}, setRecognitionInfo() {},
      async websocketUrl(url) { return url + '&course_id=optimization'; },
      async send(socket) { courseSends.push(socket); }},
    WebSocket:Socket, AudioContext, AudioWorkletNode,
    ArrayBuffer, setTimeout, clearTimeout,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/translation-state.js'), 'utf8'), context);
  const html = fs.readFileSync(path.join(__dirname, '../static/listen.html'), 'utf8');
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)];
  vm.runInContext(scripts.at(-1)[1], context);
  node('videoPlayer').src = 'blob:test-video';
  return {context, node, sockets, worklets, courseSends};
}

const tick = () => new Promise(resolve => setImmediate(resolve));

async function main() {
  const p = page();
  const started = vm.runInContext('startRecognition()', p.context);
  await tick();
  assert.equal(p.node('videoPlayer').pauses, 1);
  assert.equal(p.node('videoPlayer').plays || 0, 0, 'Video must wait for ASR configuration');
  assert.equal(p.node('startBtn').disabled, true);
  assert.match(p.sockets[0].url, /course_id=optimization/);
  const pcm = new ArrayBuffer(320);
  p.worklets[0].port.onmessage({data:{type:'pcm', pcm16:pcm}});
  assert.equal(p.sockets[0].sent.length, 0, 'Audio must wait for ASR configuration');
  p.sockets[0].receive({type:'config', session_id:'test', asr:{language:'en'}});
  await started;
  assert.equal(p.courseSends.length, 1);
  assert.equal(p.node('videoPlayer').plays, 1);
  assert.equal(p.node('startBtn').disabled, false);
  p.worklets[0].port.onmessage({data:{type:'pcm', pcm16:pcm}});
  assert.equal(p.sockets[0].sent[0], pcm);
  await vm.runInContext('stopRecognition()', p.context);

  const failed = page();
  const failedStart = vm.runInContext('startRecognition()', failed.context);
  await tick();
  failed.sockets[0].receive({type:'error', recoverable:false, msg:'词表或识别启动错误'});
  await failedStart;
  assert.equal(failed.node('videoPlayer').plays || 0, 0);
  assert.equal(failed.node('startBtn').disabled, false);
  assert.equal(failed.sockets[0].readyState, 3);
  assert.match(failed.node('statusText').textContent, /启动失败/);
  console.log('Listen startup waits for ASR before playback/audio, and recovers after failure.');
}

main().catch(error => { console.error(error); process.exitCode = 1; });

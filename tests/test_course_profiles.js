const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
require('../static/course-profiles.js');

async function main() {
  const calls = [];
  let state = {profiles:[{id:'course-1', name:'统计学', topic:'假设检验', glossary:'significant = 显著'}], activeId:'course-1'};
  globalThis.fetch = async (url, options) => {
    calls.push({url, options});
    if (url === '/api/courses/select' && options.method === 'POST') {
      state = {...state, activeId:JSON.parse(options.body).activeId};
    }
    return {ok:true, async json() { return state; }};
  };
  await CourseProfiles.load();
  assert.equal(CourseProfiles.getActive().name, '统计学');
  const url = new URL(await CourseProfiles.websocketUrl('ws://localhost/ws?mode=full&token=test-token'));
  assert.equal(url.searchParams.get('course_id'), 'course-1');
  assert.equal(url.searchParams.get('token'), 'test-token');
  assert.equal(url.searchParams.get('mode'), 'full');
  const socket = {readyState:1, sent:[], send(value) { this.sent.push(JSON.parse(value)); }};
  await CourseProfiles.send(socket);
  assert.equal(socket.sent[0].type, 'course_profile');
  assert.equal(socket.sent[0].data.glossary, 'significant = 显著');
  assert.equal('id' in socket.sent[0].data, false);
  state = {...state, activeId:''};
  await CourseProfiles.load();
  await CourseProfiles.send(socket);
  assert.deepEqual(socket.sent[1].data, {});
  assert.equal(new URL(await CourseProfiles.websocketUrl('ws://localhost/ws')).searchParams.get('course_id'), '');
  assert.match(CourseProfiles.recognitionSummary({hotwords_enabled:true, hotword_count:50, course_name:'最优化理论'}), /50 个课程热词/);
  assert.match(CourseProfiles.recognitionSummary({warning:'词表不可用'}), /词表不可用/);
  assert.equal(await CourseProfiles.send({readyState:3}), false);
  assert.equal(calls[0].url, '/api/courses');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(await CourseProfiles.selectCourse('course-1'), true);
  assert.equal(CourseProfiles.getActive().name, '统计学');
  assert.deepEqual(JSON.parse(calls.at(-1).options.body), {activeId:'course-1'});
  assert.equal(await CourseProfiles.selectCourse(''), true);
  assert.deepEqual(CourseProfiles.getActive(), {});
  globalThis.fetch = async () => ({ok:false, async json() { return {error:'配置文件不可读'}; }});
  await assert.rejects(CourseProfiles.selectCourse('course-1'), /配置文件不可读/);
  assert.deepEqual(CourseProfiles.getActive(), {});
  await assert.rejects(CourseProfiles.load(), /配置文件不可读/);
  assert.equal(CourseProfiles.normalize({name:42, topic:'  优化  '}).topic, '优化');
  assert.equal(CourseProfiles.normalize({name:'x'.repeat(201)}).name.length, 200);
  await testPickerAndEditor();
  console.log('Course configuration loading, selection, and WebSocket checks passed.');
}

async function testPickerAndEditor() {
  class Element {
    constructor() { this.value=''; this.children=[]; this.listeners={}; this.classList={add() {}}; this.dataset={}; }
    addEventListener(type, callback) { this.listeners[type]=callback; }
    replaceChildren() { this.children=[]; }
    appendChild(child) { this.children.push(child); }
    focus() {}
    async fire(type) { return this.listeners[type]?.(); }
  }
  const container = () => {
    const root = new Element(); const fields=new Map();
    root.querySelector = selector => { if(!fields.has(selector))fields.set(selector,new Element());return fields.get(selector); };
    root.querySelectorAll = () => [];
    return root;
  };
  let state={profiles:[{id:'course-1',name:'统计学',topic:'假设检验'}],activeId:'course-1'};
  let allowDiscard=false; let failRequest=false; let posts=0;
  const context=vm.createContext({document:{createElement:()=>new Element()},confirm:()=>allowDiscard,
    fetch:async (url,options)=>{
      if(failRequest)return {ok:false,json:async()=>({error:'写入失败'})};
      if(options.method==='POST'){posts++;state={...state,activeId:JSON.parse(options.body).activeId};}
      return {ok:true,json:async()=>state};
    }});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/course-profiles.js'),'utf8'),context);
  const editor=container(); const picker=container(); let sent=0;
  context.CourseProfiles.mount(editor);
  context.CourseProfiles.mountPicker(picker,{onChange:()=>sent++});
  await new Promise(resolve=>setImmediate(resolve));
  const quickSelect=picker.querySelector('select'); const topic=editor.querySelector('[data-field="topic"]');
  assert.equal(quickSelect.value,'course-1');
  topic.value='未保存的课堂主题'; await topic.fire('input');
  quickSelect.value=''; await quickSelect.fire('change');
  assert.equal(posts,0,'Declining discard must not save a new selection');
  assert.equal(quickSelect.value,'course-1');
  assert.equal(topic.value,'未保存的课堂主题');
  allowDiscard=true;quickSelect.value='';await quickSelect.fire('change');
  assert.equal(posts,1);assert.equal(sent,1);
  assert.equal(editor.querySelector('[data-course-select]').value,'');
  assert.equal(topic.value,'');
  failRequest=true;quickSelect.value='course-1';await quickSelect.fire('change');
  assert.equal(quickSelect.value,'');
  assert.equal(picker.querySelector('.course-picker-status').textContent,'写入失败');
  assert.equal(quickSelect.disabled,false);
}
main().catch(error => { console.error(error); process.exitCode = 1; });

// Pipe to node from the repository root.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Headers {
    constructor(value) { this.values = new Map(value instanceof Headers ? value.values : Object.entries(value || {}).map(([k,v]) => [k.toLowerCase(),v])); }
    set(key,value) { this.values.set(key.toLowerCase(),value); }
    get(key) { return this.values.get(key.toLowerCase()); }
}
class FormData {
    constructor() { this.values = new Map(); }
    set(key,value) { this.values.set(key,value); }
    get(key) { return this.values.get(key); }
}
class XHR {
    constructor() { this.headers = new Headers(); }
    open() {}
    send() {}
    setRequestHeader(key,value) { this.headers.set(key,value); }
}
const requests=[],beacons=[];
const context = {
    document:{querySelector:()=>({content:'synthetic-run-token'})},
    location:{href:'http://127.0.0.1:49780/connection-test',origin:'http://127.0.0.1:49780'},
    window:{fetch:(input,options)=>{requests.push({input,options}); return Promise.resolve({ok:true});}},
    navigator:{sendBeacon:(url,data)=>{beacons.push({url,data});return true;}},
    XMLHttpRequest:XHR, URL, Headers, FormData,
};
vm.runInNewContext(fs.readFileSync('static/request_security.js','utf8'),context);
context.window.fetch('/command-line/run',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
assert.equal(requests[0].options.headers.get('X-AV-Token'),'synthetic-run-token');
assert.equal(requests[0].options.headers.get('Content-Type'),'application/json');
context.window.fetch('/scripts/saved/name',{method:'DELETE'});
assert.equal(requests[1].options.headers.get('X-AV-Token'),'synthetic-run-token');
context.window.fetch('https://external.invalid/action',{method:'POST'});
assert.equal(requests[2].options.headers,undefined);
context.window.fetch('/ping/status');
assert.equal(requests[3].options.headers,undefined);
const xhr=new XHR();xhr.open('POST','/apply');xhr.send('form');
assert.equal(xhr.headers.get('X-AV-Token'),'synthetic-run-token');
const external=new XHR();external.open('POST','https://external.invalid');external.send();
assert.equal(external.headers.get('X-AV-Token'),undefined);
context.navigator.sendBeacon('/multicast/stop');
assert.equal(beacons[0].data.get('_action_token'),'synthetic-run-token');
context.navigator.sendBeacon('https://external.invalid/action');
assert.equal(beacons[1].data,undefined);
console.log('Request security tests passed: fetch, XHR, beacon and origin scoping.');

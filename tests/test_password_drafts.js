// Isolate the shared page-preservation code, including legacy draft migration.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source=fs.readFileSync('static/app.js','utf8');
const start=source.indexOf('(function preservePageFields()');
const end=source.indexOf('\nfunction updateStaticVisibility',start);
const values=new Map([
    ['avNetworkingTools:page-fields:/connection-test',JSON.stringify({'id:connPassword':{value:'OLD_SECRET'},'id:connHost':{value:'192.0.2.1'}})],
    ['avNetworkingTools:page-fields:/scripts',JSON.stringify({'field:page:target-password:0':{value:'OTHER_SECRET'}})],
]);
const field=(id,type,value,sensitive=false)=>({id,type,value,sensitive,tagName:'INPUT',closest:()=>null});
const password=field('connPassword','password','NEW_SECRET');
const host=field('connHost','text','');
const sensitive=field('customCredential','text','EXPLICIT_SECRET',true);
const fields=[password,host,sensitive],events={};
let observedSelector;
const context={
    window:{location:{pathname:'/connection-test'},addEventListener:(event,fn)=>{events[event]=fn;}},
    document:{body:{},addEventListener(){},querySelectorAll:selector=>{
        observedSelector=selector;
        return fields.filter(f=>!(f.type==='password' && selector.includes(':not([type="password"])')) && !(f.sensitive && selector.includes(':not([data-sensitive])')));
    }},
    sessionStorage:{get length(){return values.size;},key:i=>[...values.keys()][i],getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v)},
    MutationObserver:class{observe(){}},HTMLSelectElement:class{},
};
vm.runInNewContext(source.slice(start,end),context);
assert.ok(observedSelector.includes(':not([type="password"])'));
assert.ok(observedSelector.includes(':not([data-sensitive])'));
assert.equal(password.value,'NEW_SECRET','old credentials must not be restored');
assert.equal(host.value,'192.0.2.1','ordinary drafts should still restore');
assert.ok(![...values.values()].join('').includes('OLD_SECRET'));
assert.ok(![...values.values()].join('').includes('OTHER_SECRET'));
events.pagehide();
const saved=JSON.parse(values.get('avNetworkingTools:page-fields:/connection-test'));
assert.equal(saved['id:connPassword'],undefined);
assert.equal(saved['id:customCredential'],undefined);
assert.equal(saved['id:connHost'].value,'192.0.2.1');
console.log('Password draft tests passed: exclusion, migration and ordinary field preservation.');

// Offline DOM/transport harness: pipe this file to node from the repository root.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
    constructor() {
        this.children = []; this.dataset = {}; this.listeners = {};
        this.value = ''; this.hidden = false; this.open = false; this.textContent = '';
        this.parts = {}; this.moves = 0;
    }
    set innerHTML(value) {
        this.html = value;
        this.children = Array.from({length: (value.match(/<td[> ]/g) || []).length}, () => new Element());
    }
    get innerHTML() { return this.html || ''; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    querySelector(selector) {
        if (selector === '.analyzer-empty') return this.children.find(c => c.className === 'analyzer-empty');
        return this.parts[selector] || (this.parts[selector] = new Element());
    }
    insertBefore(child, before) {
        if (child.parent) child.remove();
        const index = before ? this.children.indexOf(before) : this.children.length;
        this.children.splice(index, 0, child); child.parent = this; this.moves++;
    }
    appendChild(child) { this.insertBefore(child, null); }
    remove() {
        this.parent.children.splice(this.parent.children.indexOf(this),1); this.parent = null;
    }
}

const elements = new Map();
const el = id => { if (!elements.has(id)) elements.set(id,new Element()); return elements.get(id); };
el('streamSort').value = 'first_seen';
const windowEvents = {}, scheduled = [], beacons = [];
const stream = (id, source, first) => ({id,source,first_seen:first,last_seen:101,group:'239.1.1.1',
    udp_source_port:5004,udp_destination_port:5004,ip_protocol:17,possible_protocol:'Possible RTP',
    packets:2,mbps:.1,packets_per_second:1,average_packet_size:100,ttl:32,ttl_values:[32],
    dscp:46,dscp_class:'EF',dscp_values:[46],ecn_values:[0],dscp_history:[],rtp:[],status:'Observed'});
let snapshot = {running:true,interface:'Ethernet',ip:'10.0.0.5',message:'Capturing',active_streams:2,
    health_status:'HEALTHY',health_confidence:'observing',health_message:'No problems detected so far.',
    health_checks:{igmp:{status:'HEALTHY',summary:'Querier detected',detail:'192.168.1.1'}},actionable_findings:[],
    ptp_domains:[],ptp_sources:[],warning_count:0,elapsed_seconds:5,total_mbps:.2,warnings:[],
    igmp_counts:{},igmp_versions:[],queriers:[],membership_available:true,joined_groups:[],
    igmp_events:[],dscp_distribution:[],streams:[stream('a','10.0.0.2',100),stream('b','10.0.0.1',101)]};
const context = {
    document: {currentScript:{dataset:{statusUrl:'/status',stopUrl:'/stop',startUrl:'/start'}},getElementById:el,createElement:()=>new Element()},
    window:{addEventListener:(name, fn)=>{windowEvents[name]=fn;}},
    navigator:{sendBeacon:url=>{beacons.push(url); return true;}},
    setTimeout:fn=>{scheduled.push(fn);return scheduled.length;},clearTimeout(){},
    fetch:async ()=>({ok:true,json:async ()=>snapshot}),alert:message=>{throw new Error(message);},
};
const settle = () => new Promise(resolve=>setImmediate(resolve));
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/multicast.js','utf8'),context);
(async () => {
    await settle();
    assert.equal(el('networkHealth').textContent, 'Network Health: HEALTHY');
    assert.ok(el('healthConfidence').textContent.includes('incomplete'));
    const grouped = el('groupedStreams');
    assert.equal(grouped.children.length, 1, 'streams share a destination group');
    const group = grouped.children[0];
    assert.equal(group.open, false, 'individual source details start collapsed');
    assert.ok(group.querySelector('summary').textContent.includes('2 sources | 0.200 Mbps'));
    group.open = true; group.listeners.toggle();
    assert.ok(group.querySelector('.group-stream-details').innerHTML.includes('10.0.0.2'));
    const body = el('multicastStreams');
    assert.equal(body.children[0].dataset.streamId,'a');
    assert.equal(body.children[1].dataset.streamId,'b');
    const row = body.children[0], moves = body.moves;
    const detail = row.querySelector('details');
    detail.open = true; detail.listeners.toggle({target:detail});
    snapshot.streams[0].packets = 10;
    await scheduled.shift()();
    assert.equal(body.children[0],row);
    assert.equal(body.moves,moves,'unchanged ordering must not move DOM nodes');
    assert.equal(detail.open,true);
    assert.equal(grouped.children[0],group);
    assert.equal(group.open,true,'group remains expanded across polling');
    assert.equal(row.children[6].textContent,10);
    el('streamFilter').value = '10.0.0.1'; el('streamFilter').listeners.input();
    assert.equal(row.hidden,true);
    assert.equal(body.children[1].hidden,false);
    el('streamFilter').value = ''; el('streamSort').value = 'source'; el('streamSort').listeners.change();
    assert.equal(body.children[0].dataset.streamId,'b');
    snapshot.warnings = [{severity:'warning',message:'<img src=x onerror=bad()>'}];
    await scheduled.shift()();
    assert.ok(el('multicastWarnings').innerHTML.includes('&lt;img'));
    assert.ok(!el('multicastWarnings').innerHTML.includes('<img'));
    snapshot.warnings = [{severity:'information',message:'40 sources using CS0',sources:['10.0.0.1','<script>bad</script>']}];
    await scheduled.shift()();
    assert.ok(el('multicastWarnings').innerHTML.includes('<details><summary>Sources (2)</summary>'));
    assert.ok(el('multicastWarnings').innerHTML.includes('&lt;script&gt;'));
    assert.ok(!el('healthFindings').innerHTML.includes('CS0'), 'diagnostic information stays out of findings');
    snapshot.health_status = 'ATTENTION';
    snapshot.actionable_findings = [{level:'ATTENTION',title:'Unrequested traffic <script>',detail:'Check the switch.'}];
    await scheduled.shift()();
    assert.equal(el('networkHealth').textContent, 'Network Health: ATTENTION');
    assert.ok(el('healthFindings').innerHTML.includes('&lt;script&gt;'));
    assert.ok(!el('healthFindings').innerHTML.includes('<script>'));
    snapshot.streams.forEach(s => { s.status='Inactive'; s.active=false; });
    await scheduled.shift()();
    assert.equal(group.hidden,true);
    el('showInactiveGroups').checked=true;
    el('showInactiveGroups').listeners.change();
    assert.equal(group.hidden,false);
    const markup = el('multicastWarnings').innerHTML;
    el('multicastWarnings').innerHTML = 'expanded DOM retained';
    await scheduled.shift()();
    assert.equal(el('multicastWarnings').innerHTML, 'expanded DOM retained');
    assert.equal(el('multicastWarnings').dataset.markup, markup);
    windowEvents.pagehide();
    assert.deepEqual(beacons,['/stop']);
    console.log('Analyzer UI tests passed: health findings, grouped drill-down, inactive groups, stable rows, escaping and page-exit stop.');
})().catch(error=>{console.error(error);process.exitCode=1;});

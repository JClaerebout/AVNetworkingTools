const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const window = {};
vm.runInNewContext(fs.readFileSync('static/connection_presets.js', 'utf8'), {window});
const {suggest, suggestions} = window.AVConnectionPresets;

assert.equal(suggest('Crestron Electronics, Inc.').protocol, 'ssh');
assert.equal(suggest('Crestron Electronics, Inc.').port, '22');
assert.equal(suggest('NETGEAR').protocol, 'telnet');
assert.equal(suggest('NETGEAR').port, '23');
assert.equal(suggest('Shure Incorporated').protocol, 'tcp');
assert.equal(suggest('Shure Incorporated').port, '2202');
assert.equal(suggest('Lumens Digital Optics Inc.').protocol, 'udp');
assert.equal(suggest('Lumens Digital Optics Inc.').port, '52381');
assert.equal(suggest('Sony Corporation', 'BRAVIA-TV').port, '20060');
assert.equal(suggest('Sony Corporation', 'projector-1').port, '53595');
assert.equal(suggest('Sony Corporation', 'camera-1').port, '52381');
assert.equal(suggest('1 Beyond, Inc.').port, '5500');
assert.equal(suggestions('Kramer Electronics').length, 2);
assert.equal(suggestions('QSC, LLC', 'Q-SYS-Core').length, 2);
assert.equal(suggest('QSC, LLC', 'Q-SYS-Core').port, '1702');
assert.equal(suggestions('PTZOptics').length, 2);
assert.equal(suggest('PTZOptics').port, '5678');
assert.equal(suggestions('Biamp Systems').length, 2);
assert.ok(window.AVConnectionPresets.all().some(item => item.label === 'KNX · TCP'));
for (const unknown of ['', '-', 'Looking up...', 'Acme Shure Labs', 'Cisco Systems']) {
    assert.equal(suggest(unknown), null, `no suggestion expected for ${unknown}`);
}

console.log('Connection preset tests passed.');

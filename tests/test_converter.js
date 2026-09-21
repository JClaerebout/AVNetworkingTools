// Run by piping static/converter.js followed by this file to node.
const assert = require('node:assert/strict');
assert.deepEqual(parseBytes('Hello\\r', 'ascii'), [72, 101, 108, 108, 111, 13]);
assert.deepEqual(parseBytes('4865 6c,6C6F', 'hex'), [72, 101, 108, 108, 111]);
assert.deepEqual(parseBytes('0, 127 255', 'decimal'), [0, 127, 255]);
for (const [value, format] of [['0', 'hex'], ['GG', 'hex'], ['256', 'decimal'], ['-1', 'decimal'], ['1.5', 'decimal'], ['\u00e9', 'ascii'], ['\\x0', 'ascii']]) {
    assert.throws(() => parseBytes(value, format));
}
const allBytes = Array.from({length: 256}, (_, index) => index);
assert.deepEqual(parseBytes(formatAscii(allBytes), 'ascii'), allBytes);
assert.deepEqual(parseBytes('', 'hex'), []);
console.log('Converter tests passed.');

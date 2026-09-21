function parseBytes(value, format) {
    if (format === 'ascii') {
        const bytes = [];
        for (let i = 0; i < value.length; i++) {
            let code = value.charCodeAt(i);
            if (value[i] === '\\') {
                const escape = value[++i];
                const escapes = {n: 10, r: 13, t: 9, '\\': 92};
                if (escape === 'x' && /^[0-9a-f]{2}$/i.test(value.slice(i + 1, i + 3))) {
                    code = parseInt(value.slice(i + 1, i + 3), 16);
                    i += 2;
                } else if (Object.hasOwn(escapes, escape)) code = escapes[escape];
                else throw new Error('Use \\n, \\r, \\t, \\xNN or \\\\ for escapes.');
            } else if (code > 127) throw new Error('ASCII supports characters 0–127; use hex or decimal for other bytes.');
            bytes.push(code);
        }
        return bytes;
    }
    if (!value.trim()) return [];
    const tokens = value.trim().split(/[\s,]+/);
    if (format === 'hex') {
        if (tokens.some(token => !/^(?:[0-9a-f]{2})+$/i.test(token)))
            throw new Error('Hex needs complete byte pairs, such as 0D FF.');
        return tokens.flatMap(token => token.match(/../g).map(byte => parseInt(byte, 16)));
    }
    if (tokens.some(token => !/^\d+$/.test(token) || Number(token) > 255))
        throw new Error('Decimal bytes must be whole numbers from 0 to 255.');
    return tokens.map(Number);
}

function formatAscii(bytes) {
    return bytes.map(byte => {
        if (byte === 92) return '\\\\';
        if (byte === 10) return '\\n';
        if (byte === 13) return '\\r';
        if (byte === 9) return '\\t';
        return byte >= 32 && byte <= 126 ? String.fromCharCode(byte)
            : `\\x${byte.toString(16).padStart(2, '0').toUpperCase()}`;
    }).join('');
}

if (typeof document !== 'undefined') {
    const fields = {
        ascii: document.getElementById('converterAscii'),
        hex: document.getElementById('converterHex'),
        decimal: document.getElementById('converterDecimal'),
    };
    const status = document.getElementById('converterStatus');
    Object.entries(fields).forEach(([format, field]) => field.addEventListener('input', () => {
        try {
            const bytes = parseBytes(field.value, format);
            const values = {ascii: formatAscii(bytes), hex: bytes.map(byte => byte.toString(16).padStart(2, '0').toUpperCase()).join(' '), decimal: bytes.join(' ')};
            Object.entries(fields).forEach(([other, input]) => { if (other !== format) input.value = values[other]; });
            status.textContent = `${bytes.length} bytes`;
        } catch (error) { status.textContent = error.message; }
    }));
}
if (typeof module !== 'undefined') module.exports = {parseBytes, formatAscii};

// Add the per-run token only to same-origin actions, including legacy XHR forms.
(() => {
    const token = document.querySelector('meta[name="action-token"]').content;
    const sameOrigin = url => new URL(url, location.href).origin === location.origin;
    const unsafe = method => !['GET', 'HEAD', 'OPTIONS'].includes(String(method).toUpperCase());
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, options = {}) => {
        const url = typeof input === 'string' || input instanceof URL ? input : input.url;
        const method = options.method || input.method || 'GET';
        if (sameOrigin(url) && unsafe(method)) {
            const headers = new Headers(options.headers || input.headers);
            headers.set('X-AV-Token', token);
            options = {...options, headers};
        }
        return originalFetch(input, options);
    };
    const open = XMLHttpRequest.prototype.open;
    const send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url, ...args) {
        this.avAction = unsafe(method) && sameOrigin(url);
        return open.call(this, method, url, ...args);
    };
    XMLHttpRequest.prototype.send = function (body) {
        if (this.avAction) this.setRequestHeader('X-AV-Token', token);
        return send.call(this, body);
    };
    const beacon = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = (url, data) => {
        if (sameOrigin(url) && (data == null || data instanceof FormData)) {
            data = data || new FormData();
            data.set('_action_token', token);
        }
        return beacon(url, data);
    };
})();

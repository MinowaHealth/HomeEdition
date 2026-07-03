/**
 * Minowa.ai — Feedback Widget
 *
 * Self-contained floating button + modal that posts feedback to the backend.
 * No dependencies, no framework.  Reads config from script tag attributes:
 *
 *   data-endpoint      POST URL            (default: /api/v1/feedback)
 *   data-auth-header   "Cookie" | "Bearer" (default: Cookie)
 *   data-token-key     localStorage key    (default: provider_session)
 */
(function () {
    'use strict';

    /* ---------- config from script tag ---------- */
    var script = document.currentScript;
    var ENDPOINT   = (script && script.getAttribute('data-endpoint'))    || '/api/v1/feedback';
    var AUTH_MODE  = (script && script.getAttribute('data-auth-header')) || 'Cookie';
    var TOKEN_KEY  = (script && script.getAttribute('data-token-key'))   || 'provider_session';

    /* ---------- browser detection ---------- */
    function detectBrowser() {
        var ua = navigator.userAgent;
        var m;
        if ((m = ua.match(/Edg\/(\d+)/)))            return 'Edge ' + m[1];
        if ((m = ua.match(/OPR\/(\d+)/)))             return 'Opera ' + m[1];
        if ((m = ua.match(/Chrome\/(\d+)/)))          return 'Chrome ' + m[1];
        if ((m = ua.match(/Safari\/(\d+)/) ) && /Version\/(\d+)/.test(ua))
            return 'Safari ' + ua.match(/Version\/(\d+)/)[1];
        if ((m = ua.match(/Firefox\/(\d+)/)))         return 'Firefox ' + m[1];
        return ua.substring(0, 60);
    }

    /* ---------- category map ---------- */
    var CATEGORIES = [
        { value: 'general', label: 'General' },
        { value: 'bug',     label: 'Bug Report' },
        { value: 'feature', label: 'Feature Request' },
        { value: 'praise',  label: 'Praise' }
    ];

    /* ---------- inject styles ---------- */
    var style = document.createElement('style');
    style.textContent = [
        /* Button */
        '.hb-fb-btn{',
            'position:fixed;bottom:24px;right:24px;z-index:10000;',
            'width:48px;height:48px;border-radius:50%;border:none;cursor:pointer;',
            'background:var(--color-accent,#2b6cb0);color:#fff;',
            'box-shadow:0 2px 8px rgba(0,0,0,.25);',
            'display:flex;align-items:center;justify-content:center;',
            'transition:transform .15s ease,box-shadow .15s ease;',
        '}',
        '.hb-fb-btn:hover{transform:scale(1.08);box-shadow:0 4px 14px rgba(0,0,0,.3);}',
        '.hb-fb-btn svg{width:22px;height:22px;fill:currentColor;}',

        /* Overlay */
        '.hb-fb-overlay{',
            'position:fixed;inset:0;z-index:10001;',
            'background:rgba(0,0,0,.4);backdrop-filter:blur(2px);',
            'opacity:0;transition:opacity .2s ease;pointer-events:none;',
        '}',
        '.hb-fb-overlay.hb-fb-open{opacity:1;pointer-events:auto;}',

        /* Modal */
        '.hb-fb-modal{',
            'position:fixed;z-index:10002;',
            'top:50%;left:50%;transform:translate(-50%,-50%) scale(.95);',
            'width:420px;max-width:90vw;',
            'background:var(--color-card,#fff);color:var(--color-text,#333);',
            'border-radius:var(--border-radius-lg,8px);',
            'box-shadow:0 12px 40px rgba(0,0,0,.2);',
            'padding:var(--space-lg,1.5rem);',
            'opacity:0;transition:opacity .2s ease,transform .2s ease;pointer-events:none;',
        '}',
        '.hb-fb-modal.hb-fb-open{opacity:1;transform:translate(-50%,-50%) scale(1);pointer-events:auto;}',

        /* Heading */
        '.hb-fb-title{',
            'margin:0 0 .25rem;font-size:var(--font-size-lg,1.25rem);font-weight:600;',
            'font-family:var(--font-family,system-ui,sans-serif);',
        '}',
        '.hb-fb-subtitle{',
            'margin:0 0 var(--space-md,1rem);font-size:var(--font-size-sm,.875rem);',
            'color:var(--color-muted,#718096);',
        '}',

        /* Select */
        '.hb-fb-select{',
            'display:block;width:100%;padding:.5rem .75rem;margin-bottom:var(--space-md,1rem);',
            'border:1px solid var(--color-border,#e2e8f0);border-radius:var(--border-radius,4px);',
            'background:var(--color-card,#fff);color:var(--color-text,#333);',
            'font-size:var(--font-size-sm,.875rem);',
            'font-family:var(--font-family,system-ui,sans-serif);',
        '}',

        /* Textarea */
        '.hb-fb-textarea{',
            'display:block;width:100%;min-height:120px;padding:.5rem .75rem;',
            'border:1px solid var(--color-border,#e2e8f0);border-radius:var(--border-radius,4px);',
            'background:var(--color-card,#fff);color:var(--color-text,#333);',
            'font-size:var(--font-size-sm,.875rem);resize:vertical;',
            'font-family:var(--font-family,system-ui,sans-serif);',
            'margin-bottom:var(--space-md,1rem);',
        '}',
        '.hb-fb-textarea:focus,.hb-fb-select:focus{',
            'outline:none;border-color:var(--color-accent,#2b6cb0);',
            'box-shadow:0 0 0 2px var(--color-accent-subtle,rgba(43,108,176,.15));',
        '}',

        /* Buttons row */
        '.hb-fb-actions{display:flex;justify-content:flex-end;gap:.5rem;}',
        '.hb-fb-cancel{',
            'padding:.5rem 1rem;border:1px solid var(--color-border,#e2e8f0);',
            'border-radius:var(--border-radius,4px);background:transparent;',
            'color:var(--color-text,#333);cursor:pointer;font-size:var(--font-size-sm,.875rem);',
            'font-family:var(--font-family,system-ui,sans-serif);',
        '}',
        '.hb-fb-cancel:hover{background:var(--color-hover,#f7fafc);}',
        '.hb-fb-send{',
            'padding:.5rem 1rem;border:none;border-radius:var(--border-radius,4px);',
            'background:var(--color-accent,#2b6cb0);color:#fff;cursor:pointer;',
            'font-size:var(--font-size-sm,.875rem);font-weight:600;',
            'font-family:var(--font-family,system-ui,sans-serif);',
            'transition:background .15s ease;',
        '}',
        '.hb-fb-send:hover:not(:disabled){background:var(--color-accent-hover,#2c5282);}',
        '.hb-fb-send:disabled{opacity:.5;cursor:not-allowed;}',

        /* Toast */
        '.hb-fb-toast{',
            'position:fixed;bottom:80px;right:24px;z-index:10003;',
            'padding:.75rem 1rem;border-radius:var(--border-radius,4px);',
            'font-size:var(--font-size-sm,.875rem);color:#fff;',
            'font-family:var(--font-family,system-ui,sans-serif);',
            'box-shadow:0 4px 12px rgba(0,0,0,.2);',
            'opacity:0;transform:translateY(8px);',
            'transition:opacity .25s ease,transform .25s ease;pointer-events:none;',
        '}',
        '.hb-fb-toast.hb-fb-show{opacity:1;transform:translateY(0);}',
        '.hb-fb-toast-ok{background:var(--color-success,#276749);}',
        '.hb-fb-toast-err{background:var(--color-error,#c53030);}',

        /* box-sizing reset for widget */
        '[class^="hb-fb-"]{box-sizing:border-box;}',
    ].join('\n');
    document.head.appendChild(style);

    /* ---------- create DOM ---------- */

    // Floating button
    var btn = document.createElement('button');
    btn.className = 'hb-fb-btn';
    btn.setAttribute('aria-label', 'Send feedback');
    btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.2L4 17.2V4h16v12z"/></svg>';

    // Overlay
    var overlay = document.createElement('div');
    overlay.className = 'hb-fb-overlay';

    // Modal
    var modal = document.createElement('div');
    modal.className = 'hb-fb-modal';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-label', 'Send Feedback');

    var title = document.createElement('h2');
    title.className = 'hb-fb-title';
    title.textContent = 'Send Feedback';

    var subtitle = document.createElement('p');
    subtitle.className = 'hb-fb-subtitle';
    subtitle.textContent = "Tell us what worked, what didn\u2019t, or what you want next.";

    var select = document.createElement('select');
    select.className = 'hb-fb-select';
    select.setAttribute('aria-label', 'Feedback category');
    CATEGORIES.forEach(function (cat) {
        var opt = document.createElement('option');
        opt.value = cat.value;
        opt.textContent = cat.label;
        select.appendChild(opt);
    });

    var textarea = document.createElement('textarea');
    textarea.className = 'hb-fb-textarea';
    textarea.placeholder = 'Your feedback\u2026';
    textarea.setAttribute('aria-label', 'Feedback text');

    var actions = document.createElement('div');
    actions.className = 'hb-fb-actions';

    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'hb-fb-cancel';
    cancelBtn.textContent = 'Cancel';

    var sendBtn = document.createElement('button');
    sendBtn.className = 'hb-fb-send';
    sendBtn.textContent = 'Send';
    sendBtn.disabled = true;

    actions.appendChild(cancelBtn);
    actions.appendChild(sendBtn);

    modal.appendChild(title);
    modal.appendChild(subtitle);
    modal.appendChild(select);
    modal.appendChild(textarea);
    modal.appendChild(actions);

    // Toast
    var toast = document.createElement('div');
    toast.className = 'hb-fb-toast';

    document.body.appendChild(btn);
    document.body.appendChild(overlay);
    document.body.appendChild(modal);
    document.body.appendChild(toast);

    /* ---------- behavior ---------- */

    function openModal() {
        overlay.classList.add('hb-fb-open');
        modal.classList.add('hb-fb-open');
        textarea.focus();
    }

    function closeModal() {
        overlay.classList.remove('hb-fb-open');
        modal.classList.remove('hb-fb-open');
        textarea.value = '';
        select.value = 'general';
        sendBtn.disabled = true;
    }

    var toastTimer;
    function showToast(msg, ok) {
        clearTimeout(toastTimer);
        toast.textContent = msg;
        toast.className = 'hb-fb-toast ' + (ok ? 'hb-fb-toast-ok' : 'hb-fb-toast-err');
        // Force reflow for re-trigger animation
        void toast.offsetWidth;
        toast.classList.add('hb-fb-show');
        toastTimer = setTimeout(function () { toast.classList.remove('hb-fb-show'); }, 3000);
    }

    btn.addEventListener('click', openModal);
    overlay.addEventListener('click', closeModal);
    cancelBtn.addEventListener('click', closeModal);

    textarea.addEventListener('input', function () {
        sendBtn.disabled = !textarea.value.trim();
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && modal.classList.contains('hb-fb-open')) {
            closeModal();
        }
    });

    sendBtn.addEventListener('click', function () {
        var text = textarea.value.trim();
        if (!text) return;

        sendBtn.disabled = true;
        sendBtn.textContent = 'Sending\u2026';

        var body = {
            feedback: text,
            feedback_type: select.value,
            page: window.location.pathname + window.location.hash,
            browser: detectBrowser(),
            source_app: 'WebApp'
        };

        var headers = { 'Content-Type': 'application/json' };
        var opts = { method: 'POST', headers: headers, body: JSON.stringify(body) };

        if (AUTH_MODE === 'Bearer') {
            var token = localStorage.getItem(TOKEN_KEY);
            if (token) headers['Authorization'] = 'Bearer ' + token;
        } else {
            opts.credentials = 'same-origin';
        }

        fetch(ENDPOINT, opts)
            .then(function (resp) {
                if (!resp.ok) return resp.json().then(function (d) { throw new Error(d.error || 'Request failed'); });
                return resp.json();
            })
            .then(function () {
                closeModal();
                showToast('Thanks! Your feedback was sent.', true);
            })
            .catch(function (err) {
                showToast(err.message || 'Something went wrong.', false);
            })
            .finally(function () {
                sendBtn.textContent = 'Send';
                sendBtn.disabled = !textarea.value.trim();
            });
    });
})();

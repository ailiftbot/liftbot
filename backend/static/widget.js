(function () {
  'use strict';

  function findScript() {
    if (document.currentScript && document.currentScript.getAttribute('data-employee-token')) {
      return document.currentScript;
    }
    var scripts = document.querySelectorAll('script[data-employee-token]');
    return scripts.length ? scripts[scripts.length - 1] : null;
  }

  var script = findScript();
  var token = script && script.getAttribute('data-employee-token');
  if (!token) {
    console.warn('[LiftBot] Missing data-employee-token on embed script.');
    return;
  }

  var apiBase = (
    script.getAttribute('data-api-base') ||
    script.src.replace(/\/static\/widget\.js(\?.*)?$/, '') + '/api/widget'
  ).replace(/\/$/, '');

  var visitorKey = 'liftbot_visitor_' + token;
  var sessionKey = 'liftbot_session_' + token;
  var visitorId = localStorage.getItem(visitorKey) || (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()));
  localStorage.setItem(visitorKey, visitorId);
  var sessionId = localStorage.getItem(sessionKey) || null;

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === 'style' && typeof attrs[k] === 'object') Object.assign(node.style, attrs[k]);
        else if (k === 'text') node.textContent = attrs[k];
        else node.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) {
      if (c) node.appendChild(c);
    });
    return node;
  }

  function ready(fn) {
    if (document.body) fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  fetch(apiBase + '/config/?token=' + encodeURIComponent(token), {
    method: 'GET',
    mode: 'cors',
    credentials: 'omit',
  })
    .then(function (r) {
      if (!r.ok) throw new Error('config ' + r.status);
      return r.json();
    })
    .then(function (cfg) {
      ready(function () {
        mount(cfg);
      });
    })
    .catch(function (err) {
      console.error('[LiftBot] Failed to load employee config from', apiBase, err);
    });

  function mount(cfg) {
    var color = cfg.brand_color || '#7C3AED';
    var open = false;

    var launcher = el('button', {
      type: 'button',
      'aria-label': 'Chat with ' + cfg.name,
      style: {
        position: 'fixed',
        right: '20px',
        bottom: '20px',
        zIndex: '2147483000',
        width: '56px',
        height: '56px',
        borderRadius: '999px',
        border: '0',
        cursor: 'pointer',
        background: color,
        color: '#fff',
        boxShadow: '0 8px 24px rgba(0,0,0,.18)',
        fontSize: '13px',
        fontWeight: '700',
        letterSpacing: '0.02em',
      },
      text: (cfg.name || 'AI').slice(0, 1).toUpperCase(),
    });

    var panel = el('div', {
      style: {
        position: 'fixed',
        right: '20px',
        bottom: '88px',
        zIndex: '2147483000',
        width: '360px',
        maxWidth: 'calc(100vw - 24px)',
        height: '520px',
        background: '#fff',
        borderRadius: '18px',
        overflow: 'hidden',
        display: 'none',
        boxShadow: '0 18px 50px rgba(0,0,0,.22)',
        fontFamily: 'Plus Jakarta Sans, DM Sans, system-ui, sans-serif',
      },
    });

    var header = el(
      'div',
      {
        style: {
          background: color,
          color: '#fff',
          padding: '14px 16px',
          display: 'flex',
          gap: '10px',
          alignItems: 'center',
        },
      },
      [
        cfg.avatar_url
          ? el('img', {
              src: cfg.avatar_url,
              alt: '',
              style: { width: '40px', height: '40px', borderRadius: '999px', objectFit: 'cover' },
            })
          : null,
        el('div', {}, [
          el('div', { style: { fontWeight: '700' }, text: cfg.name }),
          el('div', { style: { fontSize: '12px', opacity: '0.9' }, text: cfg.role + " · We're online" }),
        ]),
      ]
    );

    var messages = el('div', {
      style: {
        flex: '1',
        overflowY: 'auto',
        padding: '14px',
        background: '#f7faf9',
        height: '380px',
      },
    });
    var form = el('form', {
      style: {
        display: 'flex',
        gap: '8px',
        padding: '10px',
        borderTop: '1px solid #e5e7eb',
        background: '#fff',
      },
    });
    var input = el('input', {
      type: 'text',
      placeholder: 'Message ' + cfg.name + '…',
      style: {
        flex: '1',
        border: '1px solid #d1d5db',
        borderRadius: '999px',
        padding: '10px 14px',
        outline: 'none',
      },
    });
    var send = el('button', {
      type: 'submit',
      style: {
        border: '0',
        borderRadius: '999px',
        background: color,
        color: '#fff',
        padding: '0 16px',
        cursor: 'pointer',
      },
      text: 'Send',
    });
    form.appendChild(input);
    form.appendChild(send);
    panel.appendChild(header);
    panel.appendChild(messages);
    panel.appendChild(form);
    document.body.appendChild(panel);
    document.body.appendChild(launcher);

    function bubble(text, mine) {
      var row = el('div', { style: { marginBottom: '8px', textAlign: mine ? 'right' : 'left' } });
      row.appendChild(
        el('div', {
          style: {
            display: 'inline-block',
            maxWidth: '85%',
            padding: '8px 12px',
            borderRadius: '16px',
            background: mine ? color : '#fff',
            color: mine ? '#fff' : '#0B1220',
            border: mine ? '0' : '1px solid #e5e7eb',
            fontSize: '14px',
            lineHeight: '1.4',
          },
          text: text,
        })
      );
      messages.appendChild(row);
      messages.scrollTop = messages.scrollHeight;
      return row.querySelector('div');
    }

    bubble(cfg.greeting || 'Hi, I am ' + cfg.name + '.', false);

    launcher.addEventListener('click', function () {
      open = !open;
      panel.style.display = open ? 'block' : 'none';
      if (open) input.focus();
    });

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var text = (input.value || '').trim();
      if (!text) return;
      bubble(text, true);
      input.value = '';
      var replyNode = bubble('…', false);

      fetch(apiBase + '/message/', {
        method: 'POST',
        mode: 'cors',
        credentials: 'omit',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token: token,
          message: text,
          visitor_id: visitorId,
          session_id: sessionId,
          stream: false,
        }),
      })
        .then(function (res) {
          return res.json().then(function (data) {
            if (!res.ok) throw new Error(data.error || 'bad status');
            return data;
          });
        })
        .then(function (data) {
          if (data.session_id) {
            sessionId = data.session_id;
            localStorage.setItem(sessionKey, sessionId);
          }
          replyNode.textContent = data.reply || 'No reply.';
          messages.scrollTop = messages.scrollHeight;
        })
        .catch(function (err) {
          console.error('[LiftBot] Chat failed', err);
          replyNode.textContent = 'Sorry, I could not reply right now.';
        });
    });
  }
})();

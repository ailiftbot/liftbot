(function () {
  'use strict';

  function findScript() {
    var s = document.currentScript;
    if (s && (s.getAttribute('data-employee-token') || s.getAttribute('data-workspace-token'))) return s;
    var all = document.querySelectorAll('script[data-employee-token], script[data-workspace-token]');
    return all.length ? all[all.length - 1] : null;
  }

  var script = findScript();
  if (!script) {
    console.warn('[LiftBot] Embed script not found.');
    return;
  }

  var employeeToken = script.getAttribute('data-employee-token');
  var workspaceToken = script.getAttribute('data-workspace-token');
  // Prefer data-api-base; otherwise derive from the script's own src host
  // so embeds work when loaded from http://liftbot.brandinglift.com:8001/static/widget.js
  var derivedApi = '';
  try {
    if (script.src) {
      derivedApi = new URL(script.src, window.location.href).href
        .replace(/\/static\/widget\.js(\?.*)?$/i, '')
        .replace(/\/$/, '') + '/api/widget';
    }
  } catch (e) {
    derivedApi = '';
  }
  var apiBase = (script.getAttribute('data-api-base') || derivedApi).replace(/\/$/, '');
  if (!apiBase) {
    console.warn('[LiftBot] Missing data-api-base and could not derive API URL from script src.');
  }

  var storageKey = workspaceToken
    ? 'liftbot_ws_visitor_' + workspaceToken
    : 'liftbot_visitor_' + employeeToken;
  var sessionKeyPrefix = workspaceToken || employeeToken;
  var visitorId = localStorage.getItem(storageKey) || (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()));
  localStorage.setItem(storageKey, visitorId);

  function sessionKey(token) {
    return 'liftbot_session_' + sessionKeyPrefix + '_' + token;
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === 'style' && typeof attrs[k] === 'object') Object.assign(node.style, attrs[k]);
        else if (k === 'text') node.textContent = attrs[k];
        else node.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }

  function ready(fn) {
    if (document.body) fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  function fetchJson(url, opts) {
    return fetch(url, Object.assign({ mode: 'cors', credentials: 'omit' }, opts || {})).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error((data && data.error) || ('HTTP ' + r.status));
        return data;
      });
    });
  }

  function isMobileViewport() {
    return window.matchMedia && window.matchMedia('(max-width: 640px)').matches;
  }

  function startWithEmployee(token) {
    fetchJson(apiBase + '/config/?token=' + encodeURIComponent(token) + '&visitor_id=' + encodeURIComponent(visitorId))
      .then(function (cfg) {
        cfg._token = token;
        ready(function () { mount(cfg); });
      })
      .catch(function (err) { console.error('[LiftBot] Config failed', err); });
  }

  function showRoster(roster) {
    ready(function () {
      var color = roster.brand_color || '#7C3AED';
      var panel = el('div', {
        style: {
          position: 'fixed', right: '20px', bottom: '20px', zIndex: '2147483000',
          width: '320px', maxWidth: 'calc(100vw - 24px)', background: '#fff',
          borderRadius: '16px', boxShadow: '0 18px 50px rgba(0,0,0,.22)',
          fontFamily: 'Plus Jakarta Sans, system-ui, sans-serif', overflow: 'hidden',
        },
      });
      var resizeRoster = function () {
        if (isMobileViewport()) {
          Object.assign(panel.style, {
            left: '12px',
            right: '12px',
            bottom: '12px',
            width: 'auto',
            maxWidth: 'none',
            maxHeight: '70vh',
          });
        } else {
          Object.assign(panel.style, {
            left: 'auto',
            right: '20px',
            bottom: '20px',
            width: '320px',
            maxWidth: 'calc(100vw - 24px)',
            maxHeight: 'none',
          });
        }
      };
      resizeRoster();
      window.addEventListener('resize', resizeRoster);
      panel.appendChild(el('div', {
        style: { background: color, color: '#fff', padding: '14px 16px', fontWeight: '700', fontSize: '14px' },
        text: roster.workspace + ' — Choose your AI Employee',
      }));
      var list = el('div', { style: { padding: '10px' } });
      roster.employees.forEach(function (emp) {
        var btn = el('button', {
          type: 'button',
          style: {
            display: 'block', width: '100%', textAlign: 'left', border: '1px solid #E5E7EB',
            borderRadius: '10px', padding: '10px 12px', marginBottom: '8px', cursor: 'pointer',
            background: '#fff', fontFamily: 'inherit',
          },
        });
        btn.appendChild(el('div', { style: { fontWeight: '650', fontSize: '13px' }, text: emp.name }));
        btn.appendChild(el('div', { style: { fontSize: '11px', color: '#6B7280', marginTop: '2px' }, text: emp.role }));
        btn.addEventListener('click', function () {
          window.removeEventListener('resize', resizeRoster);
          panel.remove();
          startWithEmployee(emp.token);
        });
        list.appendChild(btn);
      });
      panel.appendChild(list);
      document.body.appendChild(panel);
    });
  }

  if (workspaceToken) {
    fetchJson(apiBase + '/roster/?workspace_token=' + encodeURIComponent(workspaceToken))
      .then(function (roster) {
        if (!roster.employees || !roster.employees.length) return;
        if (roster.employees.length === 1) startWithEmployee(roster.employees[0].token);
        else showRoster(roster);
      })
      .catch(function (err) { console.error('[LiftBot] Roster failed', err); });
  } else if (employeeToken) {
    startWithEmployee(employeeToken);
  } else {
    console.warn('[LiftBot] Set data-employee-token or data-workspace-token.');
  }

  function mount(cfg) {
    var token = cfg._token;
    var color = cfg.brand_color || '#7C3AED';
    var open = false;
    var humanMode = false;
    var sessionId = localStorage.getItem(sessionKey(token)) || null;
    var lastMsgId = 0;
    var pollTimer = null;
    var bodyLockApplied = false;
    var bodyOverflow = '';
    var bodyOverscrollBehavior = '';
    var visualViewportResizeTimer = null;

    var launcher = el('button', {
      type: 'button',
      'aria-label': 'Talk to ' + cfg.name,
      style: {
        position: 'fixed', right: '20px', bottom: '20px', zIndex: '2147483001',
        width: '56px', height: '56px', borderRadius: '999px', border: '0', cursor: 'pointer',
        background: color, color: '#fff', boxShadow: '0 8px 24px rgba(0,0,0,.18)',
        fontSize: '13px', fontWeight: '700',
        /* FIX: Centering for alphabet letter */
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        lineHeight: '1',
        padding: '0',
      },
      text: (cfg.name || 'AI').slice(0, 1).toUpperCase(),
    });

    var panel = el('div', {
      style: {
        position: 'fixed', right: '20px', bottom: '88px', zIndex: '2147483000',
        width: '360px', maxWidth: 'calc(100vw - 24px)', height: '560px',
        background: '#fff', borderRadius: '18px', overflow: 'hidden', display: 'none',
        boxShadow: '0 18px 50px rgba(0,0,0,.22)',
        fontFamily: 'Plus Jakarta Sans, system-ui, sans-serif',
        flexDirection: 'column',
      },
    });

    function closePanel() {
      open = false;
      panel.style.display = 'none';
      launcher.style.display = 'flex';  // Show launcher when chat closes
      setBodyScrollLocked(false);
    }

    function setBodyScrollLocked(locked) {
      if (!document.body) return;
      if (locked && isMobileViewport()) {
        if (!bodyLockApplied) {
          bodyOverflow = document.body.style.overflow || '';
          bodyOverscrollBehavior = document.body.style.overscrollBehavior || '';
          bodyLockApplied = true;
        }
        document.body.style.overflow = 'hidden';
        document.body.style.overscrollBehavior = 'none';
      } else if (bodyLockApplied) {
        document.body.style.overflow = bodyOverflow;
        document.body.style.overscrollBehavior = bodyOverscrollBehavior;
        bodyLockApplied = false;
      }
    }

    function getViewportBox() {
      var vv = window.visualViewport;
      if (vv) {
        return {
          top: Math.max(0, vv.offsetTop || 0),
          left: Math.max(0, vv.offsetLeft || 0),
          width: Math.max(0, vv.width || window.innerWidth || 0),
          height: Math.max(0, vv.height || window.innerHeight || 0),
        };
      }
      return {
        top: 0,
        left: 0,
        width: window.innerWidth || 0,
        height: window.innerHeight || 0,
      };
    }

    function handleVisualViewportChange() {
      if (!open || !isMobileViewport()) return;
      applyResponsiveLayout();
      if (visualViewportResizeTimer) window.clearTimeout(visualViewportResizeTimer);
      // iOS animates the keyboard open/close over ~250-350ms, and reports
      // viewport size in more than one step, so we re-check a few times
      // instead of trusting a single resize/scroll event.
      visualViewportResizeTimer = window.setTimeout(applyResponsiveLayout, 80);
      window.setTimeout(applyResponsiveLayout, 250);
      window.setTimeout(applyResponsiveLayout, 400);
    }

    function applyResponsiveLayout() {
      if (isMobileViewport()) {
        // FIX: Anchored at bottom, limited height to 65vh (not full screen)
        Object.assign(panel.style, {
          position: 'fixed',
          bottom: '12px',
          top: 'auto',
          left: '12px',
          right: '12px',
          width: 'auto',
          maxWidth: 'calc(100% - 24px)',
          height: 'auto',
          maxHeight: '65vh', // Prevents covering full screen
          transform: 'none',
          borderRadius: '12px',
        });
        Object.assign(launcher.style, {
          right: '12px',
          bottom: '12px',
          width: '52px',
          height: '52px',
          zIndex: '2147483001',
        });
      } else {
        Object.assign(panel.style, {
          top: 'auto',
          right: '20px',
          bottom: '88px',
          left: 'auto',
          width: '360px',
          maxWidth: 'calc(100vw - 24px)',
          height: '560px',
          transform: 'none',
          borderRadius: '18px',
        });
        Object.assign(launcher.style, {
          right: '20px',
          bottom: '20px',
          width: '56px',
          height: '56px',
          zIndex: '2147483000',
        });
      }
      setBodyScrollLocked(open && isMobileViewport());
      if (open) {
        window.requestAnimationFrame(function () {
          messages.scrollTop = messages.scrollHeight;
        });
      }
    }

    var header = el('div', {
      style: {
        background: color, color: '#fff', padding: '14px 16px', display: 'flex', gap: '10px',
        alignItems: 'center', flexShrink: '0',
      },
    }, [
      cfg.avatar_url ? el('img', { src: cfg.avatar_url, alt: '', style: { width: '40px', height: '40px', borderRadius: '999px', objectFit: 'cover' } }) : null,
      el('div', {}, [
        el('div', { style: { fontWeight: '700' }, text: cfg.name }),
        el('div', { id: 'lb-status', style: { fontSize: '12px', opacity: '0.9' }, text: cfg.role + ' · AI Employee' }),
      ]),
      el('button', {
        type: 'button',
        'aria-label': 'Close chat',
        style: {
          marginLeft: 'auto',
          border: '0',
          background: 'rgba(255,255,255,.18)',
          color: '#fff',
          width: '30px',
          height: '30px',
          borderRadius: '999px',
          cursor: 'pointer',
          fontSize: '16px',
          lineHeight: '1',
          padding: '0',
        },
        text: '×',
      }),
    ]);
    header.lastChild.addEventListener('click', closePanel);

    var resumeBar = null;
    if (cfg.returning_visitor && cfg.resume_message) {
      resumeBar = el('div', {
        style: { padding: '10px 12px', background: '#EDE9FE', borderBottom: '1px solid #DDD6FE', fontSize: '12px', color: '#5B21B6', lineHeight: '1.4', flexShrink: '0' },
      });
      resumeBar.appendChild(document.createTextNode(cfg.resume_message));
      var resumeActions = el('div', { style: { display: 'flex', gap: '6px', marginTop: '8px' } });
      var btnContinue = el('button', { type: 'button', style: pill(color, true), text: 'Continue' });
      var btnFresh = el('button', { type: 'button', style: pill('#fff', false), text: 'Start fresh' });
      btnContinue.addEventListener('click', function () {
        sendMessage("Yes, let's continue where we left off.", true);
        resumeBar.remove();
      });
      btnFresh.addEventListener('click', function () {
        sessionId = null;
        localStorage.removeItem(sessionKey(token));
        resumeBar.remove();
        addMsg('system', 'Starting a fresh conversation.');
      });
      resumeActions.appendChild(btnContinue);
      resumeActions.appendChild(btnFresh);
      resumeBar.appendChild(resumeActions);
    }

    var messages = el('div', {
      style: {
        flex: '1', minHeight: '0', overflowY: 'auto', padding: '14px', background: '#f7faf9',
        WebkitOverflowScrolling: 'touch',
      },
    });
    var actionBar = el('div', { style: { padding: '8px 10px', borderTop: '1px solid #E5E7EB', display: 'flex', flexWrap: 'wrap', gap: '6px', background: '#fff', flexShrink: '0' } });
    var form = el('form', {
      style: {
        display: 'flex',
        gap: '8px',
        padding: '10px',
        paddingBottom: 'calc(10px + env(safe-area-inset-bottom))',
        borderTop: '1px solid #e5e7eb',
        background: '#fff',
        flexShrink: '0',
      },
    });
    var input = el('input', {
      type: 'text',
      placeholder: 'Message ' + cfg.name + '…',
      style: { flex: '1', border: '1px solid #d1d5db', borderRadius: '999px', padding: '10px 14px', outline: 'none', fontSize: '16px' },
    });
    var send = el('button', { type: 'submit', style: { border: '0', borderRadius: '999px', background: color, color: '#fff', padding: '0 16px', cursor: 'pointer', flexShrink: '0' }, text: 'Send' });
    form.appendChild(input);
    form.appendChild(send);

    panel.appendChild(header);
    if (resumeBar) panel.appendChild(resumeBar);
    panel.appendChild(messages);
    panel.appendChild(actionBar);
    panel.appendChild(form);
    document.body.appendChild(panel);
    document.body.appendChild(launcher);
    applyResponsiveLayout();
    window.addEventListener('resize', applyResponsiveLayout);
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', handleVisualViewportChange);
      window.visualViewport.addEventListener('scroll', handleVisualViewportChange);
    }

    function pill(bg, solid) {
      return {
        border: solid ? '0' : '1px solid #C4B5FD',
        borderRadius: '999px',
        background: solid ? bg : '#fff',
        color: solid ? '#fff' : '#5B21B6',
        padding: '5px 12px',
        fontSize: '11px',
        cursor: 'pointer',
        fontWeight: '600',
      };
    }

    function addMsg(kind, text) {
      var mine = kind === 'you';
      var sys = kind === 'system';
      var human = kind === 'human';
      var row = el('div', { style: { marginBottom: '8px', textAlign: mine ? 'right' : 'left' } });
      row.appendChild(el('div', {
        style: {
          display: 'inline-block', maxWidth: '85%', padding: '8px 12px', borderRadius: '16px',
          background: mine ? color : sys ? '#FEF3C7' : human ? '#DBEAFE' : '#fff',
          color: mine ? '#fff' : '#0B1220',
          border: mine ? '0' : '1px solid #e5e7eb', fontSize: '14px', lineHeight: '1.4',
        },
        text: text,
      }));
      messages.appendChild(row);
      messages.scrollTop = messages.scrollHeight;
      return row.querySelector('div');
    }

    function renderActions() {
      actionBar.innerHTML = '';
      (cfg.actions || []).forEach(function (a) {
        var b = el('button', { type: 'button', style: pill('#fff', false), text: a.label });
        b.addEventListener('click', function () { runAction(a.id); });
        actionBar.appendChild(b);
      });
    }

    function runAction(actionId) {
      if (actionId === 'collect_contact') {
        showContactForm();
        return;
      }
      if (actionId === 'schedule') {
        showSchedulePicker();
        return;
      }
      if (actionId === 'handoff') {
        postAction('handoff', { note: 'Visitor requested to talk to the team' });
      }
    }

    function postAction(action, data) {
      var thinking = addMsg('them', '…');
      fetchJson(apiBase + '/action/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: token, action: action, visitor_id: visitorId, session_id: sessionId, data: data || {} }),
      }).then(function (res) {
        if (res.session_id) {
          sessionId = res.session_id;
          localStorage.setItem(sessionKey(token), sessionId);
        }
        thinking.textContent = res.message || 'Done.';
        if (res.request_takeover) {
          humanMode = true;
          document.getElementById('lb-status').textContent = 'Connecting you to a teammate…';
          startPolling();
        }
      }).catch(function (err) {
        thinking.textContent = err.message || 'Could not complete that action.';
      });
    }

    function showContactForm() {
      var wrap = el('div', { style: { marginBottom: '8px', padding: '10px', background: '#fff', border: '1px solid #E5E7EB', borderRadius: '12px' } });
      wrap.appendChild(el('div', { style: { fontSize: '12px', fontWeight: '650', marginBottom: '8px' }, text: 'Share your details' }));
      var nameI = el('input', { placeholder: 'Name', style: fieldStyle() });
      var emailI = el('input', { placeholder: 'Email', style: fieldStyle() });
      var phoneI = el('input', { placeholder: 'Phone', style: fieldStyle() });
      var go = el('button', { type: 'button', style: Object.assign(pill(color, true), { marginTop: '6px' }), text: 'Send to team' });
      go.addEventListener('click', function () {
        wrap.remove();
        postAction('collect_contact', { name: nameI.value, email: emailI.value, phone: phoneI.value });
      });
      wrap.appendChild(nameI); wrap.appendChild(emailI); wrap.appendChild(phoneI); wrap.appendChild(go);
      messages.appendChild(wrap);
      messages.scrollTop = messages.scrollHeight;
    }

    function fieldStyle() {
      return { display: 'block', width: '100%', marginBottom: '6px', border: '1px solid #D1D5DB', borderRadius: '8px', padding: '8px 10px', fontSize: '13px', boxSizing: 'border-box' };
    }

    function showSchedulePicker() {
      var wrap = el('div', { style: { marginBottom: '8px', padding: '10px', background: '#fff', border: '1px solid #E5E7EB', borderRadius: '12px' } });
      wrap.appendChild(el('div', { style: { fontSize: '12px', fontWeight: '650', marginBottom: '8px' }, text: 'Pick a time' }));
      (cfg.slots || []).forEach(function (slot) {
        var b = el('button', {
          type: 'button',
          style: { display: 'block', width: '100%', textAlign: 'left', marginBottom: '6px', border: '1px solid #DDD6FE', borderRadius: '8px', padding: '8px 10px', background: '#F5F3FF', cursor: 'pointer', fontSize: '12px' },
          text: slot.label,
        });
        b.addEventListener('click', function () {
          wrap.remove();
          postAction('schedule', { slot_id: slot.id, label: slot.label, starts_at: slot.starts_at });
        });
        wrap.appendChild(b);
      });
      if (!(cfg.slots || []).length) {
        wrap.appendChild(el('div', { style: { fontSize: '12px', color: '#6B7280' }, text: 'No slots available right now.' }));
      }
      messages.appendChild(wrap);
      messages.scrollTop = messages.scrollHeight;
    }

    function startPolling() {
      if (pollTimer) return;
      pollTimer = setInterval(function () {
        if (!sessionId) return;
        fetchJson(apiBase + '/poll/?token=' + encodeURIComponent(token) + '&session_id=' + sessionId + '&after_id=' + lastMsgId)
          .then(function (data) {
            humanMode = !!data.human_mode;
            if (humanMode) document.getElementById('lb-status').textContent = 'Talking with a teammate';
            (data.messages || []).forEach(function (m) {
              lastMsgId = Math.max(lastMsgId, m.id);
              var kind = m.role === 'human' ? 'human' : (m.role === 'system' ? 'system' : 'them');
              addMsg(kind, m.content);
            });
          })
          .catch(function () {});
      }, 2500);
    }

    function sendMessage(text, continueLast) {
      if (!text) return;
      addMsg('you', text);
      var replyNode = addMsg('them', '…');
      fetchJson(apiBase + '/message/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token: token, message: text, visitor_id: visitorId, session_id: sessionId,
          stream: false, continue_last: !!continueLast,
        }),
      }).then(function (data) {
        if (data.session_id) {
          sessionId = data.session_id;
          localStorage.setItem(sessionKey(token), sessionId);
        }
        if (data.human_mode) {
          humanMode = true;
          replyNode.textContent = data.message || 'A teammate will reply shortly.';
          document.getElementById('lb-status').textContent = 'Talking with a teammate';
          startPolling();
          return;
        }
        replyNode.textContent = data.reply || 'No reply.';
        if (data.actions && data.actions.tasks_created && data.actions.tasks_created.length) {
          addMsg('system', '✓ ' + data.actions.tasks_created[0].title);
        }
      }).catch(function () {
        replyNode.textContent = 'Sorry, I could not reply right now.';
      });
    }

    addMsg('them', cfg.greeting || ('Hi, I am ' + cfg.name + '.'));
    renderActions();

    launcher.addEventListener('click', function () {
      if (open) {
        closePanel();
        return;
      }
      open = true;
      launcher.style.display = 'none';  // Hide launcher when chat opens
      panel.style.display = 'flex';
      applyResponsiveLayout();
      input.focus();
      if (humanMode || sessionId) startPolling();
    });

    input.addEventListener('focus', function () {
      applyResponsiveLayout();
      window.requestAnimationFrame(applyResponsiveLayout);
      window.setTimeout(applyResponsiveLayout, 100);
      window.setTimeout(applyResponsiveLayout, 300);
      window.setTimeout(applyResponsiveLayout, 500);
    });

    input.addEventListener('blur', function () {
      window.setTimeout(applyResponsiveLayout, 100);
      window.setTimeout(applyResponsiveLayout, 300);
    });

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var text = (input.value || '').trim();
      input.value = '';
      sendMessage(text, false);
    });
  }
})();
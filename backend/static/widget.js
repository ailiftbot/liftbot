(function () {
  'use strict';

  /* ============================================================
     1. SCRIPT / CONFIG DISCOVERY  (same as before)
     ============================================================ */
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

  // NEW: let the site owner move the launcher so it doesn't sit on top of
  // a button they already have there (WhatsApp button, back-to-top, etc.)
  // <script data-position="bottom-left" data-offset-x="24" data-offset-y="90" ...>
  var position = (script.getAttribute('data-position') || 'bottom-right').toLowerCase();
  var offsetX = parseInt(script.getAttribute('data-offset-x'), 10);
  if (isNaN(offsetX)) offsetX = 20;
  var offsetY = parseInt(script.getAttribute('data-offset-y'), 10);
  if (isNaN(offsetY)) offsetY = 20;

  var storageKey = workspaceToken
    ? 'liftbot_ws_visitor_' + workspaceToken
    : 'liftbot_visitor_' + employeeToken;
  var sessionKeyPrefix = workspaceToken || employeeToken;
  var visitorId = localStorage.getItem(storageKey) ||
    (crypto.randomUUID ? crypto.randomUUID() : (Date.now().toString(36) + Math.random().toString(36).slice(2)));
  localStorage.setItem(storageKey, visitorId);

  function fetchJson(url, opts) {
    return fetch(url, Object.assign({ mode: 'cors', credentials: 'omit' }, opts || {})).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error((data && data.error) || ('HTTP ' + r.status));
        return data;
      });
    });
  }

  function ready(fn) {
    if (document.body) fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  function isMobileViewport() {
    return window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
  }

  function safeJSON(obj) {
    // defensive: never let a "</script>" inside cfg data break out of our injected <script> tag
    return JSON.stringify(obj).replace(/</g, '\\u003c');
  }

  function startWithEmployee(token) {
    fetchJson(apiBase + '/config/?token=' + encodeURIComponent(token) + '&visitor_id=' + encodeURIComponent(visitorId))
      .then(function (cfg) {
        cfg._token = token;
        ready(function () { mountEmployee(cfg); });
      })
      .catch(function (err) { console.error('[LiftBot] Config failed', err); });
  }

  if (workspaceToken) {
    fetchJson(apiBase + '/roster/?workspace_token=' + encodeURIComponent(workspaceToken))
      .then(function (roster) {
        if (!roster.employees || !roster.employees.length) return;
        if (roster.employees.length === 1) startWithEmployee(roster.employees[0].token);
        else ready(function () { mountRoster(roster); });
      })
      .catch(function (err) { console.error('[LiftBot] Roster failed', err); });
  } else if (employeeToken) {
    startWithEmployee(employeeToken);
  } else {
    console.warn('[LiftBot] Set data-employee-token or data-workspace-token.');
  }

  /* ============================================================
     2. ISOLATED SHELL
     A tiny fixed <div> holding a same-origin <iframe> (created
     fresh, never navigated, so it inherits this page's origin —
     fetch/localStorage behave exactly like before). Everything
     visual lives INSIDE that iframe's own document, so the host
     page's CSS (resets, "button{width:100%}", "img{width:100%}",
     global transitions, etc.) can never leak in and blow up the
     layout. This is what was causing the "covers whole screen"
     and random overlap bugs.
     The outer <div> itself is resized (small when closed, panel
     sized when open) so it also can't visually collide with other
     floating buttons once you set data-offset-y.
     ============================================================ */
  function createShell(id) {
    var container = document.createElement('div');
    container.id = id;
    Object.assign(container.style, {
      position: 'fixed',
      zIndex: '2147483647',
      background: 'transparent',
      margin: '0',
      padding: '0',
      border: '0',
      pointerEvents: 'none',
      overflow: 'visible',
    });

    // Hard-guard against aggressive host stylesheets (global "*" rules,
    // !important resets, etc.) overriding our positioning.
    var guard = document.createElement('style');
    guard.textContent =
      '#' + id + '{position:fixed!important;z-index:2147483647!important;' +
      'margin:0!important;padding:0!important;border:0!important;background:transparent!important;' +
      'pointer-events:none!important;overflow:visible!important;display:block!important;' +
      'box-sizing:content-box!important;max-width:none!important;max-height:none!important;}' +
      '#' + id + ' > iframe{width:100%!important;height:100%!important;border:0!important;display:block!important;' +
      'background:transparent!important;pointer-events:auto!important;border-radius:inherit!important;}';
    document.head.appendChild(guard);

    var iframe = document.createElement('iframe');
    iframe.setAttribute('title', 'Chat widget');
    iframe.setAttribute('scrolling', 'no');
    iframe.setAttribute('allowTransparency', 'true');
    iframe.style.background = 'transparent';
    iframe.style.borderRadius = 'inherit';
    container.appendChild(iframe);
    document.body.appendChild(container);

    function cssName(k) {
      return k.replace(/[A-Z]/g, function (m) { return '-' + m.toLowerCase(); });
    }

    return {
      container: container,
      iframe: iframe,
      place: function (rect) {
        ['top', 'right', 'bottom', 'left'].forEach(function (side) {
          container.style.removeProperty(side);
        });
        Object.keys(rect).forEach(function (key) {
          var val = rect[key];
          if (val == null || val === 'auto') {
            container.style.removeProperty(cssName(key));
            return;
          }
          var important = (key === 'top' || key === 'right' || key === 'bottom' || key === 'left' ||
            key === 'width' || key === 'height') ? 'important' : '';
          container.style.setProperty(cssName(key), String(val), important);
        });
      },
      write: function (html) {
        var doc = iframe.contentWindow.document;
        doc.open();
        doc.write(html);
        doc.close();
      },
      destroy: function () { container.remove(); guard.remove(); },
    };
  }

  function anchorStyle(rect) {
    if (position === 'bottom-left') rect.left = offsetX + 'px';
    else rect.right = offsetX + 'px';
    return rect;
  }

  /* ============================================================
     3. CHAT WIDGET (launcher + panel)
     ============================================================ */
  function mountEmployee(cfg) {
    var token = cfg._token;
    var color = cfg.brand_color || '#7C3AED';
    var sessionId = localStorage.getItem('liftbot_session_' + sessionKeyPrefix + '_' + token) || null;
    if (document.getElementById('liftbot-shell-' + token)) return;
    var shell = createShell('liftbot-shell-' + token);
    var open = false;
    var expanded = false;
    var fabSize = function () { return isMobileViewport() ? 60 : 64; };

    function closedRect() {
      if (expanded) {
        return anchorStyle({
          width: isMobileViewport() ? 'min(320px, calc(100vw - 24px))' : '312px',
          height: '184px',
          bottom: isMobileViewport() ? (offsetY + 76) + 'px' : offsetY + 'px',
          borderRadius: '20px',
          boxShadow: 'none',
          overflow: 'visible',
        });
      }
      var size = fabSize();
      return anchorStyle({
        width: size + 'px',
        height: size + 'px',
        bottom: offsetY + 'px',
        borderRadius: '50%',
        boxShadow: '0 10px 28px rgba(15,23,42,.28)',
        overflow: 'visible',
      });
    }

    function openRect() {
      if (isMobileViewport()) {
        var vv = window.visualViewport;
        var vh = vv ? vv.height : window.innerHeight;
        var vTop = vv ? Math.round(vv.offsetTop) : 0;
        return {
          left: '0px',
          right: '0px',
          width: 'auto',
          top: vTop + 'px',
          height: Math.max(240, Math.round(vh)) + 'px',
          borderRadius: '0px',
          boxShadow: 'none',
          overflow: 'hidden',
        };
      }
      var bottomOffset = offsetY + 76;
      var availableHeight = window.innerHeight - bottomOffset - 16;
      var h = Math.min(720, Math.max(360, availableHeight));
      return anchorStyle({
        width: '400px',
        height: h + 'px',
        bottom: bottomOffset + 'px',
        borderRadius: '24px',
        boxShadow: '0 24px 64px rgba(15,23,42,.28)',
        overflow: 'hidden',
      });
    }

    function reposition() { shell.place(open ? openRect() : closedRect()); }

    reposition();
    window.addEventListener('resize', reposition);
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', reposition);
      window.visualViewport.addEventListener('scroll', reposition);
    }

    shell.iframe.addEventListener('lb-toggle', function (e) {
      open = !!(e.detail && e.detail.open);
      expanded = false;
      reposition();
    });
    shell.iframe.addEventListener('lb-expand', function () {
      expanded = true;
      reposition();
    });

    shell.write(employeeDocHtml(cfg, token, apiBase, visitorId, sessionKeyPrefix, sessionId, color));
  }

  function employeeDocHtml(cfg, token, apiBase, visitorId, sessionKeyPrefix, sessionId, color) {
    var initial = escapeHtml((cfg.name || 'AI').slice(0, 1).toUpperCase());
    var avatar = cfg.avatar_url
      ? '<img class="lb-avatar-img" src="' + escapeAttr(cfg.avatar_url) + '" alt="">'
      : '<span class="lb-avatar-fallback">' + initial + '</span>';
    return '<!doctype html><html><head><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">' +
      '<style>' +
      '*{box-sizing:border-box}' +
      'html,body{margin:0;height:100%;width:100%;overflow:hidden;font-family:"Plus Jakarta Sans",ui-sans-serif,system-ui,sans-serif;background:transparent!important;pointer-events:none;color-scheme:normal;}' +
      'body{display:flex;flex-direction:column;justify-content:flex-end;align-items:flex-end;}' +
      '.lb-launcher,.lb-panel{pointer-events:auto}' +
      '.lb-launcher{all:unset;position:relative;display:flex;align-items:center;justify-content:center;width:100%;height:100%;' +
      'border-radius:50%;cursor:pointer;color:#fff;pointer-events:auto;overflow:visible;' +
      'background:radial-gradient(circle at 30% 25%,rgba(255,255,255,.28),transparent 42%),' + color + ';' +
      'transition:transform .15s ease;}' +
      '.lb-launcher:hover{transform:scale(1.04)}' +
      '.lb-launcher__pulse{position:absolute;inset:-5px;border-radius:50%;border:2px solid ' + color + ';opacity:.45;animation:lbPulse 2s ease-out infinite;pointer-events:none}' +
      '.lb-launcher svg{width:28px;height:28px;position:relative;z-index:1}' +
      '@keyframes lbPulse{0%{transform:scale(.92);opacity:.5}70%{transform:scale(1.18);opacity:0}100%{opacity:0}}' +
      '.lb-preview{display:none;flex-direction:column;gap:10px;width:100%;height:100%;pointer-events:auto;cursor:pointer}' +
      'body.expanded .lb-launcher,body.expanded .lb-panel{display:none!important}' +
      'body.expanded .lb-preview{display:flex}' +
      '.lb-preview-card{padding:14px 16px;background:#fff;border-radius:18px;box-shadow:0 10px 30px rgba(15,23,42,.18)}' +
      '.lb-preview-top{display:flex;align-items:flex-start;gap:10px}' +
      '.lb-preview-avatar{width:38px;height:38px;border-radius:50%;flex-shrink:0;display:grid;place-items:center;background:' + color + ';color:#fff;font-weight:800;font-size:14px}' +
      '.lb-preview-info{min-width:0;flex:1;padding-top:1px}' +
      '.lb-preview-name{font-weight:800;font-size:14.5px;color:#111827}' +
      '.lb-preview-online{display:inline-flex;align-items:center;gap:4px;margin-left:7px;color:#059669;font-size:10.5px;font-weight:700}' +
      '.lb-preview-online i{width:6px;height:6px;border-radius:50%;background:#22c55e}' +
      '.lb-preview-role{margin-top:2px;color:#6b7280;font-size:11.5px}' +
      '.lb-preview-greeting{margin:11px 0 0;color:#374151;font-size:12.5px;line-height:1.45}' +
      '.lb-preview-search{display:flex;align-items:center;gap:10px;padding:10px 8px 10px 16px;background:#fff;border-radius:999px;box-shadow:0 10px 30px rgba(15,23,42,.18);color:#6b7280;font-size:13.5px}' +
      '.lb-preview-search svg{width:17px;height:17px;flex-shrink:0;color:#9ca3af}' +
      '.lb-preview-search-text{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
      '.lb-preview-arrow{display:grid;place-items:center;flex:0 0 30px;width:30px;height:30px;border-radius:50%;background:' + color + ';color:#fff}' +
      '.lb-preview-arrow svg{width:14px;height:14px}' +
      '.lb-panel{display:none;position:relative;flex-direction:column;width:100%;height:100%;background:#f3f4f6;border-radius:24px;overflow:hidden}' +
      'body.open{align-items:stretch}' +
      'body.open .lb-launcher{display:none!important}' +
      'body.open .lb-panel{display:flex;flex:1;min-height:0;height:100%;width:100%;margin:0}' +
      'body.open.mobile .lb-panel{border-radius:0}' +
      '.lb-tabbar{display:flex;align-items:center;gap:8px;padding:10px 12px;background:#fff;' +
      'border-bottom:1px solid #eceff3;flex-shrink:0}' +
      '.lb-tabbar__home{border:0;background:#F3F4F6;color:#374151;width:34px;height:34px;border-radius:50%;' +
      'display:grid;place-items:center;cursor:pointer;flex-shrink:0;padding:0}' +
      '.lb-tabbar__home svg{width:16px;height:16px}' +
      '.lb-tabs{display:flex;gap:3px;background:#F3F4F6;border-radius:999px;padding:3px;flex:1;min-width:0}' +
      '.lb-tab{flex:1;min-width:0;border:0;background:transparent;color:#6B7280;font-size:12px;font-weight:700;' +
      'padding:7px 6px;border-radius:999px;cursor:pointer;font-family:inherit;display:flex;align-items:center;' +
      'justify-content:center;gap:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
      '.lb-tab svg{width:13px;height:13px;flex-shrink:0}' +
      '.lb-tab.active{background:' + color + ';color:#fff;box-shadow:0 2px 8px rgba(0,0,0,.18)}' +
      '.lb-tabbar__home:hover{background:#E5E7EB}' +
      '.lb-view{flex:1;min-height:0;display:none;flex-direction:column;overflow:hidden}' +
      '.lb-view.active{display:flex}' +
      '.lb-subheader{padding:16px 18px 6px;font-weight:800;font-size:16px;color:#111827;flex-shrink:0;background:#fff;' +
      'letter-spacing:-.01em}' +
      '.lb-list{flex:1;min-height:0;overflow-y:auto;padding:4px 10px 14px;-webkit-overflow-scrolling:touch;background:#fff}' +
      '.lb-article{display:flex;align-items:center;gap:12px;width:100%;text-align:left;border:0;border-radius:14px;' +
      'background:#fff;padding:12px 10px;cursor:pointer;font-family:inherit;transition:background .12s ease}' +
      '.lb-article:hover{background:#F8F7FF}' +
      '.lb-article__icon{width:34px;height:34px;border-radius:10px;background:#F3F4F6;color:' + color + ';flex-shrink:0;' +
      'display:grid;place-items:center}' +
      '.lb-article__icon svg{width:16px;height:16px}' +
      '.lb-article__title{font-size:13.5px;font-weight:650;color:#111827;line-height:1.4;flex:1;min-width:0}' +
      '.lb-article__chevron{flex-shrink:0;color:#d1d5db}' +
      '.lb-article__chevron svg{width:14px;height:14px;display:block}' +
      '.lb-spinner{width:15px;height:15px;border:2px solid #E5E7EB;border-top-color:' + color + ';border-radius:50%;' +
      'display:inline-block;animation:lbSpin .65s linear infinite;vertical-align:-3px;margin-right:8px}' +
      '@keyframes lbSpin{to{transform:rotate(360deg)}}' +
      '.lb-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;' +
      'padding:44px 24px;color:#9ca3af;font-size:13px;text-align:center;line-height:1.5}' +
      '.lb-empty--row{flex-direction:row;padding:20px 16px;gap:0}' +
      '.lb-empty__icon{width:48px;height:48px;border-radius:50%;background:#F3F4F6;display:grid;place-items:center;color:#c7cbd1;flex-shrink:0}' +
      '.lb-empty__icon svg{width:22px;height:22px}' +
      '.lb-detail{flex:1;min-height:0;display:flex;flex-direction:column;background:#fff}' +
      '.lb-detail__back{display:flex;align-items:center;gap:6px;border:0;background:transparent;color:' + color + ';' +
      'font-weight:700;font-size:13px;padding:12px 14px 4px;cursor:pointer;font-family:inherit;flex-shrink:0}' +
      '.lb-detail__back svg{width:14px;height:14px}' +
      '.lb-detail__body{flex:1;min-height:0;overflow-y:auto;padding:8px 20px 24px}' +
      '.lb-detail__title{font-size:17px;font-weight:800;color:#111827;margin:8px 0 14px;line-height:1.35;letter-spacing:-.01em}' +
      '.lb-detail__content{font-size:13.5px;color:#374151;line-height:1.65;white-space:pre-wrap}' +
      '.lb-searchbar{padding:12px 14px;flex-shrink:0;background:#fff;border-bottom:1px solid #f1f3f5}' +
      '.lb-searchbar__wrap{position:relative;display:flex;align-items:center}' +
      '.lb-searchbar__wrap svg{position:absolute;left:13px;width:15px;height:15px;color:#9ca3af;pointer-events:none}' +
      '.lb-searchbar input{width:100%;border:1.5px solid #e5e7eb;background:#f8fafc;border-radius:22px;' +
      'padding:11px 14px 11px 36px;outline:none;font-size:14px;font-family:inherit;transition:border-color .15s,background .15s}' +
      '.lb-searchbar input:focus{border-color:' + color + ';background:#fff;box-shadow:0 0 0 3px rgba(124,58,237,.10)}' +
      '.lb-brandfoot{flex-shrink:0;text-align:center;padding:7px 0 calc(7px + env(safe-area-inset-bottom,0px));' +
      'font-size:10.5px;color:#b3b6bd;background:#fff;border-top:1px solid #f1f3f5;letter-spacing:.01em}' +
      '.lb-brandfoot b{color:#8a8d97;font-weight:800}' +
      '.lb-header{position:relative;background:' + color + ';color:#fff;padding:12px 10px 12px 16px;' +
      'display:flex;gap:12px;align-items:center;flex-shrink:0}' +
      '.lb-avatar{position:relative;width:44px;height:44px;flex-shrink:0}' +
      '.lb-avatar-img,.lb-avatar-fallback{width:44px;height:44px;border-radius:50%;object-fit:cover;display:grid;place-items:center;' +
      'background:rgba(255,255,255,.18);font-weight:800;font-size:18px;color:#fff}' +
      '.lb-online{position:absolute;right:1px;bottom:1px;width:11px;height:11px;background:#22c55e;border:2px solid ' + color + ';border-radius:50%}' +
      '.lb-head-copy{min-width:0;flex:1}' +
      '.lb-name{font-weight:800;font-size:16px;letter-spacing:-.02em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
      '.lb-status{font-size:12.5px;opacity:.88;margin-top:2px;font-weight:500}' +
      '.lb-head-actions{display:flex;align-items:center;gap:2px;flex-shrink:0}' +
      '.lb-iconbtn{border:0;background:transparent;color:#fff;width:40px;height:40px;border-radius:12px;cursor:pointer;' +
      'font-size:20px;line-height:1;padding:0;display:grid;place-items:center;opacity:.95}' +
      '.lb-iconbtn:hover{background:rgba(255,255,255,.12)}' +
      '.lb-more-menu{display:none;position:absolute;top:56px;right:10px;background:#fff;color:#111827;border-radius:12px;' +
      'box-shadow:0 12px 32px rgba(15,23,42,.18);min-width:160px;z-index:5;overflow:hidden;font-size:13px}' +
      '.lb-more-menu.open{display:block}' +
      '.lb-more-menu button{display:block;width:100%;text-align:left;border:0;background:#fff;padding:12px 14px;cursor:pointer;font:inherit}' +
      '.lb-more-menu button:hover{background:#f3f4f6}' +
      '.lb-resume{padding:12px 14px;background:#EEF2FF;border-bottom:1px solid #E0E7FF;font-size:12.5px;color:#3730A3;line-height:1.45;flex-shrink:0}' +
      '.lb-resume-actions{display:flex;gap:8px;margin-top:10px}' +
      '.lb-messages{flex:1;min-height:0;overflow-y:auto;padding:14px 14px 10px;background:#f3f4f6;-webkit-overflow-scrolling:touch}' +
      '.lb-day{display:flex;justify-content:center;margin:4px 0 16px}' +
      '.lb-day span{background:#e5e7eb;color:#6b7280;font-size:11px;font-weight:700;letter-spacing:.02em;padding:4px 12px;border-radius:999px}' +
      '.lb-row{margin-bottom:14px;display:flex;align-items:flex-end;gap:8px}' +
      '.lb-row--you{justify-content:flex-end}' +
      '.lb-row--them{justify-content:flex-start}' +
      '.lb-mini{width:28px;height:28px;border-radius:50%;flex-shrink:0;object-fit:cover;display:grid;place-items:center;' +
      'background:' + color + ';color:#fff;font-size:11px;font-weight:800}' +
      '.lb-msg{max-width:78%;display:flex;flex-direction:column}' +
      '.lb-row--you .lb-msg{align-items:flex-end}' +
      '.lb-row--them .lb-msg{align-items:flex-start}' +
      '.lb-bubble{padding:10px 14px;border-radius:16px;font-size:14.5px;line-height:1.45;word-wrap:break-word}' +
      '.lb-bubble--them{background:#fff;color:#1f2937;border-radius:16px 16px 16px 4px;box-shadow:0 1px 2px rgba(15,23,42,.05)}' +
      '.lb-bubble--you{background:' + color + ';color:#fff;border-radius:16px 16px 4px 16px;padding:10px 14px}' +
      '.lb-bubble--sys{background:#FEF3C7;color:#92400E;font-size:12.5px;border-radius:12px}' +
      '.lb-bubble--human{background:#DBEAFE;color:#1E3A8A}' +
      '.lb-bubble--typing{display:flex;align-items:center;gap:5px;min-width:52px;min-height:36px;padding:12px 16px}' +
      '.lb-bubble--typing span{width:7px;height:7px;border-radius:50%;background:#9ca3af;animation:lbDot 1.2s infinite ease-in-out}' +
      '.lb-bubble--typing span:nth-child(2){animation-delay:.15s}' +
      '.lb-bubble--typing span:nth-child(3){animation-delay:.3s}' +
      '@keyframes lbDot{0%,80%,100%{transform:translateY(0);opacity:.4}40%{transform:translateY(-4px);opacity:1}}' +
      '.lb-meta{font-size:11px;color:#9ca3af;margin-top:5px;padding:0 2px;line-height:1.2}' +
      '.lb-meta--you{display:flex;align-items:center;gap:5px;justify-content:flex-end}' +
      '.lb-meta--you .lb-ticks{font-size:11px;letter-spacing:-.5px;opacity:.8}' +
      '.lb-actions{padding:4px 12px 8px;display:flex;flex-wrap:wrap;gap:8px;background:#f3f4f6;flex-shrink:0}' +
      '.lb-form{display:flex;align-items:center;gap:10px;padding:10px 12px calc(12px + env(safe-area-inset-bottom,0px));' +
      'background:#fff;border-top:1px solid #eceff3;flex-shrink:0}' +
      '.lb-input{flex:1;min-width:0;border:1px solid #e5e7eb;background:#f8fafc;border-radius:22px;padding:12px 16px;outline:none;font-size:16px}' +
      '.lb-input:focus{border-color:' + color + ';background:#fff;box-shadow:0 0 0 3px rgba(124,58,237,.12)}' +
      '.lb-send{border:0;border-radius:50%;background:' + color + ';color:#fff;width:44px;height:44px;cursor:pointer;flex-shrink:0;display:grid;place-items:center}' +
      '.lb-send:disabled{opacity:.55}' +
      '.lb-send svg{width:18px;height:18px}' +
      '.lb-pill{border-radius:999px;padding:7px 12px;font-size:12px;cursor:pointer;font-weight:650;font-family:inherit}' +
      '.lb-pill-solid{border:0;background:' + color + ';color:#fff}' +
      '.lb-pill-outline{border:1px solid #ddd6fe;background:#fff;color:#5B21B6}' +
      '.lb-field{display:block;width:100%;margin-bottom:6px;border:1px solid #D1D5DB;border-radius:10px;padding:10px 12px;font-size:14px}' +
      '.lb-card{margin:0 0 10px;padding:12px;background:#fff;border:1px solid #E5E7EB;border-radius:14px;width:100%}' +
      '.lb-slot{display:block;width:100%;text-align:left;margin-bottom:6px;border:1px solid #DDD6FE;border-radius:10px;' +
      'padding:10px 12px;background:#F5F3FF;cursor:pointer;font-size:13px;font-family:inherit}' +
      '</style></head><body class="closed">' +
      '<button type="button" class="lb-launcher" id="lbLauncher" aria-label="Open conversation">' +
      '<span class="lb-launcher__pulse" aria-hidden="true"></span>' +
      '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M4.5 19.5V6.75A2.75 2.75 0 0 1 7.25 4h9.5A2.75 2.75 0 0 1 19.5 6.75v7A2.75 2.75 0 0 1 16.75 16.5H8.12L4.5 19.5Z"/></svg>' +
      '</button>' +
      '<div class="lb-preview" id="lbPreview" role="button" tabindex="0" aria-label="Open chat with ' + escapeAttr(cfg.name || '') + '">' +
      '<div class="lb-preview-card"><div class="lb-preview-top">' +
      '<div class="lb-preview-avatar">' + avatar + '</div>' +
      '<div class="lb-preview-info"><div><span class="lb-preview-name">' + escapeHtml(cfg.name || '') + '</span><span class="lb-preview-online"><i></i>Online</span></div>' +
      '<div class="lb-preview-role">' + escapeHtml(cfg.role || 'AI Employee') + '</div></div></div>' +
      '<div class="lb-preview-greeting">' + escapeHtml(cfg.greeting || 'Hi! Need help with something?') + '</div></div>' +
      '<div class="lb-preview-search"><svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.75"/><path d="m20 20-3.2-3.2" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/></svg>' +
      '<span class="lb-preview-search-text">Ask ' + escapeHtml((cfg.name || 'AI').split(' ')[0]) + '...</span><span class="lb-preview-arrow"><svg viewBox="0 0 24 24" fill="none"><path d="M12 19V5M5 12l7-7 7 7" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></span></div>' +
      '</div>' +
      '<div class="lb-panel">' +
      '<div class="lb-header">' +
      '<div class="lb-avatar">' + avatar + '<span class="lb-online" aria-hidden="true"></span></div>' +
      '<div class="lb-head-copy"><div class="lb-name">' + escapeHtml(cfg.name || '') + '</div>' +
      '<div class="lb-status" id="lbStatus">Online • ' + escapeHtml(cfg.role || 'AI Employee') + '</div></div>' +
      '<div class="lb-head-actions">' +
      '<button type="button" class="lb-iconbtn" id="lbMore" aria-label="More">&#8942;</button>' +
      '<button type="button" class="lb-iconbtn" id="lbClose" aria-label="Close">&times;</button>' +
      '</div>' +
      '<div class="lb-more-menu" id="lbMoreMenu"><button type="button" id="lbFresh">Start fresh</button></div>' +
      '</div>' +
      '<div class="lb-tabbar">' +
      '<button type="button" class="lb-tabbar__home" id="lbHome" aria-label="Home">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 11 9-8 9 8"/><path d="M5 10v10h14V10"/></svg>' +
      '</button>' +
      '<div class="lb-tabs">' +
      '<button type="button" class="lb-tab active" id="lbTabMessages" data-tab="messages">' +
      '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M4.5 19.5V6.75A2.75 2.75 0 0 1 7.25 4h9.5A2.75 2.75 0 0 1 19.5 6.75v7A2.75 2.75 0 0 1 16.75 16.5H8.12L4.5 19.5Z"/></svg>Messages</button>' +
      '<button type="button" class="lb-tab" id="lbTabArticles" data-tab="articles">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h9l5 5v13H6z"/><path d="M9 12h6M9 16h6M9 8h3"/></svg>Articles</button>' +
      '<button type="button" class="lb-tab" id="lbTabSearch" data-tab="search">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>Search</button>' +
      '</div>' +
      '</div>' +
      '<div class="lb-view active" id="lbViewMessages">' +
      (cfg.returning_visitor && cfg.resume_message ? '<div class="lb-resume" id="lbResume"></div>' : '') +
      '<div class="lb-messages" id="lbMessages"></div>' +
      '<div class="lb-actions" id="lbActions"></div>' +
      '<form class="lb-form" id="lbForm">' +
      '<input class="lb-input" id="lbInput" type="text" placeholder="Write a message…" autocomplete="off">' +
      '<button class="lb-send" id="lbSend" type="submit" aria-label="Send">' +
      '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M3.4 20.6 21 12 3.4 3.4l.1 6.7L14 12 3.5 13.9z"/></svg>' +
      '</button>' +
      '</form>' +
      '</div>' +
      '<div class="lb-view" id="lbViewArticles">' +
      '<div class="lb-subheader" id="lbArticlesTitle">Help Articles</div>' +
      '<div class="lb-list" id="lbArticlesList"></div>' +
      '<div class="lb-detail" id="lbArticleDetail" style="display:none">' +
      '<button type="button" class="lb-detail__back" id="lbArticleBack">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>All articles</button>' +
      '<div class="lb-detail__body">' +
      '<div class="lb-detail__title" id="lbArticleTitle"></div>' +
      '<div class="lb-detail__content" id="lbArticleContent"></div>' +
      '</div></div>' +
      '</div>' +
      '<div class="lb-view" id="lbViewSearch">' +
      '<div class="lb-searchbar"><div class="lb-searchbar__wrap">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>' +
      '<input id="lbSearchInput" type="text" placeholder="Search for help…" autocomplete="off">' +
      '</div></div>' +
      '<div class="lb-list" id="lbSearchResults"></div>' +
      '<div class="lb-detail" id="lbSearchDetail" style="display:none">' +
      '<button type="button" class="lb-detail__back" id="lbSearchBack">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>Back to results</button>' +
      '<div class="lb-detail__body">' +
      '<div class="lb-detail__title" id="lbSearchDetailTitle"></div>' +
      '<div class="lb-detail__content" id="lbSearchDetailContent"></div>' +
      '</div></div>' +
      '</div>' +
      '<div class="lb-brandfoot">We run on <b>LiftBot</b></div>' +
      '</div>' +
      '<script>window.__LB__=' + safeJSON({
        cfg: cfg, token: token, apiBase: apiBase, visitorId: visitorId, color: color,
        sessionKey: 'liftbot_session_' + sessionKeyPrefix + '_' + token, sessionId: sessionId,
      }) + ';<\/script>' +
      '<script>' + employeeInnerScript() + '<\/script>' +
      '</body></html>';
  }

  // Runs INSIDE the iframe document (same origin as the host page).
  function employeeInnerScript() {
    return '(function(){' +
      'var D=window.__LB__,cfg=D.cfg,token=D.token,apiBase=D.apiBase,visitorId=D.visitorId,sessionKey=D.sessionKey;' +
      'var sessionId=D.sessionId,humanMode=false,lastMsgId=0,pollTimer=null,sending=false,seenIds={};' +
      'var messages=document.getElementById("lbMessages"),actions=document.getElementById("lbActions");' +
      'var statusEl=document.getElementById("lbStatus"),launcher=document.getElementById("lbLauncher"),preview=document.getElementById("lbPreview");' +
      'var closeBtn=document.getElementById("lbClose"),form=document.getElementById("lbForm"),input=document.getElementById("lbInput");' +
      'var sendBtn=document.getElementById("lbSend"),moreBtn=document.getElementById("lbMore"),moreMenu=document.getElementById("lbMoreMenu");' +
      'var initial=((cfg.name||"AI").slice(0,1)||"A").toUpperCase();' +
      'var homeBtn=document.getElementById("lbHome"),tabMessages=document.getElementById("lbTabMessages");' +
      'var tabArticles=document.getElementById("lbTabArticles"),tabSearch=document.getElementById("lbTabSearch");' +
      'var viewMessages=document.getElementById("lbViewMessages"),viewArticles=document.getElementById("lbViewArticles");' +
      'var viewSearch=document.getElementById("lbViewSearch");' +
      'var articlesList=document.getElementById("lbArticlesList"),articleDetail=document.getElementById("lbArticleDetail");' +
      'var articleBack=document.getElementById("lbArticleBack"),articleTitleEl=document.getElementById("lbArticleTitle");' +
      'var articleContentEl=document.getElementById("lbArticleContent");' +
      'var searchInput=document.getElementById("lbSearchInput"),searchResults=document.getElementById("lbSearchResults");' +
      'var searchDetail=document.getElementById("lbSearchDetail"),searchBack=document.getElementById("lbSearchBack");' +
      'var searchDetailTitle=document.getElementById("lbSearchDetailTitle"),searchDetailContent=document.getElementById("lbSearchDetailContent");' +
      'var articlesCache=null,searchTimer=null;' +

      '/* ---- Tabs: Messages / Articles / Search (Crisp-style) ---- */' +
      'function activateTab(name){' +
      '[tabMessages,tabArticles,tabSearch].forEach(function(t){t.classList.toggle("active",t.getAttribute("data-tab")===name);});' +
      '[viewMessages,viewArticles,viewSearch].forEach(function(v){v.classList.remove("active");});' +
      'if(name==="messages"){viewMessages.classList.add("active");if(!isMobile())input.focus();}' +
      'else if(name==="articles"){viewArticles.classList.add("active");loadArticles();}' +
      'else if(name==="search"){viewSearch.classList.add("active");if(!isMobile())searchInput.focus();}' +
      '}' +
      'tabMessages.addEventListener("click",function(){activateTab("messages");});' +
      'tabArticles.addEventListener("click",function(){activateTab("articles");});' +
      'tabSearch.addEventListener("click",function(){activateTab("search");});' +
      'homeBtn.addEventListener("click",function(){activateTab("messages");});' +

      'function articleIconSvg(){return "<svg viewBox=\\"0 0 24 24\\" fill=\\"none\\" stroke=\\"currentColor\\" stroke-width=\\"2\\" ' +
      'stroke-linecap=\\"round\\" stroke-linejoin=\\"round\\"><path d=\\"M6 3h9l5 5v13H6z\\"/><path d=\\"M9 12h6M9 16h6M9 8h3\\"/></svg>";}' +
      'function chevronSvg(){return "<svg viewBox=\\"0 0 24 24\\" fill=\\"none\\" stroke=\\"currentColor\\" stroke-width=\\"2.2\\" ' +
      'stroke-linecap=\\"round\\" stroke-linejoin=\\"round\\"><path d=\\"m9 6 6 6-6 6\\"/></svg>";}' +
      'function searchSvg(){return "<svg viewBox=\\"0 0 24 24\\" fill=\\"none\\" stroke=\\"currentColor\\" stroke-width=\\"1.8\\" ' +
      'stroke-linecap=\\"round\\" stroke-linejoin=\\"round\\"><circle cx=\\"11\\" cy=\\"11\\" r=\\"7\\"/><path d=\\"m21 21-4.3-4.3\\"/></svg>";}' +
      'function docSvg(){return "<svg viewBox=\\"0 0 24 24\\" fill=\\"none\\" stroke=\\"currentColor\\" stroke-width=\\"1.8\\" ' +
      'stroke-linecap=\\"round\\" stroke-linejoin=\\"round\\"><path d=\\"M6 3h9l5 5v13H6z\\"/><path d=\\"M9 12h6M9 16h6M9 8h3\\"/></svg>";}' +

      'function emptyState(container,iconSvg,text){container.innerHTML="";var e=document.createElement("div");e.className="lb-empty";' +
      'var i=document.createElement("div");i.className="lb-empty__icon";i.innerHTML=iconSvg;' +
      'var t=document.createElement("div");t.textContent=text;e.appendChild(i);e.appendChild(t);container.appendChild(e);}' +
      'function loadingState(container,text){container.innerHTML="<div class=\\"lb-empty lb-empty--row\\">' +
      '<span class=\\"lb-spinner\\"></span><span>"+text+"</span></div>";}' +

      'function renderArticleRows(container,items,onPick,emptyIcon,emptyText){container.innerHTML="";' +
      'if(!items.length){emptyState(container,emptyIcon,emptyText);return;}' +
      'items.forEach(function(a){var b=document.createElement("button");b.type="button";b.className="lb-article";' +
      'var icon=document.createElement("span");icon.className="lb-article__icon";icon.innerHTML=articleIconSvg();' +
      'var t=document.createElement("span");t.className="lb-article__title";t.textContent=a.title;' +
      'var chev=document.createElement("span");chev.className="lb-article__chevron";chev.innerHTML=chevronSvg();' +
      'b.appendChild(icon);b.appendChild(t);b.appendChild(chev);b.addEventListener("click",function(){onPick(a);});container.appendChild(b);});}' +

      'function loadArticles(){if(articlesCache){renderArticleRows(articlesList,articlesCache,openArticle,docSvg(),"No articles yet.");return;}' +
      'loadingState(articlesList,"Loading articles…");' +
      'fetchJson(apiBase+"/articles/?token="+encodeURIComponent(token)).then(function(data){' +
      'articlesCache=data.articles||[];renderArticleRows(articlesList,articlesCache,openArticle,docSvg(),"No articles yet.");' +
      '}).catch(function(){emptyState(articlesList,docSvg(),"Could not load articles.");});}' +

      'function openArticle(a){articleTitleEl.textContent=a.title;articleContentEl.textContent=a.content;' +
      'articlesList.style.display="none";articleDetail.style.display="flex";}' +
      'articleBack.addEventListener("click",function(){articleDetail.style.display="none";articlesList.style.display="block";});' +

      'function openSearchResult(a){searchDetailTitle.textContent=a.title;searchDetailContent.textContent=a.content;' +
      'searchResults.style.display="none";searchDetail.style.display="flex";}' +
      'searchBack.addEventListener("click",function(){searchDetail.style.display="none";searchResults.style.display="block";});' +

      'function runSearch(q){if(!q){emptyState(searchResults,searchSvg(),"Start typing to search help articles.");return;}' +
      'loadingState(searchResults,"Searching…");' +
      'fetchJson(apiBase+"/search/?token="+encodeURIComponent(token)+"&q="+encodeURIComponent(q)).then(function(data){' +
      'renderArticleRows(searchResults,data.results||[],openSearchResult,searchSvg(),"No results for \\""+q+"\\".");' +
      '}).catch(function(){emptyState(searchResults,searchSvg(),"Search is unavailable right now.");});}' +
      'searchInput.addEventListener("input",function(){var q=searchInput.value.trim();clearTimeout(searchTimer);' +
      'searchTimer=setTimeout(function(){runSearch(q);},300);});' +
      'runSearch("");' +

      'function fmtTime(){var d=new Date(),h=d.getHours(),m=d.getMinutes(),ap=h>=12?"PM":"AM";h=h%12||12;return h+":"+(m<10?"0":"")+m+" "+ap;}' +
      'function miniAv(){if(cfg.avatar_url){var i=document.createElement("img");i.className="lb-mini";i.src=cfg.avatar_url;i.alt="";return i;}' +
      'var s=document.createElement("span");s.className="lb-mini";s.textContent=initial;return s;}' +

      'function isMobile(){try{return window.parent.matchMedia("(max-width:768px)").matches;}catch(e){return window.matchMedia("(max-width:768px)").matches;}}' +
      'function syncChrome(){var open=document.body.classList.contains("open"),expanded=document.body.classList.contains("expanded");' +
      'document.body.className=(open?"open":expanded?"expanded":"closed")+(isMobile()?" mobile":"");}' +
      'syncChrome();window.addEventListener("resize",syncChrome);' +
      'try{if(window.parent&&window.parent.visualViewport){window.parent.visualViewport.addEventListener("resize",syncChrome);}}catch(e){}' +

      'function fetchJson(url,opts){return fetch(url,Object.assign({mode:"cors",credentials:"omit"},opts||{})).then(function(r){' +
      'return r.json().then(function(data){if(!r.ok)throw new Error((data&&data.error)||("HTTP "+r.status));return data;});});}' +

      'function setExpanded(){document.body.classList.remove("closed");document.body.classList.add("expanded");syncChrome();' +
      'window.frameElement.dispatchEvent(new CustomEvent("lb-expand"));}' +
      'function setOpen(v){document.body.classList.toggle("open",!!v);document.body.classList.toggle("closed",!v);document.body.classList.remove("expanded");syncChrome();' +
      'window.frameElement.dispatchEvent(new CustomEvent("lb-toggle",{detail:{open:!!v}}));' +
      'if(v){if(!isMobile())input.focus();if(humanMode)startPolling();}}' +
      'launcher.addEventListener("click",function(){setExpanded();});' +
      'preview.addEventListener("click",function(){setOpen(true);});' +
      'preview.addEventListener("keydown",function(e){if(e.key==="Enter"||e.key===" "){e.preventDefault();setOpen(true);}});' +
      'closeBtn.addEventListener("click",function(){setOpen(false);});' +
      'if(moreBtn&&moreMenu){moreBtn.addEventListener("click",function(e){e.stopPropagation();moreMenu.classList.toggle("open");});' +
      'document.addEventListener("click",function(){moreMenu.classList.remove("open");});}' +
      'var freshBtn=document.getElementById("lbFresh");' +
      'if(freshBtn)freshBtn.addEventListener("click",function(){sessionId=null;localStorage.removeItem(sessionKey);moreMenu.classList.remove("open");' +
      'messages.innerHTML="";var day=document.createElement("div");day.className="lb-day";day.innerHTML="<span>Today</span>";messages.appendChild(day);' +
      'addMsg("them",cfg.greeting||("Hi, I am "+cfg.name+"."));});' +

      'function addMsg(kind,text){var mine=kind==="you",sys=kind==="system",human=kind==="human",typing=text==="…";' +
      'var last=messages.lastElementChild&&messages.lastElementChild.querySelector(".lb-bubble");' +
      'if(!mine&&!typing&&last&&last.textContent===text)return last;' +
      'var row=document.createElement("div");row.className="lb-row "+(mine?"lb-row--you":"lb-row--them");' +
      'if(!mine&&!sys)row.appendChild(miniAv());' +
      'var wrap=document.createElement("div");wrap.className="lb-msg";' +
      'var b=document.createElement("div");' +
      'b.className="lb-bubble "+(mine?"lb-bubble--you":sys?"lb-bubble--sys":human?"lb-bubble--human":"lb-bubble--them");' +
      'if(typing){b.className="lb-bubble lb-bubble--them lb-bubble--typing";b.innerHTML="<span></span><span></span><span></span>";}' +
      'else{b.textContent=text;}' +
      'wrap.appendChild(b);' +
      'if(!typing){var meta=document.createElement("div");meta.className="lb-meta"+(mine?" lb-meta--you":"");' +
      'if(mine){meta.textContent=fmtTime()+" ";var ticks=document.createElement("span");ticks.className="lb-ticks";ticks.textContent="✓✓";meta.appendChild(ticks);}' +
      'else{meta.textContent=fmtTime();}' +
      'wrap.appendChild(meta);}' +
      'row.appendChild(wrap);messages.appendChild(row);messages.scrollTop=messages.scrollHeight;return b;}' +

      'function revealReply(node,text){if(!node)return;node.classList.remove("lb-bubble--typing");node.textContent=text||"";' +
      'var wrap=node.parentNode;if(wrap&&!wrap.querySelector(".lb-meta")){var meta=document.createElement("div");meta.className="lb-meta";meta.textContent=fmtTime();wrap.appendChild(meta);}}' +

      'function pill(solid){return "lb-pill "+(solid?"lb-pill-solid":"lb-pill-outline");}' +

      'function renderActions(){actions.innerHTML="";(cfg.actions||[]).forEach(function(a){' +
      'var b=document.createElement("button");b.type="button";b.className=pill(false);b.textContent=a.label;' +
      'b.addEventListener("click",function(){runAction(a.id);});actions.appendChild(b);});}' +

      'function runAction(id){if(id==="collect_contact")return showContactForm();' +
      'if(id==="schedule")return showSchedulePicker();' +
      'if(id==="handoff")postAction("handoff",{note:"Visitor requested to talk to the team"});}' +

      'function postAction(action,data){var node=addMsg("them","…");' +
      'fetchJson(apiBase+"/action/",{method:"POST",headers:{"Content-Type":"application/json"},' +
      'body:JSON.stringify({token:token,action:action,visitor_id:visitorId,session_id:sessionId,data:data||{}})})' +
      '.then(function(res){if(res.session_id){sessionId=res.session_id;localStorage.setItem(sessionKey,sessionId);}' +
      'revealReply(node,res.message||"Done.");if(res.request_takeover){humanMode=true;' +
      'statusEl.textContent="Connecting you to a teammate…";startPolling();}})' +
      '.catch(function(err){revealReply(node,err.message||"Could not complete that action.");});}' +

      'function showContactForm(){var wrap=document.createElement("div");wrap.className="lb-card";' +
      'var title=document.createElement("div");title.style.cssText="font-size:12px;font-weight:650;margin-bottom:8px";' +
      'title.textContent="Share your details";wrap.appendChild(title);' +
      'var nameI=document.createElement("input");nameI.className="lb-field";nameI.placeholder="Name";' +
      'var emailI=document.createElement("input");emailI.className="lb-field";emailI.placeholder="Email";' +
      'var phoneI=document.createElement("input");phoneI.className="lb-field";phoneI.placeholder="Phone";' +
      'var go=document.createElement("button");go.type="button";go.className=pill(true);go.style.marginTop="6px";go.textContent="Send to team";' +
      'go.addEventListener("click",function(){wrap.remove();postAction("collect_contact",{name:nameI.value,email:emailI.value,phone:phoneI.value});});' +
      'wrap.appendChild(nameI);wrap.appendChild(emailI);wrap.appendChild(phoneI);wrap.appendChild(go);' +
      'messages.appendChild(wrap);messages.scrollTop=messages.scrollHeight;}' +

      'function showSchedulePicker(){var wrap=document.createElement("div");wrap.className="lb-card";' +
      'var title=document.createElement("div");title.style.cssText="font-size:12px;font-weight:650;margin-bottom:8px";' +
      'title.textContent="Pick a time";wrap.appendChild(title);' +
      '(cfg.slots||[]).forEach(function(slot){var b=document.createElement("button");b.type="button";b.className="lb-slot";b.textContent=slot.label;' +
      'b.addEventListener("click",function(){wrap.remove();postAction("schedule",{slot_id:slot.id,label:slot.label,starts_at:slot.starts_at});});' +
      'wrap.appendChild(b);});' +
      'if(!(cfg.slots||[]).length){var e=document.createElement("div");e.style.cssText="font-size:12px;color:#6B7280";e.textContent="No slots available right now.";wrap.appendChild(e);}' +
      'messages.appendChild(wrap);messages.scrollTop=messages.scrollHeight;}' +

      'function startPolling(){if(pollTimer)return;pollTimer=setInterval(function(){if(!sessionId)return;' +
      'fetchJson(apiBase+"/poll/?token="+encodeURIComponent(token)+"&session_id="+sessionId+"&after_id="+lastMsgId)' +
      '.then(function(data){humanMode=!!data.human_mode;if(humanMode)statusEl.textContent="Talking with a teammate";' +
      '(data.messages||[]).forEach(function(m){lastMsgId=Math.max(lastMsgId,m.id);if(seenIds[m.id])return;seenIds[m.id]=1;' +
      'var kind=m.role==="human"?"human":(m.role==="system"?"system":"them");addMsg(kind,m.content);});})' +
      '.catch(function(){});},2500);}' +

      'function sendMessage(text,continueLast){if(!text||sending)return;sending=true;if(sendBtn)sendBtn.disabled=true;' +
      'addMsg("you",text);var node=addMsg("them","…");' +
      'fetchJson(apiBase+"/message/",{method:"POST",headers:{"Content-Type":"application/json"},' +
      'body:JSON.stringify({token:token,message:text,visitor_id:visitorId,session_id:sessionId,stream:false,continue_last:!!continueLast})})' +
      '.then(function(data){if(data.session_id){sessionId=data.session_id;localStorage.setItem(sessionKey,sessionId);}' +
      'if(data.message_id){lastMsgId=Math.max(lastMsgId,data.message_id);seenIds[data.message_id]=1;}' +
      'if(data.human_mode){humanMode=true;revealReply(node,data.message||"A teammate will reply shortly.");' +
      'statusEl.textContent="Talking with a teammate";startPolling();return;}' +
      'revealReply(node,data.reply||"No reply.");' +
      'if(data.actions&&data.actions.tasks_created&&data.actions.tasks_created.length)addMsg("system","✓ "+data.actions.tasks_created[0].title);})' +
      '.catch(function(){revealReply(node,"Sorry, I could not reply right now.");})' +
      '.then(function(){sending=false;if(sendBtn)sendBtn.disabled=false;});}' +

      'var resumeEl=document.getElementById("lbResume");' +
      'if(resumeEl&&cfg.returning_visitor&&cfg.resume_message){resumeEl.textContent=cfg.resume_message;' +
      'var acts=document.createElement("div");acts.className="lb-resume-actions";' +
      'var cont=document.createElement("button");cont.type="button";cont.className=pill(true);cont.textContent="Continue";' +
      'var fresh=document.createElement("button");fresh.type="button";fresh.className=pill(false);fresh.textContent="Start fresh";' +
      'cont.addEventListener("click",function(){sendMessage("Yes, let\\u2019s continue where we left off.",true);resumeEl.remove();});' +
      'fresh.addEventListener("click",function(){sessionId=null;localStorage.removeItem(sessionKey);resumeEl.remove();addMsg("system","Starting a fresh conversation.");});' +
      'acts.appendChild(cont);acts.appendChild(fresh);resumeEl.appendChild(acts);}' +

      'form.addEventListener("submit",function(e){e.preventDefault();var text=(input.value||"").trim();input.value="";sendMessage(text,false);});' +

      'var day=document.createElement("div");day.className="lb-day";day.innerHTML="<span>Today</span>";messages.appendChild(day);' +
      'addMsg("them",cfg.greeting||("Hi, I am "+cfg.name+"."));renderActions();' +
      '})();';
  }

  /* ============================================================
     4. ROSTER PICKER  (multi-employee workspace)
     ============================================================ */
  function mountRoster(roster) {
    var color = roster.brand_color || '#7C3AED';
    var shell = createShell('liftbot-roster-shell');

    function reposition() {
      if (isMobileViewport()) {
        shell.place({
          left: '12px',
          right: '12px',
          width: 'auto',
          bottom: offsetY + 'px',
          height: Math.min(window.innerHeight * 0.5, 320) + 'px',
        });
      } else {
        shell.place(anchorStyle({
          width: '320px',
          bottom: offsetY + 'px',
          height: Math.min(window.innerHeight - 40, 60 + roster.employees.length * 62) + 'px',
        }));
      }
    }
    reposition();
    window.addEventListener('resize', reposition);

    shell.iframe.addEventListener('lb-pick', function (e) {
      shell.destroy();
      window.removeEventListener('resize', reposition);
      startWithEmployee(e.detail.token);
    });

    var rows = roster.employees.map(function (emp) {
      return '<button type="button" class="lb-emp" data-token="' + escapeAttr(emp.token) + '">' +
        '<div class="lb-emp-name">' + escapeHtml(emp.name) + '</div>' +
        '<div class="lb-emp-role">' + escapeHtml(emp.role) + '</div></button>';
    }).join('');

    var html = '<!doctype html><html><head><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width,initial-scale=1"><style>' +
      '*{box-sizing:border-box}html,body{margin:0;height:100%;width:100%;font-family:"Plus Jakarta Sans",system-ui,sans-serif;overflow:hidden;}' +
      'body{display:flex;flex-direction:column;background:#fff;border-radius:16px;box-shadow:0 18px 50px rgba(0,0,0,.22);overflow:hidden;}' +
      '.lb-head{background:' + color + ';color:#fff;padding:14px 16px;font-weight:700;font-size:14px;flex-shrink:0;}' +
      '.lb-list{padding:10px;overflow-y:auto;flex:1;}' +
      '.lb-emp{display:block;width:100%;text-align:left;border:1px solid #E5E7EB;border-radius:10px;padding:10px 12px;' +
      'margin-bottom:8px;cursor:pointer;background:#fff;font-family:inherit;}' +
      '.lb-emp-name{font-weight:650;font-size:13px;}.lb-emp-role{font-size:11px;color:#6B7280;margin-top:2px;}' +
      '</style></head><body>' +
      '<div class="lb-head">' + escapeHtml(roster.workspace || '') + ' — Choose your AI Employee</div>' +
      '<div class="lb-list">' + rows + '</div>' +
      '<script>document.querySelectorAll(".lb-emp").forEach(function(b){b.addEventListener("click",function(){' +
      'window.frameElement.dispatchEvent(new CustomEvent("lb-pick",{detail:{token:b.getAttribute("data-token")}}));});});<\/script>' +
      '</body></html>';

    shell.write(html);
  }

  /* ============================================================
     5. tiny escaping helpers (defensive, since we build HTML
     strings from server-provided cfg fields)
     ============================================================ */
  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function escapeAttr(s) { return escapeHtml(s); }
})();
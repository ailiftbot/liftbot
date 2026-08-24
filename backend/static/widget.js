(function () {
  'use strict';

  /* ============================================================
     1. SCRIPT / CONFIG DISCOVERY
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
     2. ISOLATED SHELL (iframe container)
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
     3. CHAT WIDGET — Simple circular launcher
     ============================================================ */
  function mountEmployee(cfg) {
    var token = cfg._token;
    var color = cfg.brand_color || '#7C3AED';
    var sessionId = localStorage.getItem('liftbot_session_' + sessionKeyPrefix + '_' + token) || null;
    if (document.getElementById('liftbot-shell-' + token)) return;
    var shell = createShell('liftbot-shell-' + token);
    var open = false;

    function closedRect() {
      // CIRCLE BUTTON (56x56)
      return anchorStyle({
        width: '56px',
        height: '56px',
        bottom: offsetY + 'px',
        borderRadius: '50%',
        boxShadow: '0 8px 24px rgba(15,23,42,.2)',
        overflow: 'hidden',
        pointerEvents: 'auto'
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
          overflow: 'hidden'
        };
      }
      var h = Math.min(650, Math.max(600, window.innerHeight - 60));
      return anchorStyle({
        width: '400px',
        height: h + 'px',
        bottom: offsetY + 'px',
        borderRadius: '24px',
        boxShadow: '0 24px 64px rgba(15,23,42,.28)',
        overflow: 'hidden'
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
      reposition();
    });

    shell.write(employeeDocHtml(cfg, token, apiBase, visitorId, sessionKeyPrefix, sessionId, color));
  }

  function employeeDocHtml(cfg, token, apiBase, visitorId, sessionKeyPrefix, sessionId, color) {
    var initial = escapeHtml((cfg.name || 'AI').slice(0, 1).toUpperCase());

    return '<!doctype html><html><head><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">' +
      '<style>' +
      '*{box-sizing:border-box}' +
      'html,body{margin:0;height:100%;width:100%;overflow:hidden;font-family:"Plus Jakarta Sans",ui-sans-serif,system-ui,sans-serif;background:transparent!important;}' +
      'body{display:flex;flex-direction:column;justify-content:flex-end;align-items:flex-end;}' +
      '.lb-launcher{all:unset;display:flex;align-items:center;justify-content:center;width:56px;height:56px;border-radius:50%;' +
      'background:' + color + ';color:#fff;font-weight:800;font-size:20px;cursor:pointer;box-shadow:0 8px 24px rgba(0,0,0,.2);pointer-events:auto;}' +
      'body.open .lb-launcher{display:none;}' +
      '.lb-panel{display:none;position:relative;flex-direction:column;width:100%;height:100%;background:#f3f4f6;border-radius:24px;overflow:hidden}' +
      'body.open{align-items:stretch}' +
      'body.open .lb-panel{display:flex;flex:1;min-height:0;height:100%;width:100%;margin:0}' +
      'body.open.mobile .lb-panel{border-radius:0}' +
      '.lb-header{background:' + color + ';color:#fff;padding:12px 16px;display:flex;gap:12px;align-items:center;flex-shrink:0}' +
      '.lb-avatar{position:relative;width:44px;height:44px;flex-shrink:0}' +
      '.lb-avatar-img,.lb-avatar-fallback{width:44px;height:44px;border-radius:50%;object-fit:cover;display:grid;place-items:center;background:rgba(255,255,255,.18);font-weight:800;font-size:18px;color:#fff}' +
      '.lb-online{position:absolute;right:1px;bottom:1px;width:11px;height:11px;background:#22c55e;border:2px solid ' + color + ';border-radius:50%}' +
      '.lb-head-copy{min-width:0;flex:1}' +
      '.lb-name{font-weight:800;font-size:16px;letter-spacing:-.02em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
      '.lb-status{font-size:12.5px;opacity:.88;margin-top:2px;font-weight:500}' +
      '.lb-head-actions{display:flex;align-items:center;gap:2px;flex-shrink:0}' +
      '.lb-iconbtn{border:0;background:transparent;color:#fff;width:40px;height:40px;border-radius:12px;cursor:pointer;font-size:20px;line-height:1;padding:0;display:grid;place-items:center;opacity:.95}' +
      '.lb-iconbtn:hover{background:rgba(255,255,255,.12)}' +
      '.lb-messages{flex:1;min-height:0;overflow-y:auto;padding:14px;background:#f3f4f6;-webkit-overflow-scrolling:touch}' +
      '.lb-row{margin-bottom:14px;display:flex;align-items:flex-end;gap:8px}' +
      '.lb-row--you{justify-content:flex-end}' +
      '.lb-row--them{justify-content:flex-start}' +
      '.lb-mini-av{width:28px;height:28px;border-radius:50%;flex-shrink:0;object-fit:cover;display:grid;place-items:center;background:' + color + ';color:#fff;font-size:11px;font-weight:800}' +
      '.lb-msg{max-width:78%;display:flex;flex-direction:column}' +
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
      '.lb-form{display:flex;align-items:center;gap:8px;padding:10px 12px calc(12px + env(safe-area-inset-bottom,0px));background:#fff;border-top:1px solid #eceff3;flex-shrink:0}' +
      '.lb-input-wrap{flex:1;min-width:0;display:flex;align-items:center;gap:8px;border:1px solid #e5e7eb;background:#f8fafc;border-radius:22px;padding:0 6px 0 14px;transition:border-color .15s ease,box-shadow .15s ease;}' +
      '.lb-input-wrap:focus-within{border-color:' + color + ';background:#fff;box-shadow:0 0 0 3px rgba(124,58,237,.12)}' +
      '.lb-input{flex:1;min-width:0;border:0;background:transparent;padding:12px 0;outline:none;font-size:16px;font-family:inherit}' +
      '.lb-send{border:0;border-radius:50%;background:' + color + ';color:#fff;width:38px;height:38px;cursor:pointer;flex-shrink:0;display:grid;place-items:center}' +
      '.lb-send:disabled{opacity:.55}' +
      '.lb-send svg{width:16px;height:16px}' +
      '</style></head><body class="closed">' +
      '<button type="button" class="lb-launcher" id="lbLauncher" aria-label="Open chat">' + initial + '</button>' +
      '<div class="lb-panel">' +
      '<div class="lb-header">' +
      '<div class="lb-avatar">' +
      (cfg.avatar_url ? '<img class="lb-avatar-img" src="' + escapeAttr(cfg.avatar_url) + '">' : '<span class="lb-avatar-fallback">' + initial + '</span>') +
      '<span class="lb-online" aria-hidden="true"></span></div>' +
      '<div class="lb-head-copy"><div class="lb-name">' + escapeHtml(cfg.name || '') + '</div>' +
      '<div class="lb-status" id="lbStatus">Online • ' + escapeHtml(cfg.role || 'AI Employee') + '</div></div>' +
      '<div class="lb-head-actions">' +
      '<button type="button" class="lb-iconbtn" id="lbClose" aria-label="Close">&times;</button>' +
      '</div></div>' +
      '<div class="lb-messages" id="lbMessages"></div>' +
      '<form class="lb-form" id="lbForm">' +
      '<div class="lb-input-wrap"><input class="lb-input" id="lbInput" type="text" placeholder="Ask anything…" autocomplete="off"></div>' +
      '<button class="lb-send" id="lbSend" type="submit" aria-label="Send">' +
      '<svg viewBox="0 0 24 24" fill="none"><path d="M12 19V5M5 12l7-7 7 7" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
      '</button></form>' +
      '</div>' +
      '<script>window.__LB__=' + safeJSON({
        cfg: cfg, token: token, apiBase: apiBase, visitorId: visitorId, color: color,
        sessionKey: 'liftbot_session_' + sessionKeyPrefix + '_' + token, sessionId: sessionId,
      }) + ';<\/script>' +
      '<script>' + employeeInnerScript() + '<\/script>' +
      '</body></html>';
  }

  // Runs INSIDE the iframe document
  function employeeInnerScript() {
    return '(function(){' +
      'var D=window.__LB__,cfg=D.cfg,token=D.token,apiBase=D.apiBase,visitorId=D.visitorId,sessionKey=D.sessionKey;' +
      'var sessionId=D.sessionId,humanMode=false,lastMsgId=0,pollTimer=null,sending=false,seenIds={},started=false;' +
      'var messages=document.getElementById("lbMessages");' +
      'var statusEl=document.getElementById("lbStatus"),launcher=document.getElementById("lbLauncher");' +
      'var closeBtn=document.getElementById("lbClose"),form=document.getElementById("lbForm"),input=document.getElementById("lbInput");' +
      'var sendBtn=document.getElementById("lbSend");' +
      'var initial=((cfg.name||"AI").slice(0,1)||"A").toUpperCase();' +

      'function isMobile(){try{return window.parent.matchMedia("(max-width:768px)").matches;}catch(e){return window.matchMedia("(max-width:768px)").matches;}}' +
      'function syncChrome(){var open=document.body.classList.contains("open");' +
      'document.body.className=(open?"open":"closed")+(isMobile()?" mobile":"");}' +
      'syncChrome();window.addEventListener("resize",syncChrome);' +
      'try{if(window.parent&&window.parent.visualViewport){window.parent.visualViewport.addEventListener("resize",syncChrome);}}catch(e){}' +

      'function fetchJson(url,opts){return fetch(url,Object.assign({mode:"cors",credentials:"omit"},opts||{})).then(function(r){' +
      'return r.json().then(function(data){if(!r.ok)throw new Error((data&&data.error)||("HTTP "+r.status));return data;});});}' +

      'function setOpen(v){document.body.classList.toggle("open",!!v);document.body.classList.toggle("closed",!v);syncChrome();' +
      'window.frameElement.dispatchEvent(new CustomEvent("lb-toggle",{detail:{open:!!v}}));' +
      'if(v){if(!isMobile())input.focus();if(humanMode)startPolling();}}' +
      'launcher.addEventListener("click",function(){setOpen(true);});' +
      'closeBtn.addEventListener("click",function(){setOpen(false);});' +

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
      'if(!typing){var meta=document.createElement("div");meta.className="lb-meta";meta.textContent=new Date().toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"});wrap.appendChild(meta);}' +
      'row.appendChild(wrap);messages.appendChild(row);messages.scrollTop=messages.scrollHeight;return b;}' +

      'function miniAv(){if(cfg.avatar_url){var i=document.createElement("img");i.className="lb-mini-av";i.src=cfg.avatar_url;i.alt="";return i;}' +
      'var s=document.createElement("span");s.className="lb-mini-av";s.textContent=initial;return s;}' +

      'function revealReply(node,text){if(!node)return;node.classList.remove("lb-bubble--typing");node.textContent=text||"";}' +

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

      'function startPolling(){if(pollTimer)return;pollTimer=setInterval(function(){if(!sessionId)return;' +
      'fetchJson(apiBase+"/poll/?token="+encodeURIComponent(token)+"&session_id="+sessionId+"&after_id="+lastMsgId)' +
      '.then(function(data){humanMode=!!data.human_mode;if(humanMode)statusEl.textContent="Talking with a teammate";' +
      '(data.messages||[]).forEach(function(m){lastMsgId=Math.max(lastMsgId,m.id);if(seenIds[m.id])return;seenIds[m.id]=1;' +
      'var kind=m.role==="human"?"human":(m.role==="system"?"system":"them");addMsg(kind,m.content);});})' +
      '.catch(function(){});},2500);}' +

      'form.addEventListener("submit",function(e){e.preventDefault();var text=(input.value||"").trim();input.value="";sendMessage(text,false);});' +

      'var day=document.createElement("div");day.className="lb-day";day.innerHTML="<span>Today</span>";messages.appendChild(day);' +
      'addMsg("them",cfg.greeting||("Hi, I am "+cfg.name+"."));' +
      '})();';
  }

  /* ============================================================
     4. ROSTER PICKER (multi-employee) — unchanged
     ============================================================ */
  function mountRoster(roster) {
    var color = roster.brand_color || '#7C3AED';
    var shell = createShell('liftbot-roster-shell');

    function reposition() {
      if (isMobileViewport()) {
        shell.place({
          left: '12px', right: '12px', width: 'auto',
          bottom: offsetY + 'px', height: Math.min(window.innerHeight * 0.5, 320) + 'px'
        });
      } else {
        shell.place(anchorStyle({
          width: '320px', bottom: offsetY + 'px',
          height: Math.min(window.innerHeight - 40, 60 + roster.employees.length * 62) + 'px'
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
      '.lb-emp{display:block;width:100%;text-align:left;border:1px solid #E5E7EB;border-radius:10px;padding:10px 12px;margin-bottom:8px;cursor:pointer;background:#fff;font-family:inherit;}' +
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
     5. tiny escaping helpers
     ============================================================ */
  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function escapeAttr(s) { return escapeHtml(s); }
})();
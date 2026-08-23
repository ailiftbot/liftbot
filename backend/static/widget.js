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
    return window.matchMedia && window.matchMedia('(max-width: 640px)').matches;
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
    var fabSize = function () { return isMobileViewport() ? 56 : 60; };

    function closedRect() {
      var size = fabSize();
      // Shadow on the circular host shell — not inside the square iframe.
      return anchorStyle({
        width: size + 'px',
        height: size + 'px',
        bottom: offsetY + 'px',
        borderRadius: '50%',
        boxShadow: '0 8px 24px rgba(15,23,42,.22)',
        overflow: 'visible',
      });
    }

    function openRect() {
      var reset = { borderRadius: '16px', boxShadow: 'none', overflow: 'hidden' };
      if (isMobileViewport()) {
        var vv = window.visualViewport;
        var layoutH = window.innerHeight;
        var vh = vv ? vv.height : layoutH;
        var vTop = vv ? Math.round(vv.offsetTop) : 0;
        var inset = 8;
        var usable = Math.max(240, Math.round(vh - inset * 2));
        var keyboardOpen = vh < layoutH * 0.82;
        var height = keyboardOpen ? usable : Math.min(usable, Math.round(vh * 0.7));
        var top = keyboardOpen
          ? (vTop + inset)
          : (vTop + vh - height - inset);
        return Object.assign({
          left: inset + 'px',
          right: inset + 'px',
          width: 'auto',
          top: top + 'px',
          height: height + 'px',
        }, reset);
      }
      return anchorStyle(Object.assign({
        width: '380px',
        height: '600px',
        bottom: (offsetY + 68) + 'px',
      }, reset));
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
    return '<!doctype html><html><head><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<style>' +
      '*{box-sizing:border-box}' +
      'html,body{margin:0;height:100%;width:100%;overflow:hidden;font-family:"Plus Jakarta Sans",system-ui,sans-serif;background:transparent!important;pointer-events:none;color-scheme:normal;}' +
      'body{display:flex;flex-direction:column;justify-content:flex-end;align-items:flex-end;}' +
      '.lb-launcher,.lb-panel{pointer-events:auto}' +
      '.lb-launcher{all:unset;display:flex;align-items:center;justify-content:center;width:100%;height:100%;' +
      'border-radius:50%;cursor:pointer;color:#fff;font-size:15px;font-weight:700;pointer-events:auto;' +
      'box-shadow:none;background:' + color + ';}' +
      '.lb-panel{display:none;flex-direction:column;width:100%;height:100%;background:#fff;border-radius:16px;' +
      'overflow:hidden;box-shadow:0 18px 50px rgba(0,0,0,.22);}' +
      'body.open{align-items:stretch}' +
      'body.open .lb-launcher{display:none!important}' +
      'body.open .lb-panel{display:flex;flex:1;min-height:0;height:100%;width:100%;margin:0}' +
      '.lb-header{background:' + color + ';color:#fff;padding:14px 16px;display:flex;gap:10px;align-items:center;flex-shrink:0;}' +
      '.lb-header img{width:40px;height:40px;border-radius:999px;object-fit:cover;}' +
      '.lb-name{font-weight:700;}' +
      '.lb-status{font-size:12px;opacity:.9;}' +
      '.lb-close{margin-left:auto;border:0;background:rgba(255,255,255,.18);color:#fff;width:30px;height:30px;' +
      'border-radius:999px;cursor:pointer;font-size:16px;line-height:1;padding:0;}' +
      '.lb-resume{padding:10px 12px;background:#EDE9FE;border-bottom:1px solid #DDD6FE;font-size:12px;color:#5B21B6;' +
      'line-height:1.4;flex-shrink:0;}' +
      '.lb-resume-actions{display:flex;gap:6px;margin-top:8px;}' +
      '.lb-messages{flex:1;min-height:0;overflow-y:auto;padding:14px;background:#f7faf9;-webkit-overflow-scrolling:touch;}' +
      '.lb-row{margin-bottom:8px;}' +
      '.lb-bubble{display:inline-block;max-width:85%;padding:8px 12px;border-radius:16px;font-size:14px;line-height:1.4;' +
      'border:1px solid #e5e7eb;background:#fff;color:#0B1220;}' +
      '.lb-actions{padding:8px 10px;border-top:1px solid #E5E7EB;display:flex;flex-wrap:wrap;gap:6px;background:#fff;flex-shrink:0;}' +
      '.lb-form{display:flex;align-items:center;gap:8px;padding:10px;' +
      'border-top:1px solid #e5e7eb;background:#fff;flex-shrink:0;}' +
      '.lb-input{flex:1;min-width:0;border:1px solid #d1d5db;border-radius:999px;padding:10px 14px;outline:none;font-size:16px;}' +
      '.lb-send{border:0;border-radius:999px;background:' + color + ';color:#fff;padding:0 16px;min-height:40px;cursor:pointer;flex-shrink:0;}' +
      '.lb-pill{border-radius:999px;padding:5px 12px;font-size:11px;cursor:pointer;font-weight:600;font-family:inherit;}' +
      '.lb-pill-solid{border:0;background:' + color + ';color:#fff;}' +
      '.lb-pill-outline{border:1px solid #C4B5FD;background:#fff;color:#5B21B6;}' +
      '.lb-field{display:block;width:100%;margin-bottom:6px;border:1px solid #D1D5DB;border-radius:8px;padding:8px 10px;font-size:13px;}' +
      '.lb-card{margin-bottom:8px;padding:10px;background:#fff;border:1px solid #E5E7EB;border-radius:12px;}' +
      '.lb-slot{display:block;width:100%;text-align:left;margin-bottom:6px;border:1px solid #DDD6FE;border-radius:8px;' +
      'padding:8px 10px;background:#F5F3FF;cursor:pointer;font-size:12px;font-family:inherit;}' +
      '</style></head><body class="closed">' +
      '<button type="button" class="lb-launcher" id="lbLauncher" aria-label="Open chat">' +
      escapeHtml((cfg.name || 'AI').slice(0, 1).toUpperCase()) +
      '</button>' +
      '<div class="lb-panel">' +
      '<div class="lb-header">' +
      (cfg.avatar_url ? '<img src="' + escapeAttr(cfg.avatar_url) + '" alt="">' : '') +
      '<div><div class="lb-name">' + escapeHtml(cfg.name || '') + '</div>' +
      '<div class="lb-status" id="lbStatus">' + escapeHtml((cfg.role || '') + ' · AI Employee') + '</div></div>' +
      '<button type="button" class="lb-close" id="lbClose" aria-label="Close chat">&times;</button>' +
      '</div>' +
      (cfg.returning_visitor && cfg.resume_message ? '<div class="lb-resume" id="lbResume"></div>' : '') +
      '<div class="lb-messages" id="lbMessages"></div>' +
      '<div class="lb-actions" id="lbActions"></div>' +
      '<form class="lb-form" id="lbForm">' +
      '<input class="lb-input" id="lbInput" type="text" placeholder="Message ' + escapeAttr(cfg.name || '') + '…" autocomplete="off">' +
      '<button class="lb-send" id="lbSend" type="submit">Send</button>' +
      '</form>' +
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
      'var statusEl=document.getElementById("lbStatus"),launcher=document.getElementById("lbLauncher");' +
      'var closeBtn=document.getElementById("lbClose"),form=document.getElementById("lbForm"),input=document.getElementById("lbInput");' +
      'var sendBtn=document.getElementById("lbSend");' +

      'function isMobile(){try{return window.parent.matchMedia("(max-width:640px)").matches;}catch(e){return window.matchMedia("(max-width:640px)").matches;}}' +
      'function syncChrome(){var open=document.body.classList.contains("open");' +
      'document.body.className=(open?"open":"closed")+(isMobile()?" mobile":"");}' +
      'syncChrome();window.addEventListener("resize",syncChrome);' +
      'try{if(window.parent&&window.parent.visualViewport){window.parent.visualViewport.addEventListener("resize",syncChrome);}}catch(e){}' +

      'function fetchJson(url,opts){return fetch(url,Object.assign({mode:"cors",credentials:"omit"},opts||{})).then(function(r){' +
      'return r.json().then(function(data){if(!r.ok)throw new Error((data&&data.error)||("HTTP "+r.status));return data;});});}' +

      'function setOpen(v){document.body.classList.toggle("open",!!v);document.body.classList.toggle("closed",!v);syncChrome();' +
      'window.frameElement.dispatchEvent(new CustomEvent("lb-toggle",{detail:{open:!!v}}));' +
      'if(v){input.focus();if(humanMode)startPolling();}}' +
      'launcher.addEventListener("click",function(){setOpen(true);});' +
      'closeBtn.addEventListener("click",function(){setOpen(false);});' +

      'function addMsg(kind,text){var mine=kind==="you",sys=kind==="system",human=kind==="human";' +
      'var last=messages.lastElementChild&&messages.lastElementChild.querySelector(".lb-bubble");' +
      'if(!mine&&last&&last.textContent===text)return last;' +
      'var row=document.createElement("div");row.className="lb-row";row.style.textAlign=mine?"right":"left";' +
      'var b=document.createElement("div");b.className="lb-bubble";b.textContent=text;' +
      'b.style.background=sys?"#FEF3C7":human?"#DBEAFE":"#fff";' +
      'if(mine){b.style.background=D.color||"#7C3AED";b.style.color="#fff";b.style.border="0";}' +
      'row.appendChild(b);messages.appendChild(row);messages.scrollTop=messages.scrollHeight;return b;}' +

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
      'node.textContent=res.message||"Done.";if(res.request_takeover){humanMode=true;' +
      'statusEl.textContent="Connecting you to a teammate…";startPolling();}})' +
      '.catch(function(err){node.textContent=err.message||"Could not complete that action.";});}' +

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
      'if(data.human_mode){humanMode=true;node.textContent=data.message||"A teammate will reply shortly.";' +
      'statusEl.textContent="Talking with a teammate";startPolling();return;}' +
      'node.textContent=data.reply||"No reply.";' +
      'if(data.actions&&data.actions.tasks_created&&data.actions.tasks_created.length)addMsg("system","✓ "+data.actions.tasks_created[0].title);})' +
      '.catch(function(){node.textContent="Sorry, I could not reply right now.";})' +
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
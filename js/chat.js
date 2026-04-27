/* abalonecove.org — QA chat widget
 * No dependencies. Attach once via <script src="/js/chat.js"></script>.
 * Posts to /api/chat (Cloudflare Worker).
 */
(function () {
  'use strict';

  var API = '/api/chat';
  var history = [];

  // ── Styles ──────────────────────────────────────────────────────────────────
  var css = `
#acf-chat-btn {
  position: fixed;
  bottom: 28px;
  right: 24px;
  z-index: 9000;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: #2a7a7a;
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 12px rgba(0,0,0,0.18);
  transition: background 0.2s, transform 0.15s;
}
#acf-chat-btn:hover { background: #1f5e5e; transform: scale(1.06); }
#acf-chat-btn svg { width: 22px; height: 22px; fill: #f5f0e8; }

#acf-chat-panel {
  position: fixed;
  bottom: 88px;
  right: 24px;
  z-index: 9000;
  width: 340px;
  max-width: calc(100vw - 48px);
  max-height: 480px;
  background: #f5f0e8;
  border: 1px solid #c4a87a;
  border-radius: 14px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.14);
  display: none;
  flex-direction: column;
  font-family: Georgia, "Times New Roman", serif;
  font-size: 14px;
  overflow: hidden;
}
#acf-chat-panel.open { display: flex; }

#acf-chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px 10px;
  border-bottom: 1px solid #c4a87a;
  flex-shrink: 0;
}
#acf-chat-header span {
  font-size: 0.78rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #1a1a2e;
  opacity: 0.7;
}
#acf-chat-close {
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px 4px;
  color: #1a1a2e;
  opacity: 0.45;
  font-size: 18px;
  line-height: 1;
  transition: opacity 0.15s;
}
#acf-chat-close:hover { opacity: 0.9; }

#acf-chat-msgs {
  flex: 1;
  overflow-y: auto;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.acf-msg {
  line-height: 1.55;
  font-size: 0.88rem;
  max-width: 100%;
  word-wrap: break-word;
}
.acf-msg.user {
  align-self: flex-end;
  background: #2a7a7a;
  color: #f5f0e8;
  border-radius: 12px 12px 2px 12px;
  padding: 8px 12px;
  max-width: 82%;
}
.acf-msg.assistant {
  align-self: flex-start;
  color: #1a1a2e;
}
.acf-msg.assistant a { color: #2a7a7a; }

#acf-chat-form {
  display: flex;
  gap: 8px;
  padding: 10px 12px 12px;
  border-top: 1px solid #c4a87a;
  flex-shrink: 0;
}
#acf-chat-input {
  flex: 1;
  border: 1px solid #c4a87a;
  border-radius: 8px;
  padding: 7px 10px;
  font-family: Georgia, "Times New Roman", serif;
  font-size: 0.85rem;
  background: #fff;
  color: #1a1a2e;
  resize: none;
  line-height: 1.4;
  max-height: 80px;
  outline: none;
  transition: border-color 0.2s;
}
#acf-chat-input:focus { border-color: #2a7a7a; }
#acf-chat-send {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  border-radius: 8px;
  background: #2a7a7a;
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  align-self: flex-end;
  transition: background 0.2s;
}
#acf-chat-send:hover { background: #1f5e5e; }
#acf-chat-send:disabled { background: #9db8b8; cursor: default; }
#acf-chat-send svg { width: 16px; height: 16px; fill: #f5f0e8; }

.acf-typing {
  display: flex;
  gap: 4px;
  padding: 4px 0;
}
.acf-typing span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #2a7a7a;
  opacity: 0.4;
  animation: acf-bounce 1.2s infinite;
}
.acf-typing span:nth-child(2) { animation-delay: 0.2s; }
.acf-typing span:nth-child(3) { animation-delay: 0.4s; }
@keyframes acf-bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
  40% { transform: translateY(-5px); opacity: 1; }
}

#acf-chat-disclaimer {
  font-size: 0.7rem;
  color: #1a1a2e;
  opacity: 0.4;
  text-align: center;
  padding: 0 12px 8px;
  flex-shrink: 0;
}
`;

  var styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  // ── HTML ────────────────────────────────────────────────────────────────────
  var btn = document.createElement('button');
  btn.id = 'acf-chat-btn';
  btn.setAttribute('aria-label', 'Ask the Foundation');
  btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.03 2 11c0 2.7 1.2 5.12 3.1 6.83L4 22l4.44-1.48A10.5 10.5 0 0 0 12 21c5.52 0 10-4.03 10-9s-4.48-9-10-9zm1 13H7v-2h6v2zm3-4H7V9h9v2z"/></svg>';

  var panel = document.createElement('div');
  panel.id = 'acf-chat-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'Foundation research assistant');
  panel.innerHTML = `
    <div id="acf-chat-header">
      <span>Foundation Research</span>
      <button id="acf-chat-close" aria-label="Close">&times;</button>
    </div>
    <div id="acf-chat-msgs" role="log" aria-live="polite"></div>
    <div id="acf-chat-disclaimer">Answers draw from the Foundation&rsquo;s documented record only.</div>
    <form id="acf-chat-form" autocomplete="off">
      <textarea id="acf-chat-input" rows="1" placeholder="Ask about the evidence, the hypothesis, the map…" aria-label="Message"></textarea>
      <button id="acf-chat-send" type="submit" aria-label="Send">
        <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
      </button>
    </form>`;

  document.body.appendChild(btn);
  document.body.appendChild(panel);

  // ── Logic ───────────────────────────────────────────────────────────────────
  var msgs  = document.getElementById('acf-chat-msgs');
  var input = document.getElementById('acf-chat-input');
  var send  = document.getElementById('acf-chat-send');
  var form  = document.getElementById('acf-chat-form');

  btn.addEventListener('click', function () {
    panel.classList.toggle('open');
    if (panel.classList.contains('open')) {
      if (!msgs.children.length) appendWelcome();
      input.focus();
    }
  });

  document.getElementById('acf-chat-close').addEventListener('click', function () {
    panel.classList.remove('open');
  });

  function appendWelcome() {
    addMsg('assistant',
      'Ask me about the 24-inch line, the evidence library, the ten historical eras, ' +
      'documented gaps, or how to contribute a record. I answer from the Foundation\'s ' +
      'documented sources only.'
    );
  }

  function addMsg(role, text) {
    var el = document.createElement('div');
    el.className = 'acf-msg ' + role;
    el.textContent = text;
    msgs.appendChild(el);
    msgs.scrollTop = msgs.scrollHeight;
    return el;
  }

  function addTyping() {
    var el = document.createElement('div');
    el.className = 'acf-msg assistant';
    el.innerHTML = '<div class="acf-typing"><span></span><span></span><span></span></div>';
    msgs.appendChild(el);
    msgs.scrollTop = msgs.scrollHeight;
    return el;
  }

  // Auto-resize textarea
  input.addEventListener('input', function () {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 80) + 'px';
  });

  // Submit on Enter (Shift+Enter for newline)
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      form.dispatchEvent(new Event('submit', { cancelable: true }));
    }
  });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var msg = input.value.trim();
    if (!msg || send.disabled) return;

    addMsg('user', msg);
    input.value = '';
    input.style.height = 'auto';
    send.disabled = true;

    var typingEl = addTyping();

    history.push({ role: 'user', content: msg });

    fetch(API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, history: history.slice(-6) }),
    })
    .then(function (resp) {
      if (!resp.ok) {
        return resp.json().then(function (j) { throw new Error(j.error || 'Error ' + resp.status); });
      }
      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var replyEl = document.createElement('div');
      replyEl.className = 'acf-msg assistant';
      typingEl.replaceWith(replyEl);
      var accumulated = '';
      var buffer = '';

      function pump() {
        return reader.read().then(function (r) {
          if (r.done) {
            history.push({ role: 'assistant', content: accumulated });
            send.disabled = false;
            input.focus();
            return;
          }
          buffer += decoder.decode(r.value, { stream: true });
          var lines = buffer.split('\n');
          buffer = lines.pop();
          lines.forEach(function (line) {
            if (!line.startsWith('data: ')) return;
            var data = line.slice(6);
            if (data === '[DONE]') return;
            try {
              var ev = JSON.parse(data);
              if (ev.text) {
                accumulated += ev.text;
                replyEl.textContent = accumulated;
                msgs.scrollTop = msgs.scrollHeight;
              }
            } catch (_) {}
          });
          return pump();
        });
      }
      return pump();
    })
    .catch(function (err) {
      typingEl.remove();
      addMsg('assistant', 'Sorry — ' + err.message + '. Please try again.');
      send.disabled = false;
      input.focus();
    });
  });
})();

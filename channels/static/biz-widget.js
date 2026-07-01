/*
  Aura AI — Embeddable Business Chatbot Widget
  Usage (paste before </body> on the business's website):

  <script src="https://YOUR-DEPLOYMENT.up.railway.app/static/biz-widget.js"
          data-api="https://YOUR-DEPLOYMENT.up.railway.app"
          data-client-id="business-slug"
          data-bot-name="Bella Salon Assistant"
          data-color="#4F46E5"
          data-greeting="Hi! Ask me about our services, hours, or prices."
          data-persona="You are the assistant for Bella Salon, a hair & beauty salon in Asansol. Hours: 10am-8pm, closed Mondays. Be warm and concise."
  ></script>
*/
(function () {
  var scriptTag = document.currentScript;
  var API        = scriptTag.getAttribute("data-api") || "";
  var CLIENT_ID  = scriptTag.getAttribute("data-client-id") || "default";
  var BOT_NAME   = scriptTag.getAttribute("data-bot-name") || "Chat with us";
  var COLOR      = scriptTag.getAttribute("data-color") || "#4F46E5";
  var GREETING   = scriptTag.getAttribute("data-greeting") || "Hi! How can I help you today?";
  var PERSONA    = scriptTag.getAttribute("data-persona") || "";

  var STORAGE_KEY = "aura_widget_uid_" + CLIENT_ID;
  var userId = localStorage.getItem(STORAGE_KEY);
  if (!userId) {
    userId = "visitor_" + Math.random().toString(36).slice(2) + Date.now();
    localStorage.setItem(STORAGE_KEY, userId);
  }

  var css = "" +
    "#aura-bubble{position:fixed;bottom:22px;right:22px;width:60px;height:60px;border-radius:50%;" +
    "background:" + COLOR + ";box-shadow:0 6px 20px rgba(0,0,0,.25);cursor:pointer;z-index:999999;" +
    "display:flex;align-items:center;justify-content:center;transition:transform .15s ease;}" +
    "#aura-bubble:hover{transform:scale(1.06);}" +
    "#aura-bubble svg{width:28px;height:28px;fill:#fff;}" +
    "#aura-win{position:fixed;bottom:96px;right:22px;width:340px;max-width:90vw;height:460px;max-height:70vh;" +
    "background:#fff;border-radius:16px;box-shadow:0 10px 40px rgba(0,0,0,.25);display:none;flex-direction:column;" +
    "overflow:hidden;z-index:999999;font-family:Arial,Helvetica,sans-serif;}" +
    "#aura-win.open{display:flex;}" +
    "#aura-hdr{background:" + COLOR + ";color:#fff;padding:14px 16px;font-weight:600;font-size:15px;" +
    "display:flex;align-items:center;justify-content:space-between;}" +
    "#aura-hdr span.sub{display:block;font-weight:400;font-size:11px;opacity:.85;margin-top:2px;}" +
    "#aura-close{cursor:pointer;font-size:20px;line-height:1;background:none;border:none;color:#fff;padding:0 4px;}" +
    "#aura-msgs{flex:1;overflow-y:auto;padding:14px;background:#F7F8FA;display:flex;flex-direction:column;gap:10px;}" +
    ".aura-row{max-width:82%;padding:9px 13px;border-radius:12px;font-size:13.5px;line-height:1.45;word-wrap:break-word;}" +
    ".aura-bot{background:#fff;border:1px solid #E5E7EB;align-self:flex-start;color:#1F2937;}" +
    ".aura-user{background:" + COLOR + ";color:#fff;align-self:flex-end;}" +
    ".aura-typing{align-self:flex-start;background:#fff;border:1px solid #E5E7EB;border-radius:12px;padding:9px 13px;}" +
    ".aura-typing span{display:inline-block;width:6px;height:6px;border-radius:50%;background:#9CA3AF;margin:0 2px;" +
    "animation:aura-blink 1.2s infinite;}" +
    ".aura-typing span:nth-child(2){animation-delay:.2s;} .aura-typing span:nth-child(3){animation-delay:.4s;}" +
    "@keyframes aura-blink{0%,80%,100%{opacity:.3;}40%{opacity:1;}}" +
    "#aura-inputrow{display:flex;border-top:1px solid #E5E7EB;padding:8px;gap:8px;background:#fff;}" +
    "#aura-input{flex:1;border:1px solid #E5E7EB;border-radius:20px;padding:9px 14px;font-size:13.5px;outline:none;}" +
    "#aura-send{background:" + COLOR + ";border:none;color:#fff;border-radius:20px;padding:0 16px;font-size:13px;" +
    "font-weight:600;cursor:pointer;}" +
    "#aura-send:disabled{opacity:.5;cursor:default;}" +
    "#aura-badge{position:absolute;top:-2px;right:-2px;width:12px;height:12px;border-radius:50%;background:#EF4444;" +
    "border:2px solid #fff;}";
  var styleEl = document.createElement("style");
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  var bubble = document.createElement("div");
  bubble.id = "aura-bubble";
  bubble.innerHTML = '<svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zM6 9h12v2H6V9zm8 5H6v-2h8v2zm4-6H6V6h12v2z"/></svg>';
  document.body.appendChild(bubble);

  var win = document.createElement("div");
  win.id = "aura-win";
  win.innerHTML =
    '<div id="aura-hdr"><div>' + BOT_NAME + '<span class="sub">Usually replies instantly</span></div>' +
    '<button id="aura-close">✕</button></div>' +
    '<div id="aura-msgs"></div>' +
    '<div id="aura-inputrow">' +
    '<input id="aura-input" type="text" placeholder="Type a message..." />' +
    '<button id="aura-send">Send</button>' +
    "</div>";
  document.body.appendChild(win);

  var msgsEl  = win.querySelector("#aura-msgs");
  var inputEl = win.querySelector("#aura-input");
  var sendBtn = win.querySelector("#aura-send");
  var closeBtn = win.querySelector("#aura-close");
  var opened = false;

  function addMsg(text, who) {
    var row = document.createElement("div");
    row.className = "aura-row " + (who === "user" ? "aura-user" : "aura-bot");
    row.textContent = text;
    msgsEl.appendChild(row);
    msgsEl.scrollTop = msgsEl.scrollHeight;
    return row;
  }

  function showTyping() {
    var t = document.createElement("div");
    t.className = "aura-typing";
    t.id = "aura-typing-el";
    t.innerHTML = "<span></span><span></span><span></span>";
    msgsEl.appendChild(t);
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }
  function hideTyping() {
    var t = document.getElementById("aura-typing-el");
    if (t) t.remove();
  }

  function openWidget() {
    win.classList.add("open");
    if (!opened) {
      addMsg(GREETING, "bot");
      opened = true;
    }
    inputEl.focus();
  }
  bubble.addEventListener("click", function () {
    if (win.classList.contains("open")) {
      win.classList.remove("open");
    } else {
      openWidget();
    }
  });
  closeBtn.addEventListener("click", function () { win.classList.remove("open"); });

  async function sendMessage() {
    var text = inputEl.value.trim();
    if (!text) return;
    addMsg(text, "user");
    inputEl.value = "";
    sendBtn.disabled = true;
    showTyping();
    try {
      var res = await fetch(API + "/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: userId,
          message: text,
          client_id: CLIENT_ID,
          business_persona: PERSONA,
        }),
      });
      var data = await res.json();
      hideTyping();
      if (res.ok) {
        addMsg(data.response, "bot");
      } else {
        addMsg("Sorry, something went wrong. Please try again in a moment.", "bot");
      }
    } catch (e) {
      hideTyping();
      addMsg("Sorry, I couldn't connect. Please check your internet and try again.", "bot");
    }
    sendBtn.disabled = false;
    inputEl.focus();
  }

  sendBtn.addEventListener("click", sendMessage);
  inputEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter") sendMessage();
  });
})();

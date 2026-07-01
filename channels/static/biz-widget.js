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
          data-suggestions="What are your hours?|What services do you offer?|Do I need an appointment?"
  ></script>

  Rendered inside a Shadow DOM so the host page's CSS can never leak in.
  data-suggestions is a "|"-separated list of quick-reply chips shown after
  the greeting — clicking one sends it as a message automatically.
*/
(function () {
  var scriptTag = document.currentScript;
  var API         = scriptTag.getAttribute("data-api") || "";
  var CLIENT_ID   = scriptTag.getAttribute("data-client-id") || "default";
  var BOT_NAME    = scriptTag.getAttribute("data-bot-name") || "Chat with us";
  var COLOR       = scriptTag.getAttribute("data-color") || "#4F46E5";
  var GREETING    = scriptTag.getAttribute("data-greeting") || "Hi! How can I help you today?";
  var PERSONA     = scriptTag.getAttribute("data-persona") || "";
  var SUGGESTIONS = (scriptTag.getAttribute("data-suggestions") || "")
    .split("|").map(function (s) { return s.trim(); }).filter(Boolean);

  var STORAGE_KEY = "aura_widget_uid_" + CLIENT_ID;
  var userId = localStorage.getItem(STORAGE_KEY);
  if (!userId) {
    userId = "visitor_" + Math.random().toString(36).slice(2) + Date.now();
    localStorage.setItem(STORAGE_KEY, userId);
  }

  var host = document.createElement("div");
  host.id = "aura-biz-widget-host";
  document.body.appendChild(host);
  var root = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;

  var FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Roboto,Helvetica,Arial,sans-serif";

  var css = "" +
    ":host{all:initial;}" +
    "*{box-sizing:border-box;}" +
    "#aura-bubble{position:fixed;bottom:22px;right:22px;width:62px;height:62px;border-radius:50%;" +
    "background:linear-gradient(135deg," + COLOR + "," + COLOR + "cc);" +
    "box-shadow:0 8px 24px " + COLOR + "55,0 2px 8px rgba(0,0,0,.15);cursor:pointer;z-index:2147483000;" +
    "display:flex;align-items:center;justify-content:center;transition:transform .18s ease,box-shadow .18s ease;" +
    "border:none;animation:aura-pulse 2.6s ease-in-out infinite;}" +
    "#aura-bubble:hover{transform:scale(1.08);box-shadow:0 10px 30px " + COLOR + "77,0 2px 10px rgba(0,0,0,.2);}" +
    "#aura-bubble svg{width:26px;height:26px;fill:#fff;pointer-events:none;transition:transform .2s ease;}" +
    "#aura-bubble.open svg{transform:rotate(90deg) scale(0);}" +
    "#aura-bubble .aura-x{position:absolute;width:22px;height:22px;fill:#fff;opacity:0;transform:rotate(-90deg) scale(0);" +
    "transition:transform .2s ease,opacity .2s ease;pointer-events:none;}" +
    "#aura-bubble.open .aura-x{opacity:1;transform:rotate(0) scale(1);}" +
    "@keyframes aura-pulse{0%,100%{box-shadow:0 8px 24px " + COLOR + "55,0 2px 8px rgba(0,0,0,.15);}" +
    "50%{box-shadow:0 8px 28px " + COLOR + "88,0 0 0 8px " + COLOR + "22;}}" +
    "#aura-win{position:fixed;bottom:96px;right:22px;width:350px;max-width:92vw;height:480px;max-height:72vh;" +
    "background:#ffffff;border-radius:20px;box-shadow:0 20px 60px rgba(0,0,0,.28),0 2px 10px rgba(0,0,0,.08);" +
    "display:none;flex-direction:column;overflow:hidden;z-index:2147483000;font-family:" + FONT + ";color:#1F2937;" +
    "opacity:0;transform:translateY(18px) scale(.97);transition:opacity .22s ease,transform .22s ease;}" +
    "#aura-win.open{display:flex;}" +
    "#aura-win.show{opacity:1;transform:translateY(0) scale(1);}" +
    "#aura-hdr{background:linear-gradient(135deg," + COLOR + "," + COLOR + "dd);color:#fff;padding:16px 18px;" +
    "display:flex;align-items:center;gap:12px;flex-shrink:0;}" +
    "#aura-avatar{width:38px;height:38px;border-radius:50%;background:rgba(255,255,255,.2);flex-shrink:0;" +
    "display:flex;align-items:center;justify-content:center;font-size:17px;}" +
    "#aura-hdr .title{color:#fff;font-weight:700;font-size:14.5px;flex:1;min-width:0;}" +
    "#aura-hdr .title-row{display:flex;align-items:center;gap:6px;}" +
    "#aura-hdr .dot{width:7px;height:7px;border-radius:50%;background:#4ADE80;flex-shrink:0;" +
    "box-shadow:0 0 0 2px rgba(255,255,255,.3);}" +
    "#aura-hdr span.sub{display:block;font-weight:400;font-size:11px;opacity:.88;margin-top:2px;color:#fff;}" +
    "#aura-close{cursor:pointer;font-size:15px;line-height:1;background:rgba(255,255,255,.15);border:none;" +
    "color:#fff;padding:0;width:26px;height:26px;border-radius:50%;flex-shrink:0;display:flex;" +
    "align-items:center;justify-content:center;transition:background .15s ease;}" +
    "#aura-close:hover{background:rgba(255,255,255,.3);}" +
    "#aura-msgs{flex:1;overflow-y:auto;padding:16px;background:#F9FAFB;display:flex;flex-direction:column;gap:10px;}" +
    ".aura-row{max-width:84%;padding:10px 14px;border-radius:14px;font-size:13.5px;line-height:1.5;word-wrap:break-word;" +
    "-webkit-user-select:text;user-select:text;font-family:" + FONT + ";animation:aura-fadein .25s ease;}" +
    "@keyframes aura-fadein{from{opacity:0;transform:translateY(4px);}to{opacity:1;transform:translateY(0);}}" +
    ".aura-bot{background:#ffffff;border:1px solid #EDEEF1;align-self:flex-start;color:#1F2937;" +
    "box-shadow:0 1px 3px rgba(0,0,0,.04);border-bottom-left-radius:4px;}" +
    ".aura-user{background:linear-gradient(135deg," + COLOR + "," + COLOR + "dd);color:#fff;align-self:flex-end;" +
    "border-bottom-right-radius:4px;}" +
    ".aura-suggestions{display:flex;flex-wrap:wrap;gap:8px;padding:2px 0 4px;}" +
    ".aura-chip{background:#fff;border:1.5px solid " + COLOR + "44;color:" + COLOR + ";padding:7px 13px;" +
    "border-radius:16px;font-size:12.5px;font-weight:600;cursor:pointer;font-family:" + FONT + ";" +
    "transition:background .15s ease,transform .1s ease;white-space:nowrap;}" +
    ".aura-chip:hover{background:" + COLOR + "12;transform:translateY(-1px);}" +
    ".aura-typing{align-self:flex-start;background:#fff;border:1px solid #EDEEF1;border-radius:14px;" +
    "border-bottom-left-radius:4px;padding:11px 15px;box-shadow:0 1px 3px rgba(0,0,0,.04);}" +
    ".aura-typing span{display:inline-block;width:6px;height:6px;border-radius:50%;background:#9CA3AF;margin:0 2px;" +
    "animation:aura-blink 1.2s infinite;}" +
    ".aura-typing span:nth-child(2){animation-delay:.2s;} .aura-typing span:nth-child(3){animation-delay:.4s;}" +
    "@keyframes aura-blink{0%,80%,100%{opacity:.3;}40%{opacity:1;}}" +
    "#aura-inputrow{display:flex;border-top:1px solid #EDEEF1;padding:10px;gap:8px;background:#ffffff;flex-shrink:0;}" +
    "#aura-input{flex:1;border:1.5px solid #E5E7EB;border-radius:22px;padding:10px 15px;font-size:13.5px;outline:none;" +
    "background:#F9FAFB;color:#1F2937;font-family:" + FONT + ";-webkit-user-select:text;user-select:text;" +
    "cursor:text;caret-color:" + COLOR + ";transition:border-color .15s ease,background .15s ease;}" +
    "#aura-input:focus{border-color:" + COLOR + "77;background:#fff;}" +
    "#aura-input::placeholder{color:#9CA3AF;opacity:1;}" +
    "#aura-send{background:linear-gradient(135deg," + COLOR + "," + COLOR + "dd);border:none;color:#fff;" +
    "border-radius:50%;width:38px;height:38px;font-size:13px;font-weight:600;cursor:pointer;flex-shrink:0;" +
    "display:flex;align-items:center;justify-content:center;transition:transform .1s ease;}" +
    "#aura-send:hover:not(:disabled){transform:scale(1.06);}" +
    "#aura-send:disabled{opacity:.4;cursor:default;}" +
    "#aura-send svg{width:16px;height:16px;fill:#fff;margin-left:-1px;}" +
    "#aura-msgs::-webkit-scrollbar{width:5px;}" +
    "#aura-msgs::-webkit-scrollbar-thumb{background:#D1D5DB;border-radius:3px;}";

  var styleEl = document.createElement("style");
  styleEl.textContent = css;
  root.appendChild(styleEl);

  var bubble = document.createElement("button");
  bubble.id = "aura-bubble";
  bubble.type = "button";
  bubble.setAttribute("aria-label", "Open chat");
  bubble.innerHTML =
    '<svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zM6 9h12v2H6V9zm8 5H6v-2h8v2zm4-6H6V6h12v2z"/></svg>' +
    '<svg class="aura-x" viewBox="0 0 24 24"><path d="M18.3 5.71a1 1 0 00-1.41 0L12 10.59 7.11 5.7A1 1 0 105.7 7.11L10.59 12 5.7 16.89a1 1 0 101.41 1.41L12 13.41l4.89 4.89a1 1 0 001.41-1.41L13.41 12l4.89-4.89a1 1 0 000-1.4z"/></svg>';
  root.appendChild(bubble);

  var suggestionsHtml = SUGGESTIONS.length
    ? '<div class="aura-suggestions" id="aura-suggestions">' +
      SUGGESTIONS.map(function (s) { return '<button type="button" class="aura-chip">' + s + "</button>"; }).join("") +
      "</div>"
    : "";

  var win = document.createElement("div");
  win.id = "aura-win";
  win.innerHTML =
    '<div id="aura-hdr">' +
    '<div id="aura-avatar">✨</div>' +
    '<div class="title"><div class="title-row"><span class="dot"></span>' + BOT_NAME + "</div>" +
    '<span class="sub">Usually replies instantly</span></div>' +
    '<button id="aura-close" type="button" aria-label="Close chat">&#10005;</button></div>' +
    '<div id="aura-msgs"></div>' +
    '<div id="aura-inputrow">' +
    '<input id="aura-input" type="text" placeholder="Type a message..." autocomplete="off" />' +
    '<button id="aura-send" type="button" aria-label="Send"><svg viewBox="0 0 24 24"><path d="M2 21l21-9L2 3v7l15 2-15 2z"/></svg></button>' +
    "</div>";
  root.appendChild(win);

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

  function addSuggestions() {
    if (!SUGGESTIONS.length) return;
    var wrap = document.createElement("div");
    wrap.className = "aura-suggestions";
    wrap.id = "aura-suggestions";
    SUGGESTIONS.forEach(function (s) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "aura-chip";
      chip.textContent = s;
      chip.addEventListener("click", function () {
        removeSuggestions();
        sendMessage(s);
      });
      wrap.appendChild(chip);
    });
    msgsEl.appendChild(wrap);
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }

  function removeSuggestions() {
    var el = root.getElementById ? root.getElementById("aura-suggestions") : root.querySelector("#aura-suggestions");
    if (el) el.remove();
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
    var t = root.getElementById ? root.getElementById("aura-typing-el") : root.querySelector("#aura-typing-el");
    if (t) t.remove();
  }

  function openWidget() {
    win.classList.add("open");
    bubble.classList.add("open");
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { win.classList.add("show"); });
    });
    if (!opened) {
      addMsg(GREETING, "bot");
      addSuggestions();
      opened = true;
    }
    inputEl.focus();
  }
  function closeWidget() {
    win.classList.remove("show");
    bubble.classList.remove("open");
    setTimeout(function () { win.classList.remove("open"); }, 220);
  }
  bubble.addEventListener("click", function () {
    if (win.classList.contains("open")) { closeWidget(); } else { openWidget(); }
  });
  closeBtn.addEventListener("click", closeWidget);

  async function sendMessage(overrideText) {
    var text = (overrideText !== undefined ? overrideText : inputEl.value).trim();
    if (!text) return;
    removeSuggestions();
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

  sendBtn.addEventListener("click", function () { sendMessage(); });
  inputEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter") sendMessage();
  });
})();

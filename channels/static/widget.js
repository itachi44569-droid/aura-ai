/**
 * Nova AI Chat Widget
 * Drop-in script for embedding Nova on any website.
 * Reads config from the <script> tag's data-* attributes.
 */
(function () {
  'use strict';

  var me = document.currentScript || (function () {
    var scripts = document.getElementsByTagName('script');
    return scripts[scripts.length - 1];
  })();

  var cfg = {
    nova:     me.dataset.nova     || window.location.origin,
    color:    me.dataset.color    || '#4F46E5',
    position: me.dataset.position || 'bottom-right',
    label:    me.dataset.label    || 'Chat with us',
    greeting: me.dataset.greeting || '',
    botname:  me.dataset.botname  || '',
  };

  var isLeft = cfg.position === 'bottom-left';
  var isOpen = false;
  var host, shadow, bubble, panel, iframe;

  function injectStyles(root) {
    var style = document.createElement('style');
    style.textContent = [
      ':host { all: initial; font-family: system-ui, sans-serif; }',
      '.nv-bubble {',
      '  position: fixed; bottom: 20px; ' + (isLeft ? 'left' : 'right') + ': 20px;',
      '  width: 56px; height: 56px; border-radius: 50%;',
      '  background: ' + cfg.color + ';',
      '  box-shadow: 0 4px 24px ' + cfg.color + '88;',
      '  border: none; cursor: pointer; z-index: 2147483646;',
      '  display: flex; align-items: center; justify-content: center;',
      '  font-size: 24px; transition: transform .2s, box-shadow .2s;',
      '}',
      '.nv-bubble:hover { transform: scale(1.1); box-shadow: 0 8px 32px ' + cfg.color + 'aa; }',
      '.nv-bubble .nv-close { display: none; font-size: 20px; color: #fff; font-weight: 700; }',
      '.nv-bubble.open .nv-icon { display: none; }',
      '.nv-bubble.open .nv-close { display: block; }',
      '.nv-tooltip {',
      '  position: fixed; bottom: 86px; ' + (isLeft ? 'left' : 'right') + ': 20px;',
      '  background: #1e1b4b; color: #fff; font-size: 12px; padding: 5px 10px;',
      '  border-radius: 8px; white-space: nowrap; pointer-events: none;',
      '  opacity: 0; transition: opacity .2s; z-index: 2147483645;',
      '}',
      '.nv-bubble:hover ~ .nv-tooltip { opacity: 1; }',
      '.nv-panel {',
      '  position: fixed; bottom: 90px; ' + (isLeft ? 'left' : 'right') + ': 20px;',
      '  width: 370px; height: 580px;',
      '  border-radius: 16px; overflow: hidden;',
      '  box-shadow: 0 20px 60px rgba(0,0,0,.45);',
      '  z-index: 2147483645; display: none;',
      '  border: 1px solid rgba(255,255,255,.08);',
      '  transform: translateY(16px) scale(.96); opacity: 0;',
      '  transition: transform .22s cubic-bezier(.4,0,.2,1), opacity .22s;',
      '}',
      '.nv-panel.open {',
      '  display: block; transform: translateY(0) scale(1); opacity: 1;',
      '}',
      '.nv-panel iframe { width: 100%; height: 100%; border: none; display: block; }',
      '@media (max-width: 420px) {',
      '  .nv-panel { left: 8px !important; right: 8px !important; width: auto; bottom: 84px; }',
      '}',
    ].join('\n');
    root.appendChild(style);
  }

  function buildIframeSrc() {
    var base = cfg.nova.replace(/\/$/, '') + '/';
    var params = ['widget=1'];
    if (cfg.greeting) params.push('greeting=' + encodeURIComponent(cfg.greeting));
    if (cfg.botname)  params.push('botname='  + encodeURIComponent(cfg.botname));
    return base + '?' + params.join('&');
  }

  function openPanel() {
    isOpen = true;
    bubble.classList.add('open');
    if (!iframe) {
      iframe = document.createElement('iframe');
      iframe.src = buildIframeSrc();
      iframe.title = cfg.botname || 'Nova AI Chat';
      iframe.allow = 'microphone; clipboard-write';
      panel.appendChild(iframe);
    }
    panel.style.display = 'block';
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        panel.classList.add('open');
      });
    });
  }

  function closePanel() {
    isOpen = false;
    bubble.classList.remove('open');
    panel.classList.remove('open');
    setTimeout(function () {
      if (!isOpen) panel.style.display = 'none';
    }, 230);
  }

  function init() {
    host = document.createElement('div');
    host.id = 'nova-widget-root';
    document.body.appendChild(host);

    shadow = host.attachShadow({ mode: 'open' });
    injectStyles(shadow);

    bubble = document.createElement('button');
    bubble.className = 'nv-bubble';
    bubble.setAttribute('aria-label', cfg.label);
    bubble.innerHTML = '<span class="nv-icon">💬</span><span class="nv-close">&#x2715;</span>';
    bubble.addEventListener('click', function () {
      isOpen ? closePanel() : openPanel();
    });

    var tooltip = document.createElement('div');
    tooltip.className = 'nv-tooltip';
    tooltip.textContent = cfg.label;

    panel = document.createElement('div');
    panel.className = 'nv-panel';

    shadow.appendChild(bubble);
    shadow.appendChild(tooltip);
    shadow.appendChild(panel);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

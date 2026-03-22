/* espresso-bridge — touchscreen UI logic */

(function() {
  'use strict';

  // State
  let state = {
    shotstopper: { connected: false, weight_target: 36, shot_active: false, scale_status: 0 },
    lamarzocco: { connected: false, turned_on: false, coffee_temp_target: 93.0, steam_level: 2, steam_enabled: false }
  };

  // Elements
  const el = {
    weightValue: document.getElementById('weight-value'),
    weightUp: document.getElementById('weight-up'),
    weightDown: document.getElementById('weight-down'),
    shotIndicator: document.getElementById('shot-indicator'),
    ssStatus: document.getElementById('ss-status'),
    lmStatus: document.getElementById('lm-status'),
    scaleStatus: document.getElementById('scale-status'),
    lmPower: document.getElementById('lm-power'),
    coffeeTemp: document.getElementById('coffee-temp'),
    coffeeUp: document.getElementById('coffee-up'),
    coffeeDown: document.getElementById('coffee-down'),
  };

  // -- API helpers --

  async function api(method, path, body) {
    try {
      const opts = { method, headers: { 'Content-Type': 'application/json' } };
      if (body) opts.body = JSON.stringify(body);
      const res = await fetch('/api' + path, opts);
      return await res.json();
    } catch (e) {
      console.error('API error:', e);
      return { ok: false };
    }
  }

  // -- UI update --

  function render() {
    const ss = state.shotstopper;
    const lm = state.lamarzocco;

    // Weight
    el.weightValue.textContent = ss.weight_target;

    // Shot indicator
    el.shotIndicator.textContent = ss.shot_active ? 'BREWING' : 'IDLE';
    el.shotIndicator.classList.toggle('brewing', ss.shot_active);

    // Status bar
    setStatus(el.ssStatus, ss.connected);
    setStatus(el.lmStatus, lm.connected);
    setStatus(el.scaleStatus, ss.scale_status === 1);

    // LM power
    el.lmPower.textContent = lm.turned_on ? 'ON' : 'OFF';
    el.lmPower.classList.toggle('on', lm.turned_on);

    // Coffee temp
    el.coffeeTemp.textContent = lm.coffee_temp_target.toFixed(1);

    // Steam levels
    document.querySelectorAll('.level-btn').forEach(btn => {
      btn.classList.toggle('active', parseInt(btn.dataset.level) === lm.steam_level);
    });

    // Weight presets
    document.querySelectorAll('.preset-btn').forEach(btn => {
      btn.classList.toggle('active', parseInt(btn.dataset.weight) === ss.weight_target);
    });
  }

  function setStatus(el, connected) {
    el.classList.remove('connected', 'disconnected');
    el.classList.add(connected ? 'connected' : 'disconnected');
  }

  // -- WebSocket --

  function connectWS() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(protocol + '//' + location.host + '/ws');

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        state.shotstopper = data.shotstopper;
        state.lamarzocco = data.lamarzocco;
        render();
      } catch (err) {
        console.error('WS parse error:', err);
      }
    };

    ws.onclose = () => {
      console.log('WS disconnected, reconnecting in 2s...');
      setTimeout(connectWS, 2000);
    };

    ws.onerror = () => ws.close();
  }

  // -- Event handlers --

  // Weight +/- buttons
  el.weightUp.addEventListener('click', () => {
    const w = Math.min(200, state.shotstopper.weight_target + 1);
    api('POST', '/shotstopper/weight', { grams: w });
    state.shotstopper.weight_target = w;
    render();
  });

  el.weightDown.addEventListener('click', () => {
    const w = Math.max(10, state.shotstopper.weight_target - 1);
    api('POST', '/shotstopper/weight', { grams: w });
    state.shotstopper.weight_target = w;
    render();
  });

  // Weight presets
  document.querySelectorAll('.preset-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const w = parseInt(btn.dataset.weight);
      api('POST', '/shotstopper/weight', { grams: w });
      state.shotstopper.weight_target = w;
      render();
    });
  });

  // LM power toggle
  el.lmPower.addEventListener('click', () => {
    const on = !state.lamarzocco.turned_on;
    api('POST', '/lm/power', { on });
    state.lamarzocco.turned_on = on;
    render();
  });

  // Coffee temp +/- (0.5° steps)
  el.coffeeUp.addEventListener('click', () => {
    const t = Math.min(104, state.lamarzocco.coffee_temp_target + 0.5);
    api('POST', '/lm/temperature', { celsius: t });
    state.lamarzocco.coffee_temp_target = t;
    render();
  });

  el.coffeeDown.addEventListener('click', () => {
    const t = Math.max(85, state.lamarzocco.coffee_temp_target - 0.5);
    api('POST', '/lm/temperature', { celsius: t });
    state.lamarzocco.coffee_temp_target = t;
    render();
  });

  // Steam level buttons
  document.querySelectorAll('.level-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const level = parseInt(btn.dataset.level);
      api('POST', '/lm/steam', { level });
      state.lamarzocco.steam_level = level;
      render();
    });
  });

  // -- Init --

  // Fetch initial state, then connect WebSocket
  api('GET', '/status').then(data => {
    if (data.shotstopper) state.shotstopper = data.shotstopper;
    if (data.lamarzocco) state.lamarzocco = data.lamarzocco;
    render();
    connectWS();
  });

})();

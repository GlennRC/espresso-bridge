/* espresso-bridge — touchscreen UI logic */

(function() {
  'use strict';

  const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
  const DAY_LABELS = { monday: 'Mon', tuesday: 'Tue', wednesday: 'Wed', thursday: 'Thu', friday: 'Fri', saturday: 'Sat', sunday: 'Sun' };

  // State
  let state = {
    shotstopper: { connected: false, weight_target: 36, shot_active: false, scale_status: 0 },
    lamarzocco: { connected: false, turned_on: false, coffee_temp_target: 93.0, steam_level: 2, steam_enabled: false }
  };

  let scheduleState = {
    schedule: {
      enabled: false,
      week_a: {},
      week_b: {},
      reference_date: ''
    },
    current_week: 'a',
    next_event: null
  };

  let activeWeekTab = 'a';

  // Elements
  const el = {
    weightValue: document.getElementById('weight-value'),
    weightUp: document.getElementById('weight-up'),
    weightDown: document.getElementById('weight-down'),
    shotIndicator: document.getElementById('shot-indicator'),
    ssStatus: document.getElementById('ss-status'),
    lmStatus: document.getElementById('lm-status'),
    lmPower: document.getElementById('lm-power'),
    coffeeTemp: document.getElementById('coffee-temp'),
    coffeeUp: document.getElementById('coffee-up'),
    coffeeDown: document.getElementById('coffee-down'),
    bannerText: document.getElementById('banner-text'),
    scheduleEnable: document.getElementById('schedule-enable'),
    scheduleDays: document.getElementById('schedule-days'),
    weekATab: document.getElementById('week-a-tab'),
    weekBTab: document.getElementById('week-b-tab'),
    currentWeekBadge: document.getElementById('current-week-badge'),
    refDate: document.getElementById('ref-date'),
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

  // -- Brew view render --

  function render() {
    const ss = state.shotstopper;
    const lm = state.lamarzocco;

    el.weightValue.textContent = ss.weight_target;
    el.shotIndicator.textContent = ss.shot_active ? 'BREWING' : 'IDLE';
    el.shotIndicator.classList.toggle('brewing', ss.shot_active);

    setStatus(el.ssStatus, ss.connected);
    setStatus(el.lmStatus, lm.connected);

    el.lmPower.textContent = lm.turned_on ? 'ON' : 'OFF';
    el.lmPower.classList.toggle('on', lm.turned_on);

    el.coffeeTemp.textContent = lm.coffee_temp_target.toFixed(1);

    document.querySelectorAll('.level-btn').forEach(btn => {
      btn.classList.toggle('active', parseInt(btn.dataset.level) === lm.steam_level);
    });

    document.querySelectorAll('.preset-btn').forEach(btn => {
      btn.classList.toggle('active', parseInt(btn.dataset.weight) === ss.weight_target);
    });
  }

  function setStatus(el, connected) {
    el.classList.remove('connected', 'disconnected');
    el.classList.add(connected ? 'connected' : 'disconnected');
  }

  // -- Schedule view render --

  function renderSchedule() {
    const sched = scheduleState.schedule;
    const enabled = sched.enabled;
    const next = scheduleState.next_event;

    // Banner
    el.scheduleEnable.textContent = enabled ? 'DISABLE' : 'ENABLE';
    el.scheduleEnable.classList.toggle('on', enabled);

    if (!enabled) {
      el.bannerText.textContent = 'Schedule not enabled';
    } else if (next) {
      const dayLabel = DAY_LABELS[next.day] || next.day;
      const h = next.hour % 12 || 12;
      const ampm = next.hour < 12 ? 'AM' : 'PM';
      const m = String(next.minute).padStart(2, '0');
      const verb = next.type === 'on' ? 'Turns on' : 'Turns off';
      el.bannerText.textContent = `${verb} ${dayLabel} at ${h}:${m} ${ampm}`;
    } else {
      el.bannerText.textContent = 'No upcoming events';
    }

    // Week tabs
    el.weekATab.classList.toggle('active', activeWeekTab === 'a');
    el.weekBTab.classList.toggle('active', activeWeekTab === 'b');

    // Current week badge
    const curWeek = scheduleState.current_week;
    el.currentWeekBadge.classList.toggle('visible', true);
    if (curWeek === activeWeekTab) {
      el.currentWeekBadge.style.display = '';
      el.currentWeekBadge.classList.add('visible');
    } else {
      el.currentWeekBadge.classList.remove('visible');
    }

    // Position badge on the active tab
    const badge = el.currentWeekBadge;
    if (curWeek === 'a') {
      badge.style.left = '16px';
      badge.style.right = '';
    } else {
      badge.style.left = '';
      badge.style.right = '16px';
    }

    // Day rows
    const weekKey = activeWeekTab === 'a' ? 'week_a' : 'week_b';
    const week = sched[weekKey] || {};
    renderDayRows(week);

    // Reference date
    el.refDate.value = sched.reference_date || '';
  }

  function renderDayRows(week) {
    el.scheduleDays.innerHTML = '';

    DAYS.forEach(day => {
      const ds = week[day] || { enabled: false, on_hour: 7, on_minute: 0, off_hour: 22, off_minute: 0, steam: true };

      const row = document.createElement('div');
      row.className = 'day-row';

      const nameEl = document.createElement('span');
      nameEl.className = 'day-name';
      nameEl.textContent = DAY_LABELS[day];

      const toggle = document.createElement('button');
      toggle.className = 'day-toggle' + (ds.enabled ? ' on' : '');
      toggle.addEventListener('click', () => {
        ds.enabled = !ds.enabled;
        toggle.classList.toggle('on', ds.enabled);
        times.classList.toggle('disabled', !ds.enabled);
        saveSchedule();
      });

      const times = document.createElement('div');
      times.className = 'day-times' + (ds.enabled ? '' : ' disabled');

      const onInput = createTimeInput(ds.on_hour, ds.on_minute, (h, m) => {
        ds.on_hour = h;
        ds.on_minute = m;
        saveSchedule();
      });
      const sep = document.createElement('span');
      sep.className = 'time-sep';
      sep.textContent = '→';
      const offInput = createTimeInput(ds.off_hour, ds.off_minute, (h, m) => {
        ds.off_hour = h;
        ds.off_minute = m;
        saveSchedule();
      });

      times.appendChild(onInput);
      times.appendChild(sep);
      times.appendChild(offInput);

      const steamBtn = document.createElement('button');
      steamBtn.className = 'steam-toggle' + (ds.steam ? ' on' : '');
      steamBtn.textContent = '♨';
      steamBtn.title = 'Steam on wake';
      steamBtn.addEventListener('click', () => {
        ds.steam = !ds.steam;
        steamBtn.classList.toggle('on', ds.steam);
        saveSchedule();
      });

      row.appendChild(nameEl);
      row.appendChild(toggle);
      row.appendChild(times);
      row.appendChild(steamBtn);
      el.scheduleDays.appendChild(row);
    });
  }

  function createTimeInput(hour, minute, onChange) {
    const input = document.createElement('input');
    input.type = 'time';
    input.className = 'time-input';
    input.value = String(hour).padStart(2, '0') + ':' + String(minute).padStart(2, '0');
    input.addEventListener('change', () => {
      const [h, m] = input.value.split(':').map(Number);
      if (!isNaN(h) && !isNaN(m)) onChange(h, m);
    });
    return input;
  }

  // -- Schedule API --

  let saveTimeout = null;

  function saveSchedule() {
    // Debounce saves (300ms)
    if (saveTimeout) clearTimeout(saveTimeout);
    saveTimeout = setTimeout(async () => {
      const sched = buildSchedulePayload();
      const res = await api('POST', '/lm/schedule', sched);
      if (res.schedule) {
        scheduleState.schedule = res.schedule;
        scheduleState.current_week = res.current_week;
        scheduleState.next_event = res.next_event;
        renderSchedule();
      }
    }, 300);
  }

  function buildSchedulePayload() {
    const sched = scheduleState.schedule;
    // Read day rows from DOM for active week
    const weekKey = activeWeekTab === 'a' ? 'week_a' : 'week_b';
    const weekData = {};

    const rows = el.scheduleDays.querySelectorAll('.day-row');
    rows.forEach((row, i) => {
      const day = DAYS[i];
      const enabled = row.querySelector('.day-toggle').classList.contains('on');
      const timeInputs = row.querySelectorAll('.time-input');
      const [onH, onM] = timeInputs[0].value.split(':').map(Number);
      const [offH, offM] = timeInputs[1].value.split(':').map(Number);
      const steam = row.querySelector('.steam-toggle').classList.contains('on');

      weekData[day] = {
        enabled,
        on_hour: onH || 0,
        on_minute: onM || 0,
        off_hour: offH || 0,
        off_minute: offM || 0,
        steam
      };
    });

    sched[weekKey] = weekData;
    sched.reference_date = el.refDate.value || '';

    return sched;
  }

  async function loadSchedule() {
    const res = await api('GET', '/lm/schedule');
    if (res.schedule) {
      scheduleState.schedule = res.schedule;
      scheduleState.current_week = res.current_week || 'a';
      scheduleState.next_event = res.next_event;
      activeWeekTab = scheduleState.current_week;
      renderSchedule();
    }
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

  // -- Tab Bar Navigation --

  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const viewId = tab.dataset.view;
      document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.getElementById(viewId).classList.add('active');
      tab.classList.add('active');

      if (viewId === 'schedule-view') loadSchedule();
    });
  });

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

  // Schedule enable toggle
  el.scheduleEnable.addEventListener('click', () => {
    scheduleState.schedule.enabled = !scheduleState.schedule.enabled;
    saveSchedule();
  });

  // Week A/B tabs
  el.weekATab.addEventListener('click', () => {
    activeWeekTab = 'a';
    renderSchedule();
  });
  el.weekBTab.addEventListener('click', () => {
    activeWeekTab = 'b';
    renderSchedule();
  });

  // Reference date
  el.refDate.addEventListener('change', () => {
    saveSchedule();
  });

  // -- Init --

  api('GET', '/status').then(data => {
    if (data.shotstopper) state.shotstopper = data.shotstopper;
    if (data.lamarzocco) state.lamarzocco = data.lamarzocco;
    render();
    connectWS();
  });

})();

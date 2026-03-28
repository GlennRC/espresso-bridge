/* espresso-bridge — touchscreen UI logic */

(function() {
  'use strict';

  const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
  const DAY_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  // State
  let state = {
    shotstopper: { connected: false, weight_target: 36, shot_active: false, scale_status: 0 },
    lamarzocco: { connected: false, turned_on: false, coffee_temp_target: 93.0, steam_level: 2, steam_enabled: false }
  };

  let scheduleState = {
    schedule: { enabled: false, rules: [], events: {}, skips: [] },
    resolved: [],
    next_event: null
  };

  let expandedDate = null;

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
    calendarStrip: document.getElementById('calendar-strip'),
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

  // -- Schedule helpers --

  function formatTime12(h, m) {
    const hr = h % 12 || 12;
    const ampm = h < 12 ? 'AM' : 'PM';
    return `${hr}:${String(m).padStart(2, '0')} ${ampm}`;
  }

  function formatDateShort(iso) {
    const d = new Date(iso + 'T00:00:00');
    return `${MONTH_SHORT[d.getMonth()]} ${d.getDate()}`;
  }

  // -- Schedule API --

  async function loadSchedule() {
    const res = await api('GET', '/lm/schedule');
    if (res.schedule) {
      scheduleState.schedule = res.schedule;
      scheduleState.resolved = res.resolved || [];
      scheduleState.next_event = res.next_event;
      renderSchedule();
    }
  }

  async function saveFullSchedule() {
    await api('POST', '/lm/schedule', scheduleState.schedule);
    await loadSchedule();
  }

  async function dayAction(isoDate, action, entryData) {
    const body = { action };
    if (entryData) body.entry = entryData;
    await api('POST', `/lm/schedule/day/${isoDate}`, body);
    expandedDate = null;
    await loadSchedule();
  }

  // -- Schedule render --

  function renderSchedule() {
    const { schedule, next_event } = scheduleState;

    // Banner
    el.scheduleEnable.textContent = schedule.enabled ? 'DISABLE' : 'ENABLE';
    el.scheduleEnable.classList.toggle('on', schedule.enabled);

    if (!schedule.enabled) {
      el.bannerText.textContent = 'Schedule not enabled';
    } else if (next_event) {
      const dayLabel = DAY_SHORT[DAYS.indexOf(next_event.day)] || next_event.day;
      const verb = next_event.type === 'on' ? 'Turns on' : 'Turns off';
      const dateStr = next_event.date ? formatDateShort(next_event.date) : '';
      el.bannerText.textContent = `${verb} ${dayLabel} ${dateStr} at ${formatTime12(next_event.hour, next_event.minute)}`;
    } else {
      el.bannerText.textContent = 'No upcoming events';
    }

    renderCalendarStrip();
  }

  function renderCalendarStrip() {
    const strip = el.calendarStrip;
    const scrollPos = strip.scrollTop;
    strip.innerHTML = '';

    const today = new Date();
    today.setHours(0, 0, 0, 0);
    let lastMonth = null;

    for (const day of scheduleState.resolved) {
      const d = new Date(day.date + 'T00:00:00');
      const isTodayDate = d.getTime() === today.getTime();
      const hasEntry = !!day.entry;
      const isSkip = day.source === 'skip';
      const isRecurring = day.source.startsWith('rule:');
      const isManual = day.source === 'manual';
      const isExpanded = expandedDate === day.date;

      // Month separator
      const month = d.getMonth();
      if (month !== lastMonth) {
        const sep = document.createElement('div');
        sep.className = 'cal-month-sep';
        sep.textContent = MONTH_SHORT[month] + ' ' + d.getFullYear();
        strip.appendChild(sep);
        lastMonth = month;
      }

      // Day row
      const row = document.createElement('div');
      row.className = 'cal-day';
      if (hasEntry) row.classList.add('on');
      if (isSkip) row.classList.add('skip');
      if (isTodayDate) row.classList.add('today');
      if (isExpanded) row.classList.add('expanded');

      // Date column
      const dateCol = document.createElement('div');
      dateCol.className = 'cal-date';
      const jsDay = d.getDay() === 0 ? 6 : d.getDay() - 1;
      dateCol.innerHTML =
        '<div class="cal-date-num">' + d.getDate() + '</div>' +
        '<div class="cal-date-day">' + (isTodayDate ? 'Today' : DAY_SHORT[jsDay]) + '</div>';

      // Info column
      const info = document.createElement('div');
      info.className = 'cal-info';

      if (isSkip) {
        info.innerHTML = '<span class="cal-status">SKIP</span>';
      } else if (hasEntry) {
        const e = day.entry;
        const srcIcon = isRecurring ? '🔄' : '📌';
        info.innerHTML =
          '<span class="cal-status">● ON</span>' +
          '<span class="cal-time">' + formatTime12(e.wake_hour, e.wake_minute) + '</span>' +
          (e.steam ? '<span class="cal-steam">♨</span>' : '') +
          '<span class="cal-source">' + srcIcon + '</span>';
      } else {
        info.innerHTML = '<span class="cal-status">OFF</span>';
      }

      row.appendChild(dateCol);
      row.appendChild(info);

      // Tap to expand/collapse
      row.addEventListener('click', () => {
        expandedDate = expandedDate === day.date ? null : day.date;
        renderCalendarStrip();
      });

      strip.appendChild(row);

      // Editor accordion
      if (isExpanded) {
        strip.appendChild(createEditor(day));
      }
    }

    strip.scrollTop = scrollPos;
  }

  function createEditor(day) {
    const editor = document.createElement('div');
    editor.className = 'cal-editor';

    const hasEntry = !!day.entry;
    const isSkip = day.source === 'skip';
    const isRecurring = day.source.startsWith('rule:');
    const isManual = day.source === 'manual';

    const wakeH = hasEntry ? day.entry.wake_hour : 4;
    const wakeM = hasEntry ? day.entry.wake_minute : 50;
    const steam = hasEntry ? day.entry.steam : true;
    const timeVal = String(wakeH).padStart(2, '0') + ':' + String(wakeM).padStart(2, '0');

    // Controls row
    const controls = document.createElement('div');
    controls.className = 'editor-controls';
    controls.innerHTML =
      '<div class="editor-field">' +
        '<label>Wake</label>' +
        '<input type="time" class="time-input editor-time" value="' + timeVal + '">' +
      '</div>' +
      '<div class="editor-field">' +
        '<label>Steam</label>' +
        '<button class="day-toggle editor-steam ' + (steam ? 'on' : '') + '"></button>' +
      '</div>';

    // Prevent clicks in editor from collapsing
    controls.addEventListener('click', function(e) { e.stopPropagation(); });

    // Steam toggle
    const steamBtn = controls.querySelector('.editor-steam');
    steamBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      steamBtn.classList.toggle('on');
    });

    // Actions row
    const actions = document.createElement('div');
    actions.className = 'editor-actions';

    const timeInput = controls.querySelector('.editor-time');

    function getEntryData() {
      const parts = timeInput.value.split(':');
      return {
        wake_hour: parseInt(parts[0]) || 4,
        wake_minute: parseInt(parts[1]) || 50,
        off_hour: 23, off_minute: 0,
        steam: steamBtn.classList.contains('on')
      };
    }

    if (isSkip) {
      addBtn(actions, 'Un-skip', 'unskip', function() { dayAction(day.date, 'remove'); });
    } else if (!hasEntry) {
      addBtn(actions, '+ Add', 'add', function() { dayAction(day.date, 'add', getEntryData()); });
    } else if (isManual) {
      addBtn(actions, 'Remove', 'remove', function() { dayAction(day.date, 'remove'); });
      addBtn(actions, 'Save', 'save', function() { dayAction(day.date, 'add', getEntryData()); });
    } else if (isRecurring) {
      addBtn(actions, 'Skip', 'skip', function() { dayAction(day.date, 'skip'); });
      addBtn(actions, 'Customize', 'save', function() { dayAction(day.date, 'add', getEntryData()); });
    }

    editor.appendChild(controls);
    editor.appendChild(actions);
    return editor;
  }

  function addBtn(container, text, cls, handler) {
    const btn = document.createElement('button');
    btn.className = 'editor-btn ' + cls;
    btn.textContent = text;
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      handler();
    });
    container.appendChild(btn);
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
    saveFullSchedule();
  });

  // -- Init --

  api('GET', '/status').then(data => {
    if (data.shotstopper) state.shotstopper = data.shotstopper;
    if (data.lamarzocco) state.lamarzocco = data.lamarzocco;
    render();
    connectWS();
  });

})();

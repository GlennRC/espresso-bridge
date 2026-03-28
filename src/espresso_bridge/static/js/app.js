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
    schedule: {
      enabled: false,
      week_a: {},
      week_b: {},
      reference_date: ''
    },
    current_week: 'a',
    next_event: null
  };

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
    schedWakeTime: document.getElementById('sched-wake-time'),
    schedSteamToggle: document.getElementById('sched-steam-toggle'),
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

  // -- Schedule: date helpers --

  function dateKey(d) {
    // "YYYY-MM-DD"
    return d.toISOString().slice(0, 10);
  }

  function isToday(d) {
    const now = new Date();
    return d.getFullYear() === now.getFullYear() &&
           d.getMonth() === now.getMonth() &&
           d.getDate() === now.getDate();
  }

  function getWeekLabel(d, refDate) {
    // Determine week A or B from reference_date
    if (!refDate) return 'a';
    const ref = new Date(refDate + 'T00:00:00');
    const diff = Math.floor((d - ref) / (7 * 86400000));
    return diff % 2 === 0 ? 'a' : 'b';
  }

  function getDaySchedule(d, sched) {
    // Get the DaySchedule for a given Date from the schedule config
    const weekLabel = getWeekLabel(d, sched.reference_date);
    const weekKey = weekLabel === 'a' ? 'week_a' : 'week_b';
    const week = sched[weekKey] || {};
    const dayName = DAYS[d.getDay() === 0 ? 6 : d.getDay() - 1]; // JS 0=Sun
    return { daySchedule: week[dayName] || { enabled: false }, weekLabel, dayName };
  }

  function formatTime12(h, m) {
    const hr = h % 12 || 12;
    const ampm = h < 12 ? 'AM' : 'PM';
    return `${hr}:${String(m).padStart(2, '0')} ${ampm}`;
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
      const dayLabel = DAY_SHORT[DAYS.indexOf(next.day)] || next.day;
      const verb = next.type === 'on' ? 'Turns on' : 'Turns off';
      el.bannerText.textContent = `${verb} ${dayLabel} at ${formatTime12(next.hour, next.minute)}`;
    } else {
      el.bannerText.textContent = 'No upcoming events';
    }

    // Reference date
    el.refDate.value = sched.reference_date || '';

    // Wake time (use first enabled day we find, or default)
    let wakeH = 4, wakeM = 50, steamOn = true;
    for (const wk of [sched.week_a, sched.week_b]) {
      for (const day of DAYS) {
        const ds = (wk || {})[day];
        if (ds && ds.enabled) {
          wakeH = ds.on_hour;
          wakeM = ds.on_minute;
          steamOn = ds.steam !== false;
          break;
        }
      }
    }
    el.schedWakeTime.value = String(wakeH).padStart(2, '0') + ':' + String(wakeM).padStart(2, '0');
    el.schedSteamToggle.classList.toggle('on', steamOn);

    // Calendar strip — show 28 days from today
    renderCalendarStrip(sched);
  }

  function renderCalendarStrip(sched) {
    const strip = el.calendarStrip;
    strip.innerHTML = '';

    const today = new Date();
    today.setHours(0, 0, 0, 0);
    let lastWeekLabel = null;

    for (let i = 0; i < 28; i++) {
      const d = new Date(today);
      d.setDate(today.getDate() + i);

      const { daySchedule: ds, weekLabel } = getDaySchedule(d, sched);

      // Week separator
      if (weekLabel !== lastWeekLabel) {
        const sep = document.createElement('div');
        sep.className = 'cal-week-sep';
        sep.textContent = 'Week ' + weekLabel.toUpperCase();
        strip.appendChild(sep);
        lastWeekLabel = weekLabel;
      }

      const row = document.createElement('div');
      row.className = 'cal-day' + (ds.enabled ? ' on' : '') + (isToday(d) ? ' today' : '');
      row.dataset.date = dateKey(d);

      // Date column
      const dateCol = document.createElement('div');
      dateCol.className = 'cal-date';
      const dateNum = document.createElement('div');
      dateNum.className = 'cal-date-num';
      dateNum.textContent = d.getDate();
      const dateDay = document.createElement('div');
      dateDay.className = 'cal-date-day';
      const jsDay = d.getDay() === 0 ? 6 : d.getDay() - 1;
      dateDay.textContent = isToday(d) ? 'Today' : DAY_SHORT[jsDay];
      dateCol.appendChild(dateNum);
      dateCol.appendChild(dateDay);

      // Info column
      const info = document.createElement('div');
      info.className = 'cal-info';

      const statusEl = document.createElement('span');
      statusEl.className = 'cal-status';
      statusEl.textContent = ds.enabled ? '● ON' : 'OFF';

      info.appendChild(statusEl);

      if (ds.enabled) {
        const timeEl = document.createElement('span');
        timeEl.className = 'cal-time';
        timeEl.textContent = formatTime12(ds.on_hour || 0, ds.on_minute || 0);
        info.appendChild(timeEl);

        if (ds.steam !== false) {
          const steamEl = document.createElement('span');
          steamEl.className = 'cal-steam';
          steamEl.textContent = '♨';
          info.appendChild(steamEl);
        }
      }

      row.appendChild(dateCol);
      row.appendChild(info);

      // Tap to toggle
      row.addEventListener('click', () => toggleDay(d, sched));

      strip.appendChild(row);
    }

    // Scroll to today (first item, so already at top)
  }

  function toggleDay(date, sched) {
    const { daySchedule: ds, weekLabel, dayName } = getDaySchedule(date, sched);
    const weekKey = weekLabel === 'a' ? 'week_a' : 'week_b';

    if (!sched[weekKey]) sched[weekKey] = {};
    if (!sched[weekKey][dayName]) {
      sched[weekKey][dayName] = { enabled: false, on_hour: 4, on_minute: 50, off_hour: 23, off_minute: 0, steam: true };
    }

    // Read current wake time from settings
    const [wH, wM] = el.schedWakeTime.value.split(':').map(Number);
    const steamOn = el.schedSteamToggle.classList.contains('on');

    const day = sched[weekKey][dayName];
    day.enabled = !day.enabled;
    day.on_hour = wH || 4;
    day.on_minute = wM || 50;
    day.steam = steamOn;

    saveSchedule();
  }

  // -- Schedule API --

  let saveTimeout = null;

  function saveSchedule() {
    if (saveTimeout) clearTimeout(saveTimeout);
    saveTimeout = setTimeout(async () => {
      const sched = scheduleState.schedule;
      sched.reference_date = el.refDate.value || '';
      const res = await api('POST', '/lm/schedule', sched);
      if (res.schedule) {
        scheduleState.schedule = res.schedule;
        scheduleState.current_week = res.current_week;
        scheduleState.next_event = res.next_event;
        renderSchedule();
      }
    }, 300);
  }

  async function loadSchedule() {
    const res = await api('GET', '/lm/schedule');
    if (res.schedule) {
      scheduleState.schedule = res.schedule;
      scheduleState.current_week = res.current_week || 'a';
      scheduleState.next_event = res.next_event;
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

  // Wake time change — update all enabled days
  el.schedWakeTime.addEventListener('change', () => {
    const [h, m] = el.schedWakeTime.value.split(':').map(Number);
    if (isNaN(h) || isNaN(m)) return;
    const sched = scheduleState.schedule;
    for (const weekKey of ['week_a', 'week_b']) {
      const week = sched[weekKey] || {};
      for (const day of DAYS) {
        if (week[day] && week[day].enabled) {
          week[day].on_hour = h;
          week[day].on_minute = m;
        }
      }
    }
    saveSchedule();
  });

  // Steam toggle
  el.schedSteamToggle.addEventListener('click', () => {
    const on = !el.schedSteamToggle.classList.contains('on');
    el.schedSteamToggle.classList.toggle('on', on);
    const sched = scheduleState.schedule;
    for (const weekKey of ['week_a', 'week_b']) {
      const week = sched[weekKey] || {};
      for (const day of DAYS) {
        if (week[day] && week[day].enabled) {
          week[day].steam = on;
        }
      }
    }
    saveSchedule();
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

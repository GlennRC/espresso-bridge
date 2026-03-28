/* espresso-bridge — touchscreen UI logic */

(function() {
  'use strict';

  const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
  const DAY_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const DAY_LETTER = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
  const MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  // State
  let state = {
    shotstopper: { connected: false, weight_target: 36, shot_active: false, scale_status: 0 },
    lamarzocco: { connected: false, turned_on: false, coffee_temp_target: 93.0, steam_level: 2, steam_enabled: false }
  };

  let scheduleData = { schedules: [], next_event: null };
  let editingSchedule = null; // null = create new, or existing schedule id

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
    scheduleList: document.getElementById('schedule-list'),
    addBtn: document.getElementById('add-schedule-btn'),
    wizard: document.getElementById('wizard'),
    wizStep1: document.getElementById('wiz-step-1'),
    wizStep2: document.getElementById('wiz-step-2'),
    wizStep3: document.getElementById('wiz-step-3'),
    wizSteam: document.getElementById('wiz-steam'),
    wizDaysContent: document.getElementById('wiz-days-content'),
    wizDaysTitle: document.getElementById('wiz-days-title'),
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
    if (ss.shot_active) {
      el.shotIndicator.textContent = 'BREWING';
    } else {
      el.shotIndicator.textContent = ss.enabled ? 'IDLE' : 'OFF';
    }
    el.shotIndicator.classList.toggle('brewing', ss.shot_active);
    el.shotIndicator.classList.toggle('disabled', !ss.enabled);

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

  const RECURRENCE_LABELS = {
    once: '📌 Once',
    daily: '📅 Daily',
    weekly: '🔄 Weekly',
    biweekly: '🔁 Biweekly',
    monthly: '📆 Monthly'
  };
  const RECURRENCE_ICONS = { once: '📌', daily: '📅', weekly: '🔄', biweekly: '🔁', monthly: '📆' };

  // -- Schedule API --

  async function loadSchedule() {
    const res = await api('GET', '/schedules');
    if (res.schedules !== undefined) {
      scheduleData = res;
      renderScheduleList();
    }
  }

  async function createSchedule(sched) {
    const res = await api('POST', '/schedules', sched);
    if (res.ok) { scheduleData = res; renderScheduleList(); }
  }

  async function updateSchedule(id, sched) {
    const res = await api('PUT', `/schedules/${id}`, sched);
    if (res.ok) { scheduleData = res; renderScheduleList(); }
  }

  async function deleteSchedule(id) {
    const res = await api('DELETE', `/schedules/${id}`);
    if (res.ok) { scheduleData = res; renderScheduleList(); }
  }

  async function toggleSchedule(id) {
    const res = await api('POST', `/schedules/${id}/toggle`);
    if (res.ok) { scheduleData = res; renderScheduleList(); }
  }

  // -- Schedule List Render --

  function renderScheduleList() {
    const { schedules, next_event } = scheduleData;

    // Banner
    if (next_event) {
      const dayLabel = DAY_SHORT[DAYS.indexOf(next_event.day)] || next_event.day;
      const dateStr = next_event.date ? formatDateShort(next_event.date) : '';
      el.bannerText.textContent = `Turns on ${dayLabel} ${dateStr} at ${formatTime12(next_event.hour, next_event.minute)}`;
    } else {
      el.bannerText.textContent = schedules.length ? 'No upcoming events' : 'No schedules yet';
    }

    // Schedule cards
    const list = el.scheduleList;
    list.innerHTML = '';

    for (const s of schedules) {
      const card = document.createElement('div');
      card.className = 'sched-card';

      const icon = document.createElement('div');
      icon.className = 'sched-icon';
      icon.textContent = RECURRENCE_ICONS[s.recurrence] || '📅';

      const details = document.createElement('div');
      details.className = 'sched-details';
      details.innerHTML =
        '<div class="sched-name">' + (s.name || 'Schedule') + '</div>' +
        '<div class="sched-summary">' + (s.summary || '') + '</div>';

      // Tap card to edit
      details.addEventListener('click', () => openWizard(s));

      const actions = document.createElement('div');
      actions.className = 'sched-actions';

      // Toggle
      const toggle = document.createElement('button');
      toggle.className = 'day-toggle' + (s.enabled ? ' on' : '');
      toggle.addEventListener('click', (e) => { e.stopPropagation(); toggleSchedule(s.id); });

      // Delete
      const del = document.createElement('button');
      del.className = 'sched-delete';
      del.textContent = '✕';
      del.addEventListener('click', (e) => { e.stopPropagation(); deleteSchedule(s.id); });

      actions.appendChild(toggle);
      actions.appendChild(del);

      card.appendChild(icon);
      card.appendChild(details);
      card.appendChild(actions);
      list.appendChild(card);
    }
  }

  // -- Scroll Wheel Picker --

  function createWheel(container, items, selectedIndex, onChange) {
    const scroller = container.querySelector('.wheel-scroller');
    scroller.innerHTML = '';
    const itemHeight = 44;
    // Use CSS-defined heights (offsetHeight may be 0 if not yet laid out)
    const isLg = container.classList.contains('wheel-picker-lg');
    const containerHeight = isLg ? 200 : 180;
    // CSS padding centers the selected item in the highlight zone
    const padPx = (containerHeight - itemHeight) / 2;
    scroller.style.paddingTop = padPx + 'px';
    scroller.style.paddingBottom = padPx + 'px';

    items.forEach((text, idx) => {
      const item = document.createElement('div');
      item.className = 'wheel-item';
      item.textContent = text;
      item.dataset.idx = idx;
      scroller.appendChild(item);
    });

    let currentIdx = selectedIndex;
    let startY = 0, startOffset = 0, dragging = false;

    function setIndex(idx) {
      idx = Math.max(0, Math.min(items.length - 1, idx));
      currentIdx = idx;
      scroller.style.transform = `translateY(${-idx * itemHeight}px)`;

      // Highlight selected
      scroller.querySelectorAll('.wheel-item').forEach(el => {
        el.classList.remove('selected', 'far');
        const elIdx = parseInt(el.dataset.idx);
        if (isNaN(elIdx)) return;
        if (elIdx === idx) el.classList.add('selected');
        else if (Math.abs(elIdx - idx) > 1) el.classList.add('far');
      });

      onChange(idx);
    }

    function handleStart(e) {
      dragging = true;
      startY = e.touches ? e.touches[0].clientY : e.clientY;
      startOffset = currentIdx * itemHeight;
      scroller.style.transition = 'none';
    }

    function handleMove(e) {
      if (!dragging) return;
      e.preventDefault();
      const y = e.touches ? e.touches[0].clientY : e.clientY;
      const dy = startY - y;
      const newIdx = Math.round((startOffset + dy) / itemHeight);
      setIndex(newIdx);
    }

    function handleEnd() {
      dragging = false;
      scroller.style.transition = 'transform 0.15s ease-out';
      setIndex(currentIdx);
    }

    container.addEventListener('touchstart', handleStart, { passive: true });
    container.addEventListener('touchmove', handleMove, { passive: false });
    container.addEventListener('touchend', handleEnd);
    container.addEventListener('mousedown', handleStart);
    container.addEventListener('mousemove', handleMove);
    container.addEventListener('mouseup', handleEnd);
    container.addEventListener('mouseleave', handleEnd);

    scroller.style.transition = 'transform 0.15s ease-out';
    setIndex(selectedIndex);

    return { getIndex: () => currentIdx, setIndex };
  }

  // -- Wizard State --
  let wizWheels = {};
  let wizState = {
    wake_hour: 4, wake_minute: 50, steam: true,
    recurrence: 'weekly',
    days: [], days_b: [], date: '', month_days: [],
    reference_date: '', name: '', id: '', enabled: true
  };

  function openWizard(existing) {
    if (existing) {
      editingSchedule = existing.id;
      wizState = { ...existing };
    } else {
      editingSchedule = null;
      wizState = {
        wake_hour: 4, wake_minute: 50, steam: true,
        recurrence: 'weekly', days: [], days_b: [],
        date: new Date().toISOString().split('T')[0],
        month_days: [], reference_date: '', name: '', id: '', enabled: true
      };
    }

    el.wizard.style.display = 'flex';
    el.addBtn.style.display = 'none';
    showWizStep(1);
  }

  function closeWizard() {
    el.wizard.style.display = 'none';
    el.addBtn.style.display = 'block';
    editingSchedule = null;
  }

  function showWizStep(step) {
    el.wizStep1.style.display = step === 1 ? 'flex' : 'none';
    el.wizStep2.style.display = step === 2 ? 'flex' : 'none';
    el.wizStep3.style.display = step === 3 ? 'flex' : 'none';

    if (step === 1) initStep1();
    if (step === 2) initStep2();
    if (step === 3) initStep3();
  }

  function initStep1() {
    // Convert 24h to 12h for wheel
    let h12 = wizState.wake_hour % 12;
    if (h12 === 0) h12 = 12;
    const isPM = wizState.wake_hour >= 12;

    const hours = Array.from({length: 12}, (_, i) => String(i + 1));
    const minutes = Array.from({length: 60}, (_, i) => String(i).padStart(2, '0'));

    wizWheels.hour = createWheel(
      document.getElementById('wiz-hour'), hours, h12 - 1,
      (idx) => { wizState._h12 = idx + 1; }
    );
    wizWheels.minute = createWheel(
      document.getElementById('wiz-minute'), minutes, wizState.wake_minute,
      (idx) => { wizState.wake_minute = idx; }
    );
    wizWheels.ampm = createWheel(
      document.getElementById('wiz-ampm'), ['AM', 'PM'], isPM ? 1 : 0,
      (idx) => { wizState._isPM = idx === 1; }
    );
    wizState._h12 = h12;
    wizState._isPM = isPM;

    el.wizSteam.className = 'day-toggle' + (wizState.steam ? ' on' : '');
  }

  function initStep2() {
    const types = ['once', 'daily', 'weekly', 'biweekly', 'monthly'];
    const labels = types.map(t => RECURRENCE_LABELS[t]);
    const idx = types.indexOf(wizState.recurrence);

    wizWheels.recurrence = createWheel(
      document.getElementById('wiz-recurrence'), labels, Math.max(0, idx),
      (idx) => { wizState.recurrence = types[idx]; }
    );
  }

  function initStep3() {
    const content = el.wizDaysContent;
    content.innerHTML = '';

    const rec = wizState.recurrence;

    if (rec === 'once') {
      el.wizDaysTitle.textContent = '📋 Pick Date';
      // Parse existing date or use today
      const d = wizState.date ? new Date(wizState.date + 'T00:00:00') : new Date();
      const selMonth = d.getMonth();
      const selDay = d.getDate() - 1;
      const selYear = d.getFullYear();

      const row = document.createElement('div');
      row.className = 'wheel-row';

      // Month wheel
      const monthContainer = document.createElement('div');
      monthContainer.className = 'wheel-picker wheel-picker-md';
      monthContainer.innerHTML = '<div class="wheel-highlight"></div><div class="wheel-scroller"></div>';
      row.appendChild(monthContainer);

      // Day wheel
      const dayContainer = document.createElement('div');
      dayContainer.className = 'wheel-picker';
      dayContainer.innerHTML = '<div class="wheel-highlight"></div><div class="wheel-scroller"></div>';
      row.appendChild(dayContainer);

      // Year wheel
      const yearContainer = document.createElement('div');
      yearContainer.className = 'wheel-picker wheel-picker-md';
      yearContainer.innerHTML = '<div class="wheel-highlight"></div><div class="wheel-scroller"></div>';
      row.appendChild(yearContainer);

      content.appendChild(row);

      const years = Array.from({length: 5}, (_, i) => String(selYear + i - 1));
      const yearBaseIdx = 1; // current year at index 1

      let pickedMonth = selMonth;
      let pickedDay = selDay;
      let pickedYear = selYear;

      function updateDate() {
        const m = String(pickedMonth + 1).padStart(2, '0');
        const dd = String(pickedDay + 1).padStart(2, '0');
        wizState.date = `${pickedYear}-${m}-${dd}`;
      }

      wizWheels.dateMonth = createWheel(monthContainer, MONTH_SHORT, selMonth, (idx) => {
        pickedMonth = idx;
        updateDate();
      });
      wizWheels.dateDay = createWheel(dayContainer, Array.from({length: 31}, (_, i) => String(i + 1)), selDay, (idx) => {
        pickedDay = idx;
        updateDate();
      });
      wizWheels.dateYear = createWheel(yearContainer, years, yearBaseIdx, (idx) => {
        pickedYear = parseInt(years[idx]);
        updateDate();
      });
      updateDate();

    } else if (rec === 'daily') {
      el.wizDaysTitle.textContent = '📋 Every Day';
      const msg = document.createElement('div');
      msg.style.cssText = 'text-align:center;color:var(--text-dim);font-size:16px;padding:40px 0;';
      msg.textContent = 'Fires every day — no selection needed';
      content.appendChild(msg);

    } else if (rec === 'weekly') {
      el.wizDaysTitle.textContent = '📋 Select Days';
      const picker = document.createElement('div');
      picker.className = 'day-picker';
      DAYS.forEach((day, i) => {
        const btn = document.createElement('button');
        btn.className = 'day-btn' + (wizState.days.includes(day) ? ' active' : '');
        btn.textContent = DAY_LETTER[i];
        btn.addEventListener('click', () => {
          btn.classList.toggle('active');
          if (wizState.days.includes(day)) {
            wizState.days = wizState.days.filter(d => d !== day);
          } else {
            wizState.days.push(day);
          }
        });
        picker.appendChild(btn);
      });
      content.appendChild(picker);

    } else if (rec === 'biweekly') {
      el.wizDaysTitle.textContent = '📋 Select Days (A & B)';

      // Week A
      const labelA = document.createElement('div');
      labelA.className = 'day-picker-label';
      labelA.textContent = 'Week A';
      content.appendChild(labelA);
      const pickerA = document.createElement('div');
      pickerA.className = 'day-picker';
      DAYS.forEach((day, i) => {
        const btn = document.createElement('button');
        btn.className = 'day-btn' + (wizState.days.includes(day) ? ' active' : '');
        btn.textContent = DAY_LETTER[i];
        btn.addEventListener('click', () => {
          btn.classList.toggle('active');
          if (wizState.days.includes(day)) {
            wizState.days = wizState.days.filter(d => d !== day);
          } else {
            wizState.days.push(day);
          }
        });
        pickerA.appendChild(btn);
      });
      content.appendChild(pickerA);

      // Week B
      const labelB = document.createElement('div');
      labelB.className = 'day-picker-label';
      labelB.textContent = 'Week B';
      content.appendChild(labelB);
      const pickerB = document.createElement('div');
      pickerB.className = 'day-picker';
      DAYS.forEach((day, i) => {
        const btn = document.createElement('button');
        btn.className = 'day-btn' + (wizState.days_b.includes(day) ? ' active' : '');
        btn.textContent = DAY_LETTER[i];
        btn.addEventListener('click', () => {
          btn.classList.toggle('active');
          if (wizState.days_b.includes(day)) {
            wizState.days_b = wizState.days_b.filter(d => d !== day);
          } else {
            wizState.days_b.push(day);
          }
        });
        pickerB.appendChild(btn);
      });
      content.appendChild(pickerB);

      // Set reference_date if not set (use last Monday)
      if (!wizState.reference_date) {
        const today = new Date();
        const dow = today.getDay();
        const diff = dow === 0 ? 6 : dow - 1;
        const monday = new Date(today);
        monday.setDate(today.getDate() - diff);
        wizState.reference_date = monday.toISOString().split('T')[0];
      }

    } else if (rec === 'monthly') {
      el.wizDaysTitle.textContent = '📋 Select Days of Month';
      const grid = document.createElement('div');
      grid.className = 'month-grid';
      for (let d = 1; d <= 31; d++) {
        const btn = document.createElement('button');
        btn.className = 'month-day-btn' + (wizState.month_days.includes(d) ? ' active' : '');
        btn.textContent = d;
        btn.addEventListener('click', () => {
          btn.classList.toggle('active');
          if (wizState.month_days.includes(d)) {
            wizState.month_days = wizState.month_days.filter(x => x !== d);
          } else {
            wizState.month_days.push(d);
          }
        });
        grid.appendChild(btn);
      }
      content.appendChild(grid);
    }
  }

  async function saveWizard() {
    // Convert 12h → 24h
    let h24 = wizState._h12;
    if (wizState._isPM && h24 !== 12) h24 += 12;
    if (!wizState._isPM && h24 === 12) h24 = 0;

    const sched = {
      id: wizState.id || '',
      name: wizState.name || autoName(),
      enabled: wizState.enabled !== false,
      wake_hour: h24,
      wake_minute: wizState.wake_minute,
      off_hour: 23, off_minute: 0,
      steam: wizState.steam,
      recurrence: wizState.recurrence,
      date: wizState.date || '',
      days: wizState.days || [],
      days_b: wizState.days_b || [],
      reference_date: wizState.reference_date || '',
      month_days: wizState.month_days || []
    };

    const isEditing = editingSchedule;
    closeWizard();
    if (isEditing) {
      await updateSchedule(isEditing, sched);
    } else {
      await createSchedule(sched);
    }
  }

  function autoName() {
    const rec = wizState.recurrence;
    if (rec === 'once') return 'One-time';
    if (rec === 'daily') return 'Daily';
    if (rec === 'weekly') return 'Weekly';
    if (rec === 'biweekly') return 'Biweekly';
    if (rec === 'monthly') return 'Monthly';
    return 'Schedule';
  }

  // -- Wizard Event Wiring --

  el.addBtn.addEventListener('click', () => openWizard(null));
  document.getElementById('wiz-cancel').addEventListener('click', closeWizard);
  document.getElementById('wiz-next-1').addEventListener('click', () => showWizStep(2));
  document.getElementById('wiz-back-2').addEventListener('click', () => showWizStep(1));
  document.getElementById('wiz-next-2').addEventListener('click', () => showWizStep(3));
  document.getElementById('wiz-back-3').addEventListener('click', () => showWizStep(2));
  document.getElementById('wiz-save').addEventListener('click', saveWizard);
  el.wizSteam.addEventListener('click', () => {
    wizState.steam = !wizState.steam;
    el.wizSteam.className = 'day-toggle' + (wizState.steam ? ' on' : '');
  });

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

  // ShotStopper enable/disable toggle
  el.shotIndicator.addEventListener('click', async () => {
    const res = await api('POST', '/shotstopper/toggle');
    if (res && res.ok) {
      state.shotstopper.enabled = res.enabled;
      render();
    }
  });

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

  api('GET', '/status').then(data => {
    if (data.shotstopper) state.shotstopper = data.shotstopper;
    if (data.lamarzocco) state.lamarzocco = data.lamarzocco;
    render();
    connectWS();
  });

})();

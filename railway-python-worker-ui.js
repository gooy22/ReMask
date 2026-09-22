/* REMASK_PYTHON_WORKER_UI_V1 REMASK_PYTHON_WORKER_UI_V2 REMASK_PYTHON_WORKER_UI_V3 REMASK_PYTHON_WORKER_UI_V133 REMASK_PYTHON_WORKER_UI_V134 REMASK_PYTHON_WORKER_UI_V135 REMASK_PYTHON_WORKER_UI_V136 REMASK_PYTHON_WORKER_UI_V137 REMASK_PYTHON_WORKER_UI_V138 */
const restoredPythonWorkerJobId = localStorage.getItem('remask_python_worker_job_v1') || '';

const pythonWorkerUiState = {
  jobId: restoredPythonWorkerJobId,
  job: null,
  busy: restoredPythonWorkerJobId !== '',
  workerOnline: null,
  pollTimer: null,
  polling: false
};

window.pythonWorkerUiState = pythonWorkerUiState;

function pythonWorkerSelectedProfiles() {
  try {
    if (typeof selectedRows !== 'function' || !state || !state.activeTab) return [];
    const rows = selectedRows(state.activeTab) || [];
    const ids = [];
    for (const row of rows) {
      let profile = '';
      if (state.activeTab === 'profiles') profile = String((row && (row.name || row.profile)) || '').trim();
      else profile = String((row && (row.profile || row.profile_name)) || '').trim();
      if (profile) ids.push(profile);
    }
    return Array.from(new Set(ids));
  } catch (_) {
    return [];
  }
}

function pythonWorkerEl(id) {
  return document.getElementById(id);
}

function pythonWorkerSetText(id, value) {
  const el = pythonWorkerEl(id);
  if (el) el.textContent = String(value == null ? '' : value);
}

function pythonWorkerSelectionRefresh() {
  const profiles = pythonWorkerSelectedProfiles();
  const start = pythonWorkerEl('pythonProvisionStart');

  if (start) {
    start.disabled = pythonWorkerUiState.busy || profiles.length === 0 || pythonWorkerUiState.workerOnline === false;
    start.textContent = profiles.length
      ? 'Add BM (' + profiles.length + ')'
      : 'Add BM';
  }

  document
    .querySelectorAll('.js-btn-add-bm, #btn_add_bm, .js-python-add-bm')
    .forEach(function(btn) {
      btn.disabled = pythonWorkerUiState.busy;
      btn.classList.toggle('is-loading', pythonWorkerUiState.busy);
    });

  const retry = pythonWorkerEl('pythonProvisionRetry');
  if (retry) {
    const items = pythonWorkerUiState.job && Array.isArray(pythonWorkerUiState.job.items)
      ? pythonWorkerUiState.job.items
      : [];
    const hasFailed = items.some(function(item) {
      return item && String(item.status || '').toUpperCase() === 'FAILED';
    });
    retry.disabled = pythonWorkerUiState.busy || !pythonWorkerUiState.jobId || !hasFailed;
  }

  if (!pythonWorkerUiState.jobId && !pythonWorkerUiState.busy) {
    pythonWorkerSetText(
      'pythonPwStatus',
      profiles.length
        ? 'Worker UI v138 · Выбрано FB-профилей: ' + profiles.length + '. Готово к Add BM.'
        : 'Worker UI v138 · Выберите FB-профили в Workspace.'
    );
  }
}

async function pythonWorkerBridge(payload) {
  const response = await fetch('ajax/pythonWorkerJobs.php', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
    body: JSON.stringify(payload)
  });

  let data = null;
  try {
    data = await response.json();
  } catch (_) {
    throw new Error('Worker bridge returned invalid JSON (HTTP ' + response.status + ').');
  }

  if (!response.ok || !(data && data.ok)) {
    throw new Error(String((data && (data.message || data.error)) || ('Worker bridge HTTP ' + response.status)));
  }
  return data;
}

async function pythonWorkerHealthCheck() {
  const el = pythonWorkerEl('pythonPwWorkerHealth');
  if (!el) return false;

  el.dataset.state = 'checking';
  el.textContent = 'Worker: проверяю…';

  try {
    const data = await pythonWorkerBridge({action: 'health'});
    const worker = data && data.worker ? data.worker : {};
    const queued = Number(worker.queued_items || 0);
    const concurrency = Number(worker.worker_concurrency || 0);
    const profilesVisible = Number(worker.profiles_visible || 0);

    pythonWorkerUiState.workerOnline = true;
    el.dataset.state = 'online';
    el.textContent =
      'Worker: READY · профили ' + profilesVisible +
      ' · очередь ' + queued +
      ' · concurrency ' + concurrency;
    pythonWorkerSelectionRefresh();
    return true;
  } catch (error) {
    pythonWorkerUiState.workerOnline = false;
    el.dataset.state = 'offline';
    el.textContent = 'Worker: OFFLINE · ' + String((error && error.message) || error);
    pythonWorkerSelectionRefresh();
    return false;
  }
}

function pythonWorkerCurrentStep(item) {
  const steps = Array.isArray(item && item.provisioning_steps)
    ? item.provisioning_steps
    : [];

  const runningStep = steps.find(function(step) {
    return step && String(step.status || '').toUpperCase() === 'RUNNING';
  });
  if (runningStep) return String(runningStep.step || 'RUNNING');

  const failedStep = steps.find(function(step) {
    return step && String(step.status || '').toUpperCase() === 'FAILED';
  });
  if (failedStep) return String(failedStep.step || 'FAILED');

  if (steps.length) {
    const lastStep = steps[steps.length - 1];
    if (lastStep && lastStep.step) return String(lastStep.step);
  }

  const tasks = Array.isArray(item && item.tasks) ? item.tasks : [];
  const running = tasks.find(function(t){ return t && t.status === 'RUNNING'; });
  if (running) return String(running.action || 'RUNNING');
  const failed = tasks.find(function(t){ return t && t.status === 'FAILED'; });
  if (failed) return String(failed.action || 'FAILED');
  const queued = tasks.find(function(t){ return t && t.status === 'QUEUED'; });
  if (queued) return String(queued.action || 'QUEUED');
  const success = tasks.slice().reverse().find(function(t){ return t && t.status === 'SUCCESS'; });
  return success ? String(success.action || 'SUCCESS') : '—';
}

function pythonWorkerRenderJob(job) {
  pythonWorkerUiState.job = job;
  const items = Array.isArray(job && job.items) ? job.items : [];
  const counts = {QUEUED: 0, RUNNING: 0, SUCCESS: 0, FAILED: 0};

  for (const item of items) {
    const status = String((item && item.status) || 'QUEUED').toUpperCase();
    if (Object.prototype.hasOwnProperty.call(counts, status)) counts[status]++;
  }

  const total = items.length;
  const done = counts.SUCCESS + counts.FAILED;
  const progress = total > 0 ? Math.round((done / total) * 100) : 0;

  pythonWorkerSetText('pythonPwTotal', total);
  pythonWorkerSetText('pythonPwQueued', counts.QUEUED);
  pythonWorkerSetText('pythonPwRunning', counts.RUNNING);
  pythonWorkerSetText('pythonPwSuccess', counts.SUCCESS);
  pythonWorkerSetText('pythonPwFailed', counts.FAILED);
  pythonWorkerSetText('pythonPwJob', job && job.id ? 'Job: ' + job.id : '');

  const bar = pythonWorkerEl('pythonPwProgress');
  if (bar) bar.style.width = progress + '%';

  pythonWorkerSetText(
    'pythonPwStatus',
    'Status: ' + String((job && job.status) || 'UNKNOWN') + ' · ' + done + '/' + total + ' завершено · ' + progress + '%'
  );

  const body = pythonWorkerEl('pythonPwRows');
  if (body) {
    body.textContent = '';
    if (!items.length) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 4;
      td.textContent = 'Job не содержит JobItem.';
      tr.appendChild(td);
      body.appendChild(tr);
    } else {
      for (const item of items) {
        const tr = document.createElement('tr');

        const profileTd = document.createElement('td');
        profileTd.textContent = String((item && item.profile_id) || '—');

        const statusTd = document.createElement('td');
        const pill = document.createElement('span');
        const itemStatus = String((item && item.status) || 'QUEUED').toUpperCase();
        pill.className = 'pw-pill pw-' + itemStatus;
        pill.textContent = itemStatus;
        statusTd.appendChild(pill);

        const stepTd = document.createElement('td');
        stepTd.textContent = pythonWorkerCurrentStep(item);

        const errorTd = document.createElement('td');
        errorTd.className = 'pw-error';
        const errorParts = [];
        if (item && item.error_code) errorParts.push(item.error_code);
        if (item && item.error_message) errorParts.push(item.error_message);
        errorTd.textContent = errorParts.length ? errorParts.join(': ') : '—';

        tr.appendChild(profileTd);
        tr.appendChild(statusTd);
        tr.appendChild(stepTd);
        tr.appendChild(errorTd);
        body.appendChild(tr);
      }
    }
  }

  const retry = pythonWorkerEl('pythonProvisionRetry');
  if (retry) retry.disabled = pythonWorkerUiState.busy || counts.FAILED === 0;
}

function pythonWorkerSchedulePoll(delay) {
  const ms = Number(delay || 900);
  if (pythonWorkerUiState.pollTimer) clearTimeout(pythonWorkerUiState.pollTimer);
  pythonWorkerUiState.pollTimer = setTimeout(function() {
    pythonWorkerPoll().catch(function(){});
  }, ms);
}

function pythonWorkerFindBmDialog(button) {
  if (button && typeof button.closest === 'function') {
    const ownDialog = button.closest('[role="dialog"], .modal, .modal-content');
    if (ownDialog && /Добавить Business Manager/i.test(String(ownDialog.textContent || ''))) {
      return ownDialog;
    }
  }

  const nodes = Array.from(document.querySelectorAll('[role="dialog"], .modal, .modal-content'));
  return nodes.find(function(node) {
    return /Добавить Business Manager/i.test(String(node.textContent || ''));
  }) || null;
}

function pythonWorkerPrimaryPageSelects(dialog) {
  if (!dialog) return [];

  return Array.from(dialog.querySelectorAll('select')).filter(function(select) {
    const optionText = Array.from(select.options || []).map(function(option) {
      return String(option.textContent || '');
    }).join(' ');

    const parentText = String((select.parentElement && select.parentElement.textContent) || '');
    return /Primary Page/i.test(optionText) || /Primary Page/i.test(parentText);
  });
}

function pythonWorkerResolveBmName(button) {
  let value = '';
  const dialog = pythonWorkerFindBmDialog(button);

  if (button && button.dataset && button.dataset.bmName) {
    value = String(button.dataset.bmName).trim();
  }

  if (!value && dialog) {
    const inputs = Array.from(dialog.querySelectorAll('input'));
    const candidate = inputs.find(function(input) {
      const type = String(input.type || 'text').toLowerCase();
      const key = [
        input.name || '',
        input.id || '',
        input.className || '',
        input.placeholder || ''
      ].join(' ');
      if (type === 'email' || type === 'hidden' || type === 'checkbox' || type === 'radio') return false;
      if (/email|почт/i.test(key)) return false;
      return type === 'text' || type === 'search' || type === '';
    });
    if (candidate) value = String(candidate.value || '').trim();
  }

  if (!value) {
    const input = document.querySelector(
      '#bm_name_input_field, #bm_name, .js-bm-name-input'
    );
    if (input) value = String(input.value || '').trim();
  }

  return value;
}

function pythonWorkerBmRowConfig(dialog, profiles, fallbackName) {
  const result = {};
  if (!dialog || !Array.isArray(profiles) || !profiles.length) return result;

  const pageSelects = pythonWorkerPrimaryPageSelects(dialog);
  const tableRows = Array.from(dialog.querySelectorAll('tr'));

  profiles.forEach(function(profileId, index) {
    const profile = String(profileId || '').trim();
    let row = tableRows.find(function(candidate) {
      return profile && String(candidate.textContent || '').indexOf(profile) !== -1;
    });

    if (!row && pageSelects[index]) {
      row = pageSelects[index].closest('tr');
    }

    let name = String(fallbackName || '').trim();
    let pageId = '';

    if (row) {
      const rowInputs = Array.from(row.querySelectorAll('input'));
      const nameInput = rowInputs.find(function(input) {
        const type = String(input.type || 'text').toLowerCase();
        const key = [
          input.name || '',
          input.id || '',
          input.className || '',
          input.placeholder || ''
        ].join(' ');
        if (type === 'email' || type === 'hidden' || type === 'checkbox' || type === 'radio') return false;
        if (/email|почт/i.test(key)) return false;
        return type === 'text' || type === 'search' || type === '';
      });
      if (nameInput && String(nameInput.value || '').trim()) {
        name = String(nameInput.value || '').trim();
      }

      const rowSelect = Array.from(row.querySelectorAll('select')).find(function(select) {
        const optionText = Array.from(select.options || []).map(function(option) {
          return String(option.textContent || '');
        }).join(' ');
        const parentText = String((select.parentElement && select.parentElement.textContent) || '');
        return /Primary Page/i.test(optionText) || /Primary Page/i.test(parentText);
      });

      if (rowSelect) pageId = String(rowSelect.value || '').trim();
    }

    if (!pageId && pageSelects[index]) {
      pageId = String(pageSelects[index].value || '').trim();
    }

    result[profile] = {
      name: name,
      page_id: pageId
    };
  });

  return result;
}

async function pythonWorkerRefreshProfile(profileId) {
  if (typeof apiJson !== 'function' || typeof post !== 'function') {
    return false;
  }

  try {
    const data = await apiJson(
      'ajax/metaHierarchy.php',
      post({
        action: 'sync_profile',
        profile: profileId
      })
    );

    if (typeof applySnapshot === 'function') applySnapshot(data);
    if (typeof render === 'function') render();
    return true;
  } catch (error) {
    console.error('[ReMask Worker UI] sync_profile failed for ' + profileId + ':', error);
    return false;
  }
}

async function pythonWorkerRefreshSuccessfulProfiles(items) {
  const profiles = Array.from(new Set(
    (Array.isArray(items) ? items : [])
      .filter(function(item) {
        return item && String(item.status || '').toUpperCase() === 'SUCCESS';
      })
      .map(function(item) {
        return String(item.profile_id || '').trim();
      })
      .filter(Boolean)
  ));

  let needsReload = false;

  for (const profileId of profiles) {
    const ok = await pythonWorkerRefreshProfile(profileId);
    if (!ok) needsReload = true;
  }

  if (needsReload && profiles.length) {
    setTimeout(function() {
      window.location.reload();
    }, 800);
  }
}

async function pythonWorkerStartBusiness(bmName, options) {
  const explicitProfiles = options && Array.isArray(options.profiles)
    ? options.profiles.map(function(value) { return String(value || '').trim(); }).filter(Boolean)
    : [];
  const profiles = explicitProfiles.length ? explicitProfiles : pythonWorkerSelectedProfiles();
  if (!profiles.length || pythonWorkerUiState.busy) return;

  const cleanName = String(bmName || '').trim();
  const explicitConfig = options && options.configs && typeof options.configs === 'object'
    ? options.configs
    : null;
  const dialog = options && options.dialog ? options.dialog : null;
  const rowConfig = explicitConfig || pythonWorkerBmRowConfig(dialog, profiles, cleanName);

  const invalid = profiles.filter(function(profileId) {
    const cfg = rowConfig[String(profileId)] || {};
    return !String(cfg.name || cleanName || '').trim() || !String(cfg.page_id || '').trim();
  });

  if (invalid.length) {
    throw new Error(
      'Add BM request not sent: для каждого профиля нужны название BM и Primary Page. Проблема: ' +
      invalid.join(', ')
    );
  }

  pythonWorkerUiState.busy = true;
  pythonWorkerSelectionRefresh();
  pythonWorkerSetText(
    'pythonPwStatus',
    'Создаю Add BM Job для ' + profiles.length + ' FB-профилей...'
  );

  try {
    const nonce = Date.now() + '-' + Math.random().toString(16).slice(2);

    const payloadProfiles = profiles.map(function(profileId, index) {
      const profileKey = String(profileId);
      const config = rowConfig[profileKey] || {};
      const businessParams = {
        name: String(config.name || cleanName).trim()
      };

      const pageId = String(config.page_id || '').trim();
      if (pageId) businessParams.page_id = pageId;

      return {
        profile_id: profileKey,
        tasks: [
          {
            action: 'provisioning',
            idempotency_key: 'add-bm-' + nonce + '-' + index,
            payload: {
              steps: ['PROXY_CHECK', 'BUSINESS'],
              scope_key: 'add-bm-' + nonce,
              parameters: {
                BUSINESS: businessParams
              }
            }
          }
        ]
      };
    });

    pythonWorkerSetText(
      'pythonPwStatus',
      'POST /ajax/pythonWorkerJobs.php → создаю Job…'
    );
    pythonWorkerSetText('pythonPwJob', 'Запрос отправляется…');

    const data = await pythonWorkerBridge({
      action: 'create',
      idempotency_key: 'workspace-add-bm-' + nonce,
      profiles: payloadProfiles
    });

    pythonWorkerSetText('pythonPwStatus', 'Worker bridge ответил. Читаю Job ID…');

    const jobId = String((data && data.job && data.job.job_id) || '').trim();
    if (!jobId) throw new Error('Worker did not return job_id.');

    pythonWorkerUiState.jobId = jobId;
    localStorage.setItem('remask_python_worker_job_v1', jobId);

    pythonWorkerSetText('pythonPwJob', 'Job: ' + jobId);
    pythonWorkerSetText('pythonPwStatus', 'Создание Business Manager запущено...');

    pythonWorkerPoll().catch(function(error) {
      pythonWorkerSetText(
        'pythonPwStatus',
        'Ошибка polling: ' + ((error && error.message) || error)
      );
    });
  } catch (error) {
    pythonWorkerUiState.busy = false;
    pythonWorkerSelectionRefresh();
    pythonWorkerSetText(
      'pythonPwStatus',
      'Add BM: ' + ((error && error.message) || error)
    );
    console.error('[ReMask Worker UI] Add BM launch failed:', error);
    throw error;
  }
}

function pythonWorkerVisible(el) {
  if (!el) return false;
  if (el.disabled) return false;
  const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
  if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
  return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
}

function pythonWorkerFindLegacyBmTrigger() {
  const selectors = '.js-btn-add-bm, #btn_add_bm, .js-python-add-bm';
  const exact = Array.from(document.querySelectorAll(selectors)).filter(function(el) {
    return el.id !== 'pythonProvisionStart' && !el.hasAttribute('data-python-worker-create-bm');
  });

  const visibleExact = exact.find(pythonWorkerVisible);
  if (visibleExact) return visibleExact;
  if (exact.length) return exact[0];

  const candidates = Array.from(
    document.querySelectorAll('button, a, [role="button"], [data-action]')
  ).filter(function(el) {
    if (el.id === 'pythonProvisionStart') return false;
    if (el.hasAttribute('data-python-worker-create-bm')) return false;
    const label = String(el.textContent || el.value || el.getAttribute('aria-label') || '')
      .replace(/\\s+/g, ' ')
      .trim();
    return /^(Добавить\\s*(?:BM|Business Manager)|Add\\s*(?:BM|Business Manager))$/i.test(label);
  });

  return candidates.find(pythonWorkerVisible) || candidates[0] || null;
}

async function pythonWorkerLoadPages(profileId) {
  const response = await fetch('ajax/pythonWorkerPages.php', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
    body: JSON.stringify({profile: String(profileId || '').trim()})
  });

  let data = null;
  try {
    data = await response.json();
  } catch (_) {
    throw new Error('Pages endpoint returned invalid JSON (HTTP ' + response.status + ').');
  }

  if (!response.ok || !(data && data.ok)) {
    const detail = data && data.detail && typeof data.detail === 'object'
      ? (data.detail.message || JSON.stringify(data.detail))
      : '';
    throw new Error(
      String(detail || (data && data.error) || ('Pages HTTP ' + response.status))
    );
  }

  return Array.isArray(data.pages) ? data.pages : [];
}

function pythonWorkerEnsureBmModalStyle() {
  if (document.getElementById('pythonWorkerBmModalStyle')) return;

  const style = document.createElement('style');
  style.id = 'pythonWorkerBmModalStyle';
  style.textContent = [
    '#pythonWorkerBmModal{position:fixed;inset:0;z-index:10050;background:rgba(5,8,12,.72);display:flex;align-items:center;justify-content:center;padding:24px}',
    '#pythonWorkerBmModal .pwbm-card{width:min(920px,96vw);max-height:88vh;overflow:auto;background:#20242b;border:1px solid #3a424f;border-radius:12px;box-shadow:0 24px 70px rgba(0,0,0,.45)}',
    '#pythonWorkerBmModal .pwbm-head{display:flex;align-items:center;justify-content:space-between;padding:16px 18px;border-bottom:1px solid #343b46}',
    '#pythonWorkerBmModal .pwbm-title{font-size:16px;font-weight:700}',
    '#pythonWorkerBmModal .pwbm-close{border:0;background:transparent;color:#aeb7c4;font-size:22px;cursor:pointer}',
    '#pythonWorkerBmModal .pwbm-body{padding:16px 18px}',
    '#pythonWorkerBmModal .pwbm-note{font-size:12px;color:#9aa4b2;margin-bottom:12px}',
    '#pythonWorkerBmModal .pwbm-row{display:grid;grid-template-columns:minmax(130px,.7fr) minmax(170px,1fr) minmax(260px,1.4fr);gap:10px;align-items:start;padding:12px 0;border-bottom:1px solid #303640}',
    '#pythonWorkerBmModal .pwbm-row:last-child{border-bottom:0}',
    '#pythonWorkerBmModal .pwbm-profile{font-size:12px;font-weight:600;padding-top:9px;word-break:break-word}',
    '#pythonWorkerBmModal input,#pythonWorkerBmModal select{width:100%;min-height:38px;background:#1b1f25;border:1px solid #414a58;color:#e6ebf2;border-radius:7px;padding:7px 9px}',
    '#pythonWorkerBmModal .pwbm-manual{margin-top:7px}',
    '#pythonWorkerBmModal .pwbm-field small{display:block;margin-top:5px;color:#8f99a8;font-size:11px}',
    '#pythonWorkerBmModal .pwbm-field small.error{color:#ff8f96}',
    '#pythonWorkerBmModal .pwbm-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 18px;border-top:1px solid #343b46}',
    '#pythonWorkerBmModal .pwbm-status{font-size:12px;color:#aeb7c4;white-space:pre-wrap}',
    '#pythonWorkerBmModal .pwbm-actions{display:flex;gap:8px}',
    '@media(max-width:760px){#pythonWorkerBmModal .pwbm-row{grid-template-columns:1fr}}'
  ].join('');
  document.head.appendChild(style);
}

function pythonWorkerCloseOwnBmModal() {
  const modal = document.getElementById('pythonWorkerBmModal');
  if (modal) modal.remove();
}

async function pythonWorkerOpenOwnBmModal() {
  if (pythonWorkerUiState.workerOnline === false) {
    pythonWorkerSetText(
      'pythonPwStatus',
      'Add BM недоступен: локальный Python worker OFFLINE. Смотри индикатор Worker.'
    );
    return;
  }

  const profiles = pythonWorkerSelectedProfiles();
  if (!profiles.length) {
    pythonWorkerSetText('pythonPwStatus', 'Сначала выбери хотя бы один FB-профиль.');
    return;
  }

  pythonWorkerEnsureBmModalStyle();
  pythonWorkerCloseOwnBmModal();

  const modal = document.createElement('div');
  modal.id = 'pythonWorkerBmModal';

  const card = document.createElement('div');
  card.className = 'pwbm-card';

  const head = document.createElement('div');
  head.className = 'pwbm-head';

  const title = document.createElement('div');
  title.className = 'pwbm-title';
  title.textContent = 'Добавить Business Manager · ' + profiles.length + ' проф.';

  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'pwbm-close';
  close.setAttribute('aria-label', 'Закрыть');
  close.textContent = '×';
  close.addEventListener('click', pythonWorkerCloseOwnBmModal);

  head.appendChild(title);
  head.appendChild(close);

  const body = document.createElement('div');
  body.className = 'pwbm-body';

  const note = document.createElement('div');
  note.className = 'pwbm-note';
  note.textContent = 'Pages загружаются из текущего профиля. Для каждого профиля укажи отдельное название BM и Primary Page.';
  body.appendChild(note);

  const rows = {};
  for (let index = 0; index < profiles.length; index++) {
    const profileId = profiles[index];

    const row = document.createElement('div');
    row.className = 'pwbm-row';
    row.dataset.profile = profileId;

    const profile = document.createElement('div');
    profile.className = 'pwbm-profile';
    profile.textContent = profileId;

    const nameField = document.createElement('div');
    nameField.className = 'pwbm-field';
    const name = document.createElement('input');
    name.type = 'text';
    name.value = 'ReMask_BM_' + (index + 1);
    name.placeholder = 'Название Business Manager';
    const nameHint = document.createElement('small');
    nameHint.textContent = 'Название BM';
    nameField.appendChild(name);
    nameField.appendChild(nameHint);

    const pageField = document.createElement('div');
    pageField.className = 'pwbm-field';

    const page = document.createElement('select');
    page.disabled = true;
    const loading = document.createElement('option');
    loading.value = '';
    loading.textContent = 'Загружаю Pages…';
    page.appendChild(loading);

    const manualPage = document.createElement('input');
    manualPage.type = 'text';
    manualPage.className = 'pwbm-manual';
    manualPage.placeholder = 'Или введи Primary Page ID вручную';
    manualPage.inputMode = 'numeric';

    const pageHint = document.createElement('small');
    pageHint.textContent = 'Primary Page: загрузка…';

    pageField.appendChild(page);
    pageField.appendChild(manualPage);
    pageField.appendChild(pageHint);

    row.appendChild(profile);
    row.appendChild(nameField);
    row.appendChild(pageField);
    body.appendChild(row);

    rows[profileId] = {
      row: row,
      name: name,
      page: page,
      manualPage: manualPage,
      pageHint: pageHint,
      loaded: false,
      error: ''
    };
  }

  const footer = document.createElement('div');
  footer.className = 'pwbm-footer';

  const status = document.createElement('div');
  status.className = 'pwbm-status';
  status.textContent = 'Загружаю Primary Pages…';

  const actions = document.createElement('div');
  actions.className = 'pwbm-actions';

  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'btn btn-secondary';
  cancel.textContent = 'Отмена';
  cancel.addEventListener('click', pythonWorkerCloseOwnBmModal);

  const create = document.createElement('button');
  create.type = 'button';
  create.className = 'btn btn-primary';
  create.textContent = 'Создать BM';
  create.disabled = true;

  actions.appendChild(cancel);
  actions.appendChild(create);
  footer.appendChild(status);
  footer.appendChild(actions);

  card.appendChild(head);
  card.appendChild(body);
  card.appendChild(footer);
  modal.appendChild(card);
  document.body.appendChild(modal);

  const refreshReadyState = function() {
    const allLoaded = profiles.every(function(profileId) {
      return rows[profileId] && rows[profileId].loaded;
    });
    const allReady = profiles.every(function(profileId) {
      const cfg = rows[profileId];
      const selectedPage = cfg
        ? String(cfg.page.value || cfg.manualPage.value || '').trim()
        : '';
      return cfg &&
        String(cfg.name.value || '').trim() &&
        selectedPage;
    });

    create.disabled = pythonWorkerUiState.busy || !allLoaded || !allReady;

    if (!allLoaded) {
      status.textContent = 'Загружаю Primary Pages…';
    } else {
      const failed = profiles.filter(function(profileId) {
        const cfg = rows[profileId];
        if (!cfg) return true;
        const effectivePage = String(cfg.page.value || cfg.manualPage.value || '').trim();
        return !String(cfg.name.value || '').trim() || !effectivePage;
      });
      status.textContent = failed.length
        ? 'Не готовы профили: ' + failed.join(', ')
        : 'Готово к отправке Job: ' + profiles.length + '.';
    }
  };

  for (const profileId of profiles) {
    const cfg = rows[profileId];
    cfg.name.addEventListener('input', refreshReadyState);
    cfg.page.addEventListener('change', function() {
      if (String(cfg.page.value || '').trim()) cfg.manualPage.value = '';
      refreshReadyState();
    });
    cfg.manualPage.addEventListener('input', function() {
      if (String(cfg.manualPage.value || '').trim()) cfg.page.value = '';
      refreshReadyState();
    });

    pythonWorkerLoadPages(profileId).then(function(pages) {
      cfg.page.textContent = '';

      if (!pages.length) {
        const empty = document.createElement('option');
        empty.value = '';
        empty.textContent = 'Meta вернула 0 Pages';
        cfg.page.appendChild(empty);
        cfg.error = 'Meta вернула 0 Pages';
        cfg.pageHint.className = 'error';
        cfg.pageHint.textContent = 'Meta вернула 0 Pages. Введи Primary Page ID вручную.';
      } else {
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = 'Выбери Primary Page';
        cfg.page.appendChild(placeholder);

        for (const item of pages) {
          const option = document.createElement('option');
          option.value = String(item.id || '');
          option.textContent = String(item.name || item.id || 'Page') + ' — ' + String(item.id || '');
          cfg.page.appendChild(option);
        }

        cfg.page.value = String(pages[0].id || '');
        cfg.pageHint.textContent = 'Pages: ' + pages.length + '. Первая выбрана автоматически.';
      }

      cfg.page.disabled = false;
      cfg.loaded = true;
      refreshReadyState();
    }).catch(function(error) {
      cfg.page.textContent = '';
      const failed = document.createElement('option');
      failed.value = '';
      failed.textContent = 'Ошибка загрузки Pages';
      cfg.page.appendChild(failed);
      cfg.page.disabled = true;
      cfg.loaded = true;
      cfg.error = String((error && error.message) || error);
      cfg.pageHint.className = 'error';
      cfg.pageHint.textContent =
        'Pages не загрузились: ' + cfg.error + '. Можно ввести Primary Page ID вручную.';
      refreshReadyState();
    });
  }

  create.addEventListener('click', function() {
    if (create.disabled) return;

    const configs = {};
    for (const profileId of profiles) {
      const cfg = rows[profileId];
      configs[profileId] = {
        name: String(cfg.name.value || '').trim(),
        page_id: String(cfg.page.value || cfg.manualPage.value || '').trim()
      };
    }

    create.disabled = true;
    cancel.disabled = true;
    status.textContent = 'Отправляю Job в локальный Python worker…';

    pythonWorkerStartBusiness('', {
      profiles: profiles,
      configs: configs
    }).then(function() {
      pythonWorkerCloseOwnBmModal();
    }).catch(function(error) {
      cancel.disabled = false;
      refreshReadyState();
      status.textContent = 'Ошибка: ' + String((error && error.message) || error);
    });
  });

  refreshReadyState();
}

async function pythonWorkerStartProvisioning() {
  return pythonWorkerOpenOwnBmModal();
}

async function pythonWorkerPoll() {
  if (!pythonWorkerUiState.jobId || pythonWorkerUiState.polling) return;

  pythonWorkerUiState.polling = true;

  try {
    const data = await pythonWorkerBridge({
      action: 'status',
      job_id: pythonWorkerUiState.jobId
    });

    const job = data && data.job;
    if (!job || typeof job !== 'object') {
      throw new Error('Worker bridge returned no job.');
    }

    pythonWorkerRenderJob(job);

    const items = Array.isArray(job.items) ? job.items : [];
    const runningSteps = [];

    for (const item of items) {
      const steps = Array.isArray(item && item.provisioning_steps)
        ? item.provisioning_steps
        : [];
      const running = steps.find(function(step) {
        return step && String(step.status || '').toUpperCase() === 'RUNNING';
      });
      if (running && running.step) runningSteps.push(String(running.step));
    }

    if (runningSteps.length) {
      pythonWorkerSetText(
        'pythonPwStatus',
        'Выполняется: ' + Array.from(new Set(runningSteps)).join(', ')
      );
    }

    const status = String(job.status || '').toUpperCase();
    const terminal = ['SUCCESS', 'FAILED', 'PARTIAL'].indexOf(status) !== -1;

    if (!terminal) {
      pythonWorkerSchedulePoll(1000);
      return;
    }

    if (pythonWorkerUiState.pollTimer) {
      clearTimeout(pythonWorkerUiState.pollTimer);
      pythonWorkerUiState.pollTimer = null;
    }

    pythonWorkerUiState.busy = false;

    if (status === 'SUCCESS') {
      pythonWorkerSetText('pythonPwStatus', 'Business Manager успешно создан.');
      await pythonWorkerRefreshSuccessfulProfiles(items);

      pythonWorkerUiState.jobId = '';
      localStorage.removeItem('remask_python_worker_job_v1');
    } else {
      const errors = items
        .filter(function(item) {
          return item && String(item.status || '').toUpperCase() === 'FAILED';
        })
        .map(function(item) {
          return [item.profile_id, item.error_code, item.error_message]
            .filter(Boolean)
            .join(': ');
        });

      pythonWorkerSetText(
        'pythonPwStatus',
        status === 'PARTIAL'
          ? 'Часть BM создана. Ошибки: ' + (errors.join(' · ') || 'неизвестная ошибка')
          : 'Создание BM завершилось ошибкой: ' + (errors.join(' · ') || 'неизвестная ошибка')
      );

      if (status === 'PARTIAL') {
        await pythonWorkerRefreshSuccessfulProfiles(items);
      }

      // FAILED/PARTIAL job_id сохраняем для Retry Failed.
      localStorage.setItem('remask_python_worker_job_v1', pythonWorkerUiState.jobId);
    }

    pythonWorkerSelectionRefresh();
  } catch (error) {
    pythonWorkerSetText(
      'pythonPwStatus',
      'Ошибка чтения Job: ' + ((error && error.message) || error)
    );
    pythonWorkerSchedulePoll(3000);
  } finally {
    pythonWorkerUiState.polling = false;
  }
}

window.pythonWorkerStartBusiness = pythonWorkerStartBusiness;

async function pythonWorkerRetryFailed() {
  if (!pythonWorkerUiState.jobId || pythonWorkerUiState.busy) return;

  pythonWorkerUiState.busy = true;
  pythonWorkerSelectionRefresh();
  pythonWorkerSetText('pythonPwStatus', 'Повторно ставлю FAILED JobItem в очередь...');

  try {
    const data = await pythonWorkerBridge({
      action: 'retry_failed',
      job_id: pythonWorkerUiState.jobId
    });

    const requeued = Number((data && data.result && data.result.requeued) || 0);

    if (requeued <= 0) {
      pythonWorkerUiState.busy = false;
      pythonWorkerSelectionRefresh();
      pythonWorkerSetText('pythonPwStatus', 'Нет FAILED элементов для повторного запуска.');
      return;
    }

    pythonWorkerSetText(
      'pythonPwStatus',
      'Retry Failed: возвращено в очередь ' + requeued + '.'
    );

    pythonWorkerPoll().catch(function(error) {
      pythonWorkerSetText(
        'pythonPwStatus',
        'Retry Failed polling error: ' + ((error && error.message) || error)
      );
    });
  } catch (error) {
    pythonWorkerUiState.busy = false;
    pythonWorkerSelectionRefresh();
    pythonWorkerSetText(
      'pythonPwStatus',
      'Retry Failed error: ' + ((error && error.message) || error)
    );
  }
}

function pythonWorkerSetBmDialogStatus(dialog, text, isError) {
  if (!dialog) return;
  let el = dialog.querySelector('[data-python-worker-bm-status]');
  if (!el) {
    el = document.createElement('div');
    el.setAttribute('data-python-worker-bm-status', '1');
    el.style.marginTop = '10px';
    el.style.fontSize = '12px';
    el.style.whiteSpace = 'pre-wrap';
    const footer = dialog.querySelector('.modal-footer');
    (footer ? footer.parentElement : dialog).appendChild(el);
  }
  el.style.color = isError ? '#ff7b7b' : '#83dd99';
  el.textContent = String(text || '');
}

function pythonWorkerEnhanceBmDialog() {
  const dialog = pythonWorkerFindBmDialog(null);
  if (!dialog || dialog.getAttribute('data-python-worker-bm-bound') === '1') return;

  const candidates = Array.from(
    dialog.querySelectorAll('button, input[type="button"], input[type="submit"], a, [role="button"]')
  );

  const oldButton = candidates.find(function(el) {
    const label = String(el.textContent || el.value || '').replace(/\s+/g, ' ').trim();
    return /Создать\s*BM|Create\s*BM|Создать\s*Business Manager|Create\s*Business Manager/i.test(label);
  });

  if (!oldButton || !oldButton.parentNode) return;

  const cleanButton = oldButton.cloneNode(true);
  cleanButton.type = 'button';
  cleanButton.removeAttribute('onclick');
  cleanButton.setAttribute('data-python-worker-create-bm', '1');

  oldButton.parentNode.replaceChild(cleanButton, oldButton);

  cleanButton.addEventListener('click', function(event) {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    if (pythonWorkerUiState.busy) {
      pythonWorkerSetBmDialogStatus(dialog, 'BM Job уже выполняется…', false);
      return;
    }

    const bmName = pythonWorkerResolveBmName(cleanButton);
    if (!bmName) {
      pythonWorkerSetBmDialogStatus(dialog, 'Укажи название Business Manager.', true);
      return;
    }

    const profiles = pythonWorkerSelectedProfiles();
    if (!profiles.length) {
      pythonWorkerSetBmDialogStatus(dialog, 'Не выбран FB-профиль для Add BM.', true);
      return;
    }

    const rowConfig = pythonWorkerBmRowConfig(dialog, profiles, bmName);
    const missing = profiles.filter(function(profileId) {
      const cfg = rowConfig[String(profileId)] || {};
      return !String(cfg.page_id || '').trim();
    });

    if (missing.length) {
      pythonWorkerSetBmDialogStatus(
        dialog,
        'Для выбранного профиля не определён Primary Page.',
        true
      );
      return;
    }

    cleanButton.disabled = true;
    pythonWorkerSetBmDialogStatus(dialog, 'Создаю Business Manager через Python worker…', false);

    pythonWorkerStartBusiness(bmName, {dialog: dialog}).catch(function(error) {
      cleanButton.disabled = false;
      pythonWorkerSetBmDialogStatus(
        dialog,
        String((error && error.message) || error),
        true
      );
    });
  }, true);

  dialog.setAttribute('data-python-worker-bm-bound', '1');
  pythonWorkerSetBmDialogStatus(dialog, 'Add BM подключён к Python worker.', false);
}

function pythonWorkerInitUi() {
  const start = pythonWorkerEl('pythonProvisionStart');
  const retry = pythonWorkerEl('pythonProvisionRetry');

  if (start) {
    start.addEventListener('click', function(event) {
      event.preventDefault();
      pythonWorkerStartProvisioning().catch(function(error) {
        pythonWorkerSetText(
          'pythonPwStatus',
          String((error && error.message) || error)
        );
      });
    });
  }

  if (retry) {
    retry.addEventListener('click', function(event) {
      event.preventDefault();
      pythonWorkerRetryFailed().catch(function(error) {
        pythonWorkerSetText(
          'pythonPwStatus',
          String((error && error.message) || error)
        );
      });
    });
  }

  document.addEventListener('click', function() {
    setTimeout(pythonWorkerSelectionRefresh, 0);
  }, true);

  document.addEventListener('change', function() {
    setTimeout(pythonWorkerSelectionRefresh, 0);
  }, true);

  pythonWorkerSelectionRefresh();
  pythonWorkerEnhanceBmDialog();
  pythonWorkerHealthCheck().catch(function(){});
  setInterval(function() {
    pythonWorkerHealthCheck().catch(function(){});
  }, 15000);

  new MutationObserver(function() {
    pythonWorkerEnhanceBmDialog();
  }).observe(document.documentElement, {childList: true, subtree: true});

  if (pythonWorkerUiState.jobId) {
    pythonWorkerSetText('pythonPwStatus', 'Восстанавливаю последний Job...');
    pythonWorkerPoll().catch(function(){});
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', pythonWorkerInitUi, {once: true});
} else {
  pythonWorkerInitUi();
}

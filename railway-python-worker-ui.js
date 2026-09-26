/* REMASK_PYTHON_WORKER_UI_V1 REMASK_PYTHON_WORKER_UI_V2 REMASK_PYTHON_WORKER_UI_V3 REMASK_PYTHON_WORKER_UI_V133 REMASK_PYTHON_WORKER_UI_V134 REMASK_PYTHON_WORKER_UI_V135 REMASK_PYTHON_WORKER_UI_V136 REMASK_PYTHON_WORKER_UI_V137 REMASK_PYTHON_WORKER_UI_V138 REMASK_PYTHON_WORKER_UI_V139 REMASK_PYTHON_WORKER_UI_V140 REMASK_PYTHON_WORKER_UI_V141 REMASK_PYTHON_WORKER_UI_V142 REMASK_PYTHON_WORKER_UI_V143 REMASK_PYTHON_WORKER_UI_V144 REMASK_PYTHON_WORKER_UI_V145 REMASK_PYTHON_WORKER_UI_V146 REMASK_PYTHON_WORKER_UI_V147 REMASK_PYTHON_WORKER_UI_V148 REMASK_PYTHON_WORKER_UI_V149 REMASK_PYTHON_WORKER_UI_V150 REMASK_PYTHON_WORKER_UI_V151 REMASK_PYTHON_WORKER_UI_V152 REMASK_PYTHON_WORKER_UI_V153 REMASK_PYTHON_WORKER_UI_V154 REMASK_PYTHON_WORKER_UI_V155 REMASK_PYTHON_WORKER_UI_V156 REMASK_PYTHON_WORKER_UI_V157 REMASK_PYTHON_WORKER_UI_V158 REMASK_PYTHON_WORKER_UI_V159 REMASK_PYTHON_WORKER_UI_V160 REMASK_PYTHON_WORKER_UI_V161 */
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

async function pythonWorkerMapLimit(items, limit, worker) {
  const source = Array.isArray(items) ? items.slice() : [];
  const concurrency = Math.max(1, Math.min(Number(limit) || 1, source.length || 1));
  let index = 0;

  async function run() {
    while (true) {
      const current = index++;
      if (current >= source.length) return;
      await worker(source[current], current);
    }
  }

  const runners = [];
  for (let i = 0; i < concurrency; i++) runners.push(run());
  await Promise.all(runners);
}

function pythonWorkerSelectionRefresh() {
  const profiles = pythonWorkerSelectedProfiles();
  const start = pythonWorkerEl('pythonProvisionStart');
  const addRk = pythonWorkerEl('pythonProvisionAdAccount');

  if (start) {
    start.disabled =
      pythonWorkerUiState.busy ||
      profiles.length === 0 ||
      pythonWorkerUiState.workerOnline !== true;
    start.textContent = profiles.length
      ? 'Add BM (' + profiles.length + ')'
      : 'Add BM';
  }

  if (addRk) {
    addRk.disabled =
      pythonWorkerUiState.busy ||
      profiles.length === 0 ||
      pythonWorkerUiState.workerOnline !== true;
    addRk.textContent = profiles.length
      ? 'Add RK (' + profiles.length + ')'
      : 'Add RK';
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
    const hasRetryableFailed = items.some(function(item) {
      if (!item || String(item.status || '').toUpperCase() !== 'FAILED') return false;
      if (item.retryable === true) return true;
      const tasks = Array.isArray(item.tasks) ? item.tasks : [];
      return tasks.some(function(task) {
        return task &&
          String(task.status || '').toUpperCase() === 'FAILED' &&
          task.retryable === true;
      });
    });
    retry.disabled =
      pythonWorkerUiState.busy ||
      !pythonWorkerUiState.jobId ||
      !hasRetryableFailed;
  }

  if (!pythonWorkerUiState.jobId && !pythonWorkerUiState.busy) {
    pythonWorkerSetText(
      'pythonPwStatus',
      profiles.length
        ? (
            pythonWorkerUiState.workerOnline === true
              ? 'Worker UI v161 · Выбрано FB-профилей: ' + profiles.length + '. Готово к Add BM.'
              : 'Worker UI v161 · Выбрано FB-профилей: ' + profiles.length + '. Жду READY от worker.'
          )
        : 'Worker UI v161 · Выберите FB-профили в Workspace.'
    );
  }
}

function pythonWorkerErrorText(value, fallback) {
  if (value == null || value === '') return String(fallback || 'Unknown error');
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }

  if (value && typeof value === 'object') {
    if (typeof value.message === 'string' && value.message.trim()) {
      return value.message.trim();
    }
    if (typeof value.detail === 'string' && value.detail.trim()) {
      return value.detail.trim();
    }
    if (value.error != null && value.error !== value) {
      return pythonWorkerErrorText(value.error, fallback);
    }
    try {
      return JSON.stringify(value);
    } catch (_) {}
  }

  return String(fallback || value);
}

let pythonWorkerCsrfPromise = null;

async function pythonWorkerCsrf(forceRefresh) {
  if (forceRefresh) pythonWorkerCsrfPromise = null;
  if (pythonWorkerCsrfPromise) return pythonWorkerCsrfPromise;

  pythonWorkerCsrfPromise = (async function() {
    const response = await fetch('ajax/pythonWorkerJobs.php?action=csrf', {
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {'Accept': 'application/json'}
    });

    let data = null;
    try {
      data = await response.json();
    } catch (_) {
      throw new Error('Worker CSRF endpoint returned invalid JSON (HTTP ' + response.status + ').');
    }

    const csrf = String((data && data.csrf) || '').trim();
    if (!response.ok || !(data && data.ok) || !csrf) {
      throw new Error(
        pythonWorkerErrorText(
          data && (data.message != null ? data.message : data.error),
          'Worker CSRF token request failed (HTTP ' + response.status + ')'
        )
      );
    }
    return csrf;
  })().catch(function(error) {
    pythonWorkerCsrfPromise = null;
    throw error;
  });

  return pythonWorkerCsrfPromise;
}

async function pythonWorkerBridge(payload, csrfRetried) {
  const csrf = await pythonWorkerCsrf(false);
  const requestPayload = Object.assign({}, payload || {}, {remask_csrf: csrf});

  const response = await fetch('ajax/pythonWorkerJobs.php', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      'X-REMASK-CSRF': csrf
    },
    body: JSON.stringify(requestPayload)
  });

  let data = null;
  try {
    data = await response.json();
  } catch (_) {
    throw new Error('Worker bridge returned invalid JSON (HTTP ' + response.status + ').');
  }

  const errorText = pythonWorkerErrorText(
    data && (data.message != null ? data.message : data.error),
    'Worker bridge HTTP ' + response.status
  );

  if (
    response.status === 403 &&
    csrfRetried !== true &&
    /csrf/i.test(String(errorText || ''))
  ) {
    await pythonWorkerCsrf(true);
    return pythonWorkerBridge(payload, true);
  }

  if (!response.ok || !(data && data.ok)) {
    throw new Error(errorText);
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
    const readiness = data && data.readiness ? data.readiness : null;
    const isReady = data && data.ready === true;

    const queued = Number(
      (readiness && readiness.queued_items) != null
        ? readiness.queued_items
        : (worker.queued_items || 0)
    );
    const concurrency = Number(
      (readiness && readiness.worker_concurrency) != null
        ? readiness.worker_concurrency
        : (worker.worker_concurrency || 0)
    );
    const profilesVisible = Number((readiness && readiness.profiles_visible) || 0);
    const revision = String((readiness && readiness.revision) || '').trim();
    const bmPayloadVersion = String(
      (readiness && readiness.create_bm_payload_version) || ''
    ).trim();
    const volumeMounted = readiness ? readiness.volume_mounted === true : false;

    pythonWorkerUiState.workerOnline = true;

    if (isReady) {
      el.dataset.state = 'online';
      el.textContent =
        'Worker: READY' +
        (revision ? ' · rev ' + revision : '') +
        (bmPayloadVersion ? ' · BM ' + bmPayloadVersion : '') +
        ' · профили ' + profilesVisible +
        ' · volume ' + (volumeMounted ? 'YES' : 'NO') +
        ' · очередь ' + queued +
        ' · concurrency ' + concurrency;
    } else {
      el.dataset.state = 'offline';
      el.textContent =
        'Worker: ONLINE · resolver ERROR · ' +
        pythonWorkerErrorText(
          data && data.readiness_error,
          'profile resolver is not ready'
        );
    }

    pythonWorkerSelectionRefresh();
    return isReady;
  } catch (error) {
    pythonWorkerUiState.workerOnline = false;
    el.dataset.state = 'offline';
    el.textContent =
      'Worker: OFFLINE · ' +
      pythonWorkerErrorText(
        error && error.message != null ? error.message : error,
        'health request failed'
      );
    pythonWorkerSelectionRefresh();
    return false;
  }
}

async function pythonWorkerProfilePreflight(profileId) {
  const data = await pythonWorkerBridge({
    action: 'preflight',
    profile_id: String(profileId || '').trim()
  });

  const preflight = data && data.preflight ? data.preflight : null;
  if (!preflight || preflight.ok !== true) {
    throw new Error('Profile preflight returned no READY result.');
  }

  const browser = preflight.browser_business && typeof preflight.browser_business === 'object'
    ? preflight.browser_business
    : {};
  const routes = preflight.bm_routes && typeof preflight.bm_routes === 'object'
    ? preflight.bm_routes
    : {};

  preflight.create_route_ready =
    routes.browser_ui === true &&
    browser.ready === true &&
    browser.create_surface_ready === true;

  return preflight;
}

function pythonWorkerCurrentStep(item) {
  const steps = Array.isArray(item && item.provisioning_steps)
    ? item.provisioning_steps
    : [];

  const runningStep = steps.find(function(step) {
    return step && String(step.status || '').toUpperCase() === 'RUNNING';
  });
  if (runningStep) {
    const result = runningStep.result && typeof runningStep.result === 'object'
      ? runningStep.result
      : {};
    const detail = String(
      result.activity || result.phase || ''
    ).trim();
    return String(runningStep.step || 'RUNNING') + (detail ? ' · ' + detail : '');
  }

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

function pythonWorkerBusinessResult(item) {
  const steps = Array.isArray(item && item.provisioning_steps)
    ? item.provisioning_steps
    : [];

  const business = steps.find(function(step) {
    return (
      step &&
      String(step.step || '').toUpperCase() === 'BUSINESS' &&
      String(step.status || '').toUpperCase() === 'SUCCESS'
    );
  });

  if (!business || !business.result || typeof business.result !== 'object') {
    return null;
  }

  const result = business.result;
  const businessId = String(result.business_id || '').trim();
  if (!businessId) return null;

  return {
    business_id: businessId,
    transport: String(result.transport || '').trim(),
    primary_page_id: String(result.primary_page_id || '').trim()
  };
}

function pythonWorkerAdAccountResult(item) {
  const steps = Array.isArray(item && item.provisioning_steps)
    ? item.provisioning_steps
    : [];
  const step = steps.find(function(row) {
    return (
      row &&
      String(row.step || '').toUpperCase() === 'AD_ACCOUNT' &&
      String(row.status || '').toUpperCase() === 'SUCCESS'
    );
  });

  if (!step || !step.result || typeof step.result !== 'object') return null;
  const result = step.result;
  const adAccountId = String(result.ad_account_id || '').trim();
  if (!adAccountId) return null;

  return {
    ad_account_id: adAccountId,
    business_id: String(result.business_id || '').trim(),
    currency: String(result.currency || '').trim(),
    timezone_id: String(result.timezone_id == null ? '' : result.timezone_id).trim(),
    transport: String(result.transport || '').trim(),
    response_path: String(result.create_response_path || '').trim()
  };
}

function pythonWorkerBusinessTelemetry(item) {
  const steps = Array.isArray(item && item.provisioning_steps)
    ? item.provisioning_steps
    : [];
  const business = steps.find(function(step) {
    return step && String(step.step || '').toUpperCase() === 'BUSINESS';
  });
  if (!business || !business.result || typeof business.result !== 'object') {
    return [];
  }

  const result = business.result;
  const parts = [];
  const phase = String(result.activity || result.phase || '').trim();
  const responseId = String(
    result.business_id || result.create_response_business_id || ''
  ).trim();
  const friendly = String(
    result.create_response_friendly_name ||
    result.response_friendly_name ||
    ''
  ).trim();
  const responsePath = String(
    result.create_response_path || result.response_path || ''
  ).trim();

  if (phase) parts.push('phase ' + phase);
  if (responseId) parts.push('BM ' + responseId);
  if (friendly) parts.push('Meta ' + friendly);
  if (responsePath) parts.push('response ' + responsePath);
  if (result.recovered_cross_job === true) parts.push('cross-job resume');

  const metaErrors = Array.isArray(result.meta_errors)
    ? result.meta_errors
    : [];
  if (metaErrors.length && metaErrors[0] && typeof metaErrors[0] === 'object') {
    const first = metaErrors[0];
    const code = [
      String(first.code || '').trim(),
      String(first.subcode || '').trim()
    ].filter(Boolean).join('/');
    const message = String(first.message || '').trim();
    const detail = [code, message].filter(Boolean).join(': ');
    if (detail) parts.push('Meta error ' + detail.slice(0, 260));
  }

  return parts;
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

        const businessTelemetry = pythonWorkerBusinessTelemetry(item);

        if (errorParts.length) {
          if (businessTelemetry.length) {
            errorParts.push(businessTelemetry.join(' · '));
          }
          errorTd.textContent = errorParts.join(' · ');
        } else {
          const adAccountResult = pythonWorkerAdAccountResult(item);
          const businessResult = pythonWorkerBusinessResult(item);
          if (adAccountResult) {
            const resultParts = [
              'RK ' + adAccountResult.ad_account_id
            ];
            if (adAccountResult.business_id) {
              resultParts.push('BM ' + adAccountResult.business_id);
            }
            if (adAccountResult.currency) {
              resultParts.push(adAccountResult.currency);
            }
            if (adAccountResult.timezone_id) {
              resultParts.push('TZ ' + adAccountResult.timezone_id);
            }
            if (adAccountResult.transport) {
              resultParts.push(adAccountResult.transport);
            }
            if (adAccountResult.response_path) {
              resultParts.push('response ' + adAccountResult.response_path);
            }
            errorTd.className = 'pw-result';
            errorTd.textContent = resultParts.join(' · ');
          } else if (businessResult) {
            const resultParts = [
              'BM ' + businessResult.business_id
            ];
            if (businessResult.primary_page_id) {
              resultParts.push('FP ' + businessResult.primary_page_id);
            }
            if (businessResult.transport) {
              resultParts.push(businessResult.transport);
            }
            for (const detail of businessTelemetry) {
              if (resultParts.indexOf(detail) === -1) {
                resultParts.push(detail);
              }
            }
            errorTd.className = 'pw-result';
            errorTd.textContent = resultParts.join(' · ');
          } else if (businessTelemetry.length) {
            errorTd.className = 'pw-result';
            errorTd.textContent = businessTelemetry.join(' · ');
          } else {
            errorTd.textContent = '—';
          }
        }

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
    let userEmail = '';

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

      const emailInput = rowInputs.find(function(input) {
        const type = String(input.type || '').toLowerCase();
        const key = [
          input.name || '',
          input.id || '',
          input.className || '',
          input.placeholder || ''
        ].join(' ');
        return type === 'email' || /email|почт/i.test(key);
      });
      if (emailInput) {
        userEmail = String(emailInput.value || '').trim();
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
      page_id: pageId,
      user_email: userEmail
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

  const unconfirmed = [];

  for (const profileId of profiles) {
    const ok = await pythonWorkerRefreshProfile(profileId);
    if (!ok) unconfirmed.push(profileId);
  }

  return unconfirmed;
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

      const businessEmail = String(
        config.user_email || config.email || ''
      ).trim();
      if (businessEmail) businessParams.user_email = businessEmail;

      return {
        profile_id: profileKey,
        tasks: [
          {
            action: 'provisioning',
            idempotency_key: 'add-bm-' + nonce + '-' + index,
            payload: {
              steps: ['PROXY_CHECK', 'BUSINESS'],
              // Stable per profile+Page because profile_id is a separate
              // provisioning-state key. If CREATE succeeded but Page attach
              // failed, a later Add BM Job reuses the confirmed BM instead of
              // creating a duplicate Business Portfolio.
              scope_key: 'add-bm-page-' + pageId,
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

async function pythonWorkerLoadPages(profileId, csrfRetried) {
  const csrf = await pythonWorkerCsrf(false);
  const payload = {
    profile: String(profileId || '').trim(),
    remask_csrf: csrf
  };

  const response = await fetch('ajax/pythonWorkerPages.php', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      'X-REMASK-CSRF': csrf
    },
    body: JSON.stringify(payload)
  });

  let data = null;
  try {
    data = await response.json();
  } catch (_) {
    throw new Error('Pages endpoint returned invalid JSON (HTTP ' + response.status + ').');
  }

  const detail = data && data.detail && typeof data.detail === 'object'
    ? (data.detail.message || JSON.stringify(data.detail))
    : '';
  const errorText = String(
    detail || (data && (data.message || data.error)) || ('Pages HTTP ' + response.status)
  );

  if (
    response.status === 403 &&
    csrfRetried !== true &&
    /csrf/i.test(errorText)
  ) {
    await pythonWorkerCsrf(true);
    return pythonWorkerLoadPages(profileId, true);
  }

  if (!response.ok || !(data && data.ok)) {
    throw new Error(errorText);
  }

  return Array.isArray(data.pages) ? data.pages : [];
}

function pythonWorkerApplyPages(cfg, pages, sourceLabel) {
  const list = (Array.isArray(pages) ? pages : [])
    .filter(function(item) {
      return item && String(item.id || '').trim();
    })
    .slice()
    .sort(function(a, b) {
      const aBusiness = String((a && a.business_id) || '').trim();
      const bBusiness = String((b && b.business_id) || '').trim();
      if (!!aBusiness !== !!bBusiness) return aBusiness ? 1 : -1;
      return String((a && a.name) || '').localeCompare(
        String((b && b.name) || ''),
        undefined,
        {sensitivity: 'base'}
      );
    });

  cfg.page.textContent = '';

  if (!list.length) {
    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = 'Meta вернула 0 Pages';
    cfg.page.appendChild(empty);
    cfg.error = 'Meta вернула 0 Pages';
    cfg.pageHint.className = 'error';
    cfg.pageHint.textContent =
      'Meta вернула 0 Pages. Введи Primary Page ID вручную.';
    cfg.page.disabled = false;
    cfg.loaded = true;
    return;
  }

  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = 'Выбери Primary Page';
  cfg.page.appendChild(placeholder);

  let pagesAlreadyInBusiness = 0;
  let restrictedPages = 0;

  for (const item of list) {
    const option = document.createElement('option');
    const pageId = String(item.id || '').trim();
    const businessId = String(item.business_id || '').trim();
    const restriction =
      item.advertising_restriction_info &&
      typeof item.advertising_restriction_info === 'object'
        ? item.advertising_restriction_info
        : {};
    const restricted = restriction.is_restricted === true;

    if (businessId) pagesAlreadyInBusiness += 1;
    if (restricted) restrictedPages += 1;

    option.value = pageId;

    let suffix = '';
    if (businessId) suffix += ' · уже в BM ' + businessId;
    if (restricted) suffix += ' · restricted';

    option.textContent =
      String(item.name || pageId || 'Page') +
      ' — ' +
      pageId +
      suffix;

    if (businessId) {
      option.disabled = true;
      option.dataset.ineligibleReason = 'already_owned_by_business';
    }

    cfg.page.appendChild(option);
  }

  const preferred = list.find(function(item) {
    return !String((item && item.business_id) || '').trim();
  }) || null;

  cfg.page.value = String((preferred && preferred.id) || '');

  const currentName = String((cfg.name && cfg.name.value) || '').trim();
  const isGeneratedName =
    /^ReMask(?:_BM_| Business )\d+$/i.test(currentName);
  if (
    cfg.name &&
    isGeneratedName &&
    preferred &&
    String(preferred.name || '').trim()
  ) {
    cfg.name.value = String(preferred.name || '').trim().slice(0, 255);
  }

  cfg.page.disabled = false;
  cfg.loaded = true;
  cfg.error = '';
  cfg.pageHint.className = preferred ? '' : 'error';

  const warnings = [];
  if (pagesAlreadyInBusiness) {
    warnings.push('уже показывают владельца BM: ' + pagesAlreadyInBusiness);
  }
  if (restrictedPages) {
    warnings.push('restricted: ' + restrictedPages);
  }

  cfg.pageHint.textContent =
    'Pages: ' + list.length +
    ' · источник: ' + String(sourceLabel || 'Meta') +
    (warnings.length ? ' · ' + warnings.join(' · ') : '') +
    (
      preferred
        ? ' · выбрана Page без известного владельца BM.'
        : ' · свободная Page не определена автоматически; выбери вручную.'
    );
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
    '#pythonWorkerBmModal .pwbm-row{display:grid;grid-template-columns:minmax(130px,.7fr) minmax(220px,1.1fr) minmax(260px,1.4fr);gap:10px;align-items:start;padding:12px 0;border-bottom:1px solid #303640}',
    '#pythonWorkerBmModal .pwbm-row:last-child{border-bottom:0}',
    '#pythonWorkerBmModal .pwbm-profile{font-size:12px;font-weight:600;padding-top:9px;word-break:break-word}',
    '#pythonWorkerBmModal .pwbm-session{display:block;margin-top:6px;font-size:11px;font-weight:400;color:#8f99a8}',
    '#pythonWorkerBmModal .pwbm-session.ok{color:#82d99c}',
    '#pythonWorkerBmModal .pwbm-session.error{color:#ff8f96}',
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
  if (pythonWorkerUiState.workerOnline !== true) {
    pythonWorkerSetText(
      'pythonPwStatus',
      'Add BM недоступен: worker ещё не READY. Смотри индикатор Worker.'
    );
    await pythonWorkerHealthCheck();
    if (pythonWorkerUiState.workerOnline !== true) return;
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

    const sessionHint = document.createElement('span');
    sessionHint.className = 'pwbm-session';
    sessionHint.textContent = 'Данные синхронизации · FB проверится при создании';
    profile.appendChild(sessionHint);

    const nameField = document.createElement('div');
    nameField.className = 'pwbm-field';
    const name = document.createElement('input');
    name.type = 'text';
    name.value = 'ReMask Business ' + (index + 1);
    name.placeholder = 'Название Business Manager';
    const nameHint = document.createElement('small');
    nameHint.textContent = 'Название BM';

    const businessEmail = document.createElement('input');
    businessEmail.type = 'email';
    businessEmail.className = 'pwbm-manual';
    businessEmail.placeholder = 'Business email (обязателен, если не сохранён в профиле)';

    const emailHint = document.createElement('small');
    emailHint.textContent = 'Нужен для формы создания Business в Meta Business Suite.';

    nameField.appendChild(name);
    nameField.appendChild(nameHint);
    nameField.appendChild(businessEmail);
    nameField.appendChild(emailHint);

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
      businessEmail: businessEmail,
      emailHint: emailHint,
      page: page,
      manualPage: manualPage,
      pageHint: pageHint,
      sessionHint: sessionHint,
      loaded: false,
      preflightReady: false,
      createRouteReady: false,
      preflightError: '',
      requiresBusinessEmail: false,
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

    const failed = profiles.filter(function(profileId) {
      const cfg = rows[profileId];
      if (!cfg) return true;

      const effectivePage = String(
        cfg.page.value || cfg.manualPage.value || ''
      ).trim();

      return (
        !String(cfg.name.value || '').trim() ||
        !effectivePage
      );
    });

    create.disabled =
      pythonWorkerUiState.busy ||
      !allLoaded ||
      failed.length > 0;

    if (!allLoaded) {
      status.textContent = 'Загружаю данные последней синхронизации…';
    } else {
      status.textContent = failed.length
        ? 'Не готовы профили: ' + failed.join(', ')
        : 'Данные синхронизации загружены. Готово к запуску Job: ' +
          profiles.length + '.';
    }
  };

  pythonWorkerMapLimit(profiles, 8, async function(profileId) {
    const cfg = rows[profileId];

    cfg.name.addEventListener('input', refreshReadyState);
    cfg.businessEmail.addEventListener('input', refreshReadyState);
    cfg.page.addEventListener('change', function() {
      if (String(cfg.page.value || '').trim()) cfg.manualPage.value = '';
      refreshReadyState();
    });
    cfg.manualPage.addEventListener('input', function() {
      if (String(cfg.manualPage.value || '').trim()) cfg.page.value = '';
      refreshReadyState();
    });

    try {
      const pages = await pythonWorkerLoadPages(profileId);
      pythonWorkerApplyPages(
        cfg,
        Array.isArray(pages) ? pages : [],
        'ReMask sync cache'
      );

      cfg.preflightReady = true;
      cfg.preflightError = '';
      cfg.createRouteReady = false;
      cfg.requiresBusinessEmail = false;
      cfg.sessionHint.className = 'pwbm-session ok';
      cfg.sessionHint.textContent =
        'Синхронизация: OK · FB session / proxy проверятся внутри Job';

      refreshReadyState();
    } catch (error) {
      cfg.preflightReady = true;
      cfg.preflightError = '';
      cfg.createRouteReady = false;
      cfg.requiresBusinessEmail = false;
      cfg.error = String((error && error.message) || error);

      cfg.page.textContent = '';
      const failed = document.createElement('option');
      failed.value = '';
      failed.textContent = 'Кэш Pages недоступен';
      cfg.page.appendChild(failed);
      cfg.page.disabled = false;
      cfg.loaded = true;

      cfg.sessionHint.className = 'pwbm-session error';
      cfg.sessionHint.textContent =
        'Кэш синхронизации недоступен · можно ввести Primary Page ID вручную';
      cfg.pageHint.className = 'error';
      cfg.pageHint.textContent =
        'Не удалось прочитать Pages из синхронизации: ' + cfg.error +
        '. Введи Primary Page ID вручную.';

      refreshReadyState();
    }
  }).catch(function(error) {
    console.error('[ReMask Worker UI] cache pool failed:', error);
  });

  create.addEventListener('click', function() {
    if (create.disabled) return;

    const configs = {};
    for (const profileId of profiles) {
      const cfg = rows[profileId];
      configs[profileId] = {
        name: String(cfg.name.value || '').trim(),
        page_id: String(cfg.page.value || cfg.manualPage.value || '').trim(),
        user_email: String(cfg.businessEmail.value || '').trim()
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
      if (running && running.step) {
        const result = running.result && typeof running.result === 'object'
          ? running.result
          : {};
        const detail = String(result.activity || result.phase || '').trim();
        runningSteps.push(
          String(running.step) + (detail ? ' · ' + detail : '')
        );
      }
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
      const isFanPageJob = items.some(function(item) {
        return (Array.isArray(item && item.provisioning_steps) ? item.provisioning_steps : [])
          .some(function(step) { return step && String(step.step || '').toUpperCase() === 'FAN_PAGES'; });
      });
      const isAdAccountJob = items.some(function(item) {
        return (Array.isArray(item && item.provisioning_steps) ? item.provisioning_steps : [])
          .some(function(step) {
            return step && String(step.step || '').toUpperCase() === 'AD_ACCOUNT';
          });
      });
      const unconfirmed = await pythonWorkerRefreshSuccessfulProfiles(items);
      pythonWorkerSetText(
        'pythonPwStatus',
        isFanPageJob
          ? (
              unconfirmed.length
                ? 'FP созданы. Page ID сохранены в Job. Workspace sync не подтвердил: ' + unconfirmed.join(', ') + '.'
                : 'Fan Pages созданы и Workspace sync завершён.'
            )
          : isAdAccountJob
          ? (
              unconfirmed.length
                ? (
                    'RK создан. ad_account_id сохранён в Job. Workspace sync не ' +
                    'подтвердил профили: ' + unconfirmed.join(', ') + '.'
                  )
                : 'Рекламный кабинет создан и Workspace sync завершён.'
            )
          : (
              unconfirmed.length
                ? (
                    'BM создан. ID сохранён в Job. Обычный Meta inventory sync не ' +
                    'подтвердил профили: ' + unconfirmed.join(', ') +
                    '. Это не отменяет успешный CREATE.'
                  )
                : 'Business Manager создан и подтверждён Workspace sync.'
            )
      );

      pythonWorkerUiState.jobId = '';
      localStorage.removeItem('remask_python_worker_job_v1');
    } else {
      const isFanPageJob = items.some(function(item) {
        return (Array.isArray(item && item.provisioning_steps) ? item.provisioning_steps : [])
          .some(function(step) { return step && String(step.step || '').toUpperCase() === 'FAN_PAGES'; });
      });
      const isAdAccountJob = items.some(function(item) {
        if (item && String(item.step || '').toUpperCase() === 'AD_ACCOUNT') {
          return true;
        }
        return (Array.isArray(item && item.provisioning_steps) ? item.provisioning_steps : [])
          .some(function(step) {
            return step && String(step.step || '').toUpperCase() === 'AD_ACCOUNT';
          });
      });
      const entityLabel = isFanPageJob ? 'FP' : (isAdAccountJob ? 'RK' : 'BM');
      const entityFailureLabel = isFanPageJob
        ? 'Создание Fan Page завершилось ошибкой: '
        : isAdAccountJob
        ? 'Создание рекламного кабинета завершилось ошибкой: '
        : 'Создание BM завершилось ошибкой: ';
      const errors = items
        .filter(function(item) {
          return item && String(item.status || '').toUpperCase() === 'FAILED';
        })
        .map(function(item) {
          const retryLabel = item.retryable === true
            ? 'RETRYABLE'
            : 'NO AUTO-RETRY';
          return [item.profile_id, item.error_code, retryLabel, item.error_message]
            .filter(Boolean)
            .join(': ');
        });

      pythonWorkerSetText(
        'pythonPwStatus',
        status === 'PARTIAL'
          ? 'Часть ' + entityLabel + ' создана. Ошибки: ' + (errors.join(' · ') || 'неизвестная ошибка')
          : entityFailureLabel + (errors.join(' · ') || 'неизвестная ошибка')
      );

      if (status === 'PARTIAL') {
        const unconfirmed = await pythonWorkerRefreshSuccessfulProfiles(items);
        if (unconfirmed.length) {
          pythonWorkerSetText(
            'pythonPwStatus',
            isAdAccountJob
              ? (
                  'Часть RK создана. Успешные ad_account_id сохранены в Job; ' +
                  'Workspace sync не подтвердил: ' + unconfirmed.join(', ') +
                  '. Ошибки остальных: ' + (errors.join(' · ') || 'неизвестная ошибка')
                )
              : (
                  'Часть BM создана. Успешные BM ID сохранены в Job; Meta inventory ' +
                  'sync не подтвердил: ' + unconfirmed.join(', ') +
                  '. Ошибки остальных: ' + (errors.join(' · ') || 'неизвестная ошибка')
                )
          );
        }
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


async function pythonWorkerProfileProvisioningState(profileId) {
  const data = await pythonWorkerBridge({
    action: 'profile_state',
    profile_id: String(profileId || '').trim()
  });
  return data && data.state && typeof data.state === 'object'
    ? data.state
    : {};
}

async function pythonWorkerLoadBusinesses(profileId, csrfRetried) {
  const csrf = await pythonWorkerCsrf(false);
  const response = await fetch('ajax/pythonWorkerBusinesses.php', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      'X-REMASK-CSRF': csrf
    },
    body: JSON.stringify({
      profile: String(profileId || '').trim(),
      remask_csrf: csrf
    })
  });

  let data = null;
  try {
    data = await response.json();
  } catch (_) {
    throw new Error('Businesses endpoint returned invalid JSON (HTTP ' + response.status + ').');
  }

  const detail = data && data.detail && typeof data.detail === 'object'
    ? (data.detail.message || JSON.stringify(data.detail))
    : '';
  const errorText = String(
    detail || (data && (data.message || data.error)) || ('Businesses HTTP ' + response.status)
  );

  if (
    response.status === 403 &&
    csrfRetried !== true &&
    /csrf/i.test(errorText)
  ) {
    await pythonWorkerCsrf(true);
    return pythonWorkerLoadBusinesses(profileId, true);
  }

  if (!response.ok || !(data && data.ok)) {
    throw new Error(errorText);
  }

  const businesses = Array.isArray(data.businesses) ? data.businesses.slice() : [];

  try {
    const persisted = await pythonWorkerProfileProvisioningState(profileId);
    const persistedBusinessId = String((persisted && persisted.business_id) || '').trim();
    if (
      /^\d+$/.test(persistedBusinessId) &&
      !businesses.some(function(item) {
        return String((item && item.id) || '').trim() === persistedBusinessId;
      })
    ) {
      businesses.unshift({
        id: persistedBusinessId,
        name: 'ReMask BM ' + persistedBusinessId,
        source: 'provisioning_state',
        ad_account_id: String((persisted && persisted.ad_account_id) || '').trim()
      });
    }
  } catch (stateError) {
    console.warn('[ReMask Worker UI] provisioning state fallback failed:', stateError);
  }

  return businesses;
}

async function pythonWorkerStartAdAccounts(options) {
  const profiles = options && Array.isArray(options.profiles)
    ? options.profiles.map(function(value) { return String(value || '').trim(); }).filter(Boolean)
    : pythonWorkerSelectedProfiles();
  const configs = options && options.configs && typeof options.configs === 'object'
    ? options.configs
    : {};

  if (!profiles.length || pythonWorkerUiState.busy) return;

  const invalid = profiles.filter(function(profileId) {
    const cfg = configs[String(profileId)] || {};
    return (
      !/^\d+$/.test(String(cfg.business_id || '').trim()) ||
      !String(cfg.name || '').trim() ||
      !String(cfg.currency || '').trim() ||
      !/^\d+$/.test(String(cfg.timezone_id == null ? '' : cfg.timezone_id).trim())
    );
  });

  if (invalid.length) {
    throw new Error(
      'Add RK request not sent: нужен BM ID, имя РК, currency и timezone_id. Проблема: ' +
      invalid.join(', ')
    );
  }

  pythonWorkerUiState.busy = true;
  pythonWorkerSelectionRefresh();
  pythonWorkerSetText(
    'pythonPwStatus',
    'Создаю Add RK Job для ' + profiles.length + ' FB-профилей...'
  );

  try {
    const nonce = Date.now() + '-' + Math.random().toString(16).slice(2);
    const payloadProfiles = profiles.map(function(profileId, index) {
      const profileKey = String(profileId);
      const cfg = configs[profileKey] || {};
      const businessId = String(cfg.business_id || '').trim();

      return {
        profile_id: profileKey,
        tasks: [
          {
            action: 'provisioning',
            idempotency_key: 'add-rk-' + nonce + '-' + index,
            payload: {
              steps: ['PROXY_CHECK', 'AD_ACCOUNT'],
              // One stable RK slot per BM. Once ad_account_id is confirmed,
              // ProvisioningService reuses it instead of creating a duplicate.
              scope_key: 'add-rk-bm-' + businessId,
              parameters: {
                AD_ACCOUNT: {
                  business_id: businessId,
                  name: String(cfg.name || '').trim(),
                  currency: String(cfg.currency || '').trim().toUpperCase(),
                  timezone_id: Number(cfg.timezone_id)
                }
              }
            }
          }
        ]
      };
    });

    const data = await pythonWorkerBridge({
      action: 'create',
      idempotency_key: 'workspace-add-rk-' + nonce,
      profiles: payloadProfiles
    });

    const jobId = String((data && data.job && data.job.job_id) || '').trim();
    if (!jobId) throw new Error('Worker did not return job_id.');

    pythonWorkerUiState.jobId = jobId;
    localStorage.setItem('remask_python_worker_job_v1', jobId);
    pythonWorkerSetText('pythonPwJob', 'Job: ' + jobId);
    pythonWorkerSetText('pythonPwStatus', 'Создание рекламного кабинета запущено...');

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
      'Add RK: ' + ((error && error.message) || error)
    );
    throw error;
  }
}

async function pythonWorkerOpenOwnAdAccountModal() {
  if (pythonWorkerUiState.workerOnline !== true) {
    pythonWorkerSetText('pythonPwStatus', 'Add RK недоступен: worker ещё не READY.');
    await pythonWorkerHealthCheck();
    if (pythonWorkerUiState.workerOnline !== true) return;
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
  title.textContent = 'Добавить рекламный кабинет · ' + profiles.length + ' проф.';
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'pwbm-close';
  close.textContent = '×';
  close.addEventListener('click', pythonWorkerCloseOwnBmModal);
  head.appendChild(title);
  head.appendChild(close);

  const body = document.createElement('div');
  body.className = 'pwbm-body';
  const note = document.createElement('div');
  note.className = 'pwbm-note';
  note.textContent =
    'Модель: 1 BM = 1 RK. Выбери BM; повторный Add RK для того же BM переиспользует уже подтверждённый ad_account_id.';
  body.appendChild(note);

  const rows = {};
  for (let index = 0; index < profiles.length; index++) {
    const profileId = profiles[index];
    const row = document.createElement('div');
    row.className = 'pwbm-row';

    const profile = document.createElement('div');
    profile.className = 'pwbm-profile';
    profile.textContent = profileId;
    const sessionHint = document.createElement('span');
    sessionHint.className = 'pwbm-session';
    sessionHint.textContent = 'Загружаю Business Managers…';
    profile.appendChild(sessionHint);

    const accountField = document.createElement('div');
    accountField.className = 'pwbm-field';
    const name = document.createElement('input');
    name.type = 'text';
    name.value = 'ReMask RK ' + (index + 1);
    name.placeholder = 'Название рекламного кабинета';

    const currency = document.createElement('input');
    currency.type = 'text';
    currency.className = 'pwbm-manual';
    currency.value = 'USD';
    currency.placeholder = 'Currency, например USD';

    const timezone = document.createElement('input');
    timezone.type = 'number';
    timezone.className = 'pwbm-manual';
    timezone.value = '1';
    timezone.min = '0';
    timezone.placeholder = 'Meta timezone_id';

    const accountHint = document.createElement('small');
    accountHint.textContent = 'Имя · валюта · Meta timezone_id. Currency/timezone после CREATE обычно не меняются.';
    accountField.appendChild(name);
    accountField.appendChild(currency);
    accountField.appendChild(timezone);
    accountField.appendChild(accountHint);

    const bmField = document.createElement('div');
    bmField.className = 'pwbm-field';
    const bm = document.createElement('select');
    bm.disabled = true;
    const loading = document.createElement('option');
    loading.value = '';
    loading.textContent = 'Загружаю BM…';
    bm.appendChild(loading);

    const manualBm = document.createElement('input');
    manualBm.type = 'text';
    manualBm.className = 'pwbm-manual';
    manualBm.inputMode = 'numeric';
    manualBm.placeholder = 'Или введи BM ID вручную';

    const bmHint = document.createElement('small');
    bmHint.textContent = 'Business Manager';
    bmField.appendChild(bm);
    bmField.appendChild(manualBm);
    bmField.appendChild(bmHint);

    row.appendChild(profile);
    row.appendChild(accountField);
    row.appendChild(bmField);
    body.appendChild(row);

    rows[profileId] = {
      name: name,
      currency: currency,
      timezone: timezone,
      bm: bm,
      manualBm: manualBm,
      bmHint: bmHint,
      sessionHint: sessionHint,
      loaded: false
    };
  }

  const footer = document.createElement('div');
  footer.className = 'pwbm-footer';
  const status = document.createElement('div');
  status.className = 'pwbm-status';
  status.textContent = 'Загружаю BM…';
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
  create.textContent = 'Создать RK';
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
    const failed = profiles.filter(function(profileId) {
      const cfg = rows[profileId];
      const businessId = String(cfg.bm.value || cfg.manualBm.value || '').trim();
      return (
        !/^\d+$/.test(businessId) ||
        !String(cfg.name.value || '').trim() ||
        !String(cfg.currency.value || '').trim() ||
        !/^\d+$/.test(String(cfg.timezone.value || '').trim())
      );
    });
    create.disabled =
      pythonWorkerUiState.busy ||
      !allLoaded ||
      failed.length > 0;
    status.textContent = !allLoaded
      ? 'Загружаю Business Managers…'
      : (
          failed.length
            ? 'Не готовы профили: ' + failed.join(', ')
            : 'Готово к Add RK: ' + profiles.length + '.'
        );
  };

  pythonWorkerMapLimit(profiles, 8, async function(profileId) {
    const cfg = rows[profileId];
    cfg.name.addEventListener('input', refreshReadyState);
    cfg.currency.addEventListener('input', refreshReadyState);
    cfg.timezone.addEventListener('input', refreshReadyState);
    cfg.bm.addEventListener('change', function() {
      if (String(cfg.bm.value || '').trim()) cfg.manualBm.value = '';
      refreshReadyState();
    });
    cfg.manualBm.addEventListener('input', function() {
      if (String(cfg.manualBm.value || '').trim()) cfg.bm.value = '';
      refreshReadyState();
    });

    try {
      const businesses = await pythonWorkerLoadBusinesses(profileId);
      cfg.bm.textContent = '';

      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = 'Выбери Business Manager';
      cfg.bm.appendChild(placeholder);

      for (const item of businesses) {
        if (!item || !/^\d+$/.test(String(item.id || '').trim())) continue;
        const option = document.createElement('option');
        option.value = String(item.id).trim();
        option.textContent =
          String(item.name || item.id) + ' — ' + String(item.id).trim();
        cfg.bm.appendChild(option);
      }

      if (businesses.length === 1) {
        cfg.bm.value = String(businesses[0].id || '').trim();
      }

      cfg.bm.disabled = false;
      cfg.loaded = true;
      cfg.sessionHint.className = 'pwbm-session ok';
      cfg.sessionHint.textContent = 'BM cache: ' + businesses.length;
      cfg.bmHint.textContent = businesses.length
        ? 'Выбери BM для единственного RK.'
        : 'BM не найден в кэше — введи ID вручную.';
    } catch (error) {
      cfg.bm.textContent = '';
      const failed = document.createElement('option');
      failed.value = '';
      failed.textContent = 'Кэш BM недоступен';
      cfg.bm.appendChild(failed);
      cfg.bm.disabled = false;
      cfg.loaded = true;
      cfg.sessionHint.className = 'pwbm-session error';
      cfg.sessionHint.textContent = 'BM cache error';
      cfg.bmHint.className = 'error';
      cfg.bmHint.textContent =
        'Введи BM ID вручную: ' + String((error && error.message) || error);
    }
    refreshReadyState();
  }).catch(function(error) {
    console.error('[ReMask Worker UI] BM cache pool failed:', error);
  });

  create.addEventListener('click', function() {
    if (create.disabled) return;

    const configs = {};
    for (const profileId of profiles) {
      const cfg = rows[profileId];
      configs[profileId] = {
        business_id: String(cfg.bm.value || cfg.manualBm.value || '').trim(),
        name: String(cfg.name.value || '').trim(),
        currency: String(cfg.currency.value || '').trim().toUpperCase(),
        timezone_id: String(cfg.timezone.value || '').trim()
      };
    }

    create.disabled = true;
    cancel.disabled = true;
    status.textContent = 'Отправляю Add RK Job…';

    pythonWorkerStartAdAccounts({
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

window.pythonWorkerStartAdAccounts = pythonWorkerStartAdAccounts;



async function pythonWorkerStartFanPages(options) {
  const profiles = options && Array.isArray(options.profiles)
    ? options.profiles.map(function(v){ return String(v || '').trim(); }).filter(Boolean)
    : pythonWorkerSelectedProfiles();
  const configs = options && options.configs && typeof options.configs === 'object'
    ? options.configs : {};
  if (!profiles.length || pythonWorkerUiState.busy) return;

  const invalid = profiles.filter(function(profileId) {
    const cfg = configs[String(profileId)] || {};
    const count = Number(cfg.count || 0);
    return !String(cfg.base_name || '').trim()
      || !String(cfg.category || '').trim()
      || !Number.isInteger(count)
      || count < 1 || count > 10;
  });
  if (invalid.length) {
    throw new Error('Add FP: нужны название, category и count 1–10. Проблема: ' + invalid.join(', '));
  }

  pythonWorkerUiState.busy = true;
  pythonWorkerSelectionRefresh();
  pythonWorkerSetText('pythonPwStatus', 'Создаю Fan Page Job для ' + profiles.length + ' FB-профилей...');

  try {
    const nonce = Date.now() + '-' + Math.random().toString(16).slice(2);
    const payloadProfiles = profiles.map(function(profileId, index) {
      const cfg = configs[String(profileId)] || {};
      return {
        profile_id: String(profileId),
        tasks: [{
          action: 'provisioning',
          idempotency_key: 'add-fp-' + nonce + '-' + index,
          payload: {
            steps: ['PROXY_CHECK', 'FAN_PAGES'],
            scope_key: 'add-fp-' + nonce + '-' + index,
            parameters: {
              FAN_PAGES: {
                base_name: String(cfg.base_name || '').trim(),
                count: Number(cfg.count),
                category: String(cfg.category || '').trim(),
                bio: String(cfg.bio || '').trim()
              }
            }
          }
        }]
      };
    });

    const data = await pythonWorkerBridge({
      action: 'create',
      idempotency_key: 'workspace-add-fp-' + nonce,
      profiles: payloadProfiles
    });
    const jobId = String((data && data.job && data.job.job_id) || '').trim();
    if (!jobId) throw new Error('Worker did not return job_id.');

    pythonWorkerUiState.jobId = jobId;
    localStorage.setItem('remask_python_worker_job_v1', jobId);
    pythonWorkerSetText('pythonPwJob', 'Job: ' + jobId);
    pythonWorkerSetText('pythonPwStatus', 'Создание Fan Page запущено...');
    pythonWorkerPoll().catch(function(error) {
      pythonWorkerSetText('pythonPwStatus', 'Ошибка polling: ' + ((error && error.message) || error));
    });
  } catch (error) {
    pythonWorkerUiState.busy = false;
    pythonWorkerSelectionRefresh();
    pythonWorkerSetText('pythonPwStatus', 'Add FP: ' + ((error && error.message) || error));
    throw error;
  }
}

async function pythonWorkerOpenOwnFanPageModal() {
  if (pythonWorkerUiState.workerOnline !== true) {
    pythonWorkerSetText('pythonPwStatus', 'Add FP недоступен: worker ещё не READY.');
    await pythonWorkerHealthCheck();
    if (pythonWorkerUiState.workerOnline !== true) return;
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
  title.textContent = 'Создать Fan Page · ' + profiles.length + ' проф.';
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'pwbm-close';
  close.textContent = '×';
  close.addEventListener('click', pythonWorkerCloseOwnBmModal);
  head.appendChild(title); head.appendChild(close);

  const body = document.createElement('div');
  body.className = 'pwbm-body';
  const note = document.createElement('div');
  note.className = 'pwbm-note';
  note.textContent = 'Для двух будущих BM поставь count=2. ReMask создаст две отдельные FP и сохранит их Page ID.';
  body.appendChild(note);

  const rows = {};
  profiles.forEach(function(profileId) {
    const row = document.createElement('div');
    row.className = 'pwbm-row';

    const profile = document.createElement('div');
    profile.className = 'pwbm-profile';
    profile.textContent = profileId;

    const nameField = document.createElement('div');
    nameField.className = 'pwbm-field';
    const baseName = document.createElement('input');
    baseName.type = 'text';
    baseName.value = 'ReMask Page';
    baseName.placeholder = 'Базовое название Page';
    const count = document.createElement('input');
    count.type = 'number';
    count.className = 'pwbm-manual';
    count.min = '1'; count.max = '10'; count.value = '2';
    const nameHint = document.createElement('small');
    nameHint.textContent = 'count=2 → “ReMask Page 1” и “ReMask Page 2”.';
    nameField.appendChild(baseName); nameField.appendChild(count); nameField.appendChild(nameHint);

    const metaField = document.createElement('div');
    metaField.className = 'pwbm-field';
    const category = document.createElement('input');
    category.type = 'text';
    category.value = 'Digital creator';
    category.placeholder = 'Категория';
    const bio = document.createElement('input');
    bio.type = 'text';
    bio.className = 'pwbm-manual';
    bio.placeholder = 'Bio (необязательно)';
    const metaHint = document.createElement('small');
    metaHint.textContent = 'Категория выбирается из autocomplete Facebook.';
    metaField.appendChild(category); metaField.appendChild(bio); metaField.appendChild(metaHint);

    row.appendChild(profile); row.appendChild(nameField); row.appendChild(metaField);
    body.appendChild(row);
    rows[profileId] = {baseName:baseName,count:count,category:category,bio:bio};
  });

  const footer = document.createElement('div');
  footer.className = 'pwbm-footer';
  const status = document.createElement('div');
  status.className = 'pwbm-status';
  const actions = document.createElement('div');
  actions.className = 'pwbm-actions';
  const cancel = document.createElement('button');
  cancel.type='button'; cancel.className='btn btn-secondary'; cancel.textContent='Отмена';
  cancel.addEventListener('click', pythonWorkerCloseOwnBmModal);
  const create = document.createElement('button');
  create.type='button'; create.className='btn btn-primary'; create.textContent='Создать FP';
  actions.appendChild(cancel); actions.appendChild(create);
  footer.appendChild(status); footer.appendChild(actions);

  card.appendChild(head); card.appendChild(body); card.appendChild(footer);
  modal.appendChild(card); document.body.appendChild(modal);

  const refresh = function() {
    const bad = profiles.filter(function(profileId) {
      const cfg = rows[profileId];
      const n = Number(cfg.count.value || 0);
      return !String(cfg.baseName.value || '').trim()
        || !String(cfg.category.value || '').trim()
        || !Number.isInteger(n) || n < 1 || n > 10;
    });
    create.disabled = pythonWorkerUiState.busy || bad.length > 0;
    status.textContent = bad.length ? 'Не готовы: ' + bad.join(', ') : 'Готово к Add FP.';
  };

  profiles.forEach(function(profileId) {
    const cfg=rows[profileId];
    cfg.baseName.addEventListener('input',refresh);
    cfg.count.addEventListener('input',refresh);
    cfg.category.addEventListener('input',refresh);
  });

  create.addEventListener('click', function() {
    if (create.disabled) return;
    const configs = {};
    profiles.forEach(function(profileId) {
      const cfg=rows[profileId];
      configs[profileId] = {
        base_name:String(cfg.baseName.value || '').trim(),
        count:Number(cfg.count.value || 0),
        category:String(cfg.category.value || '').trim(),
        bio:String(cfg.bio.value || '').trim()
      };
    });
    create.disabled=true; cancel.disabled=true; status.textContent='Отправляю Add FP Job…';
    pythonWorkerStartFanPages({profiles:profiles,configs:configs})
      .then(pythonWorkerCloseOwnBmModal)
      .catch(function(error){
        cancel.disabled=false; refresh();
        status.textContent='Ошибка: ' + String((error && error.message) || error);
      });
  });

  refresh();
}

window.pythonWorkerStartFanPages = pythonWorkerStartFanPages;

function pythonWorkerEnhanceProfileThreeDots() {
  const candidates = Array.from(document.querySelectorAll(
    '.js-btn-add-bm, #btn_add_bm, .js-python-add-bm, button, a, [role="button"], [role="menuitem"], [data-action]'
  )).filter(function(el) {
    if (!el || el.id === 'pythonProvisionStart') return false;
    if (el.hasAttribute('data-python-worker-rk-menu') || el.hasAttribute('data-python-worker-fp-menu')) return false;
    const label = String(el.textContent || el.value || el.getAttribute('aria-label') || '')
      .replace(/\s+/g,' ').trim();
    return /^(Добавить\s*(?:BM|Business Manager)|Add\s*(?:BM|Business Manager))$/i.test(label);
  });

  for (const addBm of candidates) {
    const parent=addBm.parentNode;
    if (!parent || parent.nodeType !== 1) continue;

    // REMASK_BM_MENU_PYTHON_ONLY_V1
    // The legacy Add BM item used to open the packed-runtime modal
    // (Vertical / Timezone ID) and only later tried to bridge its submit
    // button into the Python worker. That left two competing creation paths.
    // Intercept the menu item itself so Business creation has one route only.
    if (!addBm.hasAttribute('data-python-worker-bm-menu')) {
      addBm.setAttribute('data-python-worker-bm-menu','1');
      addBm.removeAttribute('onclick');
      if (addBm.tagName === 'A') addBm.setAttribute('href','#');
      if (addBm.tagName === 'BUTTON') addBm.type='button';
      addBm.addEventListener('click',function(event){
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
        pythonWorkerOpenOwnBmModal().catch(function(error){
          pythonWorkerSetText(
            'pythonPwStatus',
            'Add BM: ' + String((error && error.message) || error)
          );
        });
      },true);
    }

    if (!parent.querySelector('[data-python-worker-fp-menu="1"]')) {
      const addFp=addBm.cloneNode(true);
      addFp.removeAttribute('id'); addFp.removeAttribute('onclick'); addFp.removeAttribute('data-action');
      addFp.setAttribute('data-python-worker-fp-menu','1');
      addFp.setAttribute('aria-label','Add FP');
      if (addFp.tagName === 'A') addFp.setAttribute('href','#');
      if (addFp.tagName === 'BUTTON') addFp.type='button';
      addFp.textContent='Add FP';
      addFp.addEventListener('click',function(event){
        event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation();
        pythonWorkerOpenOwnFanPageModal().catch(function(error){
          pythonWorkerSetText('pythonPwStatus','Add FP: ' + String((error && error.message) || error));
        });
      },true);
      if (addBm.nextSibling) parent.insertBefore(addFp,addBm.nextSibling); else parent.appendChild(addFp);
    }

    if (!parent.querySelector('[data-python-worker-rk-menu="1"]')) {
      const addRk=addBm.cloneNode(true);
      addRk.removeAttribute('id'); addRk.removeAttribute('onclick'); addRk.removeAttribute('data-action');
      addRk.setAttribute('data-python-worker-rk-menu','1');
      addRk.setAttribute('aria-label','Add RK');
      if (addRk.tagName === 'A') addRk.setAttribute('href','#');
      if (addRk.tagName === 'BUTTON') addRk.type='button';
      addRk.textContent='Add RK';
      addRk.addEventListener('click',function(event){
        event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation();
        pythonWorkerOpenOwnAdAccountModal().catch(function(error){
          pythonWorkerSetText('pythonPwStatus','Add RK: ' + String((error && error.message) || error));
        });
      },true);
      const fp=parent.querySelector('[data-python-worker-fp-menu="1"]');
      if (fp && fp.nextSibling) parent.insertBefore(addRk,fp.nextSibling); else parent.appendChild(addRk);
    }
  }
}

function pythonWorkerInitUi() {
  const start = pythonWorkerEl('pythonProvisionStart');
  const addRk = pythonWorkerEl('pythonProvisionAdAccount');
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

  if (addRk) {
    addRk.addEventListener('click', function(event) {
      event.preventDefault();
      pythonWorkerOpenOwnAdAccountModal().catch(function(error) {
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
  pythonWorkerEnhanceProfileThreeDots();
  pythonWorkerHealthCheck().catch(function(){});
  setInterval(function() {
    pythonWorkerHealthCheck().catch(function(){});
  }, 15000);

  new MutationObserver(function() {
    pythonWorkerEnhanceBmDialog();
    pythonWorkerEnhanceProfileThreeDots();
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

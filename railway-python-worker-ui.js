/* REMASK_PYTHON_WORKER_UI_V1 REMASK_PYTHON_WORKER_UI_V2 REMASK_PYTHON_WORKER_UI_V3 REMASK_PYTHON_WORKER_UI_V133 REMASK_PYTHON_WORKER_UI_V134 REMASK_PYTHON_WORKER_UI_V135 REMASK_PYTHON_WORKER_UI_V136 REMASK_PYTHON_WORKER_UI_V137 REMASK_PYTHON_WORKER_UI_V138 REMASK_PYTHON_WORKER_UI_V139 REMASK_PYTHON_WORKER_UI_V140 REMASK_PYTHON_WORKER_UI_V141 REMASK_PYTHON_WORKER_UI_V142 REMASK_PYTHON_WORKER_UI_V143 REMASK_PYTHON_WORKER_UI_V144 REMASK_PYTHON_WORKER_UI_V145 REMASK_PYTHON_WORKER_UI_V146 REMASK_PYTHON_WORKER_UI_V147 REMASK_PYTHON_WORKER_UI_V148 REMASK_PYTHON_WORKER_UI_V149 REMASK_PYTHON_WORKER_UI_V150 REMASK_PYTHON_WORKER_UI_V151 REMASK_PYTHON_WORKER_UI_V152 REMASK_PYTHON_WORKER_UI_V153 REMASK_PYTHON_WORKER_UI_V154 REMASK_PYTHON_WORKER_UI_V155 REMASK_PYTHON_WORKER_UI_V156 REMASK_PYTHON_WORKER_UI_V157 REMASK_PYTHON_WORKER_UI_V158 REMASK_PYTHON_WORKER_UI_V159 REMASK_PYTHON_WORKER_UI_V160 REMASK_PYTHON_WORKER_UI_V161 REMASK_PYTHON_WORKER_UI_V164 REMASK_PYTHON_WORKER_UI_V166 REMASK_PYTHON_WORKER_UI_V169 REMASK_PYTHON_WORKER_UI_V170 REMASK_PYTHON_WORKER_UI_V171 REMASK_PYTHON_WORKER_UI_V172 REMASK_PYTHON_WORKER_UI_V173 REMASK_PYTHON_WORKER_UI_V174 REMASK_PYTHON_WORKER_UI_V175 REMASK_PYTHON_WORKER_UI_V176 REMASK_PYTHON_WORKER_UI_V177 REMASK_PYTHON_WORKER_UI_V178 REMASK_PYTHON_WORKER_UI_V179 */
const restoredPythonWorkerJobId = localStorage.getItem('remask_python_worker_job_v1') || '';

const restoredPythonWorkerBatchIds = (() => {
  try {
    const raw = localStorage.getItem('remask_python_worker_batch_v1') || '[]';
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.map(function(value) { return String(value || '').trim(); }).filter(Boolean)
      : [];
  } catch (_) {
    return [];
  }
})();

const restoredPythonWorkerBatchKind =
  localStorage.getItem('remask_python_worker_batch_kind_v1') || 'add_rk';

const restoredPythonWorkerBatchTargets = (() => {
  try {
    const raw = localStorage.getItem('remask_python_worker_batch_targets_v1') || '[]';
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter(function(value) {
          return value && typeof value === 'object';
        }).map(function(value) {
          return {
            profile_id: String(value.profile_id || '').trim(),
            business_id: String(value.business_id || '').trim(),
            ad_account_id: String(value.ad_account_id || '').trim(),
            ad_account_name: String(value.ad_account_name || '').trim(),
            business_name: String(value.business_name || '').trim()
          };
        })
      : [];
  } catch (_) {
    return [];
  }
})();

const pythonWorkerUiState = {
  jobId: restoredPythonWorkerJobId,
  job: null,
  batchJobIds: restoredPythonWorkerBatchIds,
  batchTargets: restoredPythonWorkerBatchTargets,
  batchKind: restoredPythonWorkerBatchKind,
  busy: restoredPythonWorkerJobId !== '' || restoredPythonWorkerBatchIds.length > 0,
  workerOnline: null,
  pollTimer: null,
  batchPollTimer: null,
  polling: false,
  batchPolling: false,
  fpResolving: false
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

function pythonWorkerSelectedBusinessTargets() {
  try {
    if (
      typeof selectedRows !== 'function' ||
      !state ||
      String(state.activeTab || '') !== 'businesses'
    ) return [];

    const rows = selectedRows('businesses') || [];
    const out = [];
    const seen = new Set();

    for (const row of rows) {
      const profileId = String(
        (row && (row.profile || row.profile_name || row.profile_id)) || ''
      ).trim();
      const businessId = String(
        (row && (row.id || row.business_id || row.businessId || row.bm_id)) || ''
      ).trim();
      if (!profileId || !/^\d+$/.test(businessId)) continue;

      const key = profileId + ':' + businessId;
      if (seen.has(key)) continue;
      seen.add(key);

      out.push({
        profile_id: profileId,
        business_id: businessId,
        business_name: String(
          (row && (row.name || row.business_name || row.title)) || businessId
        ).trim() || businessId
      });
    }

    return out;
  } catch (_) {
    return [];
  }
}

function pythonWorkerSelectedAdAccountTargets() {
  try {
    if (
      typeof selectedRows !== 'function' ||
      !state ||
      String(state.activeTab || '') !== 'ad_accounts'
    ) return [];

    const rows = selectedRows('ad_accounts') || [];
    const out = [];
    const seen = new Set();

    for (const row of rows) {
      const profileId = String(
        (row && (row.profile || row.profile_name || row.profile_id)) || ''
      ).trim();
      const businessId = String(
        (row && (row.business_id || row.businessId || row.bm_id)) || ''
      ).trim();
      let adAccountId = String(
        (row && (row.id || row.account_id || row.ad_account_id)) || ''
      ).trim();
      if (/^act_/i.test(adAccountId)) adAccountId = adAccountId.slice(4);

      if (
        !profileId ||
        !/^\d+$/.test(businessId) ||
        !/^\d+$/.test(adAccountId)
      ) continue;

      const key = profileId + ':' + businessId + ':' + adAccountId;
      if (seen.has(key)) continue;
      seen.add(key);

      out.push({
        profile_id: profileId,
        business_id: businessId,
        ad_account_id: adAccountId,
        ad_account_name: String(
          (row && (row.name || row.account_name || row.title)) ||
          ('RK ' + adAccountId)
        ).trim() || ('RK ' + adAccountId),
        business_name: String(
          (row && (row.business_name || row.bm_name)) || businessId
        ).trim() || businessId
      });
    }

    return out;
  } catch (_) {
    return [];
  }
}

function pythonWorkerEnsureRkFanPageActions() {
  const actionsButton = document.getElementById('workspaceActions');
  if (!actionsButton || !actionsButton.parentElement) return;

  const targets = pythonWorkerSelectedAdAccountTargets();
  const active = Boolean(
    state &&
    String(state.activeTab || '') === 'ad_accounts' &&
    targets.length
  );

  // FP fast path: one visible action only. The old Create/Attach split forced
  // operators to fill or choose a Page per RK and made bulk work slower.
  for (const legacyId of ['pythonRkCreateFp', 'pythonRkAttachFp']) {
    const legacy = document.getElementById(legacyId);
    if (legacy) legacy.remove();
  }

  let autoBtn = document.getElementById('pythonRkAutoFp');
  if (!autoBtn) {
    autoBtn = document.createElement('button');
    autoBtn.id = 'pythonRkAutoFp';
    autoBtn.type = 'button';
    autoBtn.className = 'btn btn-secondary';
    autoBtn.addEventListener('click', function(event) {
      event.preventDefault();
      event.stopPropagation();
      pythonWorkerStartAutoRkFanPages().catch(function(error) {
        pythonWorkerSetText(
          'pythonPwStatus',
          'FP авто: ' + String((error && error.message) || error)
        );
      });
    });
    actionsButton.parentElement.insertBefore(autoBtn, actionsButton);
  }

  autoBtn.style.display = active ? '' : 'none';
  autoBtn.disabled =
    !active ||
    pythonWorkerUiState.busy ||
    pythonWorkerUiState.fpResolving ||
    pythonWorkerUiState.workerOnline !== true;
  autoBtn.textContent = active
    ? 'FP авто (' + targets.length + ')'
    : 'FP авто';
}

function pythonWorkerEl(id) {
  return document.getElementById(id);
}

function pythonWorkerSetText(id, value) {
  const el = pythonWorkerEl(id);
  if (el) el.textContent = String(value == null ? '' : value);
}

function pythonWorkerStableKey(value) {
  const input = String(value == null ? '' : value).trim().toLowerCase();
  let hash = 2166136261;
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

function pythonWorkerPersistBatchState() {
  localStorage.setItem(
    'remask_python_worker_batch_v1',
    JSON.stringify(
      Array.isArray(pythonWorkerUiState.batchJobIds)
        ? pythonWorkerUiState.batchJobIds
        : []
    )
  );
  localStorage.setItem(
    'remask_python_worker_batch_targets_v1',
    JSON.stringify(
      Array.isArray(pythonWorkerUiState.batchTargets)
        ? pythonWorkerUiState.batchTargets
        : []
    )
  );
  localStorage.setItem(
    'remask_python_worker_batch_kind_v1',
    String(pythonWorkerUiState.batchKind || 'add_rk')
  );
}

function pythonWorkerClearBatchState() {
  pythonWorkerUiState.batchJobIds = [];
  pythonWorkerUiState.batchTargets = [];
  pythonWorkerUiState.batchKind = 'add_rk';
  localStorage.removeItem('remask_python_worker_batch_v1');
  localStorage.removeItem('remask_python_worker_batch_targets_v1');
  localStorage.removeItem('remask_python_worker_batch_kind_v1');
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
  const auto = pythonWorkerEl('pythonProvisionAuto');
  if (auto) {
    auto.disabled = pythonWorkerUiState.busy || profiles.length === 0 || pythonWorkerUiState.workerOnline !== true;
    auto.textContent = profiles.length ? 'Auto FP → BM → RK (' + profiles.length + ')' : 'Auto FP → BM → RK';
  }

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
    const hasTerminalBatchFailure =
      Array.isArray(pythonWorkerUiState.batchJobIds) &&
      pythonWorkerUiState.batchJobIds.length > 0 &&
      pythonWorkerUiState.busy === false;

    retry.disabled =
      pythonWorkerUiState.busy ||
      (
        !hasTerminalBatchFailure &&
        (!pythonWorkerUiState.jobId || !hasRetryableFailed)
      );
    retry.textContent = hasTerminalBatchFailure
      ? 'Retry Failed batch'
      : 'Retry Failed';
  }

  pythonWorkerEnsureRkFanPageActions();

  if (
    !pythonWorkerUiState.jobId &&
    !pythonWorkerUiState.busy &&
    !(
      Array.isArray(pythonWorkerUiState.batchJobIds) &&
      pythonWorkerUiState.batchJobIds.length
    )
  ) {
    pythonWorkerSetText(
      'pythonPwStatus',
      profiles.length
        ? (
            pythonWorkerUiState.workerOnline === true
              ? 'Worker UI v177 · Выбрано FB-профилей: ' + profiles.length + '. Готово к Add BM.'
              : 'Worker UI v177 · Выбрано FB-профилей: ' + profiles.length + '. Жду READY от worker.'
          )
        : 'Worker UI v177 · Выберите FB-профили в Workspace.'
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

function pythonWorkerIsProfileAuthBlockedCode(code) {
  return [
    'CHECKPOINT_REQUIRED',
    'SESSION_EXPIRED',
    'TWO_FACTOR_REQUIRED'
  ].indexOf(String(code || '').trim().toUpperCase()) !== -1;
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

  const authErrorCode = String(
    preflight.auth_error_code ||
    browser.error_code ||
    browser.page_discovery_error_code ||
    ''
  ).trim().toUpperCase();
  const authBlocked =
    preflight.auth_blocked === true ||
    pythonWorkerIsProfileAuthBlockedCode(authErrorCode);

  preflight.auth_blocked = authBlocked;
  preflight.auth_error_code = authErrorCode;
  preflight.facebook_session_ready =
    preflight.facebook_session_ready === true ||
    (
      browser.session_ready === true &&
      authBlocked !== true
    );

  if (authBlocked) {
    const detail = String(
      browser.error ||
      browser.page_discovery_error ||
      'Facebook profile authentication is blocked.'
    ).trim();
    throw new Error(
      (authErrorCode || 'FACEBOOK_AUTH_BLOCKED') +
      ': ' + detail
    );
  }

  if (preflight.facebook_session_ready !== true) {
    throw new Error(
      'FACEBOOK_SESSION_NOT_READY: ' +
      String(browser.error || 'Facebook browser session is not ready.')
    );
  }

  preflight.create_route_ready =
    routes.browser_ui === true &&
    browser.ready === true &&
    browser.create_surface_ready === true &&
    preflight.facebook_session_ready === true;

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

  // REMASK_POST_JOB_LOCAL_REFRESH_V1
  // Successful FP/BM/RK jobs already persist the exact confirmed IDs in ReMask.
  // Refresh Workspace from local state; never hit Facebook again just to render
  // the object that the worker itself has just confirmed.
  try {
    const data = await apiJson(
      'ajax/metaHierarchy.php',
      post({
        action: 'snapshot_profile',
        profile: profileId
      })
    );

    if (typeof applySnapshot === 'function') applySnapshot(data);
    if (typeof render === 'function') render();
    return true;
  } catch (error) {
    console.error('[ReMask Worker UI] snapshot_profile failed for ' + profileId + ':', error);
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

  const pages = Array.isArray(data.pages) ? data.pages : [];
  if (!pages.length && data.sync_required === true) {
    throw new Error('Pages отсутствуют в последней синхронизации. Нажми синхронизацию профиля и открой окно снова.');
  }
  return pages;
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
    empty.textContent = 'Pages не найдены';
    cfg.page.appendChild(empty);
    cfg.error = 'Pages не найдены';
    cfg.pageHint.className = 'error';
    cfg.pageHint.textContent =
      'Pages не найдены. Введи Primary Page ID вручную.';
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
    ' · источник: ' + String(sourceLabel || 'последняя синхронизация') +
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
  note.textContent = 'Pages загружаются из сохранённого состояния ReMask или текущей приватной FB-сессии. Для каждого профиля укажи отдельное название BM и Primary Page.';
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
      failed.textContent = 'Pages нет в последней синхронизации';
      cfg.page.appendChild(failed);
      cfg.page.disabled = false;
      cfg.loaded = true;

      cfg.sessionHint.className = 'pwbm-session error';
      cfg.sessionHint.textContent =
        'Pages не были получены последней синхронизацией · синхронизируй профиль или введи Primary Page ID вручную';
      cfg.pageHint.className = 'error';
      cfg.pageHint.textContent =
        'Нет Pages в сохранённом результате синхронизации: ' + cfg.error +
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
    const plannedEntities = new Set();
    items.forEach(function(item) {
      (Array.isArray(item.tasks) ? item.tasks : []).forEach(function(task) {
        const steps = task && task.payload && task.payload.steps;
        (Array.isArray(steps) ? steps : []).forEach(function(step) {
          const label = {FAN_PAGES:'FP',BUSINESS:'BM',AD_ACCOUNT:'РК'}[String(step).toUpperCase()];
          if (label) plannedEntities.add(label);
        });
      });
    });
    const compositeLabel = ['FP','BM','РК'].filter(function(label) { return plannedEntities.has(label); }).join(' → ');
    const isCompositeJob = plannedEntities.size > 1;
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
        isCompositeJob
          ? 'Создание ' + compositeLabel + ' завершено. ID сохранены в Job.' + (unconfirmed.length ? ' Workspace sync не подтвердил: ' + unconfirmed.join(', ') + '.' : ' Workspace sync завершён.')
          : isFanPageJob
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
      const entityLabel = isCompositeJob ? compositeLabel : (isFanPageJob ? 'FP' : (isAdAccountJob ? 'RK' : 'BM'));
      const entityFailureLabel = isCompositeJob
        ? 'Создание ' + compositeLabel + ' завершилось ошибкой: '
        : isFanPageJob
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
  const batchIds = Array.isArray(pythonWorkerUiState.batchJobIds)
    ? pythonWorkerUiState.batchJobIds.slice()
    : [];

  if (batchIds.length) {
    if (pythonWorkerUiState.busy) return;

    pythonWorkerUiState.busy = true;
    pythonWorkerSelectionRefresh();
    pythonWorkerSetText(
      'pythonPwStatus',
      'Retry Failed: проверяю ' + batchIds.length + ' batch Jobs...'
    );

    try {
      let requeued = 0;
      const retryErrors = [];

      await pythonWorkerMapLimit(batchIds, 4, async function(jobId) {
        try {
          const data = await pythonWorkerBridge({
            action: 'retry_failed',
            job_id: jobId
          });
          requeued += Number(
            (data && data.result && data.result.requeued) || 0
          );
        } catch (error) {
          retryErrors.push(
            jobId + ': ' + String((error && error.message) || error)
          );
        }
      });

      if (requeued <= 0) {
        pythonWorkerUiState.busy = false;
        pythonWorkerSelectionRefresh();
        pythonWorkerSetText(
          'pythonPwStatus',
          retryErrors.length
            ? 'Retry Failed batch: ничего не поставлено в очередь. ' +
              retryErrors.join(' · ')
            : 'Retry Failed batch: нет retryable FAILED элементов.'
        );
        return;
      }

      pythonWorkerUiState.busy = true;
      pythonWorkerPersistBatchState();
      pythonWorkerSetText(
        'pythonPwStatus',
        'Retry Failed batch: возвращено в очередь ' + requeued +
          (retryErrors.length
            ? ' · ошибки отдельных Jobs: ' + retryErrors.join(' · ')
            : '.')
      );

      pythonWorkerPollAdAccountBatch().catch(function(error) {
        pythonWorkerSetText(
          'pythonPwStatus',
          'Retry Failed batch polling: ' +
            String((error && error.message) || error)
        );
      });
      return;
    } catch (error) {
      pythonWorkerUiState.busy = false;
      pythonWorkerSelectionRefresh();
      pythonWorkerSetText(
        'pythonPwStatus',
        'Retry Failed batch error: ' +
          String((error && error.message) || error)
      );
      return;
    }
  }

  if (!pythonWorkerUiState.jobId || pythonWorkerUiState.busy) return;

  pythonWorkerUiState.busy = true;
  pythonWorkerSelectionRefresh();
  pythonWorkerSetText('pythonPwStatus', 'Проверяю FAILED Job перед Retry...');

  try {
    const currentItems = pythonWorkerUiState.job && Array.isArray(pythonWorkerUiState.job.items)
      ? pythonWorkerUiState.job.items
      : [];
    const checkpointProfiles = Array.from(new Set(
      currentItems
        .filter(function(item) {
          return item &&
            String(item.status || '').toUpperCase() === 'FAILED' &&
            pythonWorkerIsProfileAuthBlockedCode(item.error_code);
        })
        .map(function(item) { return String(item.profile_id || '').trim(); })
        .filter(Boolean)
    ));

    if (checkpointProfiles.length) {
      const gate = await pythonWorkerFilterFanPageReadyProfiles(checkpointProfiles);
      if (gate.blocked.length) {
        pythonWorkerUiState.busy = false;
        pythonWorkerSelectionRefresh();
        pythonWorkerSetText(
          'pythonPwStatus',
          'Retry приостановлен: Facebook checkpoint у профиля(ей) ' +
            gate.blocked.join(', ') + '. Job остаётся FAILED/resumable.'
        );
        return;
      }
    }

    pythonWorkerSetText('pythonPwStatus', 'Повторно ставлю FAILED JobItem в очередь...');
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
              // Stable scope keeps duplicate-protection history only.
              // AD_ACCOUNT is never auto-completed from cached entity state;
              // every Add RK request reaches the create handler.
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


async function pythonWorkerStartBusinessAdAccountTargets(targets, configs) {
  const list = Array.isArray(targets) ? targets.slice() : [];
  const settings = configs && typeof configs === 'object' ? configs : {};

  if (!list.length || pythonWorkerUiState.busy) return;

  const invalid = list.filter(function(target, index) {
    const cfg = settings[String(index)] || {};
    return (
      !target ||
      !String(target.profile_id || '').trim() ||
      !/^\d+$/.test(String(target.business_id || '').trim()) ||
      !String(cfg.name || '').trim() ||
      !String(cfg.currency || '').trim() ||
      !/^\d+$/.test(String(cfg.timezone_id == null ? '' : cfg.timezone_id).trim())
    );
  });
  if (invalid.length) {
    throw new Error('Add RK: у выбранных BM не заполнены обязательные поля.');
  }

  pythonWorkerUiState.busy = true;
  pythonWorkerUiState.batchKind = 'add_rk';
  pythonWorkerUiState.batchJobIds = [];
  localStorage.setItem('remask_python_worker_batch_kind_v1', 'add_rk');
  pythonWorkerUiState.batchTargets = list.map(function(target) {
    return {
      profile_id: String(target.profile_id || '').trim(),
      business_id: String(target.business_id || '').trim(),
      business_name: String(target.business_name || target.business_id || '').trim()
    };
  });
  localStorage.removeItem('remask_python_worker_batch_v1');
  pythonWorkerSelectionRefresh();
  pythonWorkerSetText(
    'pythonPwStatus',
    'Создаю независимые Add RK Jobs для ' + list.length + ' выбранных BM...'
  );

  try {
    const nonce = Date.now() + '-' + Math.random().toString(16).slice(2);
    const created = [];

    // One selected BM = one JobItem context. This deliberately avoids putting
    // two AD_ACCOUNT tasks for the same FB profile into one JobItem because
    // provisioning_steps is keyed by item_id + step.
    await pythonWorkerMapLimit(list, 4, async function(target, index) {
      const cfg = settings[String(index)] || {};
      const profileId = String(target.profile_id || '').trim();
      const businessId = String(target.business_id || '').trim();
      const data = await pythonWorkerBridge({
        action: 'create',
        idempotency_key:
          'workspace-add-rk-selected-bm-' + businessId + '-' + nonce,
        profiles: [{
          profile_id: profileId,
          tasks: [{
            action: 'provisioning',
            idempotency_key: 'add-rk-selected-bm-' + businessId + '-' + nonce,
            payload: {
              steps: ['PROXY_CHECK', 'AD_ACCOUNT'],
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
          }]
        }]
      });

      const jobId = String((data && data.job && data.job.job_id) || '').trim();
      if (!jobId) throw new Error('Worker did not return job_id for BM ' + businessId);
      created[index] = jobId;
    });

    pythonWorkerUiState.batchJobIds = created.filter(Boolean);
    if (!pythonWorkerUiState.batchJobIds.length) {
      throw new Error('Worker did not create Add RK Jobs.');
    }

    pythonWorkerUiState.jobId = '';
    localStorage.removeItem('remask_python_worker_job_v1');
    localStorage.setItem(
      'remask_python_worker_batch_v1',
      JSON.stringify(pythonWorkerUiState.batchJobIds)
    );

    pythonWorkerSetText(
      'pythonPwJob',
      'Add RK batch: ' + pythonWorkerUiState.batchJobIds.length + ' Jobs'
    );
    pythonWorkerSetText(
      'pythonPwStatus',
      'Создание RK запущено для ' + pythonWorkerUiState.batchJobIds.length + ' BM.'
    );

    pythonWorkerPollAdAccountBatch().catch(function(error) {
      pythonWorkerSetText(
        'pythonPwStatus',
        'Ошибка batch polling: ' + ((error && error.message) || error)
      );
    });
  } catch (error) {
    pythonWorkerUiState.busy = false;
    pythonWorkerUiState.batchJobIds = [];
    pythonWorkerUiState.batchTargets = [];
    localStorage.removeItem('remask_python_worker_batch_v1');
    pythonWorkerSelectionRefresh();
    throw error;
  }
}

async function pythonWorkerStartRkFanPageTargets(targets, mode, configs) {
  const list = Array.isArray(targets) ? targets.slice() : [];
  const cleanMode = String(mode || '').trim().toLowerCase();
  const settings = configs && typeof configs === 'object' ? configs : {};

  if (!list.length || pythonWorkerUiState.busy) return;
  if (['create', 'attach_existing', 'auto'].indexOf(cleanMode) === -1) {
    throw new Error('FP mode must be create, attach_existing or auto.');
  }

  const invalid = list.filter(function(target, index) {
    const cfg = settings[String(index)] || {};
    const targetMode = cleanMode === 'auto'
      ? String(cfg.mode || '').trim().toLowerCase()
      : cleanMode;

    if (
      !target ||
      !String(target.profile_id || '').trim() ||
      !/^\d+$/.test(String(target.business_id || '').trim()) ||
      !/^\d+$/.test(String(target.ad_account_id || '').trim())
    ) return true;

    if (targetMode === 'create') {
      return !String(cfg.base_name || '').trim() ||
        !String(cfg.category || '').trim();
    }
    if (targetMode === 'attach_existing') {
      return !/^\d+$/.test(String(cfg.existing_page_id || '').trim());
    }
    return true;
  });
  if (invalid.length) {
    throw new Error(
      cleanMode === 'auto'
        ? 'FP авто: не удалось автоматически подготовить FP для части выбранных RK.'
        : cleanMode === 'create'
        ? 'Create FP: нужны имя и category для каждого выбранного RK.'
        : 'Attach FP: нужен Page ID для каждого выбранного RK.'
    );
  }

  pythonWorkerUiState.busy = true;
  pythonWorkerUiState.batchKind = 'rk_fp';
  pythonWorkerUiState.batchJobIds = [];
  pythonWorkerUiState.batchTargets = [];
  localStorage.removeItem('remask_python_worker_batch_v1');
  localStorage.removeItem('remask_python_worker_batch_targets_v1');
  localStorage.setItem('remask_python_worker_batch_kind_v1', 'rk_fp');
  pythonWorkerSelectionRefresh();

  pythonWorkerSetText(
    'pythonPwStatus',
    (
      cleanMode === 'auto'
        ? 'Автоматически подготавливаю FP для '
        : cleanMode === 'create'
        ? 'Создаю и прикрепляю FP к '
        : 'Прикрепляю FP к '
    ) + list.length + ' выбранным RK...'
  );

  const created = new Array(list.length);
  const launchErrors = new Array(list.length);

  await pythonWorkerMapLimit(list, 3, async function(target, index) {
    const cfg = settings[String(index)] || {};
    const profileId = String(target.profile_id || '').trim();
    const businessId = String(target.business_id || '').trim();
    const adAccountId = String(target.ad_account_id || '').trim();

    const normalizedTarget = {
      profile_id: profileId,
      business_id: businessId,
      ad_account_id: adAccountId,
      ad_account_name: String(
        target.ad_account_name || target.ad_account_id || ''
      ).trim()
    };

    try {
      const targetMode = cleanMode === 'auto'
        ? String(cfg.mode || '').trim().toLowerCase()
        : cleanMode;
      const fanPages = {
        mode: targetMode,
        business_id: businessId,
        ad_account_id: adAccountId
      };

      let operationSubject = '';
      if (targetMode === 'create') {
        fanPages.base_name = String(cfg.base_name || '').trim();
        fanPages.count = 1;
        fanPages.category = String(cfg.category || '').trim();
        fanPages.bio = String(cfg.bio || '').trim();
        operationSubject = fanPages.base_name;
      } else {
        fanPages.existing_page_id = String(cfg.existing_page_id || '').trim();
        fanPages.page_name = String(
          cfg.page_name || ('Page ' + fanPages.existing_page_id)
        ).trim();
        fanPages.category = String(cfg.category || '').trim();
        operationSubject = fanPages.existing_page_id;
      }

      const operationToken = pythonWorkerStableKey(
        profileId + '|' +
        businessId + '|' +
        adAccountId + '|' +
        targetMode + '|' +
        operationSubject
      );
      const operationKey =
        targetMode + '-' + businessId + '-' + adAccountId + '-' + operationToken;

      const data = await pythonWorkerBridge({
        action: 'create',
        idempotency_key: 'workspace-rk-fp-' + operationKey,
        profiles: [{
          profile_id: profileId,
          tasks: [{
            action: 'provisioning',
            idempotency_key: 'rk-fp-' + operationKey,
            payload: {
              steps: ['PROXY_CHECK', 'FAN_PAGES'],
              scope_key: 'rk-fp-' + operationKey,
              parameters: {
                FAN_PAGES: fanPages
              }
            }
          }]
        }]
      });

      const jobId = String((data && data.job && data.job.job_id) || '').trim();
      if (!jobId) {
        throw new Error('Worker did not return job_id for RK ' + adAccountId);
      }

      const returnedStatus = String(
        (data && data.job && data.job.status) || ''
      ).trim().toUpperCase();

      // Stable idempotency means a second FP-auto click can resolve to the
      // existing FAILED/PARTIAL Job. Resume it in-place instead of making the
      // user hunt for the separate Retry Failed control.
      if (returnedStatus === 'FAILED' || returnedStatus === 'PARTIAL') {
        const retryData = await pythonWorkerBridge({
          action: 'retry_failed',
          job_id: jobId
        });
        const requeued = Number(
          (retryData && retryData.result && retryData.result.requeued) || 0
        );
        if (requeued > 0) {
          pythonWorkerSetText(
            'pythonPwStatus',
            'FP авто: продолжаю предыдущий Job для RK ' + adAccountId + '...'
          );
        }
      }

      created[index] = {
        job_id: jobId,
        target: normalizedTarget
      };

      const launched = created.filter(Boolean);
      pythonWorkerUiState.batchJobIds = launched.map(function(row) {
        return row.job_id;
      });
      pythonWorkerUiState.batchTargets = launched.map(function(row) {
        return row.target;
      });
      pythonWorkerPersistBatchState();
    } catch (error) {
      launchErrors[index] = String((error && error.message) || error);
    }
  });

  const launched = created.filter(Boolean);
  const failedLaunches = launchErrors.filter(Boolean);

  if (!launched.length) {
    pythonWorkerUiState.busy = false;
    pythonWorkerClearBatchState();
    pythonWorkerSelectionRefresh();
    throw new Error(
      'Worker did not create FP Jobs. ' +
      (failedLaunches[0] || 'Unknown create error.')
    );
  }

  pythonWorkerUiState.batchJobIds = launched.map(function(row) {
    return row.job_id;
  });
  pythonWorkerUiState.batchTargets = launched.map(function(row) {
    return row.target;
  });
  pythonWorkerUiState.batchKind = 'rk_fp';
  pythonWorkerPersistBatchState();

  pythonWorkerUiState.jobId = '';
  localStorage.removeItem('remask_python_worker_job_v1');

  pythonWorkerSetText(
    'pythonPwJob',
    'RK → FP batch: ' +
      pythonWorkerUiState.batchJobIds.length +
      '/' + list.length + ' Jobs'
  );
  pythonWorkerSetText(
    'pythonPwStatus',
    failedLaunches.length
      ? (
          'FP Jobs запущены для ' + launched.length + ' из ' + list.length +
          ' RK; ' + failedLaunches.length +
          ' Job не удалось поставить в очередь. Запущенные Jobs не потеряны.'
        )
      : 'FP Jobs запущены для ' + launched.length + ' RK.'
  );

  pythonWorkerPollAdAccountBatch().catch(function(error) {
    pythonWorkerSetText(
      'pythonPwStatus',
      'Ошибка FP batch polling: ' + ((error && error.message) || error)
    );
  });
}



async function pythonWorkerFilterFanPageReadyProfiles(profileIds) {
  const source = Array.from(new Set(
    (Array.isArray(profileIds) ? profileIds : [])
      .map(function(value) { return String(value || '').trim(); })
      .filter(Boolean)
  ));
  const ready = [];
  const blocked = [];
  const errors = [];

  await pythonWorkerMapLimit(source, 3, async function(profileId) {
    try {
      await pythonWorkerProfilePreflight(profileId);
      ready.push(profileId);
    } catch (error) {
      const authMessage = pythonWorkerFpAuthBlockedMessage(error);
      if (authMessage) {
        blocked.push(profileId);
      } else {
        errors.push(
          profileId + ': ' + String((error && error.message) || error)
        );
      }
    }
  });

  return {ready: ready, blocked: blocked, errors: errors};
}

function pythonWorkerFpAuthBlockedMessage(error) {
  const message = String((error && error.message) || error || '');
  const codeMatch = message.match(/\b(CHECKPOINT_REQUIRED|TWO_FACTOR_REQUIRED|SESSION_EXPIRED)\b/i);
  if (
    !(codeMatch && pythonWorkerIsProfileAuthBlockedCode(codeMatch[1])) &&
    !/(checkpoint|two-factor|redirected.*login)/i.test(message)
  ) {
    return '';
  }
  return message;
}


async function pythonWorkerStartAutoRkFanPages() {
  if (pythonWorkerUiState.workerOnline !== true) {
    pythonWorkerSetText('pythonPwStatus', 'FP авто недоступно: worker ещё не READY.');
    await pythonWorkerHealthCheck();
    if (pythonWorkerUiState.workerOnline !== true) return;
  }

  const targets = pythonWorkerSelectedAdAccountTargets();
  if (!targets.length || pythonWorkerUiState.busy || pythonWorkerUiState.fpResolving) {
    return;
  }

  pythonWorkerUiState.fpResolving = true;
  pythonWorkerSelectionRefresh();
  pythonWorkerSetText(
    'pythonPwStatus',
    'FP авто: подбираю свободные Pages для ' + targets.length + ' RK...'
  );

  try {
    const profiles = Array.from(new Set(
      targets.map(function(target) {
        return String(target.profile_id || '').trim();
      }).filter(Boolean)
    ));

    const authBlocked = new Map();
    await pythonWorkerMapLimit(profiles, 3, async function(profileId) {
      try {
        await pythonWorkerProfilePreflight(profileId);
      } catch (error) {
        const blocked = pythonWorkerFpAuthBlockedMessage(error);
        if (blocked) {
          authBlocked.set(profileId, blocked);
        } else {
          console.warn('[ReMask Worker UI] FP preflight soft-failed:', profileId, error);
        }
      }
    });

    const activeTargets = targets.filter(function(target) {
      return !authBlocked.has(String(target.profile_id || '').trim());
    });
    const blockedProfiles = Array.from(authBlocked.keys());

    if (!activeTargets.length) {
      pythonWorkerUiState.fpResolving = false;
      if (pythonWorkerUiState.batchKind === 'rk_fp') {
        pythonWorkerClearBatchState();
      }
      pythonWorkerUiState.busy = false;
      pythonWorkerSelectionRefresh();
      pythonWorkerSetText('pythonPwJob', '');
      pythonWorkerSetText(
        'pythonPwStatus',
        'FP авто приостановлено: Facebook checkpoint у профиля(ей) ' +
          blockedProfiles.join(', ') +
          '. Backend Job сохранён; новых Jobs и FP не создавалось.'
      );
      return;
    }

    const pageCache = new Map();
    const activeProfiles = Array.from(new Set(
      activeTargets.map(function(target) {
        return String(target.profile_id || '').trim();
      }).filter(Boolean)
    ));

    await pythonWorkerMapLimit(activeProfiles, 4, async function(profileId) {
      try {
        const pages = await pythonWorkerLoadPages(profileId);
        pageCache.set(profileId, Array.isArray(pages) ? pages : []);
      } catch (error) {
        // Fast path must not force manual work when the cache endpoint is
        // temporarily unavailable. No trustworthy candidate means create one.
        pageCache.set(profileId, []);
        console.warn('[ReMask Worker UI] FP auto page cache fallback:', profileId, error);
      }
    });

    const usedPageIds = new Set();
    const configs = {};
    let attachCount = 0;
    let createCount = 0;

    activeTargets.forEach(function(target, index) {
      const profileId = String(target.profile_id || '').trim();
      const businessId = String(target.business_id || '').trim();
      const adAccountId = String(target.ad_account_id || '').trim();
      const pages = (pageCache.get(profileId) || []).filter(function(page) {
        const pageId = String((page && page.id) || '').trim();
        if (!/^\d+$/.test(pageId) || usedPageIds.has(pageId)) return false;
        const ownerBusinessId = String((page && page.business_id) || '').trim();
        const ownerAdAccountId = String((page && page.ad_account_id) || '').trim();
        if (ownerBusinessId && ownerBusinessId !== businessId) return false;
        if (ownerAdAccountId && ownerAdAccountId !== adAccountId) return false;
        return true;
      });

      const inTargetBusiness = pages.find(function(page) {
        return String((page && page.business_id) || '').trim() === businessId;
      });
      const freePage = pages.find(function(page) {
        return !String((page && page.business_id) || '').trim();
      });
      const selectedPage = inTargetBusiness || freePage || null;

      if (selectedPage) {
        const pageId = String(selectedPage.id || '').trim();
        usedPageIds.add(pageId);
        configs[String(index)] = {
          mode: 'attach_existing',
          existing_page_id: pageId,
          page_name: String(selectedPage.name || ('Page ' + pageId)).trim()
        };
        attachCount += 1;
        return;
      }

      const suffix = adAccountId.slice(-6) || String(index + 1);
      configs[String(index)] = {
        mode: 'create',
        base_name: 'ReMask ' + suffix,
        category: 'Digital creator',
        bio: ''
      };
      createCount += 1;
    });

    pythonWorkerSetText(
      'pythonPwStatus',
      'FP авто: ' + attachCount + ' готовых FP будут прикреплены, ' +
      createCount + ' FP будут созданы автоматически.' +
      (blockedProfiles.length
        ? ' Пропущены checkpoint-профили: ' + blockedProfiles.join(', ') + '.'
        : '')
    );

    pythonWorkerUiState.fpResolving = false;
    await pythonWorkerStartRkFanPageTargets(activeTargets, 'auto', configs);
  } catch (error) {
    pythonWorkerUiState.fpResolving = false;
    pythonWorkerSelectionRefresh();
    throw error;
  } finally {
    if (!pythonWorkerUiState.busy) {
      pythonWorkerUiState.fpResolving = false;
      pythonWorkerSelectionRefresh();
    }
  }
}

window.pythonWorkerStartAutoRkFanPages = pythonWorkerStartAutoRkFanPages;

function pythonWorkerFillRkAttachPages(cfg, target, pages) {
  const list = (Array.isArray(pages) ? pages : []).filter(function(item) {
    return item && /^\d+$/.test(String(item.id || '').trim());
  });

  cfg.page.textContent = '';
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = list.length ? 'Выбери Fan Page' : 'Pages не найдены';
  cfg.page.appendChild(placeholder);

  let preferred = null;
  for (const item of list) {
    const pageId = String(item.id || '').trim();
    const ownerBusinessId = String(item.business_id || '').trim();
    const option = document.createElement('option');
    option.value = pageId;
    option.dataset.pageName = String(item.name || pageId);
    option.textContent =
      String(item.name || pageId) + ' — ' + pageId +
      (
        ownerBusinessId
          ? (
              ownerBusinessId === String(target.business_id)
                ? ' · уже в этом BM'
                : ' · в BM ' + ownerBusinessId
            )
          : ''
      );

    if (
      ownerBusinessId &&
      ownerBusinessId !== String(target.business_id)
    ) {
      option.disabled = true;
    } else if (!preferred || ownerBusinessId === String(target.business_id)) {
      preferred = item;
    }
    cfg.page.appendChild(option);
  }

  if (preferred) {
    cfg.page.value = String(preferred.id || '');
    cfg.manual.value = '';
  }
  cfg.loaded = true;
  cfg.hint.textContent =
    'Pages: ' + list.length +
    ' · BM ' + target.business_id +
    ' · можно ввести Page ID вручную.';
}

async function pythonWorkerOpenRkFanPageModal(mode) {
  if (pythonWorkerUiState.workerOnline !== true) {
    pythonWorkerSetText('pythonPwStatus', 'FP недоступны: worker ещё не READY.');
    await pythonWorkerHealthCheck();
    if (pythonWorkerUiState.workerOnline !== true) return;
  }

  const cleanMode = String(mode || '').trim().toLowerCase();
  const targets = pythonWorkerSelectedAdAccountTargets();
  if (!targets.length) {
    pythonWorkerSetText(
      'pythonPwStatus',
      'Во вкладке RK выбери хотя бы один рекламный кабинет с известным BM.'
    );
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
  title.textContent =
    (cleanMode === 'create' ? 'Создать FP для RK' : 'Прикрепить FP к RK') +
    ' · ' + targets.length;
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
  note.textContent = cleanMode === 'create'
    ? (
        'На каждый выбранный RK создаётся одна Fan Page в его FB-профиле, ' +
        'после чего Page автоматически прикрепляется к BM этого RK.'
      )
    : (
        'Выбери существующую Fan Page для каждого RK. ReMask прикрепит её ' +
        'к BM кабинета через Meta Business Settings и проверит результат.'
      );
  body.appendChild(note);

  const rows = {};

  targets.forEach(function(target, index) {
    const row = document.createElement('div');
    row.className = 'pwbm-row';

    const identity = document.createElement('div');
    identity.className = 'pwbm-profile';
    identity.textContent = String(target.ad_account_name || ('RK ' + target.ad_account_id));
    const meta = document.createElement('span');
    meta.className = 'pwbm-session ok';
    meta.textContent =
      'FB ' + target.profile_id +
      ' · BM ' + target.business_id +
      ' · RK ' + target.ad_account_id;
    identity.appendChild(meta);

    if (cleanMode === 'create') {
      const nameField = document.createElement('div');
      nameField.className = 'pwbm-field';
      const name = document.createElement('input');
      name.type = 'text';
      name.value = (
        String(target.ad_account_name || 'ReMask')
          .replace(/\s+/g, ' ')
          .trim()
          .slice(0, 90) + ' Page'
      ).slice(0, 120);
      name.placeholder = 'Название Fan Page';
      const nameHint = document.createElement('small');
      nameHint.textContent = 'Будет создана 1 FP и сразу добавлена в BM этого RK.';
      nameField.appendChild(name);
      nameField.appendChild(nameHint);

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
      metaField.appendChild(category);
      metaField.appendChild(bio);

      row.appendChild(identity);
      row.appendChild(nameField);
      row.appendChild(metaField);
      rows[String(index)] = {
        name: name,
        category: category,
        bio: bio,
        loaded: true
      };
    } else {
      const pageField = document.createElement('div');
      pageField.className = 'pwbm-field';
      const page = document.createElement('select');
      const manual = document.createElement('input');
      manual.type = 'text';
      manual.inputMode = 'numeric';
      manual.className = 'pwbm-manual';
      manual.placeholder = 'Или Page ID вручную';
      const hint = document.createElement('small');
      hint.textContent = 'Загружаю Pages профиля...';
      pageField.appendChild(page);
      pageField.appendChild(manual);
      pageField.appendChild(hint);

      const relation = document.createElement('div');
      relation.className = 'pwbm-field';
      const relationText = document.createElement('small');
      relationText.textContent =
        'Page будет добавлена в BM ' + target.business_id +
        ', который владеет RK ' + target.ad_account_id + '.';
      relation.appendChild(relationText);

      row.appendChild(identity);
      row.appendChild(pageField);
      row.appendChild(relation);
      rows[String(index)] = {
        page: page,
        manual: manual,
        hint: hint,
        loaded: false
      };

      page.addEventListener('change', function() {
        if (String(page.value || '').trim()) manual.value = '';
      });
      manual.addEventListener('input', function() {
        if (String(manual.value || '').trim()) page.value = '';
      });
    }

    body.appendChild(row);
  });

  const footer = document.createElement('div');
  footer.className = 'pwbm-footer';
  const status = document.createElement('div');
  status.className = 'pwbm-status';
  status.textContent = cleanMode === 'create'
    ? 'Готово к созданию.'
    : 'Загружаю Pages...';
  const actions = document.createElement('div');
  actions.className = 'pwbm-actions';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'btn btn-secondary';
  cancel.textContent = 'Отмена';
  cancel.addEventListener('click', pythonWorkerCloseOwnBmModal);
  const submit = document.createElement('button');
  submit.type = 'button';
  submit.className = 'btn btn-primary';
  submit.textContent = cleanMode === 'create' ? 'Создать и прикрепить' : 'Прикрепить';
  actions.appendChild(cancel);
  actions.appendChild(submit);
  footer.appendChild(status);
  footer.appendChild(actions);

  card.appendChild(head);
  card.appendChild(body);
  card.appendChild(footer);
  modal.appendChild(card);
  document.body.appendChild(modal);

  const refresh = function() {
    const bad = targets.filter(function(_, index) {
      const cfg = rows[String(index)];
      if (!cfg || !cfg.loaded) return true;
      if (cleanMode === 'create') {
        return !String(cfg.name.value || '').trim() ||
          !String(cfg.category.value || '').trim();
      }
      return !/^\d+$/.test(
        String(cfg.page.value || cfg.manual.value || '').trim()
      );
    });

    submit.disabled = pythonWorkerUiState.busy || bad.length > 0;
    status.textContent = bad.length
      ? (
          cleanMode === 'create'
            ? 'Заполни имя и category у всех RK.'
            : 'Выбери или введи Page ID для всех RK.'
        )
      : 'Готово: ' + targets.length + ' RK.';
  };

  if (cleanMode === 'create') {
    Object.keys(rows).forEach(function(key) {
      rows[key].name.addEventListener('input', refresh);
      rows[key].category.addEventListener('input', refresh);
    });
  } else {
    const pageCache = new Map();
    pythonWorkerMapLimit(targets, 4, async function(target, index) {
      const profileId = String(target.profile_id || '');
      let pages = pageCache.get(profileId);
      if (!pages) {
        try {
          pages = await pythonWorkerLoadPages(profileId);
        } catch (error) {
          pages = [];
          rows[String(index)].hint.className = 'error';
          rows[String(index)].hint.textContent =
            'Pages cache недоступен: ' +
            String((error && error.message) || error) +
            '. Можно ввести Page ID вручную.';
        }
        pageCache.set(profileId, pages);
      }

      pythonWorkerFillRkAttachPages(
        rows[String(index)],
        target,
        pages
      );
      rows[String(index)].page.addEventListener('change', refresh);
      rows[String(index)].manual.addEventListener('input', refresh);
      refresh();
    }).catch(function(error) {
      status.textContent =
        'Ошибка загрузки Pages: ' + String((error && error.message) || error);
    });
  }

  submit.addEventListener('click', function() {
    if (submit.disabled) return;

    const configs = {};
    targets.forEach(function(target, index) {
      const cfg = rows[String(index)];
      if (cleanMode === 'create') {
        configs[String(index)] = {
          base_name: String(cfg.name.value || '').trim(),
          category: String(cfg.category.value || '').trim(),
          bio: String(cfg.bio.value || '').trim()
        };
      } else {
        const pageId = String(
          cfg.page.value || cfg.manual.value || ''
        ).trim();
        let pageName = 'Page ' + pageId;
        if (cfg.page.value) {
          const selected = cfg.page.options[cfg.page.selectedIndex];
          if (selected && selected.dataset.pageName) {
            pageName = String(selected.dataset.pageName);
          }
        }
        configs[String(index)] = {
          existing_page_id: pageId,
          page_name: pageName
        };
      }
    });

    submit.disabled = true;
    cancel.disabled = true;
    status.textContent = 'Отправляю FP Jobs...';

    pythonWorkerStartRkFanPageTargets(
      targets,
      cleanMode,
      configs
    ).then(function() {
      pythonWorkerCloseOwnBmModal();
    }).catch(function(error) {
      cancel.disabled = false;
      refresh();
      status.textContent =
        'Ошибка: ' + String((error && error.message) || error);
    });
  });

  refresh();
}


async function pythonWorkerPollAdAccountBatch() {
  if (
    !Array.isArray(pythonWorkerUiState.batchJobIds) ||
    !pythonWorkerUiState.batchJobIds.length ||
    pythonWorkerUiState.batchPolling
  ) return;

  pythonWorkerUiState.batchPolling = true;
  const isRkFpBatch = pythonWorkerUiState.batchKind === 'rk_fp';
  try {
    const ids = pythonWorkerUiState.batchJobIds.slice();
    const jobs = new Array(ids.length);

    await pythonWorkerMapLimit(ids, 4, async function(jobId, index) {
      const data = await pythonWorkerBridge({
        action: 'status',
        job_id: jobId
      });
      const job = data && data.job;
      if (!job || typeof job !== 'object') {
        throw new Error('Worker bridge returned no job for ' + jobId);
      }
      jobs[index] = job;
    });

    const mergedItems = [];
    const statuses = [];
    jobs.forEach(function(job, jobIndex) {
      if (!job) return;
      statuses.push(String(job.status || '').toUpperCase());
      const target = pythonWorkerUiState.batchTargets[jobIndex] || null;
      (Array.isArray(job.items) ? job.items : []).forEach(function(item) {
        const copy = Object.assign({}, item);
        if (target) {
          copy.profile_id =
            String(target.profile_id || '') +
            ' · BM ' +
            String(target.business_id || '') +
            (
              isRkFpBatch && target.ad_account_id
                ? ' · RK ' + String(target.ad_account_id)
                : ''
            );
        }
        mergedItems.push(copy);
      });
    });

    const terminal = statuses.length === ids.length && statuses.every(function(status) {
      return ['SUCCESS', 'FAILED', 'PARTIAL'].indexOf(status) !== -1;
    });
    const allSuccess = statuses.length === ids.length && statuses.every(function(status) {
      return status === 'SUCCESS';
    });
    const anyFailed = statuses.some(function(status) {
      return status === 'FAILED' || status === 'PARTIAL';
    });
    const failedItems = mergedItems.filter(function(item) {
      return item && String(item.status || '').toUpperCase() === 'FAILED';
    });
    const authBlockedFailedItems = failedItems.filter(function(item) {
      return pythonWorkerIsProfileAuthBlockedCode(item && item.error_code);
    });
    const onlyAuthBlockedFailures =
      isRkFpBatch &&
      failedItems.length > 0 &&
      authBlockedFailedItems.length === failedItems.length;

    pythonWorkerRenderJob({
      id: 'batch:' + ids.join(','),
      status: terminal ? (allSuccess ? 'SUCCESS' : (anyFailed ? 'PARTIAL' : 'SUCCESS')) : 'RUNNING',
      items: mergedItems
    });
    pythonWorkerSetText(
      'pythonPwJob',
      (isRkFpBatch ? 'RK → FP batch: ' : 'Add RK batch: ') +
        ids.length + ' Jobs'
    );

    if (!terminal) {
      if (pythonWorkerUiState.batchPollTimer) {
        clearTimeout(pythonWorkerUiState.batchPollTimer);
      }
      pythonWorkerUiState.batchPollTimer = setTimeout(function() {
        pythonWorkerPollAdAccountBatch().catch(function(){});
      }, 1000);
      return;
    }

    pythonWorkerUiState.busy = false;
    if (pythonWorkerUiState.batchPollTimer) {
      clearTimeout(pythonWorkerUiState.batchPollTimer);
      pythonWorkerUiState.batchPollTimer = null;
    }

    const realProfiles = Array.from(new Set(
      pythonWorkerUiState.batchTargets
        .map(function(target) { return String(target.profile_id || '').trim(); })
        .filter(Boolean)
    ));
    await pythonWorkerMapLimit(realProfiles, 3, async function(profileId) {
      await pythonWorkerRefreshProfile(profileId);
    });

    pythonWorkerSetText(
      'pythonPwStatus',
      isRkFpBatch
        ? (
            allSuccess
              ? 'Все запущенные FP Jobs SUCCESS: ' + ids.length + ' RK.'
              : onlyAuthBlockedFailures
              ? (
                  'FP batch приостановлен: Facebook checkpoint у ' +
                  authBlockedFailedItems.length +
                  ' Job. Backend Jobs сохранены; повторный Create/attach не отправляется.'
                )
              : 'RK → FP batch завершён: есть FAILED/PARTIAL Jobs. Повторный attach/Create автоматически не отправляется.'
          )
        : (
            allSuccess
              ? 'RK созданы для всех выбранных BM.'
              : 'Add RK batch завершён: есть FAILED/PARTIAL Jobs. Повторный CREATE автоматически не отправляется.'
          )
    );

    if (allSuccess || onlyAuthBlockedFailures) {
      pythonWorkerClearBatchState();
      if (onlyAuthBlockedFailures) {
        pythonWorkerSetText('pythonPwJob', '');
      }
    } else {
      pythonWorkerPersistBatchState();
    }
    pythonWorkerSelectionRefresh();
  } catch (error) {
    pythonWorkerSetText(
      'pythonPwStatus',
      (isRkFpBatch ? 'RK → FP' : 'Add RK') +
        ' polling временно недоступен: ' +
        String((error && error.message) || error) +
        '. Повторяю автоматически.'
    );
    if (
      Array.isArray(pythonWorkerUiState.batchJobIds) &&
      pythonWorkerUiState.batchJobIds.length
    ) {
      if (pythonWorkerUiState.batchPollTimer) {
        clearTimeout(pythonWorkerUiState.batchPollTimer);
      }
      pythonWorkerUiState.batchPollTimer = setTimeout(function() {
        pythonWorkerPollAdAccountBatch().catch(function(){});
      }, 2000);
    }
  } finally {
    pythonWorkerUiState.batchPolling = false;
  }
}

async function pythonWorkerOpenSelectedBusinessAdAccountModal() {
  if (pythonWorkerUiState.workerOnline !== true) {
    pythonWorkerSetText('pythonPwStatus', 'Add RK недоступен: worker ещё не READY.');
    await pythonWorkerHealthCheck();
    if (pythonWorkerUiState.workerOnline !== true) return;
  }

  const targets = pythonWorkerSelectedBusinessTargets();
  if (!targets.length) {
    pythonWorkerSetText(
      'pythonPwStatus',
      'Во вкладке Бизнес-менеджеры выбери хотя бы один BM.'
    );
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
  title.textContent = 'Добавить рекламные кабинеты · ' + targets.length + ' BM';
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
    'Каждый выбранный BM идёт напрямую в Python/browser Add RK. Старый Graph create с end_advertiser здесь не используется.';
  body.appendChild(note);

  const rows = {};
  targets.forEach(function(target, index) {
    const row = document.createElement('div');
    row.className = 'pwbm-row';

    const label = document.createElement('div');
    label.className = 'pwbm-profile';
    label.textContent = String(target.business_name || target.business_id);
    const meta = document.createElement('span');
    meta.className = 'pwbm-session ok';
    meta.textContent =
      String(target.profile_id) + ' · ' + String(target.business_id);
    label.appendChild(meta);

    const field = document.createElement('div');
    field.className = 'pwbm-field';
    const name = document.createElement('input');
    name.type = 'text';
    name.value =
      String(target.business_name || 'ReMask').replace(/\s+/g, ' ').trim().slice(0, 220) +
      ' RK';
    name.placeholder = 'Название рекламного кабинета';
    field.appendChild(name);

    row.appendChild(label);
    row.appendChild(field);
    body.appendChild(row);
    rows[String(index)] = {name: name};
  });

  const shared = document.createElement('div');
  shared.className = 'pwbm-row';
  const currencyField = document.createElement('div');
  currencyField.className = 'pwbm-field';
  const currency = document.createElement('input');
  currency.type = 'text';
  currency.value = 'USD';
  currency.placeholder = 'Currency';
  currencyField.appendChild(currency);

  const timezoneField = document.createElement('div');
  timezoneField.className = 'pwbm-field';
  const timezone = document.createElement('input');
  timezone.type = 'number';
  timezone.value = '1';
  timezone.min = '0';
  timezone.placeholder = 'Meta timezone_id';
  timezoneField.appendChild(timezone);
  shared.appendChild(currencyField);
  shared.appendChild(timezoneField);
  body.appendChild(shared);

  const footer = document.createElement('div');
  footer.className = 'pwbm-footer';
  const status = document.createElement('div');
  status.className = 'pwbm-status';
  status.textContent = 'Готово к Add RK.';
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
  actions.appendChild(cancel);
  actions.appendChild(create);
  footer.appendChild(status);
  footer.appendChild(actions);

  card.appendChild(head);
  card.appendChild(body);
  card.appendChild(footer);
  modal.appendChild(card);
  document.body.appendChild(modal);

  const refresh = function() {
    const namesReady = targets.every(function(_, index) {
      return String(rows[String(index)].name.value || '').trim();
    });
    const currencyReady = /^[A-Za-z]{3}$/.test(String(currency.value || '').trim());
    const timezoneReady = /^\d+$/.test(String(timezone.value || '').trim());
    create.disabled =
      pythonWorkerUiState.busy || !namesReady || !currencyReady || !timezoneReady;
    status.textContent = create.disabled
      ? 'Заполни имя, currency и timezone_id.'
      : 'Готово: ' + targets.length + ' BM.';
  };

  Object.keys(rows).forEach(function(index) {
    rows[index].name.addEventListener('input', refresh);
  });
  currency.addEventListener('input', refresh);
  timezone.addEventListener('input', refresh);

  create.addEventListener('click', function() {
    if (create.disabled) return;
    const configs = {};
    targets.forEach(function(_, index) {
      configs[String(index)] = {
        name: String(rows[String(index)].name.value || '').trim(),
        currency: String(currency.value || '').trim().toUpperCase(),
        timezone_id: Number(timezone.value)
      };
    });

    create.disabled = true;
    cancel.disabled = true;
    status.textContent = 'Отправляю Add RK Jobs…';

    pythonWorkerStartBusinessAdAccountTargets(targets, configs)
      .then(function() {
        pythonWorkerCloseOwnBmModal();
      })
      .catch(function(error) {
        cancel.disabled = false;
        refresh();
        status.textContent = 'Ошибка: ' + String((error && error.message) || error);
      });
  });

  refresh();
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
    'Модель: 1 BM = 1 RK. Add RK всегда запускает create-handler; если в выбранном BM уже есть реальный RK, Job вернёт AD_ACCOUNT_ALREADY_EXISTS.';
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



async function pythonWorkerOpenAutoModal() {
  const pendingKey = 'remask_python_worker_auto_pending_v1';
  let acceptedRequest = null;
  try {
    const saved = JSON.parse(localStorage.getItem(pendingKey) || 'null');
    if (saved && saved.action === 'create' && /^workspace-auto-/.test(saved.idempotency_key)
      && Array.isArray(saved.profiles) && saved.profiles.length
      && saved.profiles.every(function(row) { return row && row.profile_id && Array.isArray(row.tasks)
        && row.tasks.length === 1 && row.tasks[0].payload && row.tasks[0].payload.auto_generate === true; })) acceptedRequest = saved;
  } catch (_) {}
  const profiles = acceptedRequest ? acceptedRequest.profiles.map(function(row) { return String(row.profile_id); }) : pythonWorkerSelectedProfiles();
  if (!profiles.length || pythonWorkerUiState.busy) return;
  if (pythonWorkerUiState.workerOnline !== true) throw new Error('Worker ещё не READY.');
  pythonWorkerEnsureBmModalStyle();
  pythonWorkerCloseOwnBmModal();
  const modal = document.createElement('div');
  modal.id = 'pythonWorkerBmModal';
  const card = document.createElement('div'); card.className = 'pwbm-card';
  const head = document.createElement('div'); head.className = 'pwbm-head';
  const title = document.createElement('div'); title.className = 'pwbm-title';
  title.textContent = 'Автоматическое создание · ' + profiles.length + ' проф.';
  const close = document.createElement('button'); close.type = 'button'; close.className = 'pwbm-close'; close.textContent = '×';
  close.addEventListener('click', pythonWorkerCloseOwnBmModal);
  head.append(title, close);
  const body = document.createElement('div'); body.className = 'pwbm-body';
  const note = document.createElement('div'); note.className = 'pwbm-note';
  note.textContent = 'Каждый комплект: новая FP → новый BM с этой FP → новый РК. Названия и случайный адрес @gmail.com сохраняются при запуске и не меняются при Retry. Адрес — значение для формы; Gmail-ящик не регистрируется. Лимиты и проверки Meta действуют.';
  body.appendChild(note);
  function field(label, input) {
    const holder = document.createElement('label'); holder.className = 'pwbm-field';
    const text = document.createElement('span'); text.textContent = label;
    holder.append(text, input); body.appendChild(holder); return input;
  }
  const mode = document.createElement('select');
  [['2','Только FP'],['3','FP → BM'],['4','FP → BM → РК']].forEach(function(pair) {
    const option = document.createElement('option'); option.value = pair[0]; option.textContent = pair[1]; mode.appendChild(option);
  });
  mode.value = '4'; field('Что создать', mode);
  const count = document.createElement('input'); count.type = 'number'; count.min = '1'; count.max = '20'; count.value = '1';
  field('Комплектов на каждый профиль (1–20)', count);
  const category = document.createElement('input'); category.value = 'Digital creator'; field('Категория FP', category);
  const currency = document.createElement('input'); currency.value = 'USD'; currency.maxLength = 3; field('Валюта РК', currency);
  const timezone = document.createElement('input'); timezone.type = 'number'; timezone.min = '0'; timezone.value = '1'; field('Meta timezone_id РК', timezone);
  const footer = document.createElement('div'); footer.className = 'pwbm-footer';
  const status = document.createElement('div'); status.className = 'pwbm-status';
  const actions = document.createElement('div'); actions.className = 'pwbm-actions';
  const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'btn btn-secondary'; cancel.textContent = 'Отмена';
  cancel.addEventListener('click', pythonWorkerCloseOwnBmModal);
  const create = document.createElement('button'); create.type = 'button'; create.className = 'btn btn-primary'; create.textContent = 'Запустить';
  actions.append(cancel, create); footer.append(status, actions); card.append(head, body, footer); modal.appendChild(card); document.body.appendChild(modal);
  if (acceptedRequest) {
    const template = acceptedRequest.profiles[0].tasks[0].payload;
    mode.value = String(template.steps.length); count.value = String(template.batch_count);
    category.value = template.parameters.FAN_PAGES.category;
    currency.value = template.parameters.AD_ACCOUNT.currency;
    timezone.value = String(template.parameters.AD_ACCOUNT.timezone_id);
    [mode, count, category, currency, timezone].forEach(function(input) { input.disabled = true; });
    create.textContent = 'Повторить отправку';
    note.textContent = 'Восстанавливаем отправку прежнего запроса для профилей ' + profiles.join(', ') + '. Ключ Job и параметры сохранены; повтор не создаёт новый пакет.';
  }
  function refresh() {
    const n = Number(count.value); const total = profiles.length * n; const rk = mode.value === '4';
    currency.disabled = timezone.disabled = !rk || !!acceptedRequest;
    const valid = Number.isInteger(n) && n >= 1 && n <= 20 && total <= 500 && category.value.trim()
      && (!rk || (/^[A-Z]{3}$/.test(currency.value.trim().toUpperCase()) && Number.isInteger(Number(timezone.value)) && Number(timezone.value) >= 0 && timezone.value.trim()));
    create.disabled = pythonWorkerUiState.busy || !valid;
    status.textContent = valid ? 'Будет создано: FP ' + total + ', BM ' + (Number(mode.value) >= 3 ? total : 0) + ', РК ' + (rk ? total : 0) + '.'
      : 'Нужны категория, число комплектов 1–20 и параметры РК. Всего не больше 500 комплектов.';
  }
  [mode, count, category, currency, timezone].forEach(function(input) { input.addEventListener('input', refresh); input.addEventListener('change', refresh); });
  const random = new Uint32Array(4); crypto.getRandomValues(random);
  const nonce = Array.from(random, function(x) { return x.toString(16); }).join('-');
  create.addEventListener('click', async function() {
    if (create.disabled) return;
    const steps = ['PROXY_CHECK','FAN_PAGES','BUSINESS','AD_ACCOUNT'].slice(0, Number(mode.value));
    const n = Number(count.value);
    pythonWorkerUiState.busy = true; refresh(); pythonWorkerSelectionRefresh();
    try {
      if (!acceptedRequest) acceptedRequest = {action:'create', idempotency_key:'workspace-auto-' + nonce,
        profiles:profiles.map(function(profileId) { return {profile_id:String(profileId), tasks:[{action:'provisioning', payload:{
          steps:steps, auto_generate:true, batch_count:n, parameters:{
            FAN_PAGES:{category:category.value.trim()}, AD_ACCOUNT:{currency:currency.value.trim().toUpperCase(), timezone_id:Number(timezone.value)}
          }
        }}]}; })};
      [mode, count, category, currency, timezone].forEach(function(input) { input.disabled = true; });
      localStorage.setItem(pendingKey, JSON.stringify(acceptedRequest));
      const data = await pythonWorkerBridge(acceptedRequest);
      const jobId = String((data && data.job && data.job.job_id) || '').trim();
      if (!jobId) throw new Error('Worker did not return job_id.');
      pythonWorkerClearBatchState();
      pythonWorkerUiState.jobId = jobId; pythonWorkerUiState.job = null;
      localStorage.setItem('remask_python_worker_job_v1', jobId);
      localStorage.removeItem(pendingKey);
      pythonWorkerSetText('pythonPwJob', 'Job: ' + jobId);
      pythonWorkerSetText('pythonPwStatus', 'Автоматическое создание: ' + (profiles.length * n) + ' комплектов.');
      pythonWorkerCloseOwnBmModal();
      pythonWorkerPoll().catch(function(error) { pythonWorkerSetText('pythonPwStatus', 'Ошибка polling: ' + String(error.message || error)); });
    } catch (error) {
      pythonWorkerUiState.busy = false; refresh(); pythonWorkerSelectionRefresh();
      create.textContent = 'Повторить отправку';
      status.textContent = 'Ошибка: ' + String(error.message || error) + '. Повторная отправка использует тот же ключ Job.';
    }
  });
  refresh();
}

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

  pythonWorkerSetText('pythonPwStatus', 'Add FP: проверяю Facebook-сессии...');
  const gate = await pythonWorkerFilterFanPageReadyProfiles(profiles);
  if (!gate.ready.length) {
    pythonWorkerUiState.busy = false;
    pythonWorkerSelectionRefresh();
    pythonWorkerSetText('pythonPwJob', '');
    throw new Error(
      gate.blocked.length
        ? 'CHECKPOINT_REQUIRED: профили ' + gate.blocked.join(', ') +
          '. Fan Page Job не создан.'
        : 'FP preflight не прошёл: ' + (gate.errors[0] || 'нет READY профилей.')
    );
  }

  pythonWorkerUiState.busy = true;
  pythonWorkerSelectionRefresh();
  pythonWorkerSetText(
    'pythonPwStatus',
    'Создаю Fan Page Job для ' + gate.ready.length + ' FB-профилей...' +
      (gate.blocked.length
        ? ' Пропущены checkpoint-профили: ' + gate.blocked.join(', ') + '.'
        : '')
  );

  try {
    const nonce = Date.now() + '-' + Math.random().toString(16).slice(2);
    const payloadProfiles = gate.ready.map(function(profileId, index) {
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
  note.textContent = 'По умолчанию создаётся 1 FP на профиль. При необходимости count можно увеличить до 10; каждый подтверждённый Page ID сохраняется в worker state.';
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
    count.min = '1'; count.max = '10'; count.value = '1';
    const nameHint = document.createElement('small');
    nameHint.textContent = 'count=1 → точное базовое имя; count>1 → имена с порядковым номером.';
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

function pythonWorkerInstallBusinessAddRkInterceptor() {
  document.addEventListener('click', function(event) {
    try {
      if (!state || String(state.activeTab || '') !== 'businesses') return;
      const target = event.target && typeof event.target.closest === 'function'
        ? event.target.closest(
            'button,a,[role="button"],[role="menuitem"],[data-action]'
          )
        : null;
      if (!target) return;

      const label = String(
        target.textContent ||
        target.value ||
        target.getAttribute('aria-label') ||
        ''
      ).replace(/\s+/g, ' ').trim();

      if (!/^(?:Добавить\s+рекламн(?:ый\s+кабинет|ые\s+кабинеты)|Add\s+(?:RK|ad\s+accounts?))$/i.test(label)) {
        return;
      }

      const selected = pythonWorkerSelectedBusinessTargets();
      if (!selected.length) return;

      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();

      pythonWorkerOpenSelectedBusinessAdAccountModal().catch(function(error) {
        pythonWorkerSetText(
          'pythonPwStatus',
          'Add RK: ' + String((error && error.message) || error)
        );
      });
    } catch (_) {}
  }, true);
}

function pythonWorkerInitUi() {
  const start = pythonWorkerEl('pythonProvisionStart');
  if (start && !pythonWorkerEl('pythonProvisionAuto')) {
    const auto = document.createElement('button');
    auto.id = 'pythonProvisionAuto'; auto.type = 'button'; auto.className = start.className;
    auto.addEventListener('click', function(event) {
      event.preventDefault();
      pythonWorkerOpenAutoModal().catch(function(error) { pythonWorkerSetText('pythonPwStatus', String(error.message || error)); });
    });
    start.insertAdjacentElement('afterend', auto);
  }
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
  pythonWorkerInstallBusinessAddRkInterceptor();
  pythonWorkerHealthCheck().catch(function(){});
  setInterval(function() {
    pythonWorkerHealthCheck().catch(function(){});
  }, 15000);

  new MutationObserver(function() {
    pythonWorkerEnhanceBmDialog();
    pythonWorkerEnhanceProfileThreeDots();
  }).observe(document.documentElement, {childList: true, subtree: true});

  if (pythonWorkerUiState.batchJobIds.length) {
    pythonWorkerSetText(
      'pythonPwStatus',
      pythonWorkerUiState.batchKind === 'rk_fp'
        ? 'Восстанавливаю RK → FP batch...'
        : 'Восстанавливаю Add RK batch...'
    );
    pythonWorkerPollAdAccountBatch().catch(function(){});
  } else if (pythonWorkerUiState.jobId) {
    pythonWorkerSetText('pythonPwStatus', 'Восстанавливаю последний Job...');
    pythonWorkerPoll().catch(function(){});
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', pythonWorkerInitUi, {once: true});
} else {
  pythonWorkerInitUi();
}

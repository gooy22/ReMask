/* REMASK_PYTHON_WORKER_UI_V1 */
const pythonWorkerUiState = {
  jobId: localStorage.getItem('remask_python_worker_job_v1') || '',
  job: null,
  busy: false,
  pollTimer: null,
  polling: false
};

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
    start.disabled = pythonWorkerUiState.busy || profiles.length === 0;
    start.textContent = profiles.length
      ? 'Запустить provisioning (' + profiles.length + ')'
      : 'Запустить provisioning';
  }
  if (!pythonWorkerUiState.jobId && !pythonWorkerUiState.busy) {
    pythonWorkerSetText(
      'pythonPwStatus',
      profiles.length
        ? 'Готово к запуску: ' + profiles.length + ' FB-профилей. Worker pipeline: profile context → proxy_check.'
        : 'Выберите FB-профили в Workspace.'
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

function pythonWorkerCurrentStep(item) {
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

async function pythonWorkerPoll() {
  if (!pythonWorkerUiState.jobId || pythonWorkerUiState.polling) return;
  pythonWorkerUiState.polling = true;
  try {
    const data = await pythonWorkerBridge({
      action: 'status',
      job_id: pythonWorkerUiState.jobId
    });
    const job = data.job;
    if (!job || typeof job !== 'object') throw new Error('Worker bridge returned no job.');
    pythonWorkerRenderJob(job);

    const status = String(job.status || '').toUpperCase();
    if (['SUCCESS', 'FAILED', 'PARTIAL'].indexOf(status) === -1) {
      pythonWorkerSchedulePoll(900);
    }
  } catch (error) {
    pythonWorkerSetText('pythonPwStatus', 'Ошибка чтения Job: ' + ((error && error.message) || error));
    pythonWorkerSchedulePoll(3000);
  } finally {
    pythonWorkerUiState.polling = false;
    pythonWorkerSelectionRefresh();
  }
}

async function pythonWorkerStartProvisioning() {
  const profiles = pythonWorkerSelectedProfiles();
  if (!profiles.length || pythonWorkerUiState.busy) return;

  pythonWorkerUiState.busy = true;
  pythonWorkerSelectionRefresh();
  pythonWorkerSetText('pythonPwStatus', 'Создаю bulk Job для ' + profiles.length + ' FB-профилей...');

  try {
    const idempotency = 'workspace-provision-' + Date.now() + '-' + Math.random().toString(16).slice(2);
    const payloadProfiles = profiles.map(function(profileId) {
      return {
        profile_id: profileId,
        tasks: [
          {action: 'proxy_check', payload: {}}
        ]
      };
    });

    const data = await pythonWorkerBridge({
      action: 'create',
      idempotency_key: idempotency,
      profiles: payloadProfiles
    });

    const jobId = String((data && data.job && data.job.job_id) || '').trim();
    if (!jobId) throw new Error('Worker did not return job_id.');

    pythonWorkerUiState.jobId = jobId;
    localStorage.setItem('remask_python_worker_job_v1', jobId);
    pythonWorkerSetText('pythonPwJob', 'Job: ' + jobId);
    pythonWorkerSetText('pythonPwStatus', 'Bulk Job создан. Ожидаю worker...');
    await pythonWorkerPoll();
  } catch (error) {
    pythonWorkerSetText('pythonPwStatus', 'Не удалось создать Job: ' + ((error && error.message) || error));
  } finally {
    pythonWorkerUiState.busy = false;
    pythonWorkerSelectionRefresh();
  }
}

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
    pythonWorkerSetText('pythonPwStatus', 'Retry Failed: возвращено в очередь ' + requeued + '.');
    await pythonWorkerPoll();
  } catch (error) {
    pythonWorkerSetText('pythonPwStatus', 'Retry Failed error: ' + ((error && error.message) || error));
  } finally {
    pythonWorkerUiState.busy = false;
    pythonWorkerSelectionRefresh();
  }
}

function pythonWorkerInitUi() {
  const start = pythonWorkerEl('pythonProvisionStart');
  const retry = pythonWorkerEl('pythonProvisionRetry');
  if (!start || !retry) return;

  start.addEventListener('click', function() {
    pythonWorkerStartProvisioning().catch(function(error) {
      pythonWorkerSetText('pythonPwStatus', String((error && error.message) || error));
    });
  });

  retry.addEventListener('click', function() {
    pythonWorkerRetryFailed().catch(function(error) {
      pythonWorkerSetText('pythonPwStatus', String((error && error.message) || error));
    });
  });

  document.addEventListener('click', function() {
    setTimeout(pythonWorkerSelectionRefresh, 0);
  }, true);
  document.addEventListener('change', function() {
    setTimeout(pythonWorkerSelectionRefresh, 0);
  }, true);

  pythonWorkerSelectionRefresh();

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

/* REMASK_PYTHON_WORKER_UI_V1 REMASK_PYTHON_WORKER_UI_V2 REMASK_PYTHON_WORKER_UI_V3 REMASK_PYTHON_WORKER_UI_V133 */
const restoredPythonWorkerJobId = localStorage.getItem('remask_python_worker_job_v1') || '';

const pythonWorkerUiState = {
  jobId: restoredPythonWorkerJobId,
  job: null,
  busy: restoredPythonWorkerJobId !== '',
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
    start.disabled = pythonWorkerUiState.busy || profiles.length === 0;
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
        ? 'Worker UI v133 · Выбрано FB-профилей: ' + profiles.length + '. Готово к Add BM.'
        : 'Worker UI v133 · Выберите FB-профили в Workspace.'
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
  const profiles = pythonWorkerSelectedProfiles();
  if (!profiles.length || pythonWorkerUiState.busy) return;

  const cleanName = String(bmName || '').trim();
  if (!cleanName) return;

  const dialog = options && options.dialog ? options.dialog : null;
  if (!dialog) {
    throw new Error('Add BM request not sent: Primary Page form is not open.');
  }

  const rowConfig = pythonWorkerBmRowConfig(dialog, profiles, cleanName);
  const missingPrimaryPage = profiles.filter(function(profileId) {
    const cfg = rowConfig[String(profileId)] || {};
    return !String(cfg.page_id || '').trim();
  });

  if (missingPrimaryPage.length) {
    throw new Error('Add BM request not sent: Primary Page is required.');
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

async function pythonWorkerStartProvisioning() {
  const profiles = pythonWorkerSelectedProfiles();
  if (!profiles.length) {
    pythonWorkerSetText('pythonPwStatus', 'Запрос НЕ отправлен: сначала выбери FB-профиль.');
    return;
  }

  pythonWorkerSetText(
    'pythonPwStatus',
    'Открываю Add BM для ' + profiles.length + ' FB-профилей…'
  );

  const existingDialog = pythonWorkerFindBmDialog(null);
  if (existingDialog) {
    pythonWorkerEnhanceBmDialog();
    pythonWorkerSetText(
      'pythonPwStatus',
      'Форма Add BM открыта. Укажи название и Primary Page, затем нажми «Создать BM».'
    );
    return;
  }

  const trigger = pythonWorkerFindLegacyBmTrigger();
  if (!trigger) {
    pythonWorkerSetText(
      'pythonPwStatus',
      'Запрос НЕ отправлен: не найдена кнопка открытия Add BM. Открой Add BM через меню выбранного FB-профиля.'
    );
    return;
  }

  trigger.click();

  setTimeout(function() {
    const dialog = pythonWorkerFindBmDialog(null);
    if (dialog) {
      pythonWorkerEnhanceBmDialog();
      pythonWorkerSetText(
        'pythonPwStatus',
        'Форма Add BM открыта. Укажи название и Primary Page, затем нажми «Создать BM».'
      );
    } else {
      pythonWorkerSetText(
        'pythonPwStatus',
        'Запрос НЕ отправлен: форма Add BM не открылась. Открой её через меню выбранного FB-профиля.'
      );
    }
  }, 150);
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

const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const source = fs.readFileSync('railway-python-worker-ui.js', 'utf8');
const sessionStatuses = new Map([['32', {
  profile_id: '32',
  status: 'AUTH_COOKIE_HISTORY_UNVERIFIED',
  evidence_kind: 'legacy_history_unverified',
  auth_cookie_deletions: []
}]]);
const bridgeCalls = [];
const scope = {
  pythonWorkerProfileSessionStatuses: sessionStatuses,
  pythonWorkerItemErrorCode: item => String(item && item.error_code || ''),
  pythonWorkerItemErrorMessage: item => String(item && item.error_message || ''),
  async pythonWorkerBridge(payload) {
    bridgeCalls.push(payload);
    return {preflight:{ok:true, auth_blocked:false, facebook_session_ready:true}};
  }
};
vm.createContext(scope);

function loadFunction(name, nextMarker) {
  let start = source.indexOf(`function ${name}(`);
  const asyncStart = source.indexOf(`async function ${name}(`);
  if (asyncStart !== -1 && (start === -1 || asyncStart < start)) start = asyncStart;
  assert.notEqual(start, -1, `${name} exists`);
  let end = source.indexOf(`\nfunction ${nextMarker}(`, start);
  const asyncEnd = source.indexOf(`\nasync function ${nextMarker}(`, start);
  if (asyncEnd !== -1 && (end === -1 || asyncEnd < end)) end = asyncEnd;
  assert.notEqual(end, -1, `${nextMarker} boundary exists`);
  vm.runInContext(source.slice(start, end), scope);
}

loadFunction('pythonWorkerItemBlockedByKnownProfileStatus', 'pythonWorkerFailureDetails');
loadFunction('pythonWorkerItemCanRetry', 'pythonWorkerItemBlockedByKnownProfileStatus');
loadFunction('pythonWorkerIsProfileAuthBlockedCode', 'pythonWorkerProfilePreflight');
loadFunction('pythonWorkerProfilePreflight', 'pythonWorkerStepLabel');
loadFunction('pythonWorkerJobStatusView', 'pythonWorkerUserFailureText');
loadFunction('pythonWorkerUserFailureText', 'pythonWorkerPrepareStatusLabel');
loadFunction('pythonWorkerProfileSessionView', 'pythonWorkerStatusDate');

(async () => {
  assert.equal(scope.pythonWorkerIsProfileAuthBlockedCode('META_AUTH_COOKIE_REVOKED'), true);
  assert.equal(scope.pythonWorkerIsProfileAuthBlockedCode('AUTH_COOKIE_HISTORY_UNVERIFIED'), true);
  assert.equal(scope.pythonWorkerIsProfileAuthBlockedCode('PROFILE_IDENTITY_CHANGED'), true);
  assert.equal(scope.pythonWorkerIsProfileAuthBlockedCode('PROFILE_AUTH_GUARD_UNAVAILABLE'), true);
  assert.equal(scope.pythonWorkerItemBlockedByKnownProfileStatus({profile_id:'32'}), true);
  assert.equal(scope.pythonWorkerItemCanRetry({status:'FAILED', profile_id:'32', retryable:true}), false);
  assert.equal(scope.pythonWorkerProfileSessionView(sessionStatuses.get('32')).label, 'СТАРАЯ ЗАПИСЬ НЕ СВЕРЕНА');
  assert.equal(scope.pythonWorkerProfileSessionView({status:'AUTH_COOKIE_REVOKED'}).label, 'META УДАЛИЛА COOKIE');
  assert.equal(scope.pythonWorkerProfileSessionView({status:'PROFILE_AUTH_GUARD_UNAVAILABLE'}).label, 'ПРОВЕРКА ВХОДА НЕ УДАЛАСЬ');

  await assert.rejects(scope.pythonWorkerProfilePreflight('32'), /AUTH_COOKIE_HISTORY_UNVERIFIED/);
  assert.equal(bridgeCalls.length, 0);

  const item = {status:'FAILED', error_code:'META_AUTH_COOKIE_REVOKED'};
  assert.equal(scope.pythonWorkerJobStatusView(item).label, 'META УДАЛИЛА COOKIE');
  assert.match(scope.pythonWorkerUserFailureText(item), /Этот снимок не отправлялся повторно/);
  const historyItem = {status:'FAILED', error_code:'AUTH_COOKIE_HISTORY_UNVERIFIED'};
  assert.equal(scope.pythonWorkerJobStatusView(historyItem).label, 'СТАРАЯ ЗАПИСЬ НЕ СВЕРЕНА');
  assert.match(scope.pythonWorkerUserFailureText(historyItem), /не доказывает отзыв текущего снимка/);
  assert.match(scope.pythonWorkerUserFailureText(historyItem), /остановлены/);
  console.log('Unversioned auth-cookie history stays unverified and blocks requests until resolved.');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});

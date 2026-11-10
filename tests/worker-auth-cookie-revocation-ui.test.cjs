const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const source = fs.readFileSync('railway-python-worker-ui.js', 'utf8');
const sessionStatuses = new Map([['32', {
  profile_id: '32',
  status: 'AUTH_COOKIE_REVOKED',
  auth_cookie_deletions: ['c_user', 'xs']
}]]);
const bridgeCalls = [];
const scope = {
  pythonWorkerProfileSessionStatuses: sessionStatuses,
  pythonWorkerItemErrorCode: item => String(item && item.error_code || ''),
  pythonWorkerItemErrorMessage: item => String(item && item.error_message || ''),
  async pythonWorkerBridge(payload) {
    bridgeCalls.push(payload);
    throw new Error('META_AUTH_COOKIE_REVOKED: Meta explicitly removed c_user and xs');
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
loadFunction('pythonWorkerIsProfileAuthBlockedCode', 'pythonWorkerProfilePreflight');
loadFunction('pythonWorkerProfilePreflight', 'pythonWorkerStepLabel');
loadFunction('pythonWorkerJobStatusView', 'pythonWorkerUserFailureText');
loadFunction('pythonWorkerUserFailureText', 'pythonWorkerPrepareStatusLabel');
loadFunction('pythonWorkerProfileSessionView', 'pythonWorkerStatusDate');

(async () => {
  assert.equal(scope.pythonWorkerIsProfileAuthBlockedCode('META_AUTH_COOKIE_REVOKED'), true);
  assert.equal(scope.pythonWorkerIsProfileAuthBlockedCode('PROFILE_IDENTITY_CHANGED'), true);
  assert.equal(scope.pythonWorkerIsProfileAuthBlockedCode('PROFILE_AUTH_GUARD_UNAVAILABLE'), true);
  assert.equal(scope.pythonWorkerItemBlockedByKnownProfileStatus({profile_id:'32'}), true);
  assert.equal(scope.pythonWorkerProfileSessionView(sessionStatuses.get('32')).label, 'META УДАЛИЛА COOKIE');
  assert.equal(scope.pythonWorkerProfileSessionView({status:'PROFILE_AUTH_GUARD_UNAVAILABLE'}).label, 'ПРОВЕРКА ВХОДА НЕ УДАЛАСЬ');

  await assert.rejects(scope.pythonWorkerProfilePreflight('32'), /META_AUTH_COOKIE_REVOKED/);
  assert.equal(JSON.stringify(bridgeCalls), JSON.stringify([
    {action:'preflight', profile_id:'32', purpose:'fan_pages'}
  ]));

  scope.pythonWorkerBridge = async payload => {
    bridgeCalls.push(payload);
    if (payload.action === 'profile_statuses') {
      return {ok:true, profiles:[{profile_id:'32', status:'UNVERIFIED'}]};
    }
    return {preflight:{ok:true, auth_blocked:false, facebook_session_ready:true}};
  };
  scope.pythonWorkerLoadProfileStatuses = async () => {
    sessionStatuses.set('32', {profile_id:'32', status:'UNVERIFIED'});
  };
  const refreshed = await scope.pythonWorkerProfilePreflight('32');
  assert.equal(refreshed.facebook_session_ready, true);
  assert.equal(sessionStatuses.get('32').status, 'UNVERIFIED');

  const item = {status:'FAILED', error_code:'META_AUTH_COOKIE_REVOKED'};
  assert.equal(scope.pythonWorkerJobStatusView(item).label, 'META УДАЛИЛА COOKIE');
  assert.match(scope.pythonWorkerUserFailureText(item), /Этот снимок не отправлялся повторно/);
  console.log('Revoked Meta auth-cookie status blocks stale preflight and permits refresh validation.');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});

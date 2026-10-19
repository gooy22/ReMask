<?php
require_once __DIR__.'/AtomicJsonFile.php';
require_once __DIR__ . '/MetaLaunchException.php';
require_once __DIR__ . '/JobStoreInterface.php';
require_once __DIR__ . '/JobActivity.php';


class MetaJobStore implements JobStoreInterface
{
    public function __construct(private string $jobsDir, private string $mediaDir)
    {
        $this->ensureDir($this->jobsDir);
        $this->ensureDir($this->mediaDir);
    }

    public function createJob(string $profile, array $accountIds, array $payload, string $uploadedPath, string $originalName, string $mediaType, array $accountOverrides = [], array $reviewAccounts = [], array $targets = [], array $mediaBundle = []): array
    {
        $normalizedTargets = self::normalizeTargets($profile, $accountIds, $targets);
        $profile = (string)$normalizedTargets[0]['profile']; // legacy/job summary fallback
        $accountIds = array_column($normalizedTargets, 'account_id');
        if (!in_array($mediaType, ['image', 'video', 'carousel', 'existing_image', 'existing_video', 'existing_creative', 'existing_instagram_post', 'per_account_existing'], true)) {
            throw new InvalidArgumentException('Unsupported media type.');
        }
        $requiresFile = in_array($mediaType, ['image', 'video'], true);
        $requiresBundle = $mediaType === 'carousel';
        if ($requiresFile && !is_file($uploadedPath)) throw new InvalidArgumentException('Uploaded media is unavailable.');
        if ($requiresBundle) {
            if (count($mediaBundle) < 2 || count($mediaBundle) > 10) throw new InvalidArgumentException('Carousel requires 2 to 10 persisted media files.');
            foreach ($mediaBundle as $index => $bundleItem) {
                if (!is_array($bundleItem) || !is_file((string)($bundleItem['path'] ?? ''))) throw new InvalidArgumentException("Carousel media {$index} is unavailable.");
                if ((string)($bundleItem['media_type'] ?? 'image') !== 'image') throw new InvalidArgumentException('Carousel currently supports image cards only.');
            }
        }

        $jobId = gmdate('YmdHis') . '-' . bin2hex(random_bytes(5));
        $mediaPath = '';
        if ($requiresFile) {
            $ext = pathinfo($originalName, PATHINFO_EXTENSION);
            $mediaPath = rtrim($this->mediaDir, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . $jobId . ($ext !== '' ? '.' . preg_replace('/[^a-zA-Z0-9]/', '', $ext) : '');
            if (!@copy($uploadedPath, $mediaPath)) {
                throw new RuntimeException('Could not persist uploaded media for the job.');
            }
            @chmod($mediaPath, 0600);
        }
        $persistedBundle = [];
        if ($requiresBundle) {
            foreach (array_values($mediaBundle) as $index => $bundleItem) {
                $bundleName = (string)($bundleItem['original_name'] ?? ('card-' . ($index + 1) . '.jpg'));
                $ext = preg_replace('/[^a-zA-Z0-9]/', '', pathinfo($bundleName, PATHINFO_EXTENSION));
                $bundlePath = rtrim($this->mediaDir, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . $jobId . '-carousel-' . $index . ($ext !== '' ? '.' . $ext : '');
                if (!@copy((string)$bundleItem['path'], $bundlePath)) throw new RuntimeException('Could not persist Carousel media for the job.');
                @chmod($bundlePath, 0600);
                $persistedBundle[] = [
                    'index' => $index,
                    'path' => $bundlePath,
                    'original_name' => $bundleName,
                    'media_type' => 'image',
                    'card' => is_array($bundleItem['card'] ?? null) ? $bundleItem['card'] : [],
                ];
            }
        }

        $reviewMap = [];
        foreach ($reviewAccounts as $reviewRow) {
            if (!is_array($reviewRow)) continue;
            $reviewId = (string)($reviewRow['account_id'] ?? '');
            if ($reviewId !== '') $reviewMap[$reviewId] = $reviewRow;
        }

        $now = gmdate('c');
        $items = [];
        foreach ($normalizedTargets as $index => $target) {
            $accountId = (string)$target['account_id'];
            $normalizedAccountId = str_starts_with($accountId, 'act_') ? $accountId : 'act_' . $accountId;
            $items[] = [
                'index' => $index,
                'profile' => (string)$target['profile'],
                'business_id' => (string)($target['business_id'] ?? ''),
                'account_id' => $accountId,
                'status' => 'QUEUED',
                'attempts' => 0,
                'started_at' => null,
                'finished_at' => null,
                'failed_step' => null,
                'ids' => self::emptyIds(),
                'steps' => [],
                'error' => null,
                'not_before' => null,
                'api_usage' => null,
                'lease_owner' => null,
                'lease_expires_at' => null,
                'launch_review' => $reviewMap[$normalizedAccountId] ?? null,
            ];
        }

        $job = [
            'id' => $jobId,
            'profile' => $profile,
            'multi_profile' => count(array_unique(array_column($normalizedTargets, 'profile'))) > 1,
            'targets' => $normalizedTargets,
            'status' => 'QUEUED',
            'created_at' => $now,
            'updated_at' => $now,
            'launch_reviewed_at' => $reviewAccounts !== [] ? $now : null,
            'launch_review_expires_at' => $reviewAccounts !== []
                ? gmdate('c', time() + (defined('REMASK_LAUNCH_REVIEW_TTL') ? REMASK_LAUNCH_REVIEW_TTL : 900))
                : null,
            'payload' => $payload,
            'account_overrides' => $accountOverrides,
            'media' => [
                'path' => $mediaPath,
                'type' => $mediaType,
                'original_name' => $originalName,
                'bundle' => $persistedBundle ?? [],
            ],
            'items' => $items,
        ];
        $this->writeJob($job);
        return $this->publicJob($job);
    }

    public function getJob(string $jobId): array
    {
        $lock = $this->lockJob($jobId);
        try {
            $job = $this->readJob($jobId);
            $changed = $this->recoverStaleRunning($job);
            $changed = $this->recoverReadyRateLimited($job) || $changed;
            if ($changed) $this->writeJob($job);
            return $this->publicJob($job);
        } finally {
            $this->unlockJob($lock);
        }
    }

    public function processNext(string $jobId, callable $processor): array
    {
        $lock = $this->lockJob($jobId);
        try {
            $job = $this->readJob($jobId);
            $this->recoverStaleRunning($job);
            $this->recoverReadyRateLimited($job);
            $nextIndex = null;
            foreach ($job['items'] as $i => $item) {
                if (($item['status'] ?? '') === 'QUEUED') {
                    $nextIndex = $i;
                    break;
                }
            }
            if ($nextIndex === null) {
                $this->refreshJobStatus($job);
                $this->writeJob($job);
                return ['processed' => false, 'job' => $this->publicJob($job)];
            }

            $claimId = gethostname() . ':' . getmypid() . ':' . bin2hex(random_bytes(8));
            $leaseSeconds = defined('REMASK_JOB_STALE_SECONDS') ? REMASK_JOB_STALE_SECONDS : 900;
            $job['status'] = 'RUNNING';
            $job['items'][$nextIndex]['status'] = 'RUNNING';
            $job['items'][$nextIndex]['attempts'] = (int)($job['items'][$nextIndex]['attempts'] ?? 0) + 1;
            $job['items'][$nextIndex]['started_at'] = gmdate('c');
            $job['items'][$nextIndex]['error'] = null;
            $job['items'][$nextIndex]['not_before'] = null;
            $job['items'][$nextIndex]['lease_owner'] = $claimId;
            $job['items'][$nextIndex]['lease_expires_at'] = gmdate('c', time() + $leaseSeconds);
            $job['updated_at'] = gmdate('c');
            $claimedJob = $job;
            $claimedItem = $job['items'][$nextIndex];
            $this->writeJob($job);
        } finally {
            $this->unlockJob($lock);
        }

        $result = null;
        $launchError = null;
        try {
            $result = $processor($claimedJob, $claimedItem);
        } catch (Throwable $e) {
            $launchError = $e;
        }

        $lock = $this->lockJob($jobId);
        try {
            $job = $this->readJob($jobId);
            $currentItem = (array)($job['items'][$nextIndex] ?? []);
            if (($currentItem['status'] ?? '') !== 'RUNNING' || !hash_equals((string)($currentItem['lease_owner'] ?? ''), (string)$claimId)) {
                // A stale worker returned after this item was recovered/retried or
                // completed by another claim. Never let an old attempt overwrite
                // newer Job state.
                $this->refreshJobStatus($job);
                $job['updated_at'] = gmdate('c');
                $this->writeJob($job);
                return [
                    'processed' => true,
                    'discarded' => true,
                    'item_index' => $nextIndex,
                    'job' => $this->publicJob($job),
                ];
            }
            if ($launchError === null) {
                $job['items'][$nextIndex]['status'] = 'SUCCESS';
                $job['items'][$nextIndex]['finished_at'] = gmdate('c');
                $job['items'][$nextIndex]['failed_step'] = null;
                $job['items'][$nextIndex]['ids'] = $result['ids'] ?? self::emptyIds();
                $job['items'][$nextIndex]['steps'] = $result['steps'] ?? [];
                $job['items'][$nextIndex]['error'] = null;
                $job['items'][$nextIndex]['not_before'] = null;
                $job['items'][$nextIndex]['api_usage'] = $result['api_usage'] ?? null;
                $job['items'][$nextIndex]['lease_owner'] = null;
                $job['items'][$nextIndex]['lease_expires_at'] = null;
            } elseif ($launchError instanceof MetaLaunchException) {
                $errorArray = $launchError->toArray();
                $metaError = (array)($errorArray['meta_error'] ?? []);
                if (!empty($metaError['rate_limited'])) {
                    $job['items'][$nextIndex]['status'] = 'RATE_LIMITED';
                    $retryAfter = max(15, (int)($metaError['retry_after_seconds'] ?? 60));
                    $job['items'][$nextIndex]['not_before'] = gmdate('c', time() + $retryAfter);
                    $job['items'][$nextIndex]['api_usage'] = $metaError['usage'] ?? null;
                } else {
                    $job['items'][$nextIndex]['status'] = !empty($errorArray['mutation_uncertain']) ? 'REVIEW_REQUIRED' : 'FAILED';
                    $job['items'][$nextIndex]['not_before'] = null;
                }
                $job['items'][$nextIndex]['finished_at'] = gmdate('c');
                $job['items'][$nextIndex]['failed_step'] = $launchError->failedStep;
                $job['items'][$nextIndex]['ids'] = $launchError->ids;
                $job['items'][$nextIndex]['steps'] = $launchError->steps;
                $job['items'][$nextIndex]['error'] = $errorArray;
                $job['items'][$nextIndex]['lease_owner'] = null;
                $job['items'][$nextIndex]['lease_expires_at'] = null;
            } else {
                $job['items'][$nextIndex]['status'] = 'FAILED';
                $job['items'][$nextIndex]['finished_at'] = gmdate('c');
                $job['items'][$nextIndex]['error'] = ['message' => $launchError->getMessage(), 'type' => get_class($launchError)];
                $job['items'][$nextIndex]['lease_owner'] = null;
                $job['items'][$nextIndex]['lease_expires_at'] = null;
            }

            $this->refreshJobStatus($job);
            $job['updated_at'] = gmdate('c');
            $this->writeJob($job);
            $finalStatus = (string)($job['items'][$nextIndex]['status'] ?? 'FAILED');
            JobActivity::itemResult($claimedJob, $claimedItem, $finalStatus, is_array($result) ? $result : null, $launchError);
            return ['processed' => true, 'item_index' => $nextIndex, 'job' => $this->publicJob($job)];
        } finally {
            $this->unlockJob($lock);
        }
    }

    public function retryFailed(string $jobId, array $itemIndexes = []): array
    {
        $lock = $this->lockJob($jobId);
        try {
            $job = $this->readJob($jobId);
            $count = 0;
            $retriedItems = [];
            $selected = self::normalizeItemIndexSet($itemIndexes);
            foreach ($job['items'] as &$item) {
                $index = (int)($item['index'] ?? -1);
                if (($item['status'] ?? '') !== 'FAILED') continue;
                if ($selected !== [] && !isset($selected[$index])) continue;
                $retriedItems[] = $item;
                $item['status'] = 'QUEUED';
                $item['started_at'] = null;
                $item['finished_at'] = null;
                $item['error'] = null;
                $item['not_before'] = null;
                $item['lease_owner'] = null;
                $item['lease_expires_at'] = null;
                // Keep ids/steps so the executor resumes after the last successful stage.
                $count++;
            }
            unset($item);
            if ($count > 0) $job['status'] = 'QUEUED';
            $job['updated_at'] = gmdate('c');
            $this->writeJob($job);
            foreach ($retriedItems as $retriedItem) JobActivity::retryQueued($job, $retriedItem, 'FAILED', false);
            return ['retried' => $count, 'job' => $this->publicJob($job)];
        } finally {
            $this->unlockJob($lock);
        }
    }

    public function forceRetryReviewRequired(string $jobId, array $itemIndexes = []): array
    {
        $lock = $this->lockJob($jobId);
        try {
            $job = $this->readJob($jobId);
            $count = 0;
            $retriedItems = [];
            $selected = self::normalizeItemIndexSet($itemIndexes);
            foreach ($job['items'] as &$item) {
                $index = (int)($item['index'] ?? -1);
                if (($item['status'] ?? '') !== 'REVIEW_REQUIRED') continue;
                if ($selected !== [] && !isset($selected[$index])) continue;
                $retriedItems[] = $item;
                $item['status'] = 'QUEUED';
                $item['started_at'] = null;
                $item['finished_at'] = null;
                $item['error'] = null;
                $item['not_before'] = null;
                $item['lease_owner'] = null;
                $item['lease_expires_at'] = null;
                // Existing known IDs are kept. The failed mutation itself may
                // have reached Meta without returning an ID, so the caller must
                // explicitly accept the duplicate risk before using this method.
                $count++;
            }
            unset($item);
            if ($count > 0) $job['status'] = 'QUEUED';
            $job['updated_at'] = gmdate('c');
            $this->writeJob($job);
            foreach ($retriedItems as $retriedItem) JobActivity::retryQueued($job, $retriedItem, 'REVIEW_REQUIRED', true);
            return ['retried' => $count, 'job' => $this->publicJob($job)];
        } finally {
            $this->unlockJob($lock);
        }
    }

    public function recordDeliveryResults(string $jobId, string $requestedStatus, array $results): array
    {
        $requestedStatus = strtoupper(trim($requestedStatus));
        if (!in_array($requestedStatus, ['ACTIVE', 'PAUSED'], true)) {
            throw new InvalidArgumentException('Delivery status must be ACTIVE or PAUSED.');
        }
        $resultMap = [];
        foreach ($results as $row) {
            if (!is_array($row)) continue;
            $accountId = (string)($row['account_id'] ?? '');
            if ($accountId !== '') $resultMap[$accountId] = $row;
        }

        $lock = $this->lockJob($jobId);
        try {
            $job = $this->readJob($jobId);
            foreach ($job['items'] as &$item) {
                $accountId = (string)($item['account_id'] ?? '');
                if (!isset($resultMap[$accountId])) continue;
                $row = $resultMap[$accountId];
                $item['delivery'] = [
                    'requested_status' => $requestedStatus,
                    'ok' => !empty($row['ok']),
                    'updated_at' => gmdate('c'),
                    'steps' => (array)($row['steps'] ?? []),
                ];
                JobActivity::deliveryResult($job, $item, $requestedStatus, $row);
            }
            unset($item);
            $job['updated_at'] = gmdate('c');
            $this->writeJob($job);
            return $this->publicJob($job);
        } finally {
            $this->unlockJob($lock);
        }
    }

    public function recentJobs(int $limit = 20): array
    {
        $files = glob(rtrim($this->jobsDir, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . '*.json') ?: [];
        usort($files, fn($a, $b) => filemtime($b) <=> filemtime($a));
        $out = [];
        foreach (array_slice($files, 0, max(1, min($limit, 100))) as $file) {
            $jobId = basename($file, '.json');
            try { $out[] = $this->getJob($jobId); } catch (Throwable) { /* skip corrupted/unreadable entries */ }
        }
        return $out;
    }

    public function claimableJobs(int $limit = 100): array
    {
        $limit = max(1, min($limit, 500));
        $files = glob(rtrim($this->jobsDir, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . '*.json') ?: [];
        // Oldest actionable Job first. Completed history is inspected cheaply and
        // never consumes the result limit, so an old queued Job cannot starve.
        usort($files, fn($a, $b) => filemtime($a) <=> filemtime($b));
        $out = [];
        foreach ($files as $file) {
            $raw = @file_get_contents($file);
            $job = json_decode($raw ?: '', true);
            if (!is_array($job)) continue;
            $status = (string)($job['status'] ?? '');
            if (!in_array($status, ['QUEUED', 'RUNNING', 'RATE_LIMITED'], true)) continue;
            $now = time();
            $staleSeconds = defined('REMASK_JOB_STALE_SECONDS') ? REMASK_JOB_STALE_SECONDS : 900;
            $actionable = false;
            foreach ((array)($job['items'] ?? []) as $item) {
                if (!is_array($item)) continue;
                $itemStatus = (string)($item['status'] ?? '');
                if ($itemStatus === 'QUEUED') { $actionable = true; break; }
                if ($itemStatus === 'RATE_LIMITED') {
                    $notBefore = strtotime((string)($item['not_before'] ?? '')) ?: 0;
                    if ($notBefore <= $now) { $actionable = true; break; }
                }
                if ($itemStatus === 'RUNNING') {
                    $leaseExpires = strtotime((string)($item['lease_expires_at'] ?? '')) ?: 0;
                    $started = strtotime((string)($item['started_at'] ?? '')) ?: 0;
                    if (($leaseExpires > 0 && $leaseExpires <= $now) || ($leaseExpires === 0 && $started > 0 && ($now - $started) >= $staleSeconds)) {
                        $actionable = true; break;
                    }
                }
            }
            if (!$actionable) continue;
            $jobId = basename($file, '.json');
            try {
                $current = $this->getJob($jobId);
                if (in_array((string)($current['status'] ?? ''), ['QUEUED', 'RUNNING', 'RATE_LIMITED'], true)) {
                    $out[] = $current;
                    if (count($out) >= $limit) break;
                }
            } catch (Throwable) { /* skip corrupted/unreadable entries */ }
        }
        return $out;
    }

    private function refreshJobStatus(array &$job): void
    {
        $statuses = array_column($job['items'], 'status');
        if ($statuses !== [] && count(array_filter($statuses, fn($s) => $s === 'SUCCESS')) === count($statuses)) {
            $job['status'] = 'SUCCESS';
        } elseif (in_array('RUNNING', $statuses, true) || in_array('QUEUED', $statuses, true)) {
            $job['status'] = 'RUNNING';
        } elseif (in_array('REVIEW_REQUIRED', $statuses, true)) {
            $job['status'] = 'REVIEW_REQUIRED';
        } elseif (in_array('FAILED', $statuses, true)) {
            $job['status'] = 'FAILED';
        } elseif (in_array('RATE_LIMITED', $statuses, true)) {
            $job['status'] = 'RATE_LIMITED';
        } else {
            $job['status'] = 'QUEUED';
        }
    }

    private function recoverReadyRateLimited(array &$job): bool
    {
        $now = time();
        $changed = false;
        foreach ($job['items'] as &$item) {
            if (($item['status'] ?? '') !== 'RATE_LIMITED') continue;
            $notBefore = strtotime((string)($item['not_before'] ?? '')) ?: 0;
            if ($notBefore > $now) continue;
            $item['status'] = 'QUEUED';
            $item['started_at'] = null;
            $item['finished_at'] = null;
            $item['not_before'] = null;
            $item['lease_owner'] = null;
            $item['lease_expires_at'] = null;
            // Keep IDs/steps so safe resume semantics remain unchanged.
            $changed = true;
        }
        unset($item);
        if ($changed) {
            $this->refreshJobStatus($job);
            $job['updated_at'] = gmdate('c');
        }
        return $changed;
    }

    private function recoverStaleRunning(array &$job): bool
    {
        $threshold = defined('REMASK_JOB_STALE_SECONDS') ? REMASK_JOB_STALE_SECONDS : 900;
        $now = time();
        $changed = false;
        foreach ($job['items'] as &$item) {
            if (($item['status'] ?? '') !== 'RUNNING') continue;
            $started = strtotime((string)($item['started_at'] ?? '')) ?: 0;
            $leaseExpires = strtotime((string)($item['lease_expires_at'] ?? '')) ?: 0;
            $stale = $leaseExpires > 0 ? $leaseExpires <= $now : ($started > 0 && ($now - $started) >= $threshold);
            if (!$stale) continue;
            $item['status'] = 'REVIEW_REQUIRED';
            $item['finished_at'] = gmdate('c');
            $item['lease_owner'] = null;
            $item['lease_expires_at'] = null;
            $item['error'] = [
                'message' => 'Job worker stopped while this item was RUNNING. A Meta mutation may have completed without ReMask receiving the response. Inspect Ads Manager before forcing a retry.',
                'type' => 'stale_running_item',
                'mutation_uncertain' => true,
                'retry_safe' => false,
            ];
            $changed = true;
        }
        unset($item);
        if ($changed) {
            $this->refreshJobStatus($job);
            $job['updated_at'] = gmdate('c');
        }
        return $changed;
    }

    private function readJob(string $jobId): array
    {
        if (!preg_match('/^[A-Za-z0-9-]+$/', $jobId)) throw new InvalidArgumentException('Invalid job id.');
        $file = $this->jobFile($jobId);
        if (!is_file($file)) throw new RuntimeException("Job not found: {$jobId}");
        $raw = file_get_contents($file);
        $job = json_decode($raw ?: '', true);
        if (!is_array($job)) throw new RuntimeException("Job file is corrupted: {$jobId}");
        return $job;
    }

    private function writeJob(array $job): void
    {
        $path = $this->jobFile((string)$job['id']);
        $json = json_encode($job, JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR);
        AtomicJsonFile::replace($path, $json, false);
    }

    private function publicJob(array $job): array
    {
        // Never expose filesystem paths or worker claim internals to the browser.
        if (isset($job['media']['path'])) unset($job['media']['path']);
        if (isset($job['media']['bundle']) && is_array($job['media']['bundle'])) {
            foreach ($job['media']['bundle'] as &$bundleItem) if (is_array($bundleItem)) unset($bundleItem['path']);
            unset($bundleItem);
        }
        foreach ($job['items'] ?? [] as &$item) {
            unset($item['lease_owner'], $item['lease_expires_at']);
        }
        unset($item);
        return $job;
    }

    private function jobFile(string $jobId): string
    {
        return rtrim($this->jobsDir, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . $jobId . '.json';
    }

    private function ensureDir(string $dir): void
    {
        if (!is_dir($dir) && !mkdir($dir, 0700, true) && !is_dir($dir)) {
            throw new RuntimeException("Could not create storage directory: {$dir}");
        }
    }

    private function lockJob(string $jobId)
    {
        if (!preg_match('/^[A-Za-z0-9-]+$/', $jobId)) throw new InvalidArgumentException('Invalid job id.');
        $handle = fopen($this->jobFile($jobId) . '.lock', 'c+');
        if ($handle === false || !flock($handle, LOCK_EX)) throw new RuntimeException('Could not lock job state.');
        return $handle;
    }

    private function unlockJob($handle): void
    {
        if (is_resource($handle)) {
            flock($handle, LOCK_UN);
            fclose($handle);
        }
    }


    /**
     * Normalize legacy single-profile account IDs and new cross-profile Launch targets
     * into one deterministic per-JobItem target list.
     */
    private static function normalizeItemIndexSet(array $itemIndexes): array
    {
        $set = [];
        foreach ($itemIndexes as $value) {
            if (is_int($value) || (is_string($value) && ctype_digit($value))) {
                $index = (int)$value;
                if ($index >= 0) $set[$index] = true;
            }
        }
        return $set;
    }

    public static function normalizeTargets(string $profile, array $accountIds, array $targets = []): array
    {
        $profile = trim($profile);
        $normalized = [];
        $seenAccounts = [];

        if ($targets !== []) {
            foreach ($targets as $target) {
                if (!is_array($target)) throw new InvalidArgumentException('Each launch target must be an object.');
                $targetProfile = trim((string)($target['profile'] ?? ''));
                $accountId = trim((string)($target['account_id'] ?? ''));
                $businessId = trim((string)($target['business_id'] ?? ''));
                if ($targetProfile === '') throw new InvalidArgumentException('Each launch target requires profile.');
                if ($accountId === '') throw new InvalidArgumentException('Each launch target requires account_id.');
                $normalizedAccount = str_starts_with($accountId, 'act_') ? $accountId : 'act_' . $accountId;
                if (isset($seenAccounts[$normalizedAccount])) {
                    if ($seenAccounts[$normalizedAccount] !== $targetProfile) {
                        throw new InvalidArgumentException("Ad account {$normalizedAccount} was selected through multiple Facebook profiles. Choose it only once.");
                    }
                    continue;
                }
                $seenAccounts[$normalizedAccount] = $targetProfile;
                $normalized[] = [
                    'profile' => $targetProfile,
                    'business_id' => $businessId,
                    'account_id' => $normalizedAccount,
                ];
            }
        } else {
            if ($profile === '') throw new InvalidArgumentException('profile is required');
            foreach ($accountIds as $rawAccountId) {
                $accountId = trim((string)$rawAccountId);
                if ($accountId === '') continue;
                $normalizedAccount = str_starts_with($accountId, 'act_') ? $accountId : 'act_' . $accountId;
                if (isset($seenAccounts[$normalizedAccount])) continue;
                $seenAccounts[$normalizedAccount] = $profile;
                $normalized[] = ['profile' => $profile, 'business_id' => '', 'account_id' => $normalizedAccount];
            }
        }

        if ($normalized === []) throw new InvalidArgumentException('Select at least one ad account.');
        return array_values($normalized);
    }

    public static function emptyIds(): array
    {
        return [
            'campaign_id' => null,
            'adset_id' => null,
            'media_id' => null,
            'image_hash' => null,
            'carousel_image_hashes' => [],
            'creative_id' => null,
            'ad_id' => null,
        ];
    }
}

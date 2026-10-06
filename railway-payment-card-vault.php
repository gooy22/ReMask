<?php
declare(strict_types=1);

/** Private encrypted card storage. Never accepts or persists a security code. */
final class RemaskPaymentCardVault {
    private string $dir;
    private string $key;
    public function __construct(?string $directory = null) {
        $this->dir = $directory ?? rtrim((string)(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask'), '/') . '/payment-cards';
        if (!is_dir($this->dir) && !mkdir($this->dir, 0700, true) && !is_dir($this->dir)) throw new RuntimeException('CARD_STORAGE_UNAVAILABLE');
        chmod($this->dir, 0700);
        $lock = fopen($this->dir . '/vault.lock', 'c+');
        if (!$lock || !flock($lock, LOCK_EX)) throw new RuntimeException('CARD_STORAGE_UNAVAILABLE');
        chmod($this->dir . '/vault.lock', 0600);
        try {
            $path = $this->dir . '/key';
            if (!is_file($path)) {
                // An existing vault must never silently receive a new key.
                if (is_file($this->dir . '/cards.json')) throw new RuntimeException('CARD_KEY_MISSING');
                if (file_put_contents($path, random_bytes(32), LOCK_EX) !== 32) throw new RuntimeException('CARD_STORAGE_UNAVAILABLE');
            }
            chmod($path, 0600);
            $key = file_get_contents($path);
            if (!is_string($key) || strlen($key) !== 32) throw new RuntimeException('CARD_KEY_INVALID');
            $this->key = $key;
        } finally { flock($lock, LOCK_UN); fclose($lock); }
    }
    private function locked(callable $operation): mixed {
        $lock = fopen($this->dir . '/vault.lock', 'c+');
        if (!$lock || !flock($lock, LOCK_EX)) throw new RuntimeException('CARD_STORAGE_UNAVAILABLE');
        try {
            $path = $this->dir . '/cards.json';
            $data = is_file($path) ? json_decode((string)file_get_contents($path), true, 32, JSON_THROW_ON_ERROR) : ['cards'=>[], 'bindings'=>[]];
            if (!is_array($data) || !is_array($data['cards'] ?? null) || !is_array($data['bindings'] ?? null)) throw new RuntimeException('CARD_STORAGE_INVALID');
            $before = $data;
            $result = $operation($data);
            if ($data !== $before) {
                $tmp = tempnam($this->dir, 'vault-');
                if (!$tmp) throw new RuntimeException('CARD_STORAGE_UNAVAILABLE');
                try {
                    chmod($tmp, 0600);
                    $encoded = json_encode($data, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
                    if (file_put_contents($tmp, $encoded) !== strlen($encoded) || !rename($tmp, $path)) throw new RuntimeException('CARD_STORAGE_UNAVAILABLE');
                } finally { if (is_file($tmp)) unlink($tmp); }
            }
            return $result;
        } finally { flock($lock, LOCK_UN); fclose($lock); }
    }
    public static function normalize(array $input): array {
        if (isset($input['cvv']) || isset($input['cvc']) || isset($input['security_code'])) throw new InvalidArgumentException('CVV_MUST_NOT_BE_SAVED');
        $pan = preg_replace('/[\s-]+/', '', (string)($input['number'] ?? ''));
        if (!preg_match('/^\d{12,19}$/D', $pan)) throw new InvalidArgumentException('CARD_NUMBER_INVALID');
        $sum=0; $alternate=false;
        for ($i=strlen($pan)-1; $i>=0; $i--) { $n=(int)$pan[$i]; if($alternate){$n*=2;if($n>9)$n-=9;} $sum+=$n;$alternate=!$alternate; }
        if ($sum % 10 !== 0) throw new InvalidArgumentException('CARD_NUMBER_INVALID');
        $month = (int)($input['month'] ?? 0); $year = (int)($input['year'] ?? 0); if($year<100)$year+=2000;
        if($month<1 || $month>12 || $year>2100 || sprintf('%04d%02d',$year,$month)<gmdate('Ym')) throw new InvalidArgumentException('CARD_EXPIRY_INVALID');
        $card=['number'=>$pan,'month'=>$month,'year'=>$year];
        foreach (['holder','country','address','city','region','postal_code'] as $field) {
            $value=trim((string)($input[$field]??''));
            if(strlen($value)>200 || preg_match('/[\x00-\x1f]/',$value)) throw new InvalidArgumentException('CARD_BILLING_INVALID');
            $card[$field]=$value;
        }
        $card['label']=trim((string)($input['label']??''));
        if(strlen($card['label'])>80 || preg_match('/\d{12,19}/',preg_replace('/[\s-]+/','',$card['label']))) throw new InvalidArgumentException('CARD_LABEL_INVALID');
        return $card;
    }
    private static function cardBrand(string $value): string {
        $brand=strtolower((string)preg_replace('/[^a-z]/i','',$value));
        return match($brand){
            'americanexpress','amex'=>'amex',
            'mastercard','master'=>'mastercard',
            'visa'=>'visa',
            'discover'=>'discover',
            default=>$brand,
        };
    }
    private static function panBrand(string $pan): string {
        if(str_starts_with($pan,'4'))return 'Visa';
        if(preg_match('/^(?:5[1-5]|2(?:2[2-9]|[3-6]\d|7[01]))/',$pan))return 'Mastercard';
        if(preg_match('/^3[47]/',$pan))return 'Amex';
        if(preg_match('/^(?:6011|65|64[4-9]|622(?:12[6-9]|1[3-9]\d|[2-8]\d{2}|9[01]\d|92[0-5]))/',$pan))return 'Discover';
        return 'Card';
    }
    private static function publicCard(array $row): array {
        return array_intersect_key($row,array_flip(['id','last4','brand','month','year','label','created_at']));
    }
    private static function bindingState(array $row): array {
        // A gate observed before Save needs a new user action, not reconciliation
        // of a submission that never happened. Include legacy stored results.
        if(($row['status']??'')==='ACTION_REQUIRED'&&($row['submitted']??null)===false)$row['status']='BLOCKED';
        return $row;
    }
    public function all(): array {
        return $this->locked(static fn(array &$data) => ['cards'=>array_values(array_map([self::class,'publicCard'],$data['cards'])), 'bindings'=>array_values(array_map([self::class,'bindingState'],$data['bindings']))]);
    }
    public function add(array $input): array {
        $card=self::normalize($input); $fingerprint=hash_hmac('sha256',$card['number'],$this->key);
        $iv=random_bytes(12); $tag='';
        $cipher=openssl_encrypt(json_encode($card,JSON_THROW_ON_ERROR),'aes-256-gcm',$this->key,OPENSSL_RAW_DATA,$iv,$tag,'remask-payment-card-v1');
        if(!is_string($cipher))throw new RuntimeException('CARD_ENCRYPTION_FAILED');
        $pan=$card['number'];
        $brand=self::panBrand($pan);
        return $this->locked(static function(array &$data) use($card,$fingerprint,$iv,$tag,$cipher,$pan,$brand) {
            $id='card_'.bin2hex(random_bytes(12)); $created=gmdate('c');
            foreach($data['cards'] as $old)if(hash_equals((string)$old['fingerprint'],$fingerprint)){ $id=$old['id'];$created=$old['created_at'];break; }
            $row=['id'=>$id,'last4'=>substr($pan,-4),'brand'=>$brand,'month'=>$card['month'],'year'=>$card['year'],'label'=>$card['label'],'created_at'=>$created,
                'fingerprint'=>$fingerprint,'encrypted'=>base64_encode($iv.$tag.$cipher)];
            $data['cards'][$id]=$row;
            return self::publicCard($row);
        });
    }
    public function secret(string $id): array {
        $row=$this->locked(static fn(array &$data) => $data['cards'][$id]??null);
        if(!is_array($row))throw new InvalidArgumentException('CARD_NOT_FOUND');
        return $this->decryptRow($row);
    }
    private function decryptRow(array $row): array {
        $bytes=base64_decode($row['encrypted'],true);
        if(!is_string($bytes)||strlen($bytes)<29)throw new RuntimeException('CARD_STORAGE_INVALID');
        $raw=openssl_decrypt(substr($bytes,28),'aes-256-gcm',$this->key,OPENSSL_RAW_DATA,substr($bytes,0,12),substr($bytes,12,16),'remask-payment-card-v1');
        if(!is_string($raw))throw new RuntimeException('CARD_DECRYPTION_FAILED');
        return json_decode($raw,true,16,JSON_THROW_ON_ERROR);
    }
    public function updateBilling(string $id,array $patch): array {
        if(array_diff(array_keys($patch),['holder','country','address','city','region','postal_code','label']))throw new InvalidArgumentException('CARD_BILLING_PATCH_INVALID');
        return $this->locked(function(array &$data)use($id,$patch){
            $row=$data['cards'][$id]??null;
            if(!is_array($row))throw new InvalidArgumentException('CARD_NOT_FOUND');
            $card=self::normalize(array_replace($this->decryptRow($row),$patch));
            $iv=random_bytes(12);$tag='';
            $cipher=openssl_encrypt(json_encode($card,JSON_THROW_ON_ERROR),'aes-256-gcm',$this->key,OPENSSL_RAW_DATA,$iv,$tag,'remask-payment-card-v1');
            if(!is_string($cipher))throw new RuntimeException('CARD_ENCRYPTION_FAILED');
            $row['encrypted']=base64_encode($iv.$tag.$cipher);$row['label']=$card['label'];
            $data['cards'][$id]=$row;return self::publicCard($row);
        });
    }
    public function missingBilling(string $id,array $fields): array {
        $card=$this->secret($id);$missing=[];
        foreach($fields as $field){
            $kind=(string)($field['kind']??'');
            if(in_array($kind,['holder','country','address','city','region','postal_code'],true)&&
                (($field['required']??false)===true||$kind==='holder')&&trim((string)($card[$kind]??''))==='')$missing[]=$kind;
        }
        return array_values(array_unique($missing));
    }
    private static function exactEmpty(string $profile,string $account,array $funding): bool {
        return ($funding['profile_id']??null)===$profile&&($funding['account_id']??null)===$account&&
            ($funding['account_scope_verified']??false)===true&&($funding['checked_live']??false)===true&&
            ($funding['verification_status']??'')==='NONE'&&($funding['payment_methods']??null)===[]&&
            in_array($funding['source']??'',['private_facebook_billing_ui','private_facebook_selected_rk_payment_tab'],true);
    }
    private static function reviewable(?array $row): bool {
        return is_array($row)&&in_array($row['status'],['IN_PROGRESS','SUBMITTED_UNVERIFIED'],true)&&
            time()-(strtotime((string)$row['updated_at'])?:time())>=180;
    }
    private function retryReview(array $row): array {
        $expires=time()+300;
        $stamp=hash('sha256',json_encode($row,JSON_THROW_ON_ERROR));
        return ['token'=>$expires.'.'.hash_hmac('sha256','card-retry-v1|'.$expires.'|'.$stamp,$this->key),'expires_at'=>gmdate('c',$expires)];
    }
    public function beginReviewed(string $id,string $profile,string $account,string $token,?array $expected,array $funding): array {
        return $this->locked(function(array &$data)use($id,$profile,$account,$token,$expected,$funding){
            if(!isset($data['cards'][$id]))throw new InvalidArgumentException('CARD_NOT_FOUND');
            $key=hash('sha256',$profile.'|'.$account);$old=$data['bindings'][$key]??null;
            if($old!==$expected||!is_array($old)||$old['card_id']!==$id)throw new InvalidArgumentException('CARD_BINDING_CHANGED');
            if(!self::reviewable($old))throw new InvalidArgumentException('CARD_RETRY_REVIEW_REQUIRED');
            if(!preg_match('/^(\d{10})\.([a-f0-9]{64})$/D',$token,$parts)||
                (int)$parts[1]<time()||(int)$parts[1]>time()+300)throw new InvalidArgumentException('CARD_RETRY_REVIEW_EXPIRED');
            $stamp=hash('sha256',json_encode($old,JSON_THROW_ON_ERROR));
            if(!hash_equals(hash_hmac('sha256','card-retry-v1|'.$parts[1].'|'.$stamp,$this->key),$parts[2]))throw new InvalidArgumentException('CARD_RETRY_REVIEW_INVALID');
            if(!self::exactEmpty($profile,$account,$funding))throw new InvalidArgumentException('CARD_RETRY_ACCOUNT_NOT_EMPTY');
            $row=['card_id'=>$id,'profile'=>$profile,'account_id'=>$account,'last4'=>$data['cards'][$id]['last4'],
                'status'=>'IN_PROGRESS','updated_at'=>gmdate('c'),'attempt_id'=>bin2hex(random_bytes(12)),
                'reviewed_retry'=>true,'previous_status'=>$old['status']];
            $data['bindings'][$key]=$row;return $row;
        });
    }
    public function linkedBinding(string $id,string $profile,string $account): ?array {
        return $this->locked(static function(array &$data) use($id,$profile,$account) {
            if(!isset($data['cards'][$id]))throw new InvalidArgumentException('CARD_NOT_FOUND');
            $row=$data['bindings'][hash('sha256',$profile.'|'.$account)]??null;
            return is_array($row)&&$row['card_id']===$id&&$row['status']==='LINKED'?$row:null;
        });
    }
    public function begin(string $id,string $profile,string $account): array {
        return $this->locked(static function(array &$data) use($id,$profile,$account) {
            if(!isset($data['cards'][$id]))throw new InvalidArgumentException('CARD_NOT_FOUND');
            $key=hash('sha256',$profile.'|'.$account);
            $old=$data['bindings'][$key]??null;
            if(is_array($old))$old=self::bindingState($old);
            if(is_array($old)&&in_array($old['status'],['IN_PROGRESS','SUBMITTED_UNVERIFIED','ACTION_REQUIRED'],true))throw new InvalidArgumentException('CARD_BINDING_RECONCILE_REQUIRED');
            if(is_array($old)&&$old['card_id']===$id&&$old['status']==='LINKED')return $old;
            $row=['card_id'=>$id,'profile'=>$profile,'account_id'=>$account,'last4'=>$data['cards'][$id]['last4'],'status'=>'IN_PROGRESS','updated_at'=>gmdate('c'),'attempt_id'=>bin2hex(random_bytes(12))];
            $data['bindings'][$key]=$row;return $row;
        });
    }
    public function binding(string $id,string $profile,string $account): ?array {
        return $this->locked(static function(array &$data)use($id,$profile,$account){
            if(!isset($data['cards'][$id]))throw new InvalidArgumentException('CARD_NOT_FOUND');
            $row=$data['bindings'][hash('sha256',$profile.'|'.$account)]??null;
            if(is_array($row)&&$row['card_id']!==$id)throw new InvalidArgumentException('CARD_BINDING_CARD_MISMATCH');
            return is_array($row)?$row:null;
        });
    }
    public function reconcile(string $id,string $profile,string $account,?array $expected,array $funding): array {
        return $this->locked(function(array &$data)use($id,$profile,$account,$expected,$funding){
            $card=$data['cards'][$id]??null;
            if(!is_array($card))throw new InvalidArgumentException('CARD_NOT_FOUND');
            $key=hash('sha256',$profile.'|'.$account);$current=$data['bindings'][$key]??null;
            if($current!==$expected)throw new InvalidArgumentException('CARD_BINDING_CHANGED');
            if(is_array($current)&&$current['status']==='IN_PROGRESS'&&
                time()-(strtotime((string)$current['updated_at'])?:time())<180)throw new InvalidArgumentException('CARD_BINDING_IN_PROGRESS');
            $scope=($funding['profile_id']??null)===$profile&&($funding['account_id']??null)===$account&&
                ($funding['account_scope_verified']??false)===true&&($funding['checked_live']??false)===true&&
                in_array($funding['source']??'',['private_facebook_billing_ui','private_facebook_selected_rk_payment_tab'],true);
            $brand=static fn(string $value)=>self::cardBrand($value);
            $matches=array_filter($data['cards'],static fn($c)=>$c['last4']===$card['last4']&&$brand((string)$c['brand'])===$brand((string)$card['brand']));
            $observed=false;
            if($scope&&count($matches)===1&&($funding['verification_status']??'')==='LINKED'){
                foreach($funding['payment_methods']??[] as $method){
                    if(is_array($method)&&($method['last4']??null)===$card['last4']&&$brand((string)($method['type']??''))===$brand($card['brand']))$observed=true;
                }
            }
            if(!$observed){
                $result=['status'=>'SUBMITTED_UNVERIFIED','code'=>$scope&&($funding['verification_status']??'')==='NONE'?'CARD_RECONCILE_NO_METHOD':'CARD_RECONCILE_UNVERIFIED','submitted'=>false,'funding_verified'=>false,'funding'=>$funding];
                // Fresh evidence for this exact RK supersedes a cached link.
                // Keep the original attempt time and submission metadata: this
                // read does not submit anything or unlock an automatic retry.
                if(is_array($current)&&$current['status']==='LINKED'&&$scope&&
                    (self::exactEmpty($profile,$account,$funding)||($funding['verification_status']??'')==='LINKED')){
                    $current['status']='SUBMITTED_UNVERIFIED';
                    $current['checked_live']=false;
                    $current['last_result_code']=$result['code'];
                    $data['bindings'][$key]=$current;
                }
                if(self::reviewable($current)&&self::exactEmpty($profile,$account,$funding))$result['retry_review']=$this->retryReview($current);
                return $result;
            }
            $data['bindings'][$key]=['card_id'=>$id,'profile'=>$profile,'account_id'=>$account,'last4'=>$card['last4'],
                'status'=>'LINKED','updated_at'=>gmdate('c'),'last_result_code'=>'CARD_LINK_OBSERVED','submitted'=>false,'checked_live'=>true];
            return ['status'=>'LINKED','code'=>'CARD_LINK_OBSERVED','submitted'=>false,'funding_verified'=>false,'funding'=>$funding];
        });
    }
    public function finish(string $id,string $profile,string $account,string $status,array $result=[],?string $attemptId=null): void {
        if(!in_array($status,['LINKED','BLOCKED','FAILED','SUBMITTED_UNVERIFIED','ACTION_REQUIRED'],true))$status='SUBMITTED_UNVERIFIED';
        // Only explicit evidence that Save was not attempted allows a new bind.
        if(in_array($status,['BLOCKED','FAILED'],true)&&($result['submitted']??null)!==false)$status='SUBMITTED_UNVERIFIED';
        $status=self::bindingState(['status'=>$status,'submitted'=>$result['submitted']??null])['status'];
        $this->locked(static function(array &$data)use($id,$profile,$account,$status,$result,$attemptId){
            $key=hash('sha256',$profile.'|'.$account);$row=$data['bindings'][$key]??null;
            if(!is_array($row)||$row['card_id']!==$id)throw new RuntimeException('CARD_BINDING_SCOPE_MISMATCH');
            if($attemptId!==null&&(($row['attempt_id']??null)!==$attemptId||$row['status']!=='IN_PROGRESS'))throw new InvalidArgumentException('CARD_BINDING_CHANGED');
            $data['bindings'][$key]['status']=$status;$data['bindings'][$key]['updated_at']=gmdate('c');
            if(preg_match('/^[A-Z0-9_]{1,64}$/D',(string)($result['code']??'')))$data['bindings'][$key]['last_result_code']=$result['code'];
            if(array_key_exists('submitted',$result)&&in_array($result['submitted'],[true,false,null],true))$data['bindings'][$key]['submitted']=$result['submitted'];
        });
    }
}

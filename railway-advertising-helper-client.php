<?php
declare(strict_types=1);

/** Shared read bridge. Only the worker may contact Meta through a profile pool. */
final class RemaskAdvertisingHelperClient {
    public static function input(array $input): array {
        $request = [];
        $structured = ['targeting_spec','promoted_object','attribution_spec','special_ad_categories','creative'];
        foreach (array_merge($structured, ['optimization_goal','bid_strategy','campaign_destination_type',
            'ad_format','daily_budget','bid_amount','prediction_duration','is_cbo_enabled','include_forecast']) as $key) {
            if (!array_key_exists($key,$input)) continue;
            $value = $input[$key];
            if (in_array($key,$structured,true) && is_string($value))
                $value = json_decode($value,true,32,JSON_THROW_ON_ERROR);
            if (in_array($key,['daily_budget','bid_amount','prediction_duration'],true)) {
                if (!is_numeric($value) || !is_finite((float)$value) || (float)$value < 0
                    || (float)$value > 9007199254740991 || floor((float)$value)!==(float)$value)
                    throw new InvalidArgumentException('INVALID_ESTIMATE_BUDGET');
                $value = (int)$value;
            }
            if (in_array($key,['is_cbo_enabled','include_forecast'],true)) {
                $value = filter_var($value,FILTER_VALIDATE_BOOLEAN,FILTER_NULL_ON_FAILURE);
                if ($value===null) throw new InvalidArgumentException('INVALID_ESTIMATE_INPUT');
            }
            $request[$key]=$value;
        }
        if (strlen(json_encode($request,JSON_THROW_ON_ERROR))>65536)
            throw new InvalidArgumentException('ADVERTISING_INPUT_TOO_LARGE');
        return $request;
    }

    public static function read(string $profile, string $account, array $catalog, string $helper, array $request): array {
        if (!in_array($helper,['audience_estimate','placements','preview'],true)
            || $profile==='' || ($catalog['profile'] ?? '')!==$profile
            || !preg_match('/^\d{5,30}$/D',$account)
            || !in_array($account,array_column($catalog['ad_accounts']['data'] ?? [],'account_id'),true))
            throw new InvalidArgumentException('SELECT_ADVERTISING_ACCOUNT');
        $raw=json_encode(['account_id'=>$account]+$request,JSON_THROW_ON_ERROR);
        if(strlen($raw)>65536) throw new InvalidArgumentException('ADVERTISING_INPUT_TOO_LARGE');
        $base=rtrim((string)(getenv('REMASK_PYTHON_WORKER_URL') ?: 'http://127.0.0.1:8081'),'/');
        $key=(string)(getenv('REMASK_WORKER_API_KEY') ?: '');
        if($key==='') throw new RuntimeException('ADVERTISING_WORKER_UNAVAILABLE');
        if(session_status()===PHP_SESSION_ACTIVE) session_write_close();
        $context=stream_context_create(['http'=>['method'=>'POST','timeout'=>85,'ignore_errors'=>true,
            'follow_location'=>0,'content'=>$raw,'header'=>"Content-Type: application/json\r\nAccept: application/json\r\nX-Remask-Worker-Key: ".$key."\r\n"]]);
        $body=@file_get_contents($base.'/api/v1/profiles/'.rawurlencode($profile).'/advertising-helper/'.$helper,
            false,$context,0,524289);
        $data=is_string($body) && strlen($body)<=524288 ? json_decode($body,true) : null;
        if (!is_array($data) || ($data['ok']??null)!==true || ($data['profile']??'')!==$profile
            || ($data['account_id']??'')!==$account || ($data['checked_live']??null)!==true
            || ($data['account_scope_verified']??null)!==true || ($data['transport']??'')!=='private_http_h2')
            throw new RuntimeException(is_array($data['detail']??null)
                ? (string)($data['detail']['code']??'ADVERTISING_HELPER_UNAVAILABLE') : 'ADVERTISING_HELPER_UNAVAILABLE');
        return $data;
    }

    public static function template(array $input, array $candidates, callable $read): array {
        $helper=(string)($input['helper']??'audience_estimate');
        if(!in_array($helper,['audience_estimate','placements'],true))
            throw new InvalidArgumentException('TEMPLATE_HELPER_UNAVAILABLE');
        $request=self::input($input);
        // Source identities are transport context, never template resources.
        $builder=['identity'=>[],'existing_media'=>[],'targeting'=>$request['targeting_spec']??[],
            'adset'=>['promoted_object'=>$request['promoted_object']??[]]];
        $unbound=RemaskPrivateFormSchema::unbindTemplate($builder);
        if($unbound!==$builder) throw new InvalidArgumentException('TEMPLATE_OWNED_RESOURCE_UNAVAILABLE');
        $attempted=[];$last=new RuntimeException('TEMPLATE_LOOKUP_SOURCE_UNAVAILABLE');
        foreach($candidates as $context) {
            $profile=$context['profile'];
            if(isset($attempted[$profile]))continue;
            if(count($attempted)>=2)break;
            $attempted[$profile]=true;
            try {
                $data=$read($profile,$context['account_id'],$context['catalog'],$helper,$request);
                if(($data['checked_live']??null)!==true || ($data['account_scope_verified']??null)!==true
                    || ($data['transport']??'')!=='private_http_h2' || ($data['profile']??'')!==$profile
                    || ($data['account_id']??'')!==$context['account_id'])
                    throw new RuntimeException('TEMPLATE_LOOKUP_ACCESS_UNVERIFIED');
                unset($data['profile'],$data['account_id'],$data['business_id']);
                return $data+['template_account_bound'=>false];
            } catch(Throwable $error) { $last=$error; }
        }
        throw $last;
    }
}

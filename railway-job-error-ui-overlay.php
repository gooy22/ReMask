<?php
/**
 * v89: show useful Meta error details in Launch Job table.
 */
$path='/var/www/html/scripts/launch.js';
if(!is_file($path)){fwrite(STDERR,"[job-error-ui] launch.js missing\n");exit(141);}
$s=file_get_contents($path);
if($s===false){fwrite(STDERR,"[job-error-ui] cannot read launch.js\n");exit(142);}

if(strpos($s,'REMASK_META_ERROR_DETAILS_V1')===false){
  $old="        const error = item.error?.meta_error?.message || item.error?.message || '';";

  $new=<<<'JS'
        /* REMASK_META_ERROR_DETAILS_V1 */
        const metaError = item.error?.meta_error || null;
        const errorParts = [];
        if (metaError?.user_title) errorParts.push(metaError.user_title);
        if (metaError?.user_message) errorParts.push(metaError.user_message);
        if (metaError?.message && !errorParts.includes(metaError.message)) errorParts.push(metaError.message);
        if (!metaError && item.error?.message) errorParts.push(item.error.message);
        const metaCode = metaError?.code ?? null;
        const metaSubcode = metaError?.subcode ?? null;
        if (metaCode !== null && metaCode !== undefined) {
            errorParts.push(`code ${metaCode}${metaSubcode !== null && metaSubcode !== undefined ? ` / subcode ${metaSubcode}` : ''}`);
        }
        if (metaError?.fbtrace_id) errorParts.push(`fbtrace ${metaError.fbtrace_id}`);
        const error = errorParts.filter(Boolean).join(' | ');
JS;

  if(strpos($s,$old)===false){fwrite(STDERR,"[job-error-ui] error render target missing\n");exit(143);}
  $s=str_replace($old,$new,$s,$n);
  if($n!==1){fwrite(STDERR,"[job-error-ui] replacement count=$n\n");exit(144);}
  file_put_contents($path,$s);
}

fwrite(STDERR,"[job-error-ui] v89 detailed Meta errors ready\n");

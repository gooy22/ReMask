"""Explicit local variants, persisted through the existing managed PHP library."""
from __future__ import annotations
import asyncio
from pathlib import Path
import tempfile
import threading

from PIL import Image, ImageOps

from .media_preflight import MAX_FILE_BYTES, _run, fail, inspect_media
_preparation_slots = threading.BoundedSemaphore(1)


def _prepare_file(source, output, options):
    if Path(source).resolve() == Path(output).resolve():
        raise fail('MEDIA_ORIGINAL_PROTECTED', 'A prepared variant must not overwrite its original')
    if not isinstance(options, dict) or set(options) - {'encoding','mode','width','height','quality','background'}:
        raise fail('MEDIA_TRANSFORM_UNSUPPORTED', 'Unsupported local preparation options')
    facts = inspect_media(source)
    encoding = options.get('encoding', 'PNG' if facts['media_type'] == 'image' else 'MP4')
    width, height = options.get('width', facts['width']), options.get('height', facts['height'])
    quality = options.get('quality', 85)
    mode = options.get('mode', 'fit')
    if (type(width) is not int or type(height) is not int or not 1 <= width <= 4096
            or not 1 <= height <= 4096 or type(quality) is not int or not 55 <= quality <= 95):
        raise fail('MEDIA_TRANSFORM_UNSUPPORTED', 'Choose dimensions 1–4096 and quality 55–95')
    if facts['media_type'] == 'image':
        if encoding not in {'PNG', 'JPEG'} or mode not in {'fit','crop','pad'}:
            raise fail('MEDIA_TRANSFORM_UNSUPPORTED', 'Images support PNG/JPEG and explicit fit/crop/pad')
        background = options.get('background', '#ffffff')
        import re
        if not isinstance(background, str) or not re.fullmatch(r'#[a-fA-F0-9]{6}', background):
            raise fail('MEDIA_TRANSFORM_UNSUPPORTED', 'Padding background must be an RGB hex color')
        with Image.open(source) as original:
            image = ImageOps.exif_transpose(original).convert('RGBA')
            if mode == 'crop':
                if width > image.width or height > image.height:
                    raise fail('MEDIA_UPSCALING_REQUIRED', 'Crop would upscale the original; choose smaller dimensions or padding')
                image = ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS)
            else:
                image.thumbnail((width, height), Image.Resampling.LANCZOS)
                if mode == 'pad':
                    canvas = Image.new('RGBA', (width,height), background)
                    canvas.alpha_composite(image, ((width-image.width)//2, (height-image.height)//2))
                    image = canvas
            if encoding == 'JPEG':
                canvas = Image.new('RGB', image.size, background)
                canvas.paste(image, mask=image.getchannel('A'))
                image = canvas
            # No original EXIF/GPS/XMP is copied into the prepared asset.
            image.save(output, encoding, **({'quality':quality, 'optimize':True} if encoding == 'JPEG'
                                            else {'optimize':True}))
    else:
        if encoding != 'MP4' or mode != 'fit':
            raise fail('MEDIA_TRANSFORM_UNSUPPORTED', 'Video supports explicit MP4 H.264/AAC conversion with fit only')
        if min(width,height) < 2:
            raise fail('MEDIA_TRANSFORM_UNSUPPORTED', 'Video dimensions must be at least two pixels')
        # Fixed arguments, no shell, no network protocols or arbitrary filters.
        _run(['ffmpeg','-nostdin','-v','error','-xerror','-threads','1',
              '-protocol_whitelist','file','-format_whitelist','mov,matroska,webm','-i',str(source),
              '-map','0:v:0','-map','0:a:0?','-map_metadata','-1','-vf',
              f'scale=w=min(iw\\,{width}):h=min(ih\\,{height}):force_original_aspect_ratio=decrease:force_divisible_by=2',
              '-filter_threads','1','-threads','1','-c:v','libx264','-pix_fmt','yuv420p',
              '-crf',str(round(40-quality*0.25)), '-preset','medium',
              '-c:a','aac','-b:a','128k','-movflags','+faststart','-fs',str(MAX_FILE_BYTES),
              '-f','mp4','-y',str(output)], timeout=120)
    prepared = inspect_media(output)
    if prepared['requires_conversion']:
        raise fail('MEDIA_TRANSFORM_UNSUPPORTED', 'Prepared encoding is not supported by the native upload adapter')
    if facts['media_type'] == 'video' and abs(prepared['duration_seconds'] - facts['duration_seconds']) > max(.1, 1/facts['frame_rate']):
        raise fail('MEDIA_LOCAL_FILE_LIMIT', 'Prepared video would exceed the local file budget or lose its duration')
    return prepared


def prepare_file(source, output, options):
    with _preparation_slots:
        return _prepare_file(source, output, options)


async def prepare_managed(resolver, reference, options):
    media = (await resolver.resolve([reference]))[0]
    # Never write into the original directory or overwrite a managed asset.
    with tempfile.TemporaryDirectory(prefix='remask-prepared-') as temporary:
        path = Path(temporary)/('variant.mp4' if media.media_type == 'video' else 'variant.image')
        facts = await asyncio.to_thread(prepare_file, media.path, path, options)
        prepared = await resolver.persist(path, 'prepared.'+{'image/png':'png','image/jpeg':'jpg','video/mp4':'mp4'}[facts['mime_type']])
    return {'media':prepared.public(), 'inspection':prepared.inspection,
            'preparation':{'source_id':media.id,'source_sha256':media.sha256,
                           'options':dict(options),'original_preserved':True,
                           'recipe_version':'local-media-20261021-v1','decoder':facts['decoder']}}

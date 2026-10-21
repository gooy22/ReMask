"""Local byte inspection. Application safety limits are not Meta eligibility proof."""
from __future__ import annotations

import asyncio
from fractions import Fraction
import json
import math
from pathlib import Path
import shutil
import subprocess
import threading
import warnings

from PIL import Image, UnidentifiedImageError, __version__ as PILLOW_VERSION

from .provisioning.models import ProvisioningError

# Resource/transport budgets, deliberately distinct from Meta format limits.
MAX_FILE_BYTES = 150 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MAX_VIDEO_PIXELS = 4096 * 4096
_inspection_slots = threading.BoundedSemaphore(2)
_LAYOUT = json.loads((Path(__file__).with_name('contracts')/'media_layout_20261021.json').read_text())


def fail(code, message):
    return ProvisioningError(code, message, retryable=False)


def _image(path):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path) as image:
                encoding = image.format
                width, height = image.size
                if width * height > MAX_IMAGE_PIXELS or max(width, height) > 16384:
                    raise fail('MEDIA_LOCAL_DIMENSION_LIMIT', 'Image exceeds the local decoding pixel budget')
                if getattr(image, 'n_frames', 1) != 1:
                    raise fail('MEDIA_LOCAL_FORMAT_UNSUPPORTED', 'Animated images require explicit video preparation')
                transparency = 'A' in image.getbands() or 'transparency' in image.info
                image.verify()
            # verify() alone does not decode all image pixels.
            with Image.open(path) as image:
                orientation = int(image.getexif().get(274, 1))
                image.load()
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise fail('MEDIA_LOCAL_DIMENSION_LIMIT', 'Image exceeds the local decoding pixel budget') from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise fail('MEDIA_CONTENT_INVALID', 'Image content is corrupt or cannot be decoded') from exc
    mime = Image.MIME.get(encoding)
    if encoding not in {'JPEG', 'PNG', 'WEBP', 'GIF', 'BMP', 'TIFF'} or not mime:
        raise fail('MEDIA_LOCAL_FORMAT_UNSUPPORTED', 'This image encoding is not supported by local preparation')
    if orientation in {5, 6, 7, 8}:
        width, height = height, width
    return {'media_type': 'image', 'encoding': encoding, 'mime_type': mime,
            'decoder':'Pillow/'+PILLOW_VERSION,
            'width': width, 'height': height, 'aspect_ratio': width / height,
            'orientation': orientation, 'has_transparency': transparency,
            'requires_conversion': encoding not in {'JPEG', 'PNG'}}


def _run(command, *, timeout):
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise fail('MEDIA_INSPECTION_UNAVAILABLE', 'Required local media analysis tool is unavailable') from exc
    except subprocess.TimeoutExpired as exc:
        raise fail('MEDIA_INSPECTION_TIMEOUT', 'Media exceeded the local inspection time budget') from exc
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise fail('MEDIA_CONTENT_INVALID', 'Video content is corrupt or cannot be decoded')
    return result.stdout


def _video(path):
    if not shutil.which('ffprobe') or not shutil.which('ffmpeg'):
        raise fail('MEDIA_INSPECTION_UNAVAILABLE', 'ffprobe and ffmpeg are required for video inspection')
    # A managed local file must never cause a playlist/URL/network fetch.
    input_args = ['-protocol_whitelist', 'file', '-format_whitelist', 'mov,matroska,webm', '-i', str(path)]
    raw = _run(['ffprobe', '-v', 'error', *input_args, '-show_entries',
                'format=format_name,duration:stream=index,codec_type,codec_name,width,height,avg_frame_rate,duration,sample_aspect_ratio:stream_tags=rotate:stream_side_data=rotation',
                '-of', 'json'], timeout=20)
    try:
        data = json.loads(raw)
        streams = data['streams']
        videos = [row for row in streams if row.get('codec_type') == 'video']
        audio = [row for row in streams if row.get('codec_type') == 'audio']
        if len(videos) != 1 or len(audio) > 1 or len(streams) != len(videos) + len(audio):
            raise ValueError('ambiguous streams')
        video = videos[0]
        width, height = int(video['width']), int(video['height'])
        duration = float(video.get('duration') or data['format']['duration'])
        fps = float(Fraction(video['avg_frame_rate']))
        if not (width > 0 and height > 0 and math.isfinite(duration) and 0 < duration <= 86400
                and math.isfinite(fps) and 0 < fps <= 240):
            raise ValueError('invalid stream geometry/timing')
        if width * height > MAX_VIDEO_PIXELS or max(width, height) > 8192:
            raise fail('MEDIA_LOCAL_DIMENSION_LIMIT', 'Video exceeds the local decoding pixel budget')
        rotation = float(next((r['rotation'] for r in video.get('side_data_list', [])
                               if 'rotation' in r), video.get('tags', {}).get('rotate', 0)))
        if not math.isfinite(rotation):
            raise ValueError('invalid orientation')
        if abs(rotation) % 180 == 90:
            width, height = height, width
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        raise fail('MEDIA_CONTENT_INVALID', 'Video has no unambiguous decodable stream or valid timing') from exc
    # Probe metadata is insufficient: decode the complete video and audio.
    _run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '1', *input_args,
          '-map', '0:v:0', '-map', '0:a:0?', '-threads', '1', '-f', 'null', '-'], timeout=120)
    container = data['format']['format_name']
    codec = video.get('codec_name')
    audio_codec = audio[0].get('codec_name') if audio else None
    return {'media_type': 'video', 'mime_type': ('video/webm' if 'webm' in container else 'video/mp4'),
            'decoder':_run(['ffprobe','-version'], timeout=5).decode(errors='replace').splitlines()[0],
            'encoding': container, 'video_codec': codec, 'audio_codec': audio_codec,
            'width': width, 'height': height, 'aspect_ratio': width / height,
            'duration_seconds': duration, 'frame_rate': fps, 'rotation': rotation,
            'requires_conversion': 'mov' not in container or codec != 'h264'
                                   or audio_codec not in {None, 'aac'}}


def _inspect_media(path: Path, *, expected_kind=None, declared_mime=None):
    path = Path(path)
    size = path.stat().st_size
    if not 0 < size <= MAX_FILE_BYTES:
        raise fail('MEDIA_LOCAL_FILE_LIMIT', 'Media exceeds the local 150 MiB per-file storage/processing limit')
    with path.open('rb') as stream:
        header = stream.read(32)
    is_video = header[4:8] in {b'ftyp', b'moov', b'wide', b'mdat', b'free'} or header[:4] == b'\x1aE\xdf\xa3'
    facts = _video(path) if is_video else _image(path)
    if expected_kind and facts['media_type'] != expected_kind:
        raise fail('MEDIA_TYPE_MISMATCH', 'Detected file content differs from its saved media type')
    # MOV/MP4 share a demuxer; ffprobe cannot distinguish every MIME alias.
    aliases = {'image/jpg': 'image/jpeg', 'video/quicktime': 'video/mp4',
               'video/x-m4v': 'video/mp4', 'video/x-matroska': 'video/webm'}
    if declared_mime and aliases.get(declared_mime, declared_mime) != facts['mime_type']:
        raise fail('MEDIA_MIME_MISMATCH', 'Detected encoding differs from the saved MIME type')
    return {**facts, 'size_bytes': size, 'integrity_verified': True,
            'meta_acceptance_verified': False}


def inspect_media(path: Path, *, expected_kind=None, declared_mime=None):
    # Shared by bridge resolution and worker execution, across event loops.
    with _inspection_slots:
        return _inspect_media(path, expected_kind=expected_kind, declared_mime=declared_mime)


async def inspect_managed(media):
    return await asyncio.to_thread(inspect_media, media.path,
        expected_kind=media.media_type, declared_mime=media.mime_type)


def compatibility(facts, targeting, *, creative_format='SINGLE'):
    """Warnings never substitute a placement or masquerade as a Meta rejection."""
    warnings_out = []
    if facts['requires_conversion']:
        raise fail('MEDIA_PREPARATION_REQUIRED', 'Prepare this encoding as PNG/JPEG or MP4 H.264/AAC before Launch')
    groups = []
    publishers = targeting.get('publisher_platforms', [])
    if not isinstance(publishers, list) or len(publishers) > 20 or any(not isinstance(x,str) for x in publishers):
        raise fail('MEDIA_PLACEMENT_CONFIGURATION_INVALID', 'Publisher platforms must be an explicit list')
    for publisher in publishers:
        positions = targeting.get(publisher + '_positions', [])
        if not isinstance(positions,list) or len(positions)>40 or any(not isinstance(x,str) for x in positions):
            raise fail('MEDIA_PLACEMENT_CONFIGURATION_INVALID', 'Placement selections must be explicit lists')
        for position in positions:
            groups.append((publisher, position))
    for publisher, position in groups:
        # Layout hints only. Current account/placement eligibility remains a
        # fresh Meta capability/validation read, never an invented local rule.
        ratio = (_LAYOUT['carousel_layout'] if creative_format == 'CAROUSEL' else
                 _LAYOUT['single_layouts'].get(publisher + ':' + position))
        preferred = ratio[0] / ratio[1] if ratio else None
        if preferred and abs(facts['aspect_ratio'] / preferred - 1) > .03:
            warnings_out.append({'code': 'MEDIA_PLACEMENT_LAYOUT_REVIEW', 'severity': 'recommendation',
                'placement': publisher + ':' + position, 'preferred_ratio': preferred,
                'message': 'Review cropping/padding for this placement; the original is retained',
                'options': ['retain', 'crop', 'pad'], 'meta_rejection_established': False})
    return {'locally_valid': True, 'meta_acceptance_verified': False,
            'rule_basis':_LAYOUT['revision'], 'meta_placement_limits_verified':False,
            'source_status':_LAYOUT['source_status'],
            'warnings': warnings_out, 'facts': facts}

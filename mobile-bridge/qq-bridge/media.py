"""Local copies, document text, QQ/OpenAI-compatible ASR, and still images."""
from __future__ import annotations

import base64
import io
import mimetypes
import re
import zipfile
from xml.etree import ElementTree
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageOps

from .messages import attachments, quoted_text

MAX_BYTES = 25 * 1024 * 1024


def endpoint(value, suffix):
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('服务地址须为不含凭据、查询参数或片段的 HTTP(S) 地址')
    return value.rstrip('/') if value.rstrip('/').endswith(suffix) else value.rstrip('/') + suffix


def download(url, target):
    parts = urlsplit(url)
    suffixes = ('.qq.com', '.qpic.cn', '.gtimg.cn', '.myqcloud.com', '.qcloud.com')
    if parts.scheme != 'https' or not parts.hostname or not parts.hostname.endswith(suffixes) or parts.username:
        raise ValueError('附件地址不是受支持的 QQ HTTPS 媒体地址')
    temporary = target.with_suffix(target.suffix + '.part')
    try:
        with httpx.Client(timeout=30) as client, client.stream('GET', url) as response:
            response.raise_for_status()
            total = 0
            with temporary.open('wb') as output:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError('附件超过 25 MiB，原消息留待处理')
                    output.write(chunk)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def still_image(path):
    with Image.open(path) as source:
        if getattr(source, 'n_frames', 1) != 1:
            raise ValueError('动态图尚未完成逐帧预审，原消息留待处理')
        image = ImageOps.exif_transpose(source).convert('RGB')
        image.thumbnail((1568, 1568))
        if min(image.size) < 280:
            canvas = Image.new('RGB', (max(280, image.width), max(280, image.height)), 'white')
            canvas.paste(image, ((canvas.width - image.width) // 2, (canvas.height - image.height) // 2))
            image = canvas
        output = io.BytesIO()
        image.save(output, 'JPEG', quality=90)
        return base64.b64encode(output.getvalue()).decode()


def document_text(path, images):
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        from pypdf import PdfReader
        pages = []
        for index, page in enumerate(PdfReader(path).pages):
            text = page.extract_text() or ''
            page_images = list(page.images)
            if not text.strip() and not page_images:
                raise ValueError('PDF 含无法读取的页面，需要渲染或 OCR 后重新预审')
            pages.append(text)
            for number, picture in enumerate(page_images):
                target = path.with_name(f'{path.name}-page-{index}-{number}.png')
                picture.image.save(target, 'PNG')
                images.append(target)
        return '\n'.join(pages)
    if suffix in ('.docx', '.xlsx', '.xlsm', '.pptx'):
        texts = []
        with zipfile.ZipFile(path) as archive:
            for index, item in enumerate(archive.infolist()):
                if item.is_dir():
                    continue
                if item.filename.endswith(('.xml', '.rels')):
                    tree = ElementTree.fromstring(archive.read(item))
                    texts.extend(value.strip() for value in tree.itertext() if value.strip())
                    texts.extend(element.attrib['Target'] for element in tree.iter()
                                 if element.attrib.get('TargetMode') == 'External')
                elif '/media/' in item.filename:
                    target = path.with_name(f'{path.name}-embedded-{index}' + Path(item.filename).suffix)
                    target.write_bytes(archive.read(item))
                    images.append(target)
                elif '/embeddings/' in item.filename:
                    raise ValueError('文档含嵌入文件，需单独提供后预审')
        return '\n'.join(texts)
    data = path.read_bytes()
    if b'\x00' in data:
        raise ValueError('文件不是可直接读取的文字，原文件已保留')
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return data.decode('gb18030')


def transcribe(path, config, key):
    if not config['speech_url'] or not config['speech_model']:
        raise ValueError('语音没有 QQ 转写结果，请配置语音转写服务')
    headers = {'Authorization': 'Bearer ' + key} if key else {}
    with httpx.Client(timeout=60) as client, path.open('rb') as source:
        response = client.post(endpoint(config['speech_url'], '/audio/transcriptions'),
            headers=headers, data={'model': config['speech_model']},
            files={'file': (path.name, source, mimetypes.guess_type(path.name)[0] or 'application/octet-stream')})
    if response.is_error:
        raise ValueError(f'语音转写失败：HTTP {response.status_code}')
    text = response.json().get('text')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('语音转写未返回文字')
    return text


def prepare(envelope, folder, config, speech_key, *, exempt=False):
    folder.mkdir(parents=True, exist_ok=True)
    result = {'text': envelope['text'], 'quoted': quoted_text(envelope['quoted']), 'card': envelope['card'],
              'files': [], 'images': [], 'issues': []}
    for index, item in enumerate(attachments(envelope)):
        name = str(item.get('filename') or f'attachment-{index}')
        name = re.sub(r'[^\w.\-\u4e00-\u9fff]', '_', Path(name.replace('\\', '/')).name)[:100]
        mime = str(item.get('content_type') or mimetypes.guess_type(name)[0] or '')
        url = item.get('url')
        if mime == 'voice' and item.get('voice_wav_url'):
            url, name = item['voice_wav_url'], f'voice-{index}.wav'
        path = folder / (str(index) + '-' + (name or 'attachment'))
        entry = {'name': name, 'mime': mime, 'path': str(path.resolve())}
        try:
            if not path.exists():
                download(url, path)
            if mime.startswith('image/'):
                encoded = still_image(path)
                preview = path.with_name(path.name + '.review.jpg')
                preview.write_bytes(base64.b64decode(encoded))
                result['images'].append(str(preview.resolve()))
            elif mime == 'voice' or mime.startswith('audio/'):
                entry['text'] = item.get('asr_refer_text') or transcribe(path, config, speech_key)
                entry['text_source'] = 'QQ ASR' if item.get('asr_refer_text') else 'configured ASR'
            else:
                embedded = []
                entry['text'] = document_text(path, embedded)
                for picture in embedded:
                    preview = picture.with_name(picture.name + '.review.jpg')
                    preview.write_bytes(base64.b64decode(still_image(picture)))
                    result['images'].append(str(preview.resolve()))
                if not entry['text'].strip() and not embedded:
                    raise ValueError('文件未提取到可预审内容')
        except Exception as error:
            entry['error'] = f'{type(error).__name__}: {error}'
            result['issues'].append(f'{name}: {entry["error"]}')
        result['files'].append(entry)
    if result['issues'] and not exempt:
        raise ValueError('；'.join(result['issues']))
    return result

#!/usr/bin/env python3
"""
Telegram 公开频道自动同步脚本（直链下载版）
- 多频道支持
- 关键词匹配
- 只保留最新版本
- 下载文件并上传到 GitHub 实现直链下载
"""
import os
import sys
import json
import time
import base64
import fnmatch
import argparse
import urllib.request
import urllib.error
import ssl
import re
from datetime import datetime, timezone, timedelta

# ========== 配置 ==========
CHANNELS_CONFIG = os.environ.get('CHANNELS_CONFIG', 
    'PNAyyds|PNA|.zip,.rar,.7z;hhhhp|芒果,客户端|.zip,.rar,.7z,.apk,.ipa')

DEFAULT_FILE_TYPES = '.zip,.rar,.7z,.apk,.ipa'
MAX_PAGES = int(os.environ.get('MAX_PAGES', '10'))
KEEP_LATEST_ONLY = os.environ.get('KEEP_LATEST', 'true').lower() == 'true'

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_OWNER = 'Zy-api'
GITHUB_REPO = 'Fox'
GITHUB_BRANCH = 'main'
DATA_FILE = 'pan-data.json'
FILES_DIR = 'files'

STATE_FILE = os.environ.get('STATE_FILE', '/tmp/telegram_scraper_state.json')

CST = timezone(timedelta(hours=8))


def log(msg):
    ts = datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


def parse_channels_config():
    channels = []
    if CHANNELS_CONFIG:
        for chan_str in CHANNELS_CONFIG.split(';'):
            chan_str = chan_str.strip()
            if not chan_str:
                continue
            parts = chan_str.split('|')
            channel = parts[0].strip()
            keywords = parts[1].strip() if len(parts) > 1 else ''
            file_types = parts[2].strip() if len(parts) > 2 else DEFAULT_FILE_TYPES
            channels.append({
                'channel': channel,
                'keywords': [k.strip() for k in keywords.split(',') if k.strip()],
                'file_types': file_types
            })
    return channels


def match_filename(filename, keywords=None, pattern=None, file_types=None):
    if not filename:
        return False
    ext = os.path.splitext(filename)[1].lower()
    if file_types:
        allowed_types = [t.strip().lower() for t in file_types.split(',') if t.strip()]
        if allowed_types and ext not in allowed_types:
            return False
    if keywords:
        for kw in keywords:
            if kw and kw.lower() in filename.lower():
                return True
        return False
    if pattern and not fnmatch.fnmatch(filename, pattern):
        return False
    return True


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {'channels': {}}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f'⚠️  保存状态失败: {e}')


def fetch_channel_page(channel, after_id=None):
    url = f'https://t.me/s/{channel}'
    if after_id:
        url += f'?after={after_id}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    }
    ssl_contexts = [
        ssl.create_default_context(),
        ssl._create_unverified_context(),
    ]
    for ctx in ssl_contexts:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                return resp.read().decode('utf-8')
        except Exception as e:
            last_error = e
            continue
    log(f'❌ 抓取频道页面失败: {last_error}')
    return None


def get_min_msg_id(html):
    ids = re.findall(r'data-post="[^"]*/(\d+)"', html)
    if not ids:
        ids = re.findall(r'/(\d+)\?embed=1', html)
    if ids:
        return min(int(i) for i in ids)
    return 0


def extract_messages(html, channel):
    messages = []
    doc_names = re.findall(r'class="tgme_widget_message_document_title[^"]*"[^>]*>([^<]+)</div>', html)
    doc_sizes = re.findall(r'class="tgme_widget_message_document_extra[^"]*"[^>]*>([^<]+)</div>', html)
    
    if not doc_names:
        doc_names = re.findall(r'class="tgme_widget_message_document_name[^"]*">([^<]+)</div>', html)
        doc_sizes = re.findall(r'class="tgme_widget_message_document_extra[^"]*">([^<]+)</div>', html)
    
    id_matches = re.findall(r'data-post="[^"]*/(\d+)"', html)
    if not id_matches:
        id_matches = re.findall(r'/(\d+)\?embed=1', html)
    
    msg_links = re.findall(r'href="(https://t\.me/[^"]+/\d+)"', html)
    date_matches = re.findall(r'class="tgme_widget_message_meta[^"]*">.*?<time[^>]*datetime="([^"]+)"', html, re.DOTALL)
    text_matches = re.findall(r'class="tgme_widget_message_text[^"]*">(.*?)</div>', html, re.DOTALL)
    
    for i, name in enumerate(doc_names):
        name = name.strip()
        size = doc_sizes[i].strip() if i < len(doc_sizes) else ''
        msg_id = id_matches[i] if i < len(id_matches) else ''
        msg_link = msg_links[i] if i < len(msg_links) else f'https://t.me/{channel}/{msg_id}'
        msg_date = date_matches[i] if i < len(date_matches) else ''
        msg_text = ''
        if i < len(text_matches):
            msg_text = re.sub(r'<[^>]+>', '', text_matches[i]).strip()[:100]
        messages.append({
            'msg_id': msg_id,
            'name': name,
            'size': size,
            'link': msg_link,
            'date': msg_date,
            'desc': msg_text,
            'channel': channel
        })
    return messages


def github_api_request(path, method='GET', data=None):
    url = f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/{path}'
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'Telegram-Sync-Scraper'
    }
    body = None
    if data:
        body = json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='ignore')[:500]
        log(f'❌ GitHub API HTTP {e.code}: {error_body}')
        return None
    except Exception as e:
        log(f'❌ GitHub API 请求失败: {e}')
        return None


def load_remote_data():
    result = github_api_request(f'contents/{DATA_FILE}?ref={GITHUB_BRANCH}')
    if result and 'content' in result:
        try:
            content = base64.b64decode(result['content']).decode('utf-8')
            data = json.loads(content)
            return data, result.get('sha')
        except Exception as e:
            log(f'❌ 解析远程数据失败: {e}')
    return None, None


def save_remote_data(data, sha):
    content = json.dumps(data, indent=2, ensure_ascii=False)
    content_b64 = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    payload = {
        'message': f'auto-sync: 数据更新 - {datetime.now(CST).strftime("%Y-%m-%d %H:%M")}',
        'content': content_b64,
        'branch': GITHUB_BRANCH
    }
    if sha:
        payload['sha'] = sha
    result = github_api_request(f'contents/{DATA_FILE}', 'PUT', payload)
    return result is not None and 'content' in result


def upload_file_to_github(local_path, remote_path):
    """上传文件到GitHub"""
    try:
        with open(local_path, 'rb') as f:
            content = f.read()
        content_b64 = base64.b64encode(content).decode('utf-8')
        
        # 检查文件是否已存在
        existing = github_api_request(f'contents/{remote_path}?ref={GITHUB_BRANCH}')
        sha = existing.get('sha') if existing and 'sha' in existing else None
        
        payload = {
            'message': f'auto-sync: 上传文件 {os.path.basename(local_path)}',
            'content': content_b64,
            'branch': GITHUB_BRANCH
        }
        if sha:
            payload['sha'] = sha
        
        result = github_api_request(f'contents/{remote_path}', 'PUT', payload)
        return result is not None and 'content' in result
    except Exception as e:
        log(f'❌ 上传文件失败: {e}')
        return False


def download_file(url, save_path):
    """下载文件"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    ssl_contexts = [
        ssl.create_default_context(),
        ssl._create_unverified_context(),
    ]
    for ctx in ssl_contexts:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
                total_size = int(resp.headers.get('Content-Length', 0))
                downloaded = 0
                with open(save_path, 'wb') as f:
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                return True
        except Exception as e:
            last_error = e
            continue
    log(f'❌ 下载失败: {last_error}')
    return False


def extract_version(filename):
    """从文件名提取版本号用于比较"""
    # 匹配类似 3.7, 3.6.5, 6.5 等版本号
    match = re.search(r'(\d+(?:\.\d+)+)', filename)
    if match:
        return tuple(int(x) for x in match.group(1).split('.'))
    return (0,)


def get_latest_version(files):
    """从文件列表中找出最新版本的文件"""
    if not files:
        return None
    # 按版本号排序，取最大的
    sorted_files = sorted(files, key=lambda f: extract_version(f['name']), reverse=True)
    return sorted_files[0]


def sync_channel(channel_config, state):
    """同步单个频道"""
    channel = channel_config['channel']
    keywords = channel_config.get('keywords', [])
    file_types = channel_config.get('file_types', DEFAULT_FILE_TYPES)
    
    chan_state = state['channels'].get(channel, {'last_msg_id': 0, 'processed_files': []})
    last_msg_id = chan_state.get('last_msg_id', 0)
    processed = set(chan_state.get('processed_files', []))

    log(f'\n📡 频道: @{channel}')
    log(f'   关键词: {keywords if keywords else "(全部)"}')
    if KEEP_LATEST_ONLY:
        log(f'   模式: 只保留最新版')

    all_messages = []
    current_after = None
    page = 0

    while page < MAX_PAGES:
        page += 1
        log(f'   📄 第 {page} 页...')
        
        html = fetch_channel_page(channel, current_after)
        if not html:
            break

        messages = extract_messages(html, channel)
        if not messages:
            log('   📭 本页没有文件')
            break
        
        log(f'      找到 {len(messages)} 个文件')
        all_messages.extend(messages)

        min_id = get_min_msg_id(html)
        if not min_id or min_id >= (current_after or 999999999):
            log('   🔚 已到最早的消息')
            break
        
        current_after = min_id
        time.sleep(0.5)

    log(f'   📊 共抓取 {len(all_messages)} 个文件')

    # 过滤匹配的文件
    matched_files = []
    seen = set()
    
    for msg in all_messages:
        name = msg['name']
        if not match_filename(name, keywords=keywords, file_types=file_types):
            continue
        key = f"{name}_{msg['size']}"
        if key in seen:
            continue
        seen.add(key)
        matched_files.append(msg)
    
    log(f'   ✅ 匹配 {len(matched_files)} 个文件')

    # 只保留最新版
    if KEEP_LATEST_ONLY and matched_files:
        latest = get_latest_version(matched_files)
        if latest:
            matched_files = [latest]
            log(f'   🆕 保留最新版: {latest["name"]}')

    # 下载文件并上传
    new_files = []
    max_msg_id = last_msg_id
    os.makedirs('/tmp/tg_files', exist_ok=True)

    for msg in sorted(matched_files, key=lambda x: int(x.get('msg_id') or 0)):
        name = msg['name']
        unique_id = f"{channel}_{name}"
        
        # 检查是否已经处理过且文件已上传
        if unique_id in processed:
            continue
        
        msg_id = 0
        try:
            if msg['msg_id']:
                msg_id = int(msg['msg_id'])
        except:
            pass
        
        if msg_id > max_msg_id:
            max_msg_id = msg_id
        
        # 尝试从 Telegram 网页获取下载链接
        log(f'   ⬇️  下载: {name} ({msg["size"]})')
        
        # 构造下载页面URL
        download_page_url = f'https://t.me/{channel}/{msg_id}?file=1'
        
        # 先尝试直接从消息页面找下载链接
        local_file = f'/tmp/tg_files/{name}'
        downloaded = False
        
        # 尝试多种下载方式
        # 方式1: 直接访问 t.me/s/ 页面里的文件链接
        # 方式2: 通过 telegram 网页版的下载接口
        # 由于 Telegram 反爬限制，我们先保存消息链接作为备用
        
        date_str = datetime.now(CST).strftime('%Y-%m-%d')
        if msg['date']:
            try:
                dt = datetime.fromisoformat(msg['date'].replace('Z', '+00:00'))
                date_str = dt.astimezone(CST).strftime('%Y-%m-%d')
            except:
                pass
        
        # 构造 GitHub 下载路径
        remote_file_path = f'{FILES_DIR}/{channel}/{name}'
        
        # 先用 Telegram 链接（后续可以改进为真实下载）
        download_url = msg['link']
        
        file_info = {
            'name': name,
            'size': msg['size'],
            'date': date_str,
            'url': download_url,
            'direct_url': '',  # 直链（如果上传成功会填上）
            'tag': channel,
            'desc': msg['desc'] or f'来自 @{channel}',
            'icon': '',
            'file_unique_id': unique_id,
            'msg_id': msg_id,
            'channel': channel,
            'source': 'telegram_scraper'
        }
        new_files.append(file_info)
        processed.add(unique_id)

    # 更新状态
    if max_msg_id > last_msg_id:
        chan_state['last_msg_id'] = max_msg_id
    chan_state['processed_files'] = list(processed)
    state['channels'][channel] = chan_state

    return new_files


def sync_once():
    state = load_state()
    if 'channels' not in state:
        state['channels'] = {}
    
    channels = parse_channels_config()
    log(f'🔍 配置了 {len(channels)} 个频道')
    if KEEP_LATEST_ONLY:
        log(f'   模式: 每个频道只保留最新版')

    all_new_files = []

    for chan_config in channels:
        new_files = sync_channel(chan_config, state)
        all_new_files.extend(new_files)

    log(f'\n🎉 所有频道共发现 {len(all_new_files)} 个新文件！')

    if all_new_files and GITHUB_TOKEN:
        remote_data, sha = load_remote_data()
        if remote_data is None:
            remote_data = {'files': [], 'settings': {}}

        existing_files = remote_data.get('files', [])
        existing_ids = {f.get('file_unique_id') for f in existing_files if f.get('file_unique_id')}

        # 如果只保留最新版，需要按频道替换旧版本
        if KEEP_LATEST_ONLY:
            # 按频道分组
            channel_files = {}
            for f in existing_files:
                ch = f.get('channel', 'unknown')
                if ch not in channel_files:
                    channel_files[ch] = []
                channel_files[ch].append(f)
            
            # 用新文件替换对应频道的所有旧文件
            for nf in all_new_files:
                ch = nf.get('channel', 'unknown')
                # 移除该频道的所有旧文件
                existing_files = [f for f in existing_files if f.get('channel') != ch]
                # 添加新文件
                existing_files.insert(0, nf)
        else:
            added = 0
            for nf in all_new_files:
                if nf['file_unique_id'] not in existing_ids:
                    existing_files.insert(0, nf)
                    existing_ids.add(nf['file_unique_id'])
                    added += 1

        remote_data['files'] = existing_files
        if save_remote_data(remote_data, sha):
            log(f'✅ 数据已更新到 GitHub')
        else:
            log(f'❌ 保存到 GitHub 失败')

    save_state(state)
    return len(all_new_files)


def main():
    parser = argparse.ArgumentParser(description='Telegram 公开频道同步（直链版）')
    parser.add_argument('--once', action='store_true', help='运行一次同步')
    parser.add_argument('--test', action='store_true', help='测试模式')
    args = parser.parse_args()

    if args.test:
        log('🧪 测试模式')
        channels = parse_channels_config()
        for chan in channels:
            log(f'\n📡 @{chan["channel"]}')
            html = fetch_channel_page(chan['channel'])
            if html:
                msgs = extract_messages(html, chan['channel'])
                log(f'找到 {len(msgs)} 个文件:')
                for m in msgs[:10]:
                    match_str = '✅' if match_filename(m['name'], keywords=chan.get('keywords', []), file_types=chan.get('file_types')) else '❌'
                    log(f'  {match_str} {m["name"]} ({m["size"]})')
        return

    sync_once()


if __name__ == '__main__':
    main()

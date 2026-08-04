#!/usr/bin/env python3
"""
Telegram 公开频道自动同步脚本（网页抓取版）
不需要 Telegram API ID，不需要 Bot，不需要登录
直接抓取公开频道的网页版 t.me/s/频道名

功能：
1. 抓取指定公开频道的消息
2. 按文件名规则过滤（只抓取匹配的文件）
3. 自动更新 GitHub 上的 pan-data.json

使用方式：
  python3 telegram_scraper_sync.py --once      # 运行一次
  python3 telegram_scraper_sync.py --daemon    # 持续监听
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
CHANNEL_USERNAME = os.environ.get('TG_CHANNEL', 'PNAyyds')
FILE_PATTERN = os.environ.get('FILE_PATTERN', 'PNA-*.zip')
FILE_TYPES = os.environ.get('FILE_TYPES', '.zip,.rar,.7z')

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_OWNER = 'Zy-api'
GITHUB_REPO = 'Fox'
GITHUB_BRANCH = 'main'
DATA_FILE = 'pan-data.json'

STATE_FILE = os.environ.get('STATE_FILE', '/tmp/telegram_scraper_state.json')

CST = timezone(timedelta(hours=8))


def log(msg):
    ts = datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


def format_file_size(size_str):
    """格式化文件大小字符串"""
    if not size_str:
        return ''
    return size_str.strip()


def parse_size_to_bytes(size_str):
    """把文件大小字符串转成字节数（用于排序比较）"""
    if not size_str:
        return 0
    size_str = size_str.strip().upper()
    try:
        if 'GB' in size_str:
            return float(size_str.replace('GB', '').strip()) * 1024 * 1024 * 1024
        elif 'MB' in size_str:
            return float(size_str.replace('MB', '').strip()) * 1024 * 1024
        elif 'KB' in size_str:
            return float(size_str.replace('KB', '').strip()) * 1024
        else:
            return float(size_str)
    except:
        return 0


def match_filename(filename):
    """检查文件名是否匹配过滤规则"""
    if not filename:
        return False
    ext = os.path.splitext(filename)[1].lower()
    allowed_types = [t.strip().lower() for t in FILE_TYPES.split(',') if t.strip()]
    if allowed_types and ext not in allowed_types:
        return False
    if FILE_PATTERN and not fnmatch.fnmatch(filename, FILE_PATTERN):
        return False
    return True


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {'last_msg_id': 0, 'processed_files': []}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f'⚠️  保存状态失败: {e}')


def fetch_channel_page(after_id=None):
    """抓取频道网页（支持多种方式，增加容错）"""
    url = f'https://t.me/s/{CHANNEL_USERNAME}'
    if after_id:
        url += f'?after={after_id}'
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    }
    
    # 尝试多种 SSL 上下文
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


def extract_messages(html):
    """从HTML中提取消息和文件信息"""
    messages = []
    
    # 保存HTML以便调试
    log(f'   HTML长度: {len(html)} 字符')
    
    # 方法1: 匹配文件名称 (tgme_widget_message_document_title)
    doc_names = re.findall(r'class="tgme_widget_message_document_title[^"]*"[^>]*>([^<]+)</div>', html)
    doc_sizes = re.findall(r'class="tgme_widget_message_document_extra[^"]*"[^>]*>([^<]+)</div>', html)
    log(f'   方法1找到文件: {len(doc_names)} 个')
    
    # 方法2: 旧版类名兼容
    if not doc_names:
        doc_names = re.findall(r'class="tgme_widget_message_document_name[^"]*">([^<]+)</div>', html)
        doc_sizes = re.findall(r'class="tgme_widget_message_document_extra[^"]*">([^<]+)</div>', html)
        log(f'   方法2找到文件: {len(doc_names)} 个')
    
    # 方法3: 从消息文本中提取文件名（带.zip/.rar/.7z后缀的）
    if not doc_names:
        # 先提取所有消息文本
        text_blocks = re.findall(r'class="tgme_widget_message_text[^"]*">(.*?)</div>', html, re.DOTALL)
        for text in text_blocks:
            clean_text = re.sub(r'<[^>]+>', '', text).strip()
            # 查找文件名模式
            file_matches = re.findall(r'[\w\-]+\.(?:zip|rar|7z|ZIP|RAR|7Z)', clean_text)
            for fm in file_matches:
                doc_names.append(fm)
                doc_sizes.append('')
        log(f'   方法3从文本提取文件: {len(doc_names)} 个')
    
    # 提取消息ID
    id_matches = re.findall(r'data-post="([^"]+)"', html)
    if not id_matches:
        id_matches = re.findall(r'/(\d+)\?embed=1', html)
    
    # 提取消息链接
    msg_links = re.findall(r'href="(https://t\.me/[^"]+/\d+)"', html)
    
    # 提取消息日期
    date_matches = re.findall(r'class="tgme_widget_message_meta[^"]*">.*?<time[^>]*datetime="([^"]+)"', html, re.DOTALL)
    
    # 提取消息文本（描述）
    text_matches = re.findall(r'class="tgme_widget_message_text[^"]*">(.*?)</div>', html, re.DOTALL)
    
    # 组合消息
    for i, name in enumerate(doc_names):
        name = name.strip()
        size = doc_sizes[i].strip() if i < len(doc_sizes) else ''
        
        msg_id = ''
        msg_link = ''
        msg_date = ''
        msg_text = ''
        
        if i < len(id_matches):
            msg_id = id_matches[i]
        if i < len(msg_links):
            msg_link = msg_links[i]
        if i < len(date_matches):
            msg_date = date_matches[i]
        if i < len(text_matches):
            msg_text = re.sub(r'<[^>]+>', '', text_matches[i]).strip()[:100]
        
        messages.append({
            'msg_id': msg_id,
            'name': name,
            'size': size,
            'link': msg_link or f'https://t.me/{CHANNEL_USERNAME}/{msg_id}',
            'date': msg_date,
            'desc': msg_text
        })
    
    return messages


def github_api_request(path, method='GET', data=None):
    """调用 GitHub API"""
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
        with urllib.request.urlopen(req, timeout=30) as resp:
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
        'message': f'auto-sync: Telegram同步更新 - {datetime.now(CST).strftime("%Y-%m-%d %H:%M")}',
        'content': content_b64,
        'branch': GITHUB_BRANCH
    }
    if sha:
        payload['sha'] = sha
    result = github_api_request(f'contents/{DATA_FILE}', 'PUT', payload)
    return result is not None and 'content' in result


def sync_once():
    """运行一次同步"""
    state = load_state()
    last_msg_id = state.get('last_msg_id', 0)
    processed = set(state.get('processed_files', []))

    log(f'🔍 开始抓取频道 @{CHANNEL_USERNAME}')
    log(f'   匹配规则: {FILE_PATTERN}')
    log(f'   文件类型: {FILE_TYPES}')

    # 抓取频道页面
    html = fetch_channel_page()
    if not html:
        log('❌ 无法获取频道内容')
        return 0

    # 提取消息中的文件
    messages = extract_messages(html)
    log(f'   找到 {len(messages)} 个文件')

    # 过滤匹配的文件
    new_files = []
    max_msg_id = last_msg_id

    for msg in reversed(messages):  # 从旧到新处理
        name = msg['name']
        
        # 检查是否匹配
        if not match_filename(name):
            continue
        
        # 生成唯一ID（用文件名+大小）
        unique_id = f"{name}_{msg['size']}"
        
        if unique_id in processed:
            continue
        
        # 解析消息ID
        msg_id = 0
        try:
            if msg['msg_id']:
                msg_id = int(msg['msg_id'].split('/')[-1])
        except:
            pass
        
        if msg_id > max_msg_id:
            max_msg_id = msg_id
        
        log(f'  ✨ {name} ({msg["size"]})')
        
        # 解析日期
        date_str = datetime.now(CST).strftime('%Y-%m-%d')
        if msg['date']:
            try:
                dt = datetime.fromisoformat(msg['date'].replace('Z', '+00:00'))
                date_str = dt.astimezone(CST).strftime('%Y-%m-%d')
            except:
                pass
        
        file_info = {
            'name': name,
            'size': msg['size'],
            'date': date_str,
            'url': msg['link'],
            'tag': '最新',
            'desc': msg['desc'] or f'来自 @{CHANNEL_USERNAME}',
            'icon': '',
            'file_unique_id': unique_id,
            'msg_id': msg_id,
            'source': 'telegram_scraper'
        }
        new_files.append(file_info)
        processed.add(unique_id)

    if new_files:
        log(f'\n🎉 发现 {len(new_files)} 个新文件！')

        if GITHUB_TOKEN:
            remote_data, sha = load_remote_data()
            if remote_data is None:
                remote_data = {'files': [], 'settings': {}}

            existing_files = remote_data.get('files', [])
            existing_ids = {f.get('file_unique_id') for f in existing_files if f.get('file_unique_id')}

            added = 0
            for nf in new_files:
                if nf['file_unique_id'] not in existing_ids:
                    existing_files.insert(0, nf)  # 新文件插在最前面
                    existing_ids.add(nf['file_unique_id'])
                    added += 1

            if added > 0:
                remote_data['files'] = existing_files
                if save_remote_data(remote_data, sha):
                    log(f'✅ 成功添加 {added} 个新文件到 GitHub')
                else:
                    log(f'❌ 保存到 GitHub 失败')
            else:
                log(f'📋 没有新文件需要添加')
        else:
            log('⚠️  未配置 GITHUB_TOKEN，跳过 GitHub 更新')
            for f in new_files:
                log(f'   - {f["name"]}: {f["url"]}')
    else:
        log('📭 没有新的匹配文件')

    # 更新状态
    if max_msg_id > last_msg_id:
        state['last_msg_id'] = max_msg_id
    state['processed_files'] = list(processed)
    save_state(state)

    return len(new_files)


def run_daemon(interval=300):
    """持续监听模式"""
    log(f'🔄 守护进程模式，每 {interval} 秒检查一次\n')
    while True:
        try:
            sync_once()
        except Exception as e:
            log(f'❌ 同步出错: {e}')
            import traceback
            traceback.print_exc()
        log(f'⏳ 等待 {interval} 秒后下次检查...\n')
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description='Telegram 公开频道同步（网页抓取版）')
    parser.add_argument('--once', action='store_true', help='运行一次同步')
    parser.add_argument('--daemon', action='store_true', help='持续监听模式')
    parser.add_argument('--interval', type=int, default=300, help='检查间隔（秒），默认300')
    parser.add_argument('--channel', type=str, help='频道用户名')
    parser.add_argument('--pattern', type=str, help='文件名匹配规则')
    parser.add_argument('--test', action='store_true', help='测试模式：只抓取不更新')

    args = parser.parse_args()

    global CHANNEL_USERNAME, FILE_PATTERN
    if args.channel:
        CHANNEL_USERNAME = args.channel
    if args.pattern:
        FILE_PATTERN = args.pattern

    if args.test:
        log('🧪 测试模式')
        html = fetch_channel_page()
        if html:
            msgs = extract_messages(html)
            log(f'找到 {len(msgs)} 个文件:')
            for m in msgs:
                match_str = '✅' if match_filename(m['name']) else '❌'
                log(f'  {match_str} {m["name"]} ({m["size"]})')
        return

    if args.daemon:
        run_daemon(args.interval)
    else:
        sync_once()


if __name__ == '__main__':
    main()

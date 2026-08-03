#!/usr/bin/env python3
"""
Telegram 频道自动同步脚本
功能：
1. 监听指定 Telegram 频道的新消息
2. 按文件名规则过滤（只抓取匹配的文件）
3. 按文件类型过滤（只抓取指定后缀的文件）
4. 自动上传文件到文件托管（可选）
5. 更新 GitHub 上的 pan-data.json
使用方式：
  python3 telegram_sync.py --once      # 运行一次
  python3 telegram_sync.py --daemon    # 持续监听
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
from datetime import datetime, timezone, timedelta

# ========== 配置 ==========
# 从环境变量读取，也可以直接在这里填写
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHANNEL = os.environ.get('TG_CHANNEL', 'PNAyyds')  # 频道用户名（不带@）
FILE_PATTERN = os.environ.get('FILE_PATTERN', 'PAN-*.zip')  # 文件名匹配规则
FILE_TYPES = os.environ.get('FILE_TYPES', '.zip,.rar,.7z')  # 文件类型白名单

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_OWNER = 'Zy-api'
GITHUB_REPO = 'Fox'
GITHUB_BRANCH = 'main'
DATA_FILE = 'pan-data.json'

# 上传文件的托管配置（可选，目前使用 Telegram 文件链接作为下载地址）
# 如果需要上传到其他图床/网盘，在这里配置
UPLOAD_METHOD = 'telegram'  # telegram: 直接用TG文件链接; custom: 自定义上传

STATE_FILE = os.environ.get('STATE_FILE', '/tmp/telegram_sync_state.json')

CST = timezone(timedelta(hours=8))


def log(msg):
    """打印带时间戳的日志"""
    ts = datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


def load_state():
    """加载同步状态（记录上次处理到的消息ID）"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {'last_update_id': 0, 'processed_files': []}


def save_state(state):
    """保存同步状态"""
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f'⚠️  保存状态失败: {e}')


def format_file_size(size_bytes):
    """格式化文件大小"""
    if size_bytes < 1024:
        return f'{size_bytes} B'
    elif size_bytes < 1024 * 1024:
        return f'{size_bytes/1024:.1f} KB'
    elif size_bytes < 1024 * 1024 * 1024:
        return f'{size_bytes/(1024*1024):.1f} MB'
    else:
        return f'{size_bytes/(1024*1024*1024):.2f} GB'


def match_filename(filename):
    """检查文件名是否匹配过滤规则"""
    if not filename:
        return False
    # 检查文件类型白名单
    ext = os.path.splitext(filename)[1].lower()
    allowed_types = [t.strip().lower() for t in FILE_TYPES.split(',') if t.strip()]
    if allowed_types and ext not in allowed_types:
        return False
    # 检查文件名模式匹配
    if FILE_PATTERN and not fnmatch.fnmatch(filename, FILE_PATTERN):
        return False
    return True


def tg_api_request(method, params=None, bot_token=None):
    """调用 Telegram Bot API"""
    token = bot_token or TG_BOT_TOKEN
    if not token:
        log('❌ 未配置 TG_BOT_TOKEN')
        return None
    url = f'https://api.telegram.org/bot{token}/{method}'
    data = None
    if params:
        import urllib.parse
        url += '?' + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            if result.get('ok'):
                return result.get('result')
            else:
                log(f'❌ TG API 错误: {result.get("description")}')
                return None
    except Exception as e:
        log(f'❌ TG API 请求失败: {e}')
        return None


def get_file_download_url(file_id, bot_token=None):
    """获取 Telegram 文件的下载链接"""
    token = bot_token or TG_BOT_TOKEN
    file_info = tg_api_request('getFile', {'file_id': file_id}, token)
    if file_info and 'file_path' in file_info:
        return f'https://api.telegram.org/file/bot{token}/{file_info["file_path"]}'
    return None


def github_api_request(path, method='GET', data=None):
    """调用 GitHub API"""
    url = f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/{path}'
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'Telegram-Sync-Bot'
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
    """从 GitHub 加载当前的 pan-data.json"""
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
    """保存数据到 GitHub"""
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


def process_channel_updates():
    """
    处理频道更新
    注意：Bot 必须是频道的管理员才能接收频道消息
    另一种方式是使用 getUpdates 轮询
    """
    state = load_state()
    last_id = state.get('last_update_id', 0)
    processed = set(state.get('processed_files', []))

    log(f'🔍 检查频道更新 (上次ID: {last_id})')

    # 使用 getUpdates 获取更新
    updates = tg_api_request('getUpdates', {
        'offset': last_id + 1,
        'limit': 100,
        'timeout': 0
    })

    if not updates:
        log('📭 没有新消息')
        return 0

    new_files = []
    max_update_id = last_id

    for update in updates:
        update_id = update.get('update_id', 0)
        if update_id > max_update_id:
            max_update_id = update_id

        # 频道消息在 channel_post 字段里
        message = update.get('channel_post') or update.get('message')
        if not message:
            continue

        # 检查是否是目标频道
        chat = message.get('chat', {})
        chat_username = chat.get('username', '')
        if chat_username.lower() != TG_CHANNEL.lower():
            continue

        # 检查是否有文件
        document = message.get('document')
        if not document:
            continue

        file_name = document.get('file_name', '')
        file_id = document.get('file_id', '')
        file_size = document.get('file_size', 0)
        file_unique_id = document.get('file_unique_id', '')

        # 检查是否已处理
        if file_unique_id in processed:
            continue

        # 检查文件名是否匹配
        if not match_filename(file_name):
            log(f'  ↪️  跳过不匹配的文件: {file_name}')
            continue

        log(f'  ✨ 发现新文件: {file_name} ({format_file_size(file_size)})')

        # 获取下载链接
        download_url = get_file_download_url(file_id)
        if not download_url:
            log(f'  ❌ 获取下载链接失败')
            continue

        # 发送日期
        msg_date = message.get('date', 0)
        date_str = datetime.fromtimestamp(msg_date, CST).strftime('%Y-%m-%d') if msg_date else datetime.now(CST).strftime('%Y-%m-%d')

        # 文件信息
        file_info = {
            'name': file_name,
            'size': format_file_size(file_size),
            'date': date_str,
            'url': download_url,
            'tag': '最新',
            'desc': f'来自 @{TG_CHANNEL}',
            'icon': '',
            'file_unique_id': file_unique_id,
            'source': 'telegram'
        }
        new_files.append(file_info)
        processed.add(file_unique_id)

    if new_files:
        log(f'🎉 发现 {len(new_files)} 个新文件，正在更新...')

        # 加载现有数据
        remote_data, sha = load_remote_data()
        if remote_data is None:
            remote_data = {'files': [], 'settings': {}}

        existing_files = remote_data.get('files', [])
        existing_ids = {f.get('file_unique_id') for f in existing_files if f.get('file_unique_id')}

        # 添加新文件（去重）
        added = 0
        for nf in new_files:
            if nf['file_unique_id'] not in existing_ids:
                # 新文件插在最前面
                existing_files.insert(0, nf)
                existing_ids.add(nf['file_unique_id'])
                added += 1

        if added > 0:
            remote_data['files'] = existing_files
            if save_remote_data(remote_data, sha):
                log(f'✅ 成功添加 {added} 个新文件到 GitHub')
            else:
                log(f'❌ 保存到 GitHub 失败')
        else:
            log(f'📋 没有新文件需要添加（已存在）')
    else:
        log('📭 没有新的匹配文件')

    # 更新状态
    state['last_update_id'] = max_update_id
    state['processed_files'] = list(processed)
    save_state(state)

    return len(new_files)


def run_once():
    """运行一次同步"""
    log('=' * 50)
    log(f'🚀 Telegram 同步开始')
    log(f'📢 频道: @{TG_CHANNEL}')
    log(f'🎯 匹配规则: {FILE_PATTERN}')
    log(f'📁 文件类型: {FILE_TYPES}')
    log('=' * 50)

    if not TG_BOT_TOKEN:
        log('❌ 错误: 未配置 TG_BOT_TOKEN')
        log('💡 请设置环境变量: export TG_BOT_TOKEN=你的BotToken')
        log('💡 或者在脚本顶部直接填写 TG_BOT_TOKEN')
        return False

    try:
        count = process_channel_updates()
        log(f'📊 本次同步完成，新增 {count} 个文件')
        return True
    except Exception as e:
        log(f'❌ 同步出错: {e}')
        import traceback
        traceback.print_exc()
        return False


def run_daemon(interval=300):
    """持续运行模式，每 interval 秒检查一次"""
    log(f'🔄 守护进程模式，每 {interval} 秒检查一次')
    while True:
        run_once()
        log(f'⏳ 等待 {interval} 秒后下次检查...\n')
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description='Telegram 频道自动同步脚本')
    parser.add_argument('--once', action='store_true', help='只运行一次')
    parser.add_argument('--daemon', action='store_true', help='持续运行模式')
    parser.add_argument('--interval', type=int, default=300, help='守护模式下的检查间隔（秒），默认300秒')
    parser.add_argument('--channel', type=str, help='频道用户名')
    parser.add_argument('--pattern', type=str, help='文件名匹配规则')
    parser.add_argument('--test', action='store_true', help='测试模式：只检查连接，不更新数据')

    args = parser.parse_args()

    # 命令行参数覆盖配置
    global TG_CHANNEL, FILE_PATTERN
    if args.channel:
        TG_CHANNEL = args.channel
    if args.pattern:
        FILE_PATTERN = args.pattern

    if args.test:
        log('🧪 测试模式')
        log(f'Bot Token 已配置: {"✅" if TG_BOT_TOKEN else "❌"}')
        log(f'频道: @{TG_CHANNEL}')
        # 测试 bot 是否有效
        me = tg_api_request('getMe')
        if me:
            log(f'Bot: @{me.get("username", "unknown")}')
            log('✅ Bot 连接正常')
        else:
            log('❌ Bot 连接失败，请检查 Token')
        return

    if args.daemon:
        run_daemon(args.interval)
    else:
        run_once()


if __name__ == '__main__':
    main()

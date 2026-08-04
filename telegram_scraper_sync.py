#!/usr/bin/env python3
"""
Telegram 公开频道自动同步 + 直接下载脚本 v3
带日志输出到文件，方便调试
"""
import os
import sys
import json
import re
import time
import base64
import subprocess
import urllib.request
import urllib.error
import urllib.parse
import ssl
import traceback
from datetime import datetime, timezone, timedelta

# ========== 配置 ==========
CHANNELS = [
    {'username': 'PNAyyds', 'keywords': ['PNA', 'PAN'], 'tag': '💜'},
    {'username': 'hhhhhp', 'keywords': ['芒果', '客户端'], 'tag': '🥭'},
]

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_OWNER = 'Zy-api'
GITHUB_REPO = 'Fox'
GITHUB_BRANCH = 'main'
DATA_FILE = 'pan-data.json'
LOG_FILE = 'sync-log.txt'

CST = timezone(timedelta(hours=8))

# 日志收集
log_lines = []


def log(msg):
    ts = datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line, flush=True)
    log_lines.append(line)


def save_log_to_github():
    """保存日志到GitHub"""
    if not GITHUB_TOKEN:
        return
    
    log_content = '\n'.join(log_lines)
    
    # 获取现有文件sha
    existing = None
    try:
        url = f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{LOG_FILE}?ref={GITHUB_BRANCH}'
        headers = {
            'Authorization': f'token {GITHUB_TOKEN}',
            'Accept': 'application/vnd.github.v3+json',
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            existing = json.loads(resp.read().decode('utf-8'))
    except:
        pass
    
    sha = existing.get('sha') if existing else None
    
    content_b64 = base64.b64encode(log_content.encode('utf-8')).decode('utf-8')
    payload = {
        'message': f'sync-log: {datetime.now(CST).strftime("%Y-%m-%d %H:%M")}',
        'content': content_b64,
        'branch': GITHUB_BRANCH
    }
    if sha:
        payload['sha'] = sha
    
    try:
        url = f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{LOG_FILE}'
        headers = {
            'Authorization': f'token {GITHUB_TOKEN}',
            'Accept': 'application/vnd.github.v3+json',
            'Content-Type': 'application/json',
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='PUT')
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
    except Exception as e:
        print(f'保存日志失败: {e}')


def run_cmd(cmd):
    log(f'   执行: {cmd[:100]}')
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            log(f'   ✅ 成功')
            if result.stdout.strip():
                for line in result.stdout.strip().split('\n')[-5:]:
                    log(f'     {line[:80]}')
            return True, result.stdout
        else:
            log(f'   ❌ 失败 (退出码: {result.returncode})')
            if result.stderr.strip():
                for line in result.stderr.strip().split('\n')[-10:]:
                    log(f'     错误: {line[:100]}')
            return False, result.stderr
    except Exception as e:
        log(f'   ❌ 异常: {e}')
        return False, str(e)


def install_playwright():
    log('📦 安装 Playwright...')
    
    ok, _ = run_cmd(f'{sys.executable} -m pip install playwright --quiet')
    if not ok:
        log('❌ Playwright pip 安装失败')
        return False
    
    log('📦 安装系统依赖...')
    ok, _ = run_cmd(f'{sys.executable} -m playwright install-deps chromium')
    if not ok:
        log('⚠️  系统依赖安装有警告，继续...')
    
    log('📦 安装 Chromium...')
    ok, _ = run_cmd(f'{sys.executable} -m playwright install chromium')
    if not ok:
        log('❌ Chromium 安装失败')
        return False
    
    # 验证安装
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        log('✅ Playwright + Chromium 验证通过')
        return True
    except Exception as e:
        log(f'❌ Playwright 验证失败: {e}')
        traceback.print_exc()
        return False


def match_keywords(filename, keywords):
    filename_lower = filename.lower()
    for kw in keywords:
        if kw.lower() in filename_lower:
            return True
    return False


def github_api_request(path, method='GET', data=None, content_type='application/json', raw_url=None):
    url = raw_url if raw_url else f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/{path}'
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'Telegram-Sync-Bot'
    }
    if content_type:
        headers['Content-Type'] = content_type
    
    body = None
    if data:
        if isinstance(data, bytes):
            body = data
        else:
            body = json.dumps(data).encode('utf-8')
    
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = resp.read()
            if result:
                return json.loads(result.decode('utf-8'))
            return {'status': 'success'}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='ignore')[:500]
        if e.code == 204:
            return {'status': 'success'}
        log(f'❌ GitHub API HTTP {e.code}: {error_body[:200]}')
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
        'message': f'auto-sync: 资源同步更新 - {datetime.now(CST).strftime("%Y-%m-%d %H:%M")}',
        'content': content_b64,
        'branch': GITHUB_BRANCH
    }
    if sha:
        payload['sha'] = sha
    result = github_api_request(f'contents/{DATA_FILE}', 'PUT', payload)
    return result is not None and 'content' in result


def get_or_create_release():
    release_tag = 'telegram-files'
    release = github_api_request(f'releases/tags/{release_tag}')
    
    if not release:
        log('📦 创建 GitHub Release...')
        release = github_api_request('releases', 'POST', {
            'tag_name': release_tag,
            'name': '📦 资源文件库',
            'body': '自动同步的 Telegram 频道资源文件',
            'draft': False,
            'prerelease': False,
        })
        if release:
            log('✅ Release 创建成功')
    
    return release


def upload_file_to_release(file_path, file_name, release):
    upload_url = release.get('upload_url', '').replace('{?name,label}', f'?name={urllib.parse.quote(file_name)}')
    
    if not upload_url:
        log('❌ 无法获取上传地址')
        return None
    
    log(f'⬆️  上传: {file_name}')
    
    try:
        file_size = os.path.getsize(file_path)
        log(f'   大小: {file_size / 1024 / 1024:.1f} MB')
        
        with open(file_path, 'rb') as f:
            file_data = f.read()
        
        result = github_api_request(
            '',
            'POST',
            data=file_data,
            content_type='application/octet-stream',
            raw_url=upload_url
        )
        
        if result and 'browser_download_url' in result:
            log(f'   ✅ 上传成功')
            return result['browser_download_url']
        else:
            log(f'   ❌ 上传失败')
            return None
    except Exception as e:
        log(f'❌ 上传失败: {e}')
        traceback.print_exc()
        return None


def delete_release_asset(asset_id):
    result = github_api_request(f'releases/assets/{asset_id}', 'DELETE')
    return result is not None


def fetch_channel_files(channel_username):
    log(f'🔍 抓取 @{channel_username} 频道...')
    
    all_messages = []
    after_id = None
    max_pages = 15
    page = 0
    
    ctx = ssl._create_unverified_context()
    
    while page < max_pages:
        page += 1
        url = f'https://t.me/s/{channel_username}'
        if after_id:
            url += f'?after={after_id}'
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                html = resp.read().decode('utf-8')
        except Exception as e:
            log(f'❌ 抓取失败 (第{page}页): {e}')
            break
        
        doc_names = re.findall(r'class="tgme_widget_message_document_name[^"]*">([^<]+)</div>', html)
        doc_sizes = re.findall(r'class="tgme_widget_message_document_extra[^"]*">([^<]+)</div>', html)
        msg_ids = re.findall(r'data-post="([^"]+)"', html)
        msg_links = re.findall(r'href="(https://t\.me/[^"]+/\d+)"', html)
        date_matches = re.findall(r'<time[^>]*datetime="([^"]+)"', html)
        text_matches = re.findall(r'class="tgme_widget_message_text[^"]*">(.*?)</div>', html, re.DOTALL)
        
        if not doc_names:
            log(f'   第{page}页没有文件，停止翻页')
            break
        
        for i, name in enumerate(doc_names):
            name = name.strip()
            size = doc_sizes[i].strip() if i < len(doc_sizes) else ''
            msg_id = msg_ids[i] if i < len(msg_ids) else ''
            link = msg_links[i] if i < len(msg_links) else ''
            date_str = date_matches[i] if i < len(date_matches) else ''
            text = text_matches[i] if i < len(text_matches) else ''
            desc = re.sub(r'<[^>]+>', '', text).strip()[:100] if text else ''
            
            all_messages.append({
                'msg_id': msg_id,
                'name': name,
                'size': size,
                'link': link,
                'date': date_str,
                'desc': desc,
                'channel': channel_username,
            })
        
        if msg_ids:
            last_id = msg_ids[-1].split('/')[-1]
            try:
                last_id_num = int(last_id)
                if after_id and last_id_num <= int(after_id):
                    break
                after_id = str(last_id_num - 1)
            except:
                break
        else:
            break
        
        time.sleep(0.3)
    
    log(f'   找到 {len(all_messages)} 个文件')
    return all_messages


def download_file_playwright(telegram_url, save_path):
    log(f'   🌐 浏览器访问: {telegram_url}')
    
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log('   ❌ Playwright 未安装')
        return False
    
    try:
        with sync_playwright() as p:
            log('   启动浏览器...')
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                accept_downloads=True,
                viewport={'width': 1920, 'height': 1080},
            )
            page = context.new_page()
            
            log('   加载页面...')
            try:
                resp = page.goto(telegram_url, wait_until='domcontentloaded', timeout=30000)
                log(f'   响应状态: {resp.status if resp else "None"}')
            except Exception as e:
                log(f'   ⚠️  页面加载警告: {e}')
            
            time.sleep(5)
            
            title = page.title()
            log(f'   页面标题: {title}')
            
            # 获取页面HTML的一部分
            html_content = page.content()
            log(f'   HTML长度: {len(html_content)}')
            
            # 保存截图
            try:
                screenshot_bytes = page.screenshot(full_page=True)
                log(f'   截图大小: {len(screenshot_bytes)} bytes')
            except Exception as e:
                log(f'   截图失败: {e}')
            
            # 获取所有链接
            all_links = page.eval_on_selector_all('a', 'elements => elements.map(e => ({text: e.textContent.trim(), href: e.href, class: e.className, download: e.download}))')
            log(f'   页面链接数: {len(all_links)}')
            
            # 打印所有链接
            for i, link in enumerate(all_links):
                href = link.get('href', '')
                text = link.get('text', '')
                cls = link.get('class', '')
                dl = link.get('download', '')
                if href and ('file' in href.lower() or 'download' in href.lower() or 'cdn' in href.lower() or dl):
                    log(f'   链接[{i}]: {text[:30]} -> {href[:80]} (download={dl}, class={cls[:50]})')
            
            # 查找所有可能的下载元素
            download_url = None
            
            # 方法1: 带 download 属性的链接
            download_els = page.query_selector_all('[download]')
            log(f'   带download属性的元素: {len(download_els)}')
            for el in download_els:
                href = el.get_attribute('href')
                dl = el.get_attribute('download')
                log(f'     download={dl}, href={href[:80] if href else None}')
                if href and href.startswith('http'):
                    download_url = href
                    break
            
            # 方法2: 各种选择器
            if not download_url:
                selectors = [
                    'a.tgme_widget_message_download_button',
                    '.tgme_widget_message_document_action_download',
                    'a.tgme_widget_message_document_action',
                    '.tgme_widget_message_document_wrap a',
                    'a.tgme_widget_message_document',
                    '.tgme_widget_message_document a',
                    '.tgme_widget_message_bubble a[href*="file"]',
                    'a[href*="cdn.telegram"]',
                    'a[href*="download"]',
                ]
                
                for sel in selectors:
                    try:
                        els = page.query_selector_all(sel)
                        if els:
                            log(f'   选择器 {sel} 找到 {len(els)} 个元素')
                            for el in els:
                                href = el.get_attribute('href')
                                if href and href.startswith('http'):
                                    download_url = href
                                    log(f'     -> {href[:80]}')
                                    break
                            if download_url:
                                break
                    except Exception as e:
                        log(f'   选择器 {sel} 错误: {e}')
            
            # 方法3: 从HTML中正则提取
            if not download_url:
                # 查找 file 相关的URL
                file_urls = re.findall(r'https?://[^\s"\'<>]+(?:file|cdn\.telegram|download)[^\s"\'<>]*', html_content, re.IGNORECASE)
                log(f'   从HTML提取文件URL: {len(file_urls)}')
                for u in file_urls[:5]:
                    log(f'     {u[:100]}')
                if file_urls:
                    download_url = file_urls[0]
            
            # 下载文件
            if download_url:
                log(f'   📥 下载文件: {download_url[:80]}...')
                ctx = ssl._create_unverified_context()
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': 'https://t.me/',
                }
                try:
                    req = urllib.request.Request(download_url, headers=headers)
                    with urllib.request.urlopen(req, timeout=300, context=ctx) as resp:
                        content_type = resp.headers.get('Content-Type', '')
                        content_length = int(resp.headers.get('Content-Length', 0))
                        log(f'   Content-Type: {content_type}')
                        log(f'   大小: {content_length / 1024 / 1024:.1f} MB')
                        
                        with open(save_path, 'wb') as f:
                            while True:
                                chunk = resp.read(8192)
                                if not chunk:
                                    break
                                f.write(chunk)
                    
                    if os.path.exists(save_path) and os.path.getsize(save_path) > 10000:
                        size_mb = os.path.getsize(save_path) / 1024 / 1024
                        log(f'   ✅ 下载成功 ({size_mb:.1f} MB)')
                        browser.close()
                        return True
                    else:
                        log(f'   ❌ 文件太小: {os.path.getsize(save_path) if os.path.exists(save_path) else 0} bytes')
                except Exception as e:
                    log(f'   ❌ 直接下载失败: {e}')
                    traceback.print_exc()
            else:
                log('   ❌ 未找到下载链接')
            
            # 方法4: 点击触发
            log('   尝试点击下载...')
            try:
                with page.expect_download(timeout=10000) as download_info:
                    for sel in [
                        'a.tgme_widget_message_download_button',
                        '.tgme_widget_message_document_action_download',
                        '.tgme_widget_message_document_wrap',
                        '.tgme_widget_message_document',
                        'a[download]',
                    ]:
                        try:
                            el = page.query_selector(sel)
                            if el:
                                log(f'   点击: {sel}')
                                el.click()
                                break
                        except:
                            continue
                
                download = download_info.value
                download.save_as(save_path)
                browser.close()
                
                if os.path.exists(save_path):
                    size_mb = os.path.getsize(save_path) / 1024 / 1024
                    log(f'   ✅ 点击下载成功 ({size_mb:.1f} MB)')
                    return True
            except Exception as e:
                log(f'   点击下载失败: {e}')
            
            browser.close()
            return False
    except Exception as e:
        log(f'❌ Playwright 错误: {e}')
        traceback.print_exc()
        return False


def sync_once():
    log('🚀 开始 Telegram 同步 v3')
    log(f'   频道: {", ".join(c["username"] for c in CHANNELS)}')
    log(f'   GITHUB_TOKEN: {"已配置" if GITHUB_TOKEN else "未配置"}')

    all_matched_files = []

    for channel in CHANNELS:
        ch_name = channel['username']
        keywords = channel['keywords']
        tag = channel['tag']
        
        log(f'\n📡 频道: @{ch_name} (关键词: {", ".join(keywords)})')
        
        try:
            messages = fetch_channel_files(ch_name)
        except Exception as e:
            log(f'❌ 抓取频道失败: {e}')
            traceback.print_exc()
            continue
        
        for msg in messages:
            name = msg['name']
            
            if not match_keywords(name, keywords):
                continue
            
            date_str = datetime.now(CST).strftime('%Y-%m-%d')
            if msg['date']:
                try:
                    dt = datetime.fromisoformat(msg['date'].replace('Z', '+00:00'))
                    date_str = dt.astimezone(CST).strftime('%Y-%m-%d')
                except:
                    pass
            
            unique_id = f"{ch_name}_{name}"
            
            file_info = {
                'name': name,
                'size': msg['size'],
                'date': date_str,
                'url': msg['link'],
                'download_url': '',
                'direct_url': '',
                'tag': f'{tag}',
                'desc': msg['desc'] or f'来自 @{ch_name}',
                'icon': '',
                'file_unique_id': unique_id,
                'msg_id': msg['msg_id'],
                'channel': ch_name,
                'source': 'telegram'
            }
            all_matched_files.append(file_info)
            log(f'  ✨ {tag} {name} ({msg["size"]})')

    if all_matched_files:
        log(f'\n📊 共发现 {len(all_matched_files)} 个匹配文件')
        log(f'   筛选每个频道最新版...')
        
        latest_by_channel = {}
        for f in all_matched_files:
            ch = f['channel']
            try:
                msg_id = int(f['msg_id'].split('/')[-1]) if f['msg_id'] else 0
            except:
                msg_id = 0
            
            if ch not in latest_by_channel or msg_id > latest_by_channel[ch].get('_msg_id_num', 0):
                f['_msg_id_num'] = msg_id
                latest_by_channel[ch] = f
        
        latest_files = list(latest_by_channel.values())
        log(f'   保留 {len(latest_files)} 个最新版')

        # 安装 Playwright
        pw_ok = install_playwright()

        # 下载文件并上传
        if pw_ok and GITHUB_TOKEN:
            release = get_or_create_release()
            if release:
                existing_assets = release.get('assets', [])
                log(f'   Release现有文件: {len(existing_assets)}')
                for asset in existing_assets:
                    log(f'     删除: {asset["name"]}')
                    delete_release_asset(asset['id'])
                
                os.makedirs('/tmp/downloads', exist_ok=True)
                
                for f in latest_files:
                    safe_name = f['name'].replace(' ', '_')
                    local_path = f'/tmp/downloads/{f["channel"]}_{safe_name}'
                    
                    log(f'\n📥 处理: {f["name"]}')
                    log(f'   消息链接: {f["url"]}')
                    
                    try:
                        success = download_file_playwright(f['url'], local_path)
                    except Exception as e:
                        log(f'❌ 下载异常: {e}')
                        traceback.print_exc()
                        success = False
                    
                    if success and os.path.exists(local_path):
                        upload_name = f'{f["channel"]}_{safe_name}'
                        dl_url = upload_file_to_release(local_path, upload_name, release)
                        if dl_url:
                            f['download_url'] = dl_url
                            f['direct_url'] = dl_url
                            log(f'   🔗 直链已就绪')
                    
                    time.sleep(1)
        else:
            log('⚠️  跳过下载')
            if not pw_ok:
                log('   原因: Playwright安装失败')
            if not GITHUB_TOKEN:
                log('   原因: 无GitHub Token')

        # 更新数据
        if GITHUB_TOKEN:
            remote_data, sha = load_remote_data()
            if remote_data is None:
                remote_data = {'files': [], 'settings': {}}

            for nf in latest_files:
                nf.pop('_msg_id_num', None)

            remote_data['files'] = latest_files
            if save_remote_data(remote_data, sha):
                log(f'\n✅ 同步完成！{len(latest_files)} 个最新版文件')
                for f in latest_files:
                    has_dl = '✅ 可直接下载' if f.get('direct_url') else '❌ 无直链'
                    log(f'   - {f["name"]} | {has_dl}')
            else:
                log(f'❌ 保存失败')
        else:
            log('⚠️  无 GitHub Token，不保存数据')
    else:
        log('📭 没有匹配的文件')
    
    # 保存日志
    save_log_to_github()
    
    return len(all_matched_files)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Telegram 同步 + 直接下载')
    parser.add_argument('--once', action='store_true', help='运行一次')
    args = parser.parse_args()

    try:
        sync_once()
    except Exception as e:
        log(f'❌ 致命错误: {e}')
        traceback.print_exc()
        save_log_to_github()
        sys.exit(1)


if __name__ == '__main__':
    main()

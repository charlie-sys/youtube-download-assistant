"""YouTube download manager. Python stdlib GUI; external tools isolated in LocalAppData."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from urllib.parse import urlparse

APP = 'YouTube 下载助手'
ROOT = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'YouTubeDownloadAssistant'
TOOLS = ROOT / 'tools'
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
MODES = ['视频 + 音频', '视频 + 音频 + 字幕', '仅字幕']
LANGS = {'中文 + 英文': 'en.*,zh.*', '仅英文': 'en.*', '仅中文': 'zh.*', '所有语言': 'all,-live_chat'}


def valid_url(value):
    try:
        u = urlparse(value.strip())
        return u.scheme in ('https', 'http') and u.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be')
    except ValueError:
        return False


def command(job, tools=TOOLS):
    args = [str(tools / 'yt-dlp.exe'), '--ignore-config', '--newline', '--no-color',
            '--no-playlist', '--windows-filenames', '--continue', '--no-overwrites',
            '--ffmpeg-location', str(tools), '--js-runtimes', 'deno:' + str(tools / 'deno.exe'),
            '--encoding', 'utf-8', '--socket-timeout', '30', '--retries', '5',
            '-P', job['folder'], '-o', '%(title).160B [%(id)s].%(ext)s',
            '--progress-template', 'download:PROGRESS|%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s',
            '--print', 'after_move:FILE|%(filepath)s']
    if job['mode'] == '仅字幕':
        args += ['--skip-download']
    else:
        height = job.get('quality', '最佳画质')
        limit = '' if height == '最佳画质' else '[height<=%s]' % height.rstrip('p')
        args += ['-f', 'bv*%s+ba/b%s' % (limit, limit), '--merge-output-format', 'mkv']
    if job['mode'] != '视频 + 音频':
        args += ['--write-subs', '--write-auto-subs', '--sub-langs', LANGS[job['language']], '--convert-subs', 'srt']
    if job.get('proxy'):
        args += ['--proxy', job['proxy']]
    return args + ['--', job['url']]


def fetch(url, destination, log):
    """Download atomically, retry transient network errors, preserve existing installation."""
    destination = Path(destination)
    part = destination.with_name(destination.name + '.part')
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'YouTubeDownloadAssistant/1.0'})
            with urllib.request.urlopen(request, timeout=45) as response, part.open('wb') as out:
                size = int(response.headers.get('Content-Length', 0))
                done, last = 0, 0
                while True:
                    chunk = response.read(1024 * 512)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if time.monotonic() - last > 1:
                        log('下载组件：%.1f MB%s' % (done / 1048576, (' / %.1f MB' % (size / 1048576)) if size else ''))
                        last = time.monotonic()
                if size and done != size:
                    raise OSError('组件下载不完整')
            part.replace(destination)
            return
        except Exception:
            part.unlink(missing_ok=True)
            if attempt == 2:
                raise
            log('网络中断，正在重试组件下载…')


def install_tools(log):
    TOOLS.mkdir(parents=True, exist_ok=True)
    specs = [
        ('yt-dlp.exe', 'yt-dlp/yt-dlp', 'yt-dlp.exe', ['yt-dlp.exe']),
        ('deno.exe', 'denoland/deno', 'deno-x86_64-pc-windows-msvc.zip', ['deno.exe']),
        ('ffmpeg.exe', 'yt-dlp/FFmpeg-Builds', 'ffmpeg-master-latest-win64-gpl.zip', ['ffmpeg.exe', 'ffprobe.exe']),
    ]
    for name, repo, asset_name, members in specs:
        if all((TOOLS / member).is_file() for member in members):
            continue
        log('正在准备 ' + name + '（首次下载可能需要几分钟）')
        with tempfile.TemporaryDirectory(dir=str(TOOLS)) as temp:
            temp = Path(temp)
            api = 'https://api.github.com/repos/%s/releases/latest' % repo
            meta_file = temp / 'release.json'
            fetch(api, meta_file, lambda s: None)
            meta = json.loads(meta_file.read_text(encoding='utf-8'))
            asset = next((a for a in meta['assets'] if a['name'] == asset_name), None)
            if not asset:
                raise RuntimeError('官方发布中未找到组件：' + asset_name)
            archive = temp / asset_name
            fetch(asset['browser_download_url'], archive, log)
            digest = asset.get('digest', '') or ''
            if digest.startswith('sha256:'):
                actual = hashlib.sha256(archive.read_bytes()).hexdigest()
                if actual != digest.split(':', 1)[1]:
                    raise RuntimeError('组件校验失败：' + name)
            staged = []
            if archive.suffix == '.zip':
                with zipfile.ZipFile(archive) as z:
                    for member in members:
                        entry = next((n for n in z.namelist() if Path(n).name == member), None)
                        if not entry:
                            raise RuntimeError('压缩包缺少 ' + member)
                        target = temp / member
                        with z.open(entry) as source, target.open('wb') as dest:
                            shutil.copyfileobj(source, dest)
                        staged.append((target, member))
            else:
                staged.append((archive, name))
            for source, member in staged:
                result = subprocess.run([str(source), '-version' if member.startswith('ff') else '--version'],
                                        capture_output=True, creationflags=FLAGS, timeout=20)
                if result.returncode:
                    raise RuntimeError('组件启动验证失败：' + member)
            for source, member in staged:
                os.replace(str(source), str(TOOLS / member))
        log(name + ' 已准备好')


class Manager:
    def __init__(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        self.events = queue.Queue()
        self.lock = threading.RLock()
        self.jobs = []
        self.process = None
        self.active = None
        self.stop_reason = None
        self.closing = False
        self.busy = False
        self.settings = {}
        try:
            data = json.loads((ROOT / 'state.json').read_text(encoding='utf-8'))
            self.settings = data.get('settings', {})
            self.jobs = data.get('jobs', [])
            for j in self.jobs:
                if j['status'] in ('排队中', '下载中', '准备中', '处理中'):
                    j['status'] = '已暂停'
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def log(self, text):
        self.events.put(('log', str(text)))

    def save(self):
        with self.lock:
            target = ROOT / 'state.json'
            temp = ROOT / 'state.tmp'
            temp.write_text(json.dumps({'settings': self.settings, 'jobs': self.jobs}, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(target)

    def update(self, job, **values):
        with self.lock:
            job.update(values)
            self.save()
        self.events.put(('refresh', None))

    def start(self):
        with self.lock:
            if self.busy or self.closing:
                return
            self.busy = True
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self, job, reason):
        with self.lock:
            if self.active is job:
                self.stop_reason = reason
                self.kill()
            else:
                self.update(job, status=reason)

    def kill(self):
        proc = self.process
        if proc and proc.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, creationflags=FLAGS)
            else:
                proc.terminate()

    def run(self):
        try:
            self.events.put(('tools', '正在检查下载工具…'))
            install_tools(self.log)
            self.events.put(('tools', '下载工具已就绪'))
            while not self.closing:
                with self.lock:
                    job = next((j for j in self.jobs if j['status'] == '排队中'), None)
                    if not job:
                        break
                    self.active, self.stop_reason = job, None
                self.download(job)
                with self.lock:
                    self.active = None
        except Exception as e:
            self.log('工具准备失败：' + str(e))
            self.events.put(('tools', '工具未就绪；检查网络后点击“安装 / 检查工具”重试'))
            with self.lock:
                for job in self.jobs:
                    if job['status'] == '排队中':
                        job.update(status='失败', detail='工具准备失败：' + str(e))
                self.save()
            self.events.put(('refresh', None))
        finally:
            with self.lock:
                self.busy, self.active = False, None
                more = not self.closing and any(j['status'] == '排队中' for j in self.jobs)
            if more:
                self.start()

    def download(self, job):
        self.update(job, status='下载中', detail='正在解析链接…')
        self.log('\n开始任务：' + job['url'])
        had_file, subtitle_seen, tail = False, False, []
        try:
            Path(job['folder']).mkdir(parents=True, exist_ok=True)
            with self.lock:
                if self.stop_reason or self.closing:
                    self.update(job, status=self.stop_reason or '已暂停')
                    return
                self.process = subprocess.Popen(command(job), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                encoding='utf-8', errors='replace', creationflags=FLAGS,
                                                env=dict(os.environ, PATH=str(TOOLS) + os.pathsep + os.environ.get('PATH', '')))
            for line in self.process.stdout:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('PROGRESS|'):
                    fields = line.split('|')
                    self.update(job, detail='  '.join(fields[1:]), progress=fields[1].strip())
                else:
                    self.log(line)
                    tail.append(line)
                    tail = tail[-12:]
                    if line.startswith('FILE|'):
                        path = line[5:]
                        if Path(path).is_file():
                            had_file = True
                            self.update(job, file=path)
                    if 'Writing video subtitles to:' in line or ('[SubtitlesConvertor]' in line and 'Converting' in line):
                        subtitle_seen = True
                    if line.startswith(('[Merger]', '[VideoRemuxer]', '[SubtitlesConvertor]')):
                        self.update(job, status='处理中', detail=line)
            code = self.process.wait()
            with self.lock:
                reason = self.stop_reason
            if reason:
                self.update(job, status=reason, detail='保留已下载部分，可继续 / 重试')
            elif code:
                self.update(job, status='失败', detail='\n'.join(tail)[-3000:])
            elif job['mode'] == '仅字幕' and not subtitle_seen:
                self.update(job, status='需检查', detail='未发现新字幕下载。可能无匹配字幕，或字幕文件已存在；请查看日志和文件夹。')
            elif job['mode'] != '仅字幕' and not had_file:
                self.update(job, status='需检查', detail='工具已结束，未确认输出文件；请查看日志。')
            else:
                detail = '下载完成'
                if job['mode'] == '视频 + 音频 + 字幕' and not subtitle_seen:
                    detail = '视频完成；字幕可能不存在或已下载，请检查日志'
                self.update(job, status='已完成', progress='100%', detail=detail)
        except Exception as e:
            self.log('任务异常：' + str(e))
            self.update(job, status=self.stop_reason or '失败', detail=str(e))
        finally:
            with self.lock:
                self.process = None


class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP)
        self.geometry('%dx%d' % (min(1280, self.winfo_screenwidth() - 80), min(1000, self.winfo_screenheight() - 120)))
        self.minsize(1000, 760)
        self.configure(bg='#f3f5f9')
        self.manager = Manager()
        self.build()
        self.refresh()
        self.after(150, self.poll)
        self.after(600, self.manager.start)
        self.protocol('WM_DELETE_WINDOW', self.close)

    def build(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background='#f3f5f9')
        style.configure('TButton', padding=(12, 7))
        style.configure('Accent.TButton', background='#2563eb', foreground='white')
        style.configure('Treeview', rowheight=32, background='white', fieldbackground='white')
        style.configure('Treeview.Heading', padding=8)
        outer = ttk.Frame(self, padding=22)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text=APP, font=('Microsoft YaHei UI', 22, 'bold'), foreground='#17243b').pack(anchor='w')
        ttk.Label(outer, text='视频与字幕，一处管理。首次启动自动准备工具，无需手动配置。', foreground='#64748b').pack(anchor='w', pady=(4, 16))
        form = ttk.Frame(outer)
        form.pack(fill='x')
        form.columnconfigure(1, weight=1)
        self.url = tk.StringVar()
        ttk.Label(form, text='YouTube 地址').grid(row=0, column=0, sticky='w', padx=(0, 12))
        ttk.Entry(form, textvariable=self.url).grid(row=0, column=1, sticky='ew', ipady=5)
        ttk.Button(form, text='粘贴链接', command=self.paste).grid(row=0, column=2, padx=(8, 0))
        self.folder = tk.StringVar(value=self.manager.settings.get('folder', str(Path.home() / 'Downloads' / 'YouTube')))
        ttk.Label(form, text='保存文件夹').grid(row=1, column=0, sticky='w', pady=10)
        ttk.Entry(form, textvariable=self.folder).grid(row=1, column=1, sticky='ew', ipady=5)
        ttk.Button(form, text='选择目录', command=self.choose_folder).grid(row=1, column=2, padx=(8, 0))
        options = ttk.Frame(outer)
        options.pack(fill='x', pady=(0, 8))
        self.mode = tk.StringVar(value=MODES[0])
        self.quality = tk.StringVar(value='1080p')
        self.language = tk.StringVar(value='中文 + 英文')
        for label, var, values, width in [('下载内容', self.mode, MODES, 22), ('画质上限', self.quality, ['最佳画质', '2160p', '1440p', '1080p', '720p', '480p'], 12), ('字幕语言', self.language, list(LANGS), 16)]:
            ttk.Label(options, text=label).pack(side='left', padx=(0, 6))
            ttk.Combobox(options, textvariable=var, values=values, state='readonly', width=width).pack(side='left', padx=(0, 14))
        proxyrow = ttk.Frame(outer)
        proxyrow.pack(fill='x', pady=(0, 8))
        self.proxy = tk.StringVar(value=self.manager.settings.get('proxy', ''))
        ttk.Label(proxyrow, text='代理（可选）').pack(side='left', padx=(0, 12))
        ttk.Entry(proxyrow, textvariable=self.proxy, width=36).pack(side='left')
        ttk.Label(proxyrow, text='例如 http://127.0.0.1:7890；留空使用系统环境', foreground='#64748b').pack(side='left', padx=8)
        actions = ttk.Frame(outer)
        actions.pack(fill='x', pady=(2, 10))
        ttk.Button(actions, text='＋ 添加下载', style='Accent.TButton', command=self.add).pack(side='left')
        ttk.Button(actions, text='安装 / 检查工具', command=self.manager.start).pack(side='left', padx=8)
        self.tool_status = tk.StringVar(value='等待工具检查…')
        ttk.Label(actions, textvariable=self.tool_status, foreground='#64748b').pack(side='left', padx=4)
        ttk.Label(outer, text='下载队列', font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w', pady=(4, 8))
        table = ttk.Frame(outer)
        table.pack(fill='both', expand=True)
        columns = ('name', 'mode', 'status', 'progress')
        self.tree = ttk.Treeview(table, columns=columns, show='headings', selectmode='extended', height=4)
        for key, label, width in [('name', '视频 / 链接', 490), ('mode', '内容', 170), ('status', '状态', 90), ('progress', '进度', 80)]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=60, stretch=key == 'name')
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self.show_detail)
        self.tree.bind('<Double-1>', lambda event: self.open_folder())
        buttons = ttk.Frame(outer)
        buttons.pack(fill='x', pady=8)
        for title, action in [('暂停', self.pause), ('继续 / 重试', self.resume), ('取消', self.cancel), ('打开文件夹', self.open_folder), ('播放视频', self.play), ('移除记录', self.remove)]:
            ttk.Button(buttons, text=title, command=action).pack(side='left', padx=(0, 6))
        self.detail = tk.StringVar(value='选择任务查看详情。暂停和取消会保留已下载部分；移除记录不会删除文件。')
        ttk.Label(outer, textvariable=self.detail, foreground='#475569', wraplength=1020).pack(fill='x', pady=(0, 5))
        self.logs = tk.Text(outer, height=4, bg='#17243b', fg='#e2e8f0', insertbackground='white', font=('Consolas', 9), relief='flat', wrap='word')
        self.logs.pack(fill='x')
        self.logs.configure(state='disabled')
        ttk.Label(outer, text='视频以 MKV 保存；字幕为独立 SRT 文件。仅下载你有权保存的内容。', foreground='#64748b').pack(anchor='w', pady=(7, 0))

    def selected(self):
        ids = set(self.tree.selection())
        return [j for j in self.manager.jobs if j['id'] in ids]

    def paste(self):
        try:
            self.url.set(self.clipboard_get().strip())
        except tk.TclError:
            pass

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.folder.get() if Path(self.folder.get()).is_dir() else str(Path.home()))
        if folder:
            self.folder.set(folder)

    def add(self):
        url = self.url.get().strip()
        if not valid_url(url):
            messagebox.showerror('链接有误', '请输入有效的 youtube.com 或 youtu.be 视频链接。')
            return
        folder = self.folder.get().strip()
        if not folder or not Path(folder).is_absolute():
            messagebox.showerror('保存目录有误', '请选择绝对路径，例如 D:\\My Videos。')
            return
        proxy = self.proxy.get().strip()
        if proxy and urlparse(proxy).scheme not in ('http', 'https', 'socks5', 'socks5h', 'socks4'):
            messagebox.showerror('代理有误', '代理地址需要以 http:// 或 socks5:// 等开头。')
            return
        job = dict(id=uuid.uuid4().hex, url=url, folder=folder, mode=self.mode.get(), quality=self.quality.get(),
                   language=self.language.get(), proxy=proxy, status='排队中', progress='—', detail='等待下载', file='')
        with self.manager.lock:
            if any(j['url'] == url and j['mode'] == job['mode'] and j['status'] in ('排队中', '下载中', '处理中') for j in self.manager.jobs):
                messagebox.showinfo('任务已存在', '相同内容正在队列中。')
                return
            self.manager.jobs.append(job)
            self.manager.settings.update(folder=folder, proxy=proxy)
            self.manager.save()
        self.url.set('')
        self.refresh()
        self.tree.selection_set(job['id'])
        self.manager.start()

    def pause(self):
        for job in self.selected():
            if job['status'] in ('排队中', '下载中', '处理中'):
                self.manager.stop(job, '已暂停')

    def cancel(self):
        for job in self.selected():
            if job['status'] in ('排队中', '下载中', '处理中', '已暂停'):
                self.manager.stop(job, '已取消')

    def resume(self):
        for job in self.selected():
            if job['status'] in ('已暂停', '已取消', '失败', '需检查') and self.manager.active is not job:
                self.manager.update(job, status='排队中', detail='等待下载')
        self.manager.start()

    def remove(self):
        with self.manager.lock:
            for job in self.selected():
                if self.manager.active is not job and job['status'] != '排队中':
                    self.manager.jobs.remove(job)
            self.manager.save()
        self.refresh()

    def open_folder(self):
        jobs = self.selected()
        folder = jobs[0]['folder'] if jobs else self.folder.get()
        if Path(folder).is_dir():
            os.startfile(folder)
        else:
            messagebox.showinfo('目录尚未创建', '下载开始后会自动创建保存目录。')

    def play(self):
        jobs = self.selected()
        if jobs and jobs[0].get('file') and Path(jobs[0]['file']).is_file():
            os.startfile(jobs[0]['file'])
        else:
            messagebox.showinfo('尚无视频文件', '请选择已完成的视频任务。')

    def show_detail(self, event=None):
        jobs = self.selected()
        if jobs:
            self.detail.set(jobs[0].get('detail', '')[-350:])

    def refresh(self):
        with self.manager.lock:
            jobs = [dict(j) for j in self.manager.jobs]
        ids = {j['id'] for j in jobs}
        for item in self.tree.get_children():
            if item not in ids:
                self.tree.delete(item)
        for job in jobs:
            name = Path(job['file']).name if job.get('file') else job['url']
            values = (name, job['mode'], job['status'], job.get('progress', '—'))
            if self.tree.exists(job['id']):
                self.tree.item(job['id'], values=values)
            else:
                self.tree.insert('', 'end', iid=job['id'], values=values)
        self.show_detail()

    def poll(self):
        refresh = False
        for _ in range(300):
            try:
                event, data = self.manager.events.get_nowait()
            except queue.Empty:
                break
            if event == 'refresh':
                refresh = True
            elif event == 'tools':
                self.tool_status.set(data)
            else:
                self.logs.configure(state='normal')
                self.logs.insert('end', data + '\n')
                if int(self.logs.index('end-1c').split('.')[0]) > 1500:
                    self.logs.delete('1.0', '300.0')
                self.logs.see('end')
                self.logs.configure(state='disabled')
                with (ROOT / 'download.log').open('a', encoding='utf-8') as log:
                    log.write(time.strftime('%Y-%m-%d %H:%M:%S ') + data + '\n')
        if refresh:
            self.refresh()
        self.after(150, self.poll)

    def close(self):
        if self.manager.active and not messagebox.askyesno('退出客户端', '有任务正在下载。退出后保留已下载部分，下次可继续。是否退出？'):
            return
        with self.manager.lock:
            self.manager.closing = True
            self.manager.stop_reason = '已暂停'
            self.manager.kill()
            for job in self.manager.jobs:
                if job['status'] in ('排队中', '下载中', '处理中'):
                    job['status'] = '已暂停'
            self.manager.settings.update(folder=self.folder.get(), proxy=self.proxy.get())
            self.manager.save()
        self.destroy()


if __name__ == '__main__':
    if os.name == 'nt':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        # A single instance prevents simultaneous writes to the task database.
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\YouTubeDownloadAssistant')
        if ctypes.windll.kernel32.GetLastError() == 183:
            ctypes.windll.user32.MessageBoxW(0, '客户端已经打开，请切换到现有窗口。', APP, 0)
            sys.exit(0)
    Application().mainloop()

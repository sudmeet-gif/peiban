"""Offline, per-user Windows installer. The adjacent payload contains no user data."""
import os
import queue
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

if getattr(sys, 'frozen', False):
    BUNDLE = Path(sys.executable).resolve().parent
else:
    BUNDLE = Path(__file__).resolve().parent
PAYLOAD = BUNDLE / 'payload'
DEFAULT = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'Programs' / 'Peiban'

def windows_path(path):
    """Use Windows extended-length paths for deeply nested Python packages."""
    value = str(Path(path).resolve())
    if os.name != 'nt' or value.startswith('\\\\?\\'):
        return value
    return '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value

def install(target, progress=lambda done,total,name:None, shortcut=False):
    target = Path(target).expanduser().resolve()
    source = PAYLOAD.resolve()
    if not (source/'Peiban.exe').is_file() or not (source/'models'/'Qwen3-4B-Q4_K_M.gguf').is_file() or not (source/'speech_runtime'/'python.exe').is_file():
        raise RuntimeError('安装文件不完整，请重新完整解压安装包。')
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise RuntimeError('安装位置不能位于安装包内，也不能包含安装包。')
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise RuntimeError('安装位置必须是空文件夹；请选择新位置，已有文件不会被覆盖。')
    source_extended=windows_path(source)
    files=[]
    for directory, _, names in os.walk(source_extended):
        for name in names:
            full=os.path.join(directory,name)
            files.append((full,os.path.relpath(full,source_extended)))
    total=sum(os.path.getsize(full) for full,_ in files); target.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(target.parent).free < total + 256*1024*1024:
        raise RuntimeError('安装位置空间不足，请选择有足够空间的磁盘。')
    stage=target.parent/('.peiban-install-'+uuid.uuid4().hex)
    done=0
    try:
        os.mkdir(windows_path(stage))
        for src,relative in files:
            dst=stage/relative; os.makedirs(windows_path(dst.parent),exist_ok=True)
            with open(src,'rb') as inp, open(windows_path(dst),'wb') as out:
                while True:
                    block=inp.read(8*1024*1024)
                    if not block: break
                    out.write(block); done+=len(block); progress(done,total,os.path.basename(src))
            if os.path.getsize(windows_path(dst)) != os.path.getsize(src): raise OSError('安装文件复制不完整：'+os.path.basename(src))
        if target.exists(): target.rmdir()
        os.rename(windows_path(stage),windows_path(target))
    except Exception:
        if stage.exists(): shutil.rmtree(windows_path(stage))
        raise
    if shortcut:
        desktop = Path(os.environ.get('USERPROFILE',str(Path.home()))) / 'Desktop'
        if desktop.is_dir():
            env=os.environ.copy(); env['PEIBAN_INSTALL_SHORTCUT']=str(desktop/'陪伴.lnk'); env['PEIBAN_INSTALL_EXE']=str(target/'Peiban.exe'); env['PEIBAN_INSTALL_DIR']=str(target)
            script="$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut($env:PEIBAN_INSTALL_SHORTCUT); $s.TargetPath=$env:PEIBAN_INSTALL_EXE; $s.WorkingDirectory=$env:PEIBAN_INSTALL_DIR; $s.Save()"
            subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],env=env,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),capture_output=True)
    return target

def main():
    if len(sys.argv)>2 and sys.argv[1]=='--test-install':
        install(sys.argv[2]); print('installation successful'); return
    root=tk.Tk(); root.title('陪伴 安装向导'); root.geometry('620x310'); root.resizable(False,False)
    ttk.Label(root,text='安装本地 AI 陪伴',font=('Microsoft YaHei',16,'bold')).pack(anchor='w',padx=24,pady=(20,8))
    ttk.Label(root,text='包含离线聊天、视觉和语音模型，安装需要较大的磁盘空间。').pack(anchor='w',padx=24)
    row=ttk.Frame(root); row.pack(fill='x',padx=24,pady=16)
    path=tk.StringVar(value=str(DEFAULT)); field=ttk.Entry(row,textvariable=path); field.pack(side='left',fill='x',expand=True)
    ttk.Button(row,text='选择文件夹',command=lambda: choose()).pack(side='left',padx=(8,0))
    def choose():
        p=filedialog.askdirectory(title='选择空文件夹或安装目录的上级文件夹')
        if p: path.set(p)
    shortcut=tk.BooleanVar(value=True); ttk.Checkbutton(root,text='在桌面创建快捷方式',variable=shortcut).pack(anchor='w',padx=24)
    bar=ttk.Progressbar(root,mode='determinate',maximum=100); bar.pack(fill='x',padx=24,pady=(16,4))
    status=ttk.Label(root,text='请选择空文件夹，然后点击安装。'); status.pack(anchor='w',padx=24)
    buttons=ttk.Frame(root); buttons.pack(fill='x',padx=24,pady=12)
    button=ttk.Button(buttons,text='开始安装'); button.pack(side='right')
    events=queue.Queue(); running=[False]
    def start():
        if running[0]: return
        target=path.get().strip()
        if not target: messagebox.showerror('安装失败','请选择安装位置。'); return
        running[0]=True; button.config(state='disabled'); selected_shortcut=shortcut.get()
        def task():
            try: events.put(('success',str(install(target,lambda done,total,name:events.put(('progress',done,total,name)),selected_shortcut))))
            except Exception as e: events.put(('error',str(e)))
        threading.Thread(target=task,daemon=True).start(); root.after(100,poll)
    def poll():
        try:
            while True:
                event=events.get_nowait()
                if event[0]=='progress': bar['value']=event[1]*100/max(event[2],1); status.config(text=f'正在安装：{event[3]}　{bar["value"]:.0f}%')
                elif event[0]=='success':
                    running[0]=False; bar['value']=100; status.config(text='安装完成'); messagebox.showinfo('安装完成',f'已安装到：\n{event[1]}\n\n打开桌面的“陪伴”快捷方式即可使用。'); root.destroy(); return
                else:
                    running[0]=False; button.config(state='normal'); status.config(text='安装未完成'); messagebox.showerror('安装失败',event[1]); return
        except queue.Empty: pass
        root.after(100,poll)
    def close():
        if running[0]: messagebox.showinfo('正在安装','请等待安装完成。')
        else: root.destroy()
    button.config(command=start); root.protocol('WM_DELETE_WINDOW',close); root.mainloop()

if __name__=='__main__': main()

"""Owned native children: hidden windows, minimal environment and race-free close."""
import os
import subprocess
import threading


def child_environment():
    allowed={'SYSTEMROOT','WINDIR','PATH','PATHEXT','TEMP','TMP','LOCALAPPDATA','APPDATA','USERPROFILE',
        'HOME','HOMEDRIVE','HOMEPATH','PROGRAMDATA','LANG','LC_ALL',
        'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','SSL_CERT_FILE','SSL_CERT_DIR'}
    return {key:value for key,value in os.environ.items() if key.upper() in allowed or key.upper().startswith('LC_')}


class OwnedProcesses:
    def __init__(self):
        self.lock=threading.Lock()
        self.processes=[]
        self.closed=False

    def start(self,command,**options):
        if not isinstance(command,(list,tuple)):raise ValueError('Native command must be an argument list')
        options.setdefault('env',child_environment())
        options['creationflags']=options.get('creationflags',0)|getattr(subprocess,'CREATE_NO_WINDOW',0)
        with self.lock:
            if self.closed:raise RuntimeError('Voice process owner has closed')
            process=subprocess.Popen(command,**options)
            self.processes.append(process)
            return process

    def close(self):
        with self.lock:
            self.closed=True
            processes=list(reversed(self.processes))
        errors=[]
        for process in processes:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(3)
                    except subprocess.TimeoutExpired:process.kill();process.wait(2)
            except OSError as error:errors.append(type(error).__name__)
        return errors

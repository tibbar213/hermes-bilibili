"""Owner-only Unix socket RPC so CLI/child workers reuse the gateway's native runtime."""
import asyncio
import fcntl
import json
import os
import socket
import stat

class RuntimeAlreadyOwned(RuntimeError):
    pass

def lock_runtime(home):
    root=home/'bilibili'
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(root/'runtime.lock',os.O_CREAT|os.O_RDWR,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise RuntimeAlreadyOwned('Another native Bilibili scheduler already owns this profile') from None
    return fd

async def start_server(host):
    path=host.home/'bilibili'/'runtime.sock'
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not stat.S_ISSOCK(path.stat().st_mode):
            raise RuntimeError('Invalid Bilibili runtime socket path')
        path.unlink()  # Exclusive runtime lock proves no other owner can be using it.
    async def handle(reader,writer):
        try:
            raw=await asyncio.wait_for(reader.readline(),timeout=5)
            data=json.loads(raw)
            operation=data.get('operation')
            if operation=='schemas':
                value=host.schemas
            elif operation=='control':
                value=await host.control(data.get('args') or {})
            elif operation=='life_action':
                value=await host.life_action(data.get('args') or {})
            elif operation=='invoke':
                value=await host.invoke(str(data.get('name','')),data.get('args') or {})
            else:
                raise ValueError('Unknown native IPC operation')
            response={'ok':True,'value':value}
        except Exception as exc:
            response={'ok':False,'error_type':type(exc).__name__}
        try:
            writer.write((json.dumps(response,ensure_ascii=False)+'\n').encode())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    server=await asyncio.start_unix_server(handle,path=str(path),limit=1024*1024)
    path.chmod(0o600)
    return server

def request(home,data,timeout=600):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(home/'bilibili'/'runtime.sock'))
        client.sendall((json.dumps(data,ensure_ascii=False)+'\n').encode())
        with client.makefile('rb') as stream:
            raw=stream.readline(2*1024*1024)
    result=json.loads(raw)
    if not result.get('ok'):
        raise RuntimeError('Native Bilibili operation failed: '+str(result.get('error_type')))
    return result['value']

class Proxy:
    def __init__(self,home):
        self.home=home

    @property
    def schemas(self):
        return request(self.home,{'operation':'schemas'},timeout=10)

    async def control(self,args):
        return await asyncio.to_thread(request,self.home,{'operation':'control','args':args})

    async def life_action(self,args):
        return await asyncio.to_thread(request,self.home,{'operation':'life_action','args':args})

    async def invoke(self,name,args):
        return await asyncio.to_thread(request,self.home,{'operation':'invoke','name':name,'args':args})

    def submit(self,coro,timeout=600):
        return asyncio.run(coro)

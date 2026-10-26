"""启动独立服务；仅监听本机，无需配置令牌。"""
import argparse
import os
import threading
from .process_generator import ProcessGenerator
from .core import MotionService
from .server import make_server


def supervise_generator(generator, service, load, stop, *, poll_s=1.0, max_backoff_s=60.0):
    """加载推理子进程，并在它因推理超时/崩溃被关闭后自动重载。

    ProcessGenerator 在一次推理超时后会永久关闭子进程；没有这层看护时服务会一直停在
    health.state=loading，直到有人手动重启窗口。
    """
    backoff = 2.0
    while not stop.is_set():
        inner = generator.inner
        if inner is not None and inner.ready:
            stop.wait(poll_s)
            continue
        if inner is not None:
            print("动作模型子进程已退出（推理超时或崩溃），自动重新加载…", flush=True)
            generator.inner = None
            try:
                inner.close()
            except Exception:
                pass
        try:
            fresh = load()
        except Exception as exc:
            with service.lock:
                service.error = type(exc).__name__
            print(f"动作模型加载失败：{type(exc).__name__}，{backoff:.0f} 秒后重试", flush=True)
            stop.wait(backoff)
            backoff = min(max_backoff_s, backoff * 2)
            continue
        generator.frames, generator.fps = fresh.frames, fresh.fps
        generator.inner = fresh
        with service.lock:
            service.error = None
        backoff = 2.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=2346)
    parser.add_argument("--device", default="xpu")
    parser.add_argument("--text-device", default="xpu")
    parser.add_argument("--checkpoints", default=os.environ.get("CHECKPOINTS_DIR"))
    parser.add_argument("--startup-timeout", type=float, default=420.)
    args = parser.parse_args()
    if not 0 < args.startup_timeout <= 3600:
        parser.error("模型启动等待时间必须大于0且不超过3600秒")
    class LoadingGenerator:
        frames = 40
        fps = 20
        inner = None
        @property
        def ready(self):return self.inner is not None and self.inner.ready
        def generate(self, *values):
            return self.inner.generate(*values)
        def generate_candidate(self,*values):
            return self.inner.generate_candidate(*values)
        def reseed(self,seed):
            return self.inner.reseed(seed)
        def history_at(self,*values):
            return self.inner.history_at(*values)
    generator = LoadingGenerator()
    service = MotionService(generator)
    server = make_server(service, port=args.port)
    service.start()
    def load():
        inner = ProcessGenerator(startup_timeout=args.startup_timeout, device=args.device, text_device=args.text_device, checkpoints=args.checkpoints,
            model_name=os.environ.get("YUI_MOTION_MODEL", "core40"), graph_dir=os.environ.get("YUI_MOTION_GRAPH_DIR"),
            continuous_graph=os.environ.get("YUI_MOTION_CONTINUOUS_GRAPH","1")=="1")
        print(f"模型加载 {inner.load_s:.2f} 秒，图预热 {inner.warmup_s:.2f} 秒，完整执行预热 {inner.execution_warmup_s:.2f} 秒；等待执行桥提供世界绑定。", flush=True)
        print("持续会话图重放="+str(os.environ.get("YUI_MOTION_CONTINUOUS_GRAPH","1")=="1"),flush=True)
        return inner
    stop = threading.Event()
    threading.Thread(target=supervise_generator, args=(generator, service, load, stop), name="ardy-loader", daemon=True).start()
    print(f"ARDY 动作服务端口 {args.port}；加载期间 health.ready=false。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        service.close()
        if generator.inner is not None:generator.inner.close()


if __name__ == "__main__":
    main()

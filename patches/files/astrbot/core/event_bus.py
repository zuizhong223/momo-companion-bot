"""事件总线, 用于处理事件的分发和处理
事件总线是一个异步队列, 用于接收各种消息事件, 并将其发送到Scheduler调度器进行处理
其中包含了一个无限循环的调度函数, 用于从事件队列中获取新的事件, 并创建一个新的异步任务来执行管道调度器的处理逻辑

class:
    EventBus: 事件总线, 用于处理事件的分发和处理

工作流程:
1. 维护一个异步队列, 来接受各种消息事件
2. 无限循环的调度函数, 从事件队列中获取新的事件, 打印日志并创建一个新的异步任务来执行管道调度器的处理逻辑
"""

import asyncio

from astrbot.core.utils.paths import ops_dir
import os
from asyncio import Queue
from collections.abc import Callable

from astrbot.core import logger
from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
from astrbot.core.config import AstrBotConfig
from astrbot.core.pipeline.message_merge import SessionMerger
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.platform.message_type import MessageType

from .platform import AstrMessageEvent

UNIQUE_SESSION_ID_BUILDERS: dict[str, Callable[[AstrMessageEvent], str | None]] = {    "aiocqhttp": lambda e: f"{e.get_sender_id()}_{e.get_group_id()}",
    "slack": lambda e: f"{e.get_sender_id()}_{e.get_group_id()}",
    "dingtalk": lambda e: e.get_sender_id(),
    "qq_official": lambda e: f"{e.get_sender_id()}_{e.get_group_id()}",
    "qq_official_webhook": lambda e: f"{e.get_sender_id()}_{e.get_group_id()}",
    "lark": lambda e: f"{e.get_sender_id()}%{e.get_group_id()}",
    "misskey": lambda e: f"{e.get_session_id()}_{e.get_sender_id()}",
    "matrix": lambda e: f"{e.get_sender_id()}_{e.get_group_id() or e.get_session_id()}",
}


_RECOVERED = False


OPS_LOG = os.environ.get("DEMO_OPS_LOG") or str(ops_dir("send_unknown.log"))


def _recover(grace_s: int = 0, tag: str = "启动") -> None:
    """补写"发出去了但历史没写"的主动消息，并把卡住的 sending 标 unknown。

    P0-5：启动时 grace=0（进程刚起，不可能有在途请求），之后每 10 分钟再跑一次。
    """
    try:
        from astrbot.core.utils.send_ledger import get_ledger

        res = get_ledger().recover(ops_log=OPS_LOG, grace_s=grace_s)
        if any(res.values()):
            logger.info(f"[发送台账] {tag}恢复: {res}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[发送台账] {tag}恢复失败（不影响启动）: {e}")


def _recover_once() -> None:
    global _RECOVERED  # noqa: PLW0603
    if _RECOVERED:
        return
    _RECOVERED = True
    _recover(grace_s=0, tag="启动")


async def _recover_loop(interval_s: int = 600) -> None:
    """周期性恢复：快速重启留下的 sending 也能在 10 分钟内结案。"""
    while True:
        await asyncio.sleep(interval_s)
        try:
            _recover(grace_s=300, tag="周期")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[发送台账] 周期恢复异常: {e}")


class EventBus:
    """用于处理事件的分发和处理"""
    def __init__(
        self,
        event_queue: Queue,
        pipeline_scheduler_mapping: dict[str, PipelineScheduler],
        astrbot_config_mgr: AstrBotConfigManager,
    ) -> None:
        self.event_queue = event_queue  # 事件队列
        # abconf uuid -> scheduler
        self.pipeline_scheduler_mapping = pipeline_scheduler_mapping
        self.astrbot_config_mgr = astrbot_config_mgr
        # 持有正在执行的 pipeline 任务的强引用, 防止 task 在 pending 状态被 GC 回收
        self._pending_tasks: set[asyncio.Task] = set()
        # 连发合并 + 按会话串行化（P0-3）
        self.merger: SessionMerger | None = None
        # 生活层开关是**环境变量**，手工启动容易漏——启动时打一行，避免静默关闭
        try:
            from astrbot.core.utils import life_state as _life

            _on, _shadow = _life.enabled(), _life.shadow()
            logger.info(f"[生活层] 启动状态 enabled={_on} shadow={_shadow} "
                        f"（DEMO_LIFE={os.environ.get('DEMO_LIFE', '')!r} "
                        f"SHADOW={os.environ.get('DEMO_LIFE_SHADOW', '')!r}）")
            if not _on:
                logger.warning("[生活层] 未启用：这次启动不会推进日程/记录冲突")
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"[生活层] 状态检查失败: {_e}")

        # 发送台账的启动恢复（P0-B）：只在第一次构造 EventBus 时跑
        _recover_once()
        try:
            mc = (
                astrbot_config_mgr.default_conf.get("platform_settings", {}).get(
                    "message_merge", {}
                )
                or {}
            )
            if mc.get("enable", True):
                self.merger = SessionMerger(
                    self._execute,
                    window_s=float(mc.get("window_s", 1.6)),
                    max_wait_s=float(mc.get("max_wait_s", 6.0)),
                )
                logger.info(
                    "连发合并已启用: window=%.1fs max_wait=%.1fs",
                    self.merger.window_s,
                    self.merger.max_wait_s,
                )
            else:
                logger.info("连发合并已按配置关闭")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"初始化连发合并失败，退回逐条处理: {e}")

    async def dispatch(self) -> None:
        if not getattr(self, "_recover_task", None):
            try:
                self._recover_task = asyncio.create_task(_recover_loop())
                self._pending_tasks.add(self._recover_task)
                self._recover_task.add_done_callback(self._pending_tasks.discard)
            except Exception:  # noqa: BLE001
                self._recover_task = None
        while True:
            event: AstrMessageEvent = await self.event_queue.get()
            if self.merger is not None:
                try:
                    if self.merger.submit(event):
                        continue
                except Exception as e:  # noqa: BLE001
                    logger.error(f"连发合并异常，退回立即处理: {e}", exc_info=True)
                    self.merger = None
            self._spawn(event)

    def _spawn(self, event: AstrMessageEvent) -> None:
        task = asyncio.create_task(self._execute(event))
        self._pending_tasks.add(task)
        task.add_done_callback(self._on_task_done)

    async def _execute(self, event: AstrMessageEvent) -> None:
        conf_info = self.astrbot_config_mgr.get_conf_info(event.unified_msg_origin)
        conf_id = conf_info["id"]
        conf_name = conf_info.get("name") or conf_id
        self._print_event(event, conf_name)
        scheduler = self.pipeline_scheduler_mapping.get(conf_id)
        if not scheduler:
            logger.error(
                f"PipelineScheduler not found for id: {conf_id}, event ignored."
            )
            return
        self._apply_session_isolation(event, scheduler.ctx.astrbot_config)
        await scheduler.execute(event)

    def _apply_session_isolation(
        self,
        event: AstrMessageEvent,
        config: AstrBotConfig,
    ) -> None:
        """Finalize the session before pipeline registration and stop lookup.

        Args:
            event: Incoming event whose original UMO was used to select a profile.
            config: Selected profile's configuration controlling session isolation.
        """
        event.set_extra("_session_isolated", False)
        platform_settings = config.get("platform_settings", {})
        if platform_settings.get("unique_session", False) and (
            event.message_obj.type == MessageType.GROUP_MESSAGE
        ):
            builder = UNIQUE_SESSION_ID_BUILDERS.get(event.get_platform_name())
            sid = builder(event) if builder else None
            if sid:
                event.session_id = sid
                event.set_extra("_session_isolated", True)

    def _on_task_done(self, task: asyncio.Task) -> None:
        """pipeline 任务结束回调: 移除强引用并暴露未捕获的异常"""
        self._pending_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Pipeline task failed.", exc_info=exc)

    def _print_event(self, event: AstrMessageEvent, conf_name: str) -> None:
        """用于记录事件信息

        Args:
            event (AstrMessageEvent): 事件对象

        """
        log_extra = {
            "category": "user_chat",
            "platform_id": event.get_platform_id(),
            "platform_type": event.get_platform_name(),
        }
        # 如果有发送者名称: [平台名] 发送者名称/发送者ID: 消息概要
        if event.get_sender_name():
            logger.info(
                f"[{conf_name}] [{event.get_platform_id()}({event.get_platform_name()})] {event.get_sender_name()}/{event.get_sender_id()}: {event.get_message_outline()}",
                extra=log_extra,
            )
        # 没有发送者名称: [平台名] 发送者ID: 消息概要
        else:
            logger.info(
                f"[{conf_name}] [{event.get_platform_id()}({event.get_platform_name()})] {event.get_sender_id()}: {event.get_message_outline()}",
                extra=log_extra,
            )

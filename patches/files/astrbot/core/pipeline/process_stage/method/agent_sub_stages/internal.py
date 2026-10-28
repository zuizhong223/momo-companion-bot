"""本地 Agent 模式的 LLM 调用 Stage"""

import asyncio
import base64
import json
from collections.abc import AsyncGenerator
from dataclasses import replace

from astrbot.core import db_helper, logger
from astrbot.core.agent.message import (
    CheckpointData,
    CheckpointMessageSegment,
    Message,
    TextPart,
    dump_messages_with_checkpoints,
)
from astrbot.core.agent.response import AgentStats
from astrbot.core.astr_main_agent import (
    LLM_ERROR_MESSAGE_EXTRA_KEY,
    MainAgentBuildConfig,
    MainAgentBuildResult,
    _matches_provider_wake_prefix,
    _provider_supports_modality,
    _set_llm_error_message,
    build_main_agent,
)
from astrbot.core.config.agent_runner import resolve_context_compression_config
from astrbot.core.message.components import File, Image, Record, Reply, Video
from astrbot.core.message.message_event_result import (
    MessageChain,
    MessageEventResult,
    ResultContentType,
)
from astrbot.core.persona_error_reply import (
    extract_persona_custom_error_message_from_event,
)
from astrbot.core.pipeline.stage import Stage
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import (
    LLMResponse,
    ProviderRequest,
)
from astrbot.core.star.session_llm_manager import SessionServiceManager
from astrbot.core.star.star_handler import EventType
from astrbot.core.utils.image_input import prepare_request_images
from astrbot.core.utils.history_sanitize import strip_inline_media
from astrbot.core.utils.media_utils import normalize_model_image_max_size
from astrbot.core.utils.metrics import Metric
from astrbot.core.utils.session_lock import session_lock_manager

from .....astr_agent_run_util import AgentRunner, run_agent, run_live_agent
from ....context import PipelineContext, call_event_hook
from ...follow_up import (
    FollowUpCapture,
    finalize_follow_up_capture,
    prepare_follow_up_capture,
    register_active_runner,
    try_capture_follow_up,
    unregister_active_runner,
)


async def _prepare_file_attachments(event: AstrMessageEvent) -> None:
    """Download file attachments before acquiring the session lock.

    Args:
        event: Incoming event whose direct and quoted files should be prepared.

    Returns:
        None.
    """
    for component in event.message_obj.message:
        if isinstance(component, File):
            await component.get_file()
        elif isinstance(component, Reply) and component.chain:
            for reply_component in component.chain:
                if isinstance(reply_component, File):
                    await reply_component.get_file()


def _plain_text(content) -> str:
    """Extract a comparable plain-text key from a message content (str/list)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
    return ""


def bind_delivered_text(messages, delivered_text: str) -> int:
    """R04：把最后一条 assistant（无 tool_calls）的文本换成实际送达/念出的那句。

    返回替换条数（0/1）。找不到合适消息时不动，绝不改带 tool_calls 的消息。
    """
    if not delivered_text:
        return 0
    for m in reversed(messages):
        if getattr(m, "role", None) != "assistant":
            continue
        if getattr(m, "tool_calls", None):
            return 0
        content = getattr(m, "content", None)
        if isinstance(content, str):
            if content.strip() == delivered_text.strip():
                return 0
            m.content = delivered_text
            return 1
        if isinstance(content, list):
            new_parts = []
            replaced = False
            for part in content:
                d = part if isinstance(part, dict) else None
                if d is None and hasattr(part, "model_dump"):
                    try:
                        d = part.model_dump()
                    except Exception:  # noqa: BLE001
                        d = None
                if isinstance(d, dict) and d.get("type") == "text":
                    if not replaced:
                        new_parts.append({"type": "text", "text": delivered_text})
                        replaced = True
                    continue
                new_parts.append(part)
            if replaced:
                m.content = new_parts
                return 1
        return 0
    return 0


def rebuild_delivered_history(messages, cut: int, delivered_text: str):
    """P0/A1：部分送达时按已送达文本重建本轮 assistant，返回 (messages, removed)。"""
    kept = []
    removed = 0
    for i, m in enumerate(messages):
        if (i >= cut and m.role == "assistant"
                and not getattr(m, "tool_calls", None)):
            removed += 1
            continue
        kept.append(m)
    kept.append(Message(role="assistant", content=[TextPart(text=delivered_text)]))
    return kept, removed


class InternalAgentSubStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        conf = ctx.astrbot_config
        settings = conf["provider_settings"]
        runner_config = conf["agent_runner"]["config"]
        model_config = runner_config["model"]
        persona_config = runner_config["persona"]
        compression_config = runner_config["compression"]
        misc_config = runner_config["misc"]
        self.streaming_response: bool = settings["streaming_response"]
        self.unsupported_streaming_strategy: str = settings[
            "unsupported_streaming_strategy"
        ]
        self.max_step: int = misc_config.get("max_steps", 128)
        self.tool_call_timeout: int = misc_config.get("tool_call_timeout", 120)
        self.tool_schema_mode: str = misc_config.get("tool_schema_mode", "full")
        if self.tool_schema_mode not in ("skills_like", "full"):
            logger.warning(
                "Unsupported tool_schema_mode: %s, fallback to skills_like",
                self.tool_schema_mode,
            )
            self.tool_schema_mode = "full"
        if isinstance(self.max_step, bool):  # workaround: #2622
            self.max_step = 128
        self.show_tool_use: bool = settings.get("show_tool_use_status", True)
        self.show_tool_call_result: bool = settings.get("show_tool_call_result", False)
        self.buffer_intermediate_messages: bool = settings.get(
            "buffer_intermediate_messages",
            False,
        )
        self.show_reasoning = settings.get("display_reasoning_text", False)
        self.sanitize_context_by_modalities: bool = misc_config.get(
            "sanitize_context_by_modalities",
            False,
        )
        self.kb_agentic_mode: bool = conf.get("kb_agentic_mode", False)

        file_extract_conf: dict = settings.get("file_extract", {})
        self.file_extract_enabled: bool = file_extract_conf.get("enable", False)
        self.file_extract_prov: str = file_extract_conf.get("provider", "moonshotai")
        self.file_extract_msh_api_key: str = file_extract_conf.get(
            "moonshotai_api_key", ""
        )

        self.llm_safety_mode = persona_config.get("safety_mode", True)
        self.safety_mode_strategy = persona_config.get(
            "safety_mode_strategy", "system_prompt"
        )

        self.computer_use_runtime = settings.get("computer_use_runtime", "none")
        self.sandbox_cfg = settings.get("sandbox", {})

        # Proactive capability configuration
        proactive_cfg = settings.get("proactive_capability", {})
        self.add_cron_tools = proactive_cfg.get("add_cron_tools", True)

        self.conv_manager = ctx.plugin_manager.context.conversation_manager

        self.main_agent_cfg = MainAgentBuildConfig(
            tool_call_timeout=self.tool_call_timeout,
            tool_schema_mode=self.tool_schema_mode,
            sanitize_context_by_modalities=self.sanitize_context_by_modalities,
            kb_agentic_mode=self.kb_agentic_mode,
            file_extract_enabled=self.file_extract_enabled,
            file_extract_prov=self.file_extract_prov,
            file_extract_msh_api_key=self.file_extract_msh_api_key,
            **resolve_context_compression_config(compression_config),
            llm_safety_mode=self.llm_safety_mode,
            safety_mode_strategy=self.safety_mode_strategy,
            computer_use_runtime=self.computer_use_runtime,
            sandbox_cfg=self.sandbox_cfg,
            add_cron_tools=self.add_cron_tools,
            provider_settings={
                **settings,
                "default_personality": persona_config.get("persona_id", "default"),
            },
            fallback_provider_ids=model_config.get("fallback_provider_ids", []),
            request_max_retries=model_config.get("request_max_retries", 5),
            subagent_orchestrator=conf.get("subagent_orchestrator", {}),
            timezone=self.ctx.plugin_manager.context.get_config().get("timezone"),
            max_quoted_fallback_images=settings.get("max_quoted_fallback_images", 20),
        )

    async def _send_llm_error_message(
        self, event: AstrMessageEvent, message: object
    ) -> None:
        await event.send(MessageChain().message(str(message)))

    async def process(
        self, event: AstrMessageEvent, provider_wake_prefix: str
    ) -> AsyncGenerator[None, None]:
        follow_up_capture: FollowUpCapture | None = None
        follow_up_consumed_marked = False
        follow_up_activated = False
        typing_requested = False
        try:
            streaming_response = self.streaming_response
            if (enable_streaming := event.get_extra("enable_streaming")) is not None:
                streaming_response = bool(enable_streaming)

            show_reasoning = self.show_reasoning
            if (enable_reasoning := event.get_extra("enable_reasoning")) is not None:
                show_reasoning = bool(enable_reasoning)

            has_provider_request = event.get_extra("provider_request") is not None
            has_valid_message = bool(event.message_str and event.message_str.strip())
            has_media_content = any(
                isinstance(comp, (Image, File, Record, Video))
                for comp in event.message_obj.message
            )
            has_reply = any(
                isinstance(comp, Reply) for comp in event.message_obj.message
            )

            if (
                not has_provider_request
                and not has_valid_message
                and not has_media_content
                and not has_reply
            ):
                logger.debug("skip llm request: empty message and no provider_request")
                return

            logger.debug("ready to request llm provider")
            follow_up_capture = try_capture_follow_up(event)
            if follow_up_capture:
                (
                    follow_up_consumed_marked,
                    follow_up_activated,
                ) = await prepare_follow_up_capture(follow_up_capture)
                if follow_up_consumed_marked:
                    event.set_extra(
                        "_follow_up_captured",
                        {"target_run_id": follow_up_capture.target_run_id},
                    )
                    logger.info(
                        "Follow-up ticket already consumed, stopping processing. umo=%s, seq=%s",
                        event.unified_msg_origin,
                        follow_up_capture.ticket.seq,
                    )
                    return

            try:
                typing_requested = True
                await event.send_typing()
            except Exception:
                logger.warning("send_typing failed", exc_info=True)
            if await call_event_hook(event, EventType.OnWaitingLLMRequestEvent):
                return

            if event.get_extra(
                "provider_request"
            ) is None and not _matches_provider_wake_prefix(
                event,
                provider_wake_prefix,
            ):
                return

            await _prepare_file_attachments(event)

            async with session_lock_manager.acquire_lock(event.unified_msg_origin):
                logger.debug("acquired session lock for llm request")
                current_config = self.ctx.plugin_manager.context.get_config(
                    umo=event.unified_msg_origin
                )
                if not current_config.get("provider_settings", {}).get(
                    "enable", True
                ) or not await SessionServiceManager.should_process_llm_request(event):
                    logger.debug(
                        "LLM was disabled while waiting for the session lock; "
                        "skipping request for %s.",
                        event.unified_msg_origin,
                    )
                    return
                agent_runner: AgentRunner | None = None
                runner_registered = False
                reset_coro = None
                try:
                    build_cfg = replace(
                        self.main_agent_cfg,
                        provider_wake_prefix=provider_wake_prefix,
                        streaming_response=streaming_response,
                    )

                    plugin_context = self.ctx.plugin_manager.context
                    prepared: dict[str, dict] = {}
                    build_result: MainAgentBuildResult | None = await build_main_agent(
                        event=event,
                        plugin_context=plugin_context,
                        config=build_cfg,
                        apply_reset=False,
                        prepared_images=prepared,
                    )

                    if build_result is None:
                        if llm_error_message := event.get_extra(
                            LLM_ERROR_MESSAGE_EXTRA_KEY
                        ):
                            await self._send_llm_error_message(
                                event,
                                llm_error_message,
                            )
                        return

                    agent_runner = build_result.agent_runner
                    req = build_result.provider_request
                    provider = build_result.provider
                    reset_coro = build_result.reset_coro

                    api_base = provider.provider_config.get("api_base", "")
                    for host in decoded_blocked:
                        if host in api_base:
                            _set_llm_error_message(
                                event, "blockedProvider", api_base=api_base
                            )
                            error_message = event.get_extra(LLM_ERROR_MESSAGE_EXTRA_KEY)
                            logger.error(
                                "Provider API base %s was blocked for security reasons",
                                api_base,
                            )
                            await self._send_llm_error_message(event, error_message)
                            return

                    stream_to_general = (
                        self.unsupported_streaming_strategy == "turn_off"
                        and not event.platform_meta.support_streaming_message
                    )

                    if await call_event_hook(event, EventType.OnLLMRequestEvent, req):
                        return

                    options = build_cfg.provider_settings.get(
                        "image_compress_options", {}
                    )
                    await prepare_request_images(
                        req,
                        event,
                        max_size=normalize_model_image_max_size(
                            options.get("max_size")
                            if isinstance(options, dict)
                            else None
                        ),
                        prepared=prepared,
                        supports_image=_provider_supports_modality(provider, "image"),
                    )
                    # apply reset
                    if reset_coro:
                        await reset_coro
                        reset_coro = None

                    register_active_runner(event.unified_msg_origin, agent_runner)
                    runner_registered = True
                    action_type = event.get_extra("action_type")

                    event.trace.record(
                        "astr_agent_prepare",
                        system_prompt=req.system_prompt,
                        tools=req.func_tool.names() if req.func_tool else [],
                        stream=streaming_response,
                        chat_provider={
                            "id": provider.provider_config.get("id", ""),
                            "model": provider.get_model(),
                        },
                    )

                    # 检测 Live Mode
                    if action_type == "live":
                        # Live Mode: 使用 run_live_agent
                        logger.info(
                            "[Internal Agent] Live Mode detected; enabling TTS processing."
                        )

                        # 获取 TTS Provider
                        tts_provider = await self.ctx.plugin_manager.context.get_using_tts_provider_async(
                            event.unified_msg_origin
                        )

                        if not tts_provider:
                            logger.warning(
                                "[Live Mode] No TTS provider is configured; using "
                                "standard streaming mode."
                            )

                        # 使用 run_live_agent，总是使用流式响应
                        event.set_result(
                            MessageEventResult()
                            .set_result_content_type(ResultContentType.STREAMING_RESULT)
                            .set_async_stream(
                                run_live_agent(
                                    agent_runner,
                                    tts_provider,
                                    self.max_step,
                                    self.show_tool_use,
                                    self.show_tool_call_result,
                                    show_reasoning=show_reasoning,
                                    buffer_intermediate_messages=self.buffer_intermediate_messages,
                                ),
                            ),
                        )
                        yield

                        # 保存历史记录
                        if agent_runner.done() and (
                            not event.is_stopped() or agent_runner.was_aborted()
                        ):
                            await self._save_to_history(
                                event,
                                req,
                                agent_runner.get_final_llm_resp(),
                                agent_runner.run_context.messages,
                                agent_runner.stats,
                                user_aborted=agent_runner.was_aborted(),
                            )

                    elif streaming_response and not stream_to_general:
                        # 流式响应
                        event.set_result(
                            MessageEventResult()
                            .set_result_content_type(ResultContentType.STREAMING_RESULT)
                            .set_async_stream(
                                run_agent(
                                    agent_runner,
                                    self.max_step,
                                    self.show_tool_use,
                                    self.show_tool_call_result,
                                    show_reasoning=show_reasoning,
                                    buffer_intermediate_messages=self.buffer_intermediate_messages,
                                ),
                            ),
                        )
                        yield
                        if agent_runner.done():
                            if final_llm_resp := agent_runner.get_final_llm_resp():
                                if final_llm_resp.completion_text:
                                    chain = (
                                        MessageChain()
                                        .message(final_llm_resp.completion_text)
                                        .chain
                                    )
                                elif final_llm_resp.result_chain:
                                    chain = final_llm_resp.result_chain.chain
                                else:
                                    chain = MessageChain().chain
                                event.set_result(
                                    MessageEventResult(
                                        chain=chain,
                                        result_content_type=ResultContentType.STREAMING_FINISH,
                                    ),
                                )
                    else:
                        async for _ in run_agent(
                            agent_runner,
                            self.max_step,
                            self.show_tool_use,
                            self.show_tool_call_result,
                            stream_to_general,
                            show_reasoning=show_reasoning,
                            buffer_intermediate_messages=self.buffer_intermediate_messages,
                        ):
                            yield

                    final_resp = agent_runner.get_final_llm_resp()

                    event.trace.record(
                        "astr_agent_complete",
                        stats=agent_runner.stats.to_dict(),
                        resp=final_resp.completion_text if final_resp else None,
                    )

                    asyncio.create_task(
                        _record_internal_agent_stats(
                            event,
                            req,
                            agent_runner,
                            final_resp,
                        )
                    )

                    # 检查事件是否被停止，如果被停止则不保存历史记录
                    if not event.is_stopped() or agent_runner.was_aborted():
                        await self._save_to_history(
                            event,
                            req,
                            final_resp,
                            agent_runner.run_context.messages,
                            agent_runner.stats,
                            user_aborted=agent_runner.was_aborted(),
                        )

                    asyncio.create_task(
                        Metric.upload(
                            llm_tick=1,
                            model_name=agent_runner.provider.get_model(),
                            provider_type=agent_runner.provider.meta().type,
                        ),
                    )
                finally:
                    if reset_coro:
                        reset_coro.close()
                    if runner_registered and agent_runner is not None:
                        unregister_active_runner(event.unified_msg_origin, agent_runner)

        except Exception as e:
            logger.error(f"Error occurred while processing agent: {e}")
            custom_error_message = extract_persona_custom_error_message_from_event(
                event
            )
            error_text = custom_error_message or (
                f"Error occurred while processing agent request: {e}"
            )
            await event.send(MessageChain().message(error_text))
        finally:
            if typing_requested:
                try:
                    await event.stop_typing()
                except Exception:
                    logger.warning("stop_typing failed", exc_info=True)
            if follow_up_capture:
                await finalize_follow_up_capture(
                    follow_up_capture,
                    activated=follow_up_activated,
                    consumed_marked=follow_up_consumed_marked,
                )

    async def _save_to_history(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        llm_response: LLMResponse | None,
        all_messages: list[Message],
        runner_stats: AgentStats | None,
        user_aborted: bool = False,
    ) -> None:
        if not req or not req.conversation:
            return

        # P0-B（被动侧）：只有确认送达的回复才能写进长期历史。
        # 发送失败/未知/空时，模型这一轮生成的 assistant 文本仍留在本轮上下文里，
        # 但不能变成“机器人记得自己说过”；否则下一轮会引用一条用户从没收到的话。
        delivery_state = event.get_extra("delivery_state")
        # R04：受台账管理的路径（QQ）必须有明确 sent 证据才算送达；
        # delivery_state=None 只对"没有 delivery_token 的旧路径"放行。
        managed_delivery = bool(event.get_extra("delivery_token"))
        delivery_ok = (delivery_state == "sent") or (
            delivery_state is None and not managed_delivery)

        messages_to_save: list[Message] = []
        skipped_initial_system = False
        for message in all_messages:
            if message.role == "system" and not skipped_initial_system:
                skipped_initial_system = True
                continue
            if message.role in ["assistant", "user"] and message._no_save:
                continue
            messages_to_save.append(message)

        # R04：历史写"实际送达/念出的那句"，不是模型原稿。
        # P0/A1：部分成功时 delivery_text 里已经有前段；不能因为末段失败就把它一起删掉。
        _dt = str(event.get_extra("delivery_text") or "").strip()

        # 找到本轮 user 的边界；之后才是本轮 assistant 草稿。
        cut = 0
        for i in range(len(messages_to_save) - 1, -1, -1):
            if messages_to_save[i].role == "user":
                cut = i + 1
                break

        if cut > 0 and _dt:
            # 只保留确认送达的文本：删掉本轮所有非 tool_calls 的 assistant 草稿，
            # 再补一条 delivery_text。成功/部分成功都走这里，避免多段重复。
            messages_to_save, removed = rebuild_delivered_history(
                messages_to_save, cut, _dt)
            logger.info(
                "delivery_state=%s，按已送达文本重建本轮 assistant（丢弃 %d 条草稿，task=%s）",
                delivery_state, removed, event.get_extra("delivery_token"),
            )
        elif not delivery_ok:
            # 没有送达证据：按本轮边界清掉所有带正文的 assistant 草稿，
            # 保留 tool_calls 消息以免破坏工具配对。
            kept = []
            removed = 0
            for i, m in enumerate(messages_to_save):
                if (i >= cut and m.role == "assistant"
                        and not getattr(m, "tool_calls", None)):
                    removed += 1
                    continue
                kept.append(m)
            if removed:
                messages_to_save = kept
                logger.info(
                    "delivery_state=%s，丢弃本轮 %d 条未送达 assistant 历史（task=%s）",
                    delivery_state, removed, event.get_extra("delivery_token"),
                )
        elif delivery_ok and _dt:
            # 没有可识别的 user 边界时，退回原来的“替换最后一条”逻辑。
            try:
                if bind_delivered_text(messages_to_save, _dt):
                    logger.info("历史已绑定实际送达文本: %s", _dt[:24])
            except Exception as _e:  # noqa: BLE001
                logger.warning("绑定送达文本失败: %s", _e)

        checkpoint_id = event.get_extra("llm_checkpoint_id")
        has_checkpoint = isinstance(checkpoint_id, str) and bool(checkpoint_id)
        # R09/A2：来源身份跟着本条消息对象走，不再按 assistant 正文回填。
        # R1：只给本轮实际保留/新建的 assistant 绑定来源；全失败/无当前消息时
        # 不能把上一轮回答的来源改成本轮最后一次尝试。
        _sent_tokens = event.get_extra("delivery_tokens") or []
        if isinstance(_sent_tokens, str):
            _sent_tokens = [_sent_tokens]
        _sent_tokens = [str(x) for x in _sent_tokens if x]
        _src_type = str(
            event.get_extra("source_type")
            or ("proactive" if event.get_extra("proactive") else "model_output")
        )
        _event_id = event.get_extra("event_id")
        if not _event_id:
            try:
                _event_id = str(event.message_obj.message_id)
            except Exception:  # noqa: BLE001
                _event_id = None
        if _dt and _sent_tokens:
            _target = None
            for _i in range(len(messages_to_save) - 1, -1, -1):
                _m = messages_to_save[_i]
                if (cut > 0 and _i < cut):
                    break
                if (_m.role == "assistant" and _m.content
                        and not getattr(_m, "tool_calls", None)):
                    _target = _m
                    break
            if _target is not None:
                _target.source_type = _src_type
                _target.source_ref = "|".join(_sent_tokens)
                _target.event_id = str(_event_id) if _event_id else None
        message_to_save = dump_messages_with_checkpoints(messages_to_save)
        # 费用治理：历史里不保留内联 base64 媒体（会把每轮请求撑到几十万 token）
        message_to_save, stripped = strip_inline_media(message_to_save)
        if stripped:
            logger.info(f"history sanitized: {stripped} inline media part(s) replaced")
        if not user_aborted and (
            llm_response is None or llm_response.role != "assistant"
        ):
            if has_checkpoint:
                message_to_save.append(
                    CheckpointMessageSegment(
                        content=CheckpointData(id=checkpoint_id),
                    ).model_dump()
                )
            if has_checkpoint or (llm_response is None and req.tool_calls_result):
                token_usage = None if has_checkpoint else req.conversation.token_usage
                await self.conv_manager.update_conversation(
                    event.unified_msg_origin,
                    req.conversation.cid,
                    history=message_to_save,
                    token_usage=token_usage,
                )
            return

        if llm_response and llm_response.role != "assistant":
            if not user_aborted:
                return
            llm_response = LLMResponse(
                role="assistant",
                completion_text=llm_response.completion_text or "",
            )
        elif llm_response is None:
            llm_response = LLMResponse(role="assistant", completion_text="")

        if (
            not llm_response.completion_text
            and not req.tool_calls_result
            and not user_aborted
        ):
            logger.debug("The LLM response is empty; not saving a record.")
            return

        if isinstance(checkpoint_id, str) and checkpoint_id:
            message_to_save.append(
                CheckpointMessageSegment(
                    content=CheckpointData(id=checkpoint_id),
                ).model_dump()
            )

        # if user_aborted:
        #     message_to_save.append(
        #         Message(
        #             role="assistant",
        #             content="[User aborted this request. Partial output before abort was preserved.]",
        #         ).model_dump()
        #     )

        token_usage = None
        if runner_stats:
            # token_usage = runner_stats.token_usage.total
            token_usage = llm_response.usage.total if llm_response.usage else None

        await self.conv_manager.update_conversation(
            event.unified_msg_origin,
            req.conversation.cid,
            history=message_to_save,
            token_usage=token_usage,
        )


# we prevent astrbot from connecting to known malicious hosts
# these hosts are base64 encoded
BLOCKED = {"dGZid2h2d3IuY2xvdWQuc2VhbG9zLmlv", "a291cmljaGF0"}
decoded_blocked = [base64.b64decode(b).decode("utf-8") for b in BLOCKED]


async def _record_internal_agent_stats(
    event: AstrMessageEvent,
    req: ProviderRequest | None,
    agent_runner: AgentRunner | None,
    final_resp: LLMResponse | None,
) -> None:
    """Persist internal agent stats without affecting the user response flow."""
    if agent_runner is None:
        return

    provider = agent_runner.provider
    stats = agent_runner.stats
    if provider is None or stats is None:
        return

    try:
        provider_config = getattr(provider, "provider_config", {}) or {}
        conversation_id = (
            req.conversation.cid
            if req is not None and req.conversation is not None
            else None
        )

        if agent_runner.was_aborted():
            status = "aborted"
        elif final_resp is not None and final_resp.role == "err":
            status = "error"
        else:
            status = "completed"

        await db_helper.insert_provider_stat(
            umo=event.unified_msg_origin,
            conversation_id=conversation_id,
            provider_id=provider_config.get("id", "") or provider.meta().id,
            provider_model=provider.get_model(),
            status=status,
            stats=stats.to_dict(),
            agent_type="internal",
        )
    except Exception as e:
        logger.warning("Persist provider stats failed: %s", e, exc_info=True)

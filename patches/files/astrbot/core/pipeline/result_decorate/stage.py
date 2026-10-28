import random
import re
import time
import traceback
from collections.abc import AsyncGenerator

from astrbot.core import file_token_service, html_renderer, logger
from astrbot.core.message.components import At, Image, Json, Node, Plain, Record, Reply
from astrbot.core.message.message_event_result import ResultContentType
from astrbot.core.pipeline.content_safety_check.stage import ContentSafetyCheckStage
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.message_type import MessageType
from astrbot.core.star.session_llm_manager import SessionServiceManager
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import EventType, star_handlers_registry

from ..context import PipelineContext
from ..stage import Stage, register_stage, registered_stages


def clean_tts_text(text: str) -> str:
    """TTS 前/合成后统一清标点：去省略号、句号、逗号、换行/多余空格。"""
    t = re.sub(r"[…]+|\.{3,}", " ", text or "")
    t = re.sub(r"[。]+", " ", t)
    t = re.sub(r"[，,]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


_SUPPORT_IN = re.compile(r"谢谢|辛苦了|需要帮忙|认可你|关心你|你还好吗")
_SLEEP_WORD = re.compile(r"睡|晚安|熬|困|早点休")
_RECIPROCAL = re.compile(r"不客气|我也认可你|我也关心你|认可你|关心你|加油|晚安|嗯嗯")
_RECIPROCAL_STRONG = re.compile(r"不客气|我也认可你|我也关心你|认可你|关心你")
_ACK_WORD = re.compile(r"知道啦|好欸|好耶|嗯嗯|谢谢|认可）|关心）")
_ASK_ACTIVITY = re.compile(r"在干嘛|在做什么|在哪|练了没|吃了吗|在忙什么|干什么")
_ACTIVITY_WORD = re.compile(
    r"在车上|练习|弹奏|演奏|录视频|刷视频|看视频|摸鱼|打游戏|在教室|在练习室|在吃饭|在忙|在回家|在上课|在上班|写作业"
)
# 旧配置/旧示例经常把支持追问接成“快十二点了/睡吧”。配置已改，
# 这里再加一层生成后兜底：只在“用户支持且没自己提睡”时生效。
def support_sleep_fallback(user_text: str, reply_text: str) -> str | None:
    u = (user_text or "").strip()
    r = (reply_text or "").strip()
    if not u or not r or not _SUPPORT_IN.search(u):
        return None
    if _SLEEP_WORD.search(u):
        return None
    if not _SLEEP_WORD.search(r):
        return None
    if _RECIPROCAL.search(r):
        return None
    # 两句以上/两次支持 = 追问；否则单句直接接受
    parts = [x for x in re.split(r"[。！？!?…\n]+", u) if x.strip()]
    repeats = len(_SUPPORT_IN.findall(u))
    if len(parts) >= 2 or repeats >= 2:
        return "知道啦\n我也认可你"
    if "想" in u and "喜欢" not in u:
        return "我也关心你"
    return "我也认可你"


def support_reply_fallback(user_text: str, reply_text: str) -> str | None:
    """支持回应兜底：不睡觉、不装没听见、缺接受就补第二句。"""
    u = (user_text or "").strip()
    r = (reply_text or "").strip()
    if not u or not r or not _SUPPORT_IN.search(u):
        return None
    if _SLEEP_WORD.search(u):
        return None
    sleep_fb = support_sleep_fallback(u, r)
    if sleep_fb:
        return sleep_fb
    # 模型已经接住（“想）我也关心你”）就不要再套模板，否则会把好回复改成
    # “好好好好）想吧想吧）”。只有模型回得干（嗯/好/晚安）才兜底。
    if _RECIPROCAL_STRONG.search(r):
        return None
    if _ACK_WORD.search(r) or len(r) <= 8:
        # 2026-10-28：原来是「原文 + 换行 + 我也认可你」，
        # 结果「谢谢」变成「谢谢\n我也认可你」——追加比原文更别扭。
        # 改成替换成一句，不追加。
        return "我也关心你" if ("想" in u and "喜欢" not in u) else "我也认可你"
    return None


def activity_fact_fallback(user_text: str, reply_text: str) -> str | None:
    """生活层关闭时，不在具体地点/活动上临时编；问“在干嘛”就答“没干嘛/在呢”。"""
    u = (user_text or "").strip()
    r = (reply_text or "").strip()
    if not u or not r or not _ASK_ACTIVITY.search(u):
        return None
    if not _ACTIVITY_WORD.search(r):
        return None
    try:
        from astrbot.core.utils import life_state as _ls
        if _ls.enabled():
            return None
    except Exception:  # noqa: BLE001
        pass
    return "没干嘛（"


@register_stage
class ResultDecorateStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        self.reply_prefix = ctx.astrbot_config["platform_settings"]["reply_prefix"]
        self.reply_with_mention = ctx.astrbot_config["platform_settings"][
            "reply_with_mention"
        ]
        self.reply_with_quote = ctx.astrbot_config["platform_settings"][
            "reply_with_quote"
        ]
        self.t2i_word_threshold = ctx.astrbot_config["t2i_word_threshold"]
        try:
            self.t2i_word_threshold = int(self.t2i_word_threshold)
            self.t2i_word_threshold = max(self.t2i_word_threshold, 50)
        except BaseException:
            self.t2i_word_threshold = 150
        self.t2i_strategy = ctx.astrbot_config["t2i_strategy"]
        self.t2i_use_network = self.t2i_strategy == "remote"
        self.t2i_active_template = ctx.astrbot_config["t2i_active_template"]

        self.forward_threshold = ctx.astrbot_config["platform_settings"][
            "forward_threshold"
        ]

        trigger_probability = ctx.astrbot_config["provider_tts_settings"].get(
            "trigger_probability",
            1,
        )
        try:
            self.tts_trigger_probability = max(
                0.0,
                min(float(trigger_probability), 1.0),
            )
        except (TypeError, ValueError):
            self.tts_trigger_probability = 1.0

        # 分段回复
        self.words_count_threshold = int(
            ctx.astrbot_config["platform_settings"]["segmented_reply"][
                "words_count_threshold"
            ],
        )
        self.enable_segmented_reply = ctx.astrbot_config["platform_settings"][
            "segmented_reply"
        ]["enable"]
        self.only_llm_result = ctx.astrbot_config["platform_settings"][
            "segmented_reply"
        ]["only_llm_result"]
        self.split_mode = ctx.astrbot_config["platform_settings"][
            "segmented_reply"
        ].get("split_mode", "regex")
        self.regex = ctx.astrbot_config["platform_settings"]["segmented_reply"]["regex"]
        self.split_words = ctx.astrbot_config["platform_settings"][
            "segmented_reply"
        ].get("split_words", ["。", "？", "！", "~", "…"])
        if self.split_words:
            escaped_words = sorted(
                [re.escape(word) for word in self.split_words], key=len, reverse=True
            )
            self.split_words_pattern = re.compile(
                f"(.*?({'|'.join(escaped_words)})|.+$)", re.DOTALL
            )
        else:
            self.split_words_pattern = None
        self.content_cleanup_rule = ctx.astrbot_config["platform_settings"][
            "segmented_reply"
        ]["content_cleanup_rule"]

        # exception
        self.content_safe_check_reply = ctx.astrbot_config["content_safety"][
            "also_use_in_response"
        ]
        self.content_safe_check_stage = None
        if self.content_safe_check_reply:
            for stage_cls in registered_stages:
                if stage_cls.__name__ == "ContentSafetyCheckStage":
                    self.content_safe_check_stage = stage_cls()
                    await self.content_safe_check_stage.initialize(ctx)

        provider_cfg = ctx.astrbot_config.get("provider_settings", {})
        self.show_reasoning = provider_cfg.get("display_reasoning_text", False)

    def _split_text_by_words(self, text: str) -> list[str]:
        """使用分段词列表分段文本"""
        if not self.split_words_pattern:
            return [text]

        segments = self.split_words_pattern.findall(text)
        result = []
        for seg in segments:
            if isinstance(seg, tuple):
                content = seg[0]
                if not isinstance(content, str):
                    continue
                for word in self.split_words:
                    if content.endswith(word):
                        content = content[: -len(word)]
                        break
                if content.strip():
                    result.append(content)
            elif seg and seg.strip():
                result.append(seg)
        return result if result else [text]

    async def process(
        self,
        event: AstrMessageEvent,
    ) -> None | AsyncGenerator[None, None]:
        result = event.get_result()
        if result is None or not result.chain:
            return

        if result.result_content_type == ResultContentType.STREAMING_RESULT:
            return

        is_stream = result.result_content_type == ResultContentType.STREAMING_FINISH

        # 回复时检查内容安全
        if (
            self.content_safe_check_reply
            and self.content_safe_check_stage
            and result.is_llm_result()
            and not is_stream  # 流式输出不检查内容安全
        ):
            text = ""
            for comp in result.chain:
                if isinstance(comp, Plain):
                    text += comp.text

            if isinstance(self.content_safe_check_stage, ContentSafetyCheckStage):
                async for _ in self.content_safe_check_stage.process(
                    event,
                    check_text=text,
                ):
                    yield

        # 发送消息前事件钩子
        handlers = star_handlers_registry.get_handlers_by_event_type(
            EventType.OnDecoratingResultEvent,
            plugins_name=event.plugins_name,
        )
        for handler in handlers:
            try:
                logger.debug(
                    f"hook(on_decorating_result) -> {star_map[handler.handler_module_path].name} - {handler.handler_name}",
                )
                if is_stream:
                    logger.warning(
                        "Plugins that depend on the pre-send event hook may not work "
                        "correctly when streaming output is enabled.",
                    )
                await handler.handler(event)

                if (result := event.get_result()) is None or not result.chain:
                    logger.debug(
                        "hook(on_decorating_result) -> "
                        f"{star_map[handler.handler_module_path].name} - "
                        f"{handler.handler_name} cleared the message result.",
                    )
            except BaseException:
                logger.error(traceback.format_exc())

            if event.is_stopped():
                logger.info(
                    f"{star_map[handler.handler_module_path].name} - "
                    f"{handler.handler_name} stopped event propagation.",
                )
                return

        # 流式输出不执行下面的逻辑
        if is_stream:
            return

        # 需要再获取一次。插件可能直接对 chain 进行了替换。
        result = event.get_result()
        if result is None:
            return

        # 生成后兜底：支持追问被接成“睡吧/晚安”时，替换成接受型两句。
        try:
            if result.is_llm_result() and all(isinstance(c, Plain) for c in result.chain):
                _reply = " ".join(c.text for c in result.chain if isinstance(c, Plain)).strip()
                _fb = support_reply_fallback(
                    getattr(event, "message_str", "") or "", _reply)
                if _fb:
                    logger.info(f"[支持兜底] {_reply[:24]!r} -> {_fb!r}")
                    result.chain = [Plain(_fb)]
                else:
                    try:
                        from astrbot.core.utils.comfort_hint import (
                            comfort_reply_fallback as _comfort_fb,
                        )

                        _cf = _comfort_fb(
                            getattr(event, "message_str", "") or "", _reply)
                    except Exception:  # noqa: BLE001
                        _cf = ""
                    if _cf:
                        logger.info(f"[安慰兜底] {_reply[:24]!r} -> {_cf!r}")
                        result.chain = [Plain(p) for p in _cf.split("\n") if p.strip()]
                    else:
                        try:
                            from astrbot.core.utils.daily_hint import (
                                daily_reply_fallback as _daily_fb,
                            )

                            _df = _daily_fb(
                                getattr(event, "message_str", "") or "", _reply)
                        except Exception:  # noqa: BLE001
                            _df = ""
                        if _df:
                            logger.info(f"[日常兜底] {_reply[:24]!r} -> {_df!r}")
                            result.chain = [Plain(_df)]
                        else:
                            try:
                                from astrbot.core.utils.game_hint import (
                                    game_reply_fallback as _game_fb,
                                )

                                _gf = _game_fb(
                                    getattr(event, "message_str", "") or "", _reply)
                            except Exception:  # noqa: BLE001
                                _gf = ""
                            if _gf:
                                logger.info(f"[游戏兜底] {_reply[:24]!r} -> {_gf!r}")
                                result.chain = [Plain(_gf)]
                            else:
                                _af = activity_fact_fallback(
                                    getattr(event, "message_str", "") or "", _reply)
                                if _af:
                                    logger.info(f"[事实兜底] {_reply[:24]!r} -> {_af!r}")
                                    result.chain = [Plain(_af)]
        except Exception:  # noqa: BLE001
            logger.warning("支持兜底检查失败", exc_info=True)

        if len(result.chain) > 0:
            # 回复前缀
            if self.reply_prefix:
                for comp in result.chain:
                    if isinstance(comp, Plain):
                        comp.text = self.reply_prefix + comp.text
                        break

            # 分段回复
            if self.enable_segmented_reply and event.get_platform_name() not in [
                "qq_official_webhook",
                "weixin_official_account",
                "dingtalk",
            ]:
                if (
                    self.only_llm_result and result.is_model_result()
                ) or not self.only_llm_result:
                    new_chain = []
                    for comp in result.chain:
                        if isinstance(comp, Plain):
                            if len(comp.text) > self.words_count_threshold:
                                # 不分段回复
                                new_chain.append(comp)
                                continue

                            # 根据 split_mode 选择分段方式
                            if self.split_mode == "words":
                                split_response = self._split_text_by_words(comp.text)
                            else:  # regex 模式
                                try:
                                    split_response = re.findall(
                                        self.regex,
                                        comp.text,
                                        re.DOTALL | re.MULTILINE,
                                    )
                                except re.error:
                                    logger.error(
                                        "Invalid segmented-reply regular expression; "
                                        "using the default segmentation method: "
                                        f"{traceback.format_exc()}",
                                    )
                                    split_response = re.findall(
                                        r".*?[。？！~…]+|.+$",
                                        comp.text,
                                        re.DOTALL | re.MULTILINE,
                                    )

                            if not split_response:
                                new_chain.append(comp)
                                continue
                            for seg in split_response:
                                if self.content_cleanup_rule:
                                    try:
                                        seg = re.sub(self.content_cleanup_rule, "", seg)
                                    except re.error:
                                        logger.error(
                                            "The segmented-reply filter expression "
                                            "failed, so the text could not be filtered: "
                                            f"{traceback.format_exc()}"
                                        )
                                        self.content_cleanup_rule = None
                                seg = seg.strip()
                                if seg:
                                    new_chain.append(Plain(seg))
                        else:
                            # 非 Plain 类型的消息段不分段
                            new_chain.append(comp)
                    result.chain = new_chain

            # TTS
            tts_provider = (
                await self.ctx.plugin_manager.context.get_using_tts_provider_async(
                    event.unified_msg_origin,
                )
            )

            should_tts = (
                bool(self.ctx.astrbot_config["provider_tts_settings"]["enable"])
                and result.is_llm_result()
                and await SessionServiceManager.should_process_tts_request(event)
                and random.random() <= self.tts_trigger_probability
                and tts_provider
            )
            if should_tts and not tts_provider:
                logger.warning(
                    f"Session {event.unified_msg_origin} has no text-to-speech "
                    "provider configured.",
                )

            show_reasoning = self.show_reasoning
            if (enable_reasoning := event.get_extra("enable_reasoning")) is not None:
                show_reasoning = bool(enable_reasoning)

            if (
                not should_tts
                and show_reasoning
                and event.get_extra("_llm_reasoning_content")
            ):
                # inject reasoning content to chain
                reasoning_content = str(event.get_extra("_llm_reasoning_content"))
                if event.get_platform_name() == "lark":
                    result.chain.insert(
                        0,
                        Json(
                            data={
                                "type": "lark_collapsible_panel_reasoning",
                                "title": "💭 Thinking",
                                "expanded": False,
                                "content": reasoning_content,
                            },
                        ),
                    )
                else:
                    result.chain.insert(
                        0, Plain(f"🤔 思考: {reasoning_content}\n\n────\n")
                    )

            if should_tts and tts_provider:
                new_chain = []
                for comp in result.chain:
                    if isinstance(comp, Plain) and len(comp.text) > 1:
                        try:
                            from astrbot.core.utils.tts_colloquial import (
                                to_speech,
                            )

                            # TTS 走的是 comp.text，不经过 send_message 的出口门禁；
                            # 先去掉换行/省略号，避免语音和历史里出现「……」和分段。
                            _tts_src = clean_tts_text(comp.text)
                            speak_text = to_speech(_tts_src or comp.text)
                            # to_speech 自己会补句号/省略号；再清一次，避免语音和历史带「。」「……」
                            speak_text = clean_tts_text(speak_text)
                            # P0：Record.text 要记最终送进 provider 的稿子，
                            # 不能只记调用 get_audio 前的 speak_text。
                            _prepare = getattr(tts_provider, "prepare_text", None)
                            if callable(_prepare):
                                try:
                                    speak_text = _prepare(speak_text)
                                except Exception:  # noqa: BLE001
                                    logger.warning(
                                        "TTS prepare_text failed; keep current text",
                                        exc_info=True,
                                    )
                            # prepare_text 可能把句号/逗号又补回来；最终送 provider 前再清一次。
                            speak_text = clean_tts_text(speak_text)
                            if not speak_text:
                                # 全是符号（比如只回了一个「）」），念不出来就发文字
                                new_chain.append(comp)
                                continue
                            logger.info(f"TTS request: {speak_text}")
                            # provider 若支持 prepared，避免把已经预处理的稿子再
                            # 处理一次；Record.text 才是真正送进 TTS 的那一份。
                            try:
                                audio_path = await tts_provider.get_audio(
                                    speak_text, prepared=True)
                            except TypeError:
                                audio_path = await tts_provider.get_audio(speak_text)
                            logger.info(f"TTS result: {audio_path}")
                            if not audio_path:
                                logger.error(
                                    "Failed to convert the message segment to speech "
                                    f"because no TTS audio file was found: {comp.text}",
                                )
                                new_chain.append(comp)
                                continue

                            use_file_service = self.ctx.astrbot_config[
                                "provider_tts_settings"
                            ]["use_file_service"]
                            callback_api_base = self.ctx.astrbot_config[
                                "callback_api_base"
                            ]
                            dual_output = self.ctx.astrbot_config[
                                "provider_tts_settings"
                            ]["dual_output"]

                            url = None
                            if use_file_service and callback_api_base:
                                token = await file_token_service.register_file(
                                    audio_path,
                                )
                                url = f"{callback_api_base}/api/file/{token}"
                                logger.debug(f"Registered: {url}")

                            new_chain.append(
                                Record(
                                    file=url or audio_path,
                                    url=url or audio_path,
                                    # A05：台账记“实际念出来的稿子”，不是打字原文；
                                    # 打字原文仍留在模型历史里，两者不要混。
                                    text=speak_text,
                                ),
                            )
                            if dual_output:
                                new_chain.append(comp)
                        except Exception:
                            logger.error(traceback.format_exc())
                            logger.error("TTS failed; sending text instead.")
                            new_chain.append(comp)
                    else:
                        new_chain.append(comp)
                result.chain = new_chain

            # 文本转图片
            elif (
                result.use_t2i_ is None and self.ctx.astrbot_config["t2i"]
            ) or result.use_t2i_:
                parts = []
                for comp in result.chain:
                    if isinstance(comp, Plain):
                        parts.append("\n\n" + comp.text)
                    else:
                        break
                plain_str = "".join(parts)
                if plain_str and len(plain_str) > self.t2i_word_threshold:
                    render_start = time.time()
                    try:
                        url = await html_renderer.render_t2i(
                            plain_str,
                            return_url=True,
                            use_network=self.t2i_use_network,
                            template_name=self.t2i_active_template,
                            endpoint=self.ctx.astrbot_config.get("t2i_endpoint")
                            or None,
                        )
                    except BaseException:
                        logger.error(
                            "Text-to-image rendering failed; sending text instead."
                        )
                        return
                    if time.time() - render_start > 3:
                        logger.warning(
                            "Text-to-image rendering took more than 3 seconds. Disable "
                            "text-to-image mode in the WebUI if it is too slow.",
                        )
                    if url:
                        if url.startswith("http"):
                            result.chain = [Image.fromURL(url)]
                        elif (
                            self.ctx.astrbot_config["t2i_use_file_service"]
                            and self.ctx.astrbot_config["callback_api_base"]
                        ):
                            token = await file_token_service.register_file(url)
                            url = f"{self.ctx.astrbot_config['callback_api_base']}/api/file/{token}"
                            logger.debug(f"Registered: {url}")
                            result.chain = [Image.fromURL(url)]
                        else:
                            result.chain = [Image.fromFileSystem(url)]

            # 触发转发消息
            if event.get_platform_name() == "aiocqhttp":
                word_cnt = 0
                for comp in result.chain:
                    if isinstance(comp, Plain):
                        word_cnt += len(comp.text)
                if word_cnt > self.forward_threshold:
                    node = Node(
                        uin=event.get_self_id(),
                        name="AstrBot",
                        content=[*result.chain],
                    )
                    result.chain = [node]

            # at 回复 / 引用回复仅适用于纯文本或图文消息
            can_decorate = all(
                isinstance(item, (Plain, Image)) for item in result.chain
            )
            if can_decorate:
                # at 回复
                if (
                    self.reply_with_mention
                    and event.get_message_type() != MessageType.FRIEND_MESSAGE
                ):
                    result.chain.insert(
                        0,
                        At(qq=event.get_sender_id(), name=event.get_sender_name()),
                    )
                    if len(result.chain) > 1 and isinstance(result.chain[1], Plain):
                        result.chain[1].text = "\n" + result.chain[1].text

                # 引用回复
                if self.reply_with_quote:
                    result.chain.insert(0, Reply(id=event.message_obj.message_id))

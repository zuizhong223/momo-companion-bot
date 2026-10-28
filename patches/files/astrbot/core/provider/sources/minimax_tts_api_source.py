import json
import os
from collections.abc import AsyncIterator

import aiohttp

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from astrbot.core.utils.datetime_utils import generate_timestamp_id

from ..entities import ProviderType
from ..provider import TTSProvider
from ..register import register_provider_adapter


@register_provider_adapter(
    "minimax_tts_api",
    "MiniMax TTS API",
    provider_type=ProviderType.TEXT_TO_SPEECH,
)
class ProviderMiniMaxTTSAPI(TTSProvider):
    def __init__(
        self,
        provider_config: dict,
        provider_settings: dict,
    ) -> None:
        super().__init__(provider_config, provider_settings)
        self.chosen_api_key: str = provider_config.get("api_key", "")
        self.api_base: str = provider_config.get(
            "api_base",
            "https://api.minimaxi.com/v1/t2a_v2",
        )
        self.group_id: str = provider_config.get("minimax-group-id", "")
        self.set_model(provider_config.get("model", ""))
        self.lang_boost: str = provider_config.get("minimax-langboost", "auto")
        self.is_timber_weight: bool = provider_config.get(
            "minimax-is-timber-weight",
            False,
        )
        default_timber_weight = [
            {"voice_id": "Chinese (Mandarin)_Warm_Girl", "weight": 1}
        ]
        raw_timber_weight = provider_config.get("minimax-timber-weight", "")
        if not raw_timber_weight:
            self.timber_weight = default_timber_weight
        else:
            try:
                self.timber_weight = json.loads(raw_timber_weight)
            except json.JSONDecodeError:
                logger.warning(
                    "MiniMax TTS 权重配置解析失败，将使用默认值。 raw_value: %s",
                    raw_timber_weight,
                )
                self.timber_weight = default_timber_weight

        self.voice_setting: dict = {
            "speed": provider_config.get("minimax-voice-speed", 1.0),
            "vol": provider_config.get("minimax-voice-vol", 1.0),
            "pitch": provider_config.get("minimax-voice-pitch", 0),
            "voice_id": ""
            if self.is_timber_weight
            else provider_config.get("minimax-voice-id", ""),
            "emotion": provider_config.get("minimax-voice-emotion", "auto"),
            "latex_read": provider_config.get("minimax-voice-latex", False),
            "english_normalization": provider_config.get(
                "minimax-voice-english-normalization",
                False,
            ),
        }

        if self.voice_setting["emotion"] == "auto":
            self.voice_setting.pop("emotion", None)

        self.audio_setting: dict = {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "wav",
        }

        self.concat_base_url: str = f"{self.api_base}?GroupId={self.group_id}"
        self.headers = {
            "Authorization": f"Bearer {self.chosen_api_key}",
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
        }

    def _build_tts_stream_body(self, text: str):
        """构建流式请求体"""
        voice_setting = dict(self.voice_setting)
        # 只有单独一句「好好好」才给 happy + 0.98：happy 会让句尾上扬，
        # 一旦后面还跟着半句（"那你去忙吧"），整句都会飘起来，不像机器人。
        bare = "".join(ch for ch in text if "\u4e00" <= ch <= "\u9fff")
        if bare == "好好好":
            voice_setting["emotion"] = "happy"
            voice_setting["speed"] = 0.98
        dict_body: dict[str, object] = {
            "model": self.model_name,
            "text": text,
            "stream": True,
            "stream_options": {"exclude_aggregated_audio": True},
            "language_boost": self.lang_boost,
            "voice_setting": voice_setting,
            "audio_setting": self.audio_setting,
        }
        if self.is_timber_weight:
            dict_body["timber_weights"] = self.timber_weight

        return json.dumps(dict_body)

    async def _call_tts_stream(self, text: str) -> AsyncIterator[str]:
        """进行流式请求。

        P0：MiniMax 流里 status=1 表示还在合成，status=2 才是完成；
        只有 HTTP 200 不代表成功。业务错误（base_resp.status_code != 0）
        或流在 status=2 之前 EOF，都必须让 get_audio 失败，不能把半截音频当成品。
        """
        completed = False

        def _parse(message: bytes) -> tuple[list[str], bool]:
            """解析一个 SSE 包；返回 (audio 片段, 是否 status=2)。"""
            audios: list[str] = []
            done = False
            for line in message.split(b"\n"):
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == b"[DONE]":
                    continue
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse JSON data from SSE message")
                    continue
                if not isinstance(data, dict):
                    continue

                base = data.get("base_resp") or {}
                code = base.get("status_code")
                if code not in (0, None):
                    raise Exception(
                        "MiniMax TTS 业务错误 "
                        f"{code}: {base.get('status_msg') or ''}"
                    )

                body = data.get("data") or {}
                status = body.get("status")
                audio = body.get("audio")
                if status not in (1, 2):
                    if audio:
                        raise Exception(f"MiniMax TTS 未知流状态: {status!r}")
                    continue
                if audio:
                    audios.append(audio)
                if status == 2:
                    done = True
            return audios, done

        try:
            async with (
                aiohttp.ClientSession(headers=self.request_headers) as session,
                session.post(
                    self.concat_base_url,
                    headers=self.headers,
                    data=self._build_tts_stream_body(text),
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as response,
            ):
                response.raise_for_status()

                buffer = b""
                while True:
                    chunk = await response.content.read(8192)
                    if not chunk:
                        break

                    # 兼容 \r\n\r\n；跨 chunk 的 \r\n 概率很低，后续 read 会补齐。
                    buffer += chunk.replace(b"\r\n", b"\n")

                    while b"\n\n" in buffer:
                        message, buffer = buffer.split(b"\n\n", 1)
                        audios, done = _parse(message)
                        for audio in audios:
                            yield audio
                        completed = completed or done

                # 某些服务端最后一个 data: 包没有尾随空行；EOF 时也要解析，
                # 否则完整的 status=2 会被误判成“流未完成”。
                if buffer.strip():
                    audios, done = _parse(buffer)
                    for audio in audios:
                        yield audio
                    completed = completed or done

                if not completed:
                    raise Exception("MiniMax TTS 流在 status=2 之前结束，音频不完整")

        except aiohttp.ClientError as e:
            raise Exception(f"MiniMax TTS API请求失败: {e!s}")

    async def _audio_play(self, audio_stream: AsyncIterator[str]) -> bytes:
        """解码数据流到 audio 比特流"""
        chunks = []
        async for chunk in audio_stream:
            if chunk.strip():
                chunks.append(bytes.fromhex(chunk.strip()))
        return b"".join(chunks)

    @staticmethod
    def _preprocess_text(text: str) -> str:
        """Chat text -> speech: original laugh tags + 4 readability rules."""
        import re

        # 原版笑声标签
        text = re.sub(r"哈{5,}", "哈哈哈", text)
        text = re.sub(r"呵{3,}", "呵呵", text)
        text = re.sub(r"嘻{3,}", "嘻嘻", text)
        # 去括号
        text = text.replace("（", "").replace("）", "")
        # Q6：TTS 旁路也不能带省略号；先换成空格，再统一断句
        text = re.sub(r"[…]+|\.{3,}", " ", text)
        # 空格断句 → 逗号
        text = re.sub(r"\s+", "，", text).strip("， ")
        # 问号/感叹号后不要跟逗号
        text = re.sub(r"([？！。])，", r"\1", text)
        # 在吗 → 疑问语气；幂等：已经有问号就不再加
        text = re.sub(r"^(在吗|在不在)(?![？?])", r"\1？", text)
        # 句尾补句号
        if text and not re.search(r"[。！？!?…]$", text):
            text = text + "。"
        return text

    def prepare_text(self, text: str) -> str:
        """给上层取“最终送进 TTS 的稿子”，不产生音频。"""
        return self._preprocess_text(text)

    async def get_audio(self, text: str, prepared: bool = False) -> str:
        temp_dir = get_astrbot_temp_path()
        os.makedirs(temp_dir, exist_ok=True)
        path = os.path.join(temp_dir, f"minimax_tts_api_{generate_timestamp_id()}.wav")

        try:
            if not prepared:
                text = self.prepare_text(text)
            if not text:
                raise ValueError("pure laughter: skip voice")
            # 直接将异步生成器传递给 _audio_play 方法
            audio_stream = self._call_tts_stream(text)
            audio = await self._audio_play(audio_stream)

            # 检查音频数据是否为空
            if not audio or len(audio) == 0:
                raise Exception(
                    "MiniMax TTS API returned empty audio data. "
                    "Please verify your configuration, especially the 'group_id' parameter. "
                    "You can find your group_id in Account Management -> Basic Information on the MiniMax platform."
                )

            # 结果保存至文件
            with open(path, "wb") as file:
                file.write(audio)

            return path

        except aiohttp.ClientError as e:
            raise Exception(f"MiniMax TTS API request failed: {e!s}")

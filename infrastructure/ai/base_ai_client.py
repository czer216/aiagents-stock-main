"""
AI 客户端基础封装
提供统一的 AI 调用接口、错误处理、日志记录
"""
import logging
import json
from typing import Dict, Any, Optional, Callable
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseAIClient(ABC):
    """AI 客户端基类"""

    def __init__(self, model: str = None):
        self.model = model
        self._client = None

    @abstractmethod
    def _init_client(self):
        """初始化具体的 AI 客户端"""
        pass

    @abstractmethod
    def _call_api(self, prompt: str, **kwargs) -> str:
        """调用 API 的具体实现"""
        pass

    def is_available(self) -> bool:
        """检查 AI 是否可用"""
        return self._client is not None

    def call_text(self, prompt: str, fallback: Optional[Callable] = None, **kwargs) -> str:
        """
        调用 AI 返回文本结果

        Args:
            prompt: 提示词
            fallback: 失败时的降级函数
            **kwargs: 其他参数

        Returns:
            AI 返回的文本
        """
        if not self.is_available():
            logger.warning("AI 客户端不可用")
            if fallback:
                return fallback()
            return "AI 服务暂时不可用"

        try:
            result = self._call_api(prompt, **kwargs)
            logger.info(f"✅ AI 调用成功，返回长度: {len(result)}")
            return result
        except Exception as e:
            logger.error(f"❌ AI 调用失败: {e}")
            if fallback:
                return fallback()
            return f"AI 调用失败: {str(e)}"

    def call_json(self, prompt: str, fallback: Optional[Callable] = None, **kwargs) -> Dict:
        """
        调用 AI 返回 JSON 结果

        Args:
            prompt: 提示词
            fallback: 失败时的降级函数
            **kwargs: 其他参数

        Returns:
            解析后的 JSON 字典
        """
        text_result = self.call_text(prompt, fallback=None, **kwargs)

        try:
            # 尝试提取 JSON
            if "```json" in text_result:
                json_start = text_result.find("```json") + 7
                json_end = text_result.find("```", json_start)
                json_text = text_result[json_start:json_end].strip()
            elif "{" in text_result and "}" in text_result:
                json_start = text_result.find("{")
                json_end = text_result.rfind("}") + 1
                json_text = text_result[json_start:json_end]
            else:
                json_text = text_result

            result = json.loads(json_text)
            logger.info(f"✅ JSON 解析成功")
            return result
        except Exception as e:
            logger.error(f"❌ JSON 解析失败: {e}")
            if fallback:
                return fallback()
            return {"error": f"JSON 解析失败: {str(e)}", "raw": text_result}


class DeepSeekAIClient(BaseAIClient):
    """DeepSeek AI 客户端封装"""

    def __init__(self, model: str = None):
        super().__init__(model)
        self._init_client()

    def _init_client(self):
        """初始化 DeepSeek 客户端"""
        try:
            from infrastructure.ai.deepseek_client import DeepSeekClient
            self._client = DeepSeekClient(model=self.model)
            logger.info(f"✅ DeepSeek 客户端初始化成功，模型: {self.model}")
        except Exception as e:
            logger.error(f"❌ DeepSeek 客户端初始化失败: {e}")
            self._client = None

    def _call_api(self, prompt: str, **kwargs) -> str:
        """调用 DeepSeek API"""
        if not self._client:
            raise RuntimeError("DeepSeek 客户端未初始化")

        return self._client.chat(prompt, **kwargs)

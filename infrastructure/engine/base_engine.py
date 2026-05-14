"""
分析引擎基础框架
提供统一的流水线编排、模块初始化、错误处理
"""
import logging
from typing import Dict, Any, Optional, List, Callable
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseAnalysisEngine(ABC):
    """分析引擎基类"""

    def __init__(self, engine_name: str):
        self.engine_name = engine_name
        self.modules = {}
        self._init_modules()
        logger.info(f"✅ {engine_name} 引擎初始化完成")

    @abstractmethod
    def _init_modules(self):
        """初始化所有模块（子类实现）"""
        pass

    def register_module(self, name: str, module: Any, required: bool = True):
        """
        注册模块

        Args:
            name: 模块名称
            module: 模块实例
            required: 是否必需
        """
        if module is None and required:
            logger.error(f"❌ 必需模块 {name} 初始化失败")
        else:
            self.modules[name] = module
            logger.info(f"✅ 模块 {name} 注册成功")

    def get_module(self, name: str) -> Optional[Any]:
        """获取模块"""
        return self.modules.get(name)

    def is_module_available(self, name: str) -> bool:
        """检查模块是否可用"""
        module = self.modules.get(name)
        return module is not None

    def run_pipeline(
        self,
        steps: List[Dict[str, Any]],
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        运行分析流水线

        Args:
            steps: 步骤列表，每个步骤包含:
                - name: 步骤名称
                - func: 执行函数
                - required: 是否必需
                - fallback: 失败时的降级函数
            context: 上下文数据

        Returns:
            分析结果
        """
        context = context or {}
        results = {"success": True, "steps": {}, "errors": []}

        for step in steps:
            step_name = step.get("name", "unknown")
            step_func = step.get("func")
            required = step.get("required", True)
            fallback = step.get("fallback")

            logger.info(f"🔄 执行步骤: {step_name}")

            try:
                # 执行步骤
                step_result = step_func(context)
                results["steps"][step_name] = step_result

                # 更新上下文
                if isinstance(step_result, dict):
                    context.update(step_result)

                logger.info(f"✅ 步骤 {step_name} 完成")

            except Exception as e:
                error_msg = f"步骤 {step_name} 失败: {str(e)}"
                logger.error(f"❌ {error_msg}")
                results["errors"].append(error_msg)

                # 如果是必需步骤且失败，尝试降级
                if required:
                    if fallback:
                        try:
                            fallback_result = fallback(context)
                            results["steps"][step_name] = fallback_result
                            context.update(fallback_result if isinstance(fallback_result, dict) else {})
                            logger.warning(f"⚠️ 步骤 {step_name} 使用降级方案")
                        except Exception as fallback_error:
                            logger.error(f"❌ 降级方案也失败: {fallback_error}")
                            results["success"] = False
                            break
                    else:
                        results["success"] = False
                        break

        return results

    @abstractmethod
    def run_analysis(self, **kwargs) -> Dict[str, Any]:
        """运行完整分析（子类实现）"""
        pass

    def run_quick_analysis(self, **kwargs) -> Dict[str, Any]:
        """运行快速分析（不含 AI，子类可选实现）"""
        logger.warning(f"{self.engine_name} 未实现快速分析")
        return {"success": False, "error": "未实现快速分析"}

    def run_deep_analysis(self, **kwargs) -> Dict[str, Any]:
        """运行深度分析（含 AI，子类可选实现）"""
        logger.warning(f"{self.engine_name} 未实现深度分析")
        return {"success": False, "error": "未实现深度分析"}


class PipelineStep:
    """流水线步骤辅助类"""

    def __init__(
        self,
        name: str,
        func: Callable,
        required: bool = True,
        fallback: Optional[Callable] = None,
    ):
        self.name = name
        self.func = func
        self.required = required
        self.fallback = fallback

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "name": self.name,
            "func": self.func,
            "required": self.required,
            "fallback": self.fallback,
        }

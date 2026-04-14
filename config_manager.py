"""
环境配置管理模块
用于读取和保存.env配置文件
"""

import os
from pathlib import Path
from typing import Dict, Any


class ConfigManager:
    """配置管理器"""
    
    def __init__(self, env_file: str = ".env"):
        self.env_file = Path(env_file)
        self.default_config = {
            "DEEPSEEK_API_KEY": {
                "value": "",
                "description": "DeepSeek API密钥",
                "required": True,
                "type": "password"
            },
            "DEEPSEEK_BASE_URL": {
                "value": "https://api.deepseek.com/v1",
                "description": "DeepSeek API地址",
                "required": False,
                "type": "text"
            },
            "DEFAULT_MODEL_NAME": {
                "value": "deepseek-chat",
                "description": "AI模型名称（支持OpenAI兼容模型）",
                "required": False,
                "type": "text"
            },
            "TUSHARE_TOKEN": {
                "value": "",
                "description": "Tushare数据接口Token（可选）",
                "required": False,
                "type": "password"
            },
            "TUSHARE_BASE_URL": {
                "value": "http://8.136.22.187:8010/",
                "description": "Tushare代理地址",
                "required": False,
                "type": "text"
            },
            "TDX_ENABLED": {
                "value": "false",
                "description": "启用TDX本地数据源",
                "required": False,
                "type": "boolean"
            },
            "TDX_BASE_URL": {
                "value": "http://192.168.1.222:8181",
                "description": "TDX数据源地址",
                "required": False,
                "type": "text"
            },
            "MINIQMT_ENABLED": {
                "value": "false",
                "description": "启用MiniQMT量化交易",
                "required": False,
                "type": "boolean"
            },
            "MINIQMT_ACCOUNT_ID": {
                "value": "",
                "description": "MiniQMT账户ID",
                "required": False,
                "type": "text"
            },
            "MINIQMT_HOST": {
                "value": "127.0.0.1",
                "description": "MiniQMT服务器地址",
                "required": False,
                "type": "text"
            },
            "MINIQMT_PORT": {
                "value": "58610",
                "description": "MiniQMT服务器端口",
                "required": False,
                "type": "text"
            },
            "EMAIL_ENABLED": {
                "value": "false",
                "description": "启用邮件通知",
                "required": False,
                "type": "boolean"
            },
            "SMTP_SERVER": {
                "value": "",
                "description": "SMTP服务器地址",
                "required": False,
                "type": "text"
            },
            "SMTP_PORT": {
                "value": "587",
                "description": "SMTP服务器端口",
                "required": False,
                "type": "text"
            },
            "EMAIL_FROM": {
                "value": "",
                "description": "发件人邮箱",
                "required": False,
                "type": "text"
            },
            "EMAIL_PASSWORD": {
                "value": "",
                "description": "邮箱授权码",
                "required": False,
                "type": "password"
            },
            "EMAIL_TO": {
                "value": "",
                "description": "收件人邮箱",
                "required": False,
                "type": "text"
            },
            "WEBHOOK_ENABLED": {
                "value": "false",
                "description": "启用Webhook通知",
                "required": False,
                "type": "boolean"
            },
            "WEBHOOK_TYPE": {
                "value": "dingtalk",
                "description": "Webhook类型（dingtalk/feishu）",
                "required": False,
                "type": "select",
                "options": ["dingtalk", "feishu"]
            },
            "WEBHOOK_URL": {
                "value": "",
                "description": "Webhook地址",
                "required": False,
                "type": "text"
            },
            "WEBHOOK_KEYWORD": {
                "value": "aiagents通知",
                "description": "Webhook自定义关键词（钉钉安全验证）",
                "required": False,
                "type": "text"
            },
        }
    
    def read_env(self) -> Dict[str, str]:
        """读取.env文件"""
        config = {}
        
        if not self.env_file.exists():
            # 如果文件不存在，返回默认配置的值
            for key, info in self.default_config.items():
                config[key] = info["value"]
            return config
        
        try:
            with open(self.env_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # 跳过空行和注释
                    if not line or line.startswith('#'):
                        continue
                    
                    # 解析键值对
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # 移除引号
                        if value.startswith('"') and value.endswith('"'):
                            value = value[1:-1]
                        elif value.startswith("'") and value.endswith("'"):
                            value = value[1:-1]
                        
                        config[key] = value
        except Exception as e:
            print(f"读取.env文件失败: {e}")
        
        # 确保所有默认配置项都存在
        for key, info in self.default_config.items():
            if key not in config:
                config[key] = info["value"]
        
        return config
    
    def write_env(self, config: Dict[str, str]) -> bool:
        """保存配置到.env文件"""
        try:
            # 合并现有配置，避免未知键在重写时丢失
            existing_config = self.read_env()
            final_config = existing_config.copy()
            final_config.update(config or {})

            lines = []
            lines.append("# AI股票分析系统环境配置")
            lines.append("# 由系统自动生成和管理")
            lines.append("")
            
            # DeepSeek配置
            lines.append("# ========== DeepSeek API配置 ==========")
            lines.append(f'DEEPSEEK_API_KEY="{final_config.get("DEEPSEEK_API_KEY", "")}"')
            lines.append(f'DEEPSEEK_BASE_URL="{final_config.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")}"')
            lines.append(f'DEFAULT_MODEL_NAME="{final_config.get("DEFAULT_MODEL_NAME", "deepseek-chat")}"')
            lines.append("")
            
            # 数据源配置
            lines.append("# ========== 数据源配置（可选）==========")
            lines.append(f'TUSHARE_TOKEN="{final_config.get("TUSHARE_TOKEN", "")}"')
            lines.append(f'TUSHARE_BASE_URL="{final_config.get("TUSHARE_BASE_URL", "http://8.136.22.187:8010/")}"')
            lines.append(f'TDX_ENABLED="{final_config.get("TDX_ENABLED", "false")}"')
            lines.append(f'TDX_BASE_URL="{final_config.get("TDX_BASE_URL", "http://192.168.1.222:8181")}"')
            lines.append("")
            
            # MiniQMT配置
            lines.append("# ========== MiniQMT量化交易配置（可选）==========")
            lines.append(f'MINIQMT_ENABLED="{final_config.get("MINIQMT_ENABLED", "false")}"')
            lines.append(f'MINIQMT_ACCOUNT_ID="{final_config.get("MINIQMT_ACCOUNT_ID", "")}"')
            lines.append(f'MINIQMT_HOST="{final_config.get("MINIQMT_HOST", "127.0.0.1")}"')
            lines.append(f'MINIQMT_PORT="{final_config.get("MINIQMT_PORT", "58610")}"')
            lines.append("")
            
            # 邮件通知配置
            lines.append("# ========== 邮件通知配置（可选）==========")
            lines.append(f'EMAIL_ENABLED="{final_config.get("EMAIL_ENABLED", "false")}"')
            lines.append(f'SMTP_SERVER="{final_config.get("SMTP_SERVER", "")}"')
            lines.append(f'SMTP_PORT="{final_config.get("SMTP_PORT", "587")}"')
            lines.append(f'EMAIL_FROM="{final_config.get("EMAIL_FROM", "")}"')
            lines.append(f'EMAIL_PASSWORD="{final_config.get("EMAIL_PASSWORD", "")}"')
            lines.append(f'EMAIL_TO="{final_config.get("EMAIL_TO", "")}"')
            lines.append("")
            
            # Webhook通知配置
            lines.append("# ========== Webhook通知配置（可选）==========")
            lines.append(f'WEBHOOK_ENABLED="{final_config.get("WEBHOOK_ENABLED", "false")}"')
            lines.append(f'WEBHOOK_TYPE="{final_config.get("WEBHOOK_TYPE", "dingtalk")}"')
            lines.append(f'WEBHOOK_URL="{final_config.get("WEBHOOK_URL", "")}"')
            lines.append(f'WEBHOOK_KEYWORD="{final_config.get("WEBHOOK_KEYWORD", "aiagents通知")}"')

            # 追加保留的其他配置项（兼容未来新增配置）
            known_keys = {
                "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEFAULT_MODEL_NAME",
                "TUSHARE_TOKEN", "TUSHARE_BASE_URL", "TDX_ENABLED", "TDX_BASE_URL",
                "MINIQMT_ENABLED", "MINIQMT_ACCOUNT_ID", "MINIQMT_HOST", "MINIQMT_PORT",
                "EMAIL_ENABLED", "SMTP_SERVER", "SMTP_PORT", "EMAIL_FROM",
                "EMAIL_PASSWORD", "EMAIL_TO", "WEBHOOK_ENABLED", "WEBHOOK_TYPE",
                "WEBHOOK_URL", "WEBHOOK_KEYWORD"
            }
            extra_items = [(k, v) for k, v in final_config.items() if k not in known_keys]
            if extra_items:
                lines.append("")
                lines.append("# ========== 其他保留配置 ==========")
                for key, value in extra_items:
                    lines.append(f'{key}="{value}"')
            
            with open(self.env_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            
            return True
        except Exception as e:
            print(f"保存.env文件失败: {e}")
            return False
    
    def get_config_info(self) -> Dict[str, Dict[str, Any]]:
        """获取配置信息（包含描述、类型等）"""
        current_values = self.read_env()
        
        config_info = {}
        for key, info in self.default_config.items():
            config_info[key] = {
                "value": current_values.get(key, info["value"]),
                "description": info["description"],
                "required": info["required"],
                "type": info["type"]
            }
            # 如果有options字段，也包含进去
            if "options" in info:
                config_info[key]["options"] = info["options"]
        
        return config_info
    
    def validate_config(self, config: Dict[str, str]) -> tuple[bool, str]:
        """验证配置"""
        # 检查必填项
        for key, info in self.default_config.items():
            if info["required"] and not config.get(key):
                return False, f"必填项 {info['description']} 不能为空"
        
        # 验证API Key格式（简单检查长度）
        if config.get("DEEPSEEK_API_KEY"):
            api_key = config.get("DEEPSEEK_API_KEY", "")
            if len(api_key) < 20:
                return False, "DeepSeek API Key格式不正确（长度太短）"
        
        return True, "配置验证通过"
    
    def reload_config(self):
        """重新加载配置（重新加载.env文件）"""
        from dotenv import load_dotenv
        # 强制覆盖已存在的环境变量
        load_dotenv(override=True)


# 全局配置管理器实例
config_manager = ConfigManager()

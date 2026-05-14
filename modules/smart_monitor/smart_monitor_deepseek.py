"""
智能盯盘 - DeepSeek AI 决策引擎
适配A股T+1交易规则的AI决策系统
"""

import logging
import json
from typing import Dict, List, Optional
from datetime import datetime, time
import pytz
import config
from modules.douban_author_strategy.douban_author_db import douban_author_db
from prompts.smart_monitor.deepseek_prompts import (
    build_a_stock_system_prompt,
    build_a_stock_user_prompt,
)


class SmartMonitorDeepSeek:
    """A股智能盯盘 - DeepSeek AI决策引擎"""

    def __init__(self, api_key: str):
        """
        初始化DeepSeek客户端
        
        Args:
            api_key: DeepSeek API密钥
        """
        self.api_key = api_key
        self.base_url = config.DEEPSEEK_BASE_URL
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        self.logger = logging.getLogger(__name__)

    def is_trading_time(self) -> bool:
        """
        判断当前是否在A股交易时间内
        
        Returns:
            bool: 是否可以交易
        """
        beijing_tz = pytz.timezone('Asia/Shanghai')
        now = datetime.now(beijing_tz)
        current_time = now.time()
        
        # 排除周末
        if now.weekday() >= 5:
            return False
        
        # 上午：9:30-11:30
        morning_start = time(9, 30)
        morning_end = time(11, 30)
        
        # 下午：13:00-15:00
        afternoon_start = time(13, 0)
        afternoon_end = time(15, 0)
        
        is_trading = (
            (morning_start <= current_time <= morning_end) or
            (afternoon_start <= current_time <= afternoon_end)
        )
        
        return is_trading

    def get_trading_session(self) -> Dict:
        """
        获取当前交易时段信息（A股版本）
        
        Returns:
            Dict: 时段信息
        """
        beijing_tz = pytz.timezone('Asia/Shanghai')
        now = datetime.now(beijing_tz)
        current_time = now.time()
        
        # 判断是否交易日
        if now.weekday() >= 5:
            return {
                'session': '休市',
                'volatility': 'none',
                'recommendation': '周末不可交易',
                'beijing_hour': now.hour,
                'can_trade': False
            }
        
        # 开盘前（9:00-9:30）：集合竞价时段
        if time(9, 0) <= current_time < time(9, 30):
            return {
                'session': '集合竞价',
                'volatility': 'high',
                'recommendation': '可观察盘面情绪，准备开盘交易',
                'beijing_hour': now.hour,
                'can_trade': False
            }
        
        # 上午盘（9:30-11:30）
        elif time(9, 30) <= current_time <= time(11, 30):
            return {
                'session': '上午盘',
                'volatility': 'high',
                'recommendation': '交易活跃，波动较大',
                'beijing_hour': now.hour,
                'can_trade': True
            }
        
        # 午间休市（11:30-13:00）
        elif time(11, 30) < current_time < time(13, 0):
            return {
                'session': '午间休市',
                'volatility': 'none',
                'recommendation': '不可交易，可分析上午盘面',
                'beijing_hour': now.hour,
                'can_trade': False
            }
        
        # 下午盘（13:00-15:00）
        elif time(13, 0) <= current_time <= time(15, 0):
            # 尾盘最后半小时（14:30-15:00）
            if current_time >= time(14, 30):
                return {
                    'session': '尾盘',
                    'volatility': 'high',
                    'recommendation': '尾盘波动大，谨慎操作',
                    'beijing_hour': now.hour,
                    'can_trade': True
                }
            else:
                return {
                    'session': '下午盘',
                    'volatility': 'medium',
                    'recommendation': '波动趋缓，适合布局',
                    'beijing_hour': now.hour,
                    'can_trade': True
                }
        
        # 盘后（15:00之后）
        else:
            return {
                'session': '盘后',
                'volatility': 'none',
                'recommendation': '收盘后，可复盘分析',
                'beijing_hour': now.hour,
                'can_trade': False
            }

    def chat_completion(self, messages: List[Dict], model: str = None,
                       temperature: float = 0.7, max_tokens: int = 2000) -> Dict:
        """
        调用DeepSeek API
        
        Args:
            messages: 对话消息列表
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大token数
            
        Returns:
            API响应
        """
        import requests
        
        model = model or config.DEFAULT_MODEL_NAME
        
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"DeepSeek API调用失败: {e}")
            raise

    def analyze_stock_and_decide(self, stock_code: str, market_data: Dict,
                                 account_info: Dict, has_position: bool = False,
                                 position_cost: float = 0, position_quantity: int = 0,
                                 douban_author_id: str = '', douban_author_name: str = '',
                                 douban_pattern_version: int = 0,
                                 stock_concepts: Optional[List[str]] = None,
                                 daily_strong_themes: Optional[List[str]] = None) -> Dict:
        """
        分析股票并做出交易决策（A股T+1规则）
        
        Args:
            stock_code: 股票代码（如：600519）
            market_data: 市场数据
            account_info: 账户信息
            has_position: 是否已持有该股票
            position_cost: 持仓成本价格
            position_quantity: 持仓数量
            
        Returns:
            交易决策
        """
        # 获取交易时段
        session_info = self.get_trading_session()
        
        author_pattern_context = self._build_author_pattern_context(
            author_id=douban_author_id,
            author_name=douban_author_name,
            pattern_version=int(douban_pattern_version or 0),
        )

        stock_concepts_clean = [str(x).strip() for x in (stock_concepts or []) if str(x).strip()]
        daily_themes_clean = [str(x).strip() for x in (daily_strong_themes or []) if str(x).strip()]

        # 构建Prompt
        prompt = self._build_a_stock_prompt(
            stock_code, market_data, account_info,
            has_position, session_info, position_cost, position_quantity,
            author_pattern_context=author_pattern_context,
            stock_concepts=stock_concepts_clean,
            daily_strong_themes=daily_themes_clean,
        )
        
        system_prompt = build_a_stock_system_prompt()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]

        try:
            response = self.chat_completion(messages, temperature=0.3)
            ai_response = response['choices'][0]['message']['content']
            
            # 解析JSON决策
            decision = self._parse_decision(ai_response)
            
            return {
                'success': True,
                'decision': decision,
                'raw_response': ai_response
            }
            
        except Exception as e:
            self.logger.error(f"AI决策失败: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def _build_a_stock_prompt(self, stock_code: str, market_data: Dict,
                             account_info: Dict, has_position: bool,
                             session_info: Dict, position_cost: float = 0,
                             position_quantity: int = 0,
                             author_pattern_context: str = '',
                             stock_concepts: Optional[List[str]] = None,
                             daily_strong_themes: Optional[List[str]] = None) -> str:
        """构建A股分析提示词"""
        
        return build_a_stock_user_prompt(
            stock_code=stock_code,
            market_data=market_data,
            account_info=account_info,
            has_position=has_position,
            session_info=session_info,
            position_cost=position_cost,
            position_quantity=position_quantity,
            author_pattern_context=author_pattern_context,
            stock_concepts=stock_concepts or [],
            daily_strong_themes=daily_strong_themes or [],
        )

    def _build_author_pattern_context(self, author_id: str, author_name: str, pattern_version: int) -> str:
        aid = str(author_id or '').strip()
        ver = int(pattern_version or 0)
        if not aid or ver <= 0:
            return ''

        try:
            row = douban_author_db.get_author_pattern(author_id=aid, pattern_version=ver)
            if not row:
                self.logger.warning(f"作者模式不存在: author_id={aid} v{ver}")
                return ''

            payload = {}
            try:
                payload = json.loads(row.get('pattern_json') or '{}') if isinstance(row, dict) else {}
            except Exception:
                self.logger.warning(f"作者模式JSON解析失败: author_id={aid} v{ver}")
                return ''

            style = str(payload.get('trading_style') or '').strip()
            rules = payload.get('rule_set') or []
            timing = payload.get('timing_rules') or []
            entry_exit = payload.get('entry_exit_heuristics') or []
            risk = payload.get('risk_controls') or []

            rule_lines = []
            for r in rules if isinstance(rules, list) else []:
                rid = str((r or {}).get('rule_id') or '').strip()
                txt = str((r or {}).get('rule') or '').strip()
                when_to_use = str((r or {}).get('when_to_use') or '').strip()
                when_not = str((r or {}).get('when_not_to_use') or '').strip()
                if txt:
                    rule_lines.append(f"- {rid or 'R'}: {txt}; 适用: {when_to_use or 'N/A'}; 不适用: {when_not or 'N/A'}")

            timing_text = '\n'.join([f"- {str(x)}" for x in timing if str(x).strip()])
            entry_exit_text = '\n'.join([f"- {str(x)}" for x in entry_exit if str(x).strip()])
            risk_text = '\n'.join([f"- {str(x)}" for x in risk if str(x).strip()])

            display_name = str(author_name or (row.get('author_name') if isinstance(row, dict) else '') or aid)
            context = (
                f"作者: {display_name} ({aid})\n"
                f"模式版本: v{ver}\n"
                f"交易风格: {style or '未提供'}\n"
                f"规则集:\n{chr(10).join(rule_lines) if rule_lines else '- 未提供'}\n"
                f"择时规则:\n{timing_text or '- 未提供'}\n"
                f"进出场规则:\n{entry_exit_text or '- 未提供'}\n"
                f"风控规则:\n{risk_text or '- 未提供'}"
            )
            return context
        except Exception as e:
            self.logger.warning(f"加载作者模式失败: {e}")
            return ''

    def _parse_decision(self, ai_response: str) -> Dict:
        """解析AI决策响应"""
        import json
        
        try:
            # 尝试多种提取方式
            if "```json" in ai_response.lower():
                json_start = ai_response.lower().find("```json") + 7
                json_end = ai_response.find("```", json_start)
                json_str = ai_response[json_start:json_end].strip()
            elif "```" in ai_response:
                first_tick = ai_response.find("```")
                json_start = ai_response.find("\n", first_tick) + 1
                json_end = ai_response.find("```", json_start)
                json_str = ai_response[json_start:json_end].strip()
            elif "{" in ai_response and "}" in ai_response:
                start_idx = ai_response.find('{')
                end_idx = ai_response.rfind('}') + 1
                json_str = ai_response[start_idx:end_idx]
            else:
                json_str = ai_response
            
            decision = json.loads(json_str)
            
            # 验证必需字段
            required_fields = ['action', 'confidence', 'reasoning']
            for field in required_fields:
                if field not in decision:
                    raise ValueError(f"缺少必需字段: {field}")
            
            # 设置默认值
            decision.setdefault('position_size_pct', 20)
            decision.setdefault('stop_loss_pct', 5.0)
            decision.setdefault('take_profit_pct', 10.0)
            decision.setdefault('risk_level', 'medium')
            
            return decision
            
        except Exception as e:
            self.logger.error(f"解析AI决策失败: {e}")
            # 返回保守决策
            return {
                'action': 'HOLD',
                'confidence': 0,
                'reasoning': f'AI响应解析失败: {str(e)}',
                'position_size_pct': 0,
                'stop_loss_pct': 5.0,
                'take_profit_pct': 10.0,
                'risk_level': 'high'
            }


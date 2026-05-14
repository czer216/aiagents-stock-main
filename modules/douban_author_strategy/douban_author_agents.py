import json
import re
from typing import Dict, List

import config
from infrastructure.ai.deepseek_client import DeepSeekClient
from prompts.douban_author_prompts import (
    build_pattern_extraction_prompt,
    build_pattern_update_prompt,
    build_candidate_generation_prompt,
)


class DoubanAuthorAgents:
    def __init__(self):
        self.client = DeepSeekClient(model=config.DEFAULT_MODEL_NAME)

    @staticmethod
    def _extract_first_valid_json_object(content: str) -> Dict:
        text = str(content or "")
        starts = [i for i, ch in enumerate(text) if ch == '{']
        for s in starts:
            depth = 0
            in_str = False
            esc = False
            for i in range(s, len(text)):
                ch = text[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == '\\':
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                    continue
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = text[s:i + 1]
                        try:
                            return json.loads(candidate)
                        except Exception:
                            break
        return {}

    @staticmethod
    def _parse_json_response(text: str) -> Dict:
        content = str(text or "").strip()
        if not content:
            return {}
        fenced = re.search(r"```json\s*([\s\S]*?)\s*```", content)
        if fenced:
            content = fenced.group(1).strip()
        try:
            return json.loads(content)
        except Exception:
            pass
        extracted = DoubanAuthorAgents._extract_first_valid_json_object(content)
        if extracted:
            return extracted
        return {"error_reason": "json_parse_failed", "raw": text[:12000]}

    @staticmethod
    def _contains_stock_hint(payload: Dict) -> bool:
        raw = json.dumps(payload or {}, ensure_ascii=False)
        if re.search(r"\b\d{6}\b", raw):
            return True
        if "股票" in raw and any(k in raw for k in ["代码", "名称", "个股"]):
            return True
        return False

    @staticmethod
    def _filter_candidates_by_pool(candidate_json: Dict, allowed_stock_pool: List[Dict], candidate_limit: int = 3) -> Dict:
        if not isinstance(candidate_json, dict):
            return {"candidates": [], "summary": "候选结果无效"}
        pool_codes = {str(x.get('code') or '').strip() for x in (allowed_stock_pool or []) if str(x.get('code') or '').strip()}
        filtered = []
        for c in (candidate_json.get('candidates') or []):
            code = str((c or {}).get('code') or '').strip()
            if code and code in pool_codes:
                filtered.append(c)
        limit = max(1, int(candidate_limit or 3))
        candidate_json['candidates'] = filtered[:limit]
        return candidate_json

    def _repair_candidate_output(self, raw_text: str) -> Dict:
        prompt = f"""
请将下面内容转换成严格JSON，且只输出JSON，不要任何解释。

目标JSON结构：
{{
  "candidates": [
    {{
      "code": "股票代码",
      "name": "股票名称",
      "theme": "所属主线/题材",
      "action": "操作建议(买入观察/低吸/持有/回避)",
      "reason": "推荐理由(不超过80字)",
      "trigger_logic": "触发逻辑",
      "entry_idea": "入场思路",
      "exit_idea": "止盈/止损思路",
      "risk": "主要风险",
      "confidence": 0,
      "matched_rule": "匹配规则"
    }}
  ],
  "positioning_advice": "仓位建议",
  "invalidators": ["失效条件1"],
  "summary": "摘要"
}}

待转换内容：
{str(raw_text or '')[:10000]}
""".strip()
        resp = self.client.call_api(
            messages=[
                {"role": "system", "content": "你是结构化数据整理助手，只输出JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2200,
        )
        repaired = self._parse_json_response(resp)
        if repaired.get('error_reason') == 'json_parse_failed':
            repaired = self._extract_first_valid_json_object(str(resp or '')) or {"candidates": [], "summary": "候选输出解析失败"}
        if repaired.get('candidates') is None:
            repaired['candidates'] = []
        return repaired

    def _repair_pattern_output(self, raw_text: str) -> Dict:
        prompt = f"""
请将下面内容转换成严格JSON，且只输出JSON，不要任何解释。

目标JSON结构：
{{
  "trading_style": "一句话概括风格",
  "rule_set": [
    {{"rule_id": "R1", "rule": "规则描述", "when_to_use": "适用场景", "when_not_to_use": "不适用场景"}}
  ],
  "sector_preferences": ["偏好板块1", "偏好板块2"],
  "timing_rules": ["择时规则1", "择时规则2"],
  "entry_exit_heuristics": ["进出场规则1", "进出场规则2"],
  "risk_controls": ["风控规则1", "风控规则2"],
  "confidence": 0,
  "evidence": [
    {{"title": "帖子标题", "date": "日期", "point": "支持该模式的原文要点"}}
  ],
  "summary": "100字以内总结"
}}

待转换内容：
{str(raw_text or '')[:10000]}
""".strip()
        resp = self.client.call_api(
            messages=[
                {"role": "system", "content": "你是结构化数据整理助手，只输出JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2200,
        )
        repaired = self._parse_json_response(resp)
        if repaired.get('error_reason') == 'json_parse_failed':
            repaired = self._extract_first_valid_json_object(str(resp or '')) or {}
        if not isinstance(repaired, dict):
            return {}
        return repaired

    def extract_pattern(self, author_name: str, posts: List[Dict], lookback_days: int = 60) -> Dict:
        prompt = build_pattern_extraction_prompt(author_name=author_name, posts=posts, lookback_days=lookback_days)
        resp = self.client.call_api(
            messages=[
                {"role": "system", "content": "你是严谨的交易模式研究员，只输出JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=2500,
        )
        parsed = self._parse_json_response(resp)
        if parsed.get('error_reason') == 'json_parse_failed' and parsed.get('raw'):
            parsed = self._repair_pattern_output(parsed.get('raw', ''))
        if self._contains_stock_hint(parsed):
            clean_prompt = f"请移除所有股票相关信息，仅保留通用交易规则并输出JSON：\n{json.dumps(parsed, ensure_ascii=False)}"
            cleaned_resp = self.client.call_api(
                messages=[
                    {"role": "system", "content": "你是交易规则清洗助手，只输出JSON。"},
                    {"role": "user", "content": clean_prompt},
                ],
                temperature=0.1,
                max_tokens=1800,
            )
            parsed = self._parse_json_response(cleaned_resp)
        if parsed.get('error_reason') == 'json_parse_failed' and parsed.get('raw'):
            parsed = self._repair_pattern_output(parsed.get('raw', ''))
        return parsed

    def update_pattern(self, author_name: str, previous_pattern_json: Dict, new_posts: List[Dict], lookback_days: int = 60) -> Dict:
        prompt = build_pattern_update_prompt(
            author_name=author_name,
            previous_pattern_json=previous_pattern_json or {},
            new_posts=new_posts or [],
            lookback_days=lookback_days,
        )
        resp = self.client.call_api(
            messages=[
                {"role": "system", "content": "你是严谨的交易模式研究员，只输出JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=2500,
        )
        parsed = self._parse_json_response(resp)
        if parsed.get('error_reason') == 'json_parse_failed' and parsed.get('raw'):
            parsed = self._repair_pattern_output(parsed.get('raw', ''))
        if self._contains_stock_hint(parsed):
            clean_prompt = f"请移除所有股票相关信息，仅保留通用交易规则并输出JSON：\n{json.dumps(parsed, ensure_ascii=False)}"
            cleaned_resp = self.client.call_api(
                messages=[
                    {"role": "system", "content": "你是交易规则清洗助手，只输出JSON。"},
                    {"role": "user", "content": clean_prompt},
                ],
                temperature=0.1,
                max_tokens=1800,
            )
            parsed = self._parse_json_response(cleaned_resp)
        if parsed.get('error_reason') == 'json_parse_failed' and parsed.get('raw'):
            parsed = self._repair_pattern_output(parsed.get('raw', ''))
        return parsed

    def generate_candidates(
        self,
        author_name: str,
        pattern_json: Dict,
        historical_success_patterns: List[Dict] = None,
        historical_failure_lessons: List[Dict] = None,
        author_feedback_metrics: Dict = None,
        allowed_stock_pool: List[Dict] = None,
        candidate_limit: int = 3,
        analysis_date: str = "",
        target_trade_date: str = "",
        market_context: Dict = None,
        intraday_mode: bool = False,
    ) -> Dict:
        prompt = build_candidate_generation_prompt(
            author_name=author_name,
            pattern_json=pattern_json,
            historical_success_patterns=historical_success_patterns or [],
            historical_failure_lessons=historical_failure_lessons or [],
            author_feedback_metrics=author_feedback_metrics or {},
            allowed_stock_pool=allowed_stock_pool or [],
            candidate_limit=candidate_limit,
            analysis_date=analysis_date,
            target_trade_date=target_trade_date,
            market_context=market_context or {},
            intraday_mode=bool(intraday_mode),
        )
        resp = self.client.call_api(
            messages=[
                {"role": "system", "content": "你是A股交易执行顾问，只输出JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=2500,
        )
        parsed = self._parse_json_response(resp)
        if parsed.get('error_reason') == 'json_parse_failed' and parsed.get('raw'):
            parsed = self._repair_candidate_output(parsed.get('raw', ''))
        return self._filter_candidates_by_pool(parsed, allowed_stock_pool or [], candidate_limit=candidate_limit)

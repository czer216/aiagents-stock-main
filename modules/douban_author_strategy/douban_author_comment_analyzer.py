from collections import Counter
import re
from typing import Dict, List, Tuple


class DoubanAuthorCommentAnalyzer:
    def __init__(self):
        self.bullish_words = ["看多", "看好", "加仓", "低吸", "反包", "起飞", "突破", "强", "机会", "买"]
        self.bearish_words = ["看空", "减仓", "清仓", "风险", "破位", "大跌", "回撤", "弱", "卖", "高位"]
        self.neutral_words = ["观望", "等等", "再看", "不确定", "分歧", "震荡", "横盘"]

    @staticmethod
    def _count_hits(text: str, words: List[str]) -> int:
        content = str(text or "")
        return sum(content.count(w) for w in words)

    @staticmethod
    def _clean_text(text: str) -> str:
        return str(text or "").replace("\n", " ").strip()

    def _classify_text(self, text: str) -> Tuple[int, int, int]:
        b = self._count_hits(text, self.bullish_words)
        s = self._count_hits(text, self.bearish_words)
        n = self._count_hits(text, self.neutral_words)
        return b, s, n

    @staticmethod
    def _pick_examples(rows: List[Tuple[int, Dict]], limit: int = 3) -> List[Dict]:
        out = []
        seen = set()
        for _, item in sorted(rows, key=lambda x: x[0], reverse=True):
            text = str(item.get("comment_text") or "").strip()
            if not text:
                continue
            key = text[:80]
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "comment_user": str(item.get("comment_user") or ""),
                    "comment_time": str(item.get("comment_time") or ""),
                    "comment_text": text,
                    "like_count": int(item.get("like_count") or 0),
                }
            )
            if len(out) >= max(1, int(limit)):
                break
        return out

    @staticmethod
    def _extract_topic_phrases(text: str) -> List[str]:
        cleaned = re.sub(r"\s+", "", str(text or ""))
        chunks = re.findall(r"[\u4e00-\u9fff]{2,6}", cleaned)
        blacklist = {
            "这个", "那个", "今天", "明天", "感觉", "真的", "还是", "就是", "一个", "现在",
            "我们", "你们", "他们", "自己", "可以", "如果", "因为", "所以", "然后", "一下",
            "评论", "楼主", "老师", "大家", "什么", "怎么", "有没有", "不是", "但是", "看到",
        }
        out = []
        for c in chunks:
            if c in blacklist:
                continue
            if len(c) < 2:
                continue
            out.append(c)
        return out

    @staticmethod
    def _build_discussion_overview(topics: List[str], operation_bias: Dict, label: str) -> str:
        if not topics:
            return "整体讨论较分散，未形成稳定话题聚焦。"
        top_text = "、".join(topics[:5])
        return (
            f"评论区主要围绕{top_text}展开，整体情绪{label}。"
            f"当前以观望({operation_bias.get('neutral', 0)}%)为主，"
            f"看多({operation_bias.get('bullish', 0)}%)与看空({operation_bias.get('bearish', 0)}%)并存。"
        )

    @staticmethod
    def _label(score: float) -> str:
        if score >= 65:
            return "偏乐观"
        if score >= 55:
            return "小幅乐观"
        if score > 45:
            return "中性"
        if score > 35:
            return "小幅悲观"
        return "偏悲观"

    def analyze_comments(self, comments: List[Dict]) -> Dict:
        total = len(comments or [])
        if total <= 0:
            return {
                "comment_count": 0,
                "sentiment_score": 50.0,
                "sentiment_label": "中性",
                "operation_bias": {"bullish": 0, "bearish": 0, "neutral": 0, "divergent": 0},
                "top_bullish_points": [],
                "top_bearish_points": [],
                "representative_comments": {"bullish": [], "bearish": [], "neutral": []},
                "viewpoint_summary": {"bullish": [], "bearish": [], "neutral": []},
                "top_discussion_topics": [],
                "discussion_overview": "暂无评论数据",
                "summary_text": "暂无评论数据",
            }

        bullish = 0
        bearish = 0
        neutral = 0
        divergent = 0
        bull_points = Counter()
        bear_points = Counter()
        neutral_points = Counter()
        topic_points = Counter()
        bullish_rows: List[Tuple[int, Dict]] = []
        bearish_rows: List[Tuple[int, Dict]] = []
        neutral_rows: List[Tuple[int, Dict]] = []

        for c in comments:
            text = self._clean_text(c.get("comment_text"))
            if not text:
                continue
            b, s, n = self._classify_text(text)
            for t in self._extract_topic_phrases(text):
                topic_points[t] += 1
            weight = max(1, int(c.get("like_count") or 0))

            if b > 0 and s > 0:
                divergent += 1
            if b >= s and b > 0:
                bullish += 1
                bullish_rows.append((weight, {**c, "comment_text": text}))
                for w in self.bullish_words:
                    if w in text:
                        bull_points[w] += 1
            elif s > b and s > 0:
                bearish += 1
                bearish_rows.append((weight, {**c, "comment_text": text}))
                for w in self.bearish_words:
                    if w in text:
                        bear_points[w] += 1
            else:
                neutral += 1
                neutral_rows.append((weight, {**c, "comment_text": text}))
                for w in self.neutral_words:
                    if w in text:
                        neutral_points[w] += 1

        score = 50.0
        score += (bullish - bearish) / max(1, total) * 35
        score -= divergent / max(1, total) * 5
        score = max(0.0, min(100.0, score))

        top_bull = [k for k, _ in bull_points.most_common(5)]
        top_bear = [k for k, _ in bear_points.most_common(5)]
        top_neutral = [k for k, _ in neutral_points.most_common(5)]
        top_topics = [k for k, _ in topic_points.most_common(8)]

        operation_bias = {
            "bullish": round(bullish / total * 100, 1),
            "bearish": round(bearish / total * 100, 1),
            "neutral": round(neutral / total * 100, 1),
            "divergent": round(divergent / total * 100, 1),
        }
        label = self._label(score)
        summary = (
            f"评论{total}条，情绪{label}（{score:.1f}）。"
            f"操作倾向：看多{operation_bias['bullish']}%，看空{operation_bias['bearish']}%，"
            f"观望{operation_bias['neutral']}%，分歧{operation_bias['divergent']}%。"
        )

        representative_comments = {
            "bullish": self._pick_examples(bullish_rows, limit=3),
            "bearish": self._pick_examples(bearish_rows, limit=3),
            "neutral": self._pick_examples(neutral_rows, limit=3),
        }

        viewpoint_summary = {
            "bullish": top_bull,
            "bearish": top_bear,
            "neutral": top_neutral,
        }
        discussion_overview = self._build_discussion_overview(top_topics, operation_bias, label)

        return {
            "comment_count": total,
            "sentiment_score": round(score, 1),
            "sentiment_label": label,
            "operation_bias": operation_bias,
            "top_bullish_points": top_bull,
            "top_bearish_points": top_bear,
            "representative_comments": representative_comments,
            "viewpoint_summary": viewpoint_summary,
            "top_discussion_topics": top_topics,
            "discussion_overview": discussion_overview,
            "summary_text": summary,
        }


comment_analyzer = DoubanAuthorCommentAnalyzer()

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
import os
import re
from datetime import datetime
from typing import Dict, List
import streamlit as st

from monitor_db import monitor_db

class NotificationService:
    """通知服务"""
    
    def __init__(self):
        # 强制重新加载环境变量
        from dotenv import load_dotenv
        load_dotenv()
        self.config = self._load_config()
    
    def _load_config(self) -> Dict:
        """加载通知配置"""
        config = {
            'email_enabled': False,
            'smtp_server': '',
            'smtp_port': 587,
            'email_from': '',
            'email_password': '',
            'email_to': '',
            'webhook_enabled': False,
            'webhook_url': '',
            'webhook_type': 'dingtalk',  # dingtalk 或 feishu
            'webhook_keyword': 'aiagents通知'  # 钉钉自定义关键词
        }
        
        # 从环境变量加载配置
        if os.getenv('EMAIL_ENABLED'):
            config['email_enabled'] = os.getenv('EMAIL_ENABLED').lower() == 'true'
        if os.getenv('SMTP_SERVER'):
            config['smtp_server'] = os.getenv('SMTP_SERVER')
        if os.getenv('SMTP_PORT'):
            config['smtp_port'] = int(os.getenv('SMTP_PORT'))
        if os.getenv('EMAIL_FROM'):
            config['email_from'] = os.getenv('EMAIL_FROM')
        if os.getenv('EMAIL_PASSWORD'):
            config['email_password'] = os.getenv('EMAIL_PASSWORD')
        if os.getenv('EMAIL_TO'):
            config['email_to'] = os.getenv('EMAIL_TO')
        if os.getenv('WEBHOOK_ENABLED'):
            config['webhook_enabled'] = os.getenv('WEBHOOK_ENABLED').lower() == 'true'
        if os.getenv('WEBHOOK_URL'):
            config['webhook_url'] = os.getenv('WEBHOOK_URL')
        if os.getenv('WEBHOOK_TYPE'):
            config['webhook_type'] = os.getenv('WEBHOOK_TYPE').lower()
        if os.getenv('WEBHOOK_KEYWORD'):
            config['webhook_keyword'] = os.getenv('WEBHOOK_KEYWORD')
        
        return config
    
    def send_notifications(self):
        """发送所有待发送的通知"""
        notifications = monitor_db.get_pending_notifications()
        
        if not notifications:
            print("没有待发送的通知")
            return
        
        print(f"\n{'='*50}")
        print(f"开始发送通知，共 {len(notifications)} 条")
        print(f"{'='*50}")
        
        for notification in notifications:
            try:
                print(f"\n处理通知: {notification['symbol']} - {notification['type']}")
                if self.send_notification(notification):
                    monitor_db.mark_notification_sent(notification['id'])
                    print(f"✅ 通知已成功发送并标记: {notification['message']}")
                else:
                    print(f"❌ 通知发送失败: {notification['message']}")
            except Exception as e:
                print(f"❌ 发送通知时出错: {e}")
                import traceback
                traceback.print_exc()
        
        print(f"{'='*50}\n")
    
    def send_notification(self, notification: Dict) -> bool:
        """发送单个通知"""
        success = False

        # 兼容没有message字段的新通知类型
        if 'message' not in notification or not notification.get('message'):
            symbol = notification.get('symbol', '')
            ntype = notification.get('type', 'notice')
            notification['message'] = f"{symbol} {ntype}".strip() or "通知"

        # 尝试webhook通知
        if self.config['webhook_enabled']:
            webhook_success = self._send_webhook_notification(notification)
            if webhook_success:
                success = True

        # 尝试邮件通知
        if self.config['email_enabled']:
            email_success = self._send_email_notification(notification)
            if email_success:
                success = True

        # 如果两者都未启用或都失败，使用界面通知作为备用
        if not success:
            self._show_streamlit_notification(notification)
            success = True

        return success
    
    def _send_email_notification(self, notification: Dict) -> bool:
        """发送邮件通知"""
        try:
            # 检查邮件配置是否完整
            if not all([self.config['smtp_server'], self.config['email_from'], 
                       self.config['email_password'], self.config['email_to']]):
                print("⚠️ 邮件配置不完整，使用界面通知")
                print(f"  - SMTP服务器: {self.config['smtp_server'] or '未配置'}")
                print(f"  - 发件人: {self.config['email_from'] or '未配置'}")
                print(f"  - 收件人: {self.config['email_to'] or '未配置'}")
                print(f"  - 密码: {'已配置' if self.config['email_password'] else '未配置'}")
                self._show_streamlit_notification(notification)
                return True
            
            # 创建邮件
            msg = MIMEMultipart()
            msg['From'] = self.config['email_from']
            msg['To'] = self.config['email_to']
            msg['Subject'] = notification.get('title') or f"股票监测提醒 - {notification['symbol']}"
            
            # 邮件正文
            body = f"""
            <h2>股票监测提醒</h2>
            <p><strong>股票代码:</strong> {notification['symbol']}</p>
            <p><strong>股票名称:</strong> {notification['name']}</p>
            <p><strong>提醒类型:</strong> {notification['type']}</p>
            <p><strong>提醒内容:</strong> {notification['message']}</p>
            <p><strong>触发时间:</strong> {notification['triggered_at']}</p>
            <hr>
            <p><em>此邮件由AI股票分析系统自动发送</em></p>
            """
            
            msg.attach(MIMEText(body, 'html'))
            
            print(f"📧 正在发送邮件...")
            print(f"  - 收件人: {self.config['email_to']}")
            print(f"  - 主题: 股票监测提醒 - {notification['symbol']}")
            
            # 根据端口选择连接方式
            if self.config['smtp_port'] == 465:
                print(f"  - 使用 SMTP_SSL 连接 {self.config['smtp_server']}:{self.config['smtp_port']}")
                server = smtplib.SMTP_SSL(self.config['smtp_server'], self.config['smtp_port'], timeout=15)
            else:
                print(f"  - 使用 SMTP+TLS 连接 {self.config['smtp_server']}:{self.config['smtp_port']}")
                server = smtplib.SMTP(self.config['smtp_server'], self.config['smtp_port'], timeout=15)
                server.starttls()
            
            print(f"  - 正在登录...")
            server.login(self.config['email_from'], self.config['email_password'])
            print(f"  - 正在发送...")
            server.send_message(msg)
            server.quit()
            print(f"✅ 邮件发送成功: {notification['symbol']}")
            return True
            
        except Exception as e:
            print(f"邮件发送失败: {e}")
            # 邮件发送失败时，使用界面通知作为备用方案
            print("使用界面通知作为备用方案")
            self._show_streamlit_notification(notification)
            return True
    
    def _show_streamlit_notification(self, notification: Dict):
        """在Streamlit界面显示通知"""
        # 使用session_state存储通知
        if 'notifications' not in st.session_state:
            st.session_state.notifications = []
        
        # 避免重复通知，使用symbol代替stock_id
        notification_key = f"{notification['symbol']}_{notification['type']}_{notification['triggered_at']}"
        if notification_key not in [n.get('key') for n in st.session_state.notifications]:
            st.session_state.notifications.append({
                'key': notification_key,
                'symbol': notification['symbol'],
                'name': notification['name'],
                'type': notification['type'],
                'message': notification['message'],
                'timestamp': notification['triggered_at']
            })
    
    def get_streamlit_notifications(self) -> List[Dict]:
        """获取Streamlit界面通知"""
        return st.session_state.get('notifications', [])
    
    def clear_streamlit_notifications(self):
        """清空Streamlit界面通知"""
        if 'notifications' in st.session_state:
            st.session_state.notifications = []
    
    def test_email_config(self) -> bool:
        """测试邮件配置"""
        if not self.config['email_enabled']:
            return False
        
        try:
            if self.config['smtp_port'] == 465:
                server = smtplib.SMTP_SSL(self.config['smtp_server'], self.config['smtp_port'], timeout=10)
            else:
                server = smtplib.SMTP(self.config['smtp_server'], self.config['smtp_port'], timeout=10)
                server.starttls()
            
            server.login(self.config['email_from'], self.config['email_password'])
            server.quit()
            return True
        except Exception as e:
            print(f"邮件配置测试失败: {e}")
            return False
    
    def send_test_email(self) -> tuple[bool, str]:
        """发送测试邮件"""
        try:
            # 检查邮件配置是否完整
            if not all([self.config['smtp_server'], self.config['email_from'], 
                       self.config['email_password'], self.config['email_to']]):
                return False, "邮件配置不完整，请检查.env文件中的邮件设置"
            
            # 创建测试邮件
            msg = MIMEMultipart()
            msg['From'] = self.config['email_from']
            msg['To'] = self.config['email_to']
            msg['Subject'] = "AI股票分析系统 - 邮件测试"
            
            # 邮件正文
            body = f"""
            <html>
            <body>
                <h2>邮件测试成功！</h2>
                <p>这是一封来自AI股票分析系统的测试邮件。</p>
                <p>如果您收到这封邮件，说明邮件通知功能已正常工作。</p>
                <hr>
                <p><strong>邮件配置信息：</strong></p>
                <ul>
                    <li>SMTP服务器: {self.config['smtp_server']}</li>
                    <li>SMTP端口: {self.config['smtp_port']}</li>
                    <li>发送邮箱: {self.config['email_from']}</li>
                    <li>接收邮箱: {self.config['email_to']}</li>
                </ul>
                <hr>
                <p><em>此邮件由AI股票分析系统自动发送</em></p>
            </body>
            </html>
            """
            
            msg.attach(MIMEText(body, 'html'))
            
            # 根据端口选择连接方式
            if self.config['smtp_port'] == 465:
                server = smtplib.SMTP_SSL(self.config['smtp_server'], self.config['smtp_port'], timeout=15)
            else:
                server = smtplib.SMTP(self.config['smtp_server'], self.config['smtp_port'], timeout=15)
                server.starttls()
            
            server.login(self.config['email_from'], self.config['email_password'])
            server.send_message(msg)
            server.quit()
            return True, "测试邮件发送成功！请检查收件箱（包括垃圾邮件箱）。"
            
        except smtplib.SMTPAuthenticationError:
            return False, "邮箱认证失败，请检查邮箱和授权码是否正确"
        except smtplib.SMTPException as e:
            return False, f"SMTP错误: {str(e)}"
        except Exception as e:
            return False, f"发送失败: {str(e)}"
    
    def get_email_config_status(self) -> Dict:
        """获取邮件配置状态"""
        return {
            'enabled': self.config['email_enabled'],
            'smtp_server': self.config['smtp_server'] or '未配置',
            'smtp_port': self.config['smtp_port'],
            'email_from': self.config['email_from'] or '未配置',
            'email_to': self.config['email_to'] or '未配置',
            'configured': all([
                self.config['smtp_server'],
                self.config['email_from'],
                self.config['email_password'],
                self.config['email_to']
            ])
        }
    
    def _send_webhook_notification(self, notification: Dict) -> bool:
        """发送Webhook通知"""
        try:
            # 检查webhook配置是否完整
            if not self.config['webhook_url']:
                print("⚠️ Webhook URL未配置")
                return False
            
            webhook_type = self.config['webhook_type']
            
            if webhook_type == 'dingtalk':
                return self._send_dingtalk_webhook(notification)
            elif webhook_type == 'feishu':
                return self._send_feishu_webhook(notification)
            else:
                print(f"⚠️ 不支持的webhook类型: {webhook_type}")
                return False
        
        except Exception as e:
            print(f"Webhook发送失败: {e}")
            return False
    
    def _send_dingtalk_webhook(self, notification: Dict) -> bool:
        """发送钉钉Webhook通知"""
        try:
            import requests
            
            # 构建钉钉消息格式（包含自定义关键词）
            keyword = self.config.get('webhook_keyword', '')
            title_prefix = f"{keyword} - " if keyword else ""
            content_prefix = f"### {keyword} - " if keyword else "### "
            
            # 构建增强的消息内容
            message_text = f"""{content_prefix}股票监测提醒

**股票代码**: {notification['symbol']}

**股票名称**: {notification['name']}

**📊 实时行情**:
- 当前价格: {notification.get('current_price', 'N/A')}元
- 涨跌幅: {notification.get('change_pct', 'N/A')}%
- 涨跌额: {notification.get('change_amount', 'N/A')}元
- 成交量: {notification.get('volume', 'N/A')}手
- 换手率: {notification.get('turnover_rate', 'N/A')}%

**🎯 AI决策**: {notification['type']}

**📝 分析内容**: {notification['message']}

**💰 持仓信息**:
- 持仓状态: {notification.get('position_status', '未知')}
- 持仓成本: {notification.get('position_cost', 'N/A')}元
- 浮动盈亏: {notification.get('profit_loss_pct', 'N/A')}%

**⏰ 触发时间**: {notification['triggered_at']}

**🕐 交易时段**: {notification.get('trading_session', '未知')}

---

_此消息由AI股票分析系统自动发送_"""
            
            data = {
                "msgtype": "markdown",
                "markdown": {
                    "title": f"{title_prefix}{notification['symbol']} {notification['name']}",
                    "text": message_text
                }
            }
            
            print(f"[钉钉] 正在发送Webhook...")
            print(f"  - URL: {self.config['webhook_url'][:50]}...")
            
            response = requests.post(
                self.config['webhook_url'],
                json=data,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('errcode') == 0:
                    print(f"[成功] 钉钉Webhook发送成功")
                    return True
                else:
                    print(f"[失败] 钉钉Webhook返回错误: {result.get('errmsg')}")
                    return False
            else:
                print(f"[失败] 钉钉Webhook请求失败: HTTP {response.status_code}")
                return False
        
        except Exception as e:
            print(f"钉钉Webhook发送异常: {e}")
            return False
    
    def _send_feishu_webhook(self, notification: Dict) -> bool:
        """发送飞书Webhook通知"""
        try:
            import requests
            
            # 构建飞书消息格式
            data = {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {
                            "content": f"📊 股票监测提醒 - {notification['symbol']}",
                            "tag": "plain_text"
                        },
                        "template": "blue"
                    },
                    "elements": [
                        {
                            "tag": "div",
                            "fields": [
                                {
                                    "is_short": True,
                                    "text": {
                                        "content": f"**股票代码**\n{notification['symbol']}",
                                        "tag": "lark_md"
                                    }
                                },
                                {
                                    "is_short": True,
                                    "text": {
                                        "content": f"**股票名称**\n{notification['name']}",
                                        "tag": "lark_md"
                                    }
                                }
                            ]
                        },
                        {
                            "tag": "div",
                            "fields": [
                                {
                                    "is_short": True,
                                    "text": {
                                        "content": f"**提醒类型**\n{notification['type']}",
                                        "tag": "lark_md"
                                    }
                                },
                                {
                                    "is_short": True,
                                    "text": {
                                        "content": f"**触发时间**\n{notification['triggered_at']}",
                                        "tag": "lark_md"
                                    }
                                }
                            ]
                        },
                        {
                            "tag": "div",
                            "text": {
                                "content": f"**提醒内容**\n{notification['message']}",
                                "tag": "lark_md"
                            }
                        },
                        {
                            "tag": "hr"
                        },
                        {
                            "tag": "note",
                            "elements": [
                                {
                                    "tag": "plain_text",
                                    "content": "此消息由AI股票分析系统自动发送"
                                }
                            ]
                        }
                    ]
                }
            }
            
            print(f"[飞书] 正在发送Webhook...")
            print(f"  - URL: {self.config['webhook_url'][:50]}...")
            
            response = requests.post(
                self.config['webhook_url'],
                json=data,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 0:
                    print(f"[成功] 飞书Webhook发送成功")
                    return True
                else:
                    print(f"[失败] 飞书Webhook返回错误: {result.get('msg')}")
                    return False
            else:
                print(f"[失败] 飞书Webhook请求失败: HTTP {response.status_code}")
                return False
        
        except Exception as e:
            print(f"飞书Webhook发送异常: {e}")
            return False
    
    def send_test_webhook(self) -> tuple[bool, str]:
        """发送测试Webhook"""
        try:
            # 检查webhook配置是否完整
            if not self.config['webhook_url']:
                return False, "Webhook URL未配置，请检查环境变量设置"
            
            # 创建测试通知（包含关键词"aiagents通知"以通过钉钉安全设置）
            test_notification = {
                'symbol': '测试',
                'name': 'Webhook配置测试',
                'type': '系统测试',
                'message': '如果您收到此消息，说明Webhook配置正确！',
                'triggered_at': '刚刚'
            }
            
            webhook_type = self.config['webhook_type']
            
            if webhook_type == 'dingtalk':
                success = self._send_dingtalk_webhook(test_notification)
                if success:
                    return True, "钉钉Webhook测试成功！请检查钉钉群消息。"
                else:
                    return False, "钉钉Webhook发送失败，请检查URL和网络连接"
            
            elif webhook_type == 'feishu':
                success = self._send_feishu_webhook(test_notification)
                if success:
                    return True, "飞书Webhook测试成功！请检查飞书群消息。"
                else:
                    return False, "飞书Webhook发送失败，请检查URL和网络连接"
            
            else:
                return False, f"不支持的webhook类型: {webhook_type}"
        
        except Exception as e:
            return False, f"发送失败: {str(e)}"
    
    def get_webhook_config_status(self) -> Dict:
        """获取Webhook配置状态"""
        return {
            'enabled': self.config['webhook_enabled'],
            'webhook_type': self.config['webhook_type'],
            'webhook_url': self.config['webhook_url'][:50] + '...' if self.config['webhook_url'] else '未配置',
            'configured': bool(self.config['webhook_url'])
        }
    
    def send_portfolio_analysis_notification(self, analysis_results: dict, sync_result: dict = None) -> bool:
        """
        发送持仓分析完成通知
        
        Args:
            analysis_results: 批量分析结果
            sync_result: 监测同步结果（可选）
            
        Returns:
            是否发送成功
        """
        try:
            # 构建通知内容
            total = analysis_results.get("total", 0)
            succeeded = len([r for r in analysis_results.get("results", []) if r.get("result", {}).get("success")])
            failed = total - succeeded
            elapsed_time = analysis_results.get("elapsed_time", 0)
            results = analysis_results.get("results", [])
            
            # 邮件主题
            subject = f"持仓定时分析完成 - 共{total}只股票"
            
            # 构建邮件正文（HTML格式）
            html_body = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; }}
                    .summary {{ background-color: #f0f8ff; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
                    .stock {{ border: 1px solid #ddd; padding: 10px; margin-bottom: 10px; border-radius: 5px; }}
                    .success {{ color: green; }}
                    .failed {{ color: red; }}
                    .rating-buy {{ color: #28a745; font-weight: bold; }}
                    .rating-hold {{ color: #ffc107; font-weight: bold; }}
                    .rating-sell {{ color: #dc3545; font-weight: bold; }}
                </style>
            </head>
            <body>
                <h2>持仓定时分析完成</h2>
                <div class="summary">
                    <h3>分析概况</h3>
                    <p>总数: {total} 只</p>
                    <p class="success">成功: {succeeded} 只</p>
                    <p class="failed">失败: {failed} 只</p>
                    <p>耗时: {elapsed_time:.2f} 秒</p>
            """
            
            # 添加监测同步结果
            if sync_result:
                html_body += f"""
                    <h3>监测同步结果</h3>
                    <p>新增监测: {sync_result.get('added', 0)} 只</p>
                    <p>更新监测: {sync_result.get('updated', 0)} 只</p>
                    <p>同步失败: {sync_result.get('failed', 0)} 只</p>
                """
            
            html_body += """
                </div>
                <h3>分析结果详情</h3>
            """
            
            # 添加每只股票的详细结果
            for item in results[:10]:  # 只显示前10只
                code = item.get("code", "")
                result = item.get("result", {})
                
                if result.get("success"):
                    final_decision = result.get("final_decision", {})
                    stock_info = result.get("stock_info", {})
                    
                    # 使用正确的字段名
                    rating = final_decision.get("rating", "未知")
                    confidence = final_decision.get("confidence_level", "N/A")
                    entry_range = final_decision.get("entry_range", "N/A")
                    take_profit = final_decision.get("take_profit", "N/A")
                    stop_loss = final_decision.get("stop_loss", "N/A")
                    
                    # 评级颜色
                    rating_class = "rating-hold"
                    if "强烈买入" in rating or "买入" in rating:
                        rating_class = "rating-buy"
                    elif "卖出" in rating:
                        rating_class = "rating-sell"
                    
                    html_body += f"""
                    <div class="stock">
                        <h4>{code} {stock_info.get('name', '')} - <span class="{rating_class}">{rating}</span> (信心度: {confidence})</h4>
                        <p>进场区间: {entry_range}</p>
                        <p>止盈位: {take_profit} | 止损位: {stop_loss}</p>
                    </div>
                    """
                else:
                    error = result.get("error", "未知错误")
                    html_body += f"""
                    <div class="stock">
                        <h4 class="failed">{code} - 分析失败</h4>
                        <p>错误: {error}</p>
                    </div>
                    """
            
            if len(results) > 10:
                html_body += f"<p>...还有 {len(results) - 10} 只股票未显示</p>"
            
            html_body += """
            </body>
            </html>
            """
            
            # 构建纯文本版本
            text_body = f"""
持仓定时分析完成

分析概况:
- 总数: {total} 只
- 成功: {succeeded} 只
- 失败: {failed} 只
- 耗时: {elapsed_time:.2f} 秒
"""
            
            if sync_result:
                text_body += f"""
监测同步结果:
- 新增监测: {sync_result.get('added', 0)} 只
- 更新监测: {sync_result.get('updated', 0)} 只
- 同步失败: {sync_result.get('failed', 0)} 只
"""
            
            text_body += "\n分析结果详情:\n"
            for item in results[:10]:
                code = item.get("code", "")
                result = item.get("result", {})
                
                if result.get("success"):
                    final_decision = result.get("final_decision", {})
                    stock_info = result.get("stock_info", {})
                    # 使用正确的字段名
                    rating = final_decision.get("rating", "未知")
                    text_body += f"- {code} {stock_info.get('name', '')}: {rating}\n"
                else:
                    error = result.get("error", "未知错误")
                    text_body += f"- {code}: 分析失败 ({error})\n"
            
            success = False
            
            # 发送邮件
            if self.config['email_enabled']:
                email_success = self._send_custom_email(subject, html_body, text_body)
                if email_success:
                    success = True
                    print("[OK] 邮件通知发送成功")
            
            # 发送Webhook
            if self.config['webhook_enabled']:
                webhook_success = self._send_portfolio_webhook(analysis_results, sync_result)
                if webhook_success:
                    success = True
                    print("[OK] Webhook通知发送成功")
            
            return success
            
        except Exception as e:
            print(f"[ERROR] 发送持仓分析通知失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return False
    
    def _send_custom_email(self, subject: str, html_body: str, text_body: str) -> bool:
        """发送自定义邮件"""
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = self.config['email_from']
            msg['To'] = self.config['email_to']
            msg['Subject'] = subject
            
            part1 = MIMEText(text_body, 'plain', 'utf-8')
            part2 = MIMEText(html_body, 'html', 'utf-8')
            
            msg.attach(part1)
            msg.attach(part2)
            
            with smtplib.SMTP(self.config['smtp_server'], self.config['smtp_port']) as server:
                server.starttls()
                server.login(self.config['email_from'], self.config['email_password'])
                server.send_message(msg)
            
            return True
            
        except Exception as e:
            print(f"[ERROR] 邮件发送失败: {str(e)}")
            return False
    
    def _send_portfolio_webhook(self, analysis_results: dict, sync_result: dict = None) -> bool:
        """发送持仓分析Webhook通知"""
        try:
            import requests
            
            total = analysis_results.get("total", 0)
            succeeded = len([r for r in analysis_results.get("results", []) if r.get("result", {}).get("success")])
            failed = total - succeeded
            elapsed_time = analysis_results.get("elapsed_time", 0)
            
            # 构建Markdown消息
            content = f"### 持仓定时分析完成\\n\\n"
            content += f"**分析概况**\\n"
            content += f"- 总数: {total} 只\\n"
            content += f"- 成功: {succeeded} 只\\n"
            content += f"- 失败: {failed} 只\\n"
            content += f"- 耗时: {elapsed_time:.2f} 秒\\n\\n"
            
            if sync_result:
                content += f"**监测同步**\\n"
                content += f"- 新增: {sync_result.get('added', 0)} 只\\n"
                content += f"- 更新: {sync_result.get('updated', 0)} 只\\n\\n"
            
            # 根据webhook类型构建请求
            if self.config['webhook_type'] == 'dingtalk':
                data = {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": f"{self.config['webhook_keyword']}",
                        "text": f"{self.config['webhook_keyword']}\\n\\n{content}"
                    }
                }
            else:  # feishu
                data = {
                    "msg_type": "text",
                    "content": {
                        "text": content
                    }
                }
            
            response = requests.post(self.config['webhook_url'], json=data, timeout=10)
            return response.status_code == 200
            
        except Exception as e:
            print(f"[ERROR] Webhook发送失败: {str(e)}")
            return False

    def send_longhubang_chief_report(self, chief_result: Dict, report_meta: Dict = None) -> bool:
        """
        发送龙虎榜「首席策略师」报告到Webhook（仅推送核心三段）。

        Args:
            chief_result: 首席策略师结果字典，至少包含 analysis
            report_meta: 附加元信息，如 report_id/date_range/timestamp

        Returns:
            bool: 是否全部发送成功
        """
        try:
            # 重新加载配置，确保读取到Web端最新保存的.env配置
            self.config = self._load_config()
            if not self.config.get("webhook_enabled"):
                print("[智瞰龙虎] Webhook未启用，跳过首席策略师报告推送")
                return False
            if not self.config.get("webhook_url"):
                print("[智瞰龙虎] Webhook URL未配置，跳过首席策略师报告推送")
                return False

            analysis = str((chief_result or {}).get("analysis", "") or "").strip()
            if not analysis:
                print("[智瞰龙虎] 首席策略师报告为空，跳过Webhook推送")
                return False

            report_meta = report_meta or {}
            report_ts = str(report_meta.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            report_id = report_meta.get("report_id")
            date_range = str(report_meta.get("date_range") or "未知")
            fallback_recommended = report_meta.get("recommended_stocks", []) or []

            sections = self._split_longhubang_report_layers(analysis)
            selected = self._pick_longhubang_notify_sections(sections)
            rec_section = selected.get("recommended", "")
            risk_section = selected.get("risk", "")
            theme_section = selected.get("theme", "")

            recommended_rows = self._extract_stock_rows_for_table(
                rec_section,
                fallback_stocks=fallback_recommended,
                max_rows=8,
            )

            header_lines = [
                "### 🎯 智瞰龙虎·次日交易要点",
                f"- 报告时间: {report_ts}",
                f"- 数据范围: {date_range}",
                f"- 报告ID: {report_id if report_id is not None else '未生成'}",
                "- 推送内容: 次日推荐股票 / 高风险警示股票 / 热点题材总结",
                "",
                "> 提示: 以下为结构化摘要，完整分析以系统内报告为准。",
            ]
            header_ok = self._send_structured_webhook_message(
                title="智瞰龙虎核心交易提醒",
                text="\n".join(header_lines),
            )
            if not header_ok:
                print("[智瞰龙虎] 核心交易提醒头消息发送失败")

            failed = 0
            sent = 1 if header_ok else 0

            recommended_text = self._build_stock_table_message(
                title="🌟 次日推荐股票（表格）",
                rows=recommended_rows,
                empty_hint="未从文本中识别到明确推荐标的，请在系统内查看完整报告。",
            )
            risk_text = self._build_plain_section_message(
                title="⚠️ 高风险警示股票",
                content=risk_section,
                empty_hint="未识别到明确高风险标的，请在系统内查看完整报告。",
                max_lines=10,
            )
            theme_text = self._build_plain_section_message(
                title="🔥 热点题材总结",
                content=theme_section,
                empty_hint="未识别到明确题材分层，请在系统内查看完整报告。",
                max_lines=10,
            )

            for section_title, section_text in [
                ("次日推荐股票", recommended_text),
                ("高风险警示股票", risk_text),
                ("热点题材总结", theme_text),
            ]:
                ok = self._send_structured_webhook_message(
                    title=f"智瞰龙虎 - {section_title}",
                    text=section_text,
                )
                if ok:
                    sent += 1
                else:
                    failed += 1

            print(
                f"[智瞰龙虎] 首席策略师核心摘要Webhook推送完成: sent={sent}, failed={failed}"
            )
            return failed == 0 and sent > 0
        except Exception as e:
            print(f"[智瞰龙虎] 首席策略师报告Webhook推送异常: {e}")
            return False

    def _pick_longhubang_notify_sections(self, sections: List[Dict[str, str]]) -> Dict[str, str]:
        """仅保留通知所需三段：推荐/风险/题材。"""
        picked = {"recommended": "", "risk": "", "theme": ""}
        for sec in sections or []:
            title = str(sec.get("title") or "").lower()
            content = str(sec.get("content") or "").strip()
            if not content:
                continue
            if (not picked["recommended"]) and (
                ("推荐股票" in title) or ("次日重点推荐" in title) or ("次日推荐" in title)
            ):
                picked["recommended"] = content
                continue
            if (not picked["risk"]) and (
                ("高风险警示" in title) or ("风险警示" in title) or ("高风险股票" in title)
            ):
                picked["risk"] = content
                continue
            if (not picked["theme"]) and (
                ("热点题材总结" in title) or ("热点题材" in title) or ("题材总结" in title)
            ):
                picked["theme"] = content

        # 兜底：标题没命中时，尝试全文关键词匹配
        joined = "\n".join([str(s.get("content") or "") for s in (sections or [])])
        if not picked["recommended"] and "推荐股票" in joined:
            picked["recommended"] = joined
        if not picked["risk"] and ("高风险" in joined or "风险警示" in joined):
            picked["risk"] = joined
        if not picked["theme"] and "题材" in joined:
            picked["theme"] = joined
        return picked

    def _extract_stock_rows_for_table(
        self,
        text: str,
        fallback_stocks: List[Dict] = None,
        max_rows: int = 8,
    ) -> List[List[str]]:
        """从文本中提取股票并构造成表格行：[序号, 股票, 代码, 关键信息]。"""
        rows: List[List[str]] = []
        seen = set()
        lines = [ln.strip() for ln in str(text or "").split("\n") if ln.strip()]
        for line in lines:
            clean_line = re.sub(r"^[\-\*\d\.\、\s]+", "", line)
            code_match = re.search(r"(?<!\d)(\d{6})(?:\.(?:SH|SZ|BJ))?(?!\d)", clean_line, re.IGNORECASE)
            if not code_match:
                continue
            code = code_match.group(1)
            if code in seen:
                continue
            name = self._extract_stock_name_from_line(clean_line, code)
            detail = self._extract_stock_note_from_line(clean_line, name, code)
            rows.append([str(len(rows) + 1), name or "未知", code, detail or "关注资金与量价配合"])
            seen.add(code)
            if len(rows) >= max_rows:
                break

        if rows:
            return rows

        for item in (fallback_stocks or []):
            code = str(item.get("code") or "").strip()
            if not (len(code) == 6 and code.isdigit()) or code in seen:
                continue
            name = str(item.get("name") or "未知").strip()
            reason = str(item.get("reason") or "").strip()
            confidence = str(item.get("confidence") or "").strip()
            note = reason
            if confidence:
                note = f"{note} | 确定性:{confidence}" if note else f"确定性:{confidence}"
            rows.append([str(len(rows) + 1), name, code, note or "关注次日承接与量能"])
            seen.add(code)
            if len(rows) >= max_rows:
                break
        return rows

    def _extract_stock_name_from_line(self, line: str, code: str) -> str:
        patterns = [
            rf"([A-Za-z0-9\u4e00-\u9fa5\*ST\-]{{2,20}})\s*[\(（]\s*{re.escape(code)}(?:\.[A-Za-z]{{2}})?\s*[\)）]",
            rf"([A-Za-z0-9\u4e00-\u9fa5\*ST\-]{{2,20}})\s+{re.escape(code)}(?!\d)",
        ]
        for p in patterns:
            m = re.search(p, line, re.IGNORECASE)
            if m:
                return str(m.group(1)).strip()
        return ""

    def _extract_stock_note_from_line(self, line: str, name: str, code: str) -> str:
        note = str(line or "")
        if name:
            note = re.sub(re.escape(name), "", note, flags=re.IGNORECASE)
        note = re.sub(rf"[\(（]?\s*{re.escape(code)}(?:\.[A-Za-z]{{2}})?\s*[\)）]?", "", note, flags=re.IGNORECASE)
        note = re.sub(r"^[\-\*\d\.\、\s]+", "", note)
        note = re.sub(r"\s+", " ", note).strip(" -:：|")
        return note[:60]

    def _extract_theme_rows_for_table(self, text: str, max_rows: int = 6) -> List[List[str]]:
        """从题材段落提取表格行：[序号, 题材, 代表标的, 研判要点]。"""
        rows: List[List[str]] = []
        seen_theme = set()
        lines = [ln.strip() for ln in str(text or "").split("\n") if ln.strip()]
        for line in lines:
            clean = re.sub(r"^[\-\*\d\.\、\s]+", "", line)
            if len(clean) < 4:
                continue
            if "题材" not in clean and ("：" not in clean and ":" not in clean):
                continue

            if "：" in clean:
                theme, detail = clean.split("：", 1)
            elif ":" in clean:
                theme, detail = clean.split(":", 1)
            else:
                theme, detail = clean[:12], clean

            theme = theme.strip("【】[]()（） ").strip()
            if not theme or theme in seen_theme:
                continue
            stocks = "、".join(self._extract_stock_mentions(detail, max_items=3)) or "-"
            view = re.sub(r"\s+", " ", detail).strip()[:58]
            rows.append([str(len(rows) + 1), theme[:16], stocks, view or "关注持续性与分歧转一致"])
            seen_theme.add(theme)
            if len(rows) >= max_rows:
                break
        return rows

    def _extract_stock_mentions(self, text: str, max_items: int = 3) -> List[str]:
        mentions: List[str] = []
        seen = set()
        raw = str(text or "")
        # 优先匹配 名称(代码)
        for m in re.finditer(
            r"([A-Za-z0-9\u4e00-\u9fa5\*ST\-]{2,20})\s*[\(（]\s*(\d{6})(?:\.[A-Za-z]{2})?\s*[\)）]",
            raw,
            re.IGNORECASE,
        ):
            name = str(m.group(1)).strip()
            code = str(m.group(2)).strip()
            key = f"{name}_{code}"
            if key in seen:
                continue
            seen.add(key)
            mentions.append(f"{name}({code})")
            if len(mentions) >= max_items:
                return mentions

        # 再匹配裸代码
        for m in re.finditer(r"(?<!\d)(\d{6})(?!\d)", raw):
            code = str(m.group(1)).strip()
            key = f"_{code}"
            if key in seen:
                continue
            seen.add(key)
            mentions.append(code)
            if len(mentions) >= max_items:
                break
        return mentions

    def _build_stock_table_message(self, title: str, rows: List[List[str]], empty_hint: str) -> str:
        if not rows:
            rows = [["-", "暂无", "-", empty_hint]]
        table = self._to_markdown_table(
            headers=["序号", "股票", "代码", "关键信息"],
            rows=rows,
        )
        lines = [
            f"### {title}",
            "",
            table,
            "",
            "> 建议结合盘口强弱、量价结构与仓位纪律执行。",
        ]
        return "\n".join(lines).strip()

    def _build_plain_section_message(
        self, title: str, content: str, empty_hint: str, max_lines: int = 10
    ) -> str:
        text = str(content or "").strip()
        bullets: List[str] = []
        if text:
            lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
            for line in lines:
                # 跳过纯标题行，避免重复
                if self._match_layer_title(line):
                    continue
                clean = re.sub(r"^[\-\*\d\.\、\s]+", "", line).strip()
                if not clean:
                    continue
                bullets.append(clean)
                if len(bullets) >= max_lines:
                    break

        if not bullets:
            bullets = [empty_hint]

        body_lines = [f"- {item}" for item in bullets]
        lines = [
            f"### {title}",
            "",
            "\n".join(body_lines),
            "",
            "> 以上为文本摘要，详情以系统完整报告为准。",
        ]
        return "\n".join(lines).strip()

    def _to_markdown_table(self, headers: List[str], rows: List[List[str]]) -> str:
        safe_headers = [self._escape_table_cell(x) for x in headers]
        lines = [
            "| " + " | ".join(safe_headers) + " |",
            "| " + " | ".join(["---"] * len(safe_headers)) + " |",
        ]
        for row in rows:
            safe_row = [self._escape_table_cell(str(col)) for col in row]
            if len(safe_row) < len(safe_headers):
                safe_row += ["-"] * (len(safe_headers) - len(safe_row))
            lines.append("| " + " | ".join(safe_row[: len(safe_headers)]) + " |")
        return "\n".join(lines)

    def _escape_table_cell(self, text: str) -> str:
        return str(text or "").replace("\n", " ").replace("|", "¦").strip()

    def _split_longhubang_report_layers(self, report_text: str) -> List[Dict[str, str]]:
        """
        按报告层级切分内容，优先识别「1. xxx」「2、xxx」「## xxx」等标题行。
        """
        text = str(report_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            return []

        sections: List[Dict[str, str]] = []
        current_title = "首席策略师综合报告"
        current_lines: List[str] = []

        def flush_current() -> None:
            content = "\n".join(current_lines).strip()
            if content:
                sections.append({"title": current_title, "content": content})

        for raw_line in text.split("\n"):
            line = raw_line.rstrip()
            title = self._match_layer_title(line)
            if title:
                flush_current()
                current_title = title
                current_lines = [line]
            else:
                current_lines.append(line)

        flush_current()
        if not sections:
            return [{"title": "首席策略师综合报告", "content": text}]
        return sections

    def _match_layer_title(self, line: str) -> str:
        stripped = str(line or "").strip()
        if not stripped:
            return ""

        # 1. 标准序号标题：1. xxx / 1、xxx
        m = re.match(r"^([0-9]{1,2})[\.\、]\s*(.+?)\s*$", stripped)
        if m:
            return f"{m.group(1)}. {m.group(2)}"

        # 2. Markdown标题：## xxx
        m = re.match(r"^#{1,3}\s*(.+?)\s*$", stripped)
        if m:
            return m.group(1)

        # 3. 单行加粗标题：**xxx**
        m = re.match(r"^\*\*(.+?)\*\*$", stripped)
        if m:
            title = m.group(1).strip()
            if 2 <= len(title) <= 40:
                return title
        return ""

    def _chunk_text_for_webhook(self, text: str, max_chars: int = 1700) -> List[str]:
        """
        按段落切分长文本，尽量保证每段不超过 max_chars，避免Webhook消息体过长。
        """
        body = str(text or "").strip()
        if not body:
            return []
        if len(body) <= max_chars:
            return [body]

        paragraphs = [p for p in body.split("\n\n") if p.strip()]
        chunks: List[str] = []
        current = ""

        for para in paragraphs:
            candidate = para.strip()
            if not candidate:
                continue
            if not current:
                if len(candidate) <= max_chars:
                    current = candidate
                else:
                    # 单段过长时按字符硬切
                    for i in range(0, len(candidate), max_chars):
                        chunks.append(candidate[i : i + max_chars])
            else:
                merged = f"{current}\n\n{candidate}"
                if len(merged) <= max_chars:
                    current = merged
                else:
                    chunks.append(current)
                    if len(candidate) <= max_chars:
                        current = candidate
                    else:
                        for i in range(0, len(candidate), max_chars):
                            chunks.append(candidate[i : i + max_chars])
                        current = ""

        if current:
            chunks.append(current)
        return chunks if chunks else [body]

    def _send_structured_webhook_message(self, title: str, text: str) -> bool:
        """发送一条结构化Webhook消息（兼容钉钉/飞书）。"""
        try:
            import requests

            webhook_type = (self.config.get("webhook_type") or "dingtalk").lower()
            webhook_url = self.config.get("webhook_url", "")
            keyword = str(self.config.get("webhook_keyword", "") or "").strip()
            if not webhook_url:
                return False

            safe_title = str(title or "消息").strip()
            safe_text = str(text or "").strip()

            if webhook_type == "dingtalk":
                title_prefix = f"{keyword} - " if keyword else ""
                text_prefix = f"{keyword}\n\n" if keyword else ""
                payload = {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": f"{title_prefix}{safe_title}",
                        "text": f"{text_prefix}{safe_text}",
                    },
                }
            elif webhook_type == "feishu":
                title_prefix = f"【{keyword}】" if keyword else ""
                payload = {
                    "msg_type": "interactive",
                    "card": {
                        "header": {
                            "title": {
                                "content": f"{title_prefix}{safe_title}",
                                "tag": "plain_text",
                            },
                            "template": "blue",
                        },
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": safe_text,
                            },
                            {
                                "tag": "note",
                                "elements": [
                                    {
                                        "tag": "plain_text",
                                        "content": "此消息由AI股票分析系统自动发送，仅供参考",
                                    }
                                ],
                            },
                        ],
                    },
                }
            else:
                print(f"[智瞰龙虎] 不支持的Webhook类型: {webhook_type}")
                return False

            resp = requests.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code != 200:
                return False

            try:
                data = resp.json()
            except Exception:
                return True

            if webhook_type == "dingtalk":
                return data.get("errcode") == 0
            if webhook_type == "feishu":
                return data.get("code", 0) == 0
            return True
        except Exception:
            return False

# 全局通知服务实例
notification_service = NotificationService()





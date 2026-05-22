# -*- coding: utf-8 -*-

import random
import time
import json
import urllib.request
import urllib.error

# 尝试导入 MoviePilot 核心基类和事件总线，若失败则生成 Mock 类以防本地编译/测试失败
try:
    from plugins import _PluginBase
    from app.core.event import eventmanager
    from app.schemas.types import EventType
    from app.core.event import Event
except ImportError:
    class _PluginBase:
        def __init__(self):
            import logging
            self.logger = logging.getLogger("railguncheckin_mock")
            logging.basicConfig(level=logging.INFO)
            self._config = {}
            self.plugin_name = "MockPlugin"

        def get_config(self):
            return self._config

        def register_task(self, name, func, cron=None):
            self.logger.info(f"[Mock] 注册定时任务: '{name}', 规则 (Cron): '{cron}'")

    class eventmanager:
        @staticmethod
        def register(event_type):
            def decorator(func):
                return func
            return decorator
        
        @staticmethod
        def send_event(event_type, event_data):
            pass

    class EventType:
        PluginAction = "PluginAction"
        NoticeMessage = "NoticeMessage"

    class Event:
        def __init__(self, event_data=None):
            self.event_data = event_data


# 随机模拟的现代浏览器 User-Agent 和关联的 Client Hints 头部组合
UA_ENVIRONMENTS = [
    {
        "name": "Chrome (Windows)",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="122", "Not(A:Bar";v="24", "Google Chrome";v="122"',
        "sec-ch-ua-platform": '"Windows"'
    },
    {
        "name": "Edge (Windows)",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
        "sec-ch-ua": '"Chromium";v="122", "Not(A:Bar";v="24", "Microsoft Edge";v="122"',
        "sec-ch-ua-platform": '"Windows"'
    },
    {
        "name": "Firefox (Windows)",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0"
    },
    {
        "name": "Chrome (macOS)",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="122", "Not(A:Bar";v="24", "Google Chrome";v="122"',
        "sec-ch-ua-platform": '"macOS"'
    },
    {
        "name": "Safari (macOS)",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15"
    }
]


class RailgunCheckin(_PluginBase):
    def __init__(self):
        super().__init__()
        self.plugin_name = "Railgun 自动签到"

    def init_plugin(self):
        """插件启用时自动调用，用于配置和注册定时任务"""
        config = self.get_config() or {}
        cron_expr = config.get("cron", "0 8 * * *")
        
        # 挂载定时签到任务
        self.register_task(
            name="railgun_checkin_task",
            func=self.checkin_task,
            cron=cron_expr
        )
        self.logger.info(f"[{self.plugin_name}] 插件已成功启用，签到定时表达式为: {cron_expr}")

    def get_command(self):
        """定义向系统注册的远程控制命令，可由微信/Telegram 等机器人接收"""
        try:
            from app.schemas.types import EventType
            event_type = EventType.PluginAction
        except ImportError:
            event_type = "PluginAction"

        return [{
            "cmd": "/railgun_checkin",
            "event": event_type,
            "desc": "执行 Railgun 自动签到",
            "category": "工具",
            "data": {
                "action": "railgun_checkin_action"
            }
        }]

    @eventmanager.register(EventType.PluginAction)
    def handle_plugin_action(self, event):
        """监听并处理来自机器人的远程 PluginAction 事件"""
        event_data = event.event_data
        if not event_data or event_data.get("action") != "railgun_checkin_action":
            return
            
        self.logger.info(f"[{self.plugin_name}] 收到远程控制命令 /railgun_checkin，开始执行即时签到...")
        # 远程用户指令触发时，直接跳过随机延时
        self.checkin_task(ignore_delay=True)

    def checkin_task(self, ignore_delay=False):
        """核心定时签到任务"""
        config = self.get_config() or {}
        cookie = config.get("cookie")
        token = config.get("token", "railgun.info")

        if not cookie:
            err_msg = "签到失败：未在插件设置中填写 Koa Session Cookie！"
            self.logger.error(f"[{self.plugin_name}] {err_msg}")
            self.send_notification("签到失败", err_msg)
            return

        # 1. 随机延迟以模拟人类行为（若是用户远程手动触发则忽略延迟）
        enable_random_delay = config.get("enable_random_delay", True)
        if enable_random_delay and not ignore_delay:
            min_delay = max(0, int(config.get("min_delay_seconds", 0)))
            max_delay = max(min_delay, int(config.get("max_delay_seconds", 1800)))
            
            if max_delay > min_delay:
                delay = random.randint(min_delay, max_delay)
                self.logger.info(f"[{self.plugin_name}] 计划随机延迟 {delay} 秒后发起签到...")
                time.sleep(delay)

        # 2. 发起签到请求
        self.logger.info(f"[{self.plugin_name}] 正在向 Railgun 服务器发送签到请求...")
        
        url = "https://railgun.info/api/user/checkin"
        env = random.choice(UA_ENVIRONMENTS)
        self.logger.info(f"[{self.plugin_name}] 随机伪装浏览器: {env['name']}")

        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "cache-control": "no-cache",
            "content-type": "application/json;charset=UTF-8",
            "cookie": cookie,
            "dnt": "1",
            "origin": "https://railgun.info",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": env["user-agent"]
        }

        # 补全 Client Hints 头部
        if "sec-ch-ua" in env:
            headers["sec-ch-ua"] = env["sec-ch-ua"]
            headers["sec-ch-ua-mobile"] = "?0"
            headers["sec-ch-ua-platform"] = env["sec-ch-ua-platform"]

        post_data = {"token": token}

        try:
            data_bytes = json.dumps(post_data).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            
            with urllib.request.urlopen(req, timeout=30) as response:
                status_code = response.getcode()
                response_body = response.read().decode("utf-8")
                
                self.logger.info(f"[{self.plugin_name}] 接口 HTTP 状态码: {status_code}")
                try:
                    result_json = json.loads(response_body)
                    # 提取成功消息和历史
                    code = result_json.get("code")
                    message = result_json.get("message", "无响应消息")
                    
                    if code == 1:
                        success_msg = f"签到执行成功！\n消息描述: {message}\n模拟浏览器: {env['name']}"
                        self.logger.info(f"[{self.plugin_name}] {success_msg}")
                        self.send_notification("签到成功", success_msg)
                    else:
                        fail_msg = f"签到结果未成功 (code={code})！\n消息描述: {message}\n模拟浏览器: {env['name']}"
                        self.logger.warning(f"[{self.plugin_name}] {fail_msg}")
                        self.send_notification("签到警告", fail_msg)
                        
                except json.JSONDecodeError:
                    parse_err = f"接口请求成功，但返回数据无法解析为 JSON。\n返回内容:\n{response_body}"
                    self.logger.error(f"[{self.plugin_name}] {parse_err}")
                    self.send_notification("签到失败", parse_err)
                    
        except urllib.error.HTTPError as e:
            err_details = f"请求失败，HTTP 状态码: {e.code}"
            try:
                err_body = e.read().decode("utf-8")
                err_json = json.loads(err_body)
                err_details += f"\n接口返回错误信息: {err_json.get('message', err_body)}"
            except Exception:
                err_details += f"\n错误原因: {e.reason}"
            self.logger.error(f"[{self.plugin_name}] {err_details}")
            self.send_notification("签到异常", err_details)
            
        except urllib.error.URLError as e:
            net_err = f"网络连接失败，请检查网络设置或目标网站是否可达。原因: {e.reason}"
            self.logger.error(f"[{self.plugin_name}] {net_err}")
            self.send_notification("签到失败 (网络异常)", net_err)
            
        except Exception as e:
            unknown_err = f"发生未知异常: {e}"
            self.logger.error(f"[{self.plugin_name}] {unknown_err}")
            self.send_notification("签到失败 (系统异常)", unknown_err)

    def send_notification(self, title_suffix, content):
        """向 MoviePilot 内置的事件总线发送 NoticeMessage，以此触发已配置机器人的通知"""
        full_title = f"{self.plugin_name} - {title_suffix}"
        
        # 尝试通过事件总线发送通知
        try:
            from app.core.event import eventmanager
            from app.schemas.types import EventType
            
            eventmanager.send_event(
                EventType.NoticeMessage,
                {
                    "title": full_title,
                    "text": content,
                    "image": None,
                    "userid": None
                }
            )
            self.logger.info(f"[{self.plugin_name}] 签到通知已成功投递到 MoviePilot 事件总线。")
        except ImportError:
            # 兼容本地调试
            self.logger.info(f"[Mock 消息投递] 标题: {full_title}\n内容:\n{content}")
        except Exception as e:
            self.logger.error(f"[{self.plugin_name}] 消息通知投递失败: {e}")

    def stop_plugin(self):
        """插件停用"""
        self.logger.info(f"[{self.plugin_name}] 插件已停用。")

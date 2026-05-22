# -*- coding: utf-8 -*-
"""
MoviePilot v2 插件 - GLaDOS 系列站点自动签到
功能：定时随机延时签到 GLaDOS 系列站点（railgun.info / glados-facility.com 等），
支持自定义站点域名、远程命令触发及机器人通知。可通过 MoviePilot 插件分身功能同时管理多个账号。
"""

import random
import time
import json
import urllib.request
import urllib.error
import threading
from typing import Any, List, Dict, Tuple, Optional

from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger


# ─── 随机模拟的现代浏览器 User-Agent 和关联的 Client Hints 头部组合 ───

UA_ENVIRONMENTS = [
    {
        "name": "Chrome 126 (Windows)",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="126", "Not A(Brand";v="8", "Google Chrome";v="126"',
        "sec-ch-ua-platform": '"Windows"'
    },
    {
        "name": "Chrome 122 (Windows)",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "sec-ch-ua-platform": '"Windows"'
    },
    {
        "name": "Edge 122 (Windows)",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
        "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Microsoft Edge";v="122"',
        "sec-ch-ua-platform": '"Windows"'
    },
    {
        "name": "Firefox 123 (Windows)",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0"
    },
    {
        "name": "Chrome 122 (macOS)",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "sec-ch-ua-platform": '"macOS"'
    },
    {
        "name": "Safari 17.3 (macOS)",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15"
    },
    {
        "name": "Chrome 126 (Linux)",
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="126", "Not A(Brand";v="8", "Google Chrome";v="126"',
        "sec-ch-ua-platform": '"Linux"'
    }
]


class RailgunCheckin(_PluginBase):
    """
    GLaDOS 系列站点自动签到插件（支持 railgun.info / glados-facility.com 等）
    """

    # ─── 插件元数据（类属性，MoviePilot 读取这些属性来显示名称、描述等） ───
    plugin_name = "GLaDOS 自动签到"
    plugin_desc = "定时随机延时签到 GLaDOS 系列站点，支持自定义域名、远程命令及机器人通知。"
    plugin_icon = "https://raw.githubusercontent.com/rolay/MoviePilot-Plugins/main/icons/railguncheckin.png"
    plugin_version = "1.3.0"
    plugin_author = "rolay"
    author_url = "https://github.com/rolay"
    plugin_config_prefix = "railguncheckin_"
    plugin_order = 30
    auth_level = 1

    # ─── 私有配置属性 ───
    _enabled: bool = False
    _notify: bool = True
    _onlyonce: bool = False
    _cron: str = ""
    _site_domain: str = "railgun.info"
    _cookie: str = ""
    _token: str = ""
    _enable_random_delay: bool = True
    _min_delay_seconds: int = 0
    _max_delay_seconds: int = 1800

    # 调度器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        """
        插件初始化入口，加载配置。
        """
        # 停止现有服务
        self.stop_service()

        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", True)
            self._onlyonce = config.get("onlyonce", False)
            self._cron = config.get("cron", "")
            self._site_domain = (config.get("site_domain", "") or "railgun.info").strip().lower()
            self._cookie = config.get("cookie", "")
            self._token = config.get("token", "") or ""
            self._enable_random_delay = config.get("enable_random_delay", True)
            self._min_delay_seconds = int(config.get("min_delay_seconds", 0))
            self._max_delay_seconds = int(config.get("max_delay_seconds", 1800))

        # 立即运行一次
        if self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
            logger.info(f"{self.plugin_name} - 开启立即运行一次模式")
            self._scheduler.add_job(
                func=self._do_checkin_task,
                trigger="date",
                name=f"{self.plugin_name} 立即执行"
            )
            # 关闭开关并保存
            self._onlyonce = False
            self.__update_config()
            # 启动
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    def get_state(self) -> bool:
        """返回插件是否启用"""
        return self._enabled

    def __update_config(self):
        """保存当前配置到 MoviePilot"""
        self.update_config({
            "enabled": self._enabled,
            "notify": self._notify,
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "site_domain": self._site_domain,
            "cookie": self._cookie,
            "token": self._token,
            "enable_random_delay": self._enable_random_delay,
            "min_delay_seconds": self._min_delay_seconds,
            "max_delay_seconds": self._max_delay_seconds,
        })

    # ─── 定时服务注册 ───

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册定时服务，MoviePilot 会自动调度。
        """
        if self._enabled and self._cron:
            try:
                return [{
                    "id": "RailgunCheckin",
                    "name": f"{self.plugin_name} 定时任务",
                    "trigger": CronTrigger.from_crontab(self._cron),
                    "func": self._do_checkin_task,
                    "kwargs": {}
                }]
            except Exception as e:
                logger.error(f"{self.plugin_name} - Cron 表达式配置错误: {e}")
        return []

    # ─── 远程命令注册 ───

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        向 MoviePilot 注册远程控制命令。
        用户可通过微信/Telegram 等发送 /railgun_checkin 触发即时签到。
        """
        return [{
            "cmd": "/railgun_checkin",
            "event": EventType.PluginAction,
            "desc": "Railgun 签到",
            "category": "工具",
            "data": {
                "action": "railgun_checkin_action"
            }
        }]

    # ─── 远程命令事件监听 ───

    @eventmanager.register(EventType.PluginAction)
    def handle_plugin_action(self, event: Event):
        """
        监听 PluginAction 事件，处理来自机器人的远程签到指令。
        """
        if not event:
            return
        event_data = event.event_data
        if not event_data or event_data.get("action") != "railgun_checkin_action":
            return

        logger.info(f"{self.plugin_name} - 收到远程签到指令 /railgun_checkin，开始即时签到...")
        # 远程用户手动触发，跳过随机延时
        self._do_checkin_task(ignore_delay=True)

    # ─── 核心签到逻辑 ───

    def _do_checkin_task(self, ignore_delay: bool = False):
        """
        核心签到任务入口。
        :param ignore_delay: 为 True 时跳过随机延时（远程手动触发时使用）。
        """
        domain = self._site_domain or "railgun.info"
        # Token 优先使用用户自定义值，否则默认取站点域名
        token = self._token.strip() if self._token and self._token.strip() else domain

        if not self._cookie:
            msg = f"[{domain}] 签到失败：未配置 Koa Session Cookie！请在插件设置中填写。"
            logger.error(f"{self.plugin_name} - {msg}")
            self._send_notification("签到失败", msg)
            return

        # 1. 随机延迟（Cron 定时触发时使用，手动触发跳过）
        if self._enable_random_delay and not ignore_delay:
            min_d = max(0, self._min_delay_seconds)
            max_d = max(min_d, self._max_delay_seconds)
            if max_d > min_d:
                delay = random.randint(min_d, max_d)
                logger.info(f"{self.plugin_name} - [{domain}] 随机延迟 {delay} 秒后发起签到...")
                time.sleep(delay)

        # 2. 根据站点域名动态构造请求 URL 和 Origin
        url = f"https://{domain}/api/user/checkin"
        origin = f"https://{domain}"
        logger.info(f"{self.plugin_name} - [{domain}] 正在发送签到请求...")

        env = random.choice(UA_ENVIRONMENTS)
        logger.info(f"{self.plugin_name} - [{domain}] 随机伪装浏览器: {env['name']}")

        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "cache-control": "no-cache",
            "content-type": "application/json;charset=UTF-8",
            "cookie": self._cookie,
            "dnt": "1",
            "origin": origin,
            "pragma": "no-cache",
            "priority": "u=1, i",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": env["user-agent"]
        }

        # 补全 Chromium 系列浏览器的 Client Hints
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
                body = response.read().decode("utf-8")

                logger.info(f"{self.plugin_name} - HTTP 状态码: {status_code}")
                try:
                    result = json.loads(body)
                    code = result.get("code")
                    message = result.get("message", "无响应消息")

                    if code == 1:
                        ok_msg = (
                            f"[{domain}] 签到成功!\n"
                            f"接口消息: {message}\n"
                            f"伪装浏览器: {env['name']}"
                        )
                        logger.info(f"{self.plugin_name} - {ok_msg}")
                        self._send_notification("签到成功", ok_msg)
                    else:
                        warn_msg = (
                            f"[{domain}] 签到返回异常 (code={code})\n"
                            f"接口消息: {message}\n"
                            f"伪装浏览器: {env['name']}"
                        )
                        logger.warning(f"{self.plugin_name} - {warn_msg}")
                        self._send_notification("签到警告", warn_msg)

                except json.JSONDecodeError:
                    err = f"接口返回内容无法解析为 JSON:\n{body}"
                    logger.error(f"{self.plugin_name} - {err}")
                    self._send_notification("签到失败", err)

        except urllib.error.HTTPError as e:
            detail = f"HTTP 请求失败，状态码: {e.code}"
            try:
                err_body = e.read().decode("utf-8")
                err_json = json.loads(err_body)
                detail += f"\n接口返回: {err_json.get('message', err_body)}"
            except Exception:
                detail += f"\n错误原因: {e.reason}"
            logger.error(f"{self.plugin_name} - {detail}")
            self._send_notification("签到异常", detail)

        except urllib.error.URLError as e:
            net_err = f"网络连接失败: {e.reason}"
            logger.error(f"{self.plugin_name} - {net_err}")
            self._send_notification("签到失败", net_err)

        except Exception as e:
            unknown = f"未知异常: {e}"
            logger.error(f"{self.plugin_name} - {unknown}")
            self._send_notification("签到失败", unknown)

    # ─── 通知发送 ───

    def _send_notification(self, title_suffix: str, content: str):
        """
        向 MoviePilot 消息通知渠道发送签到结果。
        """
        if not self._notify:
            return
        full_title = f"{self.plugin_name} - {title_suffix}"
        self.post_message(
            mtype=NotificationType.Plugin,
            title=full_title,
            text=content
        )

    # ─── 配置表单（Vuetify 组件） ───

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面。
        返回：(页面 Vuetify 组件列表, 数据结构字典)
        """
        return [
            {
                "component": "VForm",
                "content": [
                    # ── 第一行：开关 ──
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "enabled",
                                        "label": "启用插件"
                                    }
                                }]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "notify",
                                        "label": "发送通知"
                                    }
                                }]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "onlyonce",
                                        "label": "立即运行一次"
                                    }
                                }]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "enable_random_delay",
                                        "label": "启用随机延迟"
                                    }
                                }]
                            }
                        ]
                    },
                    # ── 第二行：站点域名、Cron、Token ──
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {
                                        "model": "site_domain",
                                        "label": "站点域名",
                                        "placeholder": "railgun.info",
                                        "hint": "GLaDOS 系列站点域名，如 railgun.info 或 glados-facility.com",
                                        "persistent-hint": True
                                    }
                                }]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {
                                        "model": "cron",
                                        "label": "定时执行 Cron 表达式",
                                        "placeholder": "0 8 * * *"
                                    }
                                }]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {
                                        "model": "token",
                                        "label": "签到 Token (可选)",
                                        "placeholder": "留空则自动使用站点域名",
                                        "hint": "一般无需填写，默认取站点域名作为 Token",
                                        "persistent-hint": True
                                    }
                                }]
                            }
                        ]
                    },
                    # ── 第三行：延迟参数 ──
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {
                                        "model": "min_delay_seconds",
                                        "label": "最小延迟(秒)",
                                        "type": "number",
                                        "placeholder": "0"
                                    }
                                }]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {
                                        "model": "max_delay_seconds",
                                        "label": "最大延迟(秒)",
                                        "type": "number",
                                        "placeholder": "1800"
                                    }
                                }]
                            }
                        ]
                    },
                    # ── 第四行：Cookie（多行） ──
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [{
                                    "component": "VTextarea",
                                    "props": {
                                        "model": "cookie",
                                        "label": "Koa Session Cookie",
                                        "rows": 2,
                                        "placeholder": "koa:sess=xxx; koa:sess.sig=xxx"
                                    }
                                }]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "notify": True,
            "onlyonce": False,
            "cron": "0 8 * * *",
            "site_domain": "railgun.info",
            "cookie": "",
            "token": "",
            "enable_random_delay": True,
            "min_delay_seconds": 0,
            "max_delay_seconds": 1800,
        }

    def get_page(self) -> List[dict]:
        """插件详情页（暂无）"""
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        """插件自定义 API（暂无）"""
        pass

    def stop_service(self):
        """停止插件服务"""
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error(f"{self.plugin_name} - 停止服务失败: {e}")

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
from datetime import datetime
from typing import Any, List, Dict, Tuple, Optional

from app.core.config import settings
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

# ─── 重复签到关键词识别 ───
REPEAT_KEYWORDS = ["Repeats", "Try Tomorrow", "already", "repeated", "duplicate"]


class RailgunCheckin(_PluginBase):
    """
    GLaDOS 系列站点自动签到插件（支持 railgun.info / glados-facility.com 等）
    """

    # ─── 插件元数据 ───
    plugin_name = "GLaDOS 自动签到"
    plugin_desc = "定时随机延时签到 GLaDOS 系列站点，支持自定义域名、远程命令及机器人通知。"
    plugin_icon = "https://raw.githubusercontent.com/rolay/MoviePilot-Plugins/main/icons/railguncheckin.png"
    plugin_version = "2.0.0"
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
    _proxy_enabled: bool = False
    _timeout_seconds: int = 30
    _max_attempts: int = 2
    _retry_interval_seconds: int = 5
    _history_days: int = 30

    # 调度器
    _scheduler: Optional[BackgroundScheduler] = None

    # ─── 工具方法 ───

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """安全的 int 转换，失败时返回默认值"""
        try:
            if value is None:
                return default
            if isinstance(value, (int, float)):
                return int(value)
            s = str(value).strip()
            if not s:
                return default
            return int(float(s))
        except (ValueError, TypeError):
            return default

    def _get_proxy_handler(self) -> Optional[urllib.request.ProxyHandler]:
        """获取 MoviePilot 全局代理配置并构建 ProxyHandler"""
        if not self._proxy_enabled:
            return None
        try:
            proxy = getattr(settings, "PROXY", None)
            if not proxy:
                return None
            if isinstance(proxy, str):
                return urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            if isinstance(proxy, dict):
                http_p = proxy.get("http") or proxy.get("HTTP", "")
                https_p = proxy.get("https") or proxy.get("HTTPS") or http_p
                if http_p or https_p:
                    return urllib.request.ProxyHandler({"http": http_p or https_p, "https": https_p or http_p})
        except Exception as e:
            logger.warning(f"{self.plugin_name} - 获取代理配置失败: {e}")
        return None

    def _http_request(self, url: str, headers: Dict[str, str],
                      data: Optional[bytes] = None, method: str = "GET",
                      use_proxy: bool = True) -> Tuple[int, str]:
        """
        统一的 HTTP 请求方法，支持代理和重试。
        返回 (status_code, response_body)。
        """
        last_error = None
        # 构建尝试策略：如果启用代理，先用代理尝试，失败后直连回退
        attempt_configs = []
        if use_proxy and self._proxy_enabled:
            proxy_handler = self._get_proxy_handler()
            if proxy_handler:
                attempt_configs.append(("代理", proxy_handler))
            attempt_configs.append(("直连", None))
        else:
            attempt_configs.append(("直连", None))

        for mode_name, proxy_handler in attempt_configs:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    req = urllib.request.Request(url, data=data, headers=headers, method=method)
                    if proxy_handler:
                        opener = urllib.request.build_opener(proxy_handler)
                    else:
                        opener = urllib.request.build_opener()
                    with opener.open(req, timeout=self._timeout_seconds) as response:
                        status_code = response.getcode()
                        body = response.read().decode("utf-8")
                        return status_code, body
                except urllib.error.HTTPError as e:
                    # HTTPError 也能读取 body，直接返回给调用方处理
                    try:
                        err_body = e.read().decode("utf-8")
                    except Exception:
                        err_body = str(e.reason)
                    return e.code, err_body
                except Exception as e:
                    last_error = e
                    logger.warning(
                        f"{self.plugin_name} - [{mode_name}] 第 {attempt}/{self._max_attempts} 次请求失败: {e}"
                    )
                    if attempt < self._max_attempts:
                        time.sleep(max(1, self._retry_interval_seconds))

        raise ConnectionError(f"所有请求尝试均失败，最后错误: {last_error}")

    # ─── 初始化与配置 ───

    def init_plugin(self, config: dict = None):
        """插件初始化入口，加载配置。"""
        self.stop_service()

        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", True)
            self._onlyonce = config.get("onlyonce", False)
            self._cron = config.get("cron", "")
            self._site_domain = (config.get("site_domain", "") or "railgun.info").strip().lower()
            self._cookie = (config.get("cookie", "") or "").strip()
            self._token = (config.get("token", "") or "").strip()
            self._enable_random_delay = config.get("enable_random_delay", True)
            self._min_delay_seconds = self._safe_int(config.get("min_delay_seconds"), 0)
            self._max_delay_seconds = self._safe_int(config.get("max_delay_seconds"), 1800)
            self._proxy_enabled = bool(config.get("proxy_enabled", False))
            self._timeout_seconds = self._safe_int(config.get("timeout_seconds"), 30)
            self._max_attempts = self._safe_int(config.get("max_attempts"), 2)
            self._retry_interval_seconds = self._safe_int(config.get("retry_interval_seconds"), 5)
            self._history_days = self._safe_int(config.get("history_days"), 30)

        # 立即运行一次
        if self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            logger.info(f"{self.plugin_name} - 开启立即运行一次模式")
            self._scheduler.add_job(
                func=self._do_checkin_task,
                trigger="date",
                name=f"{self.plugin_name} 立即执行"
            )
            self._onlyonce = False
            self.__update_config()
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
            "proxy_enabled": self._proxy_enabled,
            "timeout_seconds": self._timeout_seconds,
            "max_attempts": self._max_attempts,
            "retry_interval_seconds": self._retry_interval_seconds,
            "history_days": self._history_days,
        })

    # ─── 定时服务注册 ───

    def get_service(self) -> List[Dict[str, Any]]:
        """注册定时服务，MoviePilot 会自动调度。"""
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
            "desc": "GLaDOS 签到",
            "category": "工具",
            "data": {
                "action": "railgun_checkin_action"
            }
        }]

    # ─── 远程命令事件监听 ───

    @eventmanager.register(EventType.PluginAction)
    def handle_plugin_action(self, event: Event):
        """监听 PluginAction 事件，处理来自机器人的远程签到指令。"""
        if not event:
            return
        event_data = event.event_data
        if not event_data or event_data.get("action") != "railgun_checkin_action":
            return

        logger.info(f"{self.plugin_name} - 收到远程签到指令 /railgun_checkin，开始即时签到...")
        self._do_checkin_task(ignore_delay=True)

    # ─── 签到后数据回查 ───

    def _build_base_headers(self, env: dict) -> Dict[str, str]:
        """构造通用请求头"""
        domain = self._site_domain or "railgun.info"
        origin = f"https://{domain}"
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "cache-control": "no-cache",
            "content-type": "application/json;charset=UTF-8",
            "cookie": self._cookie,
            "dnt": "1",
            "origin": origin,
            "referer": f"{origin}/",
            "pragma": "no-cache",
            "user-agent": env["user-agent"]
        }
        if "sec-ch-ua" in env:
            headers["sec-ch-ua"] = env["sec-ch-ua"]
            headers["sec-ch-ua-mobile"] = "?0"
            headers["sec-ch-ua-platform"] = env["sec-ch-ua-platform"]
        return headers

    def _fetch_user_status(self, headers: Dict[str, str]) -> Dict[str, Any]:
        """签到后回查用户状态信息（剩余天数、邮箱等）"""
        domain = self._site_domain or "railgun.info"
        result = {}
        try:
            url = f"https://{domain}/api/user/status"
            # GET 请求不需要 content-type
            get_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
            status_code, body = self._http_request(url, get_headers, method="GET")
            if status_code == 200:
                data = json.loads(body)
                if data.get("code") == 0 and isinstance(data.get("data"), dict):
                    d = data["data"]
                    result["user_id"] = d.get("userId") or d.get("configureId")
                    result["email"] = d.get("email")
                    result["days"] = self._safe_int(d.get("days"), -1)
                    result["left_days"] = self._safe_int(d.get("leftDays"), -1)
                    logger.info(f"{self.plugin_name} - [{domain}] 用户状态回查成功")
        except Exception as e:
            logger.warning(f"{self.plugin_name} - [{domain}] 用户状态回查失败: {e}")
        return result

    def _fetch_user_points(self, headers: Dict[str, str]) -> Dict[str, Any]:
        """签到后回查积分余额和历史"""
        domain = self._site_domain or "railgun.info"
        result = {}
        try:
            url = f"https://{domain}/api/user/points"
            get_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
            status_code, body = self._http_request(url, get_headers, method="GET")
            if status_code == 200:
                data = json.loads(body)
                if data.get("code") == 0:
                    result["points"] = data.get("points")
                    # 同步历史记录
                    api_history = data.get("history") or data.get("list") or []
                    if api_history:
                        self._sync_history_from_api(api_history)
                    logger.info(f"{self.plugin_name} - [{domain}] 积分回查成功, 当前积分: {result.get('points')}")
        except Exception as e:
            logger.warning(f"{self.plugin_name} - [{domain}] 积分回查失败: {e}")
        return result

    def _sync_history_from_api(self, api_history: List[Dict]):
        """将 API 返回的积分历史记录同步到本地持久化存储"""
        if not api_history:
            return
        formatted = []
        for item in api_history:
            try:
                ts = self._safe_int(item.get("time"), 0)
                change = float(item.get("change") or 0)
                change_display = int(change) if change == int(change) else change
                balance = float(item.get("balance") or 0)
                balance_display = int(balance) if balance == int(balance) else balance
                business = item.get("business", "")
                detail = item.get("detail", "")

                dt = datetime.fromtimestamp(ts / 1000.0)
                dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")

                # 推断状态和中文消息
                status = "变动"
                msg = detail or business or ""
                if "checkin" in business or "checkin" in detail:
                    status = "签到"
                    msg = "每日签到奖励"
                elif "exchange" in business or "exchange" in detail:
                    status = "兑换"
                    import re
                    m = re.search(r"exchange (\d+) points for (\d+) days", msg)
                    if m:
                        pts, dys = m.groups()
                        msg = f"积分兑换{dys}天 (-{pts}点)"
                    else:
                        msg = "积分兑换套餐"

                formatted.append({
                    "date": dt_str,
                    "ts": ts,
                    "status": status,
                    "message": msg,
                    "points_gain": change_display,
                    "balance": balance_display,
                    "user_id": item.get("user_id"),
                })
            except Exception:
                continue

        # 按时间降序排列后持久化
        formatted.sort(key=lambda x: int(x.get("ts") or 0), reverse=True)
        self.save_data("checkin_history", formatted)

    def _save_checkin_record(self, record: Dict[str, Any]):
        """保存单条签到记录到历史（适用于 API 未返回历史时的兜底）"""
        try:
            history = self.get_data("checkin_history") or []
            now_ts = int(time.time() * 1000)
            record.setdefault("ts", now_ts)
            history.insert(0, record)
            # 按保留天数清理
            cutoff_ts = (time.time() - self._history_days * 86400) * 1000
            history = [r for r in history if int(r.get("ts", 0)) > cutoff_ts]
            self.save_data("checkin_history", history)
        except Exception as e:
            logger.warning(f"{self.plugin_name} - 保存签到记录失败: {e}")

    # ─── 核心签到逻辑 ───

    def _do_checkin_task(self, ignore_delay: bool = False):
        """
        核心签到任务入口。
        :param ignore_delay: 为 True 时跳过随机延时（远程手动触发时使用）。
        """
        domain = self._site_domain or "railgun.info"
        token = self._token if self._token else domain

        if not self._cookie:
            msg = f"[{domain}] 签到失败：未配置 Koa Session Cookie！请在插件设置中填写。"
            logger.error(f"{self.plugin_name} - {msg}")
            self._send_notification("签到失败", msg)
            return

        # 1. 随机延迟
        if self._enable_random_delay and not ignore_delay:
            min_d = max(0, self._min_delay_seconds)
            max_d = max(min_d, self._max_delay_seconds)
            if max_d > min_d:
                delay = random.randint(min_d, max_d)
                logger.info(f"{self.plugin_name} - [{domain}] 随机延迟 {delay} 秒后发起签到...")
                time.sleep(delay)

        # 2. 构造请求
        url = f"https://{domain}/api/user/checkin"
        env = random.choice(UA_ENVIRONMENTS)
        headers = self._build_base_headers(env)
        logger.info(f"{self.plugin_name} - [{domain}] 正在发送签到请求 (伪装: {env['name']})...")

        post_data = json.dumps({"token": token}).encode("utf-8")

        try:
            status_code, body = self._http_request(url, headers, data=post_data, method="POST")
            logger.info(f"{self.plugin_name} - [{domain}] HTTP 状态码: {status_code}")

            try:
                result = json.loads(body)
            except json.JSONDecodeError:
                err = f"[{domain}] 接口返回内容无法解析为 JSON:\n{body}"
                logger.error(f"{self.plugin_name} - {err}")
                self._send_notification("签到失败", err)
                self._save_checkin_record({
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "失败", "message": "JSON 解析失败",
                    "points_gain": 0,
                })
                return

            code = result.get("code")
            message = result.get("message", "无响应消息")
            points_gain = self._safe_int(result.get("points"), 0)

            # 从 list 中尝试获取积分（部分 API 变种）
            checkin_list = result.get("list") or []
            if points_gain == 0 and checkin_list:
                points_gain = self._safe_int(checkin_list[0].get("points"), 0)

            # 3. 智能判断签到状态
            is_repeat = any(kw.lower() in message.lower() for kw in REPEAT_KEYWORDS)

            if code == 1 and not is_repeat:
                checkin_status = "签到成功"
            elif is_repeat or code == 1:
                checkin_status = "已签到"
            else:
                checkin_status = "签到失败"

            # 4. 签到后回查用户数据（积分余额、剩余天数等）
            user_status = self._fetch_user_status(headers)
            points_info = self._fetch_user_points(headers)
            self.save_data("user_status", user_status)

            current_points = points_info.get("points")
            if current_points is not None:
                try:
                    cp = float(current_points)
                    current_points = int(cp) if cp == int(cp) else cp
                except (ValueError, TypeError):
                    pass

            left_days = user_status.get("left_days")
            user_email = user_status.get("email")

            # 5. 构造通知消息
            if checkin_status == "签到成功":
                status_line = f"[{domain}] 签到成功!"
                if points_gain > 0:
                    status_line += f" (+{points_gain} 点)"
            elif checkin_status == "已签到":
                status_line = f"[{domain}] 今日已签到"
            else:
                status_line = f"[{domain}] 签到失败 (code={code})"

            detail_parts = [status_line]
            if message:
                detail_parts.append(f"接口消息: {message}")
            if current_points is not None:
                detail_parts.append(f"当前积分: {current_points}")
            if left_days is not None and left_days >= 0:
                detail_parts.append(f"剩余天数: {left_days}")
            if user_email:
                detail_parts.append(f"邮箱: {user_email}")
            detail_parts.append(f"伪装浏览器: {env['name']}")

            full_msg = "\n".join(detail_parts)

            if checkin_status == "签到失败":
                logger.warning(f"{self.plugin_name} - {full_msg}")
                self._send_notification("签到失败", full_msg)
            elif checkin_status == "已签到":
                logger.info(f"{self.plugin_name} - {full_msg}")
                self._send_notification("今日已签到", full_msg)
            else:
                logger.info(f"{self.plugin_name} - {full_msg}")
                self._send_notification("签到成功", full_msg)

            # 6. 兜底保存签到记录（如果回查积分历史 API 未返回数据）
            self._save_checkin_record({
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": checkin_status,
                "message": message,
                "points_gain": points_gain,
                "balance": current_points,
            })

        except ConnectionError as e:
            net_err = f"[{domain}] 网络连接失败 (已重试 {self._max_attempts} 次): {e}"
            logger.error(f"{self.plugin_name} - {net_err}")
            self._send_notification("签到失败", net_err)
            self._save_checkin_record({
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "失败", "message": str(e), "points_gain": 0,
            })

        except Exception as e:
            unknown = f"[{domain}] 未知异常: {e}"
            logger.error(f"{self.plugin_name} - {unknown}")
            self._send_notification("签到失败", unknown)
            self._save_checkin_record({
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "失败", "message": str(e), "points_gain": 0,
            })

    # ─── 通知发送 ───

    def _send_notification(self, title_suffix: str, content: str):
        """向 MoviePilot 消息通知渠道发送签到结果。"""
        if not self._notify:
            return
        full_title = f"{self.plugin_name} - {title_suffix}"
        self.post_message(
            mtype=NotificationType.SiteMessage,
            title=full_title,
            text=content
        )

    # ─── 配置表单（Vuetify 组件） ───

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """拼装插件配置页面。"""
        return [
            {
                "component": "VForm",
                "content": [
                    # ── 基础设置卡片 ──
                    {
                        "component": "VCard",
                        "props": {"variant": "outlined", "class": "mb-3"},
                        "content": [
                            {"component": "VCardTitle", "props": {"class": "text-subtitle-1 font-weight-bold"}, "text": "基础设置"},
                            {"component": "VCardText", "content": [
                                {
                                    "component": "VRow",
                                    "content": [
                                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                            {"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}
                                        ]},
                                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                            {"component": "VSwitch", "props": {"model": "notify", "label": "发送通知"}}
                                        ]},
                                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                            {"component": "VSwitch", "props": {"model": "onlyonce", "label": "立即运行一次"}}
                                        ]},
                                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                            {"component": "VSwitch", "props": {"model": "enable_random_delay", "label": "启用随机延迟"}}
                                        ]},
                                    ]
                                },
                                {
                                    "component": "VRow",
                                    "content": [
                                        {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                                            {"component": "VTextField", "props": {
                                                "model": "site_domain", "label": "站点域名",
                                                "placeholder": "railgun.info",
                                                "hint": "GLaDOS 系列站点域名，如 railgun.info 或 glados-facility.com",
                                                "persistent-hint": True
                                            }}
                                        ]},
                                        {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                                            {"component": "VCronField", "props": {"model": "cron", "label": "签到周期"}}
                                        ]},
                                        {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                                            {"component": "VTextField", "props": {
                                                "model": "token", "label": "签到 Token (可选)",
                                                "placeholder": "留空则自动使用站点域名",
                                                "hint": "一般无需填写，默认取站点域名作为 Token",
                                                "persistent-hint": True
                                            }}
                                        ]},
                                    ]
                                },
                                {
                                    "component": "VRow",
                                    "content": [
                                        {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                                            {"component": "VTextField", "props": {"model": "min_delay_seconds", "label": "最小延迟(秒)", "type": "number", "placeholder": "0"}}
                                        ]},
                                        {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                                            {"component": "VTextField", "props": {"model": "max_delay_seconds", "label": "最大延迟(秒)", "type": "number", "placeholder": "1800"}}
                                        ]},
                                    ]
                                },
                            ]}
                        ]
                    },
                    # ── Cookie 卡片 ──
                    {
                        "component": "VCard",
                        "props": {"variant": "outlined", "class": "mb-3"},
                        "content": [
                            {"component": "VCardTitle", "props": {"class": "text-subtitle-1 font-weight-bold"}, "text": "域名与认证"},
                            {"component": "VCardText", "content": [
                                {
                                    "component": "VRow",
                                    "content": [
                                        {"component": "VCol", "props": {"cols": 12}, "content": [
                                            {"component": "VTextarea", "props": {
                                                "model": "cookie", "label": "Koa Session Cookie",
                                                "rows": 2, "placeholder": "koa:sess=xxx; koa:sess.sig=xxx"
                                            }}
                                        ]}
                                    ]
                                },
                                {
                                    "component": "VRow",
                                    "content": [
                                        {"component": "VCol", "props": {"cols": 12}, "content": [
                                            {"component": "VAlert", "props": {
                                                "type": "info", "variant": "tonal",
                                                "text": "从浏览器复制 Cookie (包含 koa:sess 和 koa:sess.sig)。使用插件分身功能可同时管理多个站点账号。"
                                            }}
                                        ]}
                                    ]
                                },
                            ]}
                        ]
                    },
                    # ── 网络与重试卡片 ──
                    {
                        "component": "VCard",
                        "props": {"variant": "outlined", "class": "mb-3"},
                        "content": [
                            {"component": "VCardTitle", "props": {"class": "text-subtitle-1 font-weight-bold"}, "text": "网络与重试"},
                            {"component": "VCardText", "content": [
                                {
                                    "component": "VRow",
                                    "content": [
                                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                            {"component": "VSwitch", "props": {"model": "proxy_enabled", "label": "使用 MP 全局代理"}}
                                        ]},
                                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                            {"component": "VTextField", "props": {"model": "timeout_seconds", "label": "超时(秒)", "type": "number", "placeholder": "30"}}
                                        ]},
                                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                            {"component": "VTextField", "props": {"model": "max_attempts", "label": "重试次数", "type": "number", "placeholder": "2"}}
                                        ]},
                                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                            {"component": "VTextField", "props": {"model": "retry_interval_seconds", "label": "重试间隔(秒)", "type": "number", "placeholder": "5"}}
                                        ]},
                                    ]
                                },
                                {
                                    "component": "VRow",
                                    "content": [
                                        {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                                            {"component": "VTextField", "props": {"model": "history_days", "label": "历史保留天数", "type": "number", "placeholder": "30"}}
                                        ]},
                                    ]
                                },
                                {
                                    "component": "VRow",
                                    "content": [
                                        {"component": "VCol", "props": {"cols": 12}, "content": [
                                            {"component": "VAlert", "props": {
                                                "type": "info", "variant": "tonal",
                                                "text": "代理启用后优先通过 MoviePilot 全局代理请求，失败自动回退直连。建议网络不稳时增大超时和重试间隔。"
                                            }}
                                        ]}
                                    ]
                                },
                            ]}
                        ]
                    },
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
            "proxy_enabled": False,
            "timeout_seconds": 30,
            "max_attempts": 2,
            "retry_interval_seconds": 5,
            "history_days": 30,
        }

    # ─── 详情页（用户摘要 + 签到历史） ───

    def get_page(self) -> List[dict]:
        """插件详情页：展示用户摘要信息和签到历史表格"""
        domain = self._site_domain or "railgun.info"
        user_status = self.get_data("user_status") or {}
        history = self.get_data("checkin_history") or []

        # 按时间降序排列
        history = sorted(history, key=lambda x: int(x.get("ts") or 0), reverse=True)

        # 如果没有任何数据
        if not user_status and not history:
            return [{
                "component": "VAlert",
                "props": {
                    "type": "info", "variant": "tonal",
                    "text": f"[{domain}] 暂无签到记录，请先配置域名与 Cookie 后运行一次签到。",
                    "class": "mb-2"
                }
            }]

        # ── 用户摘要卡片 ──
        uid = user_status.get("user_id")
        email = user_status.get("email")
        days = user_status.get("days")
        left_days = user_status.get("left_days")

        # 获取最新一条签到记录的积分余额
        latest = history[0] if history else {}
        latest_balance = latest.get("balance", "-")
        latest_gain = self._safe_int(latest.get("points_gain"), 0)
        latest_time = latest.get("date", "-")
        latest_status = latest.get("status", "-")

        gain_color = "success" if latest_gain > 0 else ("error" if latest_gain < 0 else "grey")

        summary_card = [
            {
                "component": "VCard",
                "props": {"variant": "elevated", "elevation": 2, "rounded": "lg", "class": "mb-4"},
                "content": [
                    {"component": "VCardTitle", "props": {"class": "text-h6 font-weight-bold"},
                     "text": f"GLaDOS [{domain}] 用户摘要"},
                    {"component": "VCardText", "content": [
                        {"component": "VRow", "content": [
                            {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                                {"component": "VChip", "props": {"size": "large", "variant": "tonal", "color": "purple"},
                                 "text": f"用户ID: {uid or '-'}"}
                            ]},
                            {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                                {"component": "VChip", "props": {"size": "large", "variant": "tonal", "color": "amber-darken-2"},
                                 "text": f"当前积分: {latest_balance}"}
                            ]},
                            {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                                {"component": "VChip", "props": {"size": "large", "variant": "tonal", "color": gain_color},
                                 "text": f"最近签到: {'+' if latest_gain > 0 else ''}{latest_gain} 点"}
                            ]},
                        ]},
                        {"component": "VDivider"},
                        {"component": "VRow", "props": {"class": "mt-3"}, "content": [
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                {"component": "VChip", "props": {"size": "default", "variant": "elevated"},
                                 "text": f"邮箱: {email or '-'}"}
                            ]},
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                {"component": "VChip", "props": {"size": "default", "variant": "elevated"},
                                 "text": f"已用天数: {days if days is not None and days >= 0 else '-'}"}
                            ]},
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                {"component": "VChip", "props": {"size": "default", "variant": "elevated"},
                                 "text": f"剩余天数: {left_days if left_days is not None and left_days >= 0 else '-'}"}
                            ]},
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                                {"component": "VChip", "props": {"size": "default", "variant": "tonal"},
                                 "text": f"更新: {latest_time}"}
                            ]},
                        ]},
                    ]}
                ]
            }
        ]

        # ── 签到历史表格 ──
        rows = []
        for h in history:
            delta = self._safe_int(h.get("points_gain"), 0)
            delta_color = "success" if delta > 0 else ("error" if delta < 0 else "grey")
            delta_text = f"+{delta}" if delta > 0 else str(delta)
            rows.append({
                "component": "tr",
                "content": [
                    {"component": "td", "props": {"class": "text-caption"}, "text": h.get("date", "")},
                    {"component": "td", "content": [
                        {"component": "VChip", "props": {"size": "small", "variant": "outlined", "color": "primary"},
                         "text": h.get("status", "-")}
                    ]},
                    {"component": "td", "content": [
                        {"component": "VChip", "props": {"size": "small", "variant": "outlined", "color": delta_color},
                         "text": delta_text}
                    ]},
                    {"component": "td", "props": {"class": "text-caption"}, "text": h.get("message", "-")},
                ]
            })

        history_table = [
            {
                "component": "VCard",
                "props": {"variant": "elevated", "elevation": 2, "rounded": "lg", "class": "mb-4"},
                "content": [
                    {"component": "VCardTitle", "props": {"class": "text-h6 font-weight-bold"},
                     "text": f"签到历史 (近{len(rows)}条)"},
                    {"component": "VCardText", "content": [
                        {"component": "VTable", "props": {"hover": True, "density": "comfortable"}, "content": [
                            {"component": "thead", "content": [{"component": "tr", "content": [
                                {"component": "th", "props": {"class": "text-body-2"}, "text": "时间"},
                                {"component": "th", "props": {"class": "text-body-2"}, "text": "状态"},
                                {"component": "th", "props": {"class": "text-body-2"}, "text": "点数变化"},
                                {"component": "th", "props": {"class": "text-body-2"}, "text": "消息"},
                            ]}]},
                            {"component": "tbody", "content": rows}
                        ]}
                    ]}
                ]
            }
        ] if rows else []

        return summary_card + history_table

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

import threading
from queue import Queue
from time import time, sleep
from typing import Any, List, Dict, Tuple
from urllib.parse import urlencode

from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType
from app.utils.http import RequestUtils


class IyuuMsgs(_PluginBase):
    # 插件名称
    plugin_name = "IYUU消息通知多人版"
    # 插件描述
    plugin_desc = "支持使用IYUU发送多人消息通知。"
    # 插件图标
    plugin_icon = "Iyuu_A.png"
    # 插件版本
    plugin_version = "1.7"
    # 插件作者
    plugin_author = "waterz"
    # 作者主页
    author_url = "https://github.com/TouchBlueSky"
    # 插件配置项ID前缀
    plugin_config_prefix = "iyuumsgs_"
    # 加载顺序
    plugin_order = 25
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _enabled = False
    # _token = None
    _msgtypes = []
    # 原 self._token 改为 self._tokens
    _tokens:str = ""
    
    # 消息处理线程
    processing_thread = None
    # 上次发送时间
    last_send_time = 0
    # 消息队列
    message_queue = Queue()
    # 消息发送间隔（秒）6分钟 由于爱语飞飞一小时限制20条，以两个号的情况来看，6分钟发两条，间隔6分钟也可以接受，这样就不用花心思写什么失败重试了，也不至于漏消息
    send_interval = 360
    # 退出事件
    __event = threading.Event()

    def init_plugin(self, config: dict = None):
        self.__event.clear()
        if config:
            self._enabled = config.get("enabled")
            self._tokens = config.get("tokens")
            self._msgtypes = config.get("msgtypes") or []

            if self._enabled and self._tokens:
                # 启动处理队列的后台线程
                self.processing_thread = threading.Thread(target=self.process_queue)
                self.processing_thread.daemon = True
                self.processing_thread.start()

    def get_state(self) -> bool:
        return self._enabled and (True if self._tokens else False)

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        # 编历 NotificationType 枚举，生成消息类型选项
        MsgTypeOptions = []
        for item in NotificationType:
            MsgTypeOptions.append({
                "title": item.value,
                "value": item.name
            })
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'tokens',
                                            'label': 'IYUU令牌',
                                            'placeholder': '多个IYUU令牌使用,隔开例如：IYUU123,IYUU456',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'multiple': True,
                                            'chips': True,
                                            'model': 'msgtypes',
                                            'label': '消息类型',
                                            'items': MsgTypeOptions
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                ]
            }
        ], {
            "enabled": False,
            'tokens': '',
            'msgtypes': []
        }

    def get_page(self) -> List[dict]:
        pass
        
    @eventmanager.register(EventType.NoticeMessage)
    def send(self, event: Event):
        """
        消息发送事件，将消息加入队列
        """
        if not self.get_state() or not event.event_data:
            return

        msg_body = event.event_data
        # 验证消息的有效性
        if not msg_body.get("title") and not msg_body.get("text"):
            logger.warn("标题和内容不能同时为空")
            return

        # 将消息加入队列
        self.message_queue.put(msg_body)
        logger.info("消息已加入队列等待发送")

    def process_queue(self):
        """
        处理队列中的消息，按间隔时间发送
        """
        while True:
            if self.__event.is_set():
                logger.info("消息发送线程正在退出...")
                break
            

            # 检查是否满足发送间隔时间
            current_time = time()
            time_since_last_send = current_time - self.last_send_time
            if time_since_last_send < self.send_interval:
                sleep(self.send_interval - time_since_last_send)
                
            # 获取队列中的下一条消息
            msg_body = self.message_queue.get()
            
            # 标记任务完成
            self.message_queue.task_done()
            
            # 处理消息内容
            channel = msg_body.get("channel")
            if channel:
                continue
            msg_type: NotificationType = msg_body.get("type")
            title = msg_body.get("title")
            text = msg_body.get("text")

            # 检查消息类型是否已启用
            if msg_type and self._msgtypes and msg_type.name not in self._msgtypes:
                logger.info(f"消息类型 {msg_type.value} 未开启消息发送")
                continue
            # 循环发送给所有Token（新增代码）
            for valid_tokens in self._tokens.split(","):
                if not valid_tokens:
                    continue
                # 尝试发送消息
                try:
                    # 构造请求URL（修改为当前token）
                    sc_url = "https://iyuu.cn/%s.send?%s" % (valid_tokens, urlencode({"text": title, "desp": text}))
                    res = RequestUtils().get_res(sc_url)
                    if res and res.status_code == 200:
                        ret_json = res.json()
                        errno = ret_json.get('errcode')
                        error = ret_json.get('errmsg')
                        if errno == 0:
                            logger.info("IYUU消息发送成功")
                            # 更新上次发送时间
                            self.last_send_time = time()
                        else:
                            logger.warn(f"IYUU消息发送失败1，错误码：{errno}，错误原因：{error}")
                    elif res is not None:
                        logger.warn(f"IYUU消息发送失败2，错误码：{res.status_code}，错误原因：{res.reason}")
                    else:
                        logger.warn("IYUU消息发送失败，未获取到返回信息")
                except Exception as msg_e:
                    logger.error(f"Token {valid_tokens} 发送失败：{str(msg_e)}")
                # 每个Token发送间隔（新增间隔控制）
                sleep(5)


            

    def stop_service(self):
        """
        退出插件
        """
        self.__event.set()

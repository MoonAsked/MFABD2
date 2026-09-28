# region 日志级别约定（由 scripts/strip_build_comments.py 从构建产物中剥离）
# 级别       | 普通 UI 中表达什么                         | 示例
# info       | 用户关心的正常结果、结束或跳过原因          | 已选中某项；本周期已完成
# warning    | 异常后的替代处理或受限结果                 | 已达尝试上限；已改用默认策略
# error      | 操作未能完成，或结果无法可靠判断           | 节点修改失败；冷却判定失败
# debug      | 过程、内部结果和诊断细节                   | 坐标、分数、参数、排序、调用栈
#
# info / warning / error 只留简短结果，不要求每个回调都向用户报告成功。
# 识别的正常命中/未命中、逐轮重试、patch/还原细节统一用 debug。
# 大量参数、原始数据和完整异常调用栈放 debug；UI 保留对象名称及必要的结果数量。
# 周期检查保留红绿灯、任务名称、上次运行时间和执行/跳过原因。
# debug 始终输出：普通模式使用 [DEBUG] 前缀，仅由客户端收进日志文件；
# 任务开启调试显示时使用 debug: 前缀，同时进入 UI。不要用裸 print 代替 debug。
# focus 是客户端控制指令，不属于日志级别。
# endregion

import sys
import time

# 核心修改：MFA GUI 监听的是标准输出，且需要特定的前缀
# 开发者原话："focus或者字符串前面拼接info："

_debug_ui_enabled = False


def set_debug_ui_enabled(enabled: bool) -> None:
    """由任务启动事件更新；日志输出本身不读取客户端配置。"""
    global _debug_ui_enabled
    _debug_ui_enabled = enabled is True


def _print_to_gui(prefix, msg):
    """
    基础输出函数
    :param prefix: 魔法前缀，如 'info:', 'error:', 'focus:'
    :param msg: 实际消息内容
    """
    # 获取当前时间（可选，有些GUI会自动加时间，你可以先试带时间的，如果重复了就去掉）
    # timestamp = time.strftime("%H:%M:%S", time.localtime())
    
    # 组合最终字符串
    # 格式可能需要是: "info:你的消息"
    final_msg = f"{prefix}{msg}"
    
    # 关键点1: 必须 flush，否则 Python 会缓存输出，导致 GUI 看起来卡顿或不显示
    print(final_msg, flush=True)

def info(msg):
    """普通日志"""
    # 对应开发者说的 "拼接 info:"
    _print_to_gui("info:", f"🟣 >>> {msg}")

def warning(msg):
    """警告日志"""
    # 客户端用 warn: 识别警告。
    _print_to_gui("warn:", f"⚠️ >>> {msg}")

def error(msg):
    """错误日志"""
    # 客户端用 error: 识别错误。
    _print_to_gui("error:", f"🔴 >>> {msg}")

def debug(msg):
    """始终保留调试日志；仅调试模式使用 MFAA 日志台识别的前缀。"""
    if _debug_ui_enabled:
        _print_to_gui("debug:", msg)
    else:
        print(f"[DEBUG] {msg}", flush=True)

def focus(task_id):
    """
    特殊指令：尝试让 GUI 聚焦/高亮某个任务
    开发者提到的 "focus" 可能指这个
    """
    _print_to_gui("focus:", task_id)

# ---------------------------------------------------------
# 必须保留的设置：防止中文乱码
# 如果 GUI 收到乱码，它可能直接丢弃整条日志，导致你看不到任何东西
if sys.version_info >= (3, 7):
    sys.stdout.reconfigure(encoding='utf-8') # type: ignore
    sys.stderr.reconfigure(encoding='utf-8') # type: ignore

# ---------------------------------------------------------
# 自测代码（直接运行这个文件测试）
if __name__ == "__main__":
    print("正在测试 MFA 日志协议...", flush=True)
    
    # 1. 开发者明确提到的格式
    info("这条日志应该能显示了！(基于 info: 前缀)")
    
    # 2. 测试其他等级
    time.sleep(0.5)
    warning("这是一条警告测试")
    error("这是一条错误测试")
    
    # 3. 测试 focus (假设任务ID是 task_1)
    time.sleep(0.5)
    focus("task_1")
    info("应该已经尝试聚焦任务了")

import json
import re
from maa.context import Context
from maa.custom_action import CustomAction
from maa.agent.agent_server import AgentServer
from utils import mfaalog

@AgentServer.custom_action("BatchNumericPatch")
class BatchNumericPatch(CustomAction):
    """
    [多规则引擎版 - 修正版]
    修复了返回值类型导致的 Action.Failed
    """
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            mfaalog.debug("[BatchPatch] Engine 启动...")

            # ================= 🔧 开发者配置区域 🔧 =================
            SPLIT_PATTERN = r'[，,;\s|]+'
            RANGE_CONNECTORS = ['~', '～', '-']
            # =======================================================

            # 1. 解析参数
            try:
                # 兼容性处理：如果传进来已经是dict (某些特定版本行为)，直接用；如果是str则解析
                if isinstance(argv.custom_action_param, dict):
                    top_params = argv.custom_action_param
                elif not argv.custom_action_param:
                    # 空参数直接返回成功
                    return CustomAction.RunResult(success=True)
                else:
                    top_params = json.loads(argv.custom_action_param)
            except json.JSONDecodeError:
                mfaalog.error("[BatchPatch] 批量修改未执行：参数格式错误")
                mfaalog.debug(f"[BatchPatch] JSON 格式错误: {argv.custom_action_param}")
                return CustomAction.RunResult(success=True)

            # 2. 构建规则队列
            rules_to_process = []
            if isinstance(top_params, dict) and "rule_list" in top_params and isinstance(top_params["rule_list"], list): # type: ignore
                rules_to_process = top_params["rule_list"] # type: ignore
            else:
                rules_to_process = [top_params]

            # 获取公共上下文
            # [关键] 这里必须是 task.json 那个节点的 Key，否则读不到 attach
            current_node_name = top_params.get("node_name", "") if isinstance(top_params, dict) else ""
            node_attach_data = {}
            
            if current_node_name:
                node_obj = context.get_node_object(current_node_name)
                if node_obj and node_obj.attach:
                    node_attach_data = node_obj.attach
                else:
                    mfaalog.debug(f"[BatchPatch] 未找到节点对象或Attach为空: {current_node_name}")
            else:
                mfaalog.debug("[BatchPatch] node_name 未配置，将无法读取 Attach")

            final_override_dict = {}

            # 3. 遍历执行规则
            for index, rule in enumerate(rules_to_process):
                # [新增] 类型防御：如果 rule 不是字典（比如是字符串或其他乱七八糟的），直接跳过
                # 这行代码能消除编译器的 "str没有get方法" 警告
                if not isinstance(rule, dict):
                    mfaalog.warning("[BatchPatch] 一项修改规则格式无效，已跳过")
                    mfaalog.debug(f"[BatchPatch] 规则格式错误（非字典），跳过: {rule}")
                    continue

                # 下面的代码就安全了，Pylance 知道 rule 肯定是 dict
                rule_tag = rule.get("comment", f"Rule_{index}")
                
                prefix = rule.get("pre_string", "")
                suffix = rule.get("post_string", "")
                patch_content = rule.get("patch", {})
                attach_key = rule.get("attach_key", "input_string")
                
                # --- 合并逻辑 ---
                static_input = str(rule.get("input_string", "")).strip()
                dynamic_input = ""
                
                # 尝试从 attach 获取
                if attach_key in node_attach_data:
                    val = node_attach_data[attach_key]
                    if val is not None:
                        dynamic_input = str(val).strip()
                        mfaalog.debug(f"[BatchPatch] [{rule_tag}] 捕获 Attach 参数: {dynamic_input}")
                
                raw_input_str = f"{static_input},{dynamic_input}"
                
                if not raw_input_str.strip(",; \t\n"):
                    continue

                # --- 解析逻辑 ---
                tokens = [x for x in re.split(SPLIT_PATTERN, raw_input_str) if x]
                final_number_set = set()
                
                for token in tokens:
                    matched_connector = None
                    for conn in RANGE_CONNECTORS:
                        if conn in token:
                            matched_connector = conn
                            break
                    
                    if matched_connector:
                        try:
                            parts = token.split(matched_connector)
                            if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                                start = int(parts[0].strip())
                                end = int(parts[1].strip())
                                if start > end:
                                    start, end = end, start
                                for i in range(start, end + 1):
                                    final_number_set.add(i)
                                    
                            else:
                                mfaalog.warning("[BatchPatch] 一项编号范围格式无效，已跳过")
                                mfaalog.debug(f"[BatchPatch] 范围格式无效: '{token}'")
                        except (ValueError, IndexError) as e:
                            # 捕获具体的转换错误，不吞没其他逻辑错误
                            mfaalog.warning("[BatchPatch] 一项编号范围无法解析，已跳过")
                            mfaalog.debug(f"[BatchPatch] 解析范围出错 '{token}': {e}")

                    else:
                        try:
                            final_number_set.add(int(token))
                        except ValueError:
                            final_number_set.add(token)

                if not final_number_set:
                    continue

                # --- 注入逻辑 ---
                item_list = list(final_number_set)
                try:
                    item_list.sort(key=lambda x: int(x) if isinstance(x, int) or (isinstance(x, str) and x.isdigit()) else str(x))
                except Exception as e:
                    # 只有当包含无法比较的类型时才会进这里（智能排序失败）
                    # 记录一条 Debug 日志即可，因为这可能是合法的纯文本输入
                    mfaalog.debug(f"[BatchPatch] 智能排序失败 (输入可能包含非数字)，回退到字符串排序: {e}")
                    item_list.sort(key=str)

                for item in item_list:
                    full_node_name = f"{prefix}{item}{suffix}"
                    final_override_dict[full_node_name] = patch_content

            # 4. 提交
            if final_override_dict:
                context.override_pipeline(final_override_dict)
                mfaalog.debug(f"[BatchPatch] 执行完毕，注入 {len(final_override_dict)} 个节点")
            
            # [修正] 必须返回 CustomAction.RunResult 对象
            return CustomAction.RunResult(success=True)

        except Exception as e:
            mfaalog.error("[BatchPatch] 批量修改执行失败")
            import traceback
            mfaalog.debug(f"[BatchPatch] 致命异常: {e}\n{traceback.format_exc()}")
            # [修正] 即使异常也建议返回 Success=True 防止卡死，或者 False 中断任务
            return CustomAction.RunResult(success=True)

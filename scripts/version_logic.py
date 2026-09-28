#!/usr/bin/env python3
"""
版本对比逻辑 - 决定跟哪个版本对比
"""

from typing import Optional, List
from version_rules import filter_valid_versions, sort_versions, core_version, is_valid_formal_version, is_valid_beta_version, is_valid_alpha_version, is_valid_ci_version

def get_all_tags() -> List[str]:
    """获取所有Git标签"""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "tag", "-l", "v*"],
            capture_output=True, 
            text=True,
            check=True
        )
        tags = [tag for tag in result.stdout.strip().split('\n') if tag]
        return tags
    except Exception as e:
        print(f"获取Git标签失败: {e}")
        return []

def get_current_branch() -> str:
    """获取当前分支名称"""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True
        )
        branch = result.stdout.strip()
        return branch if branch else "main"  # 处理分离HEAD状态
    except Exception as e:
        print(f"获取当前分支失败: {e}")
        return "main"  # 默认主分支

def is_main_branch(branch_name: str) -> bool:
    """判断是否为主分支"""
    main_branches = ["main", "master", "Main", "Master"]
    return branch_name in main_branches

def find_previous_formal_release(current_tag: str) -> Optional[str]:
    """查找上一个正式版"""
    all_tags = get_all_tags()
    filtered = filter_valid_versions(all_tags)
    formal_versions = sort_versions(filtered['formal'])
    
    print(f"所有正式版: {formal_versions}")
    
    if not formal_versions:
        return None
    
    # 清理当前标签，获取基础版本号
    current_version = core_version(current_tag)
    if current_version is None:
        return None
    current_clean = "v%d.%d.%d" % current_version

    # 找到当前标签在正式版列表中的位置
    for i, formal_tag in enumerate(formal_versions):
        if formal_tag == current_clean:
            # 如果是正式版，找上一个
            if i + 1 < len(formal_versions):
                return formal_versions[i + 1]
            else:
                return None
    
    # 如果当前标签不是正式版，找比它小的最新正式版
    # 使用版本号比较而不是字符串比较
    for formal_tag in formal_versions:
        formal_version = core_version(formal_tag)
        if formal_version is not None and formal_version < current_version:
            return formal_tag
    
    return None

def find_latest_formal_release() -> Optional[str]:
    """查找最新的正式版"""
    all_tags = get_all_tags()
    filtered = filter_valid_versions(all_tags)
    formal_versions = sort_versions(filtered['formal'])
    return formal_versions[0] if formal_versions else None

def find_safe_compare_base() -> str:
    """在CI环境中找到安全的对比基准"""
    # 尝试获取所有标签
    all_tags = get_all_tags()
    if not all_tags:
        return "HEAD~100"  # 回退一些提交
    
    # 过滤有效的正式版
    filtered = filter_valid_versions(all_tags)
    if filtered['formal']:
        # 使用最新的正式版
        latest_formal = sort_versions(filtered['formal'])[0]
        print(f"安全策略: 使用最新正式版 {latest_formal}")
        return latest_formal
    else:
        # 使用最新的标签（任何类型）
        all_sorted = sort_versions(all_tags)
        latest_tag = all_sorted[0]
        print(f"安全策略: 使用最新标签 {latest_tag}")
        return latest_tag

def calculate_compare_base(current_tag: str) -> str:
    """计算对比基准版本（CI环境兼容版）"""
    current_branch = get_current_branch()
    is_main = is_main_branch(current_branch)
    
    print(f"当前标签: {current_tag}")
    print(f"当前分支: {current_branch} ({'主分支' if is_main else '开发分支'})")
    
    # 策略1: 如果是正式版，找上一个正式版
    if is_valid_formal_version(current_tag):
        previous_formal = find_previous_formal_release(current_tag)
        if previous_formal:
            print(f"策略: 正式版 -> 对比上一个正式版: {previous_formal}")
            return previous_formal
        else:
            print("策略: 正式版 -> 没有更早的正式版，对比初始提交")
            return "HEAD~100"  # 回退一些提交
    
    # 策略2: 如果是公测版、内测版或开发版
    elif is_valid_beta_version(current_tag) or is_valid_alpha_version(current_tag) or is_valid_ci_version(current_tag):
        if is_main:
            # 主分支公测版/开发版：对比最新的正式版
            latest_formal = find_latest_formal_release()
            if latest_formal:
                version_type = current_tag.split('-')[1]
                print(f"策略: 主分支{version_type}版 -> 对比最新正式版: {latest_formal}")
                return latest_formal
            else:
                version_type = current_tag.split('-')[1]
                print(f"策略: 主分支{version_type}版 -> 没有正式版，对比初始提交")
                return "HEAD~100"
        else:
            # 开发分支公测版/内测版/开发版：使用安全的对比策略
            version_type = current_tag.split('-')[1]
            print(f"策略: 开发分支{version_type}版 -> 使用安全对比策略")
            return find_safe_compare_base()
    
    # 策略3: 其他情况
    else:
        print("策略: 未知版本格式 -> 对比最新的正式版")
        latest_formal = find_latest_formal_release()
        if latest_formal:
            return latest_formal
        else:
            return "HEAD~100"

if __name__ == "__main__":
    # 测试不同的场景
    test_scenarios = [
        "v2.3.6",                           # 正式版
        "v2.3.7-beta.3.251115.gabc1234",    # 公测版
        "v2.3.7-ci.251115.def5678",         # 开发版
        "v2.4.0-beta",                      # 无效版本（手打）
    ]
    
    print("=== 版本对比逻辑测试 ===\n")
    
    for scenario in test_scenarios:
        print(f"测试场景: {scenario}")
        compare_base = calculate_compare_base(scenario)
        print(f"对比基准: {compare_base}")
        print("-" * 50)
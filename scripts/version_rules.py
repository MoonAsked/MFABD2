#!/usr/bin/env python3
"""
版本规则 - 严格的版本过滤和分类，以及本次构建版号的生成

公测、内测版号：v主.次.补丁-<beta|alpha>.<序号>.<YYMMDD>.g<短哈希>
    序号在同一基线、同一通道内取已有最大值 +1，是唯一决定新旧的部分。
    SemVer 逐段比较，序号先分出大小，后面的日期与哈希轮不到比较，只供人读。
    哈希前的 g 保证该段含字母：纯数字且以 0 开头的段在 SemVer 里非法，
    MFAAvalonia 解析失败时会当成「没有更新」。
开发版号：v主.次.补丁-ci.<YYMMDD>.<短哈希>，不进更新通道，维持原样。

旧式公测/内测版号（日期.哈希）同一天多版时新旧由哈希字典序决定、常常排反，
已停用；这里仍识别它们，只为读懂历史标签。
"""

import argparse
import functools
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

SEMVER = re.compile(r'^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$')
FORMAL = re.compile(r'^v\d+\.\d+\.\d+$')
PRERELEASE = {
    channel: re.compile(rf'^v\d+\.\d+\.\d+-{channel}\.([1-9]\d*)\.\d{{6}}\.g[0-9a-f]{{7,40}}$')
    for channel in ('alpha', 'beta')
}
LEGACY_PRERELEASE = {
    channel: re.compile(rf'^v\d+\.\d+\.\d+-{channel}\.\d{{6}}\.[a-f0-9]{{7,}}$')
    for channel in ('alpha', 'beta')
}
CI = re.compile(r'^v\d+\.\d+\.\d+-ci\.\d{6}\.[a-f0-9]{7,}$')
# 基线 = 最新正式版；公测在其上 +1 个补丁号，内测 +2
PATCH_BUMP = {'beta': 1, 'alpha': 2}


def parse_semver(tag: str) -> Optional[Tuple[Tuple[int, int, int], List[str]]]:
    """解析为 (核心版本, 预发布段列表)；不合 SemVer（含纯数字段带前导零）时返回 None。"""
    match = SEMVER.match(tag)
    if not match:
        return None
    prerelease = match.group(4).split('.') if match.group(4) else []
    if any(not part or (part.isdigit() and len(part) > 1 and part[0] == '0') for part in prerelease):
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))), prerelease


def core_version(tag: str) -> Optional[Tuple[int, int, int]]:
    parsed = parse_semver(tag)
    return parsed[0] if parsed else None


def compare_versions(left: str, right: str) -> int:
    """按 SemVer 优先级比较，与 MFAAvalonia 和安卓端判断新旧的规则一致。"""
    parsed_left, parsed_right = parse_semver(left), parse_semver(right)
    if parsed_left is None or parsed_right is None:
        raise ValueError(f"无法按 SemVer 解析: {left if parsed_left is None else right}")
    if parsed_left[0] != parsed_right[0]:
        return -1 if parsed_left[0] < parsed_right[0] else 1
    pre_left, pre_right = parsed_left[1], parsed_right[1]
    if not pre_left or not pre_right:
        # 正式版高于同核心的任何预发布版
        return bool(pre_right) - bool(pre_left)
    for a, b in zip(pre_left, pre_right):
        if a == b:
            continue
        if a.isdigit() and b.isdigit():
            return -1 if int(a) < int(b) else 1
        if a.isdigit() != b.isdigit():
            # 纯数字段一律低于含字母段
            return -1 if a.isdigit() else 1
        return -1 if a < b else 1
    return (len(pre_left) > len(pre_right)) - (len(pre_left) < len(pre_right))


def is_valid_formal_version(tag: str) -> bool:
    """判断是否为有效的正式版 - 严格模式"""
    # 严格的正式版模式：v数字.数字.数字，从v2.0.0开始
    if not FORMAL.match(tag):
        return False

    # 只接受v2.0.0及以上的正式版，忽略所有v0.x.x和v1.x.x
    if tag.startswith(('v0.', 'v1.')):
        return False

    return True

def is_valid_alpha_version(tag: str) -> bool:
    """判断是否为有效的内测版（含旧式日期.哈希版号）"""
    return bool(PRERELEASE['alpha'].match(tag) or LEGACY_PRERELEASE['alpha'].match(tag))

def is_valid_beta_version(tag: str) -> bool:
    """判断是否为有效的公测版（含旧式日期.哈希版号）"""
    return bool(PRERELEASE['beta'].match(tag) or LEGACY_PRERELEASE['beta'].match(tag))

def is_valid_ci_version(tag: str) -> bool:
    """判断是否为有效的开发版 - 严格模式"""
    # 必须符合: v数字.数字.数字-ci.6位日期.7位以上哈希
    return bool(CI.match(tag))

def is_nested_version(tag: str) -> bool:
    """检测是否为嵌套版本（需要排除的错误版本）"""
    # 检测包含多个-beta或-ci的嵌套版本
    beta_count = tag.count('-beta')
    ci_count = tag.count('-ci')
    alpha_count = tag.count('-alpha')
    return (beta_count + ci_count + alpha_count) > 1

def filter_valid_versions(tags: List[str]) -> Dict[str, List[str]]:
    """严格过滤有效的版本"""
    result = {
        'formal': [],    # 正式版
        'beta': [],      # 公测版
        'alpha': [],     # 内测版
        'ci': [],        # 开发版
        'invalid': [],   # 无效版本
        'nested': []     # 嵌套错误版本
    }

    for tag in tags:
        # 首先检查是否为嵌套版本（最高优先级排除）
        if is_nested_version(tag):
            result['nested'].append(tag)
            continue

        # 然后检查其他有效版本
        if is_valid_formal_version(tag):
            result['formal'].append(tag)
        elif is_valid_beta_version(tag):
            result['beta'].append(tag)
        elif is_valid_alpha_version(tag):
            result['alpha'].append(tag)
        elif is_valid_ci_version(tag):
            result['ci'].append(tag)
        else:
            result['invalid'].append(tag)

    return result

def sort_versions(versions: List[str]) -> List[str]:
    """按版本号排序（从新到旧）；无法解析的排在最后"""
    parsable = [tag for tag in versions if parse_semver(tag)]
    for tag in versions:
        if not parse_semver(tag):
            print(f"版本排序警告: 无法解析 {tag}")
    ordered = sorted(parsable, key=functools.cmp_to_key(compare_versions), reverse=True)
    return ordered + [tag for tag in versions if not parse_semver(tag)]


def next_version(tags: List[str], channel: str, date: str, sha: str) -> str:
    """生成本次构建的版号。channel 取 alpha / beta / ci；tags 为仓库现有全部标签。"""
    if not re.fullmatch(r'\d{6}', date) or not re.fullmatch(r'[0-9a-f]{7,40}', sha):
        raise ValueError(f"日期或短哈希格式不对: date={date!r} sha={sha!r}")
    formal = [core for core in map(core_version, filter(FORMAL.match, tags)) if core]
    major, minor, patch = max(formal, default=(0, 0, 0))
    if channel == 'ci':
        return f"v{major}.{minor}.{patch}-ci.{date}.{sha}"
    if channel not in PATCH_BUMP:
        raise ValueError(f"未知的版本通道: {channel!r}")

    core = f"v{major}.{minor}.{patch + PATCH_BUMP[channel]}"
    sequences = [int(match.group(1)) for match in map(PRERELEASE[channel].match, tags)
                 if match and match.string.startswith(f"{core}-")]
    tag = f"{core}-{channel}.{max(sequences, default=0) + 1}.{date}.g{sha}"

    # 同一核心版本下已有的任何标签都必须比它旧，否则装着那些版本的用户会把它当成旧版、收不到更新。
    # 典型成因：同一基线上还留着旧式日期.哈希版号，或落后分支用旧工作流发了版。
    blocking = sort_versions([existing for existing in tags
                              if core_version(existing) == core_version(tag) and compare_versions(tag, existing) <= 0])
    if blocking:
        raise ValueError(
            f"{tag} 不比已有标签 {blocking[0]} 新（同基线共 {len(blocking)} 个），客户端会把它当成旧版本。"
            f"若它们是旧式日期.哈希版号，先发布正式版再发{channel}；若是误发的标签，确认后撤掉再重跑。"
        )
    return tag


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="按仓库现有标签生成本次构建的版号")
    parser.add_argument("command", choices=["next"])
    parser.add_argument("channel", choices=["alpha", "beta", "ci"])
    args = parser.parse_args()
    try:
        tag = next_version(_git("tag", "-l", "v*").splitlines(), args.channel,
                           datetime.now(timezone.utc).strftime("%y%m%d"), _git("rev-parse", "--short", "HEAD"))
    except ValueError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    print(tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""通过 JustOneAPI 抓取账号基础信息和账号内容原始列表。

本脚本只产出结构化 JSON，不直接回写 CSV。后续落盘固定交给
`账号基础数据建档批处理.py`。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import 证书信任  # noqa: F401  进程级统一 HTTPS 证书信任
from 权限错误 import permission_error_code
from 本地配置读取 import load_local_config

DEFAULT_BASE_URL = "https://api.justoneapi.com"
SENSITIVE_QUERY_KEYS = {
    "token", "key", "api_key", "apikey", "access_token", "xsec_token", "xsec_source",
}
XIAOHONGSHU_PUBLIC_QUERY_KEYS = {"xsec_source", "xsec_token"}
MAX_AVATAR_BYTES = 8 * 1024 * 1024
AVATAR_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
UPSTREAM_HTTP_STATUSES = {502, 503, 504}
AUTH_HTTP_STATUSES = {401, 403}
MAX_LIST_PAGES = 20
ACCOUNT_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
TRAILING_URL_PUNCTUATION = ",.;:!?，。；：！？、)]}）】》"
SHORT_URL_PLATFORMS = {
    "v.douyin.com": "抖音",
}
PLATFORM_REDIRECT_HOSTS = {
    "抖音": {"v.douyin.com", "douyin.com", "www.douyin.com", "iesdouyin.com", "www.iesdouyin.com"},
}
PROFILE_URL_EXAMPLES = {
    "抖音": "https://www.douyin.com/user/<sec_uid>",
    "小红书": "https://www.xiaohongshu.com/user/profile/<用户ID>",
    "B站": "https://space.bilibili.com/<UID>",
}
PLATFORM_ALIASES = {
    "xiaohongshu": "小红书", "xhs": "小红书", "小红书": "小红书",
    "douyin": "抖音", "dy": "抖音", "抖音": "抖音",
    "bilibili": "B站", "b站": "B站", "哔哩哔哩": "B站",
}


class JustOneApiHttpError(RuntimeError):
    def __init__(self, status_code: int, url: str, body: str = "") -> None:
        self.status_code = status_code
        self.url = redact_url(url)
        self.body = body
        super().__init__(f"HTTP {status_code} {self.url} body={body or '-'}")


class AccountUrlError(RuntimeError):
    def __init__(self, code: str, message: str, **details: str) -> None:
        self.code = code
        self.details = details
        super().__init__(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="通过 JustOneAPI 抓取账号基础信息和内容原始列表 JSON。")
    parser.add_argument("--workspace-root", default=".", help="当前工作区根目录")
    parser.add_argument("--account-url", required=True, help="账号主页链接")
    parser.add_argument("--account-info-json", default="", help="账号基础信息 JSON 路径；正式取数必填，预检时可用于保存结果")
    parser.add_argument("--raw-list-json", default="", help="账号内容原始列表 JSON 输出路径；非预检模式必填")
    parser.add_argument("--avatar-output", default="", help="可选：头像临时输出路径；为空时自动写到账号基础信息 JSON 同目录")
    parser.add_argument("--platform", default="", help="可选：小红书/抖音/B站；为空时从链接推断")
    parser.add_argument("--max-items", type=int, default=0, help="可选：限制保留条数；0 表示不在本地截断")
    parser.add_argument(
        "--acquisition-attempt",
        type=int,
        choices=(1, 2),
        default=1,
        help="本轮采集尝试次数；最多允许首次采集和一次集中重试",
    )
    parser.add_argument("--preflight-only", action="store_true", help="只校验配置和基础连通性，可保存临时账号信息 JSON")
    parser.add_argument("--reuse-account-info", action="store_true", help="正式取数复用预检保存的账号信息，不重复请求账号接口")
    parser.add_argument("--normalize-only", action="store_true", help="只提取、展开并校验账号主页链接，不读取配置或调用付费接口")
    return parser.parse_args()


load_env = load_local_config


def redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = []
    for key, value in query:
        if key.lower() in SENSITIVE_QUERY_KEYS:
            redacted.append((key, "***"))
        else:
            redacted.append((key, value))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(redacted), parsed.fragment))


def read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    return body[:800]


def http_get_json(url: str, *, timeout: int = 60) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = read_error_body(exc)
        raise JustOneApiHttpError(exc.code, url, body) from exc
    except urllib.error.URLError as exc:
        code = permission_error_code(exc, "network")
        if code:
            raise RuntimeError(code) from exc
        raise RuntimeError(f"URL error {redact_url(url)} reason={exc.reason}") from exc


def build_api_url(base_url: str, path: str, params: Dict[str, str]) -> str:
    query = urllib.parse.urlencode({key: value for key, value in params.items() if str(value).strip()})
    return f"{base_url.rstrip('/')}{path}?{query}"


def response_ok(response: Dict[str, Any]) -> bool:
    if response.get("code") == 0 and response.get("data") is not None:
        return True
    if response.get("success") is True and response.get("data") is not None:
        return True
    return False


def dump_raw_response(
    raw_dump_dir: Path | None,
    tag: str,
    request_url: str,
    response: Any,
    *,
    outcome: str = "",
) -> None:
    """把接口原始响应留一份档，供字段类问题事后归因。

    此前取数只保留归一化后的字段，一旦出现「取错字段」或「上游没给」的疑问，
    只能重新花钱调一次接口才能判断。请求地址做了 token 脱敏，响应体原样保存。

    成功与失败都留档：上游错误码只出现在失败响应里（例如 301 Collection Failed），
    只留成功响应会让每次归因都退回「再付费调一次接口」。失败档的文件名带「失败」
    前缀、正文带「结果」字段，与成功档一眼可分。
    留档失败不影响取数主流程，只在标准错误里提示。
    """
    if raw_dump_dir is None:
        return
    try:
        raw_dump_dir.mkdir(parents=True, exist_ok=True)
        index = len(list(raw_dump_dir.glob("*.json"))) + 1
        payload: Dict[str, Any] = {"请求地址": redact_url(request_url)}
        if outcome:
            payload["结果"] = outcome
        payload["响应"] = response
        prefix = f"{outcome}-" if outcome else ""
        write_json(raw_dump_dir / f"{prefix}{tag or 'response'}-{index:02d}.json", payload)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 原始响应留档失败: {exc}", file=sys.stderr)


def call_justoneapi(
    urls: Iterable[str],
    *,
    raw_dump_dir: Path | None = None,
    raw_dump_tag: str = "",
) -> Dict[str, Any]:
    errors: List[str] = []
    http_statuses: List[int] = []
    for url in urls:
        try:
            response = http_get_json(url)
        except JustOneApiHttpError as exc:
            http_statuses.append(exc.status_code)
            errors.append(str(exc))
            dump_raw_response(
                raw_dump_dir,
                raw_dump_tag,
                url,
                {"http_status": exc.status_code, "body": exc.body},
                outcome="失败",
            )
            continue
        except Exception as exc:  # noqa: BLE001
            code = permission_error_code(exc, "network")
            if code:
                raise RuntimeError(code) from exc
            errors.append(str(exc))
            continue
        if response_ok(response):
            dump_raw_response(raw_dump_dir, raw_dump_tag, url, response, outcome="成功")
            return response
        dump_raw_response(raw_dump_dir, raw_dump_tag, url, response, outcome="失败")
        # 上游错误码必须显式带进文案：响应只有 message 时（例如 301 Collection
        # Failed. Please Retry.），原本的 or 短路写法会把 code 整个丢掉，归因者只能
        # 去对官方错误码表反推，拿不到「看到的值」。
        message = str(response.get("message") or response.get("msg") or "").strip()
        detail = f"API failed {redact_url(url)} code={response.get('code')}"
        detail += f" message={message}" if message else f" raw={response}"
        errors.append(detail)
    if http_statuses and len(http_statuses) == len(errors):
        if all(status in UPSTREAM_HTTP_STATUSES for status in http_statuses):
            raise RuntimeError("JUSTONEAPI_UPSTREAM_UNAVAILABLE: " + "；".join(errors))
        if all(status in AUTH_HTTP_STATUSES for status in http_statuses):
            raise RuntimeError("JUSTONEAPI_AUTH_FAILED: " + "；".join(errors))
        if all(status == 429 for status in http_statuses):
            raise RuntimeError("JUSTONEAPI_QUOTA_EXCEEDED: " + "；".join(errors))
    raise RuntimeError("；".join(errors) if errors else "JustOneAPI 接口全部失败")


def infer_platform(account_url: str, explicit: str = "") -> str:
    if explicit.strip():
        return PLATFORM_ALIASES.get(explicit.strip().lower(), explicit.strip())
    url = account_url.lower()
    if "xiaohongshu.com" in url:
        return "小红书"
    if "douyin.com" in url:
        return "抖音"
    if "bilibili.com" in url:
        return "B站"
    raise RuntimeError("无法从账号主页链接识别平台")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def extract_single_url(raw_input: str) -> str:
    urls = []
    for match in ACCOUNT_URL_PATTERN.findall(str(raw_input or "")):
        candidate = match.rstrip(TRAILING_URL_PUNCTUATION)
        if candidate and candidate not in urls:
            urls.append(candidate)
    if not urls:
        raise AccountUrlError(
            "account_url_missing",
            "没有识别到 HTTP/HTTPS 账号链接，请粘贴账号主页链接或包含一个主页短链的分享文案",
        )
    if len(urls) > 1:
        raise AccountUrlError(
            "multiple_account_urls",
            f"识别到 {len(urls)} 条链接，无法确定要诊断哪个账号，请每次只提供一个账号主页链接",
        )
    return urls[0]


def validate_redirect_host(url: str, platform: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in PLATFORM_REDIRECT_HOSTS[platform]:
        raise AccountUrlError(
            "unsafe_account_url_redirect",
            f"{platform}短链跳转到了非官方域名，已停止处理",
        )


def resolve_known_short_url(url: str, platform: str, *, max_redirects: int = 5) -> str:
    """Resolve only allowlisted platform redirects without following an unchecked target."""
    opener = urllib.request.build_opener(_NoRedirectHandler())
    current = url
    for _ in range(max_redirects):
        validate_redirect_host(current, platform)
        request = urllib.request.Request(
            current,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html,*/*"},
            method="HEAD",
        )
        try:
            with opener.open(request, timeout=15) as response:
                final_url = response.geturl() or current
                validate_redirect_host(final_url, platform)
                return final_url
        except urllib.error.HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise AccountUrlError(
                    "account_short_url_unavailable",
                    f"{platform}短链无法展开（HTTP {exc.code}），请直接复制账号主页地址",
                    expected_example=PROFILE_URL_EXAMPLES[platform],
                ) from exc
            location = str(exc.headers.get("Location") or "").strip()
            if not location:
                raise AccountUrlError("account_short_url_unavailable", f"{platform}短链没有返回跳转地址") from exc
            current = urllib.parse.urljoin(current, location)
            validate_redirect_host(current, platform)
            if canonical_profile_url(current, platform):
                return current
        except urllib.error.URLError as exc:
            code = permission_error_code(exc, "network")
            if code:
                raise RuntimeError(code) from exc
            raise AccountUrlError(
                "account_short_url_unavailable",
                f"{platform}短链暂时无法展开，请直接复制账号主页地址",
                expected_example=PROFILE_URL_EXAMPLES[platform],
            ) from exc
    raise AccountUrlError("account_short_url_redirect_limit", f"{platform}短链跳转次数过多，已停止处理")


def canonical_profile_url(url: str, platform: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if platform == "小红书" and host in {"xiaohongshu.com", "www.xiaohongshu.com"}:
        match = re.fullmatch(r"/user/profile/([0-9A-Za-z_-]+)", path)
        return f"https://www.xiaohongshu.com/user/profile/{match.group(1)}" if match else ""
    if platform == "抖音" and host in {"douyin.com", "www.douyin.com", "iesdouyin.com", "www.iesdouyin.com"}:
        match = re.fullmatch(r"/(?:share/)?user/([0-9A-Za-z._-]+)", path)
        sec_uid = match.group(1) if match else ""
        if not sec_uid and path in {"/user", "/share/user"}:
            sec_uid = urllib.parse.parse_qs(parsed.query).get("sec_uid", [""])[0].strip()
        if sec_uid.lower() == "self":
            raise AccountUrlError(
                "account_url_requires_login_resolution",
                "抖音 user/self 只表示当前浏览器登录账号，必须连接已登录抖音的 Chrome 才能解析真实主页",
                platform="抖音",
                expected_example=PROFILE_URL_EXAMPLES["抖音"],
                user_action="在手机抖音打开个人主页，选择“分享主页”，再粘贴完整分享文案",
            )
        return f"https://www.douyin.com/user/{sec_uid}" if sec_uid else ""
    if platform == "B站" and host == "space.bilibili.com":
        match = re.fullmatch(r"/(\d+)", path)
        return f"https://space.bilibili.com/{match.group(1)}" if match else ""
    return ""


def platform_for_url(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in SHORT_URL_PLATFORMS:
        return SHORT_URL_PLATFORMS[host]
    if host in {"xiaohongshu.com", "www.xiaohongshu.com"}:
        return "小红书"
    if host in {"douyin.com", "www.douyin.com", "iesdouyin.com", "www.iesdouyin.com"}:
        return "抖音"
    if host in {"bilibili.com", "www.bilibili.com", "space.bilibili.com"}:
        return "B站"
    raise AccountUrlError("unsupported_account_url", "链接不属于小红书、抖音或B站官方域名")


def canonicalize_account_url_input(raw_input: str) -> Dict[str, str]:
    extracted_url = extract_single_url(raw_input)
    parsed = urllib.parse.urlsplit(extracted_url)
    if parsed.scheme not in {"http", "https"}:
        raise AccountUrlError("invalid_account_url", "账号链接必须使用 HTTP/HTTPS")
    platform = platform_for_url(extracted_url)
    host = (parsed.hostname or "").lower()
    input_type = "profile_url"
    effective_url = extracted_url
    if host in SHORT_URL_PLATFORMS:
        input_type = "share_short_url"
        effective_url = resolve_known_short_url(extracted_url, platform)
    canonical = canonical_profile_url(effective_url, platform)
    if not canonical:
        raise AccountUrlError(
            "account_url_not_profile",
            f"识别到{platform}链接，但它不是可用的账号主页链接；作品链接不能代替账号主页",
            platform=platform,
            expected_example=PROFILE_URL_EXAMPLES[platform],
            user_action="打开目标账号主页后，从浏览器地址栏复制链接，或使用平台的分享主页功能",
        )
    return {
        "platform": platform,
        "account_url": canonical,
        "input_type": input_type,
        "extracted_url": extracted_url,
    }


def normalize_number(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace(",", "")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(万|w|W)?", text)
    if match:
        number = float(match.group(1))
        if match.group(2):
            number *= 10000
        return str(int(number))
    digits = re.sub(r"[^0-9-]", "", text)
    return "" if digits in {"", "-"} else digits


def normalize_labels(value: Any) -> str:
    labels: List[str] = []

    def collect(node: Any) -> None:
        if isinstance(node, str):
            text = node.strip()
            if text:
                labels.append(text)
            return
        if isinstance(node, list):
            for item in node:
                collect(item)
            return
        if isinstance(node, dict):
            for key in ("name", "label", "title", "text"):
                text = str(node.get(key) or "").strip()
                if text:
                    labels.append(text)
                    return

    collect(value)
    return "、".join(dict.fromkeys(labels))


def normalize_url(value: Any, *, preserve_xhs_query: bool = False) -> str:
    text = str(value or "").strip()
    if not text.startswith(("http://", "https://")):
        return ""
    parsed = urllib.parse.urlsplit(text)
    host = parsed.netloc.lower().split("@")[-1].split(":", 1)[0]
    is_xiaohongshu = preserve_xhs_query and (
        host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com") or host == "xhslink.com"
    )
    safe_query = [
        (key, item)
        for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in SENSITIVE_QUERY_KEYS
        or (is_xiaohongshu and key.lower() in XIAOHONGSHU_PUBLIC_QUERY_KEYS)
    ]
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(safe_query), "")
    )


def normalize_media_type(value: Any, platform: str) -> str:
    text = str(value or "").strip()
    normalized = text.lower()
    if normalized in {"视频", "video", "video_note", "movie"}:
        return "视频"
    if normalized in {"图文", "normal", "image", "images", "text", "note"}:
        return "图文"
    if text:
        return "其他"
    return "视频" if platform in {"抖音", "B站"} else "未知"


DOUYIN_IMAGE_ALBUM_FIELDS = ("images", "image_list", "image_infos", "img_bitrate", "original_images")


def has_image_album(item: Dict[str, Any]) -> bool:
    """图集条目的图片节点必须是非空集合。"""
    for key in DOUYIN_IMAGE_ALBUM_FIELDS:
        value = item.get(key)
        if isinstance(value, (list, tuple, dict)) and len(value) > 0:
            return True
    return False


def resolve_media_type(item: Dict[str, Any], platform: str) -> str:
    """判定单条内容的媒介形式。

    抖音作品列表的 `media_type` 是数字枚举（公开接口实测视频为 4），字符串匹配表
    认不出，会把合法视频写成「其他」，并因此顶掉平台默认值。抖音一律按结构判定：
    带非空图片集合的是图文，其余是视频，与《获取账号内容原始列表.md》中抖音固定
    为视频的约定一致。其他平台仍按原字段映射。
    """
    if platform == "抖音" and isinstance(item, dict):
        return "图文" if has_image_album(item) else "视频"
    if platform == "B站" and isinstance(item, dict):
        # B站 空间投稿列表接口只返回视频（`goto = av`），项内没有 media_type / type。
        # 若走下面的通用取值，`find_first` 会递归命中 `three_point[].type`（值形如
        # `addtoview` / `share`），把合法视频判成「其他」——实测 2026-09-18 野兽游戏
        # MrBeast 的 8 条投稿全部被写成「其他」。按字段映射契约
        # （《获取账号内容原始列表.md》CONTENT-10 / 媒介形式唯一规范），B站 默认「视频」。
        return "视频"
    return normalize_media_type(find_first(item, ("媒介形式", "media_type", "type")), platform)


def resolve_publish_time(item: Dict[str, Any], platform: str) -> str:
    """取发布时间的原始取值，交给「发布时间解析」统一归一化成工作区格式。

    列表接口都带发布时间，但字段名各平台不同，此前这一列没有做任何映射：JustOneAPI
    路径下三个平台的正式「发布时间」全为空（2026-09-18 实测 小红书 `create_time`、
    抖音 `create_time`、B站 `ctime` 都在原始响应里躺着）。这里按平台取键，值统一
    交给下游 `normalize_publish_time_text` 解析（秒级时间戳与「9月11日」都能吃）。
    """
    if platform == "B站":
        keys = ("ctime", "pubdate", "publish_time", "publish_time_text")
    elif platform == "小红书":
        keys = ("create_time", "time", "publish_time", "publish_time_text")
    else:
        keys = ("create_time", "publish_time", "publish_time_text", "time")
    return str(find_first(item, keys) or "").strip()


def normalize_public_status(item: Dict[str, Any]) -> str:
    for node in content_visibility_nodes(item):
        if any(
            explicit_true(value)
            for key, value in node.items()
            if normalize_marker(key) in PRIVATE_TRUE_FIELDS
        ):
            return "私密"
        if any(
            explicit_true(value)
            for key, value in node.items()
            if normalize_marker(key) in DRAFT_TRUE_FIELDS
        ) or any(
            explicit_false(value)
            for key, value in node.items()
            if normalize_marker(key) in PUBLISHED_FALSE_FIELDS
        ):
            return "草稿"
        # 「非私密」是明确的公开信号：仅当同一节点没有审核中 / 被禁止 / 已删除时才成立；
        # 有阻塞信号时宁可留「未知」交给下游兜底，也不把还看不见的内容写成公开。
        if not any(
            explicit_true(value)
            for key, value in node.items()
            if normalize_marker(key) in REVIEW_BLOCKING_TRUE_FIELDS
        ) and any(
            explicit_false(value)
            for key, value in node.items()
            if normalize_marker(key) in PUBLIC_FALSE_FIELDS
        ):
            return "公开"
        status = next(
            (value for key, value in node.items() if normalize_marker(key) in VISIBILITY_STATUS_FIELDS),
            None,
        )
        marker = normalize_marker(status)
        if not marker:
            continue
        if marker in {"public", "visible", "public_page_visible", "published", "公开", "已发布"}:
            return "公开"
        if marker in {"draft", "unpublished", "not_published", "草稿", "未发布"}:
            return "草稿"
        if marker in {"private", "self_only", "only_self", "only_me", "仅自己可见", "私密"}:
            return "私密"
        if marker in {"unknown", "未知"}:
            return "未知"
        return "其他"
    return "未知"


def first_url(value: Any) -> str:
    direct = normalize_url(value) if isinstance(value, str) else ""
    if direct:
        return direct
    if isinstance(value, list):
        for item in value:
            found = first_url(item)
            if found:
                return found
    elif isinstance(value, dict):
        preferred_keys = ["url_list", "urlList", "urls", "url", "src", "href"]
        for key in preferred_keys:
            if key in value:
                found = first_url(value[key])
                if found:
                    return found
        for item in value.values():
            found = first_url(item)
            if found:
                return found
    return ""


def find_first_url(
    node: Any,
    keys: Iterable[str],
    *,
    skip_fields: Iterable[str] = (),
) -> str:
    """按键优先级在子树里找第一个 URL。

    `skip_fields` 里的键（按归一化键名比较）所在的子树整棵跳过，用于取封面时
    绕开 `author` / `music` 这类节点——它们里面的 cover 属于账号或音频，
    不是作品封面。不传时行为与原来完全一致。
    """
    ordered_keys = tuple(keys)
    normalized_skip = (
        {normalize_marker(field) for field in skip_fields} if skip_fields else set()
    )
    if isinstance(node, dict):
        for key in ordered_keys:
            if key in node:
                found = first_url(node[key])
                if found:
                    return found
        for key, value in node.items():
            if normalized_skip and normalize_marker(key) in normalized_skip:
                continue
            found = find_first_url(value, ordered_keys, skip_fields=normalized_skip)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_first_url(item, ordered_keys, skip_fields=normalized_skip)
            if found:
                return found
    return ""


def is_xiaohongshu_page_url(value: Any) -> bool:
    """Return true only for an XHS content page, never for media CDN URLs."""
    text = str(value or "").strip()
    if not text.startswith(("http://", "https://")):
        return False
    parsed = urllib.parse.urlsplit(text)
    host = parsed.netloc.lower().split("@")[-1].split(":", 1)[0]
    if host != "xiaohongshu.com" and not host.endswith(".xiaohongshu.com"):
        return False
    return bool(
        re.fullmatch(r"/explore/[0-9A-Za-z_-]+/?", parsed.path)
        or re.fullmatch(r"/discovery/item/[0-9A-Za-z_-]+/?", parsed.path)
        or re.fullmatch(r"/user/profile/[^/?#]+/[0-9A-Za-z_-]+/?", parsed.path)
    )


def find_first_xiaohongshu_page_url(item: Dict[str, Any]) -> str:
    """Find an explicit XHS page URL without traversing arbitrary media URLs."""
    explicit_keys = ("share_url", "note_url", "页面链接", "对外页面链接")
    def visit(node: Any) -> str:
        if isinstance(node, dict):
            for key in explicit_keys:
                candidate = normalize_url(node.get(key), preserve_xhs_query=True)
                if is_xiaohongshu_page_url(candidate):
                    return candidate
            for value in node.values():
                found = visit(value)
                if found:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = visit(value)
                if found:
                    return found
        return ""

    found = visit(item)
    if found:
        return found
    # Some API versions expose the page href as a top-level `url`/`href`.
    for key in ("url", "href"):
        candidate = normalize_url(item.get(key), preserve_xhs_query=True)
        if is_xiaohongshu_page_url(candidate):
            return candidate
    return ""


XHS_TOKEN_PATTERN = re.compile(r"[?&]xsec_token=([^&#\"'\s]+)")


def find_xiaohongshu_token(item: Dict[str, Any]) -> str:
    """从 JustOneAPI 返回的任意字段里找 xsec_token。

    小红书公开页没有 token 就打不开，而不同接口把 token 放在不同层：
    有的给 `xsec_token` 字段，有的只藏在 `share_url` 的查询串里。
    """
    for key in ("xsec_token", "xsecToken"):
        value = item.get(key) if isinstance(item, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    try:
        serialized = json.dumps(item, ensure_ascii=False)
    except (TypeError, ValueError):
        return ""
    match = XHS_TOKEN_PATTERN.search(serialized)
    return urllib.parse.unquote(match.group(1)) if match else ""


def avatar_extension(data: bytes, content_type: str, final_url: str) -> str:
    normalized_type = str(content_type or "").split(";", 1)[0].strip().lower()
    type_extensions = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
    }
    if normalized_type in type_extensions:
        return type_extensions[normalized_type]
    suffix = Path(urllib.parse.urlsplit(final_url).path).suffix.lower()
    if suffix in AVATAR_EXTENSIONS:
        return suffix
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("头像响应不是受支持的 PNG/JPG/WEBP 图片")


def prefer_web_image_url(url: str) -> str:
    """把 HEIC/HEIF 图片链接换成受支持的格式再下载。

    抖音图床按路径扩展名实时转码：同一个头像对象把 `.heic` 换成 `.jpeg`（或
    `.png`）会返回真正的 JPEG/PNG，因此无需引入 HEIC 解码依赖。非 HEIC 链接
    原样返回，行为不变。
    """
    cleaned = str(url or "").strip()
    if not cleaned:
        return cleaned
    parsed = urllib.parse.urlsplit(cleaned)
    rewritten_path = re.sub(r"\.(heic|heif)$", ".jpeg", parsed.path, flags=re.IGNORECASE)
    if rewritten_path == parsed.path:
        return cleaned
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, rewritten_path, parsed.query, parsed.fragment)
    )


def download_avatar(url: str, output_path: Path, *, timeout: int = 10) -> Path:
    url = prefer_web_image_url(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "image/webp,image/png,image/jpeg,*/*"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_AVATAR_BYTES + 1)
            if len(data) > MAX_AVATAR_BYTES:
                raise ValueError("头像文件超过 8MB 限制")
            content_type = response.headers.get("Content-Type", "")
            final_url = response.geturl()
    except Exception as exc:  # noqa: BLE001
        code = permission_error_code(exc, "network")
        if code:
            raise RuntimeError(code) from exc
        raise
    if not data:
        raise ValueError("头像响应为空")
    extension = avatar_extension(data, content_type, final_url)
    requested_suffix = output_path.suffix.lower()
    suffix_matches = requested_suffix == extension or {requested_suffix, extension} == {".jpg", ".jpeg"}
    target = output_path if requested_suffix in AVATAR_EXTENSIONS and suffix_matches else output_path.with_suffix(extension)
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(data)
            temporary.replace(target)
        except OSError as exc:
            code = permission_error_code(exc, "filesystem")
            if code:
                raise RuntimeError(code) from exc
            raise
    finally:
        temporary.unlink(missing_ok=True)
    return target.resolve()


def find_first(node: Any, keys: Iterable[str]) -> Any:
    """按调用方给定的优先级取值，不依赖字段在 JSON 中的出现顺序。

    候选名必须是有序序列。原实现按 ``node.items()`` 遍历，命中顺序由接口
    返回的字段顺序决定（列表接口多为字母序），``desc`` / ``name`` / ``url``
    这类通用名会抢在更准确的字段前面，把正文、作者名或无关链接取回来。
    """
    ordered_keys = tuple(keys)
    if isinstance(node, dict):
        for key in ordered_keys:
            if key in node:
                value = node[key]
                if value not in (None, ""):
                    return value
        for value in node.values():
            found = find_first(value, ordered_keys)
            if found not in (None, ""):
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_first(item, ordered_keys)
            if found not in (None, ""):
                return found
    return ""


def find_items(node: Any) -> List[Dict[str, Any]]:
    if isinstance(node, list):
        dict_items = [item for item in node if isinstance(item, dict)]
        if dict_items and any(find_first(item, ("title", "aweme_id", "note_id", "bvid", "desc")) for item in dict_items):
            return dict_items
        for item in dict_items:
            found = find_items(item)
            if found:
                return found
    if isinstance(node, dict):
        # B站 把内容放在单数 `item`，而同一个 dict 里还有 `order`（分区定义，
        # 元素同样带 `title`）。`item` 不在候选键里时会退化到遍历 values，先撞上
        # `order`，把「最新发布/最多播放」当成内容列表返回 —— 而且全程不报错。
        preferred_keys = ["items", "list", "aweme_list", "notes", "archives", "videos", "works", "item"]
        for key in preferred_keys:
            if key in node:
                found = find_items(node[key])
                if found:
                    return found
        for value in node.values():
            found = find_items(value)
            if found:
                return found
    return []


NON_PUBLIC_TRUE_FIELDS = {
    "is_private",
    "is_privacy",
    "private",
    "is_only_self",
    "only_self",
    "only_self_visible",
    "is_draft",
    "draft",
    "is_unpublished",
    "unpublished",
    # 抖音等平台的原生键名是 private_status，此前只认 privacy_status。
    "private_status",
}
PUBLISHED_FALSE_FIELDS = {"is_published", "is_publish", "published"}
VISIBILITY_STATUS_FIELDS = {
    "visibility",
    "visible_status",
    "privacy_status",
    "publish_status",
    "publication_status",
    "公开状态",
    "可见性",
}
NON_PUBLIC_STATUS_VALUES = {
    "private",
    "self_only",
    "only_self",
    "draft",
    "unpublished",
    "not_published",
    "仅自己可见",
    "私密",
    "草稿",
    "未发布",
}
PRIVATE_TRUE_FIELDS = {
    "is_private",
    "is_privacy",
    "private",
    "is_only_self",
    "only_self",
    "only_self_visible",
    # 抖音作品的原生字段是 status.private_status（1 = 仅自己可见，0 = 非私密）。
    # 此前只认 privacy_status，与真实键名一词之差，整批作品只能落到「未知」。
    "private_status",
}
DRAFT_TRUE_FIELDS = {"is_draft", "draft", "is_unpublished", "unpublished"}
# 值为 0/false 时明确表示「非私密」的键。抖音作品用 `status.private_status` 表达
# 可见性（1 = 仅自己可见，0 = 非私密），0 是明确的公开信号，不该只留「未知」。
PUBLIC_FALSE_FIELDS = {"private_status"}
# 这些键为真时内容其实还不可见（审核中 / 被禁止 / 已删除），不能据此判公开。
REVIEW_BLOCKING_TRUE_FIELDS = {"in_reviewing", "is_prohibited", "is_delete"}
IDENTITY_CONTAINER_FIELDS = {"author", "user", "owner", "account", "profile", "creator"}
# 取封面时要跳过的子树：身份/账号节点与音乐节点里的 cover 属于账号或音频，
# 不是作品封面（抖音的 author.cover_url 就是账号级背景图，整个账号同一个值）。
COVER_TRAVERSAL_SKIP_FIELDS = IDENTITY_CONTAINER_FIELDS | {
    "music",
    "music_info",
    "music_info_v2",
    "bgm",
}


def normalize_marker(value: Any) -> str:
    return re.sub(r"[\s-]+", "_", str(value or "").strip().lower())


def explicit_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return normalize_marker(value) in {"1", "true", "yes", "是", "private", "draft", "unpublished", "仅自己可见", "私密", "草稿", "未发布"}


def explicit_false(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    return normalize_marker(value) in {"0", "false", "no", "否", "unpublished", "not_published", "未发布"}


def content_visibility_nodes(node: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for key, value in node.items():
            if normalize_marker(key) in IDENTITY_CONTAINER_FIELDS:
                continue
            if isinstance(value, (dict, list)):
                yield from content_visibility_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from content_visibility_nodes(item)


def is_explicitly_non_public(item: Dict[str, Any]) -> bool:
    for node in content_visibility_nodes(item):
        for key, value in node.items():
            normalized_key = normalize_marker(key)
            if normalized_key in NON_PUBLIC_TRUE_FIELDS and explicit_true(value):
                return True
            if normalized_key in PUBLISHED_FALSE_FIELDS and explicit_false(value):
                return True
            if normalized_key in VISIBILITY_STATUS_FIELDS and normalize_marker(value) in NON_PUBLIC_STATUS_VALUES:
                return True
    return False


def filter_public_content_items(items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Compatibility wrapper: retain every item and expose visibility as a field."""
    return list(items), 0


def find_pagination_flag(node: Any) -> bool | None:
    marker_keys = {"has_more", "hasMore", "has_next", "hasNext", "more"}
    if isinstance(node, dict):
        for key, value in node.items():
            if key not in marker_keys:
                continue
            if isinstance(value, bool):
                return value
            normalized = str(value).strip().lower()
            if normalized in {"1", "true", "yes", "是"}:
                return True
            if normalized in {"0", "false", "no", "否"}:
                return False
        for value in node.values():
            found = find_pagination_flag(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_pagination_flag(item)
            if found is not None:
                return found
    return None


def find_pagination_value(node: Any, keys: Tuple[str, ...]) -> str:
    if isinstance(node, dict):
        for key in keys:
            value = node.get(key)
            if value not in (None, "") and not isinstance(value, (dict, list, bool)):
                return str(value).strip()
        for value in node.values():
            found = find_pagination_value(value, keys)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_pagination_value(item, keys)
            if found:
                return found
    return ""


def find_last_item_cursor(items: List[Dict[str, Any]]) -> str:
    if not items:
        return ""
    last_item = items[-1]
    if not isinstance(last_item, dict):
        return ""
    return find_pagination_value(last_item, ("cursor",))


def find_item_list_by_keys(node: Any, keys: Tuple[str, ...]) -> List[Dict[str, Any]]:
    if isinstance(node, dict):
        for key in keys:
            value = node.get(key)
            if isinstance(value, list):
                items = [item for item in value if isinstance(item, dict)]
                if items:
                    return items
        for value in node.values():
            found = find_item_list_by_keys(value, keys)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_item_list_by_keys(item, keys)
            if found:
                return found
    return []


def next_page_params(platform: str, payload: Dict[str, Any], page_number: int) -> Dict[str, str]:
    data = payload.get("data", payload)
    if platform == "小红书":
        items = find_item_list_by_keys(data, ("notes", "note_list", "items", "list"))
        cursor = find_last_item_cursor(items) or find_pagination_value(
            data, ("last_cursor", "lastCursor", "next_cursor", "nextCursor", "cursor")
        )
        return {"lastCursor": cursor} if cursor else {}
    if platform == "抖音":
        cursor = find_pagination_value(data, ("next_cursor", "nextCursor", "max_cursor", "maxCursor", "cursor"))
        return {"maxCursor": cursor} if cursor else {}
    if platform == "B站":
        cursor = find_pagination_value(data, ("param", "next_param", "nextParam", "next_cursor", "nextCursor", "cursor"))
        return {"param": cursor} if cursor else {}
    return {}


def extract_xiaohongshu_user_id(url: str) -> str:
    match = re.search(r"/user/profile/([0-9A-Za-z_-]+)", url)
    return match.group(1) if match else ""


def extract_douyin_sec_uid(url: str) -> str:
    match = re.search(r"douyin\.com/user/([0-9A-Za-z._-]+)", url)
    if not match:
        return ""
    value = match.group(1).strip()
    return "" if value.lower() in {"self"} else value


def extract_bilibili_mid(url: str) -> str:
    match = re.search(r"space\.bilibili\.com/(\d+)", url)
    return match.group(1) if match else ""


def fetch_account_payload(
    platform: str,
    base_url: str,
    token: str,
    account_url: str,
    *,
    raw_dump_dir: Path | None = None,
) -> Dict[str, Any]:
    dump_kwargs = {"raw_dump_dir": raw_dump_dir, "raw_dump_tag": f"{platform}-账号信息"}
    if platform == "小红书":
        user_id = extract_xiaohongshu_user_id(account_url)
        if not user_id:
            raise RuntimeError("小红书主页链接缺少可识别的用户ID")
        return call_justoneapi(
            [build_api_url(base_url, "/api/xiaohongshu/get-user/v3", {"token": token, "userId": user_id})],
            **dump_kwargs,
        )
    if platform == "抖音":
        sec_uid = extract_douyin_sec_uid(account_url)
        if not sec_uid:
            raise RuntimeError("抖音主页链接缺少可识别的 secUid，请使用公开主页链接")
        return call_justoneapi(
            [build_api_url(base_url, "/api/douyin/get-user-detail/v3", {"token": token, "secUid": sec_uid})],
            **dump_kwargs,
        )
    if platform == "B站":
        mid = extract_bilibili_mid(account_url)
        if not mid:
            raise RuntimeError("B站主页链接缺少可识别的 UID")
        return call_justoneapi(
            [build_api_url(base_url, "/api/bilibili/get-user-detail/v2", {"token": token, "uid": mid})],
            **dump_kwargs,
        )
    raise RuntimeError(f"暂不支持平台: {platform}")


def fetch_list_payload(
    platform: str,
    base_url: str,
    token: str,
    account_url: str,
    account_id: str,
    page_params: Dict[str, str] | None = None,
    *,
    raw_dump_dir: Path | None = None,
    page_number: int = 0,
) -> Dict[str, Any]:
    pagination = page_params or {}
    dump_kwargs = {
        "raw_dump_dir": raw_dump_dir,
        "raw_dump_tag": f"{platform}-内容列表-第{page_number}页",
    }
    if platform == "小红书":
        user_id = account_id.strip() or extract_xiaohongshu_user_id(account_url)
        if not user_id:
            raise RuntimeError("小红书账号缺少可识别的用户ID")
        return call_justoneapi(
            [
                build_api_url(
                    base_url,
                    "/api/xiaohongshu/get-user-note-list/v4",
                    {"token": token, "userId": user_id, **pagination},
                )
            ],
            **dump_kwargs,
        )
    if platform == "抖音":
        sec_uid = account_id.strip() or extract_douyin_sec_uid(account_url)
        if not sec_uid:
            raise RuntimeError("抖音账号缺少可识别的 secUid")
        return call_justoneapi(
            [
                build_api_url(
                    base_url,
                    # 抖音作品列表官方有 v1/v3 两版，v1 是文档标记「推荐」的那版。
                    # v3 仍在文档目录里但已采不动：翻页稳定 301，且第 1 页会漏掉近期作品。
                    "/api/douyin/get-user-video-list/v1",
                    {"token": token, "secUid": sec_uid, **pagination},
                )
            ],
            **dump_kwargs,
        )
    if platform == "B站":
        uid = account_id.strip() or extract_bilibili_mid(account_url)
        if not uid:
            raise RuntimeError("B站账号缺少可识别的 UID")
        return call_justoneapi(
            [
                build_api_url(
                    base_url,
                    "/api/bilibili/get-user-video-list/v2",
                    {"token": token, "uid": uid, **pagination},
                )
            ],
            **dump_kwargs,
        )
    raise RuntimeError(f"暂不支持平台: {platform}")


def fetch_all_list_pages(
    platform: str,
    base_url: str,
    token: str,
    account_url: str,
    account_id: str,
    *,
    raw_dump_dir: Path | None = None,
    max_items: int = 0,
) -> Tuple[List[Dict[str, Any]], bool | None, str]:
    all_items: List[Dict[str, Any]] = []
    seen_page_params: set[Tuple[Tuple[str, str], ...]] = set()
    page_params: Dict[str, str] = {}

    for page_number in range(1, MAX_LIST_PAGES + 1):
        try:
            payload = fetch_list_payload(
                platform,
                base_url,
                token,
                account_url,
                account_id,
                page_params,
                raw_dump_dir=raw_dump_dir,
                page_number=page_number,
            )
        except RuntimeError as exc:
            if not all_items:
                raise
            return all_items, True, f"第 {page_number} 页获取失败: {exc}"

        all_items.extend(find_items(payload.get("data", payload)))
        if max_items > 0 and len(all_items) >= max_items:
            return all_items[:max_items], True, f"达到本轮内容数量上限 {max_items} 条"
        has_more = find_pagination_flag(payload.get("data", payload))
        if has_more is False:
            return all_items, False, "JustOneAPI 已读取到列表末页"
        if has_more is None:
            return all_items, None, "JustOneAPI 未返回明确的分页结束标记"

        following_params = next_page_params(platform, payload, page_number)
        params_key = tuple(sorted(following_params.items()))
        if not following_params:
            return all_items, True, "JustOneAPI 表示仍有下一页，但没有返回可用游标"
        if params_key in seen_page_params:
            return all_items, True, "JustOneAPI 返回了重复分页游标"
        seen_page_params.add(params_key)
        page_params = following_params

    return all_items, True, f"达到 API 最大分页上限 {MAX_LIST_PAGES} 页"


def normalize_account_info(platform: str, payload: Dict[str, Any], account_url: str) -> Dict[str, str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    avatar_keys = [
        "avatar_larger",
        "avatar_medium",
        "avatar_thumb",
        "avatar_url_list",
        "avatarUrlList",
        "avatar_url",
        "avatarUrl",
        "avatar",
        "head_url",
        "headUrl",
        "face",
    ]
    if platform == "小红书":
        avatar_keys.extend(["images", "imageb", "image"])
    account_id = str(
        find_first(data, ("sec_uid", "secUid", "user_id", "userId", "userid", "mid", "uid")) or ""
    ).strip()
    account_name = str(find_first(data, ("nickname", "user_name", "username", "name")) or "").strip()
    # Only explicit profile fields may identify the account homepage. A generic
    # nested `url` is often an avatar or cover URL.
    home_url = normalize_url(find_first(data, ("主页链接", "home_url", "profile_url")) or account_url)
    return {
        "状态": "成功",
        "基础数据获取方式": "JustOneAPI",
        "平台": platform,
        "账号ID": account_id,
        "账号名": account_name,
        "简介": str(find_first(data, ("description", "signature", "sign", "bio", "desc")) or "").strip(),
        "性别": str(find_first(data, ("gender", "sex")) or "").strip(),
        "关注数": normalize_number(find_first(data, ("following_count", "follows", "following"))),
        "粉丝数": normalize_number(find_first(data, ("follower_count", "fans", "follower"))),
        "获赞数": normalize_number(
            find_first(
                data,
                (
                    "total_favorited",
                    "totalFavorited",
                    "received_like_count",
                    "total_likes",
                    "like_num",
                    "liked_count",
                    "likedCount",
                    "like_count",
                    "likes",
                    "liked",
                ),
            )
        ),
        "标签": normalize_labels(find_first(data, ("account_labels", "labels", "tags"))),
        "主页链接": home_url,
        "头像原链接": find_first_url(data, avatar_keys),
    }


def resolve_douyin_cover(item: Dict[str, Any]) -> str:
    """只从抖音作品自身的封面字段取封面，不落到账号级字段。

    `author.cover_url` 是账号级背景图，同一账号所有作品都是同一个值；
    而通用模糊匹配（keys 含 `cover_url`）会先命中所属 `author` 子树，
    导致同一账号全部作品拿到同一张占位封面。这里按作品字段精确取值；
    作品自身没有可用封面时返回空串，交由详细数据阶段用 `video.cover` 补全。
    """
    if not isinstance(item, dict):
        return ""
    video = item.get("video") if isinstance(item.get("video"), dict) else {}
    for key in ("cover", "origin_cover", "dynamic_cover"):
        url = first_url(video.get(key))
        if url:
            return url
    for images_key in ("images", "image_list", "image_infos", "original_images"):
        images = item.get(images_key)
        if not isinstance(images, list):
            continue
        for image in images:
            if isinstance(image, dict):
                for url_key in ("url_list", "url_list_large", "url_list_medium"):
                    url = first_url(image.get(url_key))
                    if url:
                        return url
            url = first_url(image)
            if url:
                return url
    for key in ("cover", "pic", "pic_url", "image"):
        url = first_url(item.get(key))
        if url:
            return url
    return ""


def resolve_xiaohongshu_gallery_cover(item: Dict[str, Any]) -> str:
    """取小红书图文笔记的封面：图集第一张，优先大图。

    图文笔记既没有 ``cover`` 也没有 ``video_info_v2``，封面只存在于
    ``images_list``；通用候选里不含这个字段，图文封面便会整批落空。
    视频笔记自身能取到封面，不走这里，避免改变它的取值来源。
    """
    images = item.get("images_list") if isinstance(item, dict) else None
    if not isinstance(images, list):
        return ""
    for image in images:
        if not isinstance(image, dict):
            continue
        for key in ("url_size_large", "url_default", "url_large", "url"):
            value = image.get(key)
            if isinstance(value, str) and value.strip():
                return normalize_url(value)
    return ""


def resolve_content_title(platform: str, item: Dict[str, Any]) -> str:
    """按平台显式优先级取标题，不依赖字段在 JSON 中的出现顺序。

    小红书笔记的正文在 ``desc``、标题在 ``title`` / ``display_title``。列表接口
    返回的笔记字段按字母序排列，``desc`` 恰好排在 ``title`` 之前，用 ``find_first``
    会把正文当成标题取走；因此小红书固定只从标题字段取值，取不到就留空，
    不回落到正文。其它平台保持原有取值规则不变。
    """
    if platform == "小红书":
        for key in ("title", "display_title"):
            value = item.get(key) if isinstance(item, dict) else None
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    if platform == "抖音":
        # 抖音作品的标题就是文案本身，落在 desc。
        return str(find_first(item, ("desc", "title", "name")) or "").strip()
    return str(find_first(item, ("title", "desc", "name")) or "").strip()


def normalize_content_item(platform: str, account: Dict[str, str], item: Dict[str, Any]) -> Dict[str, str]:
    title = resolve_content_title(platform, item)
    if platform == "抖音":
        cover = resolve_douyin_cover(item)
    else:
        cover = find_first_url(
            item,
            ("cover", "cover_url", "coverUrl", "pic", "pic_url", "image"),
            skip_fields=COVER_TRAVERSAL_SKIP_FIELDS,
        )
        # 图文笔记的封面只在 images_list 里；视频笔记在上面已经取到，不受影响。
        if not cover and platform == "小红书":
            cover = resolve_xiaohongshu_gallery_cover(item)
    note_url = "" if platform == "小红书" else normalize_url(
        find_first(item, ("note_url", "share_url", "对外页面链接", "页面链接", "href", "url")),
        preserve_xhs_query=False,
    )
    native_id = ""
    if platform == "抖音":
        native_id = str(find_first(item, ("aweme_id", "video_id", "item_id")) or "").strip()
        if native_id:
            note_url = f"https://www.douyin.com/video/{native_id}"
    elif platform == "B站":
        bvid = str(find_first(item, ("bvid", "BVID")) or "").strip()
        # B站 列表项里 aid 落在 `param`、cid 落在 `first_cid`。缺 cid 会让详细数据
        # 阶段取不到视频下载链接（playurl 按 cid 取流），进而让 ASR 无流可用。
        aid = str(find_first(item, ("aid", "param")) or "").strip()
        cid = str(find_first(item, ("cid", "first_cid")) or "").strip()
        native_id = bvid or aid
        if bvid:
            note_url = f"https://www.bilibili.com/video/{bvid}"
    elif platform == "小红书":
        # Keep the platform-specific note ID ahead of generic IDs returned by
        # author or wrapper objects.
        for key in ("note_id", "noteId", "id"):
            value = item.get(key) if isinstance(item, dict) else ""
            if value not in (None, ""):
                native_id = str(value).strip()
                break
        note_url = find_first_xiaohongshu_page_url(item)
        token = find_xiaohongshu_token(item)
        # 小红书公开页必须带 xsec_token：不带会 302 到 404「当前笔记暂时无法浏览」。
        if native_id and token:
            note_url = (
                f"https://www.xiaohongshu.com/explore/{native_id}"
                f"?xsec_token={token}&xsec_source=pc_creatormng"
            )
        elif native_id and not note_url and title:
            # 拿不到 token 时退回搜索结果页，至少不会落成死链。
            note_url = (
                "https://www.xiaohongshu.com/search_result?keyword="
                + urllib.parse.quote(title)
            )
    media_type = resolve_media_type(item, platform)
    return {
        "平台": platform,
        "账号ID": account["账号ID"],
        "账号名": account["账号名"],
        "标题": title,
        "封面图原链接": cover,
        "媒介形式": media_type,
        "公开状态": normalize_public_status(item),
        # B站 列表项没有点赞字段，只有 `play`（播放量）。`play` 以前在兜底序列里，
        # 会把播放量顶着「点赞数」写进正式 CSV（实测 719270 / 5535246），并一路污染
        # 概要分析。这里去掉 `play`：宁可点赞数为空（详细数据阶段可从
        # `x/web-interface/view` 的 `stat.like` 补），也不让播放量冒充点赞数。
        "点赞数": normalize_number(
            find_first(item, ("liked_count", "likedCount", "like_count", "digg_count", "diggCount", "likes"))
        ),
        "发布时间": resolve_publish_time(item, platform),
        "对外页面链接": note_url,
        "基础数据获取方式": "JustOneAPI",
        "平台原生内容ID": native_id,
        "bvid": bvid if platform == "B站" else str(find_first(item, ("bvid", "BVID")) or "").strip(),
        "aid": aid if platform == "B站" else str(find_first(item, ("aid",)) or "").strip(),
        "cid": cid if platform == "B站" else str(find_first(item, ("cid",)) or "").strip(),
    }


def deduplicate_normalized_items(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for item in items:
        native_id = str(item.get("平台原生内容ID") or "").strip()
        page_url = str(item.get("对外页面链接") or "").strip()
        title = str(item.get("标题") or "").strip()
        identity = ("native", native_id) if native_id else ("url", page_url) if page_url else ("title", title)
        if not identity[1] or identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return result


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_cached_account_info(path: Path, expected_platform: str) -> Dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"预检账号信息不存在: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"预检账号信息无法读取: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("预检账号信息格式错误")
    account_info = {str(key): str(value) if value is not None else "" for key, value in payload.items()}
    if account_info.get("平台") != expected_platform:
        raise RuntimeError("预检账号信息与当前账号平台不一致")
    if account_info.get("基础数据获取方式") != "JustOneAPI":
        raise RuntimeError("预检账号信息不是由 JustOneAPI 获取")
    return account_info


def validate_account_info(account_info: Dict[str, str]) -> None:
    missing = [field for field in ["平台", "账号ID", "账号名", "主页链接"] if not account_info.get(field)]
    if missing:
        raise RuntimeError("JustOneAPI 账号基础字段缺失: " + ", ".join(missing))


def main() -> int:
    try:
        args = parse_args()
        normalized_input = canonicalize_account_url_input(args.account_url)
        account_url = normalized_input["account_url"]
        platform = infer_platform(account_url, args.platform)
        if args.normalize_only:
            print(json.dumps({"ok": True, **normalized_input}, ensure_ascii=False, indent=2))
            return 0
        workspace_root = Path(args.workspace_root).expanduser().resolve()
        # 原始响应留档目录：跟着本轮暂存产物走，不新增命令行参数。
        raw_dump_dir: Path | None = None
        raw_dump_dir_display = ""
        for raw_output in (args.raw_list_json, args.account_info_json):
            if raw_output:
                raw_dump_dir = (
                    Path(raw_output).expanduser().resolve().parent / "justoneapi-原始响应"
                )
                try:
                    raw_dump_dir_display = str(raw_dump_dir.relative_to(workspace_root))
                except ValueError:
                    raw_dump_dir_display = str(raw_dump_dir)
                break
        env = load_env(workspace_root / "本地配置.txt")
        token = env.get("JUSTONEAPI_TOKEN", "").strip()
        base_url = env.get("JUSTONEAPI_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
        if not token:
            raise RuntimeError("JUSTONEAPI_TOKEN 缺失")
        if args.preflight_only and args.reuse_account_info:
            raise RuntimeError("预检模式不能使用 --reuse-account-info")
        if args.reuse_account_info:
            if not args.account_info_json:
                raise RuntimeError("--reuse-account-info 必须同时提供 --account-info-json")
            account_info = read_cached_account_info(Path(args.account_info_json).expanduser().resolve(), platform)
        else:
            # 预检使用真实账号接口，同时确认凭证、连接和必要账号字段都可用。
            account_payload = fetch_account_payload(
                platform, base_url, token, account_url, raw_dump_dir=raw_dump_dir
            )
            account_info = normalize_account_info(platform, account_payload, account_url)
        validate_account_info(account_info)
        if args.preflight_only:
            account_info_path = ""
            if args.account_info_json:
                resolved_account_info = Path(args.account_info_json).expanduser().resolve()
                write_json(resolved_account_info, account_info)
                account_info_path = str(resolved_account_info)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "platform": platform,
                        "account_id": account_info["账号ID"],
                        "account_name": account_info["账号名"],
                        "account_url": account_info["主页链接"],
                        "base_url": base_url,
                        "account_info_json": account_info_path,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if not args.account_info_json or not args.raw_list_json:
            raise RuntimeError("非预检模式必须提供 --account-info-json 和 --raw-list-json")
        account_info_path = Path(args.account_info_json).expanduser().resolve()
        avatar_source = account_info.get("头像原链接", "")
        avatar_path = ""
        avatar_status = "跳过"
        avatar_hint = ""
        if avatar_source:
            avatar_base = (
                Path(args.avatar_output).expanduser().resolve()
                if args.avatar_output
                else account_info_path.with_name(f"{account_info_path.stem}-头像")
            )
            try:
                avatar_path = str(download_avatar(avatar_source, avatar_base))
                avatar_status = "成功"
            except Exception as exc:  # noqa: BLE001
                code = permission_error_code(exc, "network")
                if code:
                    raise RuntimeError(code) from exc
                avatar_status = "失败"
                avatar_hint = f"头像获取失败，已跳过且不影响账号数据获取: {exc}"
        else:
            avatar_hint = "平台未返回头像链接，已跳过且不影响账号数据获取"
        account_info.update(
            {
                "头像获取状态": avatar_status,
                "头像临时文件路径": avatar_path,
                "头像获取提示": avatar_hint,
            }
        )
        item_limit = max(args.max_items, 0)
        try:
            all_raw_items, pagination_flag, pagination_reason = fetch_all_list_pages(
                platform,
                base_url,
                token,
                account_url,
                account_info["账号ID"],
                raw_dump_dir=raw_dump_dir,
                max_items=item_limit,
            )
        except RuntimeError as exc:
            normalized_items = []
            list_status = "失败"
            stop_reason = f"JustOneAPI 内容列表获取失败: {exc}"
            explicitly_incomplete = True
            pagination_flag = None
            empty_reason = ""
        else:
            retained_raw_items, _ = filter_public_content_items(all_raw_items)
            normalized_items = [normalize_content_item(platform, account_info, item) for item in retained_raw_items]
            normalized_items = deduplicate_normalized_items(
                [item for item in normalized_items if item.get("标题") or item.get("对外页面链接")]
            )
            locally_truncated = item_limit > 0 and len(normalized_items) > item_limit
            if locally_truncated:
                normalized_items = normalized_items[:item_limit]
            explicitly_incomplete = locally_truncated or pagination_flag is not False
            verified_empty = bool(not retained_raw_items and pagination_flag is False)
            if verified_empty:
                list_status = "成功"
                stop_reason = "JustOneAPI 已返回完整列表，当前账号没有内容"
                empty_reason = "no_public_content"
            elif not normalized_items:
                list_status = "失败"
                stop_reason = "JustOneAPI 未返回有效内容列表"
                empty_reason = ""
            elif explicitly_incomplete:
                list_status = "部分成功"
                empty_reason = ""
                if locally_truncated:
                    stop_reason = "达到本轮内容数量限制"
                else:
                    stop_reason = pagination_reason
            else:
                list_status = "成功"
                stop_reason = pagination_reason
                empty_reason = ""
        raw_list_result = {
            "状态": "成功" if list_status != "失败" else "失败",
            "基础数据获取方式": "JustOneAPI",
            "平台": account_info["平台"],
            "账号ID": account_info["账号ID"],
            "账号名": account_info["账号名"],
            "账号内容原始列表": normalized_items,
            "公开状态统计": {
                status: sum(1 for item in normalized_items if item.get("公开状态") == status)
                for status in ("公开", "草稿", "私密", "其他", "未知")
            },
            "内容列表获取状态": list_status,
            "内容列表停止原因": stop_reason,
            "内容列表停止位置": "JustOneAPI",
            "内容列表是否可能未抓全": "是" if explicitly_incomplete or pagination_flag is None else "否",
            "内容列表为空原因": empty_reason,
            "主页长截图状态": "跳过",
            "主页长截图路径": "",
            "主页长截图结果JSON路径": "",
            "主页长截图失败原因": "JustOneAPI 路径不执行主页长截图",
            "失败原因": stop_reason if list_status == "失败" else "",
            "justoneapi_原始响应目录": raw_dump_dir_display,
        }
        write_json(account_info_path, account_info)
        write_json(Path(args.raw_list_json).expanduser().resolve(), raw_list_result)
        print(
            json.dumps(
                {
                    "ok": True,
                    "platform": platform,
                    "account_id": account_info["账号ID"],
                    "account_name": account_info["账号名"],
                    "content_count": len(normalized_items),
                    "content_list_status": list_status,
                    "avatar_status": avatar_status,
                    "avatar_file": avatar_path,
                    "warnings": [warning for warning in [avatar_hint, stop_reason if list_status == "失败" else ""] if warning],
                    "account_info_json": str(Path(args.account_info_json).expanduser().resolve()),
                    "raw_list_json": str(Path(args.raw_list_json).expanduser().resolve()),
                    "raw_response_dir": raw_dump_dir_display,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        error = {"ok": False, "error": str(exc)}
        error_code = exc.code if isinstance(exc, AccountUrlError) else permission_error_code(exc, "filesystem")
        if error_code:
            error["error_code"] = error_code
        if isinstance(exc, AccountUrlError):
            error.update(exc.details)
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

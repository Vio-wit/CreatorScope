from __future__ import annotations

from typing import Any


TYPE_LABELS = {
    "blogger_list_sample": "博主列表",
    "creator_profile_sample": "博主资料与报价",
    "creator_content_performance": "内容表现",
    "fan_analysis": "粉丝画像",
    "note_detail": "笔记案例",
}

# Field metadata is based on the retained real, anonymized Pgy sample.
# The unit is the unit shown/represented by the platform; unknown periods stay unknown.
FIELD_CATALOG: dict[str, dict[str, str]] = {
    "粉丝数": {"group": "创作者", "unit": "人", "period": "采集时页面值"},
    "获赞与收藏": {"group": "创作者", "unit": "次", "period": "采集时页面值"},
    "发布笔记数": {"group": "创作者", "unit": "篇", "period": "采集时页面值"},
    "内容类目": {"group": "创作者", "unit": "类目标签", "period": "采集时页面值"},
    "全部报价": {"group": "商业报价", "unit": "人民币元", "period": "采集时页面值"},
    "图文笔记一口价": {"group": "商业报价", "unit": "人民币元", "period": "采集时页面值"},
    "视频笔记一口价": {"group": "商业报价", "unit": "人民币元", "period": "采集时页面值"},
    "关注量": {"group": "笔记案例", "unit": "人", "period": "采集时页面值"},
    "新增关注": {"group": "笔记案例", "unit": "人", "period": "采集时页面值"},
    "阅读中位数（日常）": {"group": "列表表现", "unit": "次", "period": "日常口径"},
    "互动中位数（日常）": {"group": "列表表现", "unit": "次", "period": "日常口径"},
    "曝光中位数": {"group": "内容表现", "unit": "次", "period": "页面显示周期"},
    "阅读中位数": {"group": "内容表现", "unit": "次", "period": "页面显示周期"},
    "互动中位数": {"group": "内容表现", "unit": "次", "period": "页面显示周期"},
    "中位点赞量": {"group": "内容表现", "unit": "次", "period": "页面显示周期"},
    "中位收藏量": {"group": "内容表现", "unit": "次", "period": "页面显示周期"},
    "中位评论量": {"group": "内容表现", "unit": "次", "period": "页面显示周期"},
    "中位分享量": {"group": "内容表现", "unit": "次", "period": "页面显示周期"},
    "中位关注量": {"group": "内容表现", "unit": "人", "period": "页面显示周期"},
    "互动率": {"group": "内容表现", "unit": "%", "period": "页面显示周期"},
    "视频完播率": {"group": "内容表现", "unit": "%", "period": "页面显示周期"},
    "图文3秒阅读率": {"group": "内容表现", "unit": "%", "period": "页面显示周期"},
    "粉丝量变化幅度": {"group": "粉丝画像", "unit": "%", "period": "页面显示周期"},
    "活跃粉丝占比": {"group": "粉丝画像", "unit": "%", "period": "页面显示周期"},
    "阅读粉丝占比": {"group": "粉丝画像", "unit": "%", "period": "页面显示周期"},
    "互动粉丝占比": {"group": "粉丝画像", "unit": "%", "period": "页面显示周期"},
    "下单粉丝占比": {"group": "粉丝画像", "unit": "%", "period": "页面显示周期"},
    "发布时间": {"group": "笔记案例", "unit": "本地时间", "period": "笔记发布时间"},
    "曝光量": {"group": "笔记案例", "unit": "次", "period": "采集时页面值"},
    "阅读量": {"group": "笔记案例", "unit": "次", "period": "采集时页面值"},
    "点赞量": {"group": "笔记案例", "unit": "次", "period": "采集时页面值"},
    "收藏量": {"group": "笔记案例", "unit": "次", "period": "采集时页面值"},
    "评论量": {"group": "笔记案例", "unit": "次", "period": "采集时页面值"},
    "分享量": {"group": "笔记案例", "unit": "次", "period": "采集时页面值"},
    "视频时长": {"group": "笔记案例", "unit": "秒", "period": "采集时页面值"},
    "笔记标题": {"group": "笔记文本", "unit": "文本", "period": "笔记发布时"},
    "笔记正文": {"group": "笔记文本", "unit": "文本", "period": "笔记发布时"},
    "内容标签": {"group": "笔记文本", "unit": "标签", "period": "笔记发布时"},
}

TYPE_PERIODS = {
    "blogger_list_sample": "列表页（日常统计口径）",
    "creator_profile_sample": "采集时资料快照；页面未标明统计周期",
    "creator_content_performance": "页面显示周期；若样本标注则保留原值",
    "fan_analysis": "页面显示周期；若样本标注则保留原值",
    "note_detail": "采集时页面值；发布时间单独记录",
}


def type_label(record_type: str) -> str:
    return TYPE_LABELS.get(record_type, record_type)


def period_for(record_type: str, record: dict[str, Any], field_name: str = "") -> str:
    displayed = record.get("period_displayed") or record.get("period_selector_displayed")
    if displayed:
        return str(displayed)
    field_period = FIELD_CATALOG.get(field_name, {}).get("period")
    if field_period and field_period not in {"页面显示周期", "采集时页面值"}:
        return field_period
    return TYPE_PERIODS.get(record_type, "页面未标明")


def field_dictionary(observed_fields: set[str] | None = None) -> list[dict[str, Any]]:
    observed = observed_fields or set()
    return [
        {
            "name": name,
            "group": spec["group"],
            "unit": spec["unit"],
            "period": spec["period"],
            "observed": name in observed,
        }
        for name, spec in FIELD_CATALOG.items()
    ]

//! 中文一句话解析 + 时区工具（与 legacy/taskboard.py 的 parse/bucket/due_label 对齐）。
use chrono::{DateTime, Datelike, FixedOffset, TimeZone, Timelike};
use fancy_regex::Regex;
use std::sync::OnceLock;

/// Asia/Shanghai（无夏令时，固定 UTC+8）。用 FixedOffset 而不是 chrono-tz：
/// 语义完全等价，且不依赖外部 tzdata 数据库。
pub fn shanghai_offset() -> FixedOffset {
    FixedOffset::east_opt(8 * 3600).expect("UTC+8 恒定合法")
}

pub fn now_shanghai() -> DateTime<FixedOffset> {
    chrono::Utc::now().with_timezone(&shanghai_offset())
}

/// 解析 Vikunja 的 ISO 时间串（支持 Z 结尾）。
pub fn parse_due(s: &str) -> Option<DateTime<FixedOffset>> {
    DateTime::parse_from_rfc3339(s)
        .ok()
        .map(|d| d.with_timezone(&shanghai_offset()))
}

static TIME_RE: OnceLock<Regex> = OnceLock::new();
fn time_re() -> &'static Regex {
    TIME_RE.get_or_init(|| {
        // 注意：模式必须写成单行字符串。多行 raw string 会把缩进和换行当成
        // 正则的一部分，导致几乎任何输入都匹配不上（曾踩过）。
        Regex::new(concat!(
            r"(?:(?P<prefix>今天|明天|后天|大后天|下周|本周|这周|周|星期)(?P<wd>[一二三四五六日天])?)?",
            r"\s*(?:(?P<mon>\d{1,2})[月/](?P<day>\d{1,2})[日号]?|(?P<mon2>\d{1,2})-(?P<day2>\d{1,2})(?![\d:：]))?",
            r"\s*(?:(?P<rel>\d+)\s*天后)?",
            r"\s*(?P<period>上午|早上|早晨|中午|下午|傍晚|晚上)?",
            r"\s*(?:(?P<hh>\d{1,2})(?:(?P<half>点半)|(?P<sep>[:：点时])(?:(?P<mm>\d{1,2})\s*分?)?)?",
            r"(?:\s*[-~—–至到]\s*\d{1,2}(?:(?:[:：点时])\d{1,2}\s*分?)?)?)?",
        ))
        .unwrap()
    })
}

fn weekday_of(c: char) -> Option<u32> {
    match c {
        '一' => Some(0),
        '二' => Some(1),
        '三' => Some(2),
        '四' => Some(3),
        '五' => Some(4),
        '六' => Some(5),
        '日' | '天' => Some(6),
        _ => None,
    }
}

fn period_add(p: Option<&str>) -> u32 {
    match p {
        Some("下午") | Some("傍晚") | Some("晚上") | Some("夜里") | Some("中午") => 12,
        _ => 0,
    }
}

/// 「明天 9点 交作业 !高」→ (标题, 截止时间, 优先级)。解析不出日期就返回 None。
pub fn parse(
    text: &str,
    now: DateTime<FixedOffset>,
) -> (String, Option<DateTime<FixedOffset>>, i32) {
    let mut priority = 2;
    let mut text = text.to_string();
    if text.contains("!高") {
        priority = 3;
        text = text.replace("!高", "");
    } else if text.contains("!低") {
        priority = 1;
        text = text.replace("!低", "");
    }
    let text = text.trim().to_string();

    let caps = match time_re().captures(&text) {
        Ok(Some(c)) => c,
        _ => return (text, None, priority),
    };
    let g = |name: &str| caps.name(name).map(|m| m.as_str());
    let matched = ["prefix", "mon", "mon2", "rel", "hh"]
        .iter()
        .any(|k| caps.name(k).is_some());
    if !matched {
        return (text, None, priority);
    }

    let today = now.date_naive();
    let mut day = None::<chrono::NaiveDate>;
    let prefix = g("prefix").unwrap_or("");
    let wd = g("wd").and_then(|s| s.chars().next()).and_then(weekday_of);
    if prefix == "今天" || prefix == "明天" || prefix == "后天" || prefix == "大后天" {
        let add: i64 = match prefix {
            "明天" => 1,
            "后天" => 2,
            "大后天" => 3,
            _ => 0,
        };
        day = today.checked_add_signed(chrono::Duration::days(add));
    } else if prefix == "下周" || prefix == "本周" || prefix == "这周" || prefix == "周" || prefix == "星期"
    {
        if let Some(target) = wd {
            let cur = today.weekday().num_days_from_monday();
            let delta = if prefix == "下周" {
                (7 - cur) + target
            } else {
                (target + 7 - cur) % 7
            };
            day = today.checked_add_signed(chrono::Duration::days(delta as i64));
        }
    } else if g("mon").is_some() || g("mon2").is_some() {
        let mon: u32 = g("mon").or_else(|| g("mon2")).unwrap().parse().unwrap();
        let dd: u32 = g("day")
            .or_else(|| g("day2"))
            .and_then(|s| s.parse::<u32>().ok())
            .unwrap_or(1);
        let year = today.year() + if mon < today.month() - 6 { 1 } else { 0 };
        match chrono::NaiveDate::from_ymd_opt(year, mon, dd) {
            Some(d) => day = Some(d),
            None => return (text, None, priority),
        }
    } else if let Some(rel) = g("rel") {
        let n: i64 = rel.parse().unwrap_or(0);
        day = today.checked_add_signed(chrono::Duration::days(n));
    }
    let day = match day {
        Some(d) => d,
        None => return (text, None, priority),
    };

    let (hour, minute) = if let Some(hh) = g("hh") {
        let mut h: i32 = hh.parse::<u32>().unwrap_or(0) as i32 + period_add(g("period")) as i32;
        let m: u32 = if caps.name("half").is_some() {
            30
        } else {
            g("mm").and_then(|s| s.parse::<u32>().ok()).unwrap_or(0)
        };
        if h >= 24 {
            h -= 12;
        }
        (h as u32, m)
    } else {
        (23, 59)
    };
    let tz = now.offset();
    let due = tz
        .with_ymd_and_hms(day.year(), day.month(), day.day(), hour, minute, 0)
        .earliest();
    // 整段匹配的跨度（与 Python 的 m.start()/m.end() 一致）
    let whole = caps.get(0).unwrap();
    let mut title = format!("{} {}", &text[..whole.start()], &text[whole.end()..]);
    title = title
        .trim_matches(|c| " ，,、-~—".contains(c))
        .to_string();
    if title.is_empty() {
        title = text.clone();
    }
    (title, due, priority)
}

/// 三列分桶：≤今天 → today；≤今天+7天 → soon；否则（含无日期）→ later。
pub fn bucket(
    due: Option<DateTime<FixedOffset>>,
    now: DateTime<FixedOffset>,
) -> &'static str {
    match due {
        None => "later",
        Some(d) => {
            let dd = d.date_naive();
            let today = now.date_naive();
            if dd <= today {
                "today"
            } else if dd <= today.checked_add_signed(chrono::Duration::days(7)).unwrap() {
                "soon"
            } else {
                "later"
            }
        }
    }
}

/// (时间右栏文本, 是否逾期)
pub fn due_label(
    due: Option<DateTime<FixedOffset>>,
    now: DateTime<FixedOffset>,
) -> (String, bool) {
    let Some(d) = due else { return (String::new(), false) };
    let delta = (d.date_naive() - now.date_naive()).num_days();
    let hm = if (d.hour(), d.minute()) == (23, 59) {
        String::new()
    } else {
        format!("{:02}:{:02}", d.hour(), d.minute())
    };
    if delta < 0 {
        (format!("逾期{}天 {}", -delta, hm).trim_end().to_string(), true)
    } else if delta == 0 {
        (hm.clone(), false)
    } else {
        (format!("{}/{} {}", d.month(), d.day(), hm).trim_end().to_string(), false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn now() -> DateTime<FixedOffset> {
        let tz = shanghai_offset();
        tz.with_ymd_and_hms(2026, 11, 19, 15, 30, 0).earliest().unwrap() // 周四下午
    }

    fn mdhm(d: &Option<DateTime<FixedOffset>>) -> Option<(u32, u32, u32, u32)> {
        d.as_ref()
            .map(|d| (d.month(), d.day(), d.hour(), d.minute()))
    }

    // 11 条用例抄自 legacy/tests/test_parse.py
    #[test]
    fn parse_cases() {
        let cases: Vec<(&str, Option<(u32, u32, u32, u32)>, i32, &str)> = vec![
            ("周五 17:00 交实验报告", Some((11, 20, 17, 0)), 2, "交实验报告"),
            ("明天上午9点半 组会", Some((11, 20, 9, 30)), 2, "组会"),
            ("下周一 8点 交作业", Some((11, 23, 8, 0)), 2, "交作业"),
            ("11月28日 09:30 实验室", Some((11, 28, 9, 30)), 2, "实验室"),
            ("11-25 20:30 组会", Some((11, 25, 20, 30)), 2, "组会"),
            ("3天后 交材料", Some((11, 22, 23, 59)), 2, "交材料"),
            ("周日 19:30-21:30 开发", Some((11, 22, 19, 30)), 2, "开发"),
            ("今天晚上8点 复习高数", Some((11, 19, 20, 0)), 2, "复习高数"),
            ("明天 9点 交作业 !高", Some((11, 20, 9, 0)), 3, "交作业"),
            ("周五 交报告", Some((11, 20, 23, 59)), 2, "交报告"),
            ("交水电费", None, 2, "交水电费"),
        ];
        let mut bad = 0;
        for (raw, want, wp, wtitle) in cases {
            let (title, due, prio) = parse(raw, now());
            let got = mdhm(&due);
            let ok = got == want && prio == wp && title == wtitle;
            if !ok {
                bad += 1;
                println!("FAIL {raw:?} -> {got:?} p{prio} {title:?} (期望 {want:?} p{wp} {wtitle:?})");
            }
        }
        assert_eq!(bad, 0, "{}/11 未通过", 11 - bad);
    }

    #[test]
    fn bucket_and_label() {
        let tz = shanghai_offset();
        let now = tz.with_ymd_and_hms(2026, 11, 19, 15, 30, 0).earliest().unwrap();
        let d = |y: i32, m: u32, dd: u32, h: u32| {
            tz.with_ymd_and_hms(y, m, dd, h, 0, 0).earliest()
        };
        assert_eq!(bucket(d(2026, 11, 19, 8, ), now), "today");
        assert_eq!(bucket(d(2026, 11, 18, 8), now), "today"); // 逾期也算今天
        assert_eq!(bucket(d(2026, 11, 26, 8), now), "soon");
        assert_eq!(bucket(d(2026, 11, 27, 8), now), "later");
        assert_eq!(bucket(None, now), "later");
        let (label, od) = due_label(d(2026, 11, 17, 20, ), now);
        assert!(od && label == "逾期2天 20:00");
        let (label2, od2) = due_label(d(2026, 11, 21, 9, ), now);
        assert!(!od2 && label2 == "11/21 09:00");
        let (label3, _) = due_label(None, now);
        assert!(label3.is_empty());
    }
}

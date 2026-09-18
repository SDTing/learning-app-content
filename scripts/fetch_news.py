import asyncio
import datetime
import json
import os
import re
import sys

import edge_tts
import feedparser
import requests

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

RSS_FEEDS = [
    "https://learningenglish.voanews.com/api/zyritevem",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.npr.org/rss/rss.php?id=1001",
]

VOICE = "en-US-AriaNeural"
MAX_SENTENCES = 5
KEEP_DAYS = 7

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COURSES_DIR = os.path.join(ROOT, "courses")
AUDIO_DIR = os.path.join(ROOT, "audio")
MANIFEST_PATH = os.path.join(ROOT, "manifest.json")


def fetch_titles():
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            titles = []
            for e in feed.entries:
                t = e.get("title", "").strip()
                if t and 10 < len(t) < 120:
                    titles.append(t)
                if len(titles) >= MAX_SENTENCES:
                    break
            if titles:
                print(f"使用新闻源: {url}，获取 {len(titles)} 条")
                return titles
        except Exception as exc:
            print(f"新闻源 {url} 失败: {exc}")
    raise RuntimeError("所有新闻源均失败")


def translate_titles(titles):
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
    prompt = (
        "把以下英文新闻标题翻译成自然流畅的中文。"
        "只输出一个 JSON 数组，格式为 "
        '[{"zh": "中文翻译"}, ...]，不要输出其他内容：\n' + lines
    )
    resp = requests.post(
        DEEPSEEK_URL,
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 1000,
        },
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    m = re.search(r"\[.*\]", content, re.S)
    if not m:
        raise RuntimeError(f"无法解析翻译结果: {content}")
    arr = json.loads(m.group(0))
    translations = [item["zh"] for item in arr]
    if len(translations) != len(titles):
        raise RuntimeError("翻译条数与原文不符")
    return translations


async def generate_audio(course_id, index, text):
    filename = f"{course_id}_{index:02d}.mp3"
    path = os.path.join(AUDIO_DIR, filename)
    for attempt in range(3):
        try:
            await edge_tts.Communicate(text, VOICE).save(path)
            return f"audio/{filename}"
        except Exception as exc:
            print(f"音频生成重试 {attempt + 1}: {exc}")
            await asyncio.sleep(2)
    raise RuntimeError(f"音频生成失败: {text}")


async def build_course():
    today = datetime.date.today()
    course_id = f"news_{today.strftime('%Y%m%d')}"

    titles = fetch_titles()
    translations = translate_titles(titles)

    sentences = []
    for i, (t, zh) in enumerate(zip(titles, translations)):
        audio = await generate_audio(course_id, i, t)
        sentences.append({"text": t, "translation": zh, "audio": audio})
        print(f"  {t[:40]} -> {zh[:20]}")

    course = {
        "id": course_id,
        "title": f"时事：{today.strftime('%m月%d日')}",
        "language": "en",
        "level": "B1",
        "description": f"{today.strftime('%Y-%m-%d')} 双语时事新闻",
        "sentences": sentences,
    }
    with open(os.path.join(COURSES_DIR, f"{course_id}.json"), "w", encoding="utf-8") as f:
        json.dump(course, f, ensure_ascii=False, indent=2)
    print(f"wrote courses/{course_id}.json")
    return course


def update_manifest(new_course):
    manifest = {"version": 1, "courses": []}
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            manifest = json.load(f)

    entry = {
        "id": new_course["id"],
        "title": new_course["title"],
        "language": new_course["language"],
        "level": new_course["level"],
        "description": new_course["description"],
        "file": f"courses/{new_course['id']}.json",
    }

    def is_daily_news(cid):
        return re.match(r"^news_\d{8}$", cid) is not None

    def parse_date(cid):
        return datetime.date(int(cid[5:9]), int(cid[9:11]), int(cid[11:13]))

    cutoff = datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)

    kept = [c for c in manifest["courses"] if not is_daily_news(c["id"])]
    daily = [c for c in manifest["courses"] if is_daily_news(c["id"])]
    daily = [c for c in daily if c["id"] != entry["id"] and parse_date(c["id"]) >= cutoff]
    daily.append(entry)
    daily.sort(key=lambda c: c["id"], reverse=True)

    manifest["courses"] = kept + daily
    manifest["version"] += 1

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"wrote manifest.json (version {manifest['version']})")
    return manifest


def cleanup_old():
    cutoff = datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)
    for f in os.listdir(COURSES_DIR):
        m = re.match(r"^news_(\d{8})\.json$", f)
        if not m:
            continue
        d = datetime.date(int(m.group(1)[0:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8]))
        if d < cutoff:
            os.remove(os.path.join(COURSES_DIR, f))
            print(f"删除过期课程 {f}")


async def main():
    if not DEEPSEEK_API_KEY:
        print("缺少 DEEPSEEK_API_KEY")
        sys.exit(1)
    os.makedirs(COURSES_DIR, exist_ok=True)
    os.makedirs(AUDIO_DIR, exist_ok=True)
    course = await build_course()
    update_manifest(course)
    cleanup_old()
    print("完成")


if __name__ == "__main__":
    asyncio.run(main())

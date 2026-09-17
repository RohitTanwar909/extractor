from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import json
import os
import re

app = Flask(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

VIDEO_EXTENSIONS = (".mp4", ".webm", ".m4v", ".mov", ".m3u8", ".mpd")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")


def clean(value):
    return value.strip() if value else None


def meta(soup, *names):
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return clean(tag["content"])
    return None


def absolute(url, page_url):
    if not url:
        return None
    return urljoin(page_url, url.strip())


def get_title(soup):
    return meta(soup, "og:title", "twitter:title") or clean(soup.title.string if soup.title else None)


def get_description(soup):
    return meta(soup, "og:description", "twitter:description", "description")


def get_thumbnail(soup, page_url):
    image = meta(soup, "og:image", "twitter:image", "twitter:image:src")
    return absolute(image, page_url) if image else None


def get_categories(soup):
    categories = []
    for a in soup.find_all("a", href=True):
        text = clean(a.get_text(" ", strip=True))
        href = a["href"].lower()
        if text and ("/category/" in href or "/categories/" in href) and text not in categories:
            categories.append(text)
    return categories


def get_tags(soup):
    tags = []
    for a in soup.find_all("a", href=True):
        text = clean(a.get_text(" ", strip=True))
        href = a["href"].lower()
        if text and ("/tag/" in href or "/tags/" in href) and text not in tags:
            tags.append(text)
    return tags


def looks_like_video(url, mime=""):
    if not url:
        return False
    value = url.lower().split("?", 1)[0]
    mime = (mime or "").lower()
    if any(value.endswith(ext) for ext in IMAGE_EXTENSIONS):
        return False
    return "video/" in mime or any(ext in value for ext in VIDEO_EXTENSIONS)


def add_video(videos, url, page_url, thumbnail=None, title=None, description=None,
              category=None, categories=None, tags=None, source="html", mime=None):
    if not url:
        return
    url = absolute(url, page_url)
    if not url or not looks_like_video(url, mime):
        return

    item = {
        "video_url": url,
        "thumbnail": thumbnail,
        "title": title,
        "description": description,
        "category": category,
        "categories": categories or [],
        "tags": tags or [],
        "source": source,
        "mime_type": mime,
    }

    for old in videos:
        if old["video_url"] == url:
            # Keep the more informative source/mime when a URL is found twice.
            if old.get("source") == "html" and source != "html":
                old["source"] = source
            if not old.get("mime_type") and mime:
                old["mime_type"] = mime
            return
    videos.append(item)


def extract_jsonld(soup, page_url, videos, page_title, page_description, page_thumbnail,
                   category, categories, tags):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            objects += data["@graph"]

        for obj in objects:
            if not isinstance(obj, dict):
                continue
            obj_type = obj.get("@type")
            types = obj_type if isinstance(obj_type, list) else [obj_type]
            if "VideoObject" not in types:
                continue

            video_url = obj.get("contentUrl") or obj.get("embedUrl") or obj.get("url")
            thumbnail = obj.get("thumbnailUrl") or page_thumbnail
            if isinstance(thumbnail, list):
                thumbnail = thumbnail[0] if thumbnail else None

            add_video(
                videos, video_url, page_url,
                absolute(thumbnail, page_url),
                obj.get("name") or page_title,
                obj.get("description") or page_description,
                category, categories, tags, source="jsonld"
            )


def extract_from_html(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    title = get_title(soup)
    description = get_description(soup)
    thumbnail = get_thumbnail(soup, page_url)
    categories = get_categories(soup)
    tags = get_tags(soup)
    category = categories[0] if categories else None
    videos = []

    for video in soup.find_all("video"):
        poster = absolute(video.get("poster"), page_url)
        video_title = clean(video.get("title")) or title
        for source in video.find_all("source"):
            add_video(videos, source.get("src"), page_url, poster or thumbnail,
                      video_title, description, category, categories, tags,
                      source="html", mime=source.get("type"))
        add_video(videos, video.get("src"), page_url, poster or thumbnail,
                  video_title, description, category, categories, tags, source="html")

    for source in soup.find_all("source"):
        src = source.get("src")
        if looks_like_video(src, source.get("type")):
            add_video(videos, src, page_url, thumbnail, title, description,
                      category, categories, tags, source="html", mime=source.get("type"))

    for key in ("og:video", "og:video:url", "og:video:secure_url"):
        value = meta(soup, key)
        if value:
            add_video(videos, value, page_url, thumbnail, title, description,
                      category, categories, tags, source="og")

    extract_jsonld(soup, page_url, videos, title, description, thumbnail,
                   category, categories, tags)

    attributes = ("data-video", "data-video-url", "data-video-src", "data-src",
                  "data-file", "data-video-file", "data-source")
    for element in soup.find_all():
        for attr in attributes:
            value = element.get(attr)
            if value and looks_like_video(value):
                add_video(videos, value, page_url, thumbnail, title, description,
                          category, categories, tags, source="data-attribute")

    # Also scan inline scripts for obvious MP4/HLS/DASH URLs.
    for script in soup.find_all("script"):
        text = script.string or script.get_text(" ", strip=False) or ""
        for match in re.findall(r'https?[^\"\'<>\\\s]+(?:\\/|/)[^\"\'<>\\\s]+', text):
            candidate = match.replace("\\/", "/").replace("\\u0026", "&")
            if looks_like_video(candidate):
                add_video(videos, candidate, page_url, thumbnail, title, description,
                          category, categories, tags, source="inline-script")

    return {
        "title": title,
        "description": description,
        "thumbnail": thumbnail,
        "category": category,
        "categories": categories,
        "tags": tags,
        "videos": videos,
    }


def extract_with_playwright(page_url, base_result):
    """Render JS and capture media network requests.

    This only observes resources requested by the public page. It does not bypass
    authentication, DRM, paywalls, or anti-bot controls.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except ImportError:
        return [], "playwright_not_installed"

    captured = []
    seen = set()

    def add_response(response):
        try:
            request_url = response.url
            headers = response.headers
            content_type = headers.get("content-type", "")
            resource_type = response.request.resource_type
            if resource_type in ("media", "manifest") or looks_like_video(request_url, content_type):
                key = request_url.split("#", 1)[0]
                if key not in seen:
                    seen.add(key)
                    captured.append({
                        "url": key,
                        "mime": content_type.split(";", 1)[0].strip(),
                        "resource_type": resource_type,
                    })
        except Exception:
            pass

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="en-US",
                viewport={"width": 1365, "height": 900},
                extra_http_headers={"Accept-Language": HEADERS["Accept-Language"]},
            )
            page = context.new_page()
            page.on("response", add_response)
            page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightTimeoutError:
                pass

            # Give lazy video players a short opportunity to initialize.
            page.wait_for_timeout(2500)

            # Inspect the rendered DOM too. Some players insert <source>/<video>
            # only after JavaScript runs.
            rendered_html = page.content()
            rendered = extract_from_html(rendered_html, page.url)
            base_result["title"] = base_result.get("title") or rendered.get("title")
            base_result["description"] = base_result.get("description") or rendered.get("description")
            base_result["thumbnail"] = base_result.get("thumbnail") or rendered.get("thumbnail")
            for item in rendered["videos"]:
                if not any(v["video_url"] == item["video_url"] for v in base_result["videos"]):
                    base_result["videos"].append(item)

            browser.close()
        return captured, None
    except Exception as exc:
        return [], f"playwright_error: {type(exc).__name__}: {exc}"


def fetch_html(page_url):
    response = requests.get(page_url, headers=HEADERS, timeout=30, allow_redirects=True)
    if response.status_code == 403:
        return None, {
            "success": False,
            "error": "Target website returned HTTP 403",
            "message": "The website refused the direct automated request. Browser rendering may still work if the page is publicly accessible."
        }, 403
    response.raise_for_status()
    return response.text, None, 200


def extract_page(page_url, use_browser=True):
    html, error, status = fetch_html(page_url)

    if html is not None:
        result = extract_from_html(html, page_url)
    else:
        result = {
            "title": None, "description": None, "thumbnail": None,
            "category": None, "categories": [], "tags": [], "videos": []
        }

    browser_error = None
    captured = []

    # Browser extraction is the important fallback for JS-generated players.
    if use_browser:
        captured, browser_error = extract_with_playwright(page_url, result)
        for media in captured:
            add_video(
                result["videos"], media["url"], page_url,
                result["thumbnail"], result["title"], result["description"],
                result["category"], result["categories"], result["tags"],
                source="network", mime=media["mime"]
            )

    if html is None and not result["videos"]:
        if browser_error:
            error["browser_error"] = browser_error
        return error, status

    return {
        "success": True,
        "source": page_url,
        "page": {
            "title": result["title"],
            "description": result["description"],
            "thumbnail": result["thumbnail"],
            "category": result["category"],
            "categories": result["categories"],
            "tags": result["tags"],
        },
        "count": len(result["videos"]),
        "videos": result["videos"],
        "extraction": {
            "html": html is not None,
            "browser": use_browser,
            "network_requests": len(captured),
            "browser_error": browser_error,
        },
    }, 200


@app.route("/")
def home():
    return open(os.path.join(os.path.dirname(__file__), "index.html"), encoding="utf-8").read()


@app.route("/api/extract", methods=["GET"])
def api_extract():
    url = request.args.get("url", "").strip()
    browser = request.args.get("browser", "1").lower() not in ("0", "false", "no")

    if not url:
        return jsonify({"success": False, "error": "Missing url parameter"}), 400
    if not url.startswith(("http://", "https://")):
        return jsonify({"success": False, "error": "Invalid URL"}), 400

    try:
        result, status = extract_page(url, use_browser=browser)
        return jsonify(result), status
    except requests.Timeout:
        return jsonify({"success": False, "error": "Target website timed out"}), 504
    except requests.RequestException as e:
        return jsonify({"success": False, "error": "Unable to fetch webpage", "details": str(e)}), 502
    except Exception as e:
        return jsonify({"success": False, "error": "Internal extractor error", "details": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "video-metadata-extractor", "browser_extraction": True}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

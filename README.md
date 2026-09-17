# Video URL Extractor

Flask API that extracts publicly accessible video sources using two layers:

1. **HTML extraction** — `<video>`, `<source>`, Open Graph, JSON-LD, data attributes and inline-script URLs.
2. **Browser/network extraction** — Playwright renders JavaScript and observes media/manifest requests made by the page.

## Install

```bash
pip install -r requirements.txt
playwright install chromium
```

For Linux servers, you may need:

```bash
playwright install --with-deps chromium
```

## Run

```bash
python app.py
```

or:

```bash
gunicorn -w 1 -b 0.0.0.0:5000 app:app
```

## API

```text
GET /api/extract?url=https://example.com/video-page
```

Browser extraction is enabled by default. Disable it for a fast HTML-only scan:

```text
GET /api/extract?browser=0&url=https://example.com/video-page
```

The API returns each source with `source` values such as `html`, `jsonld`, `og`, `data-attribute`, `inline-script`, or `network`.

## Limitations

Browser extraction observes resources requested by a publicly accessible page. It does not bypass authentication, DRM, paywalls, or anti-bot controls. Some media URLs are short-lived, signed, or protected and therefore should not be treated as permanent URLs.

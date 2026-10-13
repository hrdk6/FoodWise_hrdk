"""Pictures of dishes.

Each dish gets a photo automatically: first the lead image of the dish's own Wikipedia article,
otherwise the best-matching freely licensed photo on Wikimedia Commons (scored on how well the
file title matches the dish name). Only free licences are used, and the photographer and
licence are stored and shown as the credit. A kitchen can always replace it with a photo of its
own food, or pick a different one from the candidates.

Stored images are re-encoded: square-cropped, 480 px, JPEG, with all metadata (including any
GPS position in a phone photo) removed.
"""

from __future__ import annotations

import hashlib
import html
import io
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from PIL import Image, ImageOps

from foodwise.config import get_settings
from foodwise.core import telemetry
from foodwise.core.errors import Unprocessable
from foodwise.core.storage import storage, tenant_key
from foodwise.db.models import Dish

log = logging.getLogger("foodwise.images")

COMMONS = "https://commons.wikimedia.org/w/api.php"
WIKIPEDIA = "https://en.wikipedia.org/w/api.php"
SIZE = 480
MAX_BYTES = 8 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 60_000_000  # refuse decompression bombs

FREE_LICENCE = re.compile(r"^(cc0|cc[ -]by|public domain|pd\b|attribution|gfdl|fal\b|free art)", re.I)
FOOD_WORDS = re.compile(r"dish|food|curry|bread|rice|snack|dessert|sweet|beverage|drink|tea\b|pastry|flatbread|"
                        r"soup|stew|salad|condiment|breakfast|cuisine|sandwich|porridge|dumpling|fritter|pancake|"
                        r"confection|yogurt|yoghurt|dairy|milk|fermented|cheese|sauce|chutney|pickle|fried|"
                        r"baked|steamed|meal|noodle|omelette|biryani|masala", re.I)
NOISE = {"jpg", "jpeg", "png", "webp", "file", "the", "of", "and", "with", "a", "an", "in", "at", "on", "de", "img",
         "dsc", "photo", "image", "pic", "picture", "indian", "india", "food", "dish", "style", "homemade", "home",
         "made", "served", "plate", "bowl"}
BAD_WORDS = {"making", "recipe", "preparation", "cooking", "cooked", "shop", "stall", "restaurant", "hotel", "menu",
             "packet", "airline", "flight", "market", "vendor", "factory", "logo", "map", "sign", "label", "advert",
             "packaging", "raw", "ingredients", "ingredient", "plant", "field", "crop", "seeds", "flour", "festival",
             "temple", "street", "selling", "seller", "kitchen", "chef", "machine", "process", "step"}


@dataclass
class Candidate:
    title: str
    thumb_url: str
    width: int
    height: int
    license: str
    artist: str
    credit: str
    page_url: str
    score: float
    from_article: bool = False


def _headers() -> dict:
    return {"User-Agent": f"FoodWise/1.0 (dish pictures for college kitchens; {get_settings().contact})"}


def _tokens(text: str) -> list[str]:
    text = re.sub(r"^file:", "", text.strip(), flags=re.I)
    text = re.sub(r"\.(jpe?g|png|webp|gif|tiff?)$", "", text, flags=re.I)
    words = re.findall(r"[a-z]+", text.lower())
    return [w for w in words if w not in NOISE and len(w) > 1]


def score_title(names: list[str], title: str, width: int, height: int, from_article: bool = False) -> float:
    """How likely a file is a photo of this dish, from its title and shape. 1.0 is a perfect match."""
    words = _tokens(title)
    best = -1.0
    for name in names:
        q = _tokens(name)
        if not q:
            continue
        cover = len(set(q) & set(words)) / len(set(q))
        extra = len([w for w in words if w not in q])
        s = cover - 0.1 * extra
        if words[: len(q)] == q:
            s += 0.1
        best = max(best, s)
    if from_article:
        best = max(best, 0.6) + 0.25
    if set(words) & BAD_WORDS:
        best -= 0.35
    aspect = width / height if height else 0
    if aspect < 0.6 or aspect > 2.2:
        best -= 0.15
    if min(width, height) < 300:
        best -= 0.3
    return round(best, 3)


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _get(url: str, params: dict) -> dict:
    with telemetry.external("wikimedia"):
        resp = httpx.get(url, params={**params, "format": "json", "formatversion": 2}, headers=_headers(), timeout=20.0)
        resp.raise_for_status()
        return resp.json()


def _article_images(names: list[str]) -> list[str]:
    """Lead images of Wikipedia articles titled like the dish, if the article is about food."""
    data = _get(WIKIPEDIA, {"action": "query", "titles": "|".join(names[:10]), "redirects": 1,
                            "prop": "pageimages|description", "piprop": "name"})
    out = []
    for page in data.get("query", {}).get("pages", []):
        if page.get("missing") or not page.get("pageimage"):
            continue
        if FOOD_WORDS.search(page.get("description", "") or ""):
            out.append("File:" + page["pageimage"].replace("_", " "))  # Commons titles use spaces
    return out


def _search_titles(name: str, limit: int = 10) -> list[str]:
    data = _get(COMMONS, {"action": "query", "list": "search", "srnamespace": 6, "srlimit": limit,
                          "srsearch": f'intitle:"{name}" filetype:bitmap'})
    return [r["title"] for r in data.get("query", {}).get("search", [])]


def _file_info(titles: list[str]) -> dict[str, dict]:
    if not titles:
        return {}
    data = _get(COMMONS, {"action": "query", "titles": "|".join(titles[:50]), "prop": "imageinfo",
                          "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 640,
                          "iiextmetadatafilter": "LicenseShortName|Artist|UsageTerms|AttributionRequired"})
    out = {}
    for page in data.get("query", {}).get("pages", []):
        info = (page.get("imageinfo") or [None])[0]
        if info:
            out[page["title"]] = info
    return out


def candidates(names: list[str], limit: int = 8) -> list[Candidate]:
    names = [n for n in dict.fromkeys(n.strip() for n in names if n and n.strip())]
    article = set(_article_images(names))
    titles = list(article)
    for name in names[:3]:
        titles += [t for t in _search_titles(name) if t not in titles]
    out = []
    for title, info in _file_info(titles).items():
        meta = info.get("extmetadata", {})
        licence = _strip_html(meta.get("LicenseShortName", {}).get("value"))
        if info.get("mime") not in ("image/jpeg", "image/png", "image/webp") or not FREE_LICENCE.match(licence):
            continue
        artist = _strip_html(meta.get("Artist", {}).get("value"))[:120] or "Unknown author"
        out.append(Candidate(
            title=title, thumb_url=info.get("thumburl") or info["url"], width=int(info.get("width", 0)),
            height=int(info.get("height", 0)), license=licence, artist=artist,
            credit=f"{artist}, {licence}, via Wikimedia Commons", page_url=info.get("descriptionurl", ""),
            score=score_title(names, title, int(info.get("width", 0)), int(info.get("height", 0)), title in article),
            from_article=title in article,
        ))
    out.sort(key=lambda c: -c.score)
    return out[:limit]


def candidate_for(title: str) -> Candidate | None:
    """One Commons file, if it exists and is freely licensed."""
    info = _file_info([title]).get(title)
    if not info:
        return None
    meta = info.get("extmetadata", {})
    licence = _strip_html(meta.get("LicenseShortName", {}).get("value"))
    if not FREE_LICENCE.match(licence) or info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
        return None
    artist = _strip_html(meta.get("Artist", {}).get("value"))[:120] or "Unknown author"
    return Candidate(title=title, thumb_url=info.get("thumburl") or info["url"], width=int(info.get("width", 0)),
                     height=int(info.get("height", 0)), license=licence, artist=artist,
                     credit=f"{artist}, {licence}, via Wikimedia Commons", page_url=info.get("descriptionurl", ""),
                     score=1.0)


def process(data: bytes) -> bytes:
    """Validate an image and re-encode it as a square JPEG without metadata."""
    if len(data) > MAX_BYTES:
        raise Unprocessable("That picture is larger than 8 MB.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - any decoder error means "not a picture we can use"
        raise Unprocessable("That file is not a picture FoodWise can read. Use a JPEG, PNG or WebP.") from exc
    w, h = img.size
    side = min(w, h)
    img = img.crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side))
    img = img.resize((SIZE, SIZE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=84, optimize=True, progressive=True)
    return out.getvalue()


def store(dish: Dish, data: bytes, *, source: str, credit: str | None, licence: str | None, source_url: str | None) -> None:
    jpeg = process(data)
    digest = hashlib.sha256(jpeg).hexdigest()[:10]
    key = tenant_key(dish.tenant_id, "dishes", f"{dish.id}-{digest}.jpg")
    storage().put(key, jpeg)
    dish.image_key = key
    dish.image_source = source
    dish.image_status = "found"
    dish.image_credit = credit
    dish.image_license = licence
    dish.image_source_url = source_url
    dish.image_updated_at = datetime.now(UTC)


def download(url: str) -> bytes:
    with telemetry.external("wikimedia"):
        resp = httpx.get(url, headers=_headers(), timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        if len(resp.content) > MAX_BYTES:
            raise Unprocessable("That picture is too large.")
        return resp.content


def use_candidate(dish: Dish, c: Candidate) -> None:
    store(dish, download(c.thumb_url), source="wikimedia", credit=c.credit, licence=c.license, source_url=c.page_url)


def auto_find(dish: Dish, min_score: float = 0.45) -> str:
    """Pick a picture for a dish that has none. Network errors propagate so the job retries."""
    if get_settings().images_provider == "none":
        return "disabled"
    found = candidates([dish.name, *(dish.aliases or [])])
    best = next((c for c in found if c.score >= min_score), None)
    if best is None:
        dish.image_status = "none"
        dish.image_updated_at = datetime.now(UTC)
        return "none"
    use_candidate(dish, best)
    return "found"


def image_url(dish: Dish) -> str | None:
    if not dish.image_key or dish.image_status != "found":
        return None
    v = int(dish.image_updated_at.timestamp()) if dish.image_updated_at else 0
    return f"/api/v1/media/dishes/{dish.id}?v={v}"

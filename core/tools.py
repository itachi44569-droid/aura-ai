"""
Tool registry — decorator-based, auto-generates JSON schemas for LLM tool calling.
Built-in tools: calculator, datetime, web search, weather, stocks/crypto.
All 100% free, no paid API keys needed.
"""
import math
import json
import asyncio
from dataclasses import dataclass
from typing import Callable, Any
from datetime import datetime

import pytz

# ── Tool dataclass ─────────────────────────────────────────────────────────────

@dataclass
class Tool:
    name: str
    description: str
    func: Callable
    schema: dict  # OpenAI-compatible function schema

_REGISTRY: dict[str, Tool] = {}

def tool(name: str, description: str, parameters: dict, required: list[str] = None):
    """Register a function as a callable tool for the LLM."""
    def decorator(func):
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": parameters,
                    "required": required or list(parameters.keys()),
                },
            },
        }
        _REGISTRY[name] = Tool(name=name, description=description, func=func, schema=schema)
        return func
    return decorator

def get_tools(names: list[str] | None = None) -> list[Tool]:
    """Return all registered tools, or a specific subset by name."""
    if names is None:
        return list(_REGISTRY.values())
    return [_REGISTRY[n] for n in names if n in _REGISTRY]

async def run_tool(name: str, args: dict) -> Any:
    """Execute a tool by name, handling both sync and async functions."""
    t = _REGISTRY.get(name)
    if not t:
        return {"error": f"Tool '{name}' not found"}
    try:
        if asyncio.iscoroutinefunction(t.func):
            return await t.func(**args)
        return t.func(**args)
    except Exception as e:
        return {"error": str(e)}

# ── Built-in Tool: Calculator ──────────────────────────────────────────────────

@tool(
    name="calculator",
    description="Evaluate any mathematical expression. Use for arithmetic, percentages, compound interest, etc.",
    parameters={"expression": {"type": "string", "description": "Math expression e.g. '2 ** 10', 'sqrt(144)', '15% of 2000'"}},
)
def calculator(expression: str) -> dict:
    safe_globals = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    safe_globals.update({"abs": abs, "round": round, "min": min, "max": max, "sum": sum, "pow": pow})
    # Handle "X% of Y" shorthand
    import re
    m = re.match(r"([\d.]+)%\s+of\s+([\d.]+)", expression, re.I)
    if m:
        expression = f"{m.group(1)}/100*{m.group(2)}"
    try:
        result = eval(expression, {"__builtins__": {}}, safe_globals)
        return {"result": result, "expression": expression}
    except Exception as e:
        return {"error": str(e)}

# ── Built-in Tool: DateTime ────────────────────────────────────────────────────

@tool(
    name="get_datetime",
    description="Get current date, time, day of week, and timezone info.",
    parameters={"timezone": {"type": "string", "description": "Timezone e.g. 'Asia/Kolkata', 'US/Eastern', 'UTC'"}},
    required=[],
)
def get_datetime(timezone: str = "UTC") -> dict:
    try:
        tz  = pytz.timezone(timezone)
        now = datetime.now(tz)
        return {
            "date":      now.strftime("%Y-%m-%d"),
            "time":      now.strftime("%H:%M:%S"),
            "day":       now.strftime("%A"),
            "timezone":  timezone,
            "timestamp": int(now.timestamp()),
        }
    except Exception as e:
        return {"error": str(e)}

# ── Built-in Tool: Web Search (DuckDuckGo — free) ─────────────────────────────

@tool(
    name="web_search",
    description="Search the web for current news, facts, or any real-time information.",
    parameters={
        "query":       {"type": "string",  "description": "Search query"},
        "max_results": {"type": "integer", "description": "Number of results (1-5)"},
    },
    required=["query"],
)
async def web_search(query: str, max_results: int = 3) -> dict:
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title":   r.get("title", ""),
                    "snippet": r.get("body", "")[:300],
                    "url":     r.get("href", ""),
                })
        return {"results": results}
    except Exception as e:
        return {"error": str(e)}

# ── Built-in Tool: Weather (Open-Meteo — free, no API key) ────────────────────

@tool(
    name="get_weather",
    description="Get current weather conditions for any city worldwide. Free, no API key required.",
    parameters={
        "city": {"type": "string", "description": "City name e.g. 'Mumbai', 'London', 'New York'"},
    },
)
async def get_weather(city: str) -> dict:
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            # Geocode
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
            async with session.get(geo_url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                geo = await r.json()
            if not geo.get("results"):
                return {"error": f"City '{city}' not found"}
            loc = geo["results"][0]
            lat, lon = loc["latitude"], loc["longitude"]
            # Weather
            w_url = (
                f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={lat}&longitude={lon}"
                f"&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
                f"wind_speed_10m,weather_code,precipitation"
                f"&temperature_unit=celsius&wind_speed_unit=kmh"
            )
            async with session.get(w_url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                data = await r.json()
        c = data["current"]
        codes = {
            0:"Clear sky",1:"Mainly clear",2:"Partly cloudy",3:"Overcast",
            45:"Foggy",48:"Icy fog",51:"Light drizzle",53:"Drizzle",
            61:"Light rain",63:"Rain",65:"Heavy rain",71:"Light snow",
            73:"Snow",75:"Heavy snow",80:"Rain showers",81:"Showers",
            95:"Thunderstorm",99:"Thunderstorm with hail",
        }
        desc = codes.get(c["weather_code"], f"Code {c['weather_code']}")
        return {
            "city":          loc["name"],
            "country":       loc.get("country", ""),
            "condition":     desc,
            "temperature_c": c["temperature_2m"],
            "feels_like_c":  c["apparent_temperature"],
            "humidity_pct":  c["relative_humidity_2m"],
            "wind_kmh":      c["wind_speed_10m"],
            "rain_mm":       c.get("precipitation", 0),
        }
    except Exception as e:
        return {"error": str(e)}

# ── Built-in Tool: Stock / Crypto Price ───────────────────────────────────────

CRYPTO_SYMBOLS = {"BTC","ETH","SOL","BNB","XRP","DOGE","ADA","AVAX","MATIC","LTC","TRX","DOT"}

@tool(
    name="get_price",
    description="Get real-time price for any stock (AAPL, TSLA) or cryptocurrency (BTC, ETH). Free.",
    parameters={
        "symbol": {"type": "string", "description": "Ticker symbol e.g. 'AAPL', 'BTC', 'NVDA', 'ETH'"},
    },
)
async def get_price(symbol: str) -> dict:
    try:
        import yfinance as yf
        sym    = symbol.upper().strip()
        yf_sym = f"{sym}-USD" if sym in CRYPTO_SYMBOLS else sym
        ticker = yf.Ticker(yf_sym)
        info   = ticker.fast_info
        hist   = ticker.history(period="2d")
        prev   = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else None
        price  = float(info.last_price)
        chg    = round(((price - prev) / prev) * 100, 2) if prev else None
        return {
            "symbol":     sym,
            "price":      round(price, 4),
            "change_pct": chg,
            "currency":   info.currency,
            "type":       "crypto" if sym in CRYPTO_SYMBOLS else "stock",
        }
    except Exception as e:
        return {"error": str(e)}

# ── Built-in Tool: Unit Converter ─────────────────────────────────────────────

@tool(
    name="convert_units",
    description="Convert between units: length, weight, temperature, speed, area, volume.",
    parameters={
        "value": {"type": "number", "description": "The numeric value to convert"},
        "from_unit": {"type": "string", "description": "Source unit e.g. 'km', 'kg', 'celsius', 'mph'"},
        "to_unit":   {"type": "string", "description": "Target unit e.g. 'miles', 'lbs', 'fahrenheit', 'kmh'"},
    },
)
def convert_units(value: float, from_unit: str, to_unit: str) -> dict:
    f, t = from_unit.lower(), to_unit.lower()
    conversions = {
        ("km","miles"):0.621371, ("miles","km"):1.60934,
        ("kg","lbs"):2.20462,   ("lbs","kg"):0.453592,
        ("m","ft"):3.28084,     ("ft","m"):0.3048,
        ("cm","inches"):0.393701,("inches","cm"):2.54,
        ("mph","kmh"):1.60934,  ("kmh","mph"):0.621371,
        ("liters","gallons"):0.264172, ("gallons","liters"):3.78541,
        ("celsius","fahrenheit"):None, ("fahrenheit","celsius"):None,
        ("celsius","kelvin"):None,     ("kelvin","celsius"):None,
    }
    if (f,t) == ("celsius","fahrenheit"):
        return {"result": round(value*9/5+32, 4), "from":f"{value} {f}", "to":f"{round(value*9/5+32,4)} {t}"}
    if (f,t) == ("fahrenheit","celsius"):
        return {"result": round((value-32)*5/9, 4), "from":f"{value} {f}", "to":f"{round((value-32)*5/9,4)} {t}"}
    if (f,t) == ("celsius","kelvin"):
        return {"result": round(value+273.15, 4), "from":f"{value} {f}", "to":f"{round(value+273.15,4)} {t}"}
    if (f,t) == ("kelvin","celsius"):
        return {"result": round(value-273.15, 4), "from":f"{value} {f}", "to":f"{round(value-273.15,4)} {t}"}
    factor = conversions.get((f,t))
    if factor:
        return {"result": round(value*factor, 6), "from":f"{value} {f}", "to":f"{round(value*factor,6)} {t}"}
    return {"error": f"Don't know how to convert {from_unit} to {to_unit}"}

# ── Currency Exchange (Frankfurter.app — free, no API key) ────────────────────

@tool(
    name="get_currency",
    description="Get live forex exchange rates between any two currencies. 100% free, no API key.",
    parameters={
        "from_currency": {"type":"string","description":"Source currency code e.g. 'USD', 'INR', 'EUR'"},
        "to_currency":   {"type":"string","description":"Target currency code e.g. 'INR', 'GBP', 'JPY'"},
        "amount":        {"type":"number","description":"Amount to convert (default 1)"},
    },
    required=["from_currency","to_currency"],
)
async def get_currency(from_currency: str, to_currency: str, amount: float = 1.0) -> dict:
    try:
        import aiohttp
        url = (f"https://api.frankfurter.app/latest"
               f"?from={from_currency.upper()}&to={to_currency.upper()}")
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                data = await r.json()
        rate = data.get("rates", {}).get(to_currency.upper())
        if not rate:
            return {"error": f"Unknown currency code: {to_currency}"}
        return {
            "from":   from_currency.upper(),
            "to":     to_currency.upper(),
            "rate":   rate,
            "amount": amount,
            "result": round(amount * rate, 4),
            "date":   data.get("date",""),
        }
    except Exception as e:
        return {"error": str(e)}

# ── News (Google News RSS — free, no API key) ─────────────────────────────────

import re as _re

@tool(
    name="get_news",
    description="Get latest news headlines on any topic. Free, no API key required.",
    parameters={
        "topic":       {"type":"string","description":"News topic e.g. 'AI', 'Bitcoin', 'India economy'"},
        "max_results": {"type":"integer","description":"Number of headlines to return (1-8)"},
    },
    required=["topic"],
)
async def get_news(topic: str, max_results: int = 5) -> dict:
    try:
        import aiohttp
        enc = topic.replace(" ", "+")
        url = f"https://news.google.com/rss/search?q={enc}&hl=en&gl=US&ceid=US:en"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=8),
                             headers={"User-Agent": "Mozilla/5.0"}) as r:
                text = await r.text()
        titles = _re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", text)
        dates  = _re.findall(r"<pubDate>(.*?)</pubDate>", text)
        items  = []
        for i, title in enumerate(titles[1:max_results+1]):
            items.append({"headline": title, "published": dates[i] if i < len(dates) else ""})
        if not items:
            # Fallback: plain RSS title tags
            plain = _re.findall(r"<title>(.*?)</title>", text)[1:max_results+1]
            items = [{"headline": h, "published": ""} for h in plain]
        return {"topic": topic, "news": items}
    except Exception as e:
        return {"error": str(e)}

# ── YouTube Transcript (youtube-transcript-api — free, no API key) ────────────

@tool(
    name="youtube_summary",
    description="Get the transcript of any YouTube video for summarization. Free, no API key.",
    parameters={
        "url": {"type":"string","description":"YouTube URL e.g. 'https://youtu.be/xxxxx' or 'https://www.youtube.com/watch?v=xxxxx'"},
    },
)
async def youtube_summary(url: str) -> dict:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        match = _re.search(r"(?:v=|youtu\.be/|shorts/)([^&\n?#]{11})", url)
        if not match:
            return {"error": "Invalid YouTube URL. Use: https://youtu.be/VIDEO_ID or https://youtube.com/watch?v=VIDEO_ID"}
        vid_id = match.group(1)
        try:
            transcript_list = YouTubeTranscriptApi.get_transcript(vid_id)
        except Exception:
            transcripts = YouTubeTranscriptApi.list_transcripts(vid_id)
            transcript_list = transcripts.find_transcript(["en", "hi", "auto"]).fetch()
        text = " ".join(t["text"] for t in transcript_list)
        return {
            "video_id":           vid_id,
            "transcript_length":  len(text),
            "transcript":         text[:3500] + ("..." if len(text) > 3500 else ""),
        }
    except Exception as e:
        return {"error": str(e)}

# ── Wikipedia (free REST API — no API key) ────────────────────────────────────

@tool(
    name="wikipedia_search",
    description="Look up factual information about any topic, person, place, or concept on Wikipedia.",
    parameters={
        "query":     {"type":"string","description":"Topic to look up e.g. 'Elon Musk', 'quantum computing'"},
        "sentences": {"type":"integer","description":"Number of summary sentences to return (1-5)"},
    },
    required=["query"],
)
async def wikipedia_search(query: str, sentences: int = 3) -> dict:
    try:
        import aiohttp
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{query.replace(' ','_')}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 404:
                    search_url = (f"https://en.wikipedia.org/w/api.php"
                                  f"?action=opensearch&search={query}&limit=1&format=json")
                    async with s.get(search_url) as sr:
                        sd = await sr.json()
                    if not sd[1]:
                        return {"error": f"No Wikipedia article found for '{query}'"}
                    url2 = f"https://en.wikipedia.org/api/rest_v1/page/summary/{sd[1][0].replace(' ','_')}"
                    async with s.get(url2) as r2:
                        data = await r2.json()
                else:
                    data = await r.json()
        extract = data.get("extract", "")
        sents   = _re.split(r"(?<=[.!?])\s+", extract)
        return {
            "title":   data.get("title",""),
            "summary": " ".join(sents[:sentences]),
            "url":     data.get("content_urls",{}).get("desktop",{}).get("page",""),
        }
    except Exception as e:
        return {"error": str(e)}

# ── Web Page Reader (aiohttp + BeautifulSoup — free) ─────────────────────────

@tool(
    name="read_webpage",
    description=(
        "Fetch and read the full content of any webpage. Use when the user shares a URL "
        "and wants Nova to read, summarize, or answer questions about it."
    ),
    parameters={
        "url": {
            "type": "string",
            "description": "Full URL of the page to read e.g. 'https://example.com/article'",
        },
    },
)
async def read_webpage(url: str) -> dict:
    try:
        import re
        import aiohttp
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=12),
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    return {"error": f"Page returned HTTP {resp.status}"}
                ct = resp.headers.get("content-type", "")
                if "html" not in ct and "text" not in ct:
                    return {"error": f"URL is not an HTML page (content-type: {ct})"}
                html = await resp.text(errors="replace")

        soup = BeautifulSoup(html, "html.parser")

        # Strip noise elements
        for tag in soup(["script", "style", "nav", "header", "footer",
                          "aside", "noscript", "iframe", "form", "svg", "button"]):
            tag.decompose()

        # Strip elements whose class names suggest nav/ads/cookie banners
        noise_patterns = ["navigation", "nav-", "footer", "sidebar",
                          "advertisement", "cookie", "popup", "overlay", "modal"]
        for el in soup.find_all(attrs={"class": True}):
            classes = " ".join(el.get("class", []))
            if any(p in classes.lower() for p in noise_patterns):
                el.decompose()

        title = soup.title.get_text(strip=True) if soup.title else ""

        # Prefer a semantic main/article block; fall back to body
        main = (
            soup.find("main")
            or soup.find("article")
            or soup.find(id=lambda x: x and "content" in x.lower())
            or soup.body
        )
        raw = (main or soup).get_text(separator="\n", strip=True)

        # Collapse runs of blank lines
        text = re.sub(r"\n{3,}", "\n\n", raw).strip()

        MAX_CHARS = 6_000
        truncated = len(text) > MAX_CHARS
        if truncated:
            text = text[:MAX_CHARS]

        return {
            "url":       url,
            "title":     title,
            "text":      text,
            "chars":     len(text),
            "truncated": truncated,
        }
    except Exception as e:
        return {"error": str(e)}


# ── Image Generation (Pollinations.ai — free, no API key) ────────────────────

@tool(
    name="generate_image",
    description=(
        "Generate a high-quality image from a text description using Flux AI. "
        "Use whenever the user asks to create, draw, generate, make, paint, or visualise "
        "an image, picture, photo, artwork, or illustration. "
        "Write a rich, detailed visual prompt for best results — include style, lighting, mood, "
        "colors, and composition details. Return the markdown image tag so the image renders inline."
    ),
    parameters={
        "prompt": {
            "type": "string",
            "description": (
                "Detailed visual prompt. Be specific: describe subjects, setting, lighting, "
                "color palette, art style (photorealistic, oil painting, digital art, anime, etc.), "
                "mood, and composition. More detail = better image quality."
            ),
        },
        "width":  {"type": "integer", "description": "Width in pixels. Use 1024 for landscape, 768 for square, 576 for portrait."},
        "height": {"type": "integer", "description": "Height in pixels. Use 576 for landscape, 768 for square, 1024 for portrait."},
        "style":  {
            "type": "string",
            "description": "Visual style: 'photorealistic', 'digital-art', 'anime', 'oil-painting', 'watercolor', 'cinematic', '3d-render'",
        },
    },
    required=["prompt"],
)
async def generate_image(prompt: str, width: int = 1024, height: int = 768,
                         style: str = "photorealistic") -> dict:
    import urllib.parse, time, random

    style_suffixes = {
        "photorealistic":  "photorealistic, 8K UHD, sharp focus, professional photography, detailed",
        "digital-art":     "digital art, concept art, trending on ArtStation, vibrant colors, detailed",
        "anime":           "anime style, Studio Ghibli inspired, cel shading, beautiful, detailed",
        "oil-painting":    "oil painting, impressionist, rich textures, masterpiece, gallery quality",
        "watercolor":      "watercolor painting, soft edges, artistic, beautiful, flowing colors",
        "cinematic":       "cinematic, movie still, dramatic lighting, anamorphic lens, film grain",
        "3d-render":       "3D render, octane render, ray tracing, photorealistic, detailed materials",
    }
    suffix = style_suffixes.get(style, style_suffixes["photorealistic"])
    full_prompt = f"{prompt}, {suffix}"

    w = max(256, min(1792, width))
    h = max(256, min(1792, height))
    seed = random.randint(1, 999999)
    encoded = urllib.parse.quote(full_prompt)

    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?model=flux&width={w}&height={h}&nologo=true&enhance=true&seed={seed}"
    )

    return {
        "image_url": url,
        "prompt":    full_prompt,
        "display":   f"![{prompt}]({url})",
        "note":      "Include the display markdown verbatim in your response so the image renders inline.",
    }

# ── Reminder (scheduler module handles the actual scheduling) ─────────────────

@tool(
    name="set_reminder",
    description="Set a reminder or scheduled message. The user will be notified at the specified time.",
    parameters={
        "message": {"type":"string","description":"What to remind the user about"},
        "when":    {"type":"string","description":"When: '10m', '2h', 'daily 9am', 'tomorrow 8pm', 'every monday 9am'"},
    },
)
def set_reminder(message: str, when: str) -> dict:
    return {
        "status":  "reminder_requested",
        "message": message,
        "when":    when,
        "note":    "Reminder has been registered. The scheduler will deliver it at the right time.",
    }

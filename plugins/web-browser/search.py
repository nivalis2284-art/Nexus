"""Small search adapter for the browser plugin. Uses the system proxy and TLS settings."""
import base64
import html
import json
import re
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_MAX_BODY = 2 * 1024 * 1024
_TIMEOUT = 15


def _url(value):
    if not isinstance(value, str):
        return None
    value = html.unescape(value).strip()
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username is not None or parts.password is not None:
            return None
        port = parts.port
        host = parts.hostname.lower()
        if ':' in host:
            host = '[' + host + ']'
        if port is not None and not ((parts.scheme.lower() == 'http' and port == 80) or (parts.scheme.lower() == 'https' and port == 443)):
            host += ':' + str(port)
        return urlunsplit((parts.scheme.lower(), host, parts.path or '/', parts.query, ''))
    except ValueError:
        return None


def _endpoint(value):
    normalized = _url(value)
    if not normalized or urlsplit(value).query or urlsplit(value).fragment:
        raise ValueError('Search endpoint must be an HTTP(S) URL without credentials, query, or fragment')
    return normalized


def _bing_url(value):
    result = _url(value)
    if not result:
        return None
    parts = urlsplit(result)
    if (parts.hostname == 'bing.com' or parts.hostname.endswith('.bing.com')) and parts.path.startswith('/ck/a'):
        encoded = parse_qs(parts.query).get('u', [''])[0]
        if not encoded.startswith('a1'):
            return None
        try:
            encoded = encoded[2:]
            result = _url(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)).decode('utf-8'))
        except (ValueError, UnicodeError):
            return None
    return result


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)


def _plain(value):
    parser = _Text()
    parser.feed(str(value or ''))
    return ' '.join(' '.join(parser.parts).split())


class _Bing(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.result_depth = None
        self.heading_depth = None
        self.link_depth = None
        self.snippet_depth = None
        self.current = None
        self.items = []
        self.has_results_container = False
        self.no_results = False
        self.page_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get('class', '').split())
        self.depth += 1
        if attrs.get('id') == 'b_results':
            self.has_results_container = True
        if 'b_no' in classes:
            self.no_results = True
        if self.result_depth is None and tag == 'li' and 'b_algo' in classes:
            self.result_depth = self.depth
            self.current = {'title': [], 'url': '', 'snippet': []}
        elif self.current is not None:
            if tag == 'h2' and self.heading_depth is None:
                self.heading_depth = self.depth
            elif tag == 'a' and self.heading_depth is not None and not self.current['url']:
                self.link_depth = self.depth
                self.current['url'] = attrs.get('href', '')
            elif tag == 'p' and self.snippet_depth is None and not self.current['snippet']:
                self.snippet_depth = self.depth
        if tag in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.handle_endtag(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.current is not None:
            if self.link_depth == self.depth:
                self.link_depth = None
            if self.heading_depth == self.depth:
                self.heading_depth = None
            if self.snippet_depth == self.depth:
                self.snippet_depth = None
            if self.result_depth == self.depth:
                self.items.append({'title': ' '.join(self.current['title']), 'url': _bing_url(self.current['url']), 'snippet': ' '.join(self.current['snippet'])})
                self.current = None
                self.result_depth = None
        self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        self.page_text.append(data)
        if self.current is not None:
            if self.link_depth is not None:
                self.current['title'].append(data)
            if self.snippet_depth is not None:
                self.current['snippet'].append(data)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch(url, provider, key):
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; NexusBrowser/1.0)', 'Accept': 'text/html,application/json'}
    if key:
        headers['X-Subscription-Token'] = key
    request = Request(url, headers=headers)
    opener = build_opener(_NoRedirect()) if key else build_opener()
    try:
        with opener.open(request, timeout=_TIMEOUT) as response:
            data = response.read(_MAX_BODY + 1)
            if len(data) > _MAX_BODY:
                raise RuntimeError(f'{provider} response exceeded size limit')
            return data.decode('utf-8', errors='replace')
    except HTTPError as exc:
        raise RuntimeError(f'{provider} search failed (HTTP {exc.code})') from None
    except (URLError, OSError, ValueError):
        raise RuntimeError(f'{provider} search failed (network error)') from None


def search(query, limit, settings, key=''):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 2000:
        raise ValueError('Query must contain 1 to 2000 characters')
    query = query.strip()
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
        raise ValueError('Limit must be an integer from 1 to 10')
    if not isinstance(settings, dict):
        raise ValueError('Search settings must be an object')  # noqa: TRY004
    provider = settings.get('search_provider') or 'bing'
    if provider not in ('bing', 'brave', 'searxng'):
        raise ValueError('Unknown search provider')
    configured = settings.get('search_url') or ''
    if provider == 'bing':
        endpoint = _endpoint(configured or 'https://www.bing.com/search')
        params = {'q': query, 'count': limit}
    elif provider == 'brave':
        if not isinstance(key, str) or not key.strip():
            raise ValueError('Brave search requires an API key')
        endpoint = _endpoint(configured or 'https://api.search.brave.com/res/v1/web/search')
        params = {'q': query, 'count': limit}
    else:
        if not configured:
            raise ValueError('SearXNG requires a search URL')
        endpoint = _endpoint(configured).rstrip('/') + '/search'
        params = {'q': query, 'format': 'json'}
    data = _fetch(endpoint + '?' + urlencode(params), provider, key if provider == 'brave' else '')
    if provider == 'bing':
        parser = _Bing()
        parser.feed(data)
        page = ' '.join(parser.page_text).lower()
        valid_results = any(item['url'] and _plain(item['title']) for item in parser.items)
        if not valid_results and any(word in page for word in ('captcha', 'unusual traffic', 'verify you are human', 'verify that you are human')):
            raise RuntimeError('bing search was blocked or requires verification')
        if parser.items and not valid_results:
            raise RuntimeError('bing search returned unrecognized results')
        if not parser.items and not (parser.has_results_container and (parser.no_results or re.search(r'no results found|no results for|there are no results', page))):
            raise RuntimeError('bing search returned an unrecognized page')
        items = parser.items
    else:
        try:
            payload = json.loads(data)
            items = payload['web']['results'] if provider == 'brave' else payload['results']
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise RuntimeError(f'{provider} search returned invalid JSON results') from None
    results, seen = [], set()
    for item in items:
        url = _url(item.get('url'))
        title = _plain(item.get('title'))
        if not url or not title or url in seen:
            continue
        seen.add(url)
        results.append({'title': title, 'url': url, 'snippet': _plain(item.get('snippet') or item.get('description') or item.get('content'))})
        if len(results) == limit:
            break
    return {'query': query, 'provider': provider, 'results': results}

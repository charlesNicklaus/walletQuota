import hashlib
import http.cookiejar
import json
import time
import urllib.error
import urllib.request
from email.message import Message
from typing import Any, Callable, Mapping, Optional, Protocol

from .config import AtgSettings as Settings


class APIError(RuntimeError):
    """Raised for a rejected or malformed API response."""


class AuthenticationError(APIError):
    """Raised when the API does not establish a session."""


class TransportError(APIError):
    """Raised when an HTTP request cannot be completed."""


class HTTPStatusError(TransportError):
    """Raised for an HTTP status outside the successful range."""

    def __init__(self, status: int, retry_after: Optional[float] = None):
        super().__init__("HTTP request failed with status {}".format(status))
        self.status = status
        self.retry_after = retry_after


class Transport(Protocol):
    def post(
        self,
        url: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: float,
    ) -> Mapping[str, Any]:
        ...


def _javascript_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def make_signature(session_key: str, payload: Mapping[str, Any]) -> str:
    canonical = "&".join(
        "{}={}".format(key, _javascript_scalar(payload[key]))
        for key in sorted(payload)
    )
    signed = "{}&{}".format(canonical, session_key)
    return hashlib.md5(signed.encode("utf-8")).hexdigest()


def _retry_after(headers: Message) -> Optional[float]:
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


class UrllibTransport:
    """Cookie-aware JSON transport for the two supported ATG endpoints."""

    def __init__(self) -> None:
        cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookie_jar)
        )

    def post(
        self,
        url: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: float,
    ) -> Mapping[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        request = urllib.request.Request(
            url, data=body, headers=dict(headers), method="POST"
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raise HTTPStatusError(
                error.code, retry_after=_retry_after(error.headers)
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TransportError("The ATG API could not be reached") from error
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise TransportError("The ATG API returned invalid JSON") from error
        if not isinstance(value, Mapping):
            raise TransportError("The ATG API returned an invalid response")
        return value


class AtgClient:
    LOGIN_PATH = "/auth/login"
    POOL_PATH = "/pay/debit/task"
    MAX_TRANSIENT_RETRIES = 2

    def __init__(
        self,
        settings: Settings,
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self.settings = settings
        self.transport = transport or UrllibTransport()
        self.sleep = sleep
        self.clock_ms = clock_ms
        self.user_id: Optional[int] = None
        self.login_token: Optional[str] = None
        self.session_key: Optional[str] = None

    @property
    def base_headers(self) -> Mapping[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://app-web.atg-game.com",
            "Referer": "https://app-web.atg-game.com/",
            "User-Agent": "ATGPoolReport/1.0",
        }

    def _url(self, path: str) -> str:
        if path not in {self.LOGIN_PATH, self.POOL_PATH}:
            raise APIError("Unsupported API endpoint")
        return "{}{}".format(self.settings.api_base, path)

    def _post_with_retry(
        self,
        path: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        request_factory: Optional[
            Callable[[], tuple[Mapping[str, Any], Mapping[str, str]]]
        ] = None,
    ) -> Mapping[str, Any]:
        attempt = 0
        while True:
            current_payload, current_headers = (
                request_factory() if request_factory else (payload, headers)
            )
            try:
                return self.transport.post(
                    self._url(path),
                    current_payload,
                    current_headers,
                    self.settings.timeout_seconds,
                )
            except HTTPStatusError as error:
                retryable = error.status == 429 or 500 <= error.status <= 599
                if not retryable or attempt >= self.MAX_TRANSIENT_RETRIES:
                    raise
                delay = error.retry_after
                if delay is None:
                    delay = 0.5 * (2 ** attempt)
            except TransportError:
                if attempt >= self.MAX_TRANSIENT_RETRIES:
                    raise
                delay = 0.5 * (2 ** attempt)
            self.sleep(delay)
            attempt += 1

    @staticmethod
    def _data_from_envelope(
        response: Mapping[str, Any], authentication: bool = False
    ) -> Mapping[str, Any]:
        code = response.get("code")
        if code != 200:
            message = str(response.get("message") or "ATG API request failed")
            if code in {1001, 1002} or authentication:
                raise AuthenticationError("ATG authentication failed")
            raise APIError(message)
        data = response.get("data")
        if not isinstance(data, Mapping):
            raise APIError("ATG API response is missing data")
        return data

    def login(self) -> None:
        response = self._post_with_retry(
            self.LOGIN_PATH,
            {"phone": self.settings.phone, "password": self.settings.password},
            self.base_headers,
        )
        data = self._data_from_envelope(response, authentication=True)
        user_id = data.get("userId")
        login_token = data.get("loginToken")
        session_key = data.get("sessionKey")
        if user_id is None or not login_token or not session_key:
            raise AuthenticationError("Login response is missing session information")
        try:
            self.user_id = int(user_id)
        except (TypeError, ValueError) as error:
            raise AuthenticationError("Login response contains an invalid user ID") from error
        self.login_token = str(login_token)
        self.session_key = str(session_key)

    def _signed_pool_request(
        self, page: int, size: int, sort_type: int, order_type: int
    ) -> Mapping[str, Any]:
        if self.user_id is None or self.session_key is None:
            raise AuthenticationError("Login is required before reading the order pool")

        def signed_request() -> tuple[Mapping[str, Any], Mapping[str, str]]:
            payload = {
                "page": page,
                "size": size,
                "sortType": sort_type,
                "orderType": order_type,
                "ts": self.clock_ms(),
                "userId": self.user_id,
            }
            headers = dict(self.base_headers)
            headers["Signature"] = make_signature(self.session_key or "", payload)
            return payload, headers

        return self._post_with_retry(
            self.POOL_PATH, {}, {}, request_factory=signed_request
        )

    def fetch_pool_page(
        self, page: int, size: int, sort_type: int, order_type: int
    ) -> Mapping[str, Any]:
        for session_attempt in range(2):
            response = self._signed_pool_request(page, size, sort_type, order_type)
            try:
                return self._data_from_envelope(response)
            except AuthenticationError:
                if session_attempt == 1:
                    raise
                self.login()
        raise AuthenticationError("Unable to establish a valid session")

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

# Настройка вывода логов
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("remask_worker")

class AutomationError(RuntimeError): pass
class ProxyError(AutomationError): pass
class AuthenticationError(AutomationError): pass
class RemoteRequestError(AutomationError): pass

@dataclass(slots=True)
class WebProfile:
    name: str
    cookies: dict[str, str]
    proxy: Optional[str]
    user_agent: str

class WebSessionManager:
    """Управление сессией: прокси, куки, выгрузка fb_dtsg (Полная отказоустойчивость)"""
    def __init__(self, profile: WebProfile, timeout_seconds: int = 15, pool_size: int = 20):
        self.profile = profile
        self.base_url = "https://adsmanager.facebook.com/adsmanager/manage/campaigns"
        self.timeout_seconds = timeout_seconds
        self.pool_size = pool_size
        
        self.timeout = aiohttp.ClientTimeout(
            total=timeout_seconds, 
            connect=timeout_seconds, 
            sock_read=timeout_seconds
        )
        
        self.connector: Optional[aiohttp.TCPConnector] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self._csrf_token: Optional[str] = None
        self._csrf_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self.session is not None and not self.session.closed:
            return self.session
            
        async with self._session_lock:
            if self.session is not None and not self.session.closed:
                return self.session

            self.connector = aiohttp.TCPConnector(
                limit=self.pool_size, 
                limit_per_host=self.pool_size, 
                enable_cleanup_closed=True
            )
            self.session = aiohttp.ClientSession(
                timeout=self.timeout,
                connector=self.connector,
                cookies=self.profile.cookies,
                headers={
                    "User-Agent": self.profile.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                }
            )
            return self.session

    async def __aenter__(self) -> "WebSessionManager":
        await self._ensure_session()
        return self
        
    async def __aexit__(self, exc_type, exc, tb) -> None: 
        await self.close()
        
    async def close(self) -> None:
        if self.session and not self.session.closed: 
            await self.session.close()
        self.session = None
        self.connector = None

    async def check_connection(self) -> bool:
        """Проверка прокси через api.ipify.org с защитой от битого JSON"""
        if not self.profile.proxy:
            raise ProxyError("Proxy is required for this profile to prevent bans")
            
        session = await self._ensure_session()
        try:
            async with session.get("https://api.ipify.org", params={"format": "json"}, proxy=self.profile.proxy) as response:
                if response.status != 200:
                    raise ProxyError(f"Proxy check returned HTTP {response.status}")
                
                try:
                    payload = await response.json(content_type=None)
                except (json.JSONDecodeError, ValueError) as json_err:
                    raise ProxyError("Proxy checker returned invalid JSON") from json_err
                
                if not isinstance(payload, dict):
                    raise ProxyError("Unexpected response type from proxy checker")
                    
                ip = payload.get("ip")
                if not ip: 
                    raise ProxyError("Proxy check response contains no IP")
                log.info("[%s] proxy OK; exit_ip=%s", self.profile.name, ip)
                return True
        except asyncio.TimeoutError as exc:
            log.error("[%s] proxy connection timeout", self.profile.name)
            raise ProxyError("Proxy timeout") from exc
        except aiohttp.ClientError as exc:
            log.error("[%s] proxy connection failed: %s", self.profile.name, exc.__class__.__name__)
            raise ProxyError(f"Proxy connection failed: {exc.__class__.__name__}") from exc

    async def extract_csrf_token(self) -> str:
        if self._csrf_token: return self._csrf_token
        async with self._csrf_lock:
            if self._csrf_token: return self._csrf_token
            
            session = await self._ensure_session()
            try:
                async with session.get(self.base_url, proxy=self.profile.proxy) as response:
                    if response.status >= 400: 
                        raise AuthenticationError(f"Ads Manager returned HTTP {response.status}")
                    html = await response.text()
            except AuthenticationError:
                raise
            except asyncio.TimeoutError as exc:
                raise AuthenticationError("Timeout while fetching Ads Manager page") from exc
            except aiohttp.ClientError as exc:
                raise AuthenticationError(f"Network error while fetching Ads Manager: {exc.__class__.__name__}") from exc

            match = re.search(r"""["']token[#']\s*:\s*["']([^"']+)["']""", html, flags=re.IGNORECASE)
            if match and match.group(1).strip():
                self._csrf_token = match.group(1).strip()
                log.info("[%s] fb_dtsg token extracted successfully", self.profile.name)
                return self._csrf_token
            raise AuthenticationError("fb_dtsg token not found. Session expired or checkpoint.")

    async def send_post_request(self, endpoint_url: str, doc_id: str, variables: dict[str, Any]) -> dict[str, Any]:
        csrf_token = await self.extract_csrf_token()
        session = await self._ensure_session()
        
        form = {
            "fb_dtsg": csrf_token,
            "fb_api_caller_class": "RelayModern",
            "doc_id": doc_id,
            "variables": json.dumps(variables, separators=(",", ":"), ensure_ascii=False),
        }

        try:
            async with session.post(
                endpoint_url,
                proxy=self.profile.proxy,
                data=form,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "User-Agent": self.profile.user_agent,
                }
            ) as response:
                if response.status >= 400: 
                    raise RemoteRequestError(f"HTTP Error {response.status}")
                
                try:
                    payload = await response.json(content_type=None)
                except (json.JSONDecodeError, ValueError) as json_err:
                    raise RemoteRequestError("Facebook web server returned invalid JSON") from json_err
                    
                if not isinstance(payload, dict):
                    raise RemoteRequestError("Unexpected JSON response type from Facebook")
                    
                return payload
        except RemoteRequestError:
            raise
        except asyncio.TimeoutError as exc:
            raise RemoteRequestError("GraphQL request timeout") from exc
        except aiohttp.ClientError as exc:
            raise RemoteRequestError(f"GraphQL network failure: {exc.__class__.__name__}") from exc


class BusinessLogicController:
    """Контроллер приватных GraphQL мутаций (БМ, РК, Карты)"""
    def __init__(self, session: WebSessionManager):
        self.session = session
        self.graphql_url = "https://www.facebook.com/api/graphql/"

    async def create_business_manager(self, name: str, doc_id: str = "739201948201938") -> str:
        variables = {"input": {"name": name, "vertical": "ADVERTISING", "client_mutation_id": "1"}}
        response = await self.session.send_post_request(self.graphql_url, doc_id, variables)
        
        data = response.get("data", {})
        bm_data = data.get("business_manager_create", {}).get("business", {}) if isinstance(data, dict) else {}
        bm_id = bm_data.get("id")
        
        if response.get("errors") or not bm_id:
            log.error("[%s] Business Manager creation failed", self.session.profile.name)
            raise RemoteRequestError("Failed to create BM")
        return str(bm_id)

    async def create_ad_account(self, business_id: str, account_name: str, doc_id: str = "684920184730193") -> str:
        variables = {
            "input": {
                "client_mutation_id": "1",
                "business_id": str(business_id),
                "name": account_name,
                "currency": "USD",
                "timezone_id": 1
            }
        }
        response = await self.session.send_post_request(self.graphql_url, doc_id, variables)
        
        data = response.get("data", {})
        acc_data = data.get("ad_account_create", {}).get("ad_account", {}) if isinstance(data, dict) else {}
        account_id = acc_data.get("id")
        
        if response.get("errors") or not account_id:
            log.error("[%s] Ad Account creation failed", self.session.profile.name)
            raise RemoteRequestError("Failed to create Ad Account")
        return str(account_id)

    async def link_payment_credential(self, target_id: str, credential_id: str, country: str = "US", zip_code: str = "10001", doc_id: str = "582930491827304") -> bool:
        clean_target_id = str(target_id).strip()
        if clean_target_id.startswith("act_"):
            clean_target_id = clean_target_id[4:]
            
        variables = {
            "input": {
                "client_mutation_id": "1",
                "ad_account_id": f"act_{clean_target_id}",
                "credential_id": credential_id,
                "billing_address": {"country_code": country, "zip": zip_code}
            }
        }
        response = await self.session.send_post_request(self.graphql_url, doc_id, variables)
        
        data = response.get("data", {})
        success_payment = data.get("payment_credential_link") if isinstance(data, dict) else None
        
        if response.get("errors") or not success_payment:
            log.error("[%s] Payment credential link failed", self.session.profile.name)
            return False
        return True


async def run_autoreg_workflow(profile_data: WebProfile, semaphore: asyncio.Semaphore) -> None:
    """Безопасный запуск воркфлоу через Семафор (Ограничитель потоков)"""
    async with semaphore:
        async with WebSessionManager(profile_data) as session:
            try:
                await session.check_connection()
                controller = BusinessLogicController(session)
                
                bm_id = await controller.create_business_manager(name="ReMask_BM")
                log.info("[%s] Успешно создан БМ: %s", profile_data.name, bm_id)
                
                rk_id = await controller.create_ad_account(business_id=bm_id, account_name="ReMask_RK")
                log.info("[%s] Успешно создан РК: %s", profile_data.name, rk_id)

            except AutomationError as exc:
                log.error("[%s] Workflow failed: %s", profile_data.name, exc)
            except Exception:
                log.exception("[%s] Unexpected worker error", profile_data.name)


async def main() -> None:
    semaphore = asyncio.Semaphore(30)
    
    accounts = [
        WebProfile(name="Acc_1", cookies={"c_user": "1", "xs": "s1"}, proxy="http://login:pass@ip:port", user_agent="Mozilla/5.0..."),
        WebProfile(name="Acc_2", cookies={"c_user": "2", "xs": "s2"}, proxy="http://login:pass@ip:port", user_agent="Mozilla/5.0..."),
        WebProfile(name="Acc_3", cookies={"c_user": "3", "xs": "s3"}, proxy="http://login:pass@ip:port", user_agent="Mozilla/5.0..."),
    ]

    await asyncio.gather(*(run_autoreg_workflow(acc, semaphore) for acc in accounts))


if __name__ == "__main__":
    asyncio.run(main())

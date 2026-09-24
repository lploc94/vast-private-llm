import hmac
import ipaddress
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import Settings
from app.db import SESSION_SECONDS, AdminSession, Database
from app.deploy import DeploymentService
from app.inference import InferenceProxy
from app.keys import KeyStore
from app.routes.admin_keys import register_admin_key_routes
from app.routes.deploy import register_deploy_routes
from app.routes.settings import register_settings_routes
from app.routes.v1 import register_v1_routes
from app.tunnel import TunnelManager
from app.vast import VastClient


COOKIE_NAME = "vastllm_session"
PACKAGE_DIR = Path(__file__).parent


class LoginRequest(BaseModel):
    password: str


def create_app(
    settings: Settings | None = None,
    vast_client=None,
    tunnel_manager=None,
    health_check=None,
    spawn_worker=None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.data_dir)
    db.bootstrap_admin(settings.admin_password)
    keys = KeyStore(db)
    proxy = InferenceProxy()
    vast_client = vast_client or (VastClient(api_key=settings.vast_api_key) if settings.vast_api_key else None)
    tunnel_manager = tunnel_manager or TunnelManager(settings.data_dir, settings.ssh_key_path)
    service_kwargs = {}
    if health_check is not None:
        service_kwargs["health_check"] = health_check
    if spawn_worker is not None:
        service_kwargs["spawn"] = spawn_worker
    deployment = DeploymentService(
        db, vast_client, tunnel_manager, proxy, settings.ssh_key_path,
        vast_api_key=settings.vast_api_key, **service_kwargs
    )

    @asynccontextmanager
    async def lifespan(_web: FastAPI):
        deployment.start_supervisor()
        yield
        deployment.close()
        await proxy.aclose()

    web = FastAPI(title="Vast Private LLM", docs_url=None, redoc_url=None, lifespan=lifespan)
    web.state.db = db
    web.state.settings = settings
    web.state.keys = keys
    web.state.proxy = proxy
    web.state.deployment = deployment
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    web.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    def require_local(request: Request) -> None:
        client_host = request.client.host if request.client else ""
        try:
            local_client = ipaddress.ip_address(client_host).is_loopback
        except ValueError:
            local_client = client_host == "testclient"
        if not local_client or request.url.hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            raise HTTPException(status_code=403, detail="Dashboard chỉ dùng trên máy này")

    def require_admin(request: Request) -> AdminSession:
        require_local(request)
        session = db.get_session(request.cookies.get(COOKIE_NAME))
        if session is None:
            raise HTTPException(status_code=401, detail="Admin login required")
        return session

    def require_csrf(request: Request, session: AdminSession = Depends(require_admin)) -> AdminSession:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(supplied, session.csrf_token):
            raise HTTPException(status_code=403, detail="Invalid CSRF token")
        return session

    @web.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @web.get("/login")
    def login_page(request: Request):
        require_local(request)
        return RedirectResponse("/", status_code=303)

    @web.get("/")
    def dashboard(request: Request):
        require_local(request)
        response = templates.TemplateResponse(request=request, name="dashboard.html", context={})
        if db.get_session(request.cookies.get(COOKIE_NAME)) is None:
            token, _session = db.create_session()
            response.set_cookie(
                COOKIE_NAME, token, max_age=SESSION_SECONDS,
                httponly=True, samesite="strict", secure=request.url.scheme == "https",
            )
        return response

    @web.post("/api/admin/login")
    def login(request: Request, payload: LoginRequest):
        require_local(request)
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(status_code=403, detail="Invalid origin")
        if not db.has_admin():
            raise HTTPException(status_code=503, detail="Admin bootstrap required")
        if not db.verify_admin(payload.password):
            raise HTTPException(status_code=401, detail="Invalid password")
        token, session = db.create_session()
        response = JSONResponse({"csrf_token": session.csrf_token})
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_SECONDS,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return response

    @web.get("/api/admin/session")
    def session(session: AdminSession = Depends(require_admin)) -> dict[str, str]:
        return {"csrf_token": session.csrf_token}

    @web.get("/api/admin/state")
    def admin_state(_session: AdminSession = Depends(require_admin)) -> dict[str, object]:
        return db.deployment_state()

    @web.post("/api/admin/logout")
    def logout(request: Request, _session: AdminSession = Depends(require_csrf)):
        db.delete_session(request.cookies.get(COOKIE_NAME))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME)
        return response

    register_admin_key_routes(web, keys, require_admin, require_csrf)
    register_v1_routes(web, keys, proxy)
    register_deploy_routes(web, deployment, require_admin, require_csrf)
    register_settings_routes(web, deployment, settings.data_dir, require_admin, require_csrf)

    return web


app = create_app()

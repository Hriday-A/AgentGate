"""The three vendor portals: sign-in, invoice list, invoice detail, PDF download.

All three share these routes. What differs per vendor and environment is
the Layout (labels, buttons, pagination, pop-ups) and the PDF template.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from . import data
from .pdfs import render_invoice_pdf
from .state import LOCK, get_env, templates

router = APIRouter(prefix="/e/{env_id}/{vendor}")


def vendor_cfg(vendor: str) -> dict:
    """Dependency: 404 for anything that isn't one of the three vendors."""
    if vendor not in data.VENDORS:
        raise HTTPException(404, "Unknown portal")
    return data.VENDORS[vendor]


def _context(request: Request, env: data.Env, vendor: str, **extra) -> dict:
    layout = env.layouts[vendor]
    return {
        "request": request,
        "env": env,
        "vendor": vendor,
        "v": data.VENDORS[vendor],
        "color": data.VENDORS[vendor]["color"],
        "layout": layout,
        "buttons": layout.buttons,
        "cookie_banner": layout.cookie_banner,
        "popup_key": f"{env.id}-{vendor}",
        **extra,
    }


def _status_text(inv: data.Invoice, layout: data.Layout) -> str:
    return layout.status_text["overdue" if inv.overdue else inv.status]


# --- sessions ----------------------------------------------------------------
# request.session is a signed cookie (Starlette SessionMiddleware) holding
# {"auth": {"<env_id>:<vendor>": <authenticated requests used>}}.

def _auth_key(env_id: str, vendor: str) -> str:
    return f"{env_id}:{vendor}"


def _login_redirect(request: Request, env_id: str, vendor: str, expired: bool = False) -> RedirectResponse:
    url = request.url_for("login", env_id=env_id, vendor=vendor).include_query_params(
        next=request.url.path + (f"?{request.url.query}" if request.url.query else ""),
        **({"expired": 1} if expired else {}),
    )
    return RedirectResponse(str(url), status_code=303)


def require_login(request: Request, env: data.Env, vendor: str) -> RedirectResponse | None:
    """Return a redirect to the login page if the visitor isn't (or is no longer) signed in."""
    auth: dict = request.session.get("auth", {})
    key = _auth_key(env.id, vendor)
    if key not in auth:
        return _login_redirect(request, env.id, vendor)
    limit = env.layouts[vendor].session_max_requests
    if limit is not None:
        # Expire by page loads rather than wall-clock time, so a slow model and a
        # fast model hit the timeout at the same point in the task.
        if auth[key] >= limit:
            del auth[key]
            request.session["auth"] = auth
            return _login_redirect(request, env.id, vendor, expired=True)
        auth[key] += 1
        request.session["auth"] = auth
    return None


def _safe_next(next_url: str | None, env_id: str, vendor: str) -> str | None:
    """Only follow `next` back into the same portal (no open redirects)."""
    if next_url and next_url.startswith(f"/e/{env_id}/{vendor}/") and "//" not in next_url:
        return next_url
    return None


# --- routes ------------------------------------------------------------------

@router.get("/login", name="login")
def login_form(request: Request, vendor: str, env: data.Env = Depends(get_env), _=Depends(vendor_cfg)):
    expired = request.query_params.get("expired") == "1"
    return templates.TemplateResponse(request, "login.html", _context(
        request, env, vendor, error=None, expired=expired, next=request.query_params.get("next", "")))


@router.post("/login")
async def login_submit(request: Request, vendor: str, env: data.Env = Depends(get_env), _=Depends(vendor_cfg)):
    form = await request.form()
    username = str(form.get("username", "")).strip().lower()
    password = str(form.get("password", ""))
    if username == data.CREDENTIALS["username"] and password == data.CREDENTIALS["password"]:
        auth = request.session.get("auth", {})
        auth[_auth_key(env.id, vendor)] = 0
        request.session["auth"] = auth
        target = _safe_next(str(form.get("next", "")), env.id, vendor) or str(
            request.url_for("invoices", env_id=env.id, vendor=vendor))
        return RedirectResponse(target, status_code=303)
    return templates.TemplateResponse(request, "login.html", _context(
        request, env, vendor, error="Invalid username or password.", expired=False, next=form.get("next", "")),
        status_code=401)


@router.get("/logout", name="logout")
def logout(request: Request, vendor: str, env: data.Env = Depends(get_env), _=Depends(vendor_cfg)):
    auth = request.session.get("auth", {})
    auth.pop(_auth_key(env.id, vendor), None)
    request.session["auth"] = auth
    return RedirectResponse(str(request.url_for("login", env_id=env.id, vendor=vendor)), status_code=303)


@router.get("/invoices", name="invoices")
def invoices(request: Request, vendor: str, page: int = 1, env: data.Env = Depends(get_env),
             _=Depends(vendor_cfg)):
    if (redirect := require_login(request, env, vendor)) is not None:
        return redirect
    layout = env.layouts[vendor]
    ordered = sorted(env.invoices[vendor], key=lambda i: i.issue_date, reverse=layout.newest_first)
    size = layout.page_size or len(ordered)
    pages = max(1, -(-len(ordered) // size))  # ceiling division
    page = min(max(page, 1), pages)
    rows = [
        {
            "number": inv.number,
            "issue_date": data.fmt_date(inv.issue_date, vendor),
            "due_date": data.fmt_date(inv.due_date, vendor),
            "amount": data.fmt_money(inv.amount_cents, vendor),
            "status": _status_text(inv, layout),
            "href": request.url_for("invoice_detail", env_id=env.id, vendor=vendor, number=inv.number),
        }
        for inv in ordered[(page - 1) * size: page * size]
    ]
    return templates.TemplateResponse(request, "invoices.html", _context(
        request, env, vendor, rows=rows, page=page, pages=pages, total=len(ordered),
        promo_modal=layout.promo_modal))


@router.get("/invoices/{number}", name="invoice_detail")
def invoice_detail(request: Request, vendor: str, number: str, env: data.Env = Depends(get_env),
                   _=Depends(vendor_cfg)):
    if (redirect := require_login(request, env, vendor)) is not None:
        return redirect
    inv = env.find_invoice(vendor, number)
    if inv is None:
        raise HTTPException(404, "Invoice not found")
    # The portal shows a summary only. Line items, tax, shipping and PO number live in the PDF.
    return templates.TemplateResponse(request, "invoice.html", _context(
        request, env, vendor, inv=inv,
        issue=data.fmt_date(inv.issue_date, vendor), due=data.fmt_date(inv.due_date, vendor),
        total=data.fmt_money(inv.amount_cents, vendor), status=_status_text(inv, env.layouts[vendor])))


@router.get("/invoices/{number}/pdf", name="invoice_pdf")
def invoice_pdf(request: Request, vendor: str, number: str, env: data.Env = Depends(get_env),
                _=Depends(vendor_cfg)):
    if (redirect := require_login(request, env, vendor)) is not None:
        return redirect
    inv = env.find_invoice(vendor, number)
    if inv is None:
        raise HTTPException(404, "Invoice not found")
    with LOCK:
        pdf = env.pdf_cache.get(number)
        if pdf is None:
            pdf = env.pdf_cache[number] = render_invoice_pdf(inv, env.seed)
    # "attachment" makes browsers download the file instead of opening a viewer.
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{quote(number)}.pdf"'})

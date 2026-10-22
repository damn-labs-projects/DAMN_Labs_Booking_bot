import html
import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import settings
from app.legal import legal_pages

router = APIRouter()

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin:0; padding:32px 20px; background:#f5f6f8; color:#111;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; }
.wrap { max-width:560px; margin:0 auto; }
.card { background:#fff; border:1px solid #e4e6eb; border-radius:14px;
  padding:24px; box-shadow:0 1px 3px rgba(0,0,0,.05); }
h1 { font-size:20px; margin:0 0 8px; }
p { line-height:1.5; color:#444; margin:8px 0; }
.muted { color:#777; font-size:14px; }
.btn { display:inline-block; margin-top:16px; padding:12px 22px; border:0;
  border-radius:10px; background:#2563eb; color:#fff; font-size:16px;
  font-weight:600; cursor:pointer; text-decoration:none; }
.btn:disabled { background:#a5b4fc; cursor:not-allowed; }
.err { background:#fef2f2; border:1px solid #fecaca; color:#991b1b;
  border-radius:10px; padding:14px; margin-top:16px; }
footer { margin-top:20px; font-size:13px; color:#6b7280; line-height:1.9; }
footer a { color:#2563eb; text-decoration:none; }
ul { line-height:1.7; color:#444; padding-left:20px; }
li { margin:4px 0; }
strong { color:#111; }
pre { white-space:pre-wrap; word-break:break-word; font:inherit; margin:0; }
"""


def _shell(title: str, body: str, head: str = "") -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(title)}</title>"
        f"<style>{_CSS}</style>{head}</head><body><div class=\"wrap\">"
        f"{body}"
        "<footer>"
        "<a href=\"/offer\">Публичная оферта</a> · "
        "<a href=\"/privacy\">Политика конфиденциальности</a> · "
        "<a href=\"/contacts\">Контакты</a>"
        "</footer></div></body></html>"
    )


def build_pay_link(tenant_id: str) -> str:
    base = (settings.BASE_WEBHOOK_URL or "").rstrip("/")
    return f"{base}/pay?tenant_id={html.escape(tenant_id, quote=True)}"


def payment_is_configured() -> bool:
    return bool(settings.PADDLE_PRICE_ID and settings.PADDLE_PUBLIC_KEY)


@router.get("/", response_class=HTMLResponse)
async def landing_page():
    contact = (settings.SUPPORT_CONTACT or "").strip()
    body = (
        "<div class=\"card\">"
        "<h1>D.A.M.N Booking Hub</h1>"
        "<p><strong>Онлайн-запись клиентов прямо в Telegram.</strong></p>"
        "<p>Сервис для малого бизнеса: салоны, клиники, студии, авто-сервисы и "
        "школы. Подключите бота за несколько минут и начните принимать записи "
        "без разработчиков и таблиц.</p>"
        "<ul>"
        "<li>Запись онлайн 24/7 — клиент выбирает услугу, дату и время сам</li>"
        "<li>Услуги, цены, расписание и рабочие часы — настраиваются в пару кликов</li>"
        "<li>Уведомления владельцу о каждой новой записи</li>"
        "<li>Панель владельца: записи, услуги, тексты и брендинг бота</li>"
        "<li>Пробный период без оплаты, затем подписка на месяц</li>"
        "</ul>"
        "<a class=\"btn\" href=\"/contacts\">Подключить</a>"
        + (f"<p class=\"muted\">{html.escape(contact)}</p>" if contact else "")
        + "</div>"
    )
    return _shell("D.A.M.N Booking Hub — онлайн-запись в Telegram", body)


@router.get("/pay", response_class=HTMLResponse)
async def pay_page(request: Request, tenant_id: str = ""):
    tenant_id = (tenant_id or "").strip()[:128]

    if (settings.PAYMENT_MODE or "manual") != "paddle":
        contact = settings.SUPPORT_CONTACT or "Контакты в разделе «Контакты»"
        business = f"<p class=\"muted\">Бизнес: {html.escape(tenant_id)}</p>" if tenant_id else ""
        return _shell(
            "Оплата",
            "<div class=\"card\"><h1>Оплата онлайн временно выключена</h1>"
            "<p>Подписка продлевается вручную: напишите нам, и мы включим "
            "ваш доступ. Доступ уже можно продлить из панели администратора.</p>"
            f"<p class=\"muted\">{html.escape(contact)}</p>"
            f"{business}"
            "</div>",
        )

    if not payment_is_configured():
        return _shell(
            "Оплата",
            "<div class=\"card\"><h1>Оплата временно недоступна</h1>"
            "<p>Платёжная система не настроена. Пожалуйста, обратитесь в "
            "поддержку — мы включим оплату вручную.</p>"
            f"<p class=\"muted\">{html.escape(settings.SUPPORT_CONTACT or 'Контакты в разделе «Контакты»')}</p>"
            "</div>",
        )

    if not tenant_id:
        return _shell(
            "Оплата",
            "<div class=\"card\"><h1>Не указан бизнес</h1>"
            "<p>Откройте ссылку на оплату из сообщения бота — она содержит "
            "идентификатор вашего бизнеса.</p></div>",
        )

    config = {
        "token": settings.PADDLE_PUBLIC_KEY,
        "priceId": settings.PADDLE_PRICE_ID,
        "tenantId": tenant_id,
        "sandbox": settings.PADDLE_PUBLIC_KEY.startswith("test_"),
    }
    config_json = json.dumps(config).replace("<", "\\u003c")

    body = (
        "<div class=\"card\">"
        "<h1>Оплата подписки</h1>"
        "<p>Подключите тариф для продолжения работы вашего бизнеса в "
        "Telegram-боте. Оплата проходит через Paddle.</p>"
        "<button class=\"btn\" id=\"payBtn\" type=\"button\">💳 Оплатить</button>"
        "<div class=\"err\" id=\"err\" style=\"display:none\"></div>"
        f"<p class=\"muted\">Бизнес: {html.escape(tenant_id)}</p>"
        "</div>"
        "<script>"
        f"const PAY_CONFIG = {config_json};"
        "const errEl = document.getElementById('err');"
        "function showErr(m){errEl.textContent=m;errEl.style.display='block';}"
        "function ok(){errEl.style.display='none';"
        "  document.getElementById('payBtn').textContent = '✅ Оплата получена';"
        "  document.querySelector('.card').insertAdjacentHTML('beforeend',"
        "    '<p class=\"muted\">Платёж получен. Подписка активируется в течение "
        "минуты — вернитесь в бота.</p>');}"
        "if (!PAY_CONFIG.token || !PAY_CONFIG.priceId) {"
        "  showErr('Оплата не настроена. Напишите в поддержку.');"
        "  document.getElementById('payBtn').disabled = true;"
        "} else {"
        "  function describeError(e) {"
        "    if (!e) { return 'неизвестная ошибка'; }"
        "    if (typeof e === 'string') { return e; }"
        "    var detail = e.detail || (e.error && (e.error.detail || e.error.message));"
        "    if (detail) { return detail; }"
        "    try { return JSON.stringify(e); } catch (err) { return String(e); }"
        "  }"
        "  if (PAY_CONFIG.sandbox) { Paddle.Environment.set('sandbox'); }"
        "  Paddle.Initialize({"
        "    token: PAY_CONFIG.token,"
        "    eventCallback: function (event) {"
        "      console.log('paddle event', event);"
        "      if (!event) { return; }"
        "      if (event.name === 'checkout.completed') { ok(); }"
        "      if (event.name === 'checkout.error') {"
        "        showErr('Paddle: ' + describeError(event.data || event));"
        "      }"
        "    }"
        "  });"
        "  document.getElementById('payBtn').addEventListener('click', function(){"
        "    showErr('Проверяем настройки оплаты…');"
        "    Paddle.PricePreview({"
        "      items: [{ priceId: PAY_CONFIG.priceId, quantity: 1 }]"
        "    }).then(function(){"
        "      errEl.style.display = 'none';"
        "      Paddle.Checkout.open({"
        "        items: [{ priceId: PAY_CONFIG.priceId, quantity: 1 }],"
        "        customData: { tenant_id: PAY_CONFIG.tenantId },"
        "        settings: { displayMode: 'overlay' }"
        "      });"
        "    }).catch(function(e){"
        "      console.error('paddle preview failed', e);"
        "      showErr('Paddle не принимает оплату: ' + describeError(e)"
        "        + ' (код: ' + ((e && e.error && e.error.code) || 'нет') + ')');"
        "    });"
        "  });"
        "}"
        "</script>"
    )
    sdk = (
        "<script src=\"https://cdn.paddle.com/paddle/v2/paddle.js\"></script>"
    )
    return _shell(
        "Оплата — D.A.M.N Booking Hub",
        body,
        head=sdk,
    )


def _legal_response(key: str) -> HTMLResponse:
    title, text = legal_pages()[key]
    body = (
        "<div class=\"card\"><h1>" + html.escape(title) + "</h1>"
        "<pre>" + html.escape(text) + "</pre></div>"
    )
    return _shell(f"{title} — D.A.M.N Booking Hub", body)


@router.get("/offer", response_class=HTMLResponse)
async def offer_page():
    return _legal_response("offer")


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_page():
    return _legal_response("privacy")


@router.get("/contacts", response_class=HTMLResponse)
async def contacts_page():
    return _legal_response("contacts")


@router.get("/ping", response_class=HTMLResponse)
async def ping_page():
    return _shell(
        "D.A.M.N Booking Hub",
        "<div class=\"card\"><h1>D.A.M.N Booking Hub</h1>"
        "<p>Сервис работает.</p></div>",
    )

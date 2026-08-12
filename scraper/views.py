import json
import re

from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_http_methods

from .forms import ScrapeForm
from .services.amazon_scraper import ProductData, build_csv_content, product_to_dict
from .services.scraper import scrape_product, scrape_variant as fetch_variant

SESSION_KEY = "last_product"


def _product_from_session(request) -> ProductData | None:
    raw = request.session.get(SESSION_KEY)
    if not raw:
        return None
    return ProductData(**raw)


def _save_product_to_session(request, product: ProductData) -> None:
    request.session[SESSION_KEY] = product_to_dict(product)


def home(request):
    product = _product_from_session(request)
    form = ScrapeForm()
    return render(
        request,
        "scraper/home.html",
        {
            "form": form,
            "product": product,
            "product_json": json.dumps(product_to_dict(product)) if product else None,
        },
    )


@require_http_methods(["POST"])
def scrape(request):
    form = ScrapeForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "scraper/home.html",
            {"form": form, "product": None, "product_json": None},
        )

    url = form.cleaned_data["url"]
    try:
        product = scrape_product(url)
    except Exception as error:
        form.add_error(None, str(error))
        return render(
            request,
            "scraper/home.html",
            {"form": form, "product": None, "product_json": None},
        )

    _save_product_to_session(request, product)
    return render(
        request,
        "scraper/home.html",
        {
            "form": ScrapeForm(initial={"url": url}),
            "product": product,
            "product_json": json.dumps(product_to_dict(product)),
            "scraped": True,
        },
    )


@require_http_methods(["POST"])
def scrape_variant(request):
    product_id = request.POST.get("asin", "").strip().upper()
    current = _product_from_session(request)
    store = current.store if current else "amazon"

    if store == "walmart":
        product_id = request.POST.get("asin", "").strip()
        if not re.fullmatch(r"\d+", product_id):
            return JsonResponse({"error": "Invalid Walmart item ID."}, status=400)
    elif not re.fullmatch(r"[A-Z0-9]{10}", product_id):
        return JsonResponse({"error": "Invalid ASIN."}, status=400)

    try:
        product = fetch_variant(store, product_id, current.url if current else None)
    except Exception as error:
        return JsonResponse({"error": str(error)}, status=400)

    _save_product_to_session(request, product)
    html = render_to_string("scraper/product_card.html", {"product": product}, request=request)
    return JsonResponse({"html": html})


def download_json(request):
    product = _product_from_session(request)
    if not product:
        return redirect("scraper:home")

    product_id = product.asin or "data"
    filename = f"product_{product_id}.json"
    response = HttpResponse(
        json.dumps(product_to_dict(product), indent=2, ensure_ascii=False),
        content_type="application/json",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def download_csv(request):
    product = _product_from_session(request)
    if not product:
        return redirect("scraper:home")

    product_id = product.asin or "data"
    filename = f"product_{product_id}.csv"
    response = HttpResponse(build_csv_content(product), content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

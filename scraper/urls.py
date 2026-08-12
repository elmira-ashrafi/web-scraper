from django.urls import path

from . import views

app_name = "scraper"

urlpatterns = [
    path("", views.home, name="home"),
    path("scrape/", views.scrape, name="scrape"),
    path("scrape/variant/", views.scrape_variant, name="scrape_variant"),
    path("download/json/", views.download_json, name="download_json"),
    path("download/csv/", views.download_csv, name="download_csv"),
]

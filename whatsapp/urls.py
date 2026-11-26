from django.urls import path

from . import views

urlpatterns = [
    path("webhooks/meta/", views.meta_webhook, name="meta-webhook"),
    path("api/v1/messages", views.api_create_message, name="api-create-message"),
    path("api/v1/messages/<int:pk>", views.api_get_message, name="api-get-message"),
]

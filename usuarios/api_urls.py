from django.urls import path

from .views import DireccionListCreateView


urlpatterns = [
    path('direcciones/', DireccionListCreateView.as_view(), name='direcciones'),
]
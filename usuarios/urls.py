from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import CerrarSesionView, InicioSesionView, MiPerfilView, RegistroView


urlpatterns = [
    path('registro/', RegistroView.as_view(), name='registro'),
    path('iniciar-sesion/', InicioSesionView.as_view(), name='iniciar-sesion'),
    path('renovar-token/', TokenRefreshView.as_view(), name='renovar-token'),
    path('cerrar-sesion/', CerrarSesionView.as_view(), name='cerrar-sesion'),
    path('mi-perfil/', MiPerfilView.as_view(), name='mi-perfil'),
]

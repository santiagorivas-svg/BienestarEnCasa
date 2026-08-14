from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .serializers import CerrarSesionSerializer, InicioSesionSerializer, PerfilSerializer, RegistroSerializer


class RegistroView(generics.CreateAPIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = RegistroSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        perfil = serializer.save()
        return Response(PerfilSerializer(perfil).data, status=status.HTTP_201_CREATED)


class InicioSesionView(TokenObtainPairView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = InicioSesionSerializer


class CerrarSesionView(APIView):
    def post(self, request):
        serializer = CerrarSesionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MiPerfilView(generics.RetrieveAPIView):
    serializer_class = PerfilSerializer

    def get_object(self):
        return self.request.user.perfil

from django.conf import settings
from django.db import models


class PerfilUsuario(models.Model):
    class Rol(models.TextChoices):
        CLIENTE = 'CLIENTE', 'Cliente'
        PROVEEDOR = 'PROVEEDOR', 'Proveedor'

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='perfil',
    )
    rol = models.CharField(max_length=12, choices=Rol.choices)
    telefono = models.CharField(max_length=20, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'perfil de usuario'
        verbose_name_plural = 'perfiles de usuario'

    def __str__(self):
        return f'{self.usuario.email or self.usuario.username} ({self.get_rol_display()})'

class Direccion(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='direcciones',
    )
    direccion = models.CharField(max_length=255)
    ciudad = models.CharField(max_length=100)
    barrio_sector = models.CharField(max_length=100)
    informacion_complementaria = models.TextField(blank=True)
    es_principal = models.BooleanField(default=False)
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'dirección'
        verbose_name_plural = 'direcciones'

    def __str__(self):
        return f'{self.direccion}, {self.barrio_sector} - {self.ciudad}'
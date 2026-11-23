from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from .models import SolicitudReserva
from usuarios.models import PerfilProveedor
from catalogo.models import Servicio  # Ajusta la importación según la ubicación de tu modelo Servicio
from usuarios.validators import validar_horario_reserva
from .limites import validar_disponibilidad_proveedor, validar_limites_cliente

class SolicitudReservaSerializer(serializers.ModelSerializer):
    nombre_proveedor = serializers.SerializerMethodField()
    cliente_nombre = serializers.SerializerMethodField()
    cliente_telefono = serializers.SerializerMethodField()
    cliente_direccion_principal = serializers.SerializerMethodField()
    categoria_nombre = serializers.SerializerMethodField()
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    hora_fin = serializers.SerializerMethodField()

    class Meta:
        model = SolicitudReserva
        fields = [
            'id', 'cliente', 'cliente_nombre', 'cliente_telefono', 'cliente_direccion_principal',
            'proveedor', 'nombre_proveedor', 'servicio', 'categoria_nombre', 'estado_display',
            'nombre_servicio_copia', 'precio_copia', 'duracion_minutos_copia',
            'fecha_solicitada', 'hora_solicitada', 'hora_fin', 'direccion_atencion',
            'barrio_sector', 'informacion_complementaria', 'observaciones',
            'estado', 'motivo_rechazo_cancelacion', 'creado_en', 'actualizado_en'
        ]
        # El estado y el motivo solo cambian con las acciones aceptar / rechazar / cancelar,
        # para que un cliente no pueda marcar su propia solicitud como aceptada con un PATCH.
        read_only_fields = [
            'cliente', 'nombre_servicio_copia', 'precio_copia',
            'duracion_minutos_copia', 'estado', 'motivo_rechazo_cancelacion',
            'creado_en', 'actualizado_en'
        ]
        extra_kwargs = {
            # HU-012: el servicio es obligatorio; el proveedor se toma del servicio
            # (si se envía, debe coincidir con el del servicio).
            'servicio': {'required': True, 'allow_null': False},
            'proveedor': {'required': False},
        }

    def get_hora_fin(self, obj):
        """HU-012 (10) / HU-019 (2): fin = hora de inicio + duración conservada."""
        if not obj.fecha_solicitada or not obj.hora_solicitada:
            return None
        inicio = datetime.combine(obj.fecha_solicitada, obj.hora_solicitada)
        return (inicio + timedelta(minutes=obj.duracion_minutos_copia or 0)).strftime('%H:%M:%S')

    def get_cliente_telefono(self, obj):
        perfil = getattr(obj.cliente, 'perfil', None) if obj.cliente else None
        return getattr(perfil, 'telefono', '') or ''

    def get_cliente_direccion_principal(self, obj):
        """Dirección marcada como principal en el perfil del cliente (o None).
        Se recorre .all() para aprovechar el prefetch de la vista."""
        if not obj.cliente:
            return None
        principal = next((d for d in obj.cliente.direcciones.all() if d.es_principal), None)
        if principal is None:
            return None
        return {
            'direccion': principal.direccion,
            'barrio': principal.barrio,
            'ciudad': principal.ciudad,
            'complemento': principal.complemento or '',
        }

    def get_categoria_nombre(self, obj):
        servicio = obj.servicio
        categoria = getattr(servicio, 'categoria', None) if servicio else None
        return getattr(categoria, 'nombre', '') or ''

    def get_cliente_nombre(self, obj):
        if not obj.cliente:
            return "Usuario"

        # 1. Nombre completo desde el modelo User de Django
        first_name = getattr(obj.cliente, 'first_name', '')
        last_name = getattr(obj.cliente, 'last_name', '')
        nombre_completo = f"{first_name} {last_name}".strip()
        if nombre_completo:
            return nombre_completo

        # 2. Nombre completo desde PerfilUsuario si existe
        perfil = getattr(obj.cliente, 'perfil', None)
        if perfil:
            nom_p = getattr(perfil, 'nombres', '')
            ape_p = getattr(perfil, 'apellidos', '')
            completo_perfil = f"{nom_p} {ape_p}".strip()
            if completo_perfil:
                return completo_perfil

        # 3. Nombre basado en email
        email = getattr(obj.cliente, 'email', '')
        if email:
            return email.split('@')[0].capitalize()

        return getattr(obj.cliente, 'username', 'Usuario')

    def get_nombre_proveedor(self, obj):
        if not obj.proveedor or not hasattr(obj.proveedor, 'perfil_usuario'):
            return "Proveedor no asignado"
        u = obj.proveedor.perfil_usuario.usuario
        return f"{u.first_name} {u.last_name}".strip() or u.email

    def validate(self, attrs):
        if self.instance is None:
            servicio = attrs.get('servicio')
            proveedor_enviado = attrs.get('proveedor')
            if servicio and proveedor_enviado and proveedor_enviado != servicio.proveedor:
                raise serializers.ValidationError({
                    'proveedor': 'El proveedor no corresponde al servicio seleccionado.'
                })
        else:
            # Una solicitud creada conserva su servicio y su proveedor (HU-012 / HU-014):
            # cambiarlos la movería a otro proveedor sin actualizar las condiciones conservadas.
            for campo in ('servicio', 'proveedor'):
                if campo in attrs and attrs[campo] != getattr(self.instance, campo):
                    raise serializers.ValidationError({
                        campo: 'No se puede cambiar en una solicitud ya creada.'
                    })
            if self.instance.estado in SolicitudReserva.ESTADOS_FINALES:
                raise serializers.ValidationError({
                    'estado': 'La solicitud ya está finalizada y no se puede modificar.'
                })

        campos_horario = {'fecha_solicitada', 'hora_solicitada', 'servicio'}
        if self.instance is None or campos_horario.intersection(attrs):
            fecha = attrs.get('fecha_solicitada', getattr(self.instance, 'fecha_solicitada', None))
            hora = attrs.get('hora_solicitada', getattr(self.instance, 'hora_solicitada', None))
            servicio = attrs.get('servicio', getattr(self.instance, 'servicio', None))
            if (
                self.instance is None
                and servicio
                and str(servicio.estado).upper() != Servicio.Estado.ACTIVO
            ):
                raise serializers.ValidationError({
                    'servicio': 'Este servicio está inactivo y no acepta nuevas solicitudes.'
                })
            proveedor = (
                servicio.proveedor if servicio
                else getattr(self.instance, 'proveedor', None)
            )
            if self.instance and 'servicio' not in attrs:
                duracion = self.instance.duracion_minutos_copia
            else:
                duracion = servicio.duracion_minutos if servicio else 0
            validar_horario_reserva(proveedor, fecha, hora, duracion)

            # Límites por cliente: sin cruces de horario y máximo 3 solicitudes por día (solo al crear)
            if self.instance is None:
                request = self.context.get('request')
                cliente = getattr(request, 'user', None)
                if cliente is not None and cliente.is_authenticated:
                    validar_limites_cliente(cliente, fecha, hora, duracion)

            # Después de las reglas del cliente (su mensaje es más útil para quien reserva):
            # el proveedor no puede tener dos reservas activas que se crucen, aunque sean de
            # servicios o clientes distintos. Al cambiar la hora se ignora la propia solicitud.
            validar_disponibilidad_proveedor(
                proveedor, fecha, hora, duracion,
                excluir_id=getattr(self.instance, 'pk', None),
            )

        return super().validate(attrs)

    def create(self, validated_data):
        servicio = validated_data.get('servicio')

        # Si el campo servicio vino como un entero/ID, obtenemos el objeto de la BD
        if isinstance(servicio, int):
            servicio = Servicio.objects.filter(id=servicio).first()

        # Si el objeto servicio existe, copiamos sus atributos; de lo contrario asignamos valores por defecto no nulos
        if servicio:
            validated_data['nombre_servicio_copia'] = getattr(servicio, 'nombre', 'Servicio General')
            validated_data['precio_copia'] = getattr(servicio, 'precio', 0.00)
            validated_data['duracion_minutos_copia'] = getattr(servicio, 'duracion_minutos', 60)
        else:
            validated_data['nombre_servicio_copia'] = 'Servicio General'
            validated_data['precio_copia'] = 0.00
            validated_data['duracion_minutos_copia'] = 60

        # Evita la condición de carrera: dos peticiones simultáneas del mismo cliente se
        # serializan bloqueando la fila del usuario; la segunda re-valida con la primera ya guardada.
        request = self.context.get('request')
        cliente = getattr(request, 'user', None)
        if cliente is None or not cliente.is_authenticated:
            return super().create(validated_data)

        with transaction.atomic():
            # Bloquea también al proveedor: dos clientes distintos que reservan la misma
            # hora al mismo tiempo se atienden en orden y el segundo ve la reserva del primero.
            proveedor = servicio.proveedor if servicio else None
            if proveedor is not None:
                PerfilProveedor.objects.select_for_update().get(pk=proveedor.pk)
            get_user_model().objects.select_for_update().get(pk=cliente.pk)
            try:
                validar_limites_cliente(
                    cliente,
                    validated_data.get('fecha_solicitada'),
                    validated_data.get('hora_solicitada'),
                    validated_data['duracion_minutos_copia'],
                )
                validar_disponibilidad_proveedor(
                    proveedor,
                    validated_data.get('fecha_solicitada'),
                    validated_data.get('hora_solicitada'),
                    validated_data['duracion_minutos_copia'],
                )
            except ValidationError as exc:
                # Misma forma de respuesta que cuando el error sale de validate(): {campo: [mensaje]}
                raise ValidationError(serializers.as_serializer_error(exc))
            return super().create(validated_data)


class MotivoSerializer(serializers.Serializer):
    """Motivo obligatorio para rechazar o cancelar una solicitud."""
    motivo = serializers.CharField(
        max_length=500, trim_whitespace=True,
        error_messages={
            'required': 'Debes escribir el motivo.',
            'blank': 'Debes escribir el motivo.',
            'max_length': 'El motivo no puede tener más de 500 caracteres.',
        },
    )

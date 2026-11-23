"""Límites de solicitudes por cliente al crear una solicitud.

Reglas (solo cuentan las solicitudes "activas": PENDIENTE, SUGERIDA y ACEPTADA;
las CANCELADAS y COMPLETADAS no cuentan):

1. Un cliente no puede tener dos solicitudes que se crucen en el tiempo el mismo día,
   sin importar el proveedor ni el servicio. Una solicitud ocupa [hora, hora + duración);
   si una termina justo cuando empieza otra, no se cruzan.
2. Un cliente no puede tener más de MAX_SOLICITUDES_POR_DIA solicitudes para la misma fecha.
"""
from datetime import datetime, timedelta

from rest_framework.exceptions import ValidationError

from .models import SolicitudReserva

MAX_SOLICITUDES_POR_DIA = 3

ESTADOS_ACTIVOS = (
    SolicitudReserva.Estado.PENDIENTE,
    SolicitudReserva.Estado.SUGERIDA,
    SolicitudReserva.Estado.ACEPTADA,
)


def _intervalo(fecha, hora, duracion_minutos):
    inicio = datetime.combine(fecha, hora)
    # Mínimo 1 minuto: una duración de 0 no debe permitir duplicar exactamente la misma hora.
    return inicio, inicio + timedelta(minutes=max(duracion_minutos or 0, 1))


def validar_limites_cliente(cliente, fecha, hora, duracion_minutos):
    """Lanza ValidationError si el cliente se cruza con otra solicitud activa
    o ya alcanzó el máximo de solicitudes activas para esa fecha."""
    if not cliente or not fecha or not hora:
        return

    activas = SolicitudReserva.objects.filter(
        cliente=cliente,
        fecha_solicitada=fecha,
        estado__in=ESTADOS_ACTIVOS,
    )

    # Regla 1: sin solicitudes que se crucen en el tiempo
    inicio, fin = _intervalo(fecha, hora, duracion_minutos)
    for hora_existente, duracion_existente in activas.values_list(
        'hora_solicitada', 'duracion_minutos_copia'
    ):
        existente_inicio, existente_fin = _intervalo(fecha, hora_existente, duracion_existente)
        if inicio < existente_fin and fin > existente_inicio:
            raise ValidationError({
                'hora_solicitada': 'Ya tienes una solicitud en ese horario. Elige otra hora.'
            })

    # Regla 2: máximo de solicitudes por día
    if activas.count() >= MAX_SOLICITUDES_POR_DIA:
        raise ValidationError({
            'fecha_solicitada': 'Solo puedes hacer hasta 3 solicitudes por día.'
        })


def validar_disponibilidad_proveedor(proveedor, fecha, hora, duracion_minutos, excluir_id=None):
    """Lanza ValidationError si el proveedor ya tiene otra solicitud activa que se cruce
    con [hora, hora + duración) ese día, sin importar el servicio ni el cliente.

    Un proveedor no puede atender dos servicios a la vez. excluir_id sirve para
    ignorar la propia solicitud cuando se le cambia la hora (contrapropuesta)."""
    if not proveedor or not fecha or not hora:
        return

    activas = SolicitudReserva.objects.filter(
        proveedor=proveedor,
        fecha_solicitada=fecha,
        estado__in=ESTADOS_ACTIVOS,
    )
    if excluir_id:
        activas = activas.exclude(pk=excluir_id)

    inicio, fin = _intervalo(fecha, hora, duracion_minutos)
    for hora_existente, duracion_existente in activas.values_list(
        'hora_solicitada', 'duracion_minutos_copia'
    ):
        existente_inicio, existente_fin = _intervalo(fecha, hora_existente, duracion_existente)
        if inicio < existente_fin and fin > existente_inicio:
            raise ValidationError({
                'hora_solicitada': 'El proveedor ya tiene una reserva en ese horario. Elige otra hora.'
            })


def horarios_ocupados_proveedor(proveedor, desde):
    """Intervalos ocupados del proveedor desde una fecha (sin datos del cliente),
    para que el formulario de solicitud oculte las horas que ya no están libres."""
    ocupados = []
    for fecha, hora, duracion in SolicitudReserva.objects.filter(
        proveedor=proveedor,
        fecha_solicitada__gte=desde,
        estado__in=ESTADOS_ACTIVOS,
    ).order_by('fecha_solicitada', 'hora_solicitada').values_list(
        'fecha_solicitada', 'hora_solicitada', 'duracion_minutos_copia'
    ):
        inicio, fin = _intervalo(fecha, hora, duracion)
        ocupados.append({
            'fecha': fecha.isoformat(),
            'hora_inicio': inicio.strftime('%H:%M'),
            'hora_fin': fin.strftime('%H:%M'),
        })
    return ocupados

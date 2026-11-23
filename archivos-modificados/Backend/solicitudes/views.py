from django.shortcuts import render
from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
import datetime

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import SolicitudReserva
from .serializers import MotivoSerializer, SolicitudReservaSerializer
from usuarios.authentication import CookieJWTAuthentication
from usuarios.models import PerfilUsuario, PeriodoNoDisponible

class ClienteSolicitudesViewSet(viewsets.ModelViewSet):
    """
    Vista para la gestión y consulta de solicitudes/reservas (Clientes y Proveedores)
    """
    serializer_class = SolicitudReservaSerializer
    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    # Sin DELETE ni PUT: una solicitud no se borra (se cancela con motivo) para que cliente
    # y proveedor conserven su registro (HU-013 / HU-014). PATCH queda para la contrapropuesta.
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        usuario = self.request.user
        if not usuario or not usuario.is_authenticated:
            return SolicitudReserva.objects.none()

        perfil = getattr(usuario, 'perfil', None)
        rol = str(getattr(perfil, 'rol', '')).upper()

        # Si el usuario es PROVEEDOR, obtiene las solicitudes dirigidas a sus servicios o a su perfil
        if rol in ['PROVEEDOR', 'PRO'] or usuario.is_staff:
            perfil_profesional = getattr(perfil, 'perfil_profesional', None)
            
            # HU-014 (1 y 5): solo las solicitudes dirigidas a su cuenta
            filtro_proveedor = Q(proveedor__perfil_usuario__usuario=usuario)
            if perfil_profesional:
                filtro_proveedor |= Q(proveedor=perfil_profesional)
                
            return self._con_relaciones(
                SolicitudReserva.objects.filter(filtro_proveedor).distinct()
            )

        # Si es CLIENTE, retorna únicamente sus solicitudes creadas
        return self._con_relaciones(SolicitudReserva.objects.filter(cliente=usuario))

    @staticmethod
    def _con_relaciones(queryset):
        # Evita una consulta por fila al armar nombre, teléfono y dirección principal
        return (
            queryset
            .select_related('cliente__perfil', 'proveedor__perfil_usuario__usuario', 'servicio__categoria')
            .prefetch_related('cliente__direcciones')
            .order_by('-creado_en')
        )

    def perform_create(self, serializer):
        usuario = self.request.user
        perfil = getattr(usuario, 'perfil', None)
        rol = str(getattr(perfil, 'rol', '')).upper()

        # E1: Validar estrictamente que un proveedor no pueda crear solicitudes
        if rol in ['PROVEEDOR', 'PRO'] and not usuario.is_staff:
            raise PermissionDenied("Los usuarios con rol de Proveedor no tienen permitida la creación de solicitudes.")

        servicio = serializer.validated_data.get('servicio')
        proveedor_obj = None

        # 1. Obtener el proveedor desde el servicio de forma segura
        if servicio:
            if hasattr(servicio, 'proveedor') and servicio.proveedor:
                proveedor_obj = servicio.proveedor

        # 2. Asignar el User autenticado como cliente y el proveedor identificado
        serializer.save(
            cliente=usuario,
            proveedor=proveedor_obj
        )

    def perform_update(self, serializer):
        """PATCH de datos de la reserva (p. ej. la contrapropuesta de hora del proveedor).
        El estado ya no se cambia por aquí: usa las acciones aceptar / rechazar / cancelar."""
        serializer.save()

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        if not queryset.exists():
            return Response(
                {"mensaje": "No tienes solicitudes o reservas registradas.", "resultados": []},
                status=status.HTTP_200_OK
            )
        return super().list(request, *args, **kwargs)

    # ------------------------------------------------------------------
    # Respuesta del proveedor y cancelación: POST /api/solicitudes/<id>/<accion>/
    # ------------------------------------------------------------------
    @staticmethod
    def _es_proveedor_de(solicitud, usuario):
        proveedor = solicitud.proveedor
        return bool(proveedor and proveedor.perfil_usuario.usuario_id == usuario.id)

    def _cambiar_estado(self, solicitud, estado, motivo=None):
        solicitud.estado = estado
        campos = ['estado', 'actualizado_en']
        if motivo is not None:
            solicitud.motivo_rechazo_cancelacion = motivo
            campos.append('motivo_rechazo_cancelacion')
        solicitud.save(update_fields=campos)
        return Response(self.get_serializer(solicitud).data)

    @staticmethod
    def _motivo(request):
        serializer = MotivoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data['motivo']

    @action(detail=True, methods=['post'])
    def aceptar(self, request, pk=None):
        solicitud = self.get_object()
        if not self._es_proveedor_de(solicitud, request.user):
            raise PermissionDenied('Solo el proveedor de la solicitud puede aceptarla.')
        if solicitud.estado not in SolicitudReserva.ESTADOS_POR_RESPONDER:
            raise ValidationError({'estado': 'Solo se pueden aceptar solicitudes pendientes.'})
        return self._cambiar_estado(solicitud, SolicitudReserva.Estado.ACEPTADA)

    @action(detail=True, methods=['post'])
    def rechazar(self, request, pk=None):
        solicitud = self.get_object()
        if not self._es_proveedor_de(solicitud, request.user):
            raise PermissionDenied('Solo el proveedor de la solicitud puede rechazarla.')
        if solicitud.estado not in SolicitudReserva.ESTADOS_POR_RESPONDER:
            raise ValidationError({'estado': 'Solo se pueden rechazar solicitudes pendientes.'})
        motivo = self._motivo(request)
        return self._cambiar_estado(solicitud, SolicitudReserva.Estado.RECHAZADA, motivo)

    @action(detail=True, methods=['post'])
    def cancelar(self, request, pk=None):
        solicitud = self.get_object()
        if solicitud.cliente_id != request.user.id and not self._es_proveedor_de(solicitud, request.user):
            raise PermissionDenied('Solo el cliente o el proveedor de la solicitud pueden cancelarla.')
        if solicitud.estado in SolicitudReserva.ESTADOS_FINALES:
            raise ValidationError({'estado': 'Esta solicitud ya está finalizada y no se puede cancelar.'})
        motivo = self._motivo(request)
        return self._cambiar_estado(solicitud, SolicitudReserva.Estado.CANCELADA, motivo)


    # ------------------------------------------------------------------
    # HU-019: agenda del proveedor (reservas aceptadas en orden cronológico)
    # GET /api/solicitudes/agenda/?desde=AAAA-MM-DD&hasta=AAAA-MM-DD
    # ------------------------------------------------------------------
    @action(detail=False, methods=['get'])
    def agenda(self, request):
        perfil = getattr(request.user, 'perfil', None)
        if str(getattr(perfil, 'rol', '')).upper() not in ('PROVEEDOR', 'PRO'):
            raise PermissionDenied('Solo los proveedores tienen agenda.')

        def fecha_param(nombre):
            valor = request.query_params.get(nombre)
            if not valor:
                return None
            fecha = parse_date(valor)
            if fecha is None:
                raise ValidationError({nombre: 'Usa el formato AAAA-MM-DD.'})
            return fecha

        desde = fecha_param('desde') or timezone.localdate()
        hasta = fecha_param('hasta') or (desde + datetime.timedelta(days=30))
        if hasta < desde:
            raise ValidationError({'hasta': 'La fecha final no puede ser anterior a la inicial.'})
        if (hasta - desde).days > 92:
            raise ValidationError({'hasta': 'El rango máximo es de 92 días.'})

        # Siempre las del proveedor autenticado: no hay forma de pedir la agenda de otro
        reservas = self._con_relaciones(SolicitudReserva.objects.filter(
            proveedor__perfil_usuario__usuario=request.user,
            estado=SolicitudReserva.Estado.ACEPTADA,
            fecha_solicitada__gte=desde,
            fecha_solicitada__lte=hasta,
        )).order_by('fecha_solicitada', 'hora_solicitada')

        perfil_proveedor = getattr(perfil, 'perfil_profesional', None)
        periodos, horario = [], []
        if perfil_proveedor:
            periodos = [
                {'id': p.id, 'fecha_inicio': p.fecha_inicio.isoformat(),
                 'fecha_fin': p.fecha_fin.isoformat(), 'motivo': p.motivo}
                for p in PeriodoNoDisponible.objects.filter(
                    proveedor=perfil_proveedor, fecha_inicio__lte=hasta, fecha_fin__gte=desde
                ).order_by('fecha_inicio')
            ]
            horario = [
                {'dia_semana': d.dia_semana, 'dia': d.get_dia_semana_display(),
                 'hora_inicio': d.hora_inicio.strftime('%H:%M'), 'hora_fin': d.hora_fin.strftime('%H:%M')}
                for d in perfil_proveedor.disponibilidades.all().order_by('dia_semana')
            ]

        datos = self.get_serializer(reservas, many=True).data
        return Response({
            'desde': desde.isoformat(),
            'hasta': hasta.isoformat(),
            'reservas': datos,
            'mensaje': '' if datos else 'No tienes reservas aceptadas para mostrar.',
            'periodos_no_disponibles': periodos,
            'horario_semanal': horario,
        })

import datetime as dt
from datetime import date, datetime, time
from unittest import mock
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from catalogo.models import Categoria, Servicio
from usuarios.models import DisponibilidadProveedor, PerfilProveedor, PerfilUsuario
from . import limites
from .models import SolicitudReserva

User = get_user_model()
Estado = SolicitudReserva.Estado


class ReservaMismoDiaTests(APITestCase):
    def setUp(self):
        usuario_proveedor = get_user_model().objects.create_user(
            username='proveedor-horario@example.com', email='proveedor-horario@example.com', password='x'
        )
        perfil_proveedor = PerfilUsuario.objects.create(
            usuario=usuario_proveedor, rol=PerfilUsuario.Rol.PROVEEDOR
        )
        self.proveedor = PerfilProveedor.objects.create(
            perfil_usuario=perfil_proveedor, descripcion_profesional='Proveedor de pruebas'
        )
        self.cliente = get_user_model().objects.create_user(
            username='cliente-horario@example.com', email='cliente-horario@example.com', password='x'
        )
        PerfilUsuario.objects.create(usuario=self.cliente, rol=PerfilUsuario.Rol.CLIENTE)
        categoria = Categoria.objects.create(nombre='Servicios de prueba')
        self.servicio = Servicio.objects.create(
            proveedor=self.proveedor,
            categoria=categoria,
            nombre='Servicio de prueba',
            duracion_minutos=60,
            precio=25000,
        )
        DisponibilidadProveedor.objects.create(
            proveedor=self.proveedor,
            dia_semana=5,
            hora_inicio=time(8),
            hora_fin=time(17),
        )
        self.client.force_authenticate(self.cliente)

    def test_rechaza_horas_pasadas_y_la_hora_actual_del_dia(self):
        hoy = date(2026, 10, 31)
        ahora = datetime(2026, 10, 31, 12, 0, tzinfo=ZoneInfo('America/Bogota'))
        with patch('usuarios.validators.timezone.localdate', return_value=hoy), patch(
            'usuarios.validators.timezone.localtime', return_value=ahora
        ):
            for hora in ('11:00:00', '12:00:00'):
                respuesta = self.client.post('/api/solicitudes/', {
                    'servicio': self.servicio.pk,
                    'proveedor': self.proveedor.pk,
                    'fecha_solicitada': hoy.isoformat(),
                    'hora_solicitada': hora,
                    'direccion_atencion': 'Calle de prueba 123',
                    'barrio_sector': 'Centro',
                }, format='json')
                self.assertEqual(respuesta.status_code, 400)
                self.assertIn('hora_solicitada', respuesta.data)

            futura = self.client.post('/api/solicitudes/', {
                'servicio': self.servicio.pk,
                'proveedor': self.proveedor.pk,
                'fecha_solicitada': hoy.isoformat(),
                'hora_solicitada': '13:00:00',
                'direccion_atencion': 'Calle de prueba 123',
                'barrio_sector': 'Centro',
            }, format='json')

        self.assertEqual(futura.status_code, 201, futura.data)
        self.assertEqual(futura.data['hora_solicitada'], '13:00:00')

        self.client.force_authenticate(self.proveedor.perfil_usuario.usuario)
        actualizacion = self.client.patch(f'/api/catalogo/servicios/{self.servicio.pk}/', {
            'nombre': 'Servicio actualizado',
            'precio': '40000.00',
            'duracion_minutos': 90,
        }, format='json')
        self.assertEqual(actualizacion.status_code, 200, actualizacion.data)

        solicitud = SolicitudReserva.objects.get(pk=futura.data['id'])
        self.assertEqual(solicitud.nombre_servicio_copia, 'Servicio de prueba')
        self.assertEqual(str(solicitud.precio_copia), '25000.00')
        self.assertEqual(solicitud.duracion_minutos_copia, 60)

        desactivacion = self.client.patch(f'/api/catalogo/servicios/{self.servicio.pk}/', {
            'estado': 'INACTIVO',
        }, format='json')
        self.assertEqual(desactivacion.status_code, 200, desactivacion.data)
        self.client.force_authenticate(self.cliente)
        solicitud_inactiva = self.client.post('/api/solicitudes/', {
            'servicio': self.servicio.pk,
            'proveedor': self.proveedor.pk,
            'fecha_solicitada': '2026-11-02',
            'hora_solicitada': '13:00:00',
            'direccion_atencion': 'Calle de prueba 123',
            'barrio_sector': 'Centro',
        }, format='json')
        self.assertEqual(solicitud_inactiva.status_code, 400)
        self.assertIn('servicio', solicitud_inactiva.data)


MSG_CRUCE = 'Ya tienes una solicitud en ese horario. Elige otra hora.'
MSG_LIMITE = 'Solo puedes hacer hasta 3 solicitudes por día.'
MSG_PROVEEDOR_OCUPADO = 'El proveedor ya tiene una reserva en ese horario. Elige otra hora.'


def dia_habil(posicion=1):
    """Devuelve el n-ésimo día hábil (lunes a viernes) después de hoy."""
    fecha = timezone.localdate()
    while posicion > 0:
        fecha += dt.timedelta(days=1)
        if fecha.weekday() < 5:
            posicion -= 1
    return fecha


class LimitesSolicitudesClienteTests(APITestCase):
    url = '/api/solicitudes/'

    @classmethod
    def setUpTestData(cls):
        cls.cliente = cls._usuario('cliente@test.com', PerfilUsuario.Rol.CLIENTE)
        cls.otro_cliente = cls._usuario('otro@test.com', PerfilUsuario.Rol.CLIENTE)
        user_prov = cls._usuario('prov@test.com', PerfilUsuario.Rol.PROVEEDOR)
        user_prov2 = cls._usuario('prov2@test.com', PerfilUsuario.Rol.PROVEEDOR)
        cls.proveedor = PerfilProveedor.objects.create(
            perfil_usuario=user_prov.perfil, descripcion_profesional='Masajista')
        cls.proveedor2 = PerfilProveedor.objects.create(
            perfil_usuario=user_prov2.perfil, descripcion_profesional='Peluquero')
        categoria = Categoria.objects.create(nombre='Bienestar')
        cls.servicio = Servicio.objects.create(
            proveedor=cls.proveedor, categoria=categoria, nombre='Masaje',
            duracion_minutos=60, precio='80000')
        cls.servicio2 = Servicio.objects.create(
            proveedor=cls.proveedor2, categoria=categoria, nombre='Corte',
            duracion_minutos=30, precio='20000')
        cls.fecha = dia_habil(1)

    @staticmethod
    def _usuario(email, rol):
        user = User.objects.create_user(username=email, email=email, password='x12345678')
        PerfilUsuario.objects.create(usuario=user, rol=rol)
        return user

    # ----------------------------------------------------------------- helpers
    def existente(self, hora, estado=Estado.PENDIENTE, cliente=None, fecha=None, servicio=None):
        """Crea directamente en la base una solicitud previa del cliente."""
        servicio = servicio or self.servicio
        return SolicitudReserva.objects.create(
            cliente=cliente or self.cliente,
            proveedor=servicio.proveedor,
            servicio=servicio,
            nombre_servicio_copia=servicio.nombre,
            precio_copia=servicio.precio,
            duracion_minutos_copia=servicio.duracion_minutos,
            fecha_solicitada=fecha or self.fecha,
            hora_solicitada=hora,
            direccion_atencion='Calle 1',
            barrio_sector='Centro',
            estado=estado,
        )

    def crear(self, hora, cliente=None, fecha=None, servicio=None):
        """Crea una solicitud por la API, como lo hace el formulario."""
        servicio = servicio or self.servicio
        self.client.force_authenticate(cliente or self.cliente)
        return self.client.post(self.url, {
            'servicio': servicio.id,
            'proveedor': servicio.proveedor.id,
            'fecha_solicitada': (fecha or self.fecha).isoformat(),
            'hora_solicitada': hora,
            'direccion_atencion': 'Calle 1 # 2-3',
            'barrio_sector': 'Centro',
        }, format='json')

    def assertRechazo(self, respuesta, campo, mensaje):
        self.assertEqual(respuesta.status_code, 400, respuesta.data)
        self.assertEqual([str(m) for m in respuesta.data[campo]], [mensaje])

    def total_del_cliente(self):
        return SolicitudReserva.objects.filter(cliente=self.cliente).count()

    # ---------------------------------------------- Regla 1: sin cruces de hora
    def test_segunda_solicitud_a_la_misma_hora_da_400(self):
        self.assertEqual(self.crear('10:00').status_code, 201)
        self.assertRechazo(self.crear('10:00'), 'hora_solicitada', MSG_CRUCE)
        self.assertEqual(self.total_del_cliente(), 1)

    def test_solapamiento_parcial_da_400(self):
        self.existente(dt.time(10, 0))  # 10:00 a 11:00
        self.assertRechazo(self.crear('10:30'), 'hora_solicitada', MSG_CRUCE)

    def test_nueva_que_empieza_antes_y_termina_dentro_da_400(self):
        self.existente(dt.time(10, 0))  # 10:00 a 11:00
        # servicio2 dura 30 min: 09:45 a 10:15 se cruza con las 10:00
        self.assertRechazo(self.crear('09:45', servicio=self.servicio2), 'hora_solicitada', MSG_CRUCE)

    def test_solicitudes_consecutivas_se_permiten(self):
        self.existente(dt.time(10, 0))  # termina a las 11:00
        self.assertEqual(self.crear('11:00').status_code, 201)
        # y la que termina justo cuando empieza la existente (09:00 + 60 min = 10:00)
        self.assertEqual(self.crear('09:00').status_code, 201)

    def test_mismo_horario_con_otro_proveedor_da_400(self):
        self.existente(dt.time(10, 0))  # con proveedor 1
        self.assertRechazo(
            self.crear('10:00', servicio=self.servicio2), 'hora_solicitada', MSG_CRUCE)

    def test_dos_clientes_a_la_misma_hora_con_proveedores_distintos_se_permiten(self):
        self.existente(dt.time(10, 0), cliente=self.otro_cliente, servicio=self.servicio2)
        self.assertEqual(self.crear('10:00').status_code, 201)

    def test_dos_clientes_a_la_misma_hora_con_el_mismo_proveedor_da_400(self):
        # El proveedor no puede atender a dos clientes a la vez
        self.existente(dt.time(10, 0), cliente=self.otro_cliente)
        self.assertRechazo(self.crear('10:00'), 'hora_solicitada', MSG_PROVEEDOR_OCUPADO)
        self.assertEqual(self.total_del_cliente(), 0)

    def test_cuentan_pendiente_sugerida_y_aceptada(self):
        for estado in (Estado.PENDIENTE, Estado.SUGERIDA, Estado.ACEPTADA):
            with self.subTest(estado=estado):
                SolicitudReserva.objects.all().delete()
                self.existente(dt.time(10, 0), estado=estado)
                self.assertRechazo(self.crear('10:00'), 'hora_solicitada', MSG_CRUCE)

    def test_canceladas_y_completadas_no_cuentan_para_el_cruce(self):
        for estado in (Estado.CANCELADA, Estado.COMPLETADA):
            with self.subTest(estado=estado):
                SolicitudReserva.objects.all().delete()
                self.existente(dt.time(10, 0), estado=estado)
                self.assertEqual(self.crear('10:00').status_code, 201)

    def test_mismo_horario_en_otro_dia_se_permite(self):
        self.existente(dt.time(10, 0), fecha=dia_habil(2))
        self.assertEqual(self.crear('10:00').status_code, 201)

    # ------------------------------------- Regla 2: máximo 3 solicitudes por día
    def test_la_tercera_se_permite_y_la_cuarta_da_400(self):
        self.existente(dt.time(9, 0))
        self.existente(dt.time(10, 0))
        self.assertEqual(self.crear('11:00').status_code, 201)  # la tercera
        self.assertRechazo(self.crear('12:00'), 'fecha_solicitada', MSG_LIMITE)
        self.assertEqual(self.total_del_cliente(), 3)

    def test_limite_aplica_con_proveedores_distintos(self):
        self.existente(dt.time(9, 0))
        self.existente(dt.time(10, 0), servicio=self.servicio2)
        self.existente(dt.time(11, 0))
        self.assertRechazo(
            self.crear('13:00', servicio=self.servicio2), 'fecha_solicitada', MSG_LIMITE)

    def test_cancelada_no_cuenta_para_el_limite(self):
        self.existente(dt.time(9, 0))
        self.existente(dt.time(10, 0))
        self.existente(dt.time(11, 0), estado=Estado.CANCELADA)  # no cuenta: 2 activas
        self.assertEqual(self.crear('12:00').status_code, 201)         # llega a 3 activas
        self.assertRechazo(self.crear('13:00'), 'fecha_solicitada', MSG_LIMITE)

    def test_completada_no_cuenta_para_el_limite(self):
        self.existente(dt.time(9, 0))
        self.existente(dt.time(10, 0))
        self.existente(dt.time(11, 0), estado=Estado.COMPLETADA)
        self.assertEqual(self.crear('12:00').status_code, 201)

    def test_solicitudes_de_otro_dia_no_cuentan_para_el_limite(self):
        otro_dia = dia_habil(2)
        for hora in (9, 10, 11):
            self.existente(dt.time(hora, 0), fecha=otro_dia)
        self.assertEqual(self.crear('09:00').status_code, 201)

    def test_el_limite_es_por_cliente(self):
        for hora in (9, 10, 11):
            self.existente(dt.time(hora, 0), cliente=self.otro_cliente)
        # 13:00 está libre para el proveedor; las 3 del otro cliente no cuentan para este
        self.assertEqual(self.crear('13:00').status_code, 201)

    def test_si_hay_cruce_y_limite_a_la_vez_se_informa_el_cruce(self):
        for hora in (9, 10, 11):
            self.existente(dt.time(hora, 0))
        self.assertRechazo(self.crear('10:00'), 'hora_solicitada', MSG_CRUCE)

    # --------------------------------------------------- Solo al crear / carrera
    def test_las_reglas_no_se_aplican_al_editar(self):
        for hora in (9, 10, 11):
            self.existente(dt.time(hora, 0))
        solicitud = SolicitudReserva.objects.get(hora_solicitada=dt.time(11, 0))
        self.client.force_authenticate(self.cliente)
        r = self.client.patch(
            f'{self.url}{solicitud.id}/', {'hora_solicitada': '13:00'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)

    def test_al_guardar_se_vuelven_a_validar_las_reglas_dentro_de_la_transaccion(self):
        """Simula una petición concurrente: la validación inicial pasó con datos viejos
        (primera llamada sin efecto) y la solicitud rival ya está guardada al momento de crear."""
        # La rival es con otro proveedor: así solo la regla del cliente puede detectar el cruce
        self.existente(dt.time(10, 0), servicio=self.servicio2)
        llamadas = []

        def validacion_desactualizada(*args, **kwargs):
            llamadas.append(1)
            if len(llamadas) > 1:
                return limites.validar_limites_cliente(*args, **kwargs)

        with mock.patch('solicitudes.serializers.validar_limites_cliente',
                        side_effect=validacion_desactualizada):
            respuesta = self.crear('10:00')

        self.assertEqual(len(llamadas), 2)  # validate() y create()
        self.assertRechazo(respuesta, 'hora_solicitada', MSG_CRUCE)
        self.assertEqual(self.total_del_cliente(), 1)

    def test_la_cuarta_tambien_se_rechaza_al_guardar_aunque_validate_no_la_detecte(self):
        for hora in (9, 10, 11):
            self.existente(dt.time(hora, 0))
        llamadas = []

        def validacion_desactualizada(*args, **kwargs):
            llamadas.append(1)
            if len(llamadas) > 1:
                return limites.validar_limites_cliente(*args, **kwargs)

        with mock.patch('solicitudes.serializers.validar_limites_cliente',
                        side_effect=validacion_desactualizada):
            respuesta = self.crear('13:00')

        self.assertRechazo(respuesta, 'fecha_solicitada', MSG_LIMITE)
        self.assertEqual(self.total_del_cliente(), 3)


class BaseFlujoSolicitudesTest(APITestCase):
    """Datos comunes: dos clientes, dos proveedores y tres servicios (dos del mismo proveedor)."""

    @classmethod
    def setUpTestData(cls):
        cls.cliente = cls._usuario('cli@test.com', PerfilUsuario.Rol.CLIENTE, 'Maria', 'Lopez')
        cls.otro_cliente = cls._usuario('cli2@test.com', PerfilUsuario.Rol.CLIENTE, 'Juan', 'Perez')
        cls.user_prov = cls._usuario('pro@test.com', PerfilUsuario.Rol.PROVEEDOR, 'Manuel', 'Gomez')
        cls.user_prov2 = cls._usuario('pro2@test.com', PerfilUsuario.Rol.PROVEEDOR, 'Ana', 'Ruiz')
        cls.proveedor = PerfilProveedor.objects.create(perfil_usuario=cls.user_prov.perfil, descripcion_profesional='x')
        cls.proveedor2 = PerfilProveedor.objects.create(perfil_usuario=cls.user_prov2.perfil, descripcion_profesional='y')
        cat = Categoria.objects.create(nombre='Masajes')
        cls.masaje = Servicio.objects.create(proveedor=cls.proveedor, categoria=cat, nombre='Masaje',
                                             duracion_minutos=60, precio='80000')
        cls.espalda = Servicio.objects.create(proveedor=cls.proveedor, categoria=cat, nombre='Espalda',
                                              duracion_minutos=30, precio='45000')
        cls.corte = Servicio.objects.create(proveedor=cls.proveedor2, categoria=cat, nombre='Corte',
                                            duracion_minutos=30, precio='20000')
        cls.fecha = dia_habil(1)

    @staticmethod
    def _usuario(email, rol, nombre='', apellido=''):
        user = User.objects.create_user(username=email, email=email, password='x12345678',
                                        first_name=nombre, last_name=apellido)
        PerfilUsuario.objects.create(usuario=user, rol=rol, telefono='3001112233')
        return user

    def existente(self, hora, servicio=None, cliente=None, estado=Estado.PENDIENTE):
        servicio = servicio or self.masaje
        return SolicitudReserva.objects.create(
            cliente=cliente or self.cliente, proveedor=servicio.proveedor, servicio=servicio,
            nombre_servicio_copia=servicio.nombre, precio_copia=servicio.precio,
            duracion_minutos_copia=servicio.duracion_minutos, fecha_solicitada=self.fecha,
            hora_solicitada=hora, direccion_atencion='Calle 1 # 2-3', barrio_sector='Centro', estado=estado)

    def crear(self, hora, servicio=None, cliente=None):
        servicio = servicio or self.masaje
        self.client.force_authenticate(cliente or self.cliente)
        return self.client.post('/api/solicitudes/', {
            'servicio': servicio.id, 'proveedor': servicio.proveedor.id,
            'fecha_solicitada': self.fecha.isoformat(), 'hora_solicitada': hora,
            'direccion_atencion': 'Calle 1 # 2-3', 'barrio_sector': 'Centro',
        }, format='json')


class SolapamientoProveedorTests(BaseFlujoSolicitudesTest):
    """Un proveedor no puede tener dos reservas activas que se crucen."""

    def test_dos_servicios_distintos_del_mismo_proveedor_a_la_misma_hora_da_400(self):
        self.assertEqual(self.crear('10:00', self.masaje).status_code, 201)
        r = self.crear('10:00', self.espalda, cliente=self.otro_cliente)
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual([str(m) for m in r.data['hora_solicitada']], [MSG_PROVEEDOR_OCUPADO])
        self.assertEqual(SolicitudReserva.objects.filter(proveedor=self.proveedor).count(), 1)

    def test_cruce_parcial_con_el_proveedor_da_400(self):
        self.existente(dt.time(10, 0))  # 10:00 a 11:00
        self.assertEqual(self.crear('10:30', self.espalda, cliente=self.otro_cliente).status_code, 400)

    def test_horas_consecutivas_del_proveedor_se_permiten(self):
        self.existente(dt.time(10, 0))  # termina 11:00
        self.assertEqual(self.crear('11:00', self.espalda, cliente=self.otro_cliente).status_code, 201)
        self.assertEqual(self.crear('09:30', self.espalda, cliente=self.cliente).status_code, 201)  # 09:30-10:00

    def test_otro_proveedor_a_la_misma_hora_se_permite(self):
        self.existente(dt.time(10, 0))
        self.assertEqual(self.crear('10:00', self.corte, cliente=self.otro_cliente).status_code, 201)

    def test_canceladas_rechazadas_y_completadas_liberan_el_horario(self):
        for estado in (Estado.CANCELADA, Estado.RECHAZADA, Estado.COMPLETADA):
            with self.subTest(estado=estado):
                SolicitudReserva.objects.all().delete()
                self.existente(dt.time(10, 0), estado=estado)
                self.assertEqual(self.crear('10:00', self.espalda, cliente=self.otro_cliente).status_code, 201)

    def test_contrapropuesta_a_una_hora_ocupada_da_400(self):
        self.existente(dt.time(10, 0))
        otra = self.existente(dt.time(14, 0), servicio=self.espalda, cliente=self.otro_cliente)
        self.client.force_authenticate(self.user_prov)
        r = self.client.patch(f'/api/solicitudes/{otra.id}/', {'hora_solicitada': '10:00'}, format='json')
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('hora_solicitada', r.data)

    def test_contrapropuesta_no_choca_consigo_misma(self):
        s = self.existente(dt.time(10, 0))
        self.client.force_authenticate(self.user_prov)
        r = self.client.patch(f'/api/solicitudes/{s.id}/', {'hora_solicitada': '10:30'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)

    def test_al_guardar_se_revalida_el_proveedor_dentro_de_la_transaccion(self):
        """Simula dos clientes reservando a la vez: validate() pasó con datos viejos."""
        self.existente(dt.time(10, 0), cliente=self.otro_cliente)
        llamadas = []

        def desactualizada(*args, **kwargs):
            llamadas.append(1)
            if len(llamadas) > 1:
                return limites.validar_disponibilidad_proveedor(*args, **kwargs)

        with mock.patch('solicitudes.serializers.validar_disponibilidad_proveedor', side_effect=desactualizada):
            r = self.crear('10:00', self.espalda)
        self.assertEqual(len(llamadas), 2)
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(SolicitudReserva.objects.filter(cliente=self.cliente).count(), 0)

    def test_disponibilidad_publica_informa_horas_ocupadas_sin_datos_del_cliente(self):
        self.existente(dt.time(10, 0))
        self.existente(dt.time(14, 0), estado=Estado.CANCELADA)
        r = self.client.get(f'/api/proveedores/{self.proveedor.id}/disponibilidad-publica/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['ocupados'], [
            {'fecha': self.fecha.isoformat(), 'hora_inicio': '10:00', 'hora_fin': '11:00'}])
        self.assertNotIn('cli@test.com', str(r.data))


class RespuestaYCancelacionTests(BaseFlujoSolicitudesTest):
    """Aceptar, rechazar y cancelar (con motivo obligatorio) y sus permisos."""

    def url(self, s, accion):
        return f'/api/solicitudes/{s.id}/{accion}/'

    def test_proveedor_acepta(self):
        s = self.existente(dt.time(10, 0))
        self.client.force_authenticate(self.user_prov)
        r = self.client.post(self.url(s, 'aceptar'))
        self.assertEqual(r.status_code, 200, r.data)
        s.refresh_from_db()
        self.assertEqual(s.estado, Estado.ACEPTADA)
        self.assertEqual(r.data['estado_display'], 'Aceptada')

    def test_proveedor_rechaza_con_motivo(self):
        s = self.existente(dt.time(10, 0))
        self.client.force_authenticate(self.user_prov)
        r = self.client.post(self.url(s, 'rechazar'), {'motivo': '  No tengo agenda  '}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        s.refresh_from_db()
        self.assertEqual(s.estado, Estado.RECHAZADA)
        self.assertEqual(s.motivo_rechazo_cancelacion, 'No tengo agenda')

    def test_motivo_vacio_da_error(self):
        s = self.existente(dt.time(10, 0))
        self.client.force_authenticate(self.user_prov)
        for datos in ({}, {'motivo': ''}, {'motivo': '   '}):
            for accion in ('rechazar', 'cancelar'):
                r = self.client.post(self.url(s, accion), datos, format='json')
                self.assertEqual(r.status_code, 400)
                self.assertIn('motivo', r.data)
        s.refresh_from_db()
        self.assertEqual(s.estado, Estado.PENDIENTE)

    def test_cliente_cancela_con_motivo(self):
        s = self.existente(dt.time(10, 0))
        self.client.force_authenticate(self.cliente)
        r = self.client.post(self.url(s, 'cancelar'), {'motivo': 'Me salió un viaje'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        s.refresh_from_db()
        self.assertEqual((s.estado, s.motivo_rechazo_cancelacion), (Estado.CANCELADA, 'Me salió un viaje'))

    def test_proveedor_cancela_una_aceptada(self):
        s = self.existente(dt.time(10, 0), estado=Estado.ACEPTADA)
        self.client.force_authenticate(self.user_prov)
        r = self.client.post(self.url(s, 'cancelar'), {'motivo': 'Enfermedad'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)

    def test_no_se_rechaza_ni_cancela_una_finalizada(self):
        for estado in SolicitudReserva.ESTADOS_FINALES:
            with self.subTest(estado=estado):
                s = self.existente(dt.time(8, 0), estado=estado)
                self.client.force_authenticate(self.user_prov)
                self.assertEqual(self.client.post(self.url(s, 'rechazar'), {'motivo': 'x'}, format='json').status_code, 400)
                self.assertEqual(self.client.post(self.url(s, 'aceptar')).status_code, 400)
                self.client.force_authenticate(self.cliente)
                self.assertEqual(self.client.post(self.url(s, 'cancelar'), {'motivo': 'x'}, format='json').status_code, 400)

    def test_cliente_no_puede_aceptar_ni_rechazar(self):
        s = self.existente(dt.time(10, 0))
        self.client.force_authenticate(self.cliente)
        self.assertEqual(self.client.post(self.url(s, 'aceptar')).status_code, 403)
        self.assertEqual(self.client.post(self.url(s, 'rechazar'), {'motivo': 'x'}, format='json').status_code, 403)

    def test_otro_cliente_u_otro_proveedor_no_ven_la_solicitud(self):
        s = self.existente(dt.time(10, 0))
        for usuario in (self.otro_cliente, self.user_prov2):
            self.client.force_authenticate(usuario)
            self.assertEqual(self.client.post(self.url(s, 'cancelar'), {'motivo': 'x'}, format='json').status_code, 404)

    def test_anonimo_no_puede_cancelar(self):
        s = self.existente(dt.time(10, 0))
        self.assertIn(self.client.post(self.url(s, 'cancelar'), {'motivo': 'x'}, format='json').status_code, (401, 403))

    def test_el_estado_no_se_cambia_con_patch(self):
        # Antes un cliente podía marcar su propia solicitud como ACEPTADA con un PATCH
        s = self.existente(dt.time(10, 0))
        self.client.force_authenticate(self.cliente)
        self.client.patch(f'/api/solicitudes/{s.id}/', {'estado': 'ACEPTADA', 'motivo_rechazo_cancelacion': 'x'}, format='json')
        s.refresh_from_db()
        self.assertEqual(s.estado, Estado.PENDIENTE)
        self.assertIsNone(s.motivo_rechazo_cancelacion)


class DatosDeLaSolicitudTests(BaseFlujoSolicitudesTest):

    def test_proveedor_ve_la_direccion_principal_y_el_telefono_del_cliente(self):
        from usuarios.models import Direccion
        Direccion.objects.create(usuario=self.cliente, direccion='Cra 5 # 10-20', ciudad='Cali',
                                 barrio='Granada', es_principal=True)
        Direccion.objects.create(usuario=self.cliente, direccion='Otra', ciudad='Cali', barrio='Centro')
        self.existente(dt.time(10, 0))
        self.client.force_authenticate(self.user_prov)
        dato = self.client.get('/api/solicitudes/').data[0]
        self.assertEqual(dato['cliente_direccion_principal']['direccion'], 'Cra 5 # 10-20')
        self.assertEqual(dato['cliente_telefono'], '3001112233')
        self.assertEqual(dato['nombre_proveedor'], 'Manuel Gomez')

    def test_sin_direccion_principal_devuelve_none(self):
        s = self.existente(dt.time(10, 0))
        self.client.force_authenticate(self.cliente)
        self.assertIsNone(self.client.get(f'/api/solicitudes/{s.id}/').data['cliente_direccion_principal'])

    def test_la_solicitud_conserva_el_nombre_del_servicio_al_crearla(self):
        self.assertEqual(self.crear('10:00').status_code, 201)
        Servicio.objects.filter(pk=self.masaje.pk).update(nombre='Masaje premium')
        self.client.force_authenticate(self.cliente)
        dato = self.client.get('/api/solicitudes/').data[0]
        self.assertEqual(dato['nombre_servicio_copia'], 'Masaje')


class PaginaDisponibilidadTests(BaseFlujoSolicitudesTest):
    """H-11: el enlace de /disponibilidad/ abierto sin sesión."""

    def test_sin_sesion_redirige_al_inicio_de_sesion_y_vuelve(self):
        r = self.client.get('/disponibilidad/')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r['Location'], '/?login=1&next=/disponibilidad/')

    def test_cliente_con_sesion_va_al_inicio(self):
        self.client.force_authenticate(self.cliente)
        r = self.client.get('/disponibilidad/')
        self.assertEqual((r.status_code, r['Location']), (302, '/'))

    def test_las_urls_de_la_api_no_muestran_la_interfaz_de_rest_framework(self):
        r = self.client.get(f'/api/proveedores/{self.proveedor.id}/disponibilidad-publica/', HTTP_ACCEPT='text/html')
        self.assertNotIn(b'Django REST framework', r.content)
        self.assertEqual(r['Content-Type'], 'application/json')


# =====================================================================================
# Verificación de las historias de usuario, criterio por criterio
# =====================================================================================
class HU012CreacionDeSolicitudTests(BaseFlujoSolicitudesTest):
    """HU-012. Creación de una solicitud de servicio."""

    def test_c1_cliente_crea_una_solicitud_propia(self):
        r = self.crear('10:00')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['cliente'], self.cliente.id)

    def test_c1_no_puede_crear_en_nombre_de_otro_cliente(self):
        self.client.force_authenticate(self.cliente)
        r = self.client.post('/api/solicitudes/', {
            'servicio': self.masaje.id, 'cliente': self.otro_cliente.id,
            'fecha_solicitada': self.fecha.isoformat(), 'hora_solicitada': '10:00',
            'direccion_atencion': 'Calle 1', 'barrio_sector': 'Centro'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(SolicitudReserva.objects.get(pk=r.data['id']).cliente, self.cliente)

    def test_c1_un_proveedor_no_crea_solicitudes(self):
        self.assertEqual(self.crear('10:00', cliente=self.user_prov).status_code, 403)

    def test_c2_guarda_toda_la_informacion_enviada(self):
        self.client.force_authenticate(self.cliente)
        r = self.client.post('/api/solicitudes/', {
            'servicio': self.masaje.id, 'proveedor': self.proveedor.id,
            'fecha_solicitada': self.fecha.isoformat(), 'hora_solicitada': '10:00',
            'direccion_atencion': 'Cra 5 # 10-20', 'barrio_sector': 'Granada',
            'informacion_complementaria': 'Apto 302, torre B', 'observaciones': 'Traer camilla'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        s = SolicitudReserva.objects.get(pk=r.data['id'])
        self.assertEqual((s.proveedor, s.servicio, s.direccion_atencion, s.barrio_sector,
                          s.informacion_complementaria, s.observaciones),
                         (self.proveedor, self.masaje, 'Cra 5 # 10-20', 'Granada', 'Apto 302, torre B', 'Traer camilla'))

    def test_c2_el_proveedor_debe_corresponder_al_servicio(self):
        self.client.force_authenticate(self.cliente)
        r = self.client.post('/api/solicitudes/', {
            'servicio': self.masaje.id, 'proveedor': self.proveedor2.id,
            'fecha_solicitada': self.fecha.isoformat(), 'hora_solicitada': '10:00',
            'direccion_atencion': 'Calle 1', 'barrio_sector': 'Centro'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('proveedor', r.data)

    def test_c3_horario_dentro_de_la_disponibilidad(self):
        for dia in range(5):
            DisponibilidadProveedor.objects.create(proveedor=self.proveedor, dia_semana=dia,
                                                   hora_inicio=time(9, 0), hora_fin=time(12, 0))
        self.assertEqual(self.crear('10:00').status_code, 201)

    def test_c4_horario_fuera_de_la_disponibilidad_informa_y_no_crea(self):
        for dia in range(5):
            DisponibilidadProveedor.objects.create(proveedor=self.proveedor, dia_semana=dia,
                                                   hora_inicio=time(9, 0), hora_fin=time(12, 0))
        r = self.crear('11:30')  # 11:30 + 60 min = 12:30, fuera del horario
        self.assertEqual(r.status_code, 400)
        self.assertIn('hora_solicitada', r.data)
        self.assertEqual(SolicitudReserva.objects.count(), 0)

    def test_c5_conserva_nombre_precio_y_duracion(self):
        r = self.crear('10:00')
        s = SolicitudReserva.objects.get(pk=r.data['id'])
        self.assertEqual((s.nombre_servicio_copia, s.precio_copia, s.duracion_minutos_copia), ('Masaje', 80000, 60))

    def test_c6_cambios_posteriores_del_catalogo_no_afectan_la_solicitud(self):
        r = self.crear('10:00')
        Servicio.objects.filter(pk=self.masaje.pk).update(nombre='Masaje VIP', precio='99000', duracion_minutos=90)
        s = SolicitudReserva.objects.get(pk=r.data['id'])
        self.assertEqual((s.nombre_servicio_copia, s.precio_copia, s.duracion_minutos_copia), ('Masaje', 80000, 60))
        self.client.force_authenticate(self.cliente)
        dato = self.client.get(f'/api/solicitudes/{s.id}/').data
        self.assertEqual((dato['nombre_servicio_copia'], dato['hora_fin']), ('Masaje', '11:00:00'))

    def test_c6_no_se_puede_cambiar_el_servicio_de_una_solicitud_creada(self):
        s = self.existente(dt.time(10, 0))
        self.client.force_authenticate(self.cliente)
        r = self.client.patch(f'/api/solicitudes/{s.id}/', {'servicio': self.corte.id}, format='json')
        self.assertEqual(r.status_code, 400)
        s.refresh_from_db()
        self.assertEqual((s.servicio, s.proveedor), (self.masaje, self.proveedor))

    def test_c7_informacion_obligatoria_incompleta_indica_lo_pendiente(self):
        self.client.force_authenticate(self.cliente)
        r = self.client.post('/api/solicitudes/', {}, format='json')
        self.assertEqual(r.status_code, 400)
        for campo in ('servicio', 'fecha_solicitada', 'hora_solicitada', 'direccion_atencion', 'barrio_sector'):
            self.assertIn(campo, r.data)
        self.assertEqual(SolicitudReserva.objects.count(), 0)

    def test_c8_estado_inicial_pendiente_y_fechas(self):
        r = self.crear('10:00')
        self.assertEqual(r.data['estado'], 'PENDIENTE')
        self.assertTrue(r.data['creado_en'])
        self.assertTrue(r.data['actualizado_en'])

    def test_c9_la_direccion_se_guarda_tal_cual_sin_validacion_geografica(self):
        self.client.force_authenticate(self.cliente)
        r = self.client.post('/api/solicitudes/', {
            'servicio': self.masaje.id, 'fecha_solicitada': self.fecha.isoformat(), 'hora_solicitada': '10:00',
            'direccion_atencion': 'Vereda El Placer, finca La Esperanza', 'barrio_sector': 'Zona rural'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['direccion_atencion'], 'Vereda El Placer, finca La Esperanza')

    def test_c10_hora_de_finalizacion_con_la_duracion_conservada(self):
        r = self.crear('10:30', self.espalda)  # 30 min
        self.assertEqual(r.data['hora_fin'], '11:00:00')


class HU013ConsultaDelClienteTests(BaseFlujoSolicitudesTest):
    """HU-013. Consulta de solicitudes y reservas del cliente."""

    def test_c1_solo_ve_las_suyas_con_su_estado(self):
        propia = self.existente(dt.time(10, 0))
        self.existente(dt.time(14, 0), cliente=self.otro_cliente)
        self.client.force_authenticate(self.cliente)
        datos = self.client.get('/api/solicitudes/').data
        self.assertEqual([d['id'] for d in datos], [propia.id])
        self.assertEqual(datos[0]['estado'], 'PENDIENTE')

    def test_c2_detalle_con_estado_y_fechas(self):
        s = self.existente(dt.time(10, 0))
        self.client.force_authenticate(self.cliente)
        d = self.client.get(f'/api/solicitudes/{s.id}/').data
        for campo in ('estado', 'estado_display', 'creado_en', 'actualizado_en'):
            self.assertTrue(d[campo])

    def test_c3_rechazada_muestra_el_motivo(self):
        s = self.existente(dt.time(10, 0))
        self.client.force_authenticate(self.user_prov)
        self.client.post(f'/api/solicitudes/{s.id}/rechazar/', {'motivo': 'Sin cupo'}, format='json')
        self.client.force_authenticate(self.cliente)
        d = self.client.get(f'/api/solicitudes/{s.id}/').data
        self.assertEqual((d['estado'], d['motivo_rechazo_cancelacion']), ('RECHAZADA', 'Sin cupo'))

    def test_c4_cancelada_muestra_el_motivo(self):
        s = self.existente(dt.time(10, 0), estado=Estado.ACEPTADA)
        self.client.force_authenticate(self.user_prov)
        self.client.post(f'/api/solicitudes/{s.id}/cancelar/', {'motivo': 'Enfermedad'}, format='json')
        self.client.force_authenticate(self.cliente)
        d = self.client.get(f'/api/solicitudes/{s.id}/').data
        self.assertEqual((d['estado'], d['motivo_rechazo_cancelacion']), ('CANCELADA', 'Enfermedad'))

    def test_c5_sin_solicitudes_informa_que_no_hay_registros(self):
        self.client.force_authenticate(self.cliente)
        d = self.client.get('/api/solicitudes/').data
        self.assertEqual(d['resultados'], [])
        self.assertIn('No tienes solicitudes', d['mensaje'])

    def test_c6_no_accede_a_solicitudes_de_otro_cliente(self):
        ajena = self.existente(dt.time(10, 0), cliente=self.otro_cliente)
        self.client.force_authenticate(self.cliente)
        r = self.client.get(f'/api/solicitudes/{ajena.id}/')
        self.assertEqual(r.status_code, 404)
        self.assertNotIn('direccion_atencion', r.data)

    def test_c7_muestra_estado_actual_sin_historial(self):
        s = self.existente(dt.time(10, 0))
        self.client.force_authenticate(self.user_prov)
        self.client.post(f'/api/solicitudes/{s.id}/aceptar/')
        self.client.force_authenticate(self.cliente)
        d = self.client.get(f'/api/solicitudes/{s.id}/').data
        self.assertEqual(d['estado'], 'ACEPTADA')
        self.assertNotIn('historial', d)

    def test_las_solicitudes_no_se_pueden_borrar(self):
        s = self.existente(dt.time(10, 0))
        self.client.force_authenticate(self.cliente)
        self.assertEqual(self.client.delete(f'/api/solicitudes/{s.id}/').status_code, 405)
        self.assertTrue(SolicitudReserva.objects.filter(pk=s.id).exists())


class HU014SolicitudesRecibidasTests(BaseFlujoSolicitudesTest):
    """HU-014. Consulta de solicitudes recibidas por el proveedor."""

    def test_c1_solo_las_dirigidas_a_su_cuenta_con_estado(self):
        propia = self.existente(dt.time(10, 0))
        self.existente(dt.time(10, 0), servicio=self.corte, cliente=self.otro_cliente)
        self.client.force_authenticate(self.user_prov)
        datos = self.client.get('/api/solicitudes/').data
        self.assertEqual([(d['id'], d['estado']) for d in datos], [(propia.id, 'PENDIENTE')])

    def test_c2_c3_c4_detalle_con_atencion_condiciones_estado_y_fechas(self):
        s = self.existente(dt.time(10, 0))
        SolicitudReserva.objects.filter(pk=s.pk).update(informacion_complementaria='Apto 302', observaciones='Perro')
        Servicio.objects.filter(pk=self.masaje.pk).update(nombre='Otro nombre', precio='1')
        self.client.force_authenticate(self.user_prov)
        d = self.client.get(f'/api/solicitudes/{s.id}/').data
        self.assertEqual((d['direccion_atencion'], d['barrio_sector'], d['informacion_complementaria'], d['observaciones']),
                         ('Calle 1 # 2-3', 'Centro', 'Apto 302', 'Perro'))
        self.assertEqual((d['nombre_servicio_copia'], d['precio_copia'], d['duracion_minutos_copia']), ('Masaje', '80000.00', 60))
        for campo in ('estado', 'creado_en', 'actualizado_en'):
            self.assertTrue(d[campo])
        self.assertIn('motivo_rechazo_cancelacion', d)

    def test_c5_no_accede_a_solicitudes_de_otro_proveedor(self):
        s = self.existente(dt.time(10, 0))
        self.client.force_authenticate(self.user_prov2)
        self.assertEqual(self.client.get(f'/api/solicitudes/{s.id}/').status_code, 404)

    def test_c6_sin_autenticacion_se_deniega(self):
        self.existente(dt.time(10, 0))
        self.assertIn(self.client.get('/api/solicitudes/').status_code, (401, 403))


class HU019AgendaProveedorTests(BaseFlujoSolicitudesTest):
    """HU-019. Consulta de la agenda del proveedor."""
    url = '/api/solicitudes/agenda/'

    def test_c1_solo_aceptadas_en_orden_cronologico(self):
        tarde = self.existente(dt.time(15, 0), estado=Estado.ACEPTADA)
        temprano = self.existente(dt.time(9, 0), servicio=self.espalda, cliente=self.otro_cliente, estado=Estado.ACEPTADA)
        self.existente(dt.time(11, 0), estado=Estado.PENDIENTE)
        self.existente(dt.time(13, 0), estado=Estado.CANCELADA)
        self.client.force_authenticate(self.user_prov)
        datos = self.client.get(self.url).data
        self.assertEqual([r['id'] for r in datos['reservas']], [temprano.id, tarde.id])

    def test_c2_c3_periodo_servicio_y_direccion(self):
        self.existente(dt.time(9, 0), servicio=self.espalda, estado=Estado.ACEPTADA)
        self.client.force_authenticate(self.user_prov)
        r = self.client.get(self.url).data['reservas'][0]
        self.assertEqual((r['fecha_solicitada'], r['hora_solicitada'], r['hora_fin']),
                         (self.fecha.isoformat(), '09:00:00', '09:30:00'))
        self.assertEqual((r['nombre_servicio_copia'], r['direccion_atencion']), ('Espalda', 'Calle 1 # 2-3'))

    def test_c4_sin_reservas_aceptadas_lo_informa(self):
        self.existente(dt.time(9, 0), estado=Estado.PENDIENTE)
        self.client.force_authenticate(self.user_prov)
        d = self.client.get(self.url).data
        self.assertEqual(d['reservas'], [])
        self.assertEqual(d['mensaje'], 'No tienes reservas aceptadas para mostrar.')

    def test_c5_no_ve_la_agenda_de_otro_proveedor(self):
        self.existente(dt.time(9, 0), estado=Estado.ACEPTADA)
        self.client.force_authenticate(self.user_prov2)
        self.assertEqual(self.client.get(self.url).data['reservas'], [])
        self.client.force_authenticate(self.cliente)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_c6_sin_autenticacion_se_deniega(self):
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    def test_rango_de_fechas_invalido(self):
        self.client.force_authenticate(self.user_prov)
        self.assertEqual(self.client.get(self.url + '?desde=2026-12-10&hasta=2026-12-01').status_code, 400)

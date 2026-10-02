import datetime

from django.conf import settings
from django.db import models


class Grupo(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    data_inicio = models.DateField()
    quantidade_dias = models.PositiveIntegerField()
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    @property
    def data_fim(self):
        return self.data_inicio + datetime.timedelta(days=self.quantidade_dias - 1)

    def esta_aberto(self):
        hoje = datetime.date.today()
        return self.data_inicio <= hoje <= self.data_fim

    def __str__(self):
        return f"{self.usuario} — {self.data_inicio} a {self.data_fim}"


class Medicamento(models.Model):
    class Periodo(models.TextChoices):
        MANHA = "manha", "Manhã"
        TARDE = "tarde", "Tarde"
        NOITE = "noite", "Noite"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    nome = models.CharField(max_length=100)
    dosagem = models.CharField(max_length=50, blank=True)
    periodo = models.CharField(max_length=20, choices=Periodo.choices, default=Periodo.MANHA)
    ativo = models.BooleanField(default=True)

    def __str__(self):
        return self.nome


class GrupoMedicamento(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    grupo = models.ForeignKey(Grupo, on_delete=models.CASCADE, related_name="medicamentos_vinculados")
    medicamento = models.ForeignKey(Medicamento, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("usuario", "grupo", "medicamento")


class MedicaoGlicemia(models.Model):
    class Contexto(models.TextChoices):
        JEJUM = "jejum", "Jejum"
        APOS_CAFE = "apos_cafe", "2h após o café"
        ANTES_ALMOCO = "antes_almoco", "Antes do almoço"
        APOS_ALMOCO = "apos_almoco", "2h após o almoço"
        ANTES_JANTAR = "antes_jantar", "Antes do jantar"
        APOS_JANTAR = "apos_jantar", "2h após o jantar"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    grupo = models.ForeignKey(Grupo, on_delete=models.CASCADE, related_name="medicoes")
    valor_mgdl = models.PositiveIntegerField()
    data_hora = models.DateTimeField()
    # Dia da coleta em formato de data — usado para unicidade e filtros.
    # Default: data do dia pelo relógio local (USE_TZ=False).
    data = models.DateField(default=datetime.date.today)
    contexto = models.CharField(max_length=20, choices=Contexto.choices)
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        # Um registro por (usuario, dia, contexto). O grupo NÃO entra na
        # unicidade — a garantia de "mesmo usuário e mesmo grupo" é feita na
        # aplicação (grupo escolhido define o usuário). A aleatoriedade, quando
        # existir, ficará fora desta constraint por decisão de projeto.
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "data", "contexto"],
                name="uniq_usuario_data_contexto",
            )
        ]

    def __str__(self):
        return f"{self.usuario} {self.contexto} {self.data} = {self.valor_mgdl}"
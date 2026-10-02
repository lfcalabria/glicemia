import datetime

from django import forms

from .models import Grupo, MedicaoGlicemia, Medicamento

CONTEXTOS_FIXOS = [
    "jejum", "apos_cafe", "antes_almoco",
    "apos_almoco", "antes_jantar", "apos_jantar",
]


def _data_hora_local(d, hora):
    """Monta o datetime exatamente como digitado (hora local).

    Com USE_TZ=False o banco guarda a hora local, sem conversão para UTC.
    """
    return datetime.datetime.combine(d, hora)


def _hora_local(data_hora):
    """Extrai a hora do registro — o valor já está local, basta ler."""
    return data_hora.time()


class GrupoForm(forms.ModelForm):
    class Meta:
        model = Grupo
        fields = ["data_inicio", "quantidade_dias"]
        widgets = {
            "data_inicio": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "quantidade_dias": forms.NumberInput(attrs={"min": 1, "max": 90}),
        }

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        if self.instance.pk is None and usuario is not None:
            self.instance.usuario = usuario
        if "data_inicio" not in self.initial:
            self.initial["data_inicio"] = datetime.date.today()


class MedicamentoForm(forms.ModelForm):
    class Meta:
        model = Medicamento
        fields = ["nome", "dosagem", "periodo"]


class MedicaoDiaForm(forms.Form):
    """Um campo de valor + um campo de hora para cada marcação fixa do dia."""

    def __init__(self, *args, grupo=None, dia=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.grupo = grupo
        self.dia = dia
        agora = datetime.datetime.now().time()

        for contexto in CONTEXTOS_FIXOS:
            existente = MedicaoGlicemia.objects.filter(
                usuario=grupo.usuario, data=dia, contexto=contexto
            ).first()
            ja_preenchido = existente is not None

            self.fields[contexto] = forms.IntegerField(
                label="Valor (mg/dL)",
                min_value=10,
                max_value=1000,
                required=False,
                widget=forms.NumberInput(
                    attrs={"min": 10, "max": 1000, "step": 1, "placeholder": "mg/dL"}
                ),
                initial=existente.valor_mgdl if existente else None,
            )
            self.fields[f"{contexto}_hora"] = forms.TimeField(
                label="Hora da coleta",
                required=False,
                widget=forms.TimeInput(attrs={"type": "time"}),
                initial=_hora_local(existente.data_hora) if existente else agora,
            )

            for campo in (self.fields[contexto], self.fields[f"{contexto}_hora"]):
                campo.ja_preenchido = ja_preenchido
                if ja_preenchido:
                    campo.widget.attrs["disabled"] = True

    def salvar(self):
        if self.dia > datetime.date.today():
            return 0
        agora = datetime.datetime.now().time()
        criadas = 0
        for contexto in CONTEXTOS_FIXOS:
            valor = self.cleaned_data.get(contexto)
            if valor is None:
                continue
            hora = self.cleaned_data.get(f"{contexto}_hora") or agora
            # Chave de unicidade: (usuario, data, contexto). O grupo é sempre
            # o grupo escolhido — grava/atualiza mantendo usuário e grupo
            # coerentes. Nunca duplica (constraint no banco).
            _, created = MedicaoGlicemia.objects.update_or_create(
                usuario=self.grupo.usuario,
                contexto=contexto,
                data=self.dia,
                defaults={
                    "valor_mgdl": valor,
                    "data_hora": _data_hora_local(self.dia, hora),
                    "grupo": self.grupo,
                },
            )
            if created:
                criadas += 1
        return criadas


class AdminMarcacoesForm(forms.Form):
    """Grade editável das marcações de um grupo específico.

    Chave de unicidade: (usuario, data, contexto) — constraint no banco.
    O grupo é sempre o grupo escolhido na tela (grava/atualiza nele).
    O usuário usado é o do GRUPO ESCOLHIDO, nunca o logado.
    """

    def __init__(self, *args, grupo=None, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.grupo = grupo
        # Fonte da verdade: o grupo escolhido define o usuário.
        self.usuario = grupo.usuario if grupo is not None else usuario
        self.datas = [
            grupo.data_inicio + datetime.timedelta(days=i)
            for i in range(grupo.quantidade_dias)
        ]
        for i, d in enumerate(self.datas):
            for c in CONTEXTOS_FIXOS:
                prefix = f"indice_{i}_{c}"
                existente = MedicaoGlicemia.objects.filter(
                    usuario=self.usuario, data=d, contexto=c
                ).first()
                self.fields[f"{prefix}_valor"] = forms.IntegerField(
                    label="Valor (mg/dL)",
                    required=False,
                    min_value=10,
                    max_value=1000,
                    widget=forms.NumberInput(
                        attrs={"min": 10, "max": 1000, "step": 1, "style": "width:70px;"}
                    ),
                    initial=existente.valor_mgdl if existente else None,
                )
                self.fields[f"{prefix}_hora"] = forms.TimeField(
                    label="Hora",
                    required=False,
                    widget=forms.TimeInput(attrs={"type": "time", "style": "width:95px;"}),
                    initial=_hora_local(existente.data_hora) if existente else None,
                )

    def salvar(self):
        criadas = 0
        atualizadas = 0
        for i, d in enumerate(self.datas):
            for c in CONTEXTOS_FIXOS:
                prefix = f"indice_{i}_{c}"
                valor = self.cleaned_data.get(f"{prefix}_valor")
                hora = self.cleaned_data.get(f"{prefix}_hora")
                if valor is None:
                    continue  # célula vazia = nada a fazer
                hora = hora or datetime.time(0, 0)
                data_hora = _data_hora_local(d, hora)

                _, created = MedicaoGlicemia.objects.update_or_create(
                    usuario=self.usuario,
                    contexto=c,
                    data=d,
                    defaults={
                        "valor_mgdl": valor,
                        "data_hora": data_hora,
                        "grupo": self.grupo,
                    },
                )
                if created:
                    criadas += 1
                else:
                    atualizadas += 1
        return criadas, atualizadas
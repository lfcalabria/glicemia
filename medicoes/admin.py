from django.contrib import admin

from .models import Grupo, GrupoMedicamento, MedicaoGlicemia, Medicamento


@admin.register(MedicaoGlicemia)
class MedicaoGlicemiaAdmin(admin.ModelAdmin):
    list_display = ("data_hora", "usuario", "grupo", "valor_mgdl", "contexto", "criado_em")
    list_filter = ("contexto", "data_hora", "usuario", "grupo")


@admin.register(Grupo)
class GrupoAdmin(admin.ModelAdmin):
    list_display = ("id", "usuario", "data_inicio", "quantidade_dias", "data_fim", "criado_em")
    list_filter = ("data_inicio", "usuario")
    date_hierarchy = "data_inicio"


@admin.register(Medicamento)
class MedicamentoAdmin(admin.ModelAdmin):
    list_display = ("nome", "dosagem", "periodo", "ativo", "usuario")
    list_filter = ("periodo", "ativo", "usuario")
    search_fields = ("nome",)


@admin.register(GrupoMedicamento)
class GrupoMedicamentoAdmin(admin.ModelAdmin):
    list_display = ("grupo", "medicamento", "usuario")
    list_filter = ("grupo", "usuario")